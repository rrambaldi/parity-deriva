"""
Sending an order to MetaTrader 5.

The closest broker here to OANDA: a STOP and a LIMIT are different orders,
a resting order takes an expiry, the stop and target ride on the order, and
order_send answers with the outcome rather than an acknowledgement. What it
does differently:

* **size is lots, not units.** The symbol's own contract size and volume
  step convert one to the other (lib/mt5.volume); a size under the broker's
  minimum is refused here rather than rounded up into a bigger bet.
* **a market order needs a price.** The current ask or bid, with
  MT5_DEVIATION points of slippage allowed.
* **the expiry is on the broker's clock**, converted by lib/mt5.toServer.
  A symbol that takes no dated expiry gets GTC, and data/mt5.MT5Transactions
  cancels the order at its gtdTime instead.
* **a stop is moved on the position**, which MT5 names by the ticket the
  opening fill reported as tradeOpened.tradeID. The target has to be sent
  again with it, or the move deletes it.
"""

import logging

from parity_deriva.etc import settings
from parity_deriva.event.event import ClientOrderEvent
from parity_deriva.event.event import TransactionEvent
from parity_deriva.lib import mt5 as lib
from parity_deriva.trading.handler import ExecutionHandler


def fillingMode(info):
	"""The fill policy a market order on this symbol may use."""
	allowed = int(info.get('filling_mode') or 0)
	if allowed & 1:
		return lib.ORDER_FILLING_FOK
	if allowed & 2:
		return lib.ORDER_FILLING_IOC
	return lib.ORDER_FILLING_RETURN


def orderType(kind, buy):
	return {
		'MARKET': (lib.ORDER_TYPE_BUY, lib.ORDER_TYPE_SELL),
		'STOP': (lib.ORDER_TYPE_BUY_STOP, lib.ORDER_TYPE_SELL_STOP),
		'LIMIT': (lib.ORDER_TYPE_BUY_LIMIT, lib.ORDER_TYPE_SELL_LIMIT),
	}[kind][0 if buy else 1]


