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
* **windowed** - SMA (now or some bars ago), standard deviation, Bollinger
  bands and their width, the rate of change, the Donchian channel, the
  distance from the highest high and the lowest low, the average volume. These are a function of the last N bars only, so they are read off
  the window when asked for.
* **confirmed** - swing highs and lows, which are a function of the bars either
  side and so are not known until `bars` more have printed. That delay is the
  point: a swing is a fact about the past that the present has just learned.

Values holds a series a strategy derives itself - a return, a band's width -
and ranks a new value against it; Cross says the bar a value crosses a level;
Daily, Hourly and Weekly build longer bars out of the ones a strategy is fed.

Nothing here reads a bar that has not been added. That is the whole contract.
"""

import datetime

from parity_deriva.lib import indicators


class Series(object):
	"""
	The bars seen so far, with whatever a strategy declared it reads.

	The periods are declared up front rather than passed at every call,
	because a running value has to have been running: an EMA asked for the
	first time at bar 900 is not the EMA of the run, it is the EMA of
	whatever happened to be in the window.
	"""

	def __init__(self, ema=(), sma=(), stdev=(), atr=None, rsi=None,
				 volume=None, keep=600, adx=None):
		self.emaPeriods = tuple(ema)
		self.smaPeriods = tuple(sma)
		self.stdevPeriods = tuple(stdev)
		self.atrPeriod = atr
		self.rsiPeriod = rsi
		self.adxPeriod = adx
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
		# Wilder's directional movement: the true range and the +DM and -DM
		# summed over the period, their seeds, the DX values that seed the
		# ADX, and the ADX
		self._dm = None
		self._dmSeed = []
		self._dxSeed = []
		self._dx = None
		self._di = None
		self._adx = None

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
		if self.adxPeriod:
			self._advanceAdx(candle, previous)

	def _advanceAdx(self, candle, previous):
		"""
		Wilder's ADX: +DM the high's rise over the high before and -DM the
		low's fall under the low before, the larger only and only if above
		zero; each and the true range summed over the period, then carried
		as sum - sum / period + new; +DI and -DI those sums over the true
		range's; DX their difference over their total; the ADX the mean of
		the first `period` DX values, then carried as Wilder's average.
		"""
		if previous is None:
			return
		period = self.adxPeriod
		high, low = candle.mid['h'], candle.mid['l']
		up, down = high - previous.mid['h'], previous.mid['l'] - low
		plus = up if up > down and up > 0 else 0.0
		minus = down if down > up and down > 0 else 0.0
		before = previous.mid['c']
		true = max(high - low, abs(high - before), abs(low - before))
		if self._dm is None:
			self._dmSeed.append((true, plus, minus))
			if len(self._dmSeed) < period:
				return
			self._dm = [sum(v[i] for v in self._dmSeed) for i in range(3)]
		else:
			self._dm = [s - s / period + v for s, v in zip(self._dm, (true, plus, minus))]
		sumTrue, sumPlus, sumMinus = self._dm
		plusDI = 100.0 * sumPlus / sumTrue if sumTrue else 0.0
		minusDI = 100.0 * sumMinus / sumTrue if sumTrue else 0.0
		self._di = (plusDI, minusDI)
		total = plusDI + minusDI
		self._dx = 100.0 * abs(plusDI - minusDI) / total if total else 0.0
		if self._adx is None:
			self._dxSeed.append(self._dx)
			if len(self._dxSeed) == period:
				self._adx = sum(self._dxSeed) / float(period)
			return
		self._adx = (self._adx * (period - 1) + self._dx) / period

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

	def adx(self):
		"""
		Wilder's ADX, how strong the trend is whichever way it goes: under 20
		a range, over 25 a trend. None for twice its period while it warms.
		"""
		return self._adx

	def di(self):
		"""(+DI, -DI), the push up and the push down, or None while warming."""
		return self._di if self._dm is not None else None

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

	def sma(self, period, back=0):
		"""
		The SMA, or the one `back` bars ago - sma(30) against sma(30, back=4)
		is which way the average is going. None while the window is short.
		"""
		window = self.window(period + back)
		if window is None:
			return None
		return sum(c.mid['c'] for c in window[:period]) / float(period)

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

	def bandwidth(self, period, deviations):
		"""
		The bands' width as a share of the middle, (upper - lower) / middle:
		what a squeeze is read on, comparable across years and instruments
		where a width in price is not. None while the window is short.
		"""
		bands = self.bands(period, deviations)
		if bands is None or not bands[1]:
			return None
		return (bands[2] - bands[0]) / bands[1]

	def change(self, bars):
		"""
		The rate of change over `bars` bars: this close over the close `bars`
		before it, less one, so 0.05 is five percent up. None while there are
		not `bars` + 1 bars.
		"""
		window = self.window(bars + 1)
		return None if window is None else window[-1].mid['c'] / window[0].mid['c'] - 1

	def averageVolume(self, period=None, skip=0):
		"""
		The mean volume of the last `period` bars, or of the `period` before
		the last `skip`: a bar's volume against averageVolume(20, skip=1) is
		against the bars before it, not a mean it is part of.
		"""
		period = period or self.volumePeriod
		window = self.window(period + skip) if period else None
		if window is None:
			return None
		return sum(float(getattr(c, 'volume', 0) or 0) for c in window[:period]) / period

	def window(self, count):
		"""The last `count` bars, or None if there are not that many."""
		if count > len(self.bars):
			return None
		return self.bars[-count:]

	def high(self, count, skip=0):
		"""
		The highest high of the last `count` bars, or of the `count` before
		the last `skip`: high(20, skip=1) is the top of the Donchian channel
		this bar's close breaks, which the bar itself cannot be part of.
		"""
		window = self.window(count + skip)
		return None if window is None else max(c.mid['h'] for c in window[:count])

	def low(self, count, skip=0):
		"""The lowest low, the way high() reads the highest."""
		window = self.window(count + skip)
		return None if window is None else min(c.mid['l'] for c in window[:count])

	def offHigh(self, count):
		"""
		How far this close is from the highest high of the last `count` bars,
		as a share: -0.2 is twenty percent under the 52-week high, 0 is at it.
		None while there are not enough bars.
		"""
		high = self.high(count)
		return None if high is None else self.bars[-1].mid['c'] / high - 1

	def offLow(self, count):
		"""How far this close is over the lowest low, the way offHigh() reads."""
		low = self.low(count)
		return None if low is None else self.bars[-1].mid['c'] / low - 1

	def channel(self, count, skip=1):
		"""
		The Donchian channel, (low, high) of the `count` bars before the last
		`skip` - by default the channel the bar just closed is measured
		against. None while there are not enough bars.
		"""
		low, high = self.low(count, skip), self.high(count, skip)
		return None if low is None else (low, high)


class Cross(object):
	"""
	The bar a value crosses a level: a fast EMA less a slow one crossing 0,
	an RSI coming back over 30.

	add(value) returns 1 on the bar the value goes from at or under the level
	to over it, -1 from at or over it to under, and 0 otherwise - on the first
	value too, which has nothing to cross from. A None (an indicator still
	warming) is 0, and the value after it has nothing to cross from either.
	"""

	def __init__(self, level=0.0):
		self.level = level
		self.before = None

	def add(self, value):
		before, self.before = self.before, value
		if value is None or before is None:
			return 0
		if before <= self.level < value:
			return 1
		if before >= self.level > value:
			return -1
		return 0


class Values(object):
	"""
	The last `keep` values of something a strategy derives bar by bar - a
	return, a band's width - and where a new one stands among them.

	The questions are asked before the new value goes in: a week's momentum
	is ranked against the weeks before it, not against a window it is already
	part of. So the pattern is

		rank = past.rank(value) if past.full() else None
		past.add(value)
	"""

	def __init__(self, keep):
		self.keep = keep
		self.values = []

	def add(self, value):
		self.values.append(value)
		del self.values[:-self.keep]

	def full(self):
		"""Are there `keep` values yet: the ranking is over a whole window."""
		return len(self.values) >= self.keep

	def rank(self, value):
		"""
		The share of the values held that are below `value`, 0 to 1: its
		percentile rank. 0.8 is above four fifths of them. None when empty.
		"""
		if not self.values:
			return None
		return sum(1 for v in self.values if v < value) / float(len(self.values))

	def percentile(self, percent):
		"""The percentile of the values held, interpolated (lib/indicators)."""
		return indicators.percentile(self.values, percent)

	def median(self):
		return self.percentile(50)


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

	def __init__(self, keep=None):
		self.day = None
		self.open = None
		self.bars = []
		#: how many sealed bars to hold on to, None for all of them
		self.keep = keep

	def bucket(self, when):
		"""The bar an instant belongs to."""
		return when.date()

	def add(self, candle):
		"""Fold one intraday bar in. Returns the day it completed, or None."""
		date = self.bucket(candle.time)
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
		if self.keep is not None and len(self.bars) > self.keep:
			del self.bars[0]
		return bar


class Hourly(Daily):
	"""
	Hourly bars built from finer ones, the way Daily builds days.

	The M15 strategies of 6-STRATEGIE-M15.md read an H1 context. The hour is
	complete when a bar of the next hour arrives, so the context lags the
	close of the hour by one M15 bar: late, and never early.
	"""

	def __init__(self, keep=2):
		Daily.__init__(self, keep=keep)

	def bucket(self, when):
		return when.replace(minute=0, second=0, microsecond=0)


class Weekly(Daily):
	"""
	Weekly bars built from daily ones, each week sealed by its Friday.

	The daily bar a broker stamps 21:00 or 22:00 UTC - the New York close -
	is the next day's trading, so a bar belongs to the day it is `roll` hours
	later: Sunday 21:00 is Monday. A week is the ISO week of that trading day
	moved on by one day, so that a Sunday bar opens the week it trades in.

	The Friday bar seals its week at once: the week is read when the market
	shuts for the weekend, not when Monday's bar arrives. A week without a
	Friday (a holiday) is sealed by the next week's first bar, the way Daily
	seals a day.

	Fed daily bars. From intraday ones every Friday bar would seal a week of
	its own: fold them into days with Daily first.
	"""

	def __init__(self, keep=2, roll=3):
		Daily.__init__(self, keep=keep)
		self.roll = datetime.timedelta(hours=roll)

	def tradingDay(self, when):
		return (when + self.roll).date()

	def bucket(self, when):
		return (self.tradingDay(when) + datetime.timedelta(days=1)).isocalendar()[:2]

	def add(self, candle):
		"""
		Fold one daily bar in. Returns the weeks it completed, oldest first:
		none, one, or two when a week without a Friday is followed by a
		Friday alone.
		"""
		done = [Daily.add(self, candle)]
		if self.tradingDay(candle.time).weekday() == 4:
			done.append(self.close())
			self.open = self.day = None
		return [bar for bar in done if bar is not None]
