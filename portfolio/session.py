"""
End the trading day: close what is open when the session is over.

A bracket has two ways out and both are levels - the stop and the target -
decided when the order was placed. A strategy that does not hold overnight
needs a third, and it is not a level: it is the clock. This is that rule, in
one component on the bus, speaking through one CloseTradeEvent.

**The rule is here and the execution is not.** Offline the event reaches
backtest/oanda.closeOpen, which closes against the bar it arrives on; live it
would reach a broker's own close-trade call. The decision - which bar ends
the day - is taken once, here, so the simulator and the account act on the
same decision rather than on two implementations of it. That is the argument
portfolio/trailer.py makes about the stop ladder, and it is the same argument.

**Which bar ends the day.** The one the cut falls inside: with a cut at 21:00
UTC and four hour bars, the bar that opens at 20:00 and runs to midnight. The
position is closed at that bar's close, which is the last price of the day
the strategy could actually have traded at. A bar that does not contain the
cut is left alone, so a day with no bar over the cut - a holiday, a gap -
closes nothing rather than closing at an invented time.

**No number of its own.** The cut arrives from whoever built this: the end of
the session when there is one, and the end of the UTC day when there is not.
Which of those it should be is a decision about the strategy, not a fact
about the market, and this file holds neither.

	engine.add_handler(SessionCloser(at='21:00', granularity='H4',
									 instrument='EUR_USD'))
"""

import datetime
import logging

from parity_deriva.event.event import CloseTradeEvent
from parity_deriva.lib.utils import granularityToTimedelta
from parity_deriva.trading.handler import ExecutionHandler

#: what the fill says it was. Not TAKE_PROFIT_ORDER and not STOP_LOSS_ORDER:
#: the trade ended because the day did, and a report that called it a stop
#: would be counting the clock as a level the market reached.
SESSION_CLOSE = 'SESSION_CLOSE'


class SessionCloser(ExecutionHandler):
	"""
	Watches the bars and ends the day.

	It does not know whether anything is open: it says "close what is open on
	this instrument" once a day and the broker answers by closing nothing if
	there is nothing. Asking first would mean keeping a copy of the book here,
	and a second copy of the book is a second opinion about the account.
	"""

	def __init__(self, at, granularity=None, instrument=None, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self.at = _clock(at)
		self.granularity = granularity
		self.instrument = instrument
		self.span = granularityToTimedelta(granularity) if granularity else None
		#: the last day already closed, so one day closes once however many
		#: bars of it arrive
		self.closed = None

	def ends(self, when):
		"""
		Does the cut fall inside the bar that opens here?

		Half open, like every other window in this project: a bar that opens
		exactly on the cut is the first bar of the next day and not the last
		of this one.
		"""
		if self.span is None:
			return when.time() >= self.at
		cut = datetime.datetime.combine(when.date(), self.at)
		return when < cut <= when + self.span

	def execute_event(self, event):
		if str(event) != 'CANDLE':
			return
		if self.instrument is not None \
				and getattr(event, 'instrument', None) != self.instrument:
			return
		# the strategy's own bars and not the fine ones the orders rest on:
		# the day ends once, not two hundred and eighty-eight times
		if self.otherStream(event):
			return
		when = getattr(event, 'time', None)
		if when is None or not self.ends(when):
			return
		if self.closed == when.date():
			return
		self.closed = when.date()
		self.logger.info("SESSION CLOSE %s: %s ends the day"
						 % (getattr(event, 'instrument', None), when))
		self.queue_event(CloseTradeEvent({
			'instrument': getattr(event, 'instrument', None),
			'time': when,
			'reason': SESSION_CLOSE,
		}))


def _clock(text):
	"""'21:00' as a time of day; a bare number of hours is one too."""
	if isinstance(text, datetime.time):
		return text
	if isinstance(text, int):
		return datetime.time(hour=text)
	hours, _, minutes = str(text).partition(':')
	return datetime.time(hour=int(hours), minute=int(minutes or 0))


#: what the fill says when the trade ran out of time rather than reaching a
#: level or the end of the day
MAX_LENGTH = 'MAX_LENGTH'


class TradeTimer(ExecutionHandler):
	"""
	Close a trade that has been open for `bars` of the strategy's own bars.

	The same shape as SessionCloser: it decides when, one CloseTradeEvent
	says so, and whoever executes decides the price. It does have to know
	that something is open, since the clock starts at the fill - so it reads
	the fills off the bus, which is where the money manager learns them too.
	"""

	def __init__(self, bars, granularity=None, instrument=None, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self.bars = int(bars)
		self.granularity = granularity
		self.instrument = instrument
		#: strategy bars seen since the fill, or None with nothing open
		self.held = None

	def execute_event(self, event):
		kind = str(event)
		if kind == 'TRANSACTION' and getattr(event, 'type', None) == 'ORDER_FILL':
			# an opening fill starts the clock, a close stops it
			self.held = None if event.has_attr('tradesClosed') else 0
			return
		if kind != 'CANDLE' or self.held is None or self.otherStream(event):
			return
		if self.instrument is not None \
				and getattr(event, 'instrument', None) != self.instrument:
			return
		self.held += 1
		if self.held < self.bars:
			return
		self.held = None
		self.logger.info("MAX LENGTH %s: %d bars at %s"
						 % (self.instrument, self.bars, getattr(event, 'time', None)))
		self.queue_event(CloseTradeEvent({
			'instrument': getattr(event, 'instrument', None),
			'time': getattr(event, 'time', None),
			'reason': MAX_LENGTH,
		}))
