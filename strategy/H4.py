"""
The five 4H strategies of 5-STRATEGIE.md, and what they have in common.

The document (strategy/5-STRATEGIE.md, kept beside this file) describes five
systems at the level of logic and flow, for 4H candles, and states the parts
they share:

	valuta alla chiusura della candela, mai durante la sua formazione;
	una posizione per strategia; stop loss obbligatorio a ogni ingresso;
	target preferibilmente >= 2R; dimensiona sulla percentuale di capitale
	che lo stop farebbe perdere.

That shared part is this class. Each strategy is a subclass that declares the
indicators it reads and answers one question - "on this closed bar, is there a
trade, and with what stop and target?" - and everything else is here:

* **the bar is closed.** A CandleEvent arrives when the bar has printed, so
  reading its close is not look-ahead; no strategy here reads further.
* **the entry is a market order.** The document says "apri al prezzo di
  chiusura". Offline the first price after that close is the next bar's open,
  and that is where backtest/oanda.py fills a MARKET order - nothing here
  pretends to have been filled at a price that had already gone.
* **one position at a time** is portfolio/moneymanager.py's rule already, so
  it is not repeated here.
* **the stop and the target** go out with the order, as an OCO bracket the
  simulator and every broker already know how to hold. The document's exit
  rule - "se il massimo tocca il target chiudi in profitto, se il minimo tocca
  lo stop chiudi in perdita" - is what that bracket does.
* **the volatility filter** of the closing section: refuse a signal when the
  ATR is outside a band. Off unless asked for, because the document gives no
  numbers ("secondo soglie definite").

The numbers each strategy uses are the document's own where it states one, and
where it offers a range ("es. 1.5-2") the choice is named in the subclass and
is a parameter, so the range is still there for whoever wants the other end.

The periods are the document's numbers on 4H. Running them on another
granularity is a choice, not a default, and the page names the granularity in
its title so the choice is visible.
"""

import logging

from parity_deriva.event.event import SignalEvent
from parity_deriva.lib.streaming import Series
from parity_deriva.lib.utils import granularityToTimedelta, roundPrice, \
	signalNumber
from parity_deriva.trading.handler import ExecutionHandler


def bullish(candle):
	return candle.mid['c'] > candle.mid['o']


def bearish(candle):
	return candle.mid['c'] < candle.mid['o']


def body(candle):
	return abs(candle.mid['c'] - candle.mid['o'])


def span(candle):
	return candle.mid['h'] - candle.mid['l']


def pinBar(candle, side, wick=2.0):
	"""
	A candle whose tail on one side is `wick` times its body.

	The document names the pattern and not its proportions, so the two are a
	parameter with the usual chart-reading default of twice the body.
	"""
	top, bottom = max(candle.mid['o'], candle.mid['c']), \
		min(candle.mid['o'], candle.mid['c'])
	upper, lower = candle.mid['h'] - top, bottom - candle.mid['l']
	size = body(candle)
	if not span(candle):
		return False
	if side > 0:
		return lower >= wick * size and lower > upper
	return upper >= wick * size and upper > lower


def engulfing(candle, previous, side):
	"""This candle's body covers the previous one's, the other way round."""
	if side > 0:
		return bullish(candle) and bearish(previous) \
			and candle.mid['c'] >= previous.mid['o'] \
			and candle.mid['o'] <= previous.mid['c']
	return bearish(candle) and bullish(previous) \
		and candle.mid['c'] <= previous.mid['o'] \
		and candle.mid['o'] >= previous.mid['c']


