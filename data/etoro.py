"""
eToro as a data source: candles, rates, and fills that have to be asked for.

The three classes here stand in for data/candles.py, data/streaming.py and
data/transaction.py. The first two are the same shape as their OANDA
counterparts, only poorer in what they can serve. The third is a different
animal, and it is worth saying why.

OANDA pushes transactions: a fill or a close arrives on an open socket the
moment it happens, carrying the order's clientExtensions and the reason the
trade ended. eToro pushes nothing. An order acknowledged with a 200 is only
*accepted*, and there is no route that says "tell me what happened"; there
are routes that answer "what happened to this order" and "which trades
closed since this date". So EToroTransactions learns which orders exist by
listening to the bus for the acknowledgements the execution handler
publishes, then polls those two routes and synthesises the TransactionEvents
the rest of the stack already understands.

Two things it cannot recover, and does not pretend to:

* **which leg closed a trade.** OANDA states it; eToro reports the rate the
  trade closed at. closeReason() infers the leg from that rate and marks the
  event as inferred, and where the rate does not clearly belong to either
  leg it says UNKNOWN rather than picking one. trading/parity.py treats an
  UNKNOWN outcome as undecidable instead of as a divergence, because our own
  ignorance is not the market disagreeing with the simulator.
* **the account balance after a close.** It is not in the trade history, so
  the close event carries None. Fetching it would cost a call per close and
  still be a balance read at a different instant than the close.
"""

import datetime
import logging
import sys
import time

from parity_deriva.etc import settings
from parity_deriva.event.event import CandleEvent
from parity_deriva.event.event import StatusEvent
from parity_deriva.event.event import TickEvent
from parity_deriva.event.event import TransactionEvent
from parity_deriva.event.event import OrderCancelEvent
from parity_deriva.lib.closereason import STOP_LOSS as _STOP_LOSS
from parity_deriva.lib.closereason import TAKE_PROFIT as _TAKE_PROFIT
from parity_deriva.lib.closereason import UNKNOWN as _UNKNOWN
from parity_deriva.lib.closereason import closeReason as _closeReason
from parity_deriva.lib.etoro import (EToroAPI, EToroError, candleTime,
									 instrumentId, instrumentName, interval,
									 spreadModel, utcnow)
from parity_deriva.lib.utils import granularityToTimedelta
from parity_deriva.trading.handler import StreamHandler


#: eToro order status ids, from the orders:lookup spec
STATUS_RECEIVED = 1
STATUS_PLACED = 2
STATUS_FILLED = 3
STATUS_REJECTED = 4
STATUS_PARTIALLY_FILLED = 5
STATUS_PENDING_CANCEL = 6
STATUS_CANCELED = 7
STATUS_EXPIRED = 8
STATUS_CANCELED_PARTIALLY_FILLED = 9
STATUS_REJECTED_PARTIALLY_FILLED = 10
STATUS_WAITING_FOR_MARKET = 11
STATUS_PENDING_TRIGGERED_RATE = 12

#: still on its way to an outcome, so keep asking
STATUS_IN_FLIGHT = frozenset([STATUS_RECEIVED, STATUS_PLACED,
							  STATUS_PENDING_CANCEL,
							  STATUS_WAITING_FOR_MARKET,
							  STATUS_PENDING_TRIGGERED_RATE])
#: the order produced a position
STATUS_EXECUTED = frozenset([STATUS_FILLED, STATUS_PARTIALLY_FILLED])
#: the order will not produce one
STATUS_REFUSED = frozenset([STATUS_REJECTED, STATUS_REJECTED_PARTIALLY_FILLED])
STATUS_GONE = frozenset([STATUS_CANCELED, STATUS_EXPIRED,
						 STATUS_CANCELED_PARTIALLY_FILLED])

#: an order still resting on the book, and therefore still cancellable
STATUS_RESTING = frozenset([STATUS_PLACED, STATUS_WAITING_FOR_MARKET,
							STATUS_PENDING_TRIGGERED_RATE])

