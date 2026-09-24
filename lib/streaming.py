"""
Indicators a strategy can read one candle at a time, and the swings under them.

lib/indicators.py computes a whole series at once, for the chart. A strategy
sees one bar and then the next, and must never see further: this is the same
arithmetic with that shape. The two agree by construction where it matters -
the EMA is seeded with the simple mean of its first `period` closes and the
ATR with the mean of its first `period` true ranges, which is what
lib/indicators does - so the line drawn over a backtest is the line the rule
read, and not a second opinion about it.

Three kinds live here, and the difference is not cosmetic:

* **running** - EMA, Wilder's ATR, Wilder's RSI. Each is a function of every
  bar before it, so it is carried forward rather than recomputed: the value is
  exact at every bar and costs nothing per bar.
* **windowed** - SMA, standard deviation, Bollinger bands, the average volume.
  These are a function of the last N bars only, so they are read off the window
  when asked for.
* **confirmed** - swing highs and lows, which are a function of the bars either
  side and so are not known until `bars` more have printed. That delay is the
  point: a swing is a fact about the past that the present has just learned.

Nothing here reads a bar that has not been added. That is the whole contract.
"""


class Series(object):
	"""
	The bars seen so far, with whatever a strategy declared it reads.

	The periods are declared up front rather than passed at every call,
	because a running value has to have been running: an EMA asked for the
	first time at bar 900 is not the EMA of the run, it is the EMA of
	whatever happened to be in the window.
	"""

	def __init__(self, ema=(), sma=(), stdev=(), atr=None, rsi=None,
				 volume=None, keep=600):
		self.emaPeriods = tuple(ema)
		self.smaPeriods = tuple(sma)
		self.stdevPeriods = tuple(stdev)
		self.atrPeriod = atr
		self.rsiPeriod = rsi
		self.volumePeriod = volume
		#: enough window for every windowed reading asked for, and for the
		#: seeds of the running ones
		wanted = list(self.smaPeriods) + list(self.stdevPeriods) \
			+ [self.volumePeriod or 0, self.atrPeriod or 0, self.rsiPeriod or 0]
		self.keep = max(keep, max(wanted) + 1 if wanted else 0)

		self.bars = []
		self.seen = 0
		self._ema = dict((p, None) for p in self.emaPeriods)
		self._emaSeed = dict((p, []) for p in self.emaPeriods)
		self._atr = None
		self._ranges = []
		self._gain = None
		self._loss = None
		self._moves = []

	# ------------------------------------------------------------- feeding

	def add(self, candle):
		"""One more closed bar. Everything is advanced here and nowhere else."""
		previous = self.bars[-1] if self.bars else None
		self.bars.append(candle)
		if len(self.bars) > self.keep:
			self.bars.pop(0)
		self.seen += 1
		close = candle.mid['c']
		for period in self.emaPeriods:
			self._advanceEma(period, close)
		if self.atrPeriod:
			self._advanceAtr(candle, previous)
		if self.rsiPeriod:
			self._advanceRsi(close, previous)

	def _advanceEma(self, period, close):
		if self._ema[period] is None:
			seed = self._emaSeed[period]
			seed.append(close)
			if len(seed) < period:
				return
			self._ema[period] = sum(seed) / float(period)
			return
		k = 2.0 / (period + 1)
		self._ema[period] = close * k + self._ema[period] * (1 - k)

	def _advanceAtr(self, candle, previous):
		high, low = candle.mid['h'], candle.mid['l']
		if previous is None:
			# no previous close to reach for, which is Wilder's own handling
			true = high - low
		else:
			before = previous.mid['c']
			true = max(high - low, abs(high - before), abs(low - before))
		if self._atr is None:
			self._ranges.append(true)
			if len(self._ranges) < self.atrPeriod:
				return
			self._atr = sum(self._ranges) / float(self.atrPeriod)
			return
		self._atr += (true - self._atr) / self.atrPeriod

	def _advanceRsi(self, close, previous):
		if previous is None:
			return
		move = close - previous.mid['c']
		gain, loss = max(move, 0.0), max(-move, 0.0)
		if self._gain is None:
			self._moves.append((gain, loss))
			if len(self._moves) < self.rsiPeriod:
				return
			self._gain = sum(g for g, _ in self._moves) / float(self.rsiPeriod)
			self._loss = sum(l for _, l in self._moves) / float(self.rsiPeriod)
			return
		self._gain += (gain - self._gain) / self.rsiPeriod
		self._loss += (loss - self._loss) / self.rsiPeriod

	# ------------------------------------------------------------- reading

	def ema(self, period):
		"""The EMA, or None while it is still being seeded."""
		return self._ema[period]

	def atr(self):
		return self._atr

	def rsi(self):
		"""
		Wilder's RSI, or None while it is warming.

		100 when nothing has fallen in the whole window: the ratio has no
		denominator there, and every charting package draws the same.
		"""
		if self._gain is None:
			return None
		if not self._loss:
			return 100.0
		rs = self._gain / self._loss
		return 100.0 - 100.0 / (1.0 + rs)

	def sma(self, period):
		if len(self.bars) < period:
			return None
		window = self.bars[-period:]
		return sum(c.mid['c'] for c in window) / float(period)

	def stdev(self, period):
		"""
		Population deviation over the window, which is what a Bollinger band
		is drawn with - see lib/indicators.stdev for why population and not
		sample.
		"""
		mean = self.sma(period)
		if mean is None:
			return None
		window = self.bars[-period:]
		return (sum((c.mid['c'] - mean) ** 2 for c in window) / period) ** 0.5

	def bands(self, period, deviations):
		"""(lower, middle, upper), or None while the window is short."""
		middle, spread = self.sma(period), self.stdev(period)
		if middle is None or spread is None:
			return None
		return (middle - deviations * spread, middle,
				middle + deviations * spread)

	def averageVolume(self, period=None):
		period = period or self.volumePeriod
		if not period or len(self.bars) < period:
			return None
		window = self.bars[-period:]
		return sum(float(getattr(c, 'volume', 0) or 0) for c in window) / period

	def window(self, count):
		"""The last `count` bars, or None if there are not that many."""
		if count > len(self.bars):
			return None
		return self.bars[-count:]

	def high(self, count):
		window = self.window(count)
		return None if window is None else max(c.mid['h'] for c in window)

	def low(self, count):
		window = self.window(count)
		return None if window is None else min(c.mid['l'] for c in window)


