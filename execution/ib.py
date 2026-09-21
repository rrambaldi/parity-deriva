"""
Sending an order to Interactive Brokers.

The job is the same as execution/execution.py - consume an OrderEvent, put it
on the wire, acknowledge it with a ClientOrderEvent - and IB asks for a
different shape than any of the other three:

* **a bracket is three orders.** OANDA, eToro and IG all attach a stop and a
  target to the entry; IB has no such field. The stop and the target are
  separate child orders that name the entry as their ``parentId`` and sit in
  one OCA group, so filling either cancels the other. That costs a more
  involved submission - and buys the one thing the other two polled brokers
  cannot give: when a child fills, the broker has named the leg that closed
  the trade. See data/ib.IBTransactions.pollChildren.
* **side is a word and quantity is unsigned.** BUY or SELL; the sign this
  project carries on ``units`` is the direction and is translated, not sent.
* **a stop's level is auxPrice, a limit's is price.** Putting a stop level in
  ``price`` produces an order at a price nobody chose, so the two are kept
  apart by order type rather than by one field named "the level".
* **the reply can be a question.** Most submissions draw at least one, and
  the order exists only once it has been confirmed. lib/ib.IBAPI.place runs
  that exchange; every question is logged whether or not it is answered here.
* **there is no expiry instant.** Time in force is DAY, GTC or an immediate
  variety, so a strategy's gtdTime cannot be sent. The order goes as GTC and
  data/ib.py cancels it here when the expiry passes - our action, not the
  broker's, and logged each time.

A note on what is *not* done. IB will happily accept a bracket whose children
sit inside the minimum tick or the wrong side of the entry, and refuse it at
the exchange later. Nothing here nudges a level to fit: a strategy's stop
moved to satisfy a broker rule is no longer that strategy's stop, so it is
sent as asked and a refusal is published as a rejection.
"""

import logging

from parity_deriva.etc import settings
from parity_deriva.event.event import ClientOrderEvent
from parity_deriva.event.event import TransactionEvent
from parity_deriva.lib.ib import IBAPI, IBError, IBNotAuthenticated
from parity_deriva.lib.ib import clientOrderId, conid
from parity_deriva.trading.handler import ExecutionHandler


#: this project's order types as IB's. STOP_LIMIT exists at IB and is not
#: here, because nothing in this project produces one.
ORDER_TYPES = {
	'MARKET': 'MKT',
	'STOP': 'STP',
	'LIMIT': 'LMT',
}

BUY = 'BUY'
SELL = 'SELL'