#: What trading/parity.py compares, and the inference that produces it. Both
#: are shared with the IG path, which has the same gap for the same reason -
#: the broker reports a closing level and not which leg took the trade - and
#: are re-exported here because this is where they were first written and
#: where the eToro code and its tests look for them. See lib/closereason.py
#: for the rule and for why it declines to judge rather than guessing.
TAKE_PROFIT = _TAKE_PROFIT
STOP_LOSS = _STOP_LOSS
UNKNOWN = _UNKNOWN
closeReason = _closeReason


class EToroCandles(StreamHandler):
	"""
	Candles from eToro, polled.

	Same constructor contract as data/candles.ForexCandles - pairs,
	granularity, and dtfrom/dtto for an offline run - so a wiring can swap
	one for the other. Three differences are the API's, not this class's:

	* **one price series, not three.** OANDA serves ask, bid and mid;
	  eToro serves one OHLC. Without ETORO_SPREAD or spread.json the event carries mid only
	  and bid/ask stay None, and the provider declines bid_ask_candles so a
	  wiring that needs them refuses to start. See lib/etoro.SpreadModel.
	* **no date range.** The route is "the last N candles", N at most 1000.
	  dtfrom is honoured by discarding what falls before it, but nothing
	  reaches further back than N bars, so this cannot backfill and
	  data/bulksaver.py cannot be pointed here.
	* **no completeness flag.** OANDA marks a candle complete. eToro does
	  not, and its newest candle is the one still forming, so completeness is
	  computed from the candle's own start plus the interval. Emitting a
	  forming bar would have a strategy signal on a high that is not the
	  high.
	"""

	sampling = 10

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs', ['EUR_USD'])
		self._set(args, 'granularity', 'M5')

		self.api = args.get('api') or EToroAPI(setup=self.setup)
		self.interval = interval(self.granularity)
		self.period = granularityToTimedelta(self.granularity)
		self.spread = spreadModel(self.setup)
		self.sleep = int(getattr(self.setup, 'ETORO_POLL_SECONDS', 5))

		# Live, only the newest bars matter, and asking for more would open
		# the session by firing a batch of stale candles at a strategy that
		# would happily signal on a reversal from last Tuesday. data/candles.py
		# asks for two live for the same reason. Offline, reaching back is the
		# whole point - as far as the 1000-candle ceiling allows.
		self.batch_size = 2
		self.live = True
		if self._set(args, 'dtfrom', datetime.datetime(1970, 1, 1, 0, 0, 0)):
			self.live = False
			self._set(args, 'batch_size', 500)
			# UTC, like every timestamp the candle route returns
			self._set(args, 'dtto', utcnow())
			self._set(args, 'sleep', 0)
		else:
			self._set(args, 'batch_size')

		if self.batch_size > 1000:
			raise EToroError(
				"eToro serves at most 1000 candles per request, %d asked for"
				% self.batch_size)

		if not self.spread.enabled():
			self.logger.warning(
				"ETORO_SPREAD is not set and the market folder has no "
				"spread.json (scripts/spread_profile.py): candles will carry mid only, and "
				"any strategy reading candle.ask or candle.bid will fail. "
				"See lib/etoro.SpreadModel.")

		self.last = {}
		self.ids = {}
		self.num_blocks = {}
		self.num_candles = {}
		for pair in self.pairs:
			self.last[pair] = self.dtfrom if not self.live else None
			self.ids[pair] = instrumentId(pair, self.setup)
			self.num_blocks[pair] = 0
			self.num_candles[pair] = 0

	# ------------------------------------------------------------- requests

	def request(self, pair):
		"""The last batch_size candles for one instrument, oldest first."""
		status, payload = self.api.get(
			'candles', parts=(self.ids[pair], 'asc', self.interval,
							  self.batch_size))
		if status != 200 or not payload:
			self.logger.error("%s candles: status %s" % (pair, status))
			self.queue_event(StatusEvent('ERROR'))
			return None
		return payload

	def rows(self, payload):
		"""
		The candle dicts out of the nested response.

		The route answers {"interval": ..., "candles": [{"instrumentId": ...,
		"candles": [...]}]} - a list per instrument even when one was asked
		for.
		"""
		out = []
		for group in payload.get('candles') or []:
			for candle in group.get('candles') or []:
				out.append(candle)
		return out

	def complete(self, when, now=None):
		"""
		Has this candle's period elapsed?

		eToro sends no flag and its newest candle is still forming, so the
		question is answered from the candle's start and the interval. With
		no period known for the granularity, nothing is called complete:
		refusing to emit is recoverable, emitting a partial bar is not.

		Was: the comparison was against datetime.today(), which is local
		     while `when` came through candleTime() and is UTC. On a CEST
		     host that made every candle look two hours older than it was, so
		     the bar still forming passed this check and was published - the
		     one thing the check exists to prevent. Only real data showed it:
		     a fixture's timestamps are as naive as the clock they are
		     compared with.
		Now: utcnow(), so both sides are UTC.
		"""
		if self.period is None:
			return False
		now = now if now is not None else utcnow()
		return when + self.period <= now

	def event(self, pair, row):
		"""One CandleEvent, with bid/ask only if a spread model was configured."""
		when = candleTime(row.get('fromDate'))
		ohlc = {}
		for short, full in (('o', 'open'), ('h', 'high'),
							('l', 'low'), ('c', 'close')):
			try:
				ohlc[short] = float(row[full])
			except (KeyError, TypeError, ValueError):
				ohlc[short] = 0.0

		payload = {
			# eToro reports volume as null on forex, and 0.0 at the range
			# level, so there is no volume to record. It is carried as 0
			# because CandleEvent.to_dict() coerces it with int(), and
			# nothing here reads it - but 0 is the absence of the figure,
			# not a measurement of no trading.
			'time': when,
			'volume': row.get('volume') or 0,
			'complete': True,
			'mid': ohlc,
		}
		bid, ask = self.spread.apply(pair, ohlc, when, self.period)
		if bid is not None:
			payload['bid'] = bid
			payload['ask'] = ask

		event = CandleEvent(payload)
		event.instrument = pair
		event.granularity = self.granularity
		return event

	# ---------------------------------------------------------------- stream

	def poll(self, pair, now=None):
		"""Emit whatever complete candles this instrument has that are new."""
		payload = self.request(pair)
		if payload is None:
			return 0

		self.num_blocks[pair] += 1
		sent = 0
		for row in self.rows(payload):
			when = candleTime(row.get('fromDate'))
			if not self.complete(when, now):
				continue
			if self.last[pair] is not None and when <= self.last[pair]:
				continue
			if not self.live and when > self.dtto:
				continue
			event = self.event(pair, row)
			self.last[pair] = when
			sent += 1
			self.queue_event(event)
		self.num_candles[pair] += sent
		return sent

	def stream_to_queue(self):
		try:
			self.queue_event(StatusEvent('STARTED'))
			while True:
				for pair in self.pairs:
					sent = self.poll(pair)
					if not self.live:
						self.logger.debug("%s %d block sent %d candles"
										  % (pair, self.num_blocks[pair], sent))

				if not self.live:
					# One pass is all an offline run can do: the route has no
					# date range, so asking again returns the same window.
					self.logger.info("Offline pass done; eToro serves no "
									 "further history")
					self.queue_event(StatusEvent('DONE'))
					return

				if self.sleep and self.sleep > 0:
					time.sleep(self.sleep)
		except Exception as exc:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			self.queue_event(StatusEvent('ERROR'))
			self.logger.debug("ETORO CANDLES ERROR: @%d : %s"
							  % (exc_tb.tb_lineno, str(exc)))
			return None


