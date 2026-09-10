"""
Sending an order to eToro.

The shape of the job is the same as execution/execution.py - consume an
OrderEvent, put it on the wire, acknowledge it with a ClientOrderEvent - and
almost none of the detail carries over:

* **a STOP and a LIMIT become the same order.** eToro has ``mkt``, ``mit``
  (market-if-touched) and ``limitIOC``. A resting entry is ``mit`` with a
  triggerRate, and that one type covers both of this project's pending
  orders: AG01's breakout STOP above the market and AG02's fade LIMIT below
  it differ only in where the trigger sits. The distinction the strategies
  and the simulator make is therefore invisible to the broker, and the
  provider declares distinct_stop_limit False so nothing assumes otherwise.
* **direction is a word, not a sign.** ``buy`` or ``sellShort``; ``sell``
  and ``buyToCover`` are rejected by the API today.
* **a short must carry a stop.** So must anything leveraged. Rather than
  send an order eToro will accept and then refuse, this refuses it here and
  says which field is missing.
* **the reply is an acknowledgement.** A 200 means accepted for processing.
  What actually happened has to be asked for afterwards, which is
  data/etoro.EToroTransactions's job.
* **the request id is the idempotency key**, and it is derived from the
  signal rather than random - so a retry cannot double an order, and a
  replay of the same candles produces the same ids. See lib/etoro.requestId.
"""

import logging

from parity_deriva.etc import settings
from parity_deriva.event.event import ClientOrderEvent
from parity_deriva.lib.etoro import EToroAPI, EToroError, instrumentId, requestId
from parity_deriva.trading.handler import ExecutionHandler


#: this project's order types as eToro's execution types. Both resting types
#: collapse onto mit, which is the whole of what eToro offers for an entry
#: that waits for a price.
ORDER_TYPES = {
	'MARKET': 'mkt',
	'STOP': 'mit',
	'LIMIT': 'mit',
}

#: the two transaction directions eToro currently accepts
BUY = 'buy'
SELL_SHORT = 'sellShort'


class EToroExecutionHandler(ExecutionHandler):

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)

		self.api = args.get('api') or EToroAPI(setup=self.setup)
		self.leverage = int(getattr(self.setup, 'ETORO_LEVERAGE', 1) or 1)
		# Optional on the v2 create route, and not invented here: the
		# eligible values differ per instrument, direction and leverage, so
		# an unset one leaves the platform to resolve it.
		self.settlement = getattr(self.setup, 'ETORO_SETTLEMENT_TYPE', None)
		self.logger.debug("initialized... %s account"
						  % ("demo" if self.api.demo else "REAL"))

	# ------------------------------------------------------------ translation

	def orderType(self, event):
		wanted = str(getattr(event, 'orderType', '') or '').upper()
		if wanted not in ORDER_TYPES:
			raise EToroError(
				"eToro has no order type for %r; it accepts %s"
				% (wanted, ", ".join(sorted(ORDER_TYPES))))
		return ORDER_TYPES[wanted]

	def body(self, event):
		"""
		The request body for one OrderEvent.

		Raises rather than sending anything eToro documents as invalid: an
		order accepted and then refused during execution costs a signal and
		reports the reason somewhere much less obvious than here.
		"""
		units = float(event.units)
		if units == 0:
			raise EToroError("order for zero units")
		transaction = BUY if units > 0 else SELL_SHORT
		kind = self.orderType(event)

		stop = getattr(event, 'stopLoss', None)
		target = getattr(event, 'takeProfit', None)
		if stop is None and (transaction == SELL_SHORT or self.leverage > 1):
			raise EToroError(
				"eToro requires stopLossRate on a %s order (leverage %d) and "
				"this one has no stopLoss"
				% (transaction, self.leverage))

		body = {
			'action': 'open',
			'transaction': transaction,
			'instrumentId': instrumentId(event.instrument, self.setup),
			'orderType': kind,
			'units': abs(units),
			'leverage': self.leverage,
		}
		if kind == 'mit':
			body['triggerRate'] = float(event.price)
		if stop is not None:
			body['stopLossRate'] = float(stop)
		if target is not None:
			body['takeProfitRate'] = float(target)
		if self.settlement is not None:
			body['settlementType'] = self.settlement
		return body

	def key(self, event):
		"""
		The idempotency key for this order.

		A signal produces two opposite orders, so the signal alone does not
		identify one of them; the direction and the trigger do, and both are
		properties of the signal rather than of the moment it was sent.
		"""
		units = float(getattr(event, 'units', 0) or 0)
		return requestId(getattr(event, 'signalNumber', None),
						 BUY if units > 0 else SELL_SHORT,
						 getattr(event, 'price', None))

	# --------------------------------------------------------------- sending

	def placeOrder(self, event):
		try:
			body = self.body(event)
		except EToroError as exc:
			self.logger.error("ORDER NOT SENT: %s" % str(exc))
			return None

		reference = self.key(event)
		self.logger.debug("GOT REQUEST %s" % event.info())
		status, payload = self.api.post('create_order', body=body,
										request_id=reference)

		if status is None:
			self.logger.warning("ORDER NOT SENT")
			return None
		if status != 200 or not payload or payload.get('orderId') is None:
			self.logger.error("ORDER REJECTED: status %s %s"
							  % (status, (payload or {}).get('detail')
								 or (payload or {}).get('title') or ''))
			return None

		# The acknowledgement, in the shape the money manager reads: it
		# matches on price and keeps id and batchID. eToro has no batch, so
		# batchID is 0 rather than a number that looks like one.
		coe = ClientOrderEvent({
			'id': payload['orderId'],
			'batchID': 0,
			'instrument': event.instrument,
			'price': event.price,
			'units': event.units,
			'orderType': getattr(event, 'orderType', None),
			'stopLoss': getattr(event, 'stopLoss', None),
			'takeProfit': getattr(event, 'takeProfit', None),
			'gtdTime': getattr(event, 'gtdTime', None),
			'referenceId': payload.get('referenceId') or reference,
		})
		coe.signalNumber = getattr(event, 'signalNumber', None)
		self.queue_event(coe)
		return coe

	def cancelOrder(self, event):
		order_id = getattr(event, 'orderID', None)
		if order_id is None:
			self.logger.warning("CANCEL without an orderID")
			return None
		status, payload = self.api.delete(
			'cancel_order', parts=(order_id,),
			request_id=requestId('cancel', order_id))
		self.logger.debug("CANCEL RESPONSE: status %s %s" % (status, payload))
		return status

	def execute_event(self, event):
		kind = str(event)
		if kind == 'ORDERCANCEL':
			return self.cancelOrder(event)
		if kind != 'ORDER':
			return
		return self.placeOrder(event)