class Swings(object):
	"""
	The confirmed swing highs and lows of the bars seen so far.

	A swing high is a bar whose high no bar within `bars` either side reaches,
	and it is not one until `bars` more have printed after it - which is both
	the honest reading and the one the chart draws, from the bar that
	confirmed it rather than from the swing.

	Two swings within a band of each other are one level; the band is a share
	of the window's own range, which is the viewer's rule made causal (it uses
	a share of the whole run, which a strategy cannot know). A level is
	forgotten once the bars that made it have left the window: a swing from
	two years ago refusing a trade today is not a level, it is a memory.
	"""

	def __init__(self, bars=5, keep=400, near=0.01):
		self.bars = bars
		self.keep = keep
		self.near = near
		self.window = []
		self.found = []
		self.seen = 0

	def add(self, candle):
		self.seen += 1
		self.window.append(candle)
		if len(self.window) > self.keep:
			self.window.pop(0)
		self.confirm()
		self.found = [l for l in self.found if self.seen - l['at'] <= self.keep]

	def confirm(self):
		"""The bar `bars` back, now that `bars` have printed after it."""
		i = len(self.window) - self.bars - 1
		if i < self.bars:
			return
		bar = self.window[i]
		for field, kind in (('h', 'resistance'), ('l', 'support')):
			price = bar.mid[field]
			high = kind == 'resistance'
			swing = True
			for j in range(i - self.bars, i + self.bars + 1):
				if j == i:
					continue
				other = self.window[j].mid[field]
				if (other >= price) if high else (other <= price):
					swing = False
					break
			if swing:
				self.remember(price, kind, bar)

	def remember(self, price, kind, bar):
		width = self.width()
		for level in self.found:
			if level['kind'] == kind and abs(level['price'] - price) <= width:
				level['at'] = self.seen
				level['time'] = bar.time
				return
		self.found.append({'price': price, 'kind': kind, 'at': self.seen,
						   'time': bar.time})

	def width(self):
		"""Half the band, in price: a share of the window's own range."""
		if not self.window:
			return 0.0
		high = max(c.mid['h'] for c in self.window)
		low = min(c.mid['l'] for c in self.window)
		return (high - low) * self.near

	def levels(self):
		"""Every live level, with the band it is wide today."""
		width = self.width()
		return [dict(level, near=width) for level in self.found]

	def last(self, kind):
		"""The most recently confirmed level of one kind, or None."""
		found = [l for l in self.found if l['kind'] == kind]
		return max(found, key=lambda l: l['at']) if found else None


class Daily(object):
	"""
	Daily bars built from the intraday ones as they arrive.

	Strategy 4 of the document reads daily levels while trading 4H. Rather
	than a second stream on the bus, the day is aggregated from the bars the
	strategy is already being fed: the daily bar for a date is complete as
	soon as a bar of the next date arrives, and until then nothing reads it.
	That makes the daily series exactly as causal as the intraday one it is
	made of, which a second stream would have to be argued about.

	`bar` is the day that has closed, and `add` returns it at the moment it
	closes so a caller can advance whatever it keeps per day.
	"""

	class Bar(object):
		__slots__ = ('time', 'mid', 'volume')

		def __init__(self, when, mid, volume):
			self.time = when
			self.mid = mid
			self.volume = volume

	def __init__(self):
		self.day = None
		self.open = None
		self.bars = []

	def add(self, candle):
		"""Fold one intraday bar in. Returns the day it completed, or None."""
		date = candle.time.date()
		done = None
		if self.day is not None and date != self.day:
			done = self.close()
		if self.day != date:
			self.day = date
			self.open = {'o': candle.mid['o'], 'h': candle.mid['h'],
						 'l': candle.mid['l'], 'c': candle.mid['c'],
						 'time': candle.time, 'volume': 0.0}
		self.open['h'] = max(self.open['h'], candle.mid['h'])
		self.open['l'] = min(self.open['l'], candle.mid['l'])
		self.open['c'] = candle.mid['c']
		self.open['volume'] += float(getattr(candle, 'volume', 0) or 0)
		return done

	def close(self):
		"""Seal the day being built and keep it."""
		if self.open is None:
			return None
		bar = self.Bar(self.open['time'],
					   {'o': self.open['o'], 'h': self.open['h'],
						'l': self.open['l'], 'c': self.open['c']},
					   self.open['volume'])
		self.bars.append(bar)
		return bar
