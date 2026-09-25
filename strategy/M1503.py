"""
3. S/RP - Support and Resistance Price Action (6-STRATEGIE-M15.md, section 3).
"""

from parity_deriva.lib.streaming import Series, Swings
from parity_deriva.strategy.H4 import engulfing, pinBar
from parity_deriva.strategy.M15 import EARLY_EXIT, M15, Reading


class M1503(M15):
	#: the name the page and the ledger use; see H4.TAG
	TAG = 'M1503-SRP'

	#: See AG01.DESCRIPTION.
	DESCRIPTION = (
		"Support and resistance price action (6-STRATEGIE-M15.md, section 3). "
		"LEVELS: the confirmed H1 swings - 3 hours either side - of the last "
		"10 days, the H1 bars built from the M15 ones as they close. A swing "
		"high is a level as much as a swing low, since a broken resistance is "
		"a support. Each level is a zone, 0.5 ATR(14) either side. SIGNAL "
		"long: the previous bar closed above a level, this bar's low reached "
		"its zone and the bar closed back above the zone's floor, as a false "
		"break (low under the zone, close over the level), a pin bar (lower "
		"tail twice the body) or a bullish engulfing. Short is the mirror on "
		"a level above. ENTRY: a buy stop at the bar's high, for one bar. "
		"STOP: 0.2 ATR under the lower of the bar's low and the zone's floor. "
		"TARGET: the nearer of the next level's zone and 2R; no trade if that "
		"is less than 1.5R away. EARLY EXIT: a close under the zone. MONEY: "
		"no new trade once the day has lost 3R. The breakout-and-retest entry "
		"of this section is strategy 6 (M1506-BRT) and is not repeated here; "
		"the smaller risk on a fresh level is not implemented, since the risk "
		"is the page's.")

	#: hours either side of an H1 swing, and hours of levels kept
	SWING_BARS = 3
	KEEP_HOURS = 240
	#: half the zone, in M15 ATR: "tracciare zone, non linee"
	ZONE_ATR = 0.5
	#: the pin bar's tail, as a multiple of its body
	WICK = 2.0
	REWARD = 2.0
	#: "scartare il trade se lo spazio disponibile non consente almeno 1,5R"
	MIN_REWARD = 1.5
	STOP_ENTRY = True

	SETUP_BARS = 2

	#: see M15.PARAM_HELP
	PARAM_HELP = {
		'swingBars': 'hours either side that confirm an H1 swing',
		'keepHours': 'hours of swing levels kept',
		'zoneAtr': 'half the zone around a level, in ATR',
		'wick': 'pin bar tail as a multiple of its body',
		'reward': 'target distance, in R',
	}

	def setup(self, args):
		M15.setup(self, args)
		self._set(args, 'swingBars', self.SWING_BARS)
		self._set(args, 'keepHours', self.KEEP_HOURS)
		self._set(args, 'zoneAtr', self.ZONE_ATR)
		self._set(args, 'wick', self.WICK)
		self._set(args, 'reward', self.REWARD)

	def series(self):
		return Reading(Series(atr=self.atrPeriod, keep=3),
					   levels=Swings(bars=self.swingBars, keep=self.keepHours))

	def signal(self, state, candle):
		atr = state.atr()
		window = state.m15.window(2)
		if not atr or window is None:
			return None
		levels = state.levels.levels()
		if not levels:
			return None
		previous, close = window[0], candle.mid['c']
		zone = self.zoneAtr * atr

		for side in (1, -1):
			reach = candle.mid['l'] if side > 0 else candle.mid['h']
			# came from the right side, reached the zone, closed back on it
			touched = [l for l in levels
					   if side * (previous.mid['c'] - l['price']) > 0
					   and side * (reach - l['price']) <= zone
					   and side * (close - l['price']) >= -zone]
			if not touched:
				continue
			level = min(touched, key=lambda l: abs(reach - l['price']))
			price = level['price']
			broke = side * (reach - price) < -zone and side * (close - price) > 0
			if not (broke or pinBar(candle, side, self.wick)
					or engulfing(candle, previous, side)):
				continue

			floor = price - side * zone
			extreme = min(reach, floor) if side > 0 else max(reach, floor)
			stop = extreme - side * self.bufferAtr * atr
			entry = self.entry(candle, side)
			ahead = [l['price'] - side * zone for l in levels
					 if l is not level and side * (l['price'] - entry) > zone]
			nearest = min(ahead, key=lambda p: abs(p - entry)) if ahead \
				else None
			return {'side': side, 'stop': stop,
					'target': self.nearer(candle, side, stop, nearest,
										  self.reward),
					'level': price, 'zone': zone}
		return None

	def exit(self, state, candle, held):
		if held['side'] * (candle.mid['c'] - held['level']) < -held['zone']:
			return EARLY_EXIT
		return None
