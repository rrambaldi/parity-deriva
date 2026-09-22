"""
Sending an order to IG.

The job is the same as execution/execution.py - consume an OrderEvent, put it
on the wire, acknowledge it with a ClientOrderEvent - and IG asks for a
different shape:

* **two endpoints, not one.** A market order opens a position through
  /positions/otc; a resting order is a *working order* through
  /workingorders/otc, with its own type of LIMIT or STOP. So unlike eToro,
  where both collapse onto ``mit``, IG really does tell the two apart, and
  the provider says so.
* **direction is a word and size is unsigned.** BUY or SELL, with the size
  always positive - the sign this project carries on ``units`` is the
  direction and is translated, not sent.
* **forceOpen, or the stop is refused.** IG attaches a stop or a limit only
  to a deal that opens its own position; on a deal that nets off against an
  existing one there is nothing to attach them to. Every order here carries a
  bracket, so forceOpen is true and that is stated rather than left to a
  default.
* **the reply is a reference.** POST answers with a dealReference and nothing
  else. Whether the deal was accepted, and at what level, has to be read from
  GET /confirms afterwards, which is data/ig.IGTransactions's job.
* **the reference is ours to choose**, and it is derived from the signal
  rather than random - so a replay of the same candles produces the same
  references. It is not treated as an idempotency key, because whether IG
  refuses a repeat has not been established here. See lib/ig.dealReference.

A note on levels. IG rejects a stop or a limit closer to the market than the
instrument's minimum distance, and that distance is in the dealing rules of
GET /markets/{epic} rather than in anything this project knows. Nothing here
adjusts a level to fit: a strategy's stop moved to satisfy a broker rule is
no longer that strategy's stop, so the order is sent as asked and IG's
refusal is published as a rejection.
"""

import logging

from parity_deriva.etc import settings
from parity_deriva.event.event import ClientOrderEvent
from parity_deriva.event.event import TransactionEvent
from parity_deriva.lib.ig import (IGAPI, IGError, currency, dealReference,
								  epic, expiry, goodTillDate, pricePrecision,
								  scale)
from parity_deriva.trading.handler import ExecutionHandler


#: this project's order types as IG's working-order types. MARKET is absent
#: because it is not a working order at all - it goes to another endpoint.
WORKING_TYPES = {
	'STOP': 'STOP',
	'LIMIT': 'LIMIT',
}

BUY = 'BUY'
SELL = 'SELL'


