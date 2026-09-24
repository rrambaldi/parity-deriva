"""
Fill a coarse strategy's orders against the finest candles the store holds.

A bar reports four prices and not the order they happened in, so a backtest on
daily candles reads an entry stop, a stop loss and a target off the same
reading and has to guess which was touched first. That guess is a coin flip,
and backtest/resolution.py exists to measure how often a run depends on one.
This module is the other half of the same problem: instead of measuring the
ambiguity, remove the part of it that better data can remove.

The strategy keeps its own granularity. It signals on the bars it was written
for and nothing here lets it see a bar it would not have had - what moves is
where its orders rest. The simulator is pointed at the finest series the store
holds for the same instrument, so an entry, a stop and a target are resolved a
minute at a time while the rule that placed them still reads days.

Two streams on one bus need an order, and the order is **when a bar closed**,
not when it opened. A daily candle stamped Monday is knowledge of Monday
night, so it is dispatched after every minute of Monday and before the first
minute of Tuesday. That is the whole of the look-ahead argument: sorted on the
open time instead, an order derived from Monday's close would be resting for
the whole of the Monday it was derived from. Where a fine bar and a coarse one
close at the same instant the fine one goes first, for the same reason.

The finer series has to cover the coarse one or it is not used. A partial one
would fill the trades inside its range and silently drop the rest, and a trade
missing because the data ran out looks exactly like a trade the strategy never
took. Coverage is measured bar to bar - from the first coarse bar of the
window to the last - and not to the end of the window itself: the last coarse
bar closes after its own open by definition, and refusing a series for that
one bar would refuse every series there is.
"""

import os

from parity_deriva.backtest.driver import Collector
from parity_deriva.data import store
from parity_deriva.data.replay import ForexCandles
from parity_deriva.etc import settings
from parity_deriva.event.event import StatusEvent
from parity_deriva.lib.utils import granularityToTimedelta
from parity_deriva.trading.handler import StreamHandler

#: How many fine bars a run may be replayed over. Every one of them is
#: dispatched to every handler on the bus, so this is a ceiling on the time a
#: request takes rather than on anything about the market. A window too long
#: for one series falls back to the next coarser one, which is the point of
#: choosing rather than insisting.
#:
#: A million covers eleven years of M5 on one instrument, which is what the
#: whole of a EUR_USD store is, and that is deliberate: a report over the
#: whole history should be filled on the finest bars there are rather than
#: quietly dropped onto M30 because the ceiling was set for a page that had
#: no way of saying how far along it was. It now has one - see ledger.Progress
#: and /api/progress - so a long run is slow and visible instead of silent.
MAX_BARS = 1000000


def held(instrument, setup=None):
	"""
	granularity -> the timestamps the store holds for it, or can build from
	a finer series it holds (see data/store.py).
	"""
	cfg = setup if setup is not None else settings
	path = os.path.join(cfg.DATA_DIR, "%s.hd5" % instrument)
	if not os.path.exists(path):
		return {}
	return store.indexes(path)


def finer(instrument, granularity, dtfrom, dtto, setup=None,
		  max_bars=MAX_BARS):
	"""
	The finest series this run's orders can be filled against, or None.

	None is the ordinary answer and not a failure: it means the store holds
	nothing finer that covers the window, and the run fills against its own
	bars the way it always has.
	"""
	series = held(instrument, setup)
	coarse = series.get(granularity)
	if coarse is None:
		return None
	window = coarse[(coarse >= dtfrom) & (coarse <= dtto)]
	if not len(window):
		return None
	first, last = window.min(), window.max()

	span = granularityToTimedelta(granularity)
	best = None
	for name, index in series.items():
		step = granularityToTimedelta(name)
		if step is None or step >= span:
			continue
		# inside the first coarse bar, not at its open: a bar built from a
		# finer series is stamped at the floor of that series' first minute,
		# so the series itself never starts before it
		if index.min() >= first + span or index.max() < last:
			continue
		if len(index[(index >= first) & (index <= last)]) > max_bars:
			continue
		if best is None or step < best[0]:
			best = (step, name)
	return best[1] if best else None


def ticks(instrument, granularity, fine, dtfrom, dtto, setup=None):
	"""
	How many bars a run will actually replay: its own, plus the fine ones its
	orders rest on.

	This is the size of the job and not the size of the chart. A year of H4 is
	1 616 candles and 75 000 M5 bars under them, and it is the second number
	that decides whether the answer comes back in ten seconds or ten minutes.
	"""
	series = held(instrument, setup)
	def inside(name):
		index = series.get(name)
		if index is None:
			return 0
		return int(len(index[(index >= dtfrom) & (index <= dtto)]))
	own = inside(granularity)
	return own, own + (inside(fine) if fine else 0)


class Shadowed(StreamHandler):
	"""
	Two granularities of one instrument, in the order the bars closed.

	It is a source and not a handler: the driver drains it once and replays
	what comes out, so the merge happens before anything is dispatched and no
	handler ever has to buffer.
	"""

	def __init__(self, *sources):
		self.sources = sources

	def stream_to_queue(self):
		events = []
		for source in self.sources:
			bag = Collector()
			source.set_queue(bag)
			source.stream_to_queue()
			span = granularityToTimedelta(source.granularity)
			for event in bag.events:
				if str(event) == 'CANDLE':
					events.append((event.time + span, span, event))
		# (closed, span): a coarse bar is dispatched after every finer bar it
		# contains, including the one that closes with it
		events.sort(key=lambda row: (row[0], row[1]))
		self.queue_event(StatusEvent('STARTED'))
		for _, _, event in events:
			self.queue_event(event)
		self.queue_event(StatusEvent('DONE'))


def source(instrument, granularity, fine, dtfrom, dtto, setup=None,
		   progress=None):
	"""
	The candle source for a run, shadowed or not.

	`progress` is passed to both streams and called while they are read, with
	the granularity in the label: the fine one is where the time goes - a
	million M5 bars against eighteen thousand H4 ones - so a reader watching
	the line can see which of the two is holding things up.
	"""
	cfg = setup if setup is not None else settings
	coarse = ForexCandles(setup=cfg, pairs=[instrument],
						  granularity=granularity, dtfrom=dtfrom, dtto=dtto,
						  progress=progress)
	if not fine:
		return coarse
	# "EUR_USD M5 (fills): reading the file": which of the two is loading
	fills = None if progress is None else (
		lambda stage, done, total: progress(stage.replace(': ', ' (fills): ', 1), done, total))
	return Shadowed(coarse, ForexCandles(setup=cfg, pairs=[instrument],
										 granularity=fine, dtfrom=dtfrom,
										 dtto=dtto, progress=fills))
