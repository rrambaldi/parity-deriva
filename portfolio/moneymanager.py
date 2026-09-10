
import datetime
from parity_deriva.etc import settings
from parity_deriva.event.event import OrderEvent
from parity_deriva.event.event import OrderCancelEvent
from parity_deriva.trading.handler import ExecutionHandler
import logging


#: order states nothing further can come of. An order in one of these will
#: never fill, so a signal group made up entirely of them produced no trade.
DEAD = ('CANCELED', 'REJECTED')


class MoneyManager(ExecutionHandler):
	signals = {}
	processed = []
	onTrade = False
	orderIssued = False

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args,'setup', settings)
		self._set(args,'units', 1)
		# set by StatusEvent('HALT') from the parity monitor, cleared by
		# StatusEvent('RESUME'). Nothing else stopped this component before:
		# an alarm could be raised and orders kept going out.
		self.halted = False
		self.logger.debug("initialized...")

	def addOrder(self, oe):
		oe.batchID = 0
		if oe.signalNumber in self.signals:
			self.signals[oe.signalNumber].append(oe)
			return
		self.signals[oe.signalNumber] = [ oe ]


	def handleSignal(self, se):
		if self.halted:
			self.logger.warning("SIGNAL IGNORED: halted by the parity alarm")
			return
		if self.onTrade or (self.orderIssued and se.signalNumber not in self.signals):
			self.logger.info("SIGNAL IGNORED: onTrade")
			return
 
		ev_dict=se.to_dict()
		ev_dict['units'] = ev_dict['units'] * self.units
		oe = OrderEvent(ev_dict)
		self.queue_event(oe)
		self.addOrder(oe)
		self.logger.info("SENT %s" % oe.info())
		self.orderIssued = True


	# ------------------------------------------------- orders that never fill

	def findOrder(self, event):
		"""
		The (signal, order) an event refers to, or (None, None).

		By orderID where both sides have one. A rejected order never got one -
		the broker refused it before issuing an id - so instrument and price
		are the fallback, which is the same pair the simulator matches a
		cancel on, and for the same reason: they are the only fields both
		sides agree on.
		"""
		orderID = getattr(event, 'orderID', None)
		if orderID is None:
			orderID = getattr(event, 'id', None)
		try:
			orderID = int(orderID) if orderID is not None else None
		except (TypeError, ValueError):
			orderID = None

		if orderID is not None:
			for key in self.signals:
				for o in self.signals[key]:
					if o.has_attr('orderID') and o.orderID == orderID:
						return key, o

		price = getattr(event, 'price', None)
		if price is None:
			return None, None
		instrument = getattr(event, 'instrument', None)
		for key in self.signals:
			for o in self.signals[key]:
				if o.price != price:
					continue
				if instrument is not None \
						and getattr(o, 'instrument', None) != instrument:
					continue
				return key, o
		return None, None

	def orderDied(self, event, status):
		"""
		Record that one order will never fill, and release its group if
		nothing came of the group at all.

		Was: nothing did this. An order that expired or that the broker
		     refused left the money manager believing orders were still
		     outstanding, and handleSignal refuses every new signal number
		     while it believes that - so a bracket that simply expired
		     unfilled stopped the strategy for the rest of the process's
		     life. On OANDA the orders are GTD and expire nightly, so that
		     was a matter of time rather than of bad luck; on eToro they have
		     no expiry at all and data/etoro.py cancels them here instead.
		Now: the order is marked, and release() decides whether the group is
		     finished.

		A FILLED order is never overwritten: OANDA's transaction stream also
		reports the cancel of the losing leg after a fill, and the group has
		to stay alive until closeTrade.
		"""
		key, order = self.findOrder(event)
		if order is None:
			# Not ours: another strategy's order, or one from before this
			# process started. Still worth a release pass - the event may be
			# the last thing that was holding a group open.
			self.logger.debug("%s for an order in no signal group" % status)
			return self.release()

		if getattr(order, 'orderStatus', None) != 'FILLED':
			order.orderStatus = status
			self.logger.info("Signal %s: order %s %s" % (
				key, order.orderID if order.has_attr('orderID') else '(no id)',
				status.lower()))
		return self.release()

	def release(self):
		"""
		Forget the signal groups that produced no trade, and unblock.

		A group whose every order is dead had no fill and never will, so it
		is not a trade in progress; keeping it in self.signals is precisely
		what made handleSignal refuse everything afterwards. A group holding
		a fill stays until closeTrade, which is what limits the account to
		one trade at a time.
		"""
		for key in list(self.signals.keys()):
			group = self.signals[key]
			# all() of an empty group is True, and an empty group is not a
			# finished one
			if not group:
				continue
			if all(getattr(o, 'orderStatus', None) in DEAD for o in group):
				self.logger.info("Signal %s produced no trade; releasing it" % key)
				self.processed.append(group)
				del self.signals[key]

		if self.orderIssued and not self.onTrade and not self.signals:
			self.logger.info("No orders outstanding; accepting signals again")
			self.orderIssued = False

	def closeTrade(self, event):
		self.logger.debug("Trade closed...")
		self.onTrade = False
		self.orderIssued = False
		orderID = int(event.orderID)
		self.logger.debug("CLOSED: %s PRICE: %s PL: %s COSTS: %s BALANCE: %s"
			% ( orderID, event.price, event.pl, event.financing, event.accountBalance))
