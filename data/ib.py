"""
Interactive Brokers as a data source: bars, quotes, and orders to poll.

The three classes here stand in for data/candles.py, data/streaming.py and
data/transaction.py. What IB gives and what it does not:

* **one price series per bar.** The history route serves a single OHLC, the
  way eToro does, so AG01's ask-high and bid-low do not exist in it. IB_SPREAD
  (or the shared spread.json) is the same model under the same rule: unset
  it stays off, candles carry mid
  only, the provider declines bid_ask_candles, and a strategy that needs them
  refuses to start. See lib/spread.py.
* **history by date.** A start time and a duration, so an offline run can
  reach back - unlike eToro, where "the last N" is all there is.
* **nothing pushed.** IB does push, over a WebSocket on the same gateway, and
  that is a second protocol this module does not speak. So bars and orders are
  polled.
* **which leg closed a trade, stated rather than inferred.** This is the one
  place IB is *better* than eToro and IG, and it falls out of how a bracket
  has to be built: IB has no stop-and-limit attached to an entry, so the stop
  and the target are separate child orders with ids of their own. When one of
  them fills, the broker has named the leg - no inference from the closing
  price, no UNKNOWN. The price of that is that a bracket is three orders
  rather than one, and IBTransactions has to follow all three.

The join back to a signal is IB's own doing and is the nicest of the three
brokers': the client order id this project derives from the signal comes back
as ``order_ref`` on the order list, so one poll answers for every outstanding
order at once and needs no table of broker ids in between.
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
from parity_deriva.lib.closereason import STOP_LOSS, TAKE_PROFIT
from parity_deriva.lib.ib import (FIELD_ASK, FIELD_BID, IBAPI,
								  IBNotAuthenticated, bar, barTime, conid,
								  duration, instrumentName, spreadModel,
								  startTime, utcnow)
from parity_deriva.lib.utils import granularityToTimedelta
from parity_deriva.trading.handler import StreamHandler


#: IB's order states, as the order list and the status route spell them. The
#: sets matter more than the individual names: what the poll needs to know is
#: whether an order is still on its way to an outcome, has produced a fill, or
#: never will.
FILLED = 'Filled'
SUBMITTED = 'Submitted'
PRESUBMITTED = 'PreSubmitted'
PENDING_SUBMIT = 'PendingSubmit'
PENDING_CANCEL = 'PendingCancel'
CANCELLED = 'Cancelled'
INACTIVE = 'Inactive'
REJECTED = 'Rejected'

#: resting on the book, and therefore still cancellable
RESTING = frozenset([SUBMITTED, PRESUBMITTED])
#: the order will not produce a position
REFUSED = frozenset([REJECTED, INACTIVE])
GONE = frozenset([CANCELLED])


class IBCandles(StreamHandler):
	"""
	Bars from IB, polled.

	Same constructor contract as data/candles.ForexCandles - pairs,
	granularity, and dtfrom/dtto for an offline run - so a wiring can swap one
	for the other.

	Three things worth knowing about the route:

	* **the newest bar is still forming.** There is no completeness flag, so
	  completeness is computed from the bar's own start plus the interval.
	  Emitting a forming bar would have a strategy signal on a high that is
	  not yet the high.
	* **it takes a duration, not an end.** So an offline window is expressed
	  as "this much, from here", rounded up, and whatever falls outside
	  dtfrom/dtto is discarded locally. That local filter is also what makes
	  the request safe: if IB reads startTime differently than this code does,
	  the result is fewer bars, never bars from the wrong window.
	* **priceFactor.** IB reports a scaling factor alongside the points and
	  documents the prices as needing it. It is 1 for the instruments this
	  project deals; where it is not, it is applied and said out loud, because
	  a factor silently ignored is every level wrong by a power of ten.
	"""

	sampling = 10

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs', ['EUR_USD'])
		self._set(args, 'granularity', 'M5')

		self.api = args.get('api') or IBAPI(setup=self.setup)
		self.bar = bar(self.granularity)
		self.period = granularityToTimedelta(self.granularity)
		self.spread = spreadModel(self.setup)
		self.sleep = int(getattr(self.setup, 'IB_POLL_SECONDS', 5))
		self.outside_rth = bool(getattr(self.setup, 'IB_OUTSIDE_RTH', True))

		self.batch_size = 2
		self.live = True
		if self._set(args, 'dtfrom', datetime.datetime(1970, 1, 1, 0, 0, 0)):
			self.live = False
			self._set(args, 'batch_size', 500)
			self._set(args, 'dtto', utcnow())
			self._set(args, 'sleep', 0)
		else:
			self._set(args, 'batch_size')

		if not self.spread.enabled():
			self.logger.warning(
				"IB_SPREAD is not set and the market folder has no spread.json "
				"(scripts/spread_profile.py): bars will carry mid only, and any "
				"strategy reading candle.ask or candle.bid will fail. See "
				"lib/spread.py.")

		self.last = {}
		self.conids = {}
		self.num_blocks = {}
		self.num_candles = {}
		for pair in self.pairs:
			self.last[pair] = self.dtfrom if not self.live else None
			self.conids[pair] = conid(pair, self.setup)
			self.num_blocks[pair] = 0
			self.num_candles[pair] = 0

	# ------------------------------------------------------------- requests

	def params(self, pair):
		"""
		What to ask for: a short window live, the configured range offline.

		Live, the period is a few bars' worth rather than a fixed string, so
		changing the granularity does not quietly change how much history each
		poll drags back.
		"""
		params = {'conid': self.conids[pair], 'bar': self.bar,
				  'outsideRth': 'true' if self.outside_rth else 'false'}
		if self.live:
			span = (self.period * max(self.batch_size, 2)
					if self.period is not None else None)
			params['period'] = duration(span) if span is not None else '1d'
			return params
		start = self.last[pair] or self.dtfrom
		params['startTime'] = startTime(start)
		params['period'] = duration(self.dtto - start)
		return params

	def request(self, pair):
		try:
			status, payload = self.api.get('history', params=self.params(pair))
		except IBNotAuthenticated as exc:
			# Not retried and not swallowed: no amount of polling logs a human
			# into a browser, so the run says what is wrong and stops asking.
			self.logger.error(str(exc))
			self.queue_event(StatusEvent('ERROR'))
			raise
		if status != 200 or not payload:
			self.logger.error("%s history: status %s" % (pair, status))
			self.queue_event(StatusEvent('ERROR'))
			return None
		return payload

	def factor(self, payload, pair):
		value = (payload or {}).get('priceFactor')
		try:
			value = float(value)
		except (TypeError, ValueError):
			return 1.0
		if value in (0.0, 1.0):
			return 1.0
		self.logger.info("%s: IB reports priceFactor %s; applying it to every "
						 "level in this response" % (pair, value))
		return value

	def complete(self, when, now=None):
		"""
		Has this bar's period elapsed?

		With no period known for the granularity, nothing is called complete:
		refusing to emit is recoverable, emitting a partial bar is not.
		"""
		if self.period is None:
			return False
		now = now if now is not None else utcnow()
		return when + self.period <= now

	def event(self, pair, point, factor=1.0):
		"""One CandleEvent, with bid/ask only if a spread model was configured."""
		ohlc = {}
		for key in ('o', 'h', 'l', 'c'):
			try:
				ohlc[key] = float(point[key]) / factor
			except (KeyError, TypeError, ValueError, ZeroDivisionError):
				ohlc[key] = 0.0

		payload = {
			'time': barTime(point.get('t')),
			'volume': point.get('v') or 0,
			'complete': True,
			'mid': ohlc,
		}
		bid, ask = self.spread.apply(pair, ohlc, payload['time'], self.period)
		if bid is not None:
			payload['bid'] = bid
			payload['ask'] = ask

		event = CandleEvent(payload)
		event.instrument = pair
		event.granularity = self.granularity
		return event

	# ---------------------------------------------------------------- stream

	def poll(self, pair, now=None):
		"""Emit whatever complete bars this instrument has that are new."""
		payload = self.request(pair)
		if payload is None:
			return 0

		factor = self.factor(payload, pair)
		self.num_blocks[pair] += 1
		sent = 0
		for point in payload.get('points') or []:
			when = barTime(point.get('t'))
			if not self.complete(when, now):
				continue
			if self.last[pair] is not None and when <= self.last[pair]:
				continue
			if not self.live and when > self.dtto:
				continue
			event = self.event(pair, point, factor)
			self.last[pair] = when
			sent += 1
			self.queue_event(event)
		self.num_candles[pair] += sent
		return sent

	def stream_to_queue(self):
		try:
			self.queue_event(StatusEvent('STARTED'))
			while True:
				done = 0
				for pair in self.pairs:
					sent = self.poll(pair)
					if not self.live:
						self.logger.debug("%s %d block sent %d bars"
										  % (pair, self.num_blocks[pair], sent))
					done += sent

				if not self.live:
					# The window walks forward on self.last until a pass brings
					# nothing back.
					if done == 0:
						self.logger.info("Offline pass done; no further IB history")
						self.queue_event(StatusEvent('DONE'))
						return
					continue

				if self.sleep and self.sleep > 0:
					time.sleep(self.sleep)
		except IBNotAuthenticated:
			self.queue_event(StatusEvent('ERROR'))
			return None
		except Exception as exc:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			self.queue_event(StatusEvent('ERROR'))
			self.logger.debug("IB CANDLES ERROR: @%d : %s"
							  % (exc_tb.tb_lineno, str(exc)))
			return None


class IBRates(StreamHandler):
	"""
	Bid and ask for the configured instruments, polled.

	This is where IB does serve two sides, which is why it exists even though
	nothing in the trading path needs ticks: it is how the spread can be
	measured before being written into IB_SPREAD rather than guessed.

	One quirk of the route is handled here and nowhere else: the first request
	for a contract starts the subscription and often answers without the
	fields asked for. That is not an error and not an empty market - the
	second request answers. So a reply missing bid or ask is skipped quietly,
	and only a run of them is worth a word.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs', ['EUR_USD'])

		self.api = args.get('api') or IBAPI(setup=self.setup)
		self.sleep = int(getattr(self.setup, 'IB_POLL_SECONDS', 5))
		self.conids = dict((p, conid(p, self.setup)) for p in self.pairs)
		self.empty = 0
		self.running = True

	def quit(self):
		self.running = False

	def poll(self):
		ids = ",".join(str(self.conids[p]) for p in self.pairs)
		status, payload = self.api.get(
			'snapshot', params={'conids': ids,
								'fields': "%s,%s" % (FIELD_BID, FIELD_ASK)})
		if status != 200 or payload is None:
			self.logger.error("snapshot: status %s" % status)
			self.queue_event(StatusEvent('ERROR'))
			return 0

		rows = payload if isinstance(payload, list) else [payload]
		sent = 0
		for row in rows:
			name = instrumentName(row.get('conid'), self.setup) \
				if row.get('conid') is not None else None
			if name is None:
				continue
			bid, ask = row.get(FIELD_BID), row.get(FIELD_ASK)
			if bid is None or ask is None:
				continue
			try:
				bid, ask = float(bid), float(ask)
			except (TypeError, ValueError):
				continue
			self.queue_event(TickEvent({
				'type': 'TICK',
				'instrument': name,
				'time': utcnow(),
				'bid': bid,
				'ask': ask,
			}))
			sent += 1

		if sent == 0:
			self.empty += 1
			if self.empty in (5, 50):
				self.logger.warning(
					"%d snapshot replies in a row without a bid and an ask. "
					"The first request for a contract only starts the "
					"subscription, but this many means the market is shut or "
					"the account has no data permission for it." % self.empty)
		else:
			self.empty = 0
		return sent

	def stream_to_queue(self):
		try:
			while self.running:
				self.poll()
				if self.sleep and self.sleep > 0:
					time.sleep(self.sleep)
		except IBNotAuthenticated as exc:
			self.logger.error(str(exc))
			self.queue_event(StatusEvent('ERROR'))
			return None
		except Exception as exc:
			self.queue_event(StatusEvent('ERROR'))
			self.logger.error("IB RATES ERROR: %s" % str(exc))
			return None


