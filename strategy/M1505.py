"""
5. BBO - Bollinger Breakout (6-STRATEGIE-M15.md, section 5).
"""

from parity_deriva.lib.indicators import percentile
from parity_deriva.lib.streaming import Series
from parity_deriva.strategy.H4 import body
from parity_deriva.strategy.M15 import EARLY_EXIT, M15, Reading


class M1505(M15):
	#: the name the page and the ledger use; see H4.TAG
	TAG = 'M1505-BBO'

	#: See AG01.DESCRIPTION.
	DESCRIPTION = (
		"Bollinger breakout (6-STRATEGIE-M15.md, section 5). COMPRESSION: the "
		"band width (20, 2), over the middle band, at or under the 20th "
		"percentile of its previous 100 bars, for at least 3 bars in a row; "
		"the congestion is the high and low of those bars, and it can be "
		"broken for 12 bars after it ends. SIGNAL long: the previous bar "
		"closed inside the congestion and this one closes above its high, "
		"bullish, with a body of at least 0.5 ATR(14) and no wider than 2 "
		"ATR. Of the document's two triggers - the band or the congestion's "
		"high - the congestion is used, because the stop and the early exit "
		"are both measured against it. Short is the mirror under the low. "
		"The volume confirmation (at least three quarters of the 20-bar "
		"average) is off unless useVolume is set. ENTRY: at market on the "
		"next open. STOP: the middle of the congestion. TARGET: 2R. EARLY "
		"EXIT: a close back inside the congestion. TIME STOP: out after 6 "
		"bars if the trade never went 0.5R its way. MONEY: two trades per "
		"compression - the breakout and one re-entry after a false one - and "
		"no new trade once the day has lost 3R. Trailing after 1R is the "
		"page's trailing stop.")

	PERIOD = 20
	DEVIATIONS = 2.0
	INDICATORS = ({'kind': 'bollinger', 'period': PERIOD,
				   'deviations': DEVIATIONS},)
	#: "bandwidth sotto il percentile 20 degli ultimi 100 periodi"
	PERCENTILE = 20.0
	LOOKBACK = 100
	#: "compresse per almeno 3-5 candele": the near end
	SQUEEZE_BARS = 3
	#: bars after the compression a breakout of it still counts
	BOX_BARS = 12
	#: "il corpo della candela supera un filtro minimo, per esempio 0,5 ATR"
	BODY_ATR = 0.5
	VOLUME_BARS = 20
	VOLUME_SHARE = 0.75
	REWARD = 2.0
	#: "uscire se il breakout non produce follow-through entro 4-6 candele"
	TIME_STOP_BARS = 6
	#: "un solo nuovo ingresso dopo un falso breakout"
	MAX_ATTEMPTS = 2
	#: "non entrare dopo una candela già estesa oltre 1,5-2 ATR"
	MAX_BAR_ATR = 2.0

	SETUP_BARS = SQUEEZE_BARS + 1

	#: see M15.PARAM_HELP
	PARAM_HELP = {
		'period': 'period of the Bollinger bands',
		'deviations': 'band width, in standard deviations',
		'squeezePercentile': 'percentile of band width counted as a squeeze',
		'lookback': 'bars the percentile is read from',
		'squeezeBars': 'bars in a row to count as a squeeze',
		'boxBars': 'bars after the squeeze a breakout counts',
		'bodyAtr': 'minimum body of the breakout bar, in ATR',
		'volumeBars': 'bars the average volume is taken over',
		'volumeShare': 'minimum volume, as a share of its average',
		'reward': 'target distance, in R',
	}

	def setup(self, args):
		M15.setup(self, args)
		self._set(args, 'period', self.PERIOD)
		self._set(args, 'deviations', self.DEVIATIONS)
		self._set(args, 'squeezePercentile', self.PERCENTILE)
		self._set(args, 'lookback', self.LOOKBACK)
		self._set(args, 'squeezeBars', self.SQUEEZE_BARS)
		self._set(args, 'boxBars', self.BOX_BARS)
		self._set(args, 'bodyAtr', self.BODY_ATR)
		self._set(args, 'volumeBars', self.VOLUME_BARS)
		self._set(args, 'volumeShare', self.VOLUME_SHARE)
		self._set(args, 'reward', self.REWARD)
		self.useVolume = False
		self._set(args, 'useVolume')

	def series(self):
		state = Reading(Series(sma=(self.period,), stdev=(self.period,),
							   atr=self.atrPeriod, volume=self.volumeBars,
							   keep=max(self.period, self.volumeBars) + 2))
		#: the widths the percentile is read from; the compression being
		#: built (bars, high, low, first bar); the last congestion, and the
		#: one as it stood before this bar
		state.widths = []
		state.run, state.high, state.low, state.first = 0, None, None, None
		state.box = state.before = None
		return state

	def feed(self, state, candle):
		M15.feed(self, state, candle)
		state.before = state.box
		bands = state.m15.bands(self.period, self.deviations)
		if bands is None or not bands[1]:
			return
		lower, middle, upper = bands
		width = (upper - lower) / middle
		squeezed = len(state.widths) >= self.lookback \
			and width <= percentile(state.widths, self.squeezePercentile)
		state.widths.append(width)
		del state.widths[:-self.lookback]
		if not squeezed:
			state.run = 0
			return
		if not state.run:
			state.high, state.low = candle.mid['h'], candle.mid['l']
			state.first = state.m15.seen
		state.high = max(state.high, candle.mid['h'])
		state.low = min(state.low, candle.mid['l'])
		state.run += 1
		if state.run >= self.squeezeBars:
			state.box = {'high': state.high, 'low': state.low,
						 'episode': state.first, 'last': state.m15.seen}

	def signal(self, state, candle):
		box, atr = state.before, state.atr()
		window = state.m15.window(2)
		if box is None or not atr or window is None:
			return None
		if state.m15.seen - box['last'] > self.boxBars:
			return None
		if body(candle) < self.bodyAtr * atr:
			return None
		if self.useVolume:
			average = state.m15.averageVolume()
			volume = float(getattr(candle, 'volume', 0) or 0)
			if average is None or volume < self.volumeShare * average:
				return None
		previous, close = window[0], candle.mid['c']

		for side, edge in ((1, box['high']), (-1, box['low'])):
			if side * (previous.mid['c'] - edge) > 0:
				# already out: this bar is not the one that broke it
				continue
			if side * (close - edge) <= 0 or side * (close - candle.mid['o']) <= 0:
				continue
			stop = (box['high'] + box['low']) / 2.0
			return {'side': side, 'stop': stop,
					'target': self.levels(candle, side, stop, self.reward),
					'key': box['episode'], 'edge': edge}
		return None

	def exit(self, state, candle, held):
		if held['side'] * (candle.mid['c'] - held['edge']) < 0:
			return EARLY_EXIT
		return None
