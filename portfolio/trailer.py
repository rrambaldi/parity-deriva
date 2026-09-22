"""
Walk the stop of an open trade up the ladder its order was issued with.

Some strategies have no target at all: their whole exit is a stop that
climbs, by a step the signal states, to levels the signal states. A trade
that never gets going has its stop pulled up to a recent extreme after a few
bars instead. Which strategies those are is not this file's business - the
ladder arrives as numbers on the signal, so this component holds no constant
of its own and a different strategy is a different signal, not a different
trailer.

None of that can live in the order. An order states a level once; this is a
rule that answers differently on every bar. So it lives here, in one component
on the bus, and speaks to whatever is executing through a single
StopModifyEvent.

**One implementation, two backends.** Offline the event reaches
backtest/oanda.modifyStop, which reprices the resting stop. Live it reaches
execution/execution.OANDAExecutionHandler.modifyStop, which asks OANDA to
replace the trade's stop - one request that cancels the old one and attaches
the new. Providers that cannot do either declare `stop_modify` False and
scripts/live.py refuses to start such a strategy on them at all. That refusal
is the point: a ladder walked in the simulator and not on the account is
exactly the divergence trading/parity.py exists to detect, and it is cheaper
to not start than to detect it afterwards.

**Two names for one trade.** Offline the simulator's entry order and the trade
it opens are the same number. At OANDA they are not: the stop hangs off a
tradeID the opening fill reports separately, under `tradeOpened`. Both are read
off the fill and both travel on the event, so each side is addressed by the
name it actually uses and neither has to guess the other's.

**No numbers of its own.** Every figure - the step, the first rung, how many
bars before the time stop, how far beyond the extreme it goes - arrives on the
order, because the strategy is where the document's parameters are configured.
This component knows the shape of the ladder and none of its dimensions, so an
instrument with a different pip size is a different strategy constructor call
and not a change here.

	engine.add_handler(Trailer(granularity='D'))
"""

import logging

from parity_deriva.event.event import StopModifyEvent
from parity_deriva.lib.utils import roundPrice
from parity_deriva.trading.handler import ExecutionHandler