class MT5ExecutionHandler(ExecutionHandler):

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self.api = args.get('api') or lib.connect(self.setup)
		self.magic = int(getattr(self.setup, 'MT5_MAGIC', 0))
		self.deviation = int(getattr(self.setup, 'MT5_DEVIATION', 10))

	def send(self, request):
		result, error = self.api.order_send(request)
		if not result or result.get('retcode') not in lib.RETCODE_OK:
			code = (result or {}).get('retcode')
			reason = (result or {}).get('comment') or error
			self.logger.error("MT5 REJECTED %s: %s %s" % (request.get('action'), code, reason))
			return None, code, reason
		return result, None, None

	def request(self, event):
		"""The order_send request for one OrderEvent; raises MT5Error if it cannot be one."""
		kind = str(getattr(event, 'orderType', '') or '').upper()
		if kind not in ('MARKET', 'STOP', 'LIMIT'):
			raise lib.MT5Error("MT5 has no order type for %r" % kind)
		units = float(event.units)
		sym = lib.symbol(event.instrument, self.setup)
		info, _ = self.api.symbol_info(sym)
		if not info:
			raise lib.MT5Error("MT5 has no symbol %s" % sym)
		if not info.get('visible'):
			self.api.symbol_select(sym, True)
		lots = lib.volume(units, info)
		if lots is None:
			raise lib.MT5Error("%s units of %s is under the minimum lot (%s x %s)"
							   % (units, sym, info.get('volume_min'),
								  info.get('trade_contract_size')))

		request = {
			'symbol': sym,
			'volume': lots,
			'type': orderType(kind, units > 0),
			'magic': self.magic,
			# the signal, so an order can be told apart in the terminal too.
			# The package refuses the whole order over 29 characters
			# ('Invalid "comment" argument'), so it is cut, not trusted
			'comment': str(getattr(event, 'signalNumber', '') or '')[:29],
		}
		if kind == 'MARKET':
			tick, _ = self.api.symbol_info_tick(sym)
			request.update({
				'action': lib.TRADE_ACTION_DEAL,
				'price': float(tick['ask'] if units > 0 else tick['bid']),
				'deviation': self.deviation,
				'type_filling': fillingMode(info),
			})
		else:
			request.update({
				'action': lib.TRADE_ACTION_PENDING,
				'price': float(event.price),
				'type_filling': lib.ORDER_FILLING_RETURN,
				'type_time': lib.ORDER_TIME_GTC,
			})
			gtd = getattr(event, 'gtdTime', None)
			if gtd is not None and int(info.get('expiration_mode') or 0) \
					& lib.SYMBOL_EXPIRATION_SPECIFIED:
				request['type_time'] = lib.ORDER_TIME_SPECIFIED
				request['expiration'] = self.api.toServer(gtd, sym)
		if getattr(event, 'stopLoss', None) is not None:
			request['sl'] = float(event.stopLoss)
		if getattr(event, 'takeProfit', None) is not None:
			request['tp'] = float(event.takeProfit)
		return request, info

	def placeOrder(self, event):
		self.logger.debug("GOT REQUEST %s" % event.info())
		try:
			request, info = self.request(event)
		except lib.MT5Error as exc:
			self.logger.error("ORDER NOT SENT: %s" % exc)
			result, code, reason = None, None, str(exc)
		else:
			result, code, reason = self.send(request)

		if result is None:
			# published, as OANDA's handler does, so the money manager stops
			# waiting on an order the broker never took
			rejection = TransactionEvent({
				'type': 'ORDER_REJECT',
				'instrument': event.instrument,
				'price': event.price,
				'units': event.units,
				'orderType': getattr(event, 'orderType', None),
				'rejectReason': code,
				'errorMessage': reason,
			})
			rejection.signalNumber = getattr(event, 'signalNumber', None)
			self.queue_event(rejection)
			return None

		coe = ClientOrderEvent({
			'id': result['order'],
			'batchID': 0,
			'instrument': event.instrument,
			'price': event.price,
			'units': event.units,
			'orderType': getattr(event, 'orderType', None),
			'stopLoss': getattr(event, 'stopLoss', None),
			'takeProfit': getattr(event, 'takeProfit', None),
			'gtdTime': getattr(event, 'gtdTime', None),
			'contractSize': info.get('trade_contract_size'),
		})
		coe.signalNumber = getattr(event, 'signalNumber', None)
		self.queue_event(coe)
		return coe

	def cancelOrder(self, event):
		order_id = getattr(event, 'orderID', None)
		if order_id is None:
			self.logger.warning("CANCEL without an orderID")
			return None
		result, _, _ = self.send({'action': lib.TRADE_ACTION_REMOVE, 'order': int(order_id)})
		self.logger.debug("CANCEL %s: %s" % (order_id, result and result.get('retcode')))
		return result

	def modifyStop(self, event):
		"""Move the stop of an open position, keeping its target."""
		ticket = getattr(event, 'tradeID', None)
		try:
			price = float(getattr(event, 'price', None))
		except (TypeError, ValueError):
			price = None
		if ticket is None or not price:
			self.logger.error("STOP NOT MOVED: no position to move it on (%s)" % event.to_json())
			return None
		positions, _ = self.api.positions_get(ticket=int(ticket))
		if not positions:
			self.logger.error("STOP NOT MOVED: position %s is not open" % ticket)
			return None
		position = positions[0]
		result, _, _ = self.send({
			'action': lib.TRADE_ACTION_SLTP,
			'position': int(ticket),
			'symbol': position['symbol'],
			'sl': price,
			'tp': position.get('tp') or 0.0,
			'magic': self.magic,
		})
		if result is not None:
			self.logger.info("MOVED STOP position %s -> %s" % (ticket, price))
		return result

	def execute_event(self, event):
		kind = str(event)
		if kind == 'ORDER':
			return self.placeOrder(event)
		if kind == 'ORDERCANCEL':
			return self.cancelOrder(event)
		if kind == 'STOPMODIFY':
			return self.modifyStop(event)
