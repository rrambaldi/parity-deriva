"""
6. BRT - Breakout Retest (6-STRATEGIE-M15.md, section 6).
"""

from parity_deriva.lib.streaming import Series, Swings
from parity_deriva.strategy.H4 import pinBar
from parity_deriva.strategy.M15 import EARLY_EXIT, M15, Reading


class M1506(M15):
	#: the name the page and the ledger use; see H4.TAG
	TAG = 'M1506-BRT'

	#: See AG01.DESCRIPTION.
	DESCRIPTION = (
		"Breakout retest (6-STRATEGIE-M15.md, section 6). LEVELS: the "
		"confirmed H1 swings - 3 hours either side - of the last 10 days, the "
		"H1 bars built from the M15 ones as they close. BREAKOUT long: an M15 "
		"close above a level the previous close was not above. RETEST: within "
		"20 bars, a bar whose low comes back to within 0.5 ATR(14) of the "
		"level, closes above it, bullish, with a lower tail at least as long "
		"as its body and longer than the upper one. A close more than 0.5 ATR "
		"back under the level cancels the breakout, and so does no retest in "
		"20 bars: the price is not chased. Short is the mirror under a level. "
		"ENTRY: a buy stop at the retest bar's high, for one bar. STOP: 0.2 "
		"ATR under the lower of the retest's low and the level. TARGET: the "
		"nearer of the next level (0.5 ATR short of it) and 2R. EARLY EXIT: an "
		"M15 close back under the level. TIME STOP: out after 8 bars if the "
		"trade never went 0.5R its way. MONEY: one trade per breakout, no new "
		"trade once the day has lost 3R. Break even is not automatic: it is "
		"the page's trailing stop, off unless asked for.")

	#: hours either side of an H1 swing, and hours of levels kept
	SWING_BARS = 3
	KEEP_HOURS = 240
	#: how near the retest must come, and how far back a close must go to
	#: cancel the breakout, in M15 ATR
	ZONE_ATR = 0.5
	#: bars a breakout waits for its retest: "se non avviene il retest, non
	#: inseguire il prezzo"
	RETEST_BARS = 20
	#: "una candela con ombra inferiore": the tail against the body
	WICK = 1.0
	REWARD = 2.0
	#: "mancato follow-through entro un numero prefissato di candele"
	TIME_STOP_BARS = 8
	MAX_ATTEMPTS = 1
	STOP_ENTRY = True

	SETUP_BARS = 2

	#: see M15.PARAM_HELP
	PARAM_HELP = {
		'swingBars': 'hours either side that confirm an H1 swing',
		'keepHours': 'hours of swing levels kept',
		'zoneAtr': 'distance from the level, in ATR',
		'retestBars': 'bars a breakout waits for its retest',
		'wick': 'pin bar tail as a multiple of its body',
		'reward': 'target distance, in R',
	}

	def setup(self, args):
		M15.setup(self, args)
		self._set(args, 'swingBars', self.SWING_BARS)
		self._set(args, 'keepHours', self.KEEP_HOURS)
		self._set(args, 'zoneAtr', self.ZONE_ATR)
		self._set(args, 'retestBars', self.RETEST_BARS)
		self._set(args, 'wick', self.WICK)
		self._set(args, 'reward', self.REWARD)

	def series(self):
		state = Reading(Series(atr=self.atrPeriod, keep=3),
						levels=Swings(bars=self.swingBars, keep=self.keepHours))
		#: side -> the breakout waiting for its retest
		state.broken = {}
		return state

	def feed(self, state, candle):
		M15.feed(self, state, candle)
		atr, window = state.atr(), state.m15.window(2)
		if not atr or window is None:
			return
		zone = self.zoneAtr * atr
		close, before = candle.mid['c'], window[0].mid['c']
		seen = state.m15.seen
		for side in (1, -1):
			pending = state.broken.get(side)
			if pending is not None and (
					seen - pending['at'] > self.retestBars
					or side * (close - pending['level']) < -zone):
				del state.broken[side]
			crossed = [l['price'] for l in state.levels.levels()
					   if side * (before - l['price']) <= 0
					   < side * (close - l['price'])]
			if crossed:
				level = max(crossed) if side > 0 else min(crossed)
				state.broken[side] = {'level': level, 'at': seen,
									  'key': (seen, side)}

	def signal(self, state, candle):
		atr = state.atr()
		if not atr:
			return None
		zone = self.zoneAtr * atr
		close = candle.mid['c']
		for side in (1, -1):
			pending = state.broken.get(side)
			if pending is None or state.m15.seen <= pending['at']:
				continue
			level = pending['level']
			reach = candle.mid['l'] if side > 0 else candle.mid['h']
			if side * (reach - level) > zone or side * (close - level) <= 0:
				continue
			if side * (close - candle.mid['o']) <= 0 \
					or not pinBar(candle, side, self.wick):
				continue

			extreme = min(reach, level) if side > 0 else max(reach, level)
			stop = extreme - side * self.bufferAtr * atr
			entry = self.entry(candle, side)
			ahead = [l['price'] - side * zone for l in state.levels.levels()
					 if side * (l['price'] - entry) > zone]
			nearest = min(ahead, key=lambda p: abs(p - entry)) if ahead \
				else None
			return {'side': side, 'stop': stop,
					'target': self.nearer(candle, side, stop, nearest,
										  self.reward),
					'key': pending['key'], 'level': level}
		return None

	def exit(self, state, candle, held):
		if held['side'] * (candle.mid['c'] - held['level']) < 0:
			return EARLY_EXIT
		return None
