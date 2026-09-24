"""
MetaTrader 5 as a data source: candles, and fills that have to be asked for.

The package pushes nothing, so both are polled - but what the polls return
is better than eToro's or IG's:

* **bars carry bid and spread.** MT5 bars are bid prices with the spread of
  the bar in points, so ask is bid plus that; mid is their average. The
  newest bar is the one still forming and is never read (start_pos 1).
* **a deal says why it happened.** A closing deal's reason is SL or TP when
  the broker's stop or target took it, so the leg is reported, not inferred.
* **everything is on the broker's clock**, and converted to UTC on the way
  in by lib/mt5.toUTC.

MT5Transactions learns of orders from the CLIENTORDER events the execution
handler publishes, like its eToro and IG counterparts, and so it has the
same blind spot: a position opened by hand in the terminal is not followed.
"""

import datetime
import logging
import sys
import time

from parity_deriva.etc import settings
from parity_deriva.event.event import CandleEvent
from parity_deriva.event.event import OrderCancelEvent
from parity_deriva.event.event import StatusEvent
from parity_deriva.event.event import TransactionEvent
from parity_deriva.lib import mt5 as lib
from parity_deriva.lib.closereason import STOP_LOSS, TAKE_PROFIT
from parity_deriva.lib.utils import granularityToTimedelta
from parity_deriva.trading.handler import StreamHandler


