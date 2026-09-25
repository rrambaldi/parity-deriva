"""
4. BMR - Bollinger Mean Reversion (6-STRATEGIE-M15.md, section 4).
"""

from parity_deriva.lib.streaming import Series
from parity_deriva.strategy.M15 import EARLY_EXIT, M15, Reading


class M1504(M15):
	#: the name the page and the ledger use; see H4.TAG
	TAG = 'M1504-BMR'

	#: See AG01.DESCRIPTION.
	DESCRIPTION = (
		"Bollinger mean reversion (6-STRATEGIE-M15.md, section 4). CONTEXT, "
		"all of: the H1 EMA 50 is flat - over the last 10 hours it moved at "
		"most 0.06 H1 ATR(14) per hour, which is the flatter half of EUR_USD "
		"2016-2023, measured, since the document gives no number; the H1 "
		"highs and lows are not both rising or both falling (the last 12 "
		"hours against the 12 before); the M15 bands are not expanding "
		"violently - at most 1.9 times as wide as 5 bars ago, the 95th "
		"percentile of that ratio. SIGNAL long: this bar or the one before "
		"touched the lower band (20, 2) with RSI(14) under 30, and this bar "
		"closes back inside the bands. Short is the mirror on the upper band "
		"with RSI over 70. ENTRY: a buy stop at the re-entry bar's high, for "
		"one bar. STOP: 0.2 ATR under the false break's low. TARGET: the "
		"middle band. EARLY EXIT: a new close outside the band against the "
		"trade, or the bands expanding past 1.9 times. MONEY: one trade per "
		"band until price crosses the middle band again; no new trade once "
		"the day has lost 3R; the ATR ceiling is the volatility filter "
		"(atrMaxPips). Not implemented: the RSI divergence and the extended "
		"target at the opposite band, which the document makes optional.")

	PERIOD = 20
	DEVIATIONS = 2.0
	INDICATORS = ({'kind': 'bollinger', 'period': PERIOD,
				   'deviations': DEVIATIONS},)
	RSI_PERIOD = 14
	RSI_LOW = 30.0
	RSI_HIGH = 70.0
	#: bars back the touch may be from the re-entry
	TOUCH_BARS = 2
	#: the H1 average and its flatness, measured: see the description
	H1_SLOW = 50
	FLAT_BARS = 10
	FLAT_SLOPE = 0.06
	STRUCTURE_BARS = 12
	#: band width against its width this many bars ago
	EXPANSION = 1.9
	EXPANSION_BARS = 5
	#: "massimo un tentativo per banda e per zona"
	MAX_ATTEMPTS = 1
	STOP_ENTRY = True

	SETUP_BARS = TOUCH_BARS

	def setup(self, args):
		M15.setup(self, args)
		self._set(args, 'period', self.PERIOD)
		self._set(args, 'deviations', self.DEVIATIONS)
		self._set(args, 'rsiPeriod', self.RSI_PERIOD)
		self._set(args, 'rsiLow', self.RSI_LOW)
		self._set(args, 'rsiHigh', self.RSI_HIGH)
		self._set(args, 'touchBars', self.TOUCH_BARS)
		self._set(args, 'h1Slow', self.H1_SLOW)
		self._set(args, 'flatBars', self.FLAT_BARS)
		self._set(args, 'flatSlope', self.FLAT_SLOPE)
		self._set(args, 'structureBars', self.STRUCTURE_BARS)
		self._set(args, 'expansion', self.EXPANSION)
		self._set(args, 'expansionBars', self.EXPANSION_BARS)

	def series(self):
		state = Reading(Series(sma=(self.period,), stdev=(self.period,),
							   atr=self.atrPeriod, rsi=self.rsiPeriod,
							   keep=self.period + 2),
						h1=Series(ema=(self.h1Slow,), atr=self.atrPeriod,
								  keep=2 * self.structureBars + 1))
		#: per recent bar (touched the lower band, the upper one, its RSI);
		#: the band widths; the H1 averages; the middle-band crossings, and
		#: which side of the middle the last close was on
		state.marks, state.widths, state.slows = [], [], []
		state.crossings, state.above = 0, None
		return state

	def feed(self, state, candle):
		M15.feed(self, state, candle)
		if state.hour is not None:
			state.slows.append(state.h1.ema(self.h1Slow))
			del state.slows[:-(self.flatBars + 1)]
		bands = state.m15.bands(self.period, self.deviations)
		if bands is None:
			return
		lower, middle, upper = bands
		state.marks.append({1: candle.mid['l'] <= lower,
							-1: candle.mid['h'] >= upper,
							'rsi': state.m15.rsi()})
		del state.marks[:-self.touchBars]
		state.widths.append(upper - lower)
		del state.widths[:-(self.expansionBars + 1)]
		above = candle.mid['c'] > middle
		if state.above is not None and above != state.above:
			state.crossings += 1
		state.above = above

	def expanding(self, state):
		if len(state.widths) <= self.expansionBars or not state.widths[0]:
			return False
		return state.widths[-1] > self.expansion * state.widths[0]

	def ranging(self, state):
		"""The document's context: a flat H1, no H1 trend, calm bands."""
		atr = state.h1.atr()
		if len(state.slows) <= self.flatBars or None in state.slows or not atr:
			return False
		slope = abs(state.slows[-1] - state.slows[0]) / (self.flatBars * atr)
		if slope > self.flatSlope:
			return False
		if state.structure(self.structureBars) != 0:
			return False
		return not self.expanding(state)

	def signal(self, state, candle):
		bands = state.m15.bands(self.period, self.deviations)
		atr = state.atr()
		if bands is None or not atr or len(state.marks) < self.touchBars:
			return None
		if not self.ranging(state):
			return None
		lower, middle, upper = bands
		close = candle.mid['c']
		rsis = [m['rsi'] for m in state.marks if m['rsi'] is not None]
		if not rsis:
			return None
		bars = state.m15.window(self.touchBars)

		for side, band, stretched in ((1, lower, min(rsis) < self.rsiLow),
									  (-1, upper, max(rsis) > self.rsiHigh)):
			if not stretched or not any(m[side] for m in state.marks):
				continue
			if side * (close - band) <= 0:
				# still outside: "non comprare una semplice candela che
				# continua a chiudere fuori dalla banda"
				continue
			extreme = min(c.mid['l'] for c in bars) if side > 0 \
				else max(c.mid['h'] for c in bars)
			return {'side': side,
					'stop': extreme - side * self.bufferAtr * atr,
					'target': middle, 'key': (side, state.crossings)}
		return None

	def exit(self, state, candle, held):
		bands = state.m15.bands(self.period, self.deviations)
		if bands is None:
			return None
		band = bands[0] if held['side'] > 0 else bands[2]
		if held['side'] * (candle.mid['c'] - band) < 0:
			return EARLY_EXIT
		if self.expanding(state):
			return EARLY_EXIT
		return None