class EToroRates(StreamHandler):
	"""
	Bid/ask for the configured instruments, polled.

	This is the only place eToro serves a real bid and a real ask, which is
	why it exists even though nothing in the trading path requires ticks: it
	is how a spread can be measured before being written into ETORO_SPREAD,
	instead of guessed.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs', ['EUR_USD'])

		self.api = args.get('api') or EToroAPI(setup=self.setup)
		self.sleep = int(getattr(self.setup, 'ETORO_POLL_SECONDS', 5))
		self.ids = dict((p, instrumentId(p, self.setup)) for p in self.pairs)
		self.running = True

	def quit(self):
		self.running = False

	def poll(self):
		ids = ",".join(str(self.ids[p]) for p in self.pairs)
		status, payload = self.api.get('rates', params={'instrumentIds': ids})
		if status not in (200, 206) or not payload:
			self.logger.error("rates: status %s" % status)
			self.queue_event(StatusEvent('ERROR'))
			return 0

		sent = 0
		for row in payload.get('results') or []:
			name = instrumentName(row.get('instrumentId'), self.setup)
			if name is None:
				continue
			if row.get('bid') is None or row.get('ask') is None:
				continue
			self.queue_event(TickEvent({
				'type': 'TICK',
				'instrument': name,
				'time': candleTime(row.get('date')),
				'bid': float(row['bid']),
				'ask': float(row['ask']),
				'quoteType': row.get('quoteType'),
			}))
			sent += 1
		return sent

	def stream_to_queue(self):
		try:
			while self.running:
				self.poll()
				if self.sleep and self.sleep > 0:
					time.sleep(self.sleep)
		except Exception as exc:
			self.queue_event(StatusEvent('ERROR'))
			self.logger.error("ETORO RATES ERROR: %s" % str(exc))
			return None


class EToroTransactions(StreamHandler):
	"""
	The fills and closes OANDA would have pushed, asked for instead.

	It is both a handler and a source. As a handler it listens for the
	CLIENTORDER acknowledgements the execution handler publishes, which is
	how it learns an order exists without being told by the wiring; as a
	source it polls the two routes that can answer for those orders and
	publishes TransactionEvents in the shape the money manager and the parity
	monitor already read.

	Because the poll is a loop over known orders, an order nobody
	acknowledged is invisible here - a position opened from eToro's own app,
	say. That is a real blind spot and the honest place to state it: the
	parity monitor counts unpaired trades separately for exactly this kind of
	reason, and a position this handler never saw will show up there.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs', ['EUR_USD'])

		self.api = args.get('api') or EToroAPI(setup=self.setup)
		self.sleep = int(getattr(self.setup, 'ETORO_POLL_SECONDS', 5))
		self.enforce_expiry = bool(
			getattr(self.setup, 'ETORO_ENFORCE_EXPIRY', True))

		#: orderID -> what we know about an order we are waiting on
		self.orders = {}
		#: positionId -> the open position a fill produced
		self.positions = {}
		#: positionIds already reported closed, so a re-read does not repeat
		self.closed = set()
		self.running = True

	def quit(self):
		self.running = False

	# ------------------------------------------------------------- listening

	def execute_event(self, event):
		kind = str(event)
		if kind == 'CLIENTORDER':
			return self.watch(event)
		if kind == 'CLOSETRADE':
			return self.closeTrades(event)
		if kind == 'ORDERCANCEL':
			# The execution handler sends the DELETE; drop the order here so
			# the poll stops asking about it.
			self.orders.pop(int(getattr(event, 'orderID', 0)), None)

	def watch(self, event):
		"""Start following an order the execution handler just placed."""
		try:
			order_id = int(event.id)
		except (AttributeError, TypeError, ValueError):
			self.logger.error("CLIENTORDER without a usable id: %s"
							  % event.to_json())
			return
		self.orders[order_id] = {
			'orderID': order_id,
			'signalNumber': getattr(event, 'signalNumber', None),
			'instrument': getattr(event, 'instrument', None),
			'price': getattr(event, 'price', None),
			'units': getattr(event, 'units', None),
			'gtdTime': getattr(event, 'gtdTime', None),
			'stopLoss': getattr(event, 'stopLoss', None),
			'takeProfit': getattr(event, 'takeProfit', None),
		}
		self.logger.debug("watching eToro order %d for signal %s"
						  % (order_id, self.orders[order_id]['signalNumber']))

	# ----------------------------------------------------------------- fills

	def lookup(self, order_id):
		status, payload = self.api.get('order_lookup',
									   params={'orderId': str(order_id)})
		if status != 200 or not payload:
			self.logger.error("order %s lookup: status %s" % (order_id, status))
			return None
		return payload

	def pollOrders(self, now=None):
		"""Ask after every order still without an outcome."""
		sent = 0
		for order_id in list(self.orders):
			known = self.orders[order_id]
			payload = self.lookup(order_id)
			if payload is None:
				continue

			state = (payload.get('status') or {})
			state_id = state.get('id')

			if state_id in STATUS_EXECUTED:
				sent += self.reportFill(known, payload)
				del self.orders[order_id]
				continue

			if state_id in STATUS_REFUSED:
				self.logger.error(
					"eToro rejected order %s: %s %s"
					% (order_id, state.get('errorCode'),
					   state.get('errorMessage')))
				self.queue_event(TransactionEvent({
					'type': 'ORDER_REJECT',
					'orderID': order_id,
					'instrument': known['instrument'],
					'price': known['price'],
					'signalNumber': known['signalNumber'],
					'rejectReason': state.get('errorCode'),
					'errorMessage': state.get('errorMessage'),
					'time': candleTime(payload.get('lastUpdate')),
				}))
				del self.orders[order_id]
				continue

			if state_id in STATUS_GONE:
				self.logger.info("eToro order %s is %s"
								 % (order_id, state.get('name')))
				del self.orders[order_id]
				continue

			if state_id in STATUS_RESTING and self.enforce_expiry:
				self.expire(known, now)

		return sent

	def reportFill(self, known, payload):
		"""
		Publish one ORDER_FILL per position the order opened.

		The fields are named as OANDA names them, because that is what the
		money manager and the parity monitor read. avgPrice is the fill, and
		the position id is carried along so the close can be joined back to
		this signal later.
		"""
		sent = 0
		name = known['instrument'] or instrumentName(
			(payload.get('asset') or {}).get('instrumentId'), self.setup)
		short = (payload.get('asset') or {}).get('side') == 'short'

		for execution in payload.get('positionExecutions') or []:
			opening = execution.get('openingData') or {}
			units = opening.get('units')
			if units is not None:
				units = -abs(float(units)) if short else abs(float(units))
			position_id = execution.get('positionId')

			event = TransactionEvent({
				'type': 'ORDER_FILL',
				'id': position_id,
				'orderID': known['orderID'],
				'instrument': name,
				'units': units,
				'price': opening.get('avgPrice'),
				'time': candleTime(opening.get('executionTime')
								   or opening.get('openTime')),
				'reason': 'MARKET_ORDER' if payload.get('type') == 'mkt'
						  else 'LIMIT_ORDER',
				'signalNumber': known['signalNumber'],
				'positionId': position_id,
				'marketSpread': opening.get('marketSpread'),
				'financing': opening.get('fees'),
				'referenceId': payload.get('referenceId'),
			})
			self.queue_event(event)
			sent += 1

			if execution.get('state') != 'closed':
				self.positions[position_id] = {
					'positionId': position_id,
					'orderID': known['orderID'],
					'signalNumber': known['signalNumber'],
					'instrument': name,
					'units': units,
					'openRate': opening.get('avgPrice'),
					'stopLoss': execution.get('stopLossRate'),
					'takeProfit': execution.get('takeProfitRate'),
					'opened': candleTime(opening.get('openTime')
										 or opening.get('executionTime')),
				}
		return sent

	# ---------------------------------------------------------------- closes

	def closeTrades(self, event):
		"""
		A CloseTradeEvent: every position this handler opened on the
		instrument, closed at market through the market-close route. The
		close comes back through the trade history like any other.
		"""
		instrument = getattr(event, 'instrument', None)
		done = 0
		for position_id, position in list(self.positions.items()):
			if position.get('instrument') != instrument:
				continue
			status, payload = self.api.post(
				'close_position', parts=(position_id,),
				body={'InstrumentId': instrumentId(instrument, self.setup),
					  'UnitsToDeduct': None})
			self.logger.info("CLOSE %s %s (%s): status %s %s"
							 % (instrument, position_id, getattr(event, 'reason', None),
								status, payload))
			done += status in (200, 201)
		return done

	def since(self):
		"""
		The minDate for the history read: the oldest position still open.

		Required by the route, and deliberately not widened to a fixed
		lookback - a wider window costs nothing in requests but pulls in
		trades from before this session, which have no signal to be joined
		to and would be reported as closes that never opened here.
		"""
		days = [p['opened'] for p in self.positions.values()
				if p.get('opened') is not None]
		# UTC: the opened times came through candleTime(), and near midnight
		# a local clock would ask for the wrong day
		when = min(days) if days else utcnow()
		return when.strftime('%Y-%m-%d')

	def history(self):
		status, payload = self.api.get('trade_history',
									   params={'minDate': self.since()})
		if status != 200 or payload is None:
			self.logger.error("trade history: status %s" % status)
			return None
		if isinstance(payload, dict):
			payload = payload.get('trades') or payload.get('results') or []
		return payload

	def pollCloses(self):
		"""Report the open positions the history now says are closed."""
		if not self.positions:
			return 0
		rows = self.history()
		if rows is None:
			return 0

		sent = 0
		for row in rows:
			position_id = row.get('positionId')
			if position_id not in self.positions:
				continue
			if position_id in self.closed:
				continue
			sent += self.reportClose(self.positions.pop(position_id), row)
			self.closed.add(position_id)
		return sent

	def reportClose(self, position, row):
		"""
		Publish the close of one position.

		reason is inferred from the closing rate - see closeReason() - and
		the event says so, so nothing downstream mistakes an inference for
		what the broker reported. accountBalance is None because the history
		does not carry it.
		"""
		reason = closeReason(row.get('closeRate'),
							 row.get('stopLossRate', position.get('stopLoss')),
							 row.get('takeProfitRate', position.get('takeProfit')))
		if reason == UNKNOWN:
			self.logger.warning(
				"position %s closed at %s, which is not clearly the stop (%s) "
				"or the target (%s); reporting the outcome as UNKNOWN"
				% (position['positionId'], row.get('closeRate'),
				   row.get('stopLossRate'), row.get('takeProfitRate')))

		self.queue_event(TransactionEvent({
			'type': 'ORDER_FILL',
			'orderID': position['orderID'],
			'instrument': position['instrument'],
			'price': row.get('closeRate'),
			'units': position['units'],
			'time': candleTime(row.get('closeTimestamp')),
			'pl': row.get('netProfit'),
			'financing': row.get('fees'),
			# not in the trade history; inventing a number here would put a
			# figure in the ledger that no read ever returned
			'accountBalance': None,
			'tradesClosed': [{'tradeID': position['positionId'],
							  'units': position['units'],
							  'price': row.get('closeRate')}],
			'reason': reason,
			'reasonInferred': True,
			'signalNumber': position['signalNumber'],
			'positionId': position['positionId'],
			'openRate': row.get('openRate'),
		}))
		return 1

	# ---------------------------------------------------------------- expiry

	def expire(self, known, now=None):
		"""
		Cancel a resting order whose own expiry has passed.

		eToro has no expiry on an order, and the strategies here place orders
		that are meant to die at the end of the day - AG01 brackets one
		reversal, and a bracket still resting a week later is not that trade
		any more. The gtdTime the order was issued with is honoured on our
		side: this publishes an OrderCancelEvent, which the execution handler
		turns into a DELETE and the simulator applies to its own book, so
		both sides drop the order together.

		This is our action, not the broker's. ETORO_ENFORCE_EXPIRY turns it
		off, and then a resting order rests until it triggers.
		"""
		when = known.get('gtdTime')
		if when is None:
			return False
		# UTC, because that is the clock gtdTime is on: the strategies
		# build it from the candle's own timestamp (lib/utils.expiryAt), and
		# the candles are UTC. It used to be the machine's local time, back
		# when the expiry came from datetime.today() - on a machine that is
		# not on UTC the two differ by its offset, and the order died early
		# or outlived its day by that much.
		now = now if now is not None else datetime.datetime.utcnow()
		if when > now:
			return False

		self.logger.info(
			"eToro order %s is past its gtdTime %s; cancelling it here, "
			"since eToro does not expire orders"
			% (known['orderID'], when))
		self.queue_event(OrderCancelEvent({
			'orderID': known['orderID'],
			'price': known['price'],
			'instrument': known['instrument'],
			'reason': 'GTD_EXPIRY_ENFORCED',
		}))
		self.orders.pop(known['orderID'], None)
		return True

	# ---------------------------------------------------------------- stream

	def poll(self, now=None):
		return self.pollOrders(now) + self.pollCloses()

	def stream_to_queue(self):
		try:
			while self.running:
				self.poll()
				if self.sleep and self.sleep > 0:
					time.sleep(self.sleep)
		except Exception as exc:
			self.queue_event(StatusEvent('ERROR'))
			self.logger.error("ETORO TRANSACTIONS ERROR: %s" % str(exc))
			return None
