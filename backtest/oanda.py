import json
import datetime
import logging
from zoneinfo import ZoneInfo

from parity_deriva.etc import settings
from parity_deriva.trading.handler import ExecutionHandler
from parity_deriva.event.event import ClientOrderEvent
from parity_deriva.lib.oanda import OANDAOrder
from parity_deriva.lib.oanda import OANDATrade
from parity_deriva.event.event import Event
from parity_deriva.event.event import SimulatedOrderEvent
from parity_deriva.event.event import SimulatedFillEvent
from parity_deriva.event.event import SimulatedOrderCancelEvent

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
		#: instrument -> the last candle seen on that stream. A gap is only
		#: visible against the bar before it: see fillAt().
		self.last = {}
		#: the commission a side ('X/lot', 'X/trade'; COMMISSION) and the
		#: yearly financing by instrument (FINANCING): see costs()
		self.commission = None
		self._set(args, 'commission')
		if self.commission is None:
			self.commission = getattr(self.setup, 'COMMISSION', None)
		self.financing = parseFinancing(getattr(self.setup, 'FINANCING', None))

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

		Never a bracket's own stop or target, though, and that is not a
		refinement - it is the difference between a trade having a stop and
		not having one. **In AG01 the long's stop sits at exactly the short
		leg's entry price**, by construction: the pair is a straddle around
		two candles and each leg's stop is the other leg's entry. So when one
		leg fills and the money manager cancels the other by price, a match on
		price alone can find the stop of the trade that just opened and
		cancel that instead - leaving a live position with nothing under it,
		which then runs until it happens to reach its target. On EUR_USD
		daily that was months, and it read as a strategy with a remarkable
		win rate.

		A child is never what a cancel is about: the money manager issues
		legs and hears about legs. The bracket's children are the simulator's
		own, and retire() cancels those - by object, from the one place that
		knows which of the two closed the trade.
		"""
		instrument = getattr(event, 'instrument', None)
		for o in list(self.orders):
			if getattr(o, 'orig', None) is not None:
				continue
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
			commission, financing = self.costs(o.orig, o.price, e.time)
			gain = (o.price - o.orig.price) * o.orig.units + commission + financing
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
				'financing': financing,
				'commission': commission,
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


	def costs(self, trade, price, when):
		"""
		(commission, financing) of a trade closed at `price` on `when`, both
		negative for a cost and in the quote currency, as the move is: the
		commission on the way in and on the way out, and the instrument's
		yearly rate for its side on the notional each night it was held.
		"""
		units = abs(float(getattr(trade, 'units', 0) or 0))
		commission = 0.0
		text = str(self.commission or '').strip()
		if text:
			amount, _, per = text.partition('/')
			try:
				amount = float(amount)
			except ValueError:
				amount = 0.0
			commission = -2 * (amount if per.strip() == 'trade' else amount * units / 100000.0)
		rates = self.financing.get(getattr(trade, 'instrument', None))
		financing = 0.0
		opened = getattr(trade, 'filledAt', None)
		if rates and opened is not None and when is not None:
			rate = rates[0] if float(getattr(trade, 'units', 0) or 0) > 0 else rates[1]
			financing = nights(opened, when) * rate / 100.0 / 365.0 * units * float(price)
		return commission, financing

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

	def dropSiblings(self, filled):
		"""
		One entry of a signal fills; the other end of it never existed.

		AG01 brackets a reversal with two opposite orders and says
		signalType EXCLUSIVE: on the account, the first to fill cancels the
		other. Here nothing did that within a bar. The money manager does
		publish the cancel, but it hears about the fill only after this
		candle has been handed to every handler - and by then both ends had
		filled, on a bar wide enough to reach them both.

		Children are left alone: a trade's stop and target are cancelled by
		each other through retire(), which knows which of them closed it.
		"""
		key = getattr(filled, 'signalNumber', None)
		if key is None:
			return
		for other in list(self.orders):
			if other is filled or getattr(other, 'orig', None) is not None:
				continue
			if getattr(other, 'signalNumber', None) != key:
				continue
			if self.retire(other):
				self.logger.info("===== OCO: ORDER# %s dropped, %s filled"
								 % (other.id, filled.id))

	def expired(self, o, event):
		"""
		Has this order outlived the expiry it was issued with?

		Nothing offline read gtdTime before. AG01 and AG02 issue a bracket
		that is meant to die at the end of the day - a straddle around one
		reversal is not that trade a week later - and live the brokers
		enforce it, three of them on our own side (data/etoro.py, data/ib.py,
		data/ig.py). Here the order simply rested: on EUR_USD daily a bracket
		from 3 July 2022 filled on 15 November at a price from four months
		earlier, and, this stack taking one position at a time, held the
		strategy shut for all of it.

		Only an entry expires. The stop and the target of an open trade carry
		their parent's fields, expiry included, and cancelling those at
		midnight would leave a position standing with neither - which is not
		what any account does: a bracket's children are good until the trade
		closes.

		The comparison is against the candle's own time, so the order dies on
		the first bar that begins after the instant it was given, which is
		where it would have died.
		"""
		if getattr(o, 'orig', None) is not None:
			return False
		when = getattr(o, 'gtdTime', None)
		if when is None or event.time <= when:
			return False
		o.state = 'CANCELED'
		if o in self.orders:
			self.orders.remove(o)
			self.closed_orders.append(o)
		self.logger.info("===== EXPIRED ORDER# %s %s past %s"
						 % (o.id, o.price, when))
		# said out loud, because the money manager counts a group as open
		# until its orders are accounted for, and the ledger writes the leg
		# down as cancelled rather than as a leg that vanished.
		# Was: a plain OrderCancelEvent. Live, that is an instruction: the
		#      real execution handler cancelled the order at the broker and
		#      the money manager, matching on price, killed the live leg -
		#      the simulator's expiry leaked onto the real account.
		# Now: the simulator's own type, like its orders and fills; offline
		#      SimulatedBroker promotes it, so nothing there changes.
		self.queue_event(SimulatedOrderCancelEvent({
			'orderID': o.id,
			'price': o.price,
			'instrument': getattr(o, 'instrument', None),
			'signalNumber': getattr(o, 'signalNumber', None),
			'reason': 'GTD_EXPIRY',
		}))
		return True

	def fillAt(self, o, event, side):
		"""
		What this resting order trades at on this bar, or None for no fill.

		Inside the bar's range it trades at its own level, which is what a
		resting order means.

		Outside it, there is one case that is still a fill and it is the one
		this exists for: **price gapped over the level while the book was
		shut.** A bar whose open is on the far side of the level from the
		previous close never offered the level at all - between the two
		prints the market crossed it - and the first price anybody could have
		had is the open. So that is the fill, which is worse than the level
		for a stop or a buy stop and better for a target or a limit, exactly
		as it would have been on the account.

		Was: [low, high] and nothing else, so an order the market jumped over
		     stayed on the book. EUR_USD daily, 21 to 23 April 2017: a long
		     entered at 1.06778 with its target at 1.07851, Friday closing at
		     1.07268 and Monday opening at 1.09197 - straight over it. The
		     target was never touched again until 19 February 2020, so the
		     simulator carried that position for nearly three years and then
		     closed it at a price the trade had made and given back twice
		     over. Every trade after it in that run was refused, because this
		     stack takes one position at a time.
		Now: it fills on the Monday open, which is where it would have filled.

		A gap needs a bar before it. On the first candle of a run there is
		nothing to have gapped from, so only the range applies.
		"""
		book = event.ask if side == 'ask' else event.bid
		before = self.last.get(getattr(o, 'instrument', None))
		opened = book['o']
		# A market order has no level to be reached: it is filled at the first
		# price there is, which is this bar's open - this being the first bar
		# after the close the strategy acted on. Only an entry: the stop and
		# the target a bracket builds carry their parent's fields, this one
		# included, and a stop filled at the open would close every trade on
		# the bar after it opened.
		if getattr(o, 'orig', None) is None \
				and getattr(o, 'orderType', None) == 'MARKET':
			return opened
		# A stop the market is already past is taken at the first price
		# there is. That is what a stop is - "sell once the bid is at or under
		# this" - and it is not a gap: the level can have been passed inside
		# a bar the simulator never saw it on.
		#
		# Was: only a level inside the bar or inside the gap from the last
		#      close filled. A stop moved after the fact - the trailer reads
		#      the strategy's M15 bar, which is dispatched after the M5 bars
		#      it contains - could land above a long's market: the M15 high
		#      reached the rung, the last M5 bar had closed back under it.
		#      Nothing then filled it until price came back up, and on
		#      EUR_USD FTWP a trade stayed open from 2015 to the end of the
		#      data while the strategy kept trading around it.
		# Now: fills at this bar's open, as the account would have.
		if getattr(o, 'type', None) == 'STOP_LOSS_ORDER' \
				and (opened <= o.price if o.units < 0 else opened >= o.price):
			return opened
		if before is not None:
			closed = (before.ask if side == 'ask' else before.bid)['c']
			# the gap is asked about first, and not only when the level is
			# outside the bar: a bar that gapped over the level and then came
			# back through it during the session still crossed it while the
			# book was shut, so the open is the first price there was. Asking
			# the range first would fill that one at the level - better than
			# the market gave, on a bar that had already jumped it
			if min(closed, opened) <= o.price <= max(closed, opened):
				return opened
		if book['l'] <= o.price <= book['h']:
			return o.price
		return None

	def checkOrder(self, event):
		if self.granularity is not None and getattr(event, 'granularity', None) != self.granularity:
			return
		instrument = getattr(event, 'instrument', None)
		self.logger.debug("TIME: %s o:%6.2f h:%6.2f l:%6.2f c:%6.2f"
			% ( event.time, event.mid['o'], event.mid['h'], event.mid['l'], event.mid['c']))
		# snapshot: handleSLTP appends the stop/target to self.orders, and a
		# child must not be matched against the very bar that opened the trade
		# Pending ones only: the book keeps every order that expired or filled
		# (thousands over a year), and sorting them all on every bar was most
		# of a run's time. None of those turns pending again, and dropping
		# them keeps the others in their relative order, so it is the same
		# walk
		resting = [o for o in self.orders
				   if o.state == 'PENDING' and (instrument is None
				   or getattr(o, 'instrument', None) == instrument)]
		# Nearest to the open first.
		#
		# A bar that reaches two of these says nothing about which it reached
		# first - the same blindness backtest/resolution.py measures - and the
		# order matters, because the first fill takes the other off the book.
		# Coming out of the open, the nearer level is the one price met first
		# unless it doubled back, so that is the reading taken.
		#
		# Was: the book's own order, which on a wide bar filled *both* ends of
		#      an AG01 straddle. EUR_USD daily, 26 August 2016: a long at
		#      1.13117 and a short at 1.12452 from one signal, both opened,
		#      each one's stop being the other's entry. The money manager
		#      never saw that group finish and refused every signal after it -
		#      ten years of data, and the run's last trade is in September
		#      2016.
		resting.sort(key=lambda o: abs(
			o.price - (event.ask if o.units > 0 else event.bid)['o']))
		for o in resting:
			if o.state=='PENDING':
				# before the fill: an order whose expiry has passed was not
				# on the book when this bar traded
				if self.expired(o, event):
					continue
				# a buy trades against the ask and a sell against the bid,
				# whatever the order is for: an entry, a stop or a target
				side = 'ask' if o.units>0 else 'bid'
				at = self.fillAt(o, event, side)
				if at is None:
					continue
				book = event.ask if side=='ask' else event.bid
				if at != o.price:
					self.logger.info("===== GAPPED PAST ORDER# %s %f, filled "
						"at the open %f [ %f %f ]"
						% (o.id, o.price, at, book['l'], book['h']))
					# the level it traded at, not the level it asked for.
					# handleSLTP books the P&L off these prices, so a fill
					# recorded at the level would be money the account
					# neither made nor lost
					o.price = at
				o.state='FILLED'
				o.filledAt = event.time
				self.logger.info("===== FILLED %s ORDER# %s %f [ %f %f ]"
					% ('BUY' if o.units>0 else 'SELL', o.id, o.price,
					   book['l'], book['h']))
				self.reportFill(o, event)
				self.handleSLTP(o, event)
				self.dropSiblings(o)
			elif o.state=='FILLED':
				pass

		self.last[instrument] = event

	def closeOpen(self, event):
		"""
		Close whatever is open on an instrument, at the bar this arrives on.

		The third way out of a trade, and the only one that is not a level:
		see event.CloseTradeEvent. A long is sold on the bid and a short
		bought back on the ask, the way every other fill here is priced, and
		the close of the bar is the price - the decision was taken on this
		bar and the account cannot trade at a price the bar never showed.

		Both legs of the bracket come off the book, for the reason retire()
		exists: a stop left resting closes the same trade a second time when
		price reaches it later.
		"""
		instrument = getattr(event, 'instrument', None)
		bar = self.last.get(instrument)
		if bar is None:
			self.logger.warning("CLOSE asked for %s before any bar of it"
								% instrument)
			return
		reason = getattr(event, 'reason', 'CLOSE')
		for o in list(self.orders):
			if o.state != 'FILLED':
				continue
			if instrument is not None \
					and getattr(o, 'instrument', None) != instrument:
				continue
			book = bar.bid if o.units > 0 else bar.ask
			price = float(book['c'])
			commission, financing = self.costs(o, price, getattr(event, 'time', None) or bar.time)
			gain = (price - o.price) * o.units + commission + financing
			o.state = 'CLOSED'
			self.retire(getattr(o, 'SLOrder', None))
			self.retire(getattr(o, 'TPOrder', None))
			if o in self.orders:
				self.orders.remove(o)
				self.closed_orders.append(o)
			self.balance += gain
			self.logger.info("******** CLOSED TRADE TYPE: %s PL: %d OPEN:%6.2f "
				"CLOSED:%6.2f BALANCE:%6.2f"
				% (reason, gain, o.price, price, self.balance))
			self.queue_event(SimulatedFillEvent({
				'orderID': o.id,
				# no second order took this trade: the close is the decision
				# itself, so the closing id is the trade's own
				'closingOrderID': o.id,
				'instrument': getattr(o, 'instrument', None),
				'units': -o.units,
				'price': price,
				'time': getattr(event, 'time', None) or bar.time,
				'pl': gain,
				'financing': financing,
				'commission': commission,
				'accountBalance': self.balance,
				'reason': reason,
				'tradesClosed': [{'tradeID': o.id, 'realizedPL': gain}],
				'signalNumber': getattr(o, 'signalNumber', None),
			}))

	def execute_event(self, event):
		if str(event)=='ORDERCANCEL':
			return self.cancelOrder(event)

		if str(event)=='CLOSETRADE':
			return self.closeOpen(event)

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


NEW_YORK = ZoneInfo('America/New_York')


def parseFinancing(text):
	"""'EUR_USD:-2.5/0.8,GBP_USD:...' as {instrument: (long %, short %)}; bad parts are left out."""
	out = {}
	for part in str(text or '').split(','):
		name, _, rates = part.strip().partition(':')
		lon, _, sho = rates.partition('/')
		try:
			out[name.strip()] = (float(lon), float(sho or 0))
		except ValueError:
			continue
	return out


def nights(opened, closed):
	"""
	How many 17:00s New York - the forex day's end, where a position held pays
	its financing - fall after `opened` and by `closed` (naive UTC).
	"""
	# ponytail: one charge a night, not Wednesday's triple for the weekend
	start = opened.replace(tzinfo=datetime.timezone.utc).astimezone(NEW_YORK)
	end = closed.replace(tzinfo=datetime.timezone.utc).astimezone(NEW_YORK)
	count, day = 0, start.date()
	while True:
		roll = datetime.datetime.combine(day, datetime.time(17), NEW_YORK)
		if roll > end:
			return count
		if roll > start:
			count += 1
		day += datetime.timedelta(days=1)
