
import datetime
import math
from parity_deriva.etc import settings
from parity_deriva.event.event import OrderEvent
from parity_deriva.event.event import OrderCancelEvent
from parity_deriva.trading.handler import ExecutionHandler
import logging


#: "no month has been reviewed yet", which None cannot stand for: a signal
#: that carries no time has no month either, and the two have to be told
#: apart or every such signal would look like the first one and re-read the
#: balance - the compounding the monthly review exists to avoid.
_NEVER = object()


#: order states nothing further can come of. An order in one of these will
#: never fill, so a signal group made up entirely of them produced no trade.
DEAD = ('CANCELED', 'REJECTED')


def orderId(value):
	"""
	An order id, in a form two of them can be compared in.

	Every id here used to go through int(), because OANDA and eToro number
	their orders and their payloads spell the number sometimes as an integer
	and sometimes as text. IG does not number a deal, it names it -
	'PD6e1b03...' - and int() on that raises *inside the handler*: the
	acknowledgement was lost, and with it every match, fill and cancel for
	that signal.

	So a number is still normalised to a number, and anything else is kept as
	the text it is. Two ids that came from the same broker compare correctly
	either way; two from different brokers never meet.
	"""
	if value is None:
		return None
	try:
		return int(value)
	except (TypeError, ValueError):
		return str(value).strip()


class MoneyManager(ExecutionHandler):
	signals = {}
	processed = []
	onTrade = False
	orderIssued = False

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args,'setup', settings)
		self._set(args,'units', 1)
		# Size from the account rather than from a fixed number of units.
		# `risk` is the fraction of the capital a trade is allowed to lose if
		# it exits on its stop - 0.01 for one per cent - and None keeps the
		# old behaviour, which is what every caller that has not asked for
		# this gets. `balance` is what the account holds; it is seeded with
		# the opening figure and then followed from the broker's own closes,
		# because the account is the thing being risked and the only honest
		# source for it is the account.
		self.risk = None
		self._set(args,'risk')
		self.balance = None
		self._set(args,'balance')
		#: the capital the risk is taken from, and the month it belongs to
		self.capital = self.balance
		self.month = _NEVER
		# set by StatusEvent('HALT') from the parity monitor, cleared by
		# StatusEvent('RESUME'). Nothing else stopped this component before:
		# an alarm could be raised and orders kept going out.
		self.halted = False
		self.logger.debug("initialized...")

	def base(self, when):
		"""
		The capital a trade's risk is a percentage of, reviewed monthly.

		Not the running balance. Re-reading it after every close would make
		each trade's size depend on the one before it - compounding by the
		hour - and two runs over the same month would size differently
		because of where a trade happened to land inside it. Re-reading it
		once a month is a decision somebody could actually take, and it is
		the one this implements: within a calendar month the size of a trade
		does not move, and at the first signal of the next one the balance is
		read again.

		A signal without a time leaves the month as None, which matches
		itself, so such a run sizes off the opening capital throughout rather
		than re-reading it on every signal.
		"""
		month = None if when is None else (when.year, when.month)
		if self.month is _NEVER or month != self.month:
			if self.month is not _NEVER and self.balance is not None:
				self.logger.info("New month %s: risk now taken on %s"
					% (month, self.balance))
			self.month = month
			if self.balance is not None:
				self.capital = self.balance
		return self.capital

	def size(self, ev, when):
		"""
		The units one signal is worth, or None for a signal that cannot be
		sized and must therefore not be sent.

		With `risk` set, the distance to the stop is what decides: a trade
		that exits there loses the capital times the risk, whatever that
		distance is, so a wide stop buys fewer units and a narrow one more.
		That is the whole point of sizing this way, and it means a signal
		carrying no stop has no size - there is no distance to divide by.
		Refusing it is the only safe answer: any number invented here would
		be a position whose loss nobody chose.
		"""
		if self.risk is None:
			return ev['units'] * self.units

		capital = self.base(when)
		price, stop = ev.get('price'), ev.get('stopLoss')
		if not capital or capital <= 0:
			self.logger.warning("cannot size a signal: capital is %s" % capital)
			return None
		if price is None or stop is None:
			self.logger.warning("cannot size a signal with no stop: "
				"price %s stop %s" % (price, stop))
			return None
		distance = abs(float(price) - float(stop))
		if not distance:
			self.logger.warning("cannot size a signal whose stop is its own "
				"entry price (%s)" % price)
			return None

		# ponytail: two decimals, which is the precision IG and eToro already
		# take a size in (see SignalEvent.info). There is no per-instrument
		# rounding rule here because this project does not model contract
		# sizes; add one when a broker refuses a size this produces.
		units = round(capital * self.risk / distance, 2)
		if units <= 0:
			self.logger.warning("cannot size a signal: %s of %s over a stop "
				"%s away rounds to nothing" % (self.risk, capital, distance))
			return None
		return math.copysign(units, ev['units'])

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
		units = self.size(ev_dict, getattr(se, 'time', None))
		if units is None:
			# said and dropped, not raised: trading/engine.py answers an
			# exception in a handler with os._exit(1), and one unsizable
			# signal is not a reason to take the process down mid-session
			self.logger.warning("SIGNAL IGNORED: it cannot be sized")
			return
		ev_dict['units'] = units
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
		orderID = orderId(orderID)

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
		# the account's own figure, which is what the monthly review reads.
		# Only the broker knows it - adding up this component's own fills
		# would miss financing, and on a live account would drift from the
		# truth a little more with every trade.
		balance = getattr(event, 'accountBalance', None)
		if balance is not None:
			self.balance = float(balance)
		self.onTrade = False
		self.orderIssued = False
		orderID = orderId(getattr(event, 'orderID', None))
		# getattr, not attribute access: financing is an OANDA field and no
		# other broker here sends one, so reading it directly raised - in a
		# log line, on the close of a live trade, which trading/engine.py
		# answers by killing the process
		self.logger.debug("CLOSED: %s PRICE: %s PL: %s COSTS: %s BALANCE: %s"
			% ( orderID, getattr(event, 'price', None), getattr(event, 'pl', None),
				getattr(event, 'financing', None),
				getattr(event, 'accountBalance', None)))
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
		orderID = orderId(getattr(event, 'orderID', None))
		for s in self.signals.keys():
			found = False
			for o in self.signals[s]:
#				self.logger.debug("batch: %s: %s" % ( s, o.dump()))
				if o.has_attr('orderID') and o.orderID==orderID:
					found = True

			# cancelliamo gli altri ordini
			if not found:
				self.logger.info("OrderID %s not found in %s" % (orderID, s))
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
				self.logger.info("SENT %s orderID: %s" % (str(oce), o.orderID))
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
				o.batchID=orderId(event.batchID)
				o.orderID=orderId(event.id)
				self.logger.info("saved id %s batch: %s" % (o.orderID, o.batchID))

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