class Trailer(ExecutionHandler):
	"""
	Remembers the ladder an order carried, and applies it once it fills.

	The join is on (signalNumber, price): an order announces itself with both,
	and so does the fill that opens it. The fill also carries the id the
	book gave that order, which is what a later amend has to name - so the id
	is learned here rather than assumed, and a trade whose fill was never seen
	is never trailed.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		# Which candle stream drives the ladder. Same reason
		# backtest/oanda.py takes one: with two granularities of an
		# instrument on the bus, nothing in a candle says which is the one
		# the strategy signalled on. _set() only assigns when the default is
		# not None, so seed it first.
		self.granularity = None
		self._set(args, 'granularity')

		#: (signalNumber, price) -> the ladder that order was issued with
		self.ladders = {}
		#: orderID -> the trade it opened
		self.open = {}

	# ------------------------------------------------------------- recording

	def onOrder(self, event):
		"""
		Keep the ladder an order carries, if it carries one.

		An order without a trailStep is somebody else's - AG01's brackets go
		past here too - and is not remembered at all, so this component is
		inert on a bus it has nothing to do with.
		"""
		step = getattr(event, 'trailStep', None)
		if step is None:
			return
		key = (getattr(event, 'signalNumber', None), getattr(event, 'price', None))
		self.ladders[key] = {
			'instrument': getattr(event, 'instrument', None),
			'units': float(getattr(event, 'units', 0) or 0),
			'stop': _float(getattr(event, 'stopLoss', None)),
			'step': _float(step),
			'first': _float(getattr(event, 'trailFirst', None)),
			'timeStopBars': int(getattr(event, 'timeStopBars', 0) or 0),
			'timeStopOffset': _float(getattr(event, 'timeStopOffset', None)),
		}

	def onFill(self, event):
		if event.has_attr('tradesClosed'):
			return self.onClose(event)

		signal = getattr(event, 'signalNumber', None)
		key = (signal, getattr(event, 'price', None))
		ladder = self.ladders.pop(key, None)
		if ladder is None:
			ladder = self.bySignal(signal)
		if ladder is None:
			return
		orderID = getattr(event, 'orderID', None)
		if orderID is None:
			return

		trade = dict(ladder)
		trade['signalNumber'] = signal
		#: the broker's own name for the trade, when it has one. OANDA reports
		#: it under `tradeOpened` on the opening fill and addresses the stop by
		#: it; the simulator numbers a trade after the order that opened it and
		#: reports no such field, so offline this stays None and the orderID is
		#: the only name in play.
		trade['tradeID'] = _tradeID(event)
		trade['entry'] = _float(getattr(event, 'price', None))
		#: bars seen since the entry bar. The entry bar itself is not counted:
		#: this component sees a candle before the simulator fills against it,
		#: so the trade does not exist yet when its own bar goes past. That
		#: makes bars==1 the second day, which is what "DOPO DUE GIORNI DALLA
		#: MIA ENTRY ( ENTRY COMPRESA )" asks for.
		trade['bars'] = 0
		trade['stepped'] = False
		trade['timed'] = False
		self.open[orderID] = trade
		self.logger.debug("trailing order# %s from %s, step %s"
			% (orderID, trade['entry'], trade['step']))

	def bySignal(self, signal):
		"""
		The ladder of a signal whose fill did not land on the order's price.

		The exact join is on (signalNumber, price), because a straddle puts two
		orders of one signal on the book at different prices. Live, though, a
		stop order fills at the price the market gave, not the price it was
		written at, so the exact key misses on the first slipped pip. When the
		signal left one order - which is the case for every SINGLE signal -
		there is no ambiguity to protect and the ladder is found by name.
		"""
		if signal is None:
			return None
		keys = [k for k in self.ladders if k[0] == signal]
		if len(keys) != 1:
			return None
		return self.ladders.pop(keys[0])

	def onClose(self, event):
		"""
		Forget a trade that has closed, by whichever name the close gives it.

		The simulator reports the closing fill under the *entry* order's id.
		OANDA reports it under the closing order's - the stop's - and names the
		trade in `tradesClosed`. Matching on both means a closed trade stops
		being trailed on either side; matching on one would leave a live trade
		being walked up a ladder after it had been taken out.
		"""
		orderID = getattr(event, 'orderID', None)
		if orderID in self.open:
			del self.open[orderID]

		closed = set()
		for t in (getattr(event, 'tradesClosed', None) or []):
			tradeID = t.get('tradeID') if isinstance(t, dict) else t
			if tradeID is not None:
				closed.add(str(tradeID))
		if not closed:
			return
		for key in list(self.open):
			if str(self.open[key].get('tradeID')) in closed:
				del self.open[key]

	def onReject(self, event):
		"""An order that never became a trade takes its ladder with it."""
		key = (getattr(event, 'signalNumber', None), getattr(event, 'price', None))
		self.ladders.pop(key, None)

	# ---------------------------------------------------------- the ladder

	def rung(self, trade, candle):
		"""
		Where the stop belongs after this bar, or None to leave it alone.

		The step count is read off the bar's favourable extreme on the side
		the trade will exit against - a long exits by selling, so its progress
		is measured on the bid - which is the same convention
		backtest/resolution.py fills on. Measuring on the mid would credit the
		trade with half a spread it could not have taken.

		The first rung is special and the document says so: break even plus a
		fixed few pips, not entry plus a step. From the second on it is entry
		plus (n-1) steps, which is what the worked example on p. 7 shows -
		1.3100 puts the stop at 1.3050, 1.3150 at 1.3100.
		"""
		step = trade['step']
		if not step or step <= 0:
			return None

		long = trade['units'] > 0
		sign = 1 if long else -1
		best = candle.bid['h'] if long else candle.ask['l']
		# the epsilon is not decoration: a bar landing exactly on a rung is
		# the document's own worked example (p. 7, 1.3050 off an entry of
		# 1.3000 with a 50 pip step), and in binary that subtraction comes out
		# a shade under one step. Without it the example's first rung is
		# missed and the time stop fires in its place.
		reached = int((best - trade['entry']) * sign / step + 1e-9)

		if reached >= 1:
			# Once the trade has stepped, the time stop no longer applies:
			# N.B. 3 fires only when the market "NON MI FA SPOSTARE A B.E."
			trade['stepped'] = True
			if reached == 1:
				return trade['entry'] + sign * trade['first']
			return trade['entry'] + sign * (reached - 1) * step

		if trade['stepped'] or trade['timed']:
			return None
		if trade['timeStopBars'] and trade['bars'] >= trade['timeStopBars'] - 1:
			# A once-only move, not a daily trail: N.B. 3 describes a single
			# action and never says it repeats (zone d'ombra 12).
			trade['timed'] = True
			offset = trade['timeStopOffset'] or 0.0
			return (candle.bid['l'] - offset) if long else (candle.ask['h'] + offset)

		return None

	def onCandle(self, event):
		if self.granularity is not None \
				and getattr(event, 'granularity', None) != self.granularity:
			return

		for orderID in list(self.open):
			trade = self.open[orderID]
			if trade['instrument'] != event.instrument:
				continue
			trade['bars'] += 1

			level = self.rung(trade, event)
			if level is None:
				continue
			level = roundPrice(event.instrument, level)

			# a ratchet: the stop only ever moves the way the trade is going,
			# so a bar that gives back ground cannot loosen it
			if trade['units'] > 0 and level <= trade['stop']:
				continue
			if trade['units'] < 0 and level >= trade['stop']:
				continue

			trade['stop'] = level
			self.queue_event(StopModifyEvent({
				'orderID': orderID,
				'tradeID': trade.get('tradeID'),
				'signalNumber': trade.get('signalNumber'),
				'instrument': event.instrument,
				'price': level,
				'time': event.time,
			}))
			self.logger.info("STOP to %s on order# %s" % (level, orderID))

	def execute_event(self, event):
		kind = str(event)
		if kind == 'CANDLE':
			return self.onCandle(event)
		if kind == 'ORDER':
			return self.onOrder(event)
		if kind == 'TRANSACTION':
			what = str(getattr(event, 'type', ''))
			if what == 'ORDER_REJECT' or what.endswith('_ORDER_REJECT'):
				return self.onReject(event)
			return self.onFill(event)
		if kind == 'ORDERCANCEL':
			return


def _tradeID(event):
	"""
	The broker's id for the trade an opening fill just created.

	OANDA's ORDER_FILL carries `tradeOpened` - a TradeOpen object, or a list of
	them on a fill that opened more than one. Nothing else here reports the
	field, so its absence is the normal case and not an error.
	"""
	opened = getattr(event, 'tradeOpened', None)
	if isinstance(opened, (list, tuple)):
		opened = opened[0] if opened else None
	if isinstance(opened, dict):
		return opened.get('tradeID')
	if opened is not None:
		return getattr(opened, 'tradeID', None)
	return None


def _float(value):
	try:
		return float(value)
	except (TypeError, ValueError):
		return None