class IGExecutionHandler(ExecutionHandler):

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)

		self.api = args.get('api') or IGAPI(setup=self.setup)
		self.guaranteed = bool(getattr(self.setup, 'IG_GUARANTEED_STOP', False))
		self.logger.debug("initialized... %s account"
						  % ("demo" if self.api.demo else "REAL"))

	# ------------------------------------------------------------ translation

	def level(self, instrument, value):
		"""
		A price of ours as a level of IG's.

		The inverse of what data/ig.py does on the way in: a market IG quotes
		scaled is dealt in the same scaled units it quotes, so a level derived
		from a descaled candle has to be put back. Unset scaling - the normal
		case - leaves the number alone.
		"""
		if value is None:
			return None
		return float(value) * scale(instrument, self.setup)

	def body(self, event):
		"""
		The request body for one OrderEvent, and which endpoint it goes to.

		Returns (route, body). Raises rather than sending anything IG
		documents as invalid: an order refused during execution reports its
		reason somewhere much less obvious than here.
		"""
		units = float(event.units)
		if units == 0:
			raise IGError("order for zero units")
		direction = BUY if units > 0 else SELL
		wanted = str(getattr(event, 'orderType', '') or '').upper()
		instrument = event.instrument

		body = {
			'epic': epic(instrument, self.setup),
			'expiry': expiry(instrument, self.setup),
			'direction': direction,
			'size': abs(units),
			'currencyCode': currency(instrument, self.setup),
			# Every order here brackets a position of its own, and IG attaches
			# a stop or a limit only to a deal that opens one.
			'forceOpen': True,
			'guaranteedStop': self.guaranteed,
			'dealReference': self.key(event),
		}

		stop = getattr(event, 'stopLoss', None)
		target = getattr(event, 'takeProfit', None)
		if stop is not None:
			body['stopLevel'] = self.level(instrument, stop)
		if target is not None:
			body['limitLevel'] = self.level(instrument, target)
		if self.guaranteed and stop is None:
			raise IGError(
				"IG_GUARANTEED_STOP is set and this order has no stopLoss; a "
				"guaranteed stop is a stop that has to exist")

		if wanted == 'MARKET':
			body['orderType'] = 'MARKET'
			# EXECUTE_AND_ELIMINATE fills what it can at the level available
			# and cancels the rest, which is the honest behaviour for a market
			# entry: FILL_OR_KILL would silently drop a signal whenever the
			# full size was not there.
			body['timeInForce'] = 'EXECUTE_AND_ELIMINATE'
			return 'open_position', body

		if wanted not in WORKING_TYPES:
			raise IGError(
				"IG has no order type for %r; it accepts MARKET, %s"
				% (wanted, ", ".join(sorted(WORKING_TYPES))))

		body['type'] = WORKING_TYPES[wanted]
		body['level'] = self.level(instrument, event.price)
		gtd = getattr(event, 'gtdTime', None)
		if gtd is not None:
			# IG really does expire an order, which is the one thing eToro
			# cannot do, so the strategies' end-of-day expiry is sent rather
			# than enforced on our side.
			body['timeInForce'] = 'GOOD_TILL_DATE'
			body['goodTillDate'] = goodTillDate(gtd)
		else:
			body['timeInForce'] = 'GOOD_TILL_CANCELLED'
		return 'create_order', body

	def key(self, event):
		"""
		The deal reference for this order.

		A signal produces two opposite orders, so the signal alone does not
		identify one of them; the direction and the level do, and both are
		properties of the signal rather than of the moment it was sent.
		"""
		units = float(getattr(event, 'units', 0) or 0)
		return dealReference(getattr(event, 'signalNumber', None),
							 BUY if units > 0 else SELL,
							 getattr(event, 'price', None))

	# --------------------------------------------------------------- sending

	def placeOrder(self, event):
		try:
			route, body = self.body(event)
		except IGError as exc:
			self.logger.error("ORDER NOT SENT: %s" % str(exc))
			return None

		reference = body['dealReference']
		self.logger.debug("GOT REQUEST %s" % event.info())
		status, payload = self.api.post(route, body=body)

		if status is None:
			self.logger.warning("ORDER NOT SENT")
			return None
		if status != 200 or not payload or not payload.get('dealReference'):
			# IG refuses before the deal exists with an errorCode in the body;
			# published rather than logged and dropped, because the money
			# manager refuses every new signal while it believes an order is
			# outstanding - so a rejection that never reached it would stop
			# the strategy for good. Normalised to the same 'ORDER_REJECT'
			# type the other brokers publish.
			code = (payload or {}).get('errorCode')
			self.logger.error("ORDER REJECTED: status %s %s" % (status, code))
			rejection = TransactionEvent({
				'type': 'ORDER_REJECT',
				# named the way the money manager reads it; without an
				# orderID the rejection frees nothing and the strategy stays
				# blocked behind an order that does not exist
				'orderID': reference,
				'instrument': event.instrument,
				'price': event.price,
				'units': event.units,
				'orderType': getattr(event, 'orderType', None),
				'dealReference': reference,
				'rejectReason': code,
				'errorMessage': code,
			})
			rejection.signalNumber = getattr(event, 'signalNumber', None)
			self.queue_event(rejection)
			return None

		# The acknowledgement, in the shape the money manager reads: it
		# matches on price and keeps an id. IG has no batch, so batchID is 0
		# rather than a number that looks like one, and the id is the deal
		# reference because at this point that is the only name the deal has -
		# its dealId arrives with the confirmation.
		returned = payload['dealReference']
		if returned != reference:
			# Worth knowing about: everything downstream joins on the
			# reference we derived, so a reference IG changed would leave the
			# confirmation unmatched.
			self.logger.warning("IG returned dealReference %s for %s"
								% (returned, reference))
		coe = ClientOrderEvent({
			'id': returned,
			'batchID': 0,
			'dealReference': returned,
			'instrument': event.instrument,
			'price': event.price,
			'units': event.units,
			'orderType': getattr(event, 'orderType', None),
			'stopLoss': getattr(event, 'stopLoss', None),
			'takeProfit': getattr(event, 'takeProfit', None),
			'gtdTime': getattr(event, 'gtdTime', None),
		})
		coe.signalNumber = getattr(event, 'signalNumber', None)
		self.queue_event(coe)
		return coe

	def resolveOrder(self, event):
		"""
		The dealId of the working order an event names, or None.

		The money manager cancels the losing leg by the id it was given, and
		for IG that id is the deal *reference*: the acknowledgement had
		nothing else to offer, since the dealId only exists once IG has
		processed the deal. GET /workingorders then publishes the dealId and
		**not** the reference - measured, not assumed - so the two names for
		one order never appear together and the order has to be found some
		other way.

		It is found on the epic and the level, which is the same pair the
		simulator matches a cancel on and for the same reason: they are the
		fields both sides agree on. Two orders at one level on one market
		resolve to neither. That is AG01's straddle seen from the wrong side -
		its two legs sit at different levels, so it does not arise there - but
		cancelling the wrong leg would leave the account holding the position
		the strategy meant to abandon, which is worse than cancelling nothing.
		"""
		instrument = getattr(event, 'instrument', None)
		price = getattr(event, 'price', None)
		if instrument is None or price is None:
			return None

		status, payload = self.api.get('workingorders')
		if status != 200 or not payload:
			self.logger.error("CANCEL: working orders came back %s" % status)
			return None

		wanted = epic(instrument, self.setup)
		digits = pricePrecision(instrument, self.setup)
		level = round(self.level(instrument, price), digits)
		found = []
		for row in payload.get('workingOrders') or []:
			data = row.get('workingOrderData') or {}
			if data.get('epic') != wanted or data.get('orderLevel') is None:
				continue
			if round(float(data['orderLevel']), digits) == level:
				found.append(data.get('dealId'))

		if len(found) == 1:
			return found[0]
		if not found:
			self.logger.warning("CANCEL: no working order on %s at %s"
								% (wanted, level))
		else:
			self.logger.error(
				"CANCEL: %d working orders on %s at %s; cancelling none of "
				"them rather than the wrong one" % (len(found), wanted, level))
		return None

	def cancelOrder(self, event):
		"""
		Delete a working order.

		IG keys the delete by dealId, which is not what the acknowledgement
		carried - that was the deal *reference*. Where the event names the
		dealId, it is used; otherwise the order is looked up, and a cancel
		that cannot be resolved is refused rather than sent against a
		resource named by the wrong identifier.
		"""
		deal_id = getattr(event, 'dealId', None)
		if deal_id is None:
			deal_id = self.resolveOrder(event)
		if deal_id is None:
			self.logger.warning(
				"CANCEL NOT SENT for order %s at %s: no dealId, and the "
				"working orders did not identify one"
				% (getattr(event, 'orderID', None), getattr(event, 'price', None)))
			return None
		status, payload = self.api.delete('cancel_order', parts=(deal_id,))
		self.logger.debug("CANCEL RESPONSE: status %s %s" % (status, payload))
		return status

	def execute_event(self, event):
		kind = str(event)
		if kind == 'ORDERCANCEL':
			return self.cancelOrder(event)
		if kind != 'ORDER':
			return
		return self.placeOrder(event)