class IBTransactions(StreamHandler):
	"""
	The fills and closes OANDA would have pushed, asked for instead.

	Both a handler and a source: as a handler it listens for the CLIENTORDER
	acknowledgements the execution handler publishes, which is how it learns
	an order exists; as a source it polls the order list and publishes
	TransactionEvents in the shape the money manager and the parity monitor
	already read.

	The bracket is what makes this different from its eToro and IG
	counterparts. An entry here is three orders - the entry, a stop child and
	a target child - so this class follows a *group*, and the group is what
	answers the question the other two polled brokers cannot: the child that
	filled names the leg that closed the trade, so the close event carries a
	reason the broker stated instead of one inferred from a price.

	An order nobody acknowledged is invisible here - something dealt from
	TWS, say. The parity monitor counts unpaired trades separately for exactly
	this kind of reason.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs', ['EUR_USD'])

		self.api = args.get('api') or IBAPI(setup=self.setup)
		self.sleep = int(getattr(self.setup, 'IB_POLL_SECONDS', 5))
		self.enforce_expiry = bool(getattr(self.setup, 'IB_ENFORCE_EXPIRY', True))

		#: cOID of the entry -> everything known about that bracket
		self.groups = {}
		#: cOID of a child -> the entry's cOID, so a fill finds its group
		self.children = {}
		#: entries already reported filled / closed, so a re-read does not repeat
		self.opened = set()
		self.closed = set()
		self.running = True

	def quit(self):
		self.running = False

	# ------------------------------------------------------------- listening

	def execute_event(self, event):
		kind = str(event)
		if kind == 'CLIENTORDER':
			return self.watch(event)
		if kind == 'ORDERCANCEL':
			reference = getattr(event, 'clientOrderId', None)
			if reference is not None:
				self.forget(reference)

	def watch(self, event):
		"""Start following the bracket the execution handler just placed."""
		reference = getattr(event, 'clientOrderId', None) or getattr(event, 'id', None)
		if reference is None:
			self.logger.error("CLIENTORDER without a client order id: %s"
							  % event.to_json())
			return
		stop_id = getattr(event, 'stopChildId', None)
		target_id = getattr(event, 'targetChildId', None)
		self.groups[reference] = {
			'clientOrderId': reference,
			'orderId': getattr(event, 'id', None),
			'signalNumber': getattr(event, 'signalNumber', None),
			'instrument': getattr(event, 'instrument', None),
			'price': getattr(event, 'price', None),
			'units': getattr(event, 'units', None),
			'gtdTime': getattr(event, 'gtdTime', None),
			'stopLoss': getattr(event, 'stopLoss', None),
			'takeProfit': getattr(event, 'takeProfit', None),
			'stopChildId': stop_id,
			'targetChildId': target_id,
			'openPrice': None,
		}
		for child, leg in ((stop_id, STOP_LOSS), (target_id, TAKE_PROFIT)):
			if child is not None:
				self.children[child] = (reference, leg)
		self.logger.debug("watching IB bracket %s for signal %s"
						  % (reference, self.groups[reference]['signalNumber']))

	def forget(self, reference):
		group = self.groups.pop(reference, None)
		if group is None:
			return
		for key in ('stopChildId', 'targetChildId'):
			if group.get(key) is not None:
				self.children.pop(group[key], None)

	# ----------------------------------------------------------------- polls

	def orders(self):
		"""
		Every order IB currently knows about, keyed by our own reference.

		One request answers for the whole book, because IB reports the client
		order id back as ``order_ref``. Orders it does not recognise as ours
		are ignored rather than reported: something dealt from TWS is not a
		signal this stack produced.
		"""
		status, payload = self.api.get('orders')
		if status != 200 or payload is None:
			self.logger.error("orders: status %s" % status)
			return None
		out = {}
		for row in (payload.get('orders') or []):
			reference = row.get('order_ref')
			if reference:
				out[reference] = row
		return out

	def fillPrice(self, row):
		"""
		What an order actually filled at.

		The order list does not carry an average price, so a filled order
		costs one extra request to the status route. Its own 'price' is the
		price the order was *placed* at, and reporting that as the fill would
		quietly hide every bit of slippage - which is one of the two things
		the parity monitor exists to measure.
		"""
		order_id = row.get('orderId')
		if order_id is None:
			return None
		status, payload = self.api.get('order_status', parts=(order_id,))
		if status != 200 or not payload:
			self.logger.error("order %s status: status %s" % (order_id, status))
			return None
		for key in ('average_price', 'avgPrice', 'avg_price'):
			if payload.get(key) not in (None, ''):
				try:
					return float(payload[key])
				except (TypeError, ValueError):
					return None
		return None

	def poll(self, now=None):
		book = self.orders()
		if book is None:
			return 0

		sent = 0
		for reference in list(self.groups):
			group = self.groups[reference]
			row = book.get(reference)
			if row is None:
				continue
			state = str(row.get('status') or '')

			if state == FILLED and reference not in self.opened:
				sent += self.reportFill(group, row)
				self.opened.add(reference)
			elif state in REFUSED:
				self.logger.error("IB refused order %s: %s"
								  % (reference, row.get(
									  'order_cancellation_by_system_reason')))
				self.queue_event(TransactionEvent({
					'type': 'ORDER_REJECT',
					'clientOrderId': reference,
					'orderID': row.get('orderId'),
					'instrument': group['instrument'],
					'price': group['price'],
					'units': group['units'],
					'signalNumber': group['signalNumber'],
					'rejectReason': row.get(
						'order_cancellation_by_system_reason') or state,
				}))
				self.forget(reference)
				continue
			elif state in GONE:
				self.logger.info("IB order %s is %s" % (reference, state))
				self.forget(reference)
				continue
			elif state in RESTING and self.enforce_expiry:
				self.expire(group, now)
				continue

			sent += self.pollChildren(group, book)
		return sent

	def pollChildren(self, group, book):
		"""
		Has the stop or the target taken the trade?

		This is the whole benefit of IB's bracket being three orders: the
		child that filled *is* the leg, so the close carries a reason the
		broker stated rather than one read off the closing price. Nothing here
		goes near lib/closereason.py, and the event does not claim the reason
		was inferred, because it was not.
		"""
		reference = group['clientOrderId']
		if reference not in self.opened or reference in self.closed:
			return 0
		for child, leg in ((group.get('stopChildId'), STOP_LOSS),
						   (group.get('targetChildId'), TAKE_PROFIT)):
			if child is None:
				continue
			row = book.get(child)
			if row is None or str(row.get('status') or '') != FILLED:
				continue
			self.closed.add(reference)
			sent = self.reportClose(group, row, leg)
			self.forget(reference)
			return sent
		return 0

	def reportFill(self, group, row):
		"""Publish the fill of an entry order."""
		price = self.fillPrice(row)
		if price is None:
			self.logger.warning(
				"IB order %s filled and its average price could not be read; "
				"reporting the level it was placed at, which hides any "
				"slippage" % group['clientOrderId'])
			price = row.get('price')
		group['openPrice'] = price
		self.queue_event(TransactionEvent({
			'type': 'ORDER_FILL',
			'id': row.get('orderId'),
			'orderID': row.get('orderId'),
			'clientOrderId': group['clientOrderId'],
			'instrument': group['instrument'] or instrumentName(row.get('conid'),
																self.setup),
			'units': group['units'],
			'price': price,
			'time': utcnow(),
			'reason': 'MARKET_ORDER' if str(row.get('origOrderType') or
											'').upper() == 'MKT'
					  else 'LIMIT_ORDER',
			'signalNumber': group['signalNumber'],
		}))
		return 1

	def reportClose(self, group, row, leg):
		"""
		Publish the close of a position, naming the leg IB filled.

		accountBalance is None: the order routes do not carry it, and reading
		it separately would be a balance taken at a different instant than the
		close.
		"""
		price = self.fillPrice(row) or row.get('price')
		self.queue_event(TransactionEvent({
			'type': 'ORDER_FILL',
			'orderID': row.get('orderId'),
			'clientOrderId': group['clientOrderId'],
			'instrument': group['instrument'],
			'price': price,
			'units': group['units'],
			'time': utcnow(),
			'accountBalance': None,
			'tradesClosed': [{'tradeID': group['clientOrderId'],
							  'units': group['units'],
							  'price': price}],
			# Stated by the broker, not read off the price: IB filled this
			# particular child order and this project knows which leg it
			# placed it as.
			'reason': leg,
			'reasonInferred': False,
			'signalNumber': group['signalNumber'],
			'openLevel': group.get('openPrice'),
		}))
		return 1

	# ---------------------------------------------------------------- expiry

	def expire(self, group, now=None):
		"""
		Cancel a resting entry whose own expiry has passed.

		The Web API's time-in-force values are DAY, GTC and the immediate
		ones - there is no arbitrary expiry instant - so the strategies'
		end-of-day gtdTime is honoured on our side, the way it is on eToro:
		publish an OrderCancelEvent, which the execution handler turns into a
		delete and the simulator applies to its own book, so both sides drop
		the order together.
		"""
		when = group.get('gtdTime')
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
			"IB order %s is past its gtdTime %s; cancelling it here, since "
			"this API has no expiry instant" % (group['clientOrderId'], when))
		self.queue_event(OrderCancelEvent({
			'clientOrderId': group['clientOrderId'],
			'orderID': group.get('orderId'),
			'price': group['price'],
			'instrument': group['instrument'],
			'reason': 'GTD_EXPIRY_ENFORCED',
		}))
		self.forget(group['clientOrderId'])
		return True

	# ---------------------------------------------------------------- stream

	def stream_to_queue(self):
		try:
			while self.running:
				self.poll()
				if self.sleep and self.sleep > 0:
					time.sleep(self.sleep)
		except IBNotAuthenticated as exc:
			self.logger.error(str(exc))
			self.queue_event(StatusEvent('ERROR'))
			return None
		except Exception as exc:
			self.queue_event(StatusEvent('ERROR'))
			self.logger.error("IB TRANSACTIONS ERROR: %s" % str(exc))
			return None
