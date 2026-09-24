
import datetime
import math
from parity_deriva.etc import settings
from parity_deriva.event.event import OrderEvent
from parity_deriva.event.event import OrderCancelEvent
from parity_deriva.event.event import CloseTradeEvent
from parity_deriva.lib.utils import pipSize
from parity_deriva.trading.handler import ExecutionHandler
import logging


#: "no month has been reviewed yet", which None cannot stand for: a signal
#: that carries no time has no month either, and the two have to be told
#: apart or every such signal would look like the first one and re-read the
#: balance - the compounding the monthly review exists to avoid.
_NEVER = object()


def _clock(text):
	"""'07:30' as a time of day. A bare number of hours is one too: 7 is 07:00."""
	if isinstance(text, datetime.time):
		return text
	if isinstance(text, int):
		return datetime.time(hour=text)
	hours, _, minutes = str(text).partition(':')
	return datetime.time(hour=int(hours), minute=int(minutes or 0))


#: order states nothing further can come of. An order in one of these will
#: never fill, so a signal group made up entirely of them produced no trade.
DEAD = ('CANCELED', 'REJECTED')

#: what a close says when the session was stopped from the live page
SESSION_STOP = 'SESSION_STOP'


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
		# `reference` says `balance` is a reference capital the operator
		# chose (the live page's "capitale di riferimento") and not the
		# account's figure: the account holds other money too, so at a close
		# the broker's balance must not overwrite it. It moves with this
		# session's own closes instead - each trade's P&L, as the backtest's
		# simulated account moves - so the monthly review compounds the same
		# way here as there. `plRate` converts a broker's P&L, in the
		# account's currency, into the quote currency the capital is in.
		# ponytail: pl only, not financing: eToro's netProfit is already
		# net of fees and OANDA's financing comes apart. IB sends no pl at
		# all, so on IB a reference capital does not move.
		self.reference = False
		self._set(args,'reference')
		self.plRate = 1.0
		self._set(args,'plRate')
		# The widest stop any strategy may be given a position for, in pips,
		# or None for no ceiling. It is here rather than in a strategy
		# because it is a rule about the account and not about a setup: every
		# strategy that trades goes through this component, so one number
		# covers all of them and none of them has to be edited to change it.
		self.maxStopPips = None
		self._set(args,'maxStopPips')
		# When a signal may be taken, and when it may not. Both are rules
		# about the account rather than about a setup, so they live here with
		# maxStopPips: every strategy that trades comes through this
		# component, and none of them has to be edited to change them.
		#
		# `session` is ('07:00', '16:00') in UTC, or None for the whole day.
		# UTC because everything in this project is UTC, and a window that
		# moved with somebody's summer time would be a different rule twice a
		# year. It closes the *signal* and not the order: an order placed
		# inside the window rests until it fills or expires, because "this
		# account decides between eight and four" is a different rule from
		# "its pending orders vanish at four".
		#
		# `calendar` is a data.calendar.Calendar, or None. It carries its own
		# widths and its own idea of which events matter; this only asks it
		# whether an instant is inside one of its windows.
		self.session = None
		self._set(args,'session')
		self.calendar = None
		self._set(args,'calendar')
		# How far the initial stop and target sit from the entry, as a
		# multiple of where the strategy put them: 1.5 is half as far again,
		# 0.5 half the distance, None (or 1) leaves them alone. Initial only:
		# a stop the strategy walks later (portfolio/trailer.py) is moved
		# where the strategy says, not scaled.
		self.slScale = None
		self._set(args,'slScale')
		self.tpScale = None
		self._set(args,'tpScale')
		#: the capital the risk is taken from, and the month it belongs to
		self.capital = self.balance
		self.month = _NEVER
		# set by StatusEvent('HALT') from the parity monitor, cleared by
		# StatusEvent('RESUME'). Nothing else stopped this component before:
		# an alarm could be raised and orders kept going out.
		self.halted = False
		# Live only. `notBefore` is the first bar open a signal may come
		# from: live.py sets it so that the first bar acted on is one that
		# closed after the session started - a signal from a bar that closed
		# before is minutes or hours old, and the price has moved on from
		# the levels it was drawn at. None, the backtest's, takes them all.
		self.notBefore = None
		self._set(args,'notBefore')
		self.pairs = []
		self._set(args,'pairs')
		# set by StatusEvent('LIQUIDATE'): the session is being stopped, and
		# everything it has on the account comes off - see liquidate()
		self.liquidating = False
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

	def tooWide(self, ev):
		"""
		Is this signal's stop further away than the ceiling allows?

		The trade is refused rather than having its stop pulled in. Moving it
		would be this component inventing a level the strategy did not choose
		- the stop is where the rule says the setup failed - and it would move
		the target with it, turning a 1:1.2 into whatever the clamp left. A
		setup that needs a stop this wide is a setup this account does not
		take, which is a decision somebody can read off the counts.

		A signal with no stop is not too wide: it is unsizable, and size()
		already refuses it with a message that says so.
		"""
		if not self.maxStopPips:
			return False
		price, stop = ev.get('price'), ev.get('stopLoss')
		if price is None or stop is None:
			return False
		pip = pipSize(ev.get('instrument'), self.setup)
		return abs(float(price) - float(stop)) > self.maxStopPips * pip

	def outsideSession(self, when):
		"""
		Is this signal's own time outside the hours this account trades?

		The signal's time, in UTC, against a window given in UTC. A window
		whose end is before its start wraps midnight - ('22:00', '06:00') is
		the Asian session and not an empty one - because a day is a circle,
		and the alternative is writing two windows for one session.
		"""
		if not self.session or when is None:
			return False
		start, end = [_clock(one) for one in self.session]
		now = when.time()
		if start <= end:
			return not (start <= now < end)
		return not (now >= start or now < end)

	def onNews(self, when):
		"""Is this signal's own time inside one of the calendar's windows?"""
		if self.calendar is None or when is None:
			return False
		return self.calendar.blocked(when)

	def scaleLevels(self, ev):
		"""The signal's stop and target moved by slScale and tpScale."""
		price = ev.get('price')
		if price is None:
			return
		for key, scale in (('stopLoss', self.slScale), ('takeProfit', self.tpScale)):
			if scale and scale != 1 and ev.get(key) is not None:
				ev[key] = float(price) + (float(ev[key]) - float(price)) * scale

	def addOrder(self, oe):
		oe.batchID = 0
		if oe.signalNumber in self.signals:
			self.signals[oe.signalNumber].append(oe)
			return
		self.signals[oe.signalNumber] = [ oe ]


	def handleSignal(self, se):
		if self.halted:
			self.logger.warning("SIGNAL IGNORED: %s" % ("the session is stopping"
				if self.liquidating else "halted by the parity alarm"))
			return
		when = getattr(se, 'time', None)
		if self.notBefore is not None and when is not None and when < self.notBefore:
			self.logger.info("SIGNAL IGNORED: its bar closed before the session started (%s)"
				% when)
			return
		if self.onTrade or (self.orderIssued and se.signalNumber not in self.signals):
			self.logger.info("SIGNAL IGNORED: onTrade")
			return
 
		if self.outsideSession(when):
			self.logger.info("SIGNAL IGNORED: %s is outside %s-%s UTC"
				% (when, self.session[0], self.session[1]))
			return
		if self.onNews(when):
			ahead = self.calendar.next(when)
			self.logger.info("SIGNAL IGNORED: %s is on the news%s"
				% (when, "" if ahead is None
				   else " (%s %s, %s)" % (ahead['currency'], ahead['impact'],
										  ahead['title'])))
			return

		ev_dict=se.to_dict()
		# before the ceiling and the size, which are about the stop traded
		self.scaleLevels(ev_dict)
		if self.tooWide(ev_dict):
			self.logger.info("SIGNAL IGNORED: stop %s is more than %s pips "
				"from the entry %s"
				% (ev_dict.get('stopLoss'), self.maxStopPips,
				   ev_dict.get('price')))
			return
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
		pl = getattr(event, 'pl', None)
		if self.reference:
			if pl is not None and self.balance is not None:
				self.balance += float(pl) * self.plRate
		elif balance is not None:
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
		if self.liquidating:
			# an order that filled after the stop cancelled it, or before the
			# cancel reached the broker: it comes off like the rest
			self.closeAll(getattr(event, 'instrument', None))
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

	# ------------------------------------------------------ stop and close

	def resting(self):
		"""The orders the broker holds for this session and has not filled."""
		# list(): the stop's wait reads this from another thread
		return [o for group in list(self.signals.values()) for o in list(group)
				if o.has_attr('orderID') and o.orderID is not None
				and getattr(o, 'orderStatus', None) not in DEAD + ('FILLED', 'CLOSED')]

	def closeAll(self, instrument):
		self.queue_event(CloseTradeEvent({
			'instrument': instrument,
			'time': datetime.datetime.utcnow(),
			'reason': SESSION_STOP,
		}))

	def liquidate(self):
		"""
		Stop and close everything: no signal is taken from here on, every
		order still resting is cancelled and every trade open is closed at
		market. Only this session's: the cancels name its own order ids and a
		CloseTradeEvent is answered by the transaction handler with the
		positions it opened, so another strategy on the same account keeps
		what it holds.
		"""
		self.liquidating = True
		self.halted = True
		for o in self.resting():
			self.logger.info("STOP: cancelling order %s" % o.orderID)
			self.queue_event(OrderCancelEvent({'orderID': o.orderID, 'price': o.price,
				'instrument': o.instrument, 'reason': SESSION_STOP}))
		instruments = set(self.pairs or []) | set(
			getattr(o, 'instrument', None) for g in self.signals.values() for o in g)
		for instrument in sorted(i for i in instruments if i):
			self.logger.info("STOP: closing what is open on %s" % instrument)
			self.closeAll(instrument)

	def settled(self):
		"""Nothing left on the account: no open trade, no resting order."""
		return not self.onTrade and not self.resting()

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
			elif status == 'LIQUIDATE':
				self.liquidate()
			elif status == 'RESUME' and not self.liquidating:
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