class IBExecutionHandler(ExecutionHandler):

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)

		self.api = args.get('api') or IBAPI(setup=self.setup)
		self.outside_rth = bool(getattr(self.setup, 'IB_OUTSIDE_RTH', True))
		self.confirm = bool(getattr(self.setup,
									'IB_CONFIRM_ORDER_QUESTIONS', True))
		self.refuse = tuple(getattr(self.setup, 'IB_REFUSE_QUESTIONS', ()) or ())
		self.logger.debug("initialized... account %s" % self.api.account)

	# ------------------------------------------------------------ translation

	def orderType(self, event):
		wanted = str(getattr(event, 'orderType', '') or '').upper()
		if wanted not in ORDER_TYPES:
			raise IBError(
				"IB has no order type for %r; this project sends %s"
				% (wanted, ", ".join(sorted(ORDER_TYPES))))
		return ORDER_TYPES[wanted]

	def orders(self, event):
		"""
		The entry and its children, as one submission.

		Raises rather than sending something IB documents as invalid. The
		children are built from the same units as the entry and in the
		opposite direction, which is what makes them a bracket rather than
		three unrelated orders.
		"""
		units = float(event.units)
		if units == 0:
			raise IBError("order for zero units")
		side = BUY if units > 0 else SELL
		opposite = SELL if side == BUY else BUY
		kind = self.orderType(event)
		contract = conid(event.instrument, self.setup)
		parent = self.key(event)

		entry = {
			'conid': contract,
			'orderType': kind,
			'side': side,
			'quantity': abs(units),
			# GTC rather than DAY: the strategies decide when an order dies
			# and data/ib.py enforces that instant, where DAY would have IB
			# kill the order at a session boundary of its own choosing.
			'tif': 'GTC',
			'cOID': parent,
			'outsideRTH': self.outside_rth,
		}
		if kind == 'LMT':
			entry['price'] = float(event.price)
		elif kind == 'STP':
			# A stop's level is auxPrice. IB reads 'price' on a stop order as
			# the limit of a stop-limit, which is a different order.
			entry['auxPrice'] = float(event.price)

		out = [entry]
		stop = getattr(event, 'stopLoss', None)
		target = getattr(event, 'takeProfit', None)
		if stop is not None:
			out.append({
				'conid': contract,
				'orderType': 'STP',
				'side': opposite,
				'quantity': abs(units),
				'auxPrice': float(stop),
				'tif': 'GTC',
				'cOID': self.childKey(parent, 'stop'),
				'parentId': parent,
				# One OCA group, so the target is cancelled when the stop
				# fills and the other way round. Without it a closed trade
				# leaves its other leg resting and the next fill opens a
				# position nobody signalled.
				'isSingleGroup': True,
				'outsideRTH': self.outside_rth,
			})
		if target is not None:
			out.append({
				'conid': contract,
				'orderType': 'LMT',
				'side': opposite,
				'quantity': abs(units),
				'price': float(target),
				'tif': 'GTC',
				'cOID': self.childKey(parent, 'target'),
				'parentId': parent,
				'isSingleGroup': True,
				'outsideRTH': self.outside_rth,
			})
		return out

	def key(self, event):
		"""
		The client order id for this order.

		A signal produces two opposite orders, so the signal alone does not
		identify one of them; the direction and the level do, and both are
		properties of the signal rather than of the moment it was sent. IB
		reports this back as order_ref, which is what the poll joins on.
		"""
		units = float(getattr(event, 'units', 0) or 0)
		return clientOrderId(getattr(event, 'signalNumber', None),
							 BUY if units > 0 else SELL,
							 getattr(event, 'price', None))

	def childKey(self, parent, leg):
		"""The id of one child, derived from its parent's and its own leg."""
		return clientOrderId(parent, leg)

	# --------------------------------------------------------------- sending

	def placeOrder(self, event):
		try:
			orders = self.orders(event)
		except IBError as exc:
			self.logger.error("ORDER NOT SENT: %s" % str(exc))
			return None

		parent = orders[0]['cOID']
		self.logger.debug("GOT REQUEST %s" % event.info())
		try:
			answer = self.api.place(orders, confirm=self.confirm,
									refuse=self.refuse)
		except IBNotAuthenticated as exc:
			self.logger.error("ORDER NOT SENT: %s" % str(exc))
			return None

		placed = [row for row in (answer or [])
				  if isinstance(row, dict) and row.get('order_id')]
		if not placed:
			# Either an error, or a question that was not answered here. Both
			# are published as a rejection, and normalised to the same
			# 'ORDER_REJECT' type the other brokers publish: the money manager
			# refuses every new signal while it believes an order is
			# outstanding, so a rejection that never reached it would stop the
			# strategy for good.
			reason = _reason(answer)
			self.logger.error("ORDER REJECTED: %s" % reason)
			rejection = TransactionEvent({
				'type': 'ORDER_REJECT',
				'instrument': event.instrument,
				'price': event.price,
				'units': event.units,
				'orderType': getattr(event, 'orderType', None),
				'clientOrderId': parent,
				'rejectReason': reason,
				'errorMessage': reason,
			})
			rejection.signalNumber = getattr(event, 'signalNumber', None)
			self.queue_event(rejection)
			return None

		# The acknowledgement, in the shape the money manager reads: it
		# matches on price and keeps an id. IB has no batch, so batchID is 0
		# rather than a number that looks like one. The child ids travel with
		# it because they are what data/ib.py needs to say which leg closed a
		# trade, and they are ours rather than IB's - derived from the parent,
		# so they survive a restart.
		coe = ClientOrderEvent({
			'id': placed[0]['order_id'],
			'batchID': 0,
			'clientOrderId': parent,
			'instrument': event.instrument,
			'price': event.price,
			'units': event.units,
			'orderType': getattr(event, 'orderType', None),
			'stopLoss': getattr(event, 'stopLoss', None),
			'takeProfit': getattr(event, 'takeProfit', None),
			'gtdTime': getattr(event, 'gtdTime', None),
			'stopChildId': (self.childKey(parent, 'stop')
							if getattr(event, 'stopLoss', None) is not None
							else None),
			'targetChildId': (self.childKey(parent, 'target')
							  if getattr(event, 'takeProfit', None) is not None
							  else None),
		})
		coe.signalNumber = getattr(event, 'signalNumber', None)
		self.queue_event(coe)
		return coe

	def cancelOrder(self, event):
		"""
		Cancel an order, by the id IB gave it.

		IB deletes by its own order id, not by the client order id this
		project derives, so a cancel without one cannot be sent. Saying so is
		better than issuing a delete against a resource named by the wrong
		identifier - which, on a route that takes an account and an id, would
		be somebody else's order.
		"""
		order_id = getattr(event, 'orderID', None)
		if order_id is None:
			self.logger.warning(
				"CANCEL without an orderID (client id %s); IB deletes by its "
				"own order id" % getattr(event, 'clientOrderId', None))
			return None
		status, payload = self.api.delete(
			'cancel_order', parts=(self.api.account, order_id))
		self.logger.debug("CANCEL RESPONSE: status %s %s" % (status, payload))
		return status

	def execute_event(self, event):
		kind = str(event)
		if kind == 'ORDERCANCEL':
			return self.cancelOrder(event)
		if kind != 'ORDER':
			return
		return self.placeOrder(event)


def _reason(answer):
	"""Whatever IB said, as one line worth putting in a log."""
	if not answer:
		return "no answer from the gateway"
	parts = []
	for row in answer:
		if not isinstance(row, dict):
			parts.append(str(row))
			continue
		if row.get('error'):
			parts.append(str(row['error']))
		elif row.get('message'):
			parts.append(" ".join(str(m) for m in row['message']))
		else:
			parts.append(str(row))
	return "; ".join(parts) or "unrecognised answer"