def utcnow():
	return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class MT5Candles(StreamHandler):
	"""
	Complete bars from the terminal. Same constructor contract as
	data/candles.ForexCandles: pairs, granularity, and dtfrom/dtto for an
	offline run.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs', ['EUR_USD'])
		self._set(args, 'granularity', 'M5')
		self.api = args.get('api') or lib.connect(self.setup)
		self.timeframe = lib.timeframe(self.granularity)
		self.period = granularityToTimedelta(self.granularity)
		self.sleep = int(getattr(self.setup, 'MT5_POLL_SECONDS', 5))

		self.live = True
		if self._set(args, 'dtfrom', datetime.datetime(1970, 1, 1)):
			self.live = False
			self._set(args, 'dtto', utcnow())
		self.symbols = dict((p, lib.symbol(p, self.setup)) for p in self.pairs)
		self.points = {}
		self.last = dict((p, None) for p in self.pairs)
		for pair, sym in self.symbols.items():
			self.api.symbol_select(sym, True)
			info, _ = self.api.symbol_info(sym)
			if not info:
				raise lib.MT5Error("MT5 has no symbol %s for %s" % (sym, pair))
			self.points[pair] = float(info['point'])

	def rates(self, pair):
		sym = self.symbols[pair]
		if self.live:
			# the two newest complete bars, as data/candles.py asks for live
			rows, error = self.api.copy_rates_from_pos(sym, self.timeframe, 1, 2)
		else:
			rows, error = self.api.copy_rates_range(
				sym, self.timeframe, self.api.toServer(self.dtfrom, sym),
				self.api.toServer(self.dtto, sym))
		if rows is None:
			self.logger.error("%s rates: %s" % (pair, error))
			self.queue_event(StatusEvent('ERROR'))
			return []
		return rows

	def event(self, pair, row, when):
		point = self.points[pair]
		bid = dict((k[0], float(row[k])) for k in ('open', 'high', 'low', 'close'))
		spread = float(row['spread']) * point
		ask = dict((k, v + spread) for k, v in bid.items())
		mid = dict((k, v + spread / 2.0) for k, v in bid.items())
		event = CandleEvent({'time': when, 'volume': int(row['tick_volume']),
							 'complete': True, 'bid': bid, 'ask': ask, 'mid': mid})
		event.instrument = pair
		event.granularity = self.granularity
		return event

	def poll(self, pair, now=None):
		now = now if now is not None else utcnow()
		sent = 0
		for row in self.rates(pair):
			when = self.api.toUTC(row['time'], self.symbols[pair])
			if when + self.period > now:
				continue
			if self.last[pair] is not None and when <= self.last[pair]:
				continue
			self.last[pair] = when
			self.queue_event(self.event(pair, row, when))
			sent += 1
		return sent

	def stream_to_queue(self):
		try:
			self.queue_event(StatusEvent('STARTED'))
			while True:
				for pair in self.pairs:
					self.poll(pair)
				if not self.live:
					self.queue_event(StatusEvent('DONE'))
					return
				time.sleep(self.sleep)
		except Exception as exc:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			self.queue_event(StatusEvent('ERROR'))
			self.logger.error("MT5 CANDLES ERROR: @%d : %s" % (exc_tb.tb_lineno, exc))


class MT5Transactions(StreamHandler):
	"""The fills and closes OANDA would have pushed, asked for instead."""

	#: order types, as the reason an opening fill carries
	REASONS = {'MARKET': 'MARKET_ORDER', 'STOP': 'STOP_ORDER', 'LIMIT': 'LIMIT_ORDER'}

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs', ['EUR_USD'])
		self.api = args.get('api') or lib.connect(self.setup)
		self.sleep = int(getattr(self.setup, 'MT5_POLL_SECONDS', 5))
		self.magic = int(getattr(self.setup, 'MT5_MAGIC', 0))
		self.deviation = int(getattr(self.setup, 'MT5_DEVIATION', 10))
		#: order ticket -> an order we are waiting on
		self.orders = {}
		#: position ticket -> an open position one of those orders produced
		self.positions = {}
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
			self.orders.pop(int(getattr(event, 'orderID', 0) or 0), None)

	def watch(self, event):
		try:
			ticket = int(event.id)
		except (AttributeError, TypeError, ValueError):
			self.logger.error("CLIENTORDER without a usable id: %s" % event.to_json())
			return
		self.orders[ticket] = {
			'orderID': ticket,
			'signalNumber': getattr(event, 'signalNumber', None),
			'instrument': getattr(event, 'instrument', None),
			'price': getattr(event, 'price', None),
			'gtdTime': getattr(event, 'gtdTime', None),
			'orderType': str(getattr(event, 'orderType', '') or '').upper(),
			'contractSize': float(getattr(event, 'contractSize', 1) or 1),
		}

	# ----------------------------------------------------------------- fills

	def pollOrders(self, now=None):
		sent = 0
		for ticket in list(self.orders):
			known = self.orders[ticket]
			pending, _ = self.api.orders_get(ticket=ticket)
			if pending:
				self.expire(known, now)
				continue
			history, _ = self.api.history_orders_get(ticket=ticket)
			if not history:
				# not in the history yet: asked again on the next poll
				continue
			state = history[0]['state']
			if state in (lib.ORDER_STATE_FILLED, lib.ORDER_STATE_PARTIAL):
				sent += self.reportFill(known, history[0]['position_id'])
			elif state == lib.ORDER_STATE_REJECTED:
				self.queue_event(TransactionEvent({
					'type': 'ORDER_REJECT', 'orderID': ticket,
					'instrument': known['instrument'], 'price': known['price'],
					'signalNumber': known['signalNumber'],
					'rejectReason': 'REJECTED'}))
			else:
				self.logger.info("MT5 order %s ended in state %s" % (ticket, state))
			del self.orders[ticket]
		return sent

	def reportFill(self, known, position):
		deals, _ = self.api.history_deals_get(position=position)
		opening = [d for d in deals or [] if d['entry'] == lib.DEAL_ENTRY_IN
				   and d['order'] == known['orderID']]
		if not opening:
			self.logger.error("MT5 order %s filled with no opening deal" % known['orderID'])
			return 0
		deal = opening[0]
		units = deal['volume'] * known['contractSize']
		if deal['type'] == lib.ORDER_TYPE_SELL:
			units = -units
		self.queue_event(TransactionEvent({
			'type': 'ORDER_FILL',
			'id': deal['ticket'],
			'orderID': known['orderID'],
			'instrument': known['instrument'],
			'units': units,
			'price': deal['price'],
			'time': self.api.toUTC(deal['time'], deal['symbol']),
			'reason': self.REASONS.get(known['orderType'], 'MARKET_ORDER'),
			'tradeOpened': {'tradeID': position, 'units': units, 'price': deal['price']},
			'signalNumber': known['signalNumber'],
			'positionId': position,
		}))
		self.positions[position] = dict(known, positionId=position, units=units,
										symbol=deal['symbol'], volume=deal['volume'])
		return 1

	# ---------------------------------------------------------------- closes

	def closeTrades(self, event):
		"""Close every followed position on the instrument, at market."""
		from parity_deriva.execution.mt5 import fillingMode
		instrument = getattr(event, 'instrument', None)
		done = 0
		for ticket, position in list(self.positions.items()):
			if position['instrument'] != instrument:
				continue
			info, _ = self.api.symbol_info(position['symbol'])
			tick, _ = self.api.symbol_info_tick(position['symbol'])
			buy = position['units'] > 0
			result, error = self.api.order_send({
				'action': lib.TRADE_ACTION_DEAL, 'position': ticket,
				'symbol': position['symbol'], 'volume': position['volume'],
				'type': lib.ORDER_TYPE_SELL if buy else lib.ORDER_TYPE_BUY,
				'price': float(tick['bid'] if buy else tick['ask']),
				'deviation': self.deviation, 'magic': self.magic,
				'type_filling': fillingMode(info)})
			ok = result and result.get('retcode') in lib.RETCODE_OK
			self.logger.info("CLOSE %s position %s (%s): %s"
							 % (instrument, ticket, getattr(event, 'reason', None),
								(result or {}).get('retcode') or error))
			if ok:
				position['closeReason'] = getattr(event, 'reason', None)
				done += 1
		return done

	def pollCloses(self):
		sent = 0
		for ticket in list(self.positions):
			still, _ = self.api.positions_get(ticket=ticket)
			if still:
				continue
			deals, _ = self.api.history_deals_get(position=ticket)
			closing = [d for d in deals or [] if d['entry'] != lib.DEAL_ENTRY_IN]
			if not closing:
				continue
			sent += self.reportClose(self.positions.pop(ticket), closing)
		return sent

	def reportClose(self, position, closing):
		volume = sum(d['volume'] for d in closing) or 1.0
		price = sum(d['price'] * d['volume'] for d in closing) / volume
		last = closing[-1]
		reason = {lib.DEAL_REASON_SL: STOP_LOSS, lib.DEAL_REASON_TP: TAKE_PROFIT,
				  lib.DEAL_REASON_SO: 'MARGIN_CLOSEOUT'}.get(last['reason']) \
			or position.get('closeReason') or 'MARKET_ORDER_TRADE_CLOSE'
		account, _ = self.api.account_info()
		self.queue_event(TransactionEvent({
			'type': 'ORDER_FILL',
			'id': last['ticket'],
			'orderID': position['orderID'],
			'instrument': position['instrument'],
			'price': price,
			'units': position['units'],
			'time': self.api.toUTC(last['time'], last['symbol']),
			'pl': sum(d['profit'] + d['swap'] + d['commission'] + d['fee'] for d in closing),
			'accountBalance': (account or {}).get('balance'),
			'tradesClosed': [{'tradeID': position['positionId'],
							  'units': position['units'], 'price': price}],
			'reason': reason,
			'signalNumber': position['signalNumber'],
			'positionId': position['positionId'],
		}))
		return 1

	# ---------------------------------------------------------------- expiry

	def expire(self, known, now=None):
		"""
		Cancel a resting order past its gtdTime. Only matters where the symbol
		took no dated expiry and the order went in GTC; otherwise the broker
		has already removed it by the time this could look.
		"""
		when = known.get('gtdTime')
		now = now if now is not None else utcnow()
		if when is None or when > now:
			return False
		self.logger.info("MT5 order %s is past its gtdTime %s; cancelling it"
						 % (known['orderID'], when))
		self.queue_event(OrderCancelEvent({
			'orderID': known['orderID'], 'price': known['price'],
			'instrument': known['instrument'], 'reason': 'GTD_EXPIRY_ENFORCED'}))
		self.orders.pop(known['orderID'], None)
		return True

	# ---------------------------------------------------------------- stream

	def poll(self, now=None):
		return self.pollOrders(now) + self.pollCloses()

	def stream_to_queue(self):
		try:
			while self.running:
				self.poll()
				time.sleep(self.sleep)
		except Exception as exc:
			self.queue_event(StatusEvent('ERROR'))
			self.logger.error("MT5 TRANSACTIONS ERROR: %s" % exc)
