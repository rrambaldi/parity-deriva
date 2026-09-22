import json
import datetime
import logging

from parity_deriva.etc import settings
from parity_deriva.trading.handler import ExecutionHandler
from parity_deriva.event.event import ClientOrderEvent
from parity_deriva.lib.oanda import OANDAOrder
from parity_deriva.lib.oanda import OANDATrade
from parity_deriva.event.event import Event
from parity_deriva.event.event import SimulatedOrderEvent
from parity_deriva.event.event import SimulatedFillEvent

class OANDABacktester(ExecutionHandler):

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		# per instance, not per class: one simulator per instrument must keep
		# its own book
		self.trades = []
		self.orders = []
		self.closed_orders = []
		self.closed_trades = []
		self.lastTradeID = 0
		self.lastOrderID = 0
		self._set(args,'setup', settings)
		self._set(args,'currency', 'EUR')
		self._set(args,'balance', 100000.0)
		# Which candle stream drives the fills. An order is only ever matched
		# against its own instrument - that is intrinsic and needs no
		# configuration - but nothing in a candle says whether it is the
		# stream this simulator should be reading, so when two granularities
		# of the same instrument are on the bus, say the H1 the strategy sees
		# and the M1 the simulator shadows it with, name the one to use.
		# _set() only assigns when the default is not None, so seed it first
		self.granularity = None
		self._set(args,'granularity')

	def dumpOrders(self):
		for o in self.orders:
			self.logger.info("ORDER# %d: %s" % (o.id, o.dump()))

	def dumpTrades(self):
		for t in self.trades:
			self.logger.info("TRADE# %d: %s" % (t.id, t.dump()))

	def cancelOrder(self, event):
		"""
		Cancel a resting order. The simulator numbers its orders itself, so it
		cannot match on the broker's orderID carried by the event: instrument
		and price are the only fields both sides agree on.
		"""
		instrument = getattr(event, 'instrument', None)
		for o in list(self.orders):
			if o.price != event.price:
				continue
			if instrument is not None and getattr(o, 'instrument', None) != instrument:
				continue
			if o.state=='PENDING':
				o.state='CANCELED'
				self.closed_orders.append(o)
				self.orders.remove(o)
				self.logger.debug("CANCELED @price %s" % o.price)
				return
			if o.state=='FILLED':
				self.logger.warning("WARNING CANCEL DI FILL %s" % o.dump())
		return

	def createOrder(self, event):
		"""
		Put an order on the book and acknowledge it.

		Returns the OANDAOrder that was added. That return value is not
		decoration: the book holds this object, and the Event handed in is a
		different one, so anything that means to act on a resting order later
		- cancelling the sibling of a leg that just closed a trade, say - has
		to hold on to what comes back here.
		"""
		self.logger.debug("== createOrder price %s" % event.price)
		self.lastOrderID = self.lastOrderID + 1
		o = OANDAOrder(self.lastOrderID, event.to_dict())
		o.state = 'PENDING'
		self.orders.append(o)
		# Acknowledge it the way the broker does, so the signal it came from
		# can be told which order id it now owns on this side.
		self.queue_event(SimulatedOrderEvent({
			'id': o.id,
			'batchID': o.id,
			'price': o.price,
			'instrument': getattr(o, 'instrument', None),
			'units': o.units,
			'signalNumber': getattr(o, 'signalNumber', None),
		}))
		return o

	def retire(self, order):
		"""
		Take a resting order off the book.

		Was: nothing did this for the sibling of a leg that closed a trade.
		     handleSLTP() assigned the string 'CANCELED' over o.orig.SLOrder,
		     which replaced a reference nothing consults - the book holds the
		     OANDAOrder that createOrder() built, not the Event that was
		     handed to it - so the other half of every bracket stayed PENDING
		     for the rest of the run. When price later reached it, it filled
		     and published a second close for a trade that had already closed:
		     over two months of EUR_USD H1, 73 of 103 trades closed twice, the
		     balance moved twice, and the second outcome was usually the
		     opposite of the first.
		Now: the sibling is cancelled the way cancelOrder() cancels, which is
		     what OCO means and what the account would have done.
		"""
		if order is None or getattr(order, 'state', None) != 'PENDING':
			return False
		order.state = 'CANCELED'
		if order in self.orders:
			self.orders.remove(order)
			self.closed_orders.append(order)
		return True


	def modifyStop(self, event):
		"""
		Move the stop of an open trade to a new level.

		Was: impossible. A bracket's stop was fixed when handleSLTP created it
		     and nothing could speak to it again, so a strategy whose exit is a
		     rule rather than a level had no way to be simulated at all.
		Now: portfolio/trailer.py computes the level and says it here. Only the
		     price moves - the order keeps its identity, its parent and its
		     place on the book - which is what a broker's amend does and what
		     keeps the fill logic in checkOrder untouched.

		The id is the *entry* order's, because that is the one the opening fill
		reported and therefore the only one the trailer ever saw. A stop that
		has already been taken is not moved: it did its job, and repricing it
		would reopen a closed trade.

		Offline that id is this book's own. Shadowing a live account it is the
		broker's, which this book has never heard of - the simulator numbers its
		orders itself. So the signal is the fallback name: it is on both sides'
		orders by construction, and the money manager keeps one trade at a time,
		so a signal with a single filled order is unambiguous.
		"""
		# Event.__set__ renders a price it cannot read as the string "0.0", so
		# a level of zero is the absence of one and not a level to move to.
		try:
			price = float(getattr(event, 'price', None))
		except (TypeError, ValueError):
			price = None
		if not price:
			return False
		o = self.findTrade(event)
		if o is None:
			return False
		sl = getattr(o, 'SLOrder', None)
		if sl is None or getattr(sl, 'state', None) != 'PENDING':
			return False
		was, sl.price = sl.price, price
		self.logger.info("===== MOVED STOP order# %s %s -> %s"
			% (o.id, was, sl.price))
		return True

	def findTrade(self, event):
		"""The filled order a stop modification is about: by id, else by signal."""
		orderID = getattr(event, 'orderID', None)
		if orderID is not None:
			for o in self.orders:
				if o.id == orderID:
					return o

		signal = getattr(event, 'signalNumber', None)
		if signal is None:
			return None
		# the parent of a bracket, not its stop leg: only the parent holds an
		# SLOrder, and both carry the signal they came from
		found = [o for o in self.orders
				 if getattr(o, 'signalNumber', None) == signal
				 and getattr(o, 'SLOrder', None) is not None]
		return found[0] if len(found) == 1 else None

	def handleSLTP(self, o, e):
		if o.type in ['TAKE_PROFIT_ORDER','STOP_LOSS_ORDER']:
			# Signed from the trade's own direction, not from which leg took
			# it.
			# Was: abs(), negated for a STOP_LOSS_ORDER - which reads "a stop
			#      is a loss". That held while every stop sat the adverse side
			#      of the entry, and stops being true the moment one is moved:
			#      a trailing stop taken above a long's entry is a win, and
			#      was being booked as a loss of the same size, so the balance
			#      moved the wrong way twice over.
			# Now: (exit - entry) * the units of the trade. Identical on every
			#      bracket whose stop never moves, which is all of AG01 and
			#      AG02.
			gain = (o.price - o.orig.price) * o.orig.units
			o.state = 'CLOSED'
			o.orig.state = 'CLOSED'
			# one of the two took the trade, so the other is off the book:
			# a bracket is one-cancels-the-other, and leaving the loser
			# resting closes the same trade a second time when price reaches
			# it later. See retire().
			if o.type=='TAKE_PROFIT_ORDER':
				self.retire(o.orig.SLOrder)
			if o.type=='STOP_LOSS_ORDER':
				self.retire(o.orig.TPOrder)
			self.balance += gain
			self.logger.info("******** CLOSED TRADE TYPE: %s PL: %d OPEN:%6.2f CLOSED:%6.2f BALANCE:%6.2f"
				% (o.type, gain, o.orig.price, o.price, self.balance))
			self.queue_event(SimulatedFillEvent({
				'orderID': o.orig.id,
				'closingOrderID': o.id,
				'instrument': getattr(o, 'instrument', None),
				'units': o.units,
				'price': o.price,
				'time': e.time,
				'pl': gain,
				'financing': 0.0,
				'accountBalance': self.balance,
				'reason': o.type,
				'tradesClosed': [{'tradeID': o.orig.id, 'realizedPL': gain}],
				'signalNumber': getattr(o.orig, 'signalNumber', None),
			}))
			return 

		o.batchID = o.id
		o.SLOrder = None
		o.TPOrder = None
		o.type = None
		if o.stopLoss is not None:
			new = Event(o.to_dict())
			new.units = - o.units
			new.price = o.stopLoss
			new.type = 'STOP_LOSS_ORDER'
			new.state = 'PENDING'
			new.orig = o
			# the order the book holds, not the event it was built from
			o.SLOrder = self.createOrder(new)
			self.logger.debug("== ADDED STOP LOSS @%f" % new.price)

		if o.takeProfit is not None:
			new = Event(o.to_dict())
			new.units = - o.units
			new.price = o.takeProfit
			new.type = 'TAKE_PROFIT_ORDER'
			new.state = 'PENDING'
			new.orig = o
			o.TPOrder = self.createOrder(new)
			self.logger.debug("== ADDED TAKE PROFIT @%f" % new.price)


	def reportFill(self, o, event):
		"""
		Publish an opening fill. Only the parent order is reported here: the
		stop and target legs report through handleSLTP when they close the
		trade, which is the shape OANDA's transaction stream has.
		"""
		self.queue_event(SimulatedFillEvent({
			'orderID': o.id,
			'instrument': getattr(o, 'instrument', None),
			'units': o.units,
			'price': o.price,
			'time': event.time,
			'reason': 'ORDER_FILL',
			'accountBalance': self.balance,
			'signalNumber': getattr(o, 'signalNumber', None),
		}))

	def checkOrder(self, event):
		if self.granularity is not None and getattr(event, 'granularity', None) != self.granularity:
			return
		instrument = getattr(event, 'instrument', None)
		self.logger.debug("TIME: %s o:%6.2f h:%6.2f l:%6.2f c:%6.2f"
			% ( event.time, event.mid['o'], event.mid['h'], event.mid['l'], event.mid['c']))
		# snapshot: handleSLTP appends the stop/target to self.orders, and a
		# child must not be matched against the very bar that opened the trade
		for o in list(self.orders):
			# an order is only ever filled by its own instrument's candles
			if instrument is not None and getattr(o, 'instrument', None) != instrument:
				continue
			if o.state=='PENDING':
				# a real broker fills on touch, so the bounds are inclusive
				if o.units>0 and o.price >= event.ask['l'] and o.price <= event.ask['h']:
					o.state='FILLED'
					self.logger.info("===== FILLED BUY ORDER# %s %f [ %f %f ]" % (o.id, o.price, event.ask['l'], event.ask['h']))
					self.reportFill(o, event)
					self.handleSLTP(o, event)
				if o.units<0 and o.price >= event.bid['l'] and o.price <= event.bid['h']:
					o.state='FILLED'
					self.logger.info("===== FILLED SELL ORDER# %s %f [ %f %f ]" % (o.id, o.price, event.bid['l'], event.bid['h']))
					self.reportFill(o, event)
					self.handleSLTP(o, event)
			elif o.state=='FILLED':
				pass

	def execute_event(self, event):
		if str(event)=='ORDERCANCEL':
			return self.cancelOrder(event)

		if str(event)=='STOPMODIFY':
			return self.modifyStop(event)
	
		if str(event)=='ORDER':
			return self.createOrder(event)

		if str(event)=='CANDLE':
			return self.checkOrder(event)

		return
		'''
{"batchID": "1253"
, "_type": "TRANSACTION"
, "triggerCondition": "TRIGGER_DEFAULT"
, "price": 11720.0
, "stopLossOnFill": {"timeInForce": "GTC", "price": "11730.0"}
, "userID": 0
, "takeProfitOnFill": {"timeInForce": "GTC", "price": "11710.0"}
, "timeInForce": "GTD"
, "instrument": "DE30_EUR"
, "reason": "CLIENT_ORDER"
, "id": "1253"
, "time": "1970-01-01T00:00:00"
, "units": "-1"
, "_created": "2017-01-30T11:10:27.551657"
, "gtdTime": "2017-01-30T14:17:00.000000000Z"
, "type": "STOP_ORDER"
, "positionFill": "DEFAULT"
, "accountID": "101-000-0000000-000"}

ORDER:
{"order":
{"instrument":"DE30_EUR"
,"type":"STOP"
,"units":"-1"
,"price":"11690.2"
,"timeInForce":"GTD"
,"gtdTime":"2017-01-31T15:55:00.000Z"
,"stopLossOnFill":{"price":"11704.2"}
,"takeProfitOnFill":{"price":"11680.2"}
}}
		'''