class H4(ExecutionHandler):
	"""One strategy of the document. Subclasses answer signal()."""

	#: the timeframe the document is written for, and the instrument; both
	#: are also what the page selects when one of the five is picked
	GRANULARITY = 'H4'
	INSTRUMENT = 'EUR_USD'

	#: ATR(14), which every one of the five uses for something - sizing a
	#: stop, measuring "narrow", or the volatility filter
	ATR_PERIOD = 14

	#: how many bars back from the signal the entry rule reads, for the box
	#: the chart draws. One bar unless the subclass says otherwise.
	SETUP_BARS = 1

	INDICATORS = ()

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'pairs', [self.INSTRUMENT])
		self._set(args, 'granularity', self.GRANULARITY)
		self._set(args, 'atrPeriod', self.ATR_PERIOD)
		#: the volatility filter of the document's closing section, in pips.
		#: None is no filter, which is what every run gets unless it asks.
		self.atrMinPips = None
		self.atrMaxPips = None
		self._set(args, 'atrMinPips')
		self._set(args, 'atrMaxPips')
		#: what a pip is. Only the volatility filter reads it, and only when
		#: it is switched on.
		self._set(args, 'pipSize', 0.0001)
		self.bar = granularityToTimedelta(self.granularity)
		self.setup(args)
		self.state = dict((pair, self.series()) for pair in self.pairs)
		self.logger.debug("%s ready on %s" % (self.__class__.__name__,
											  self.granularity))

	# ------------------------------------------------------ the subclass

	def setup(self, args):
		"""Read the parameters this strategy adds. Optional."""

	def series(self):
		"""The reading state one instrument needs. Subclasses extend it."""
		return Series(atr=self.atrPeriod)

	def signal(self, state, candle):
		"""
		(side, stop, target) for a trade on this closed bar, or None.

		`side` is +1 or -1 and the two levels are prices. Called only once the
		bar has been folded into `state`, so everything it reads is a fact
		about bars that have printed.
		"""
		return None

	# --------------------------------------------------------- the flow

	def execute_event(self, event):
		if str(event) != 'CANDLE' or self.otherStream(event):
			return
		if event.instrument not in self.state:
			return

		state = self.state[event.instrument]
		self.feed(state, event)

		found = self.signal(state, event)
		if not found:
			return
		side, stop, target = found
		if not self.volatile(state):
			return
		self.emit(event, side, stop, target)

	def feed(self, state, candle):
		"""Advance the reading state. Subclasses with more state extend it."""
		state.add(candle)

	def volatile(self, state):
		"""
		Is the market lively enough, and not too lively, to trade?

		The document asks for this and states no numbers, so the thresholds
		are the caller's and the filter is off until one is given.
		"""
		if self.atrMinPips is None and self.atrMaxPips is None:
			return True
		atr = state.atr() if hasattr(state, 'atr') else None
		if atr is None:
			return False
		if self.atrMinPips is not None and atr < self.atrMinPips * self.pipSize:
			self.logger.debug("no trade: ATR %s below the floor" % atr)
			return False
		if self.atrMaxPips is not None and atr > self.atrMaxPips * self.pipSize:
			self.logger.debug("no trade: ATR %s above the ceiling" % atr)
			return False
		return True

	def emit(self, candle, side, stop, target, entry=None, expiry=None):
		"""
		One market order with its bracket.

		The price carried is the close of the signal bar on the side the trade
		opens against - the ask for a long, the bid for a short - because that
		is the document's entry and it is what the position is sized from. The
		fill is whatever the next bar opens at, which the ledger writes down
		separately: the difference between the two is the cost of acting on a
		close, and hiding it would be the point of the exercise lost.

		With an `entry` it is a stop order resting at that price instead,
		until `expiry` - "entra sopra il massimo della candela di conferma",
		which the M15 strategies of 6-STRATEGIE-M15.md ask for. Returns the
		signal, so a caller can know it again when it fills.
		"""
		i = candle.instrument
		price = candle.ask['c'] if side > 0 else candle.bid['c']
		if entry is not None:
			price = entry

		se = SignalEvent()
		se.signalNumber = signalNumber(self.tag(), i, self.granularity,
									   candle.time)
		se.clientExtension = {'id': se.signalNumber,
							  'tag': self.tag(),
							  'comment': '%s' % self.granularity}
		#: one leg: the side is already decided, there is nothing to straddle
		se.signalType = 'SINGLE'
		se.orderType = se.type = 'MARKET' if entry is None else 'STOP'
		se.instrument = i
		se.time = candle.time
		se.price = roundPrice(i, price)
		se.stopLoss = roundPrice(i, stop)
		se.takeProfit = roundPrice(i, target)
		se.units = 1 if side > 0 else -1
		# a market order that has not filled by the next bar has missed the
		# close it was acting on, and is not this trade any more
		se.gtdTime = candle.time + 2 * self.bar if self.bar is not None else None
		if expiry is not None:
			se.gtdTime = expiry

		if self.event_queue is not None:
			self.queue_event(se)
			self.logger.info("SENT %s" % se.info())
		return se

	#: What the ledger, the order comment and the page call this strategy.
	#: The class name is a file name - H401 says nothing about what it does -
	#: so each subclass sets the name a human reads, and the two are allowed
	#: to differ exactly the way AG01MOD's already do.
	TAG = None

	def tag(self):
		return self.TAG or self.__class__.__name__

	# ------------------------------------------------------------ helpers

	def levels(self, candle, side, stop, ratio):
		"""A target `ratio` times the stop distance, on the right side."""
		close = candle.mid['c']
		risk = abs(close - stop)
		return close + ratio * risk if side > 0 else close - ratio * risk