#		self.logger.debug(event.dump())
		for s in list(self.signals.keys()):
			# reset per group: otherwise the first match closes out every
			# group visited after it as well
			found = False
			for o in self.signals[s]:
#				self.logger.debug("%s %s" % (s, o.dump()))
				if o.has_attr('orderID') and o.orderID==orderID:
					found = True
					o.orderStatus = 'CLOSED'
					o.closeEvent = event
			if found:
				self.processed.append(self.signals[s])
				del(self.signals[s])
			

	def handleFilled(self,event):
		if event.has_attr('tradesClosed'):
			return self.closeTrade(event)

#		self.logger.debug("GOT %s" % event.info())
		self.onTrade = True
		self.orderIssued = True
		orderID = int(event.orderID)
		for s in self.signals.keys():
			found = False
			for o in self.signals[s]:
#				self.logger.debug("batch: %s: %s" % ( s, o.dump()))
				if o.has_attr('orderID') and o.orderID==orderID:
					found = True

			# cancelliamo gli altri ordini
			if not found:
				self.logger.info("OrderID %d not found in %s" % (orderID, s))
				continue

			for o in self.signals[s]:
				if o.has_attr('orderID') and o.orderID==orderID:
					o.orderStatus = 'FILLED'
					continue

				o.orderStatus = 'CANCELED'
				if not o.has_attr('orderID'):
					self.logger.warning("Order without OrderID: %s" % o.dump())
					continue
				oce = OrderCancelEvent({ 'orderID': o.orderID
					, 'price': o.price
					, 'instrument': o.instrument })
				self.queue_event(oce)
				self.logger.info("SENT %s orderID: %d" % (str(oce), o.orderID))
			return
		self.logger.info("NOT FOUND OrderID %s" % (event.dump()))
		for s in self.signals.keys():
			for o in self.signals[s]:
				self.logger.debug("Signal# %s: %s" % ( s, o.dump()))
		#

	def handleClientOrder(self,event):
		if event.signalNumber not in self.signals:
			self.logger.error("Missing %s" % event.signalNumber)
			return
		for o in self.signals[event.signalNumber]:
			if o.price == event.price:
				#self.logger.error(event.dump())
				o.batchID=int(event.batchID)
				o.orderID=int(event.id)
				self.logger.info("saved id %d batch: %d" % (o.orderID, o.batchID))

	def execute_event(self, event):
		if str(event) == 'STATUS':
			status = getattr(event, 'status', None)
			if status == 'HALT':
				if not self.halted:
					self.logger.critical("HALTED by the parity alarm")
				self.halted = True
			elif status == 'RESUME':
				if self.halted:
					self.logger.warning("RESUMED")
				self.halted = False
			return

		if str(event) not in ['SIGNAL','TRANSACTION','CLIENTORDER','ORDERCANCEL']:
			return

		# A cancel reaches here from two directions: this component's own,
		# when it drops the losing leg after a fill, and someone else's - the
		# broker expiring an order, or data/etoro.py enforcing an expiry the
		# broker does not have. Marking an already-marked order is harmless,
		# so both are handled the same way.
		if str(event)=='ORDERCANCEL':
			return self.orderDied(event, 'CANCELED')

		if str(event)=='TRANSACTION':
			kind = getattr(event, 'type', None)
			if kind=='ORDER_FILL':
				return self.handleFilled(event)
			if kind=='ORDER_CANCEL':
				return self.orderDied(event, 'CANCELED')
			# 'ORDER_REJECT' is what both execution handlers publish; OANDA's
			# transaction stream spells it per order type instead
			# (STOP_ORDER_REJECT, LIMIT_ORDER_REJECT, ...)
			if kind=='ORDER_REJECT' or str(kind).endswith('_ORDER_REJECT'):
				return self.orderDied(event, 'REJECTED')
			return

		if str(event)=='CLIENTORDER':
			return self.handleClientOrder(event)
		
		if str(event)=='SIGNAL':
			return self.handleSignal(event)


