"""
1. MTP - Moving-average Trend Pullback (6-STRATEGIE-M15.md, section 1).
"""

from parity_deriva.lib.streaming import Series
from parity_deriva.strategy.H4 import engulfing
from parity_deriva.strategy.M15 import EARLY_EXIT, M15, Reading


class M1501(M15):
	#: the name the page and the ledger use; see H4.TAG
	TAG = 'M1501-MTP'

	#: See AG01.DESCRIPTION.
	DESCRIPTION = (
		"Moving-average trend pullback (6-STRATEGIE-M15.md, section 1). "
		"CONTEXT: on H1, EMA 20 above EMA 50, or higher highs and higher lows "
		"(the last 12 hours against the 12 before); on M15, EMA 20 above EMA "
		"50. SETUP long: one of the last 3 M15 bars reached down to the EMA 20 "
		"and none of them closed under the EMA 50, then a confirming bar "
		"closes above the previous bar's high or is a bullish engulfing, and "
		"is no wider than 2 ATR(14). Short is the mirror. ENTRY: at market on "
		"the next open. STOP: under the pullback's low by 0.2 ATR, and at "
		"least 1 ATR from the entry. TARGET: 2R. EARLY EXIT: an M15 close on "
		"the other side of the EMA 50, a close beyond the pullback's extreme, "
		"or the H1 context gone. MONEY: at most 2 trades on one M15 trend "
		"(from one EMA 20/50 cross to the next), and no new trade once the "
		"day has lost 3R. Trailing to break even is the page's trailing "
		"stop; the risk per trade is the page's risk field.")

	FAST = 20
	SLOW = 50
	INDICATORS = ({'kind': 'ema', 'period': FAST},
				  {'kind': 'ema', 'period': SLOW})

	#: hours per window of the H1 structure
	STRUCTURE_BARS = 12
	#: bars back the pullback may have touched the fast average
	PULLBACK_BARS = 3
	#: "a una distanza minima di 1,0-1,5 ATR": the near end
	MIN_STOP_ATR = 1.0
	REWARD = 2.0
	#: "rischio/rendimento minimo: 1:1,5"
	MIN_REWARD = 1.5
	#: "massimo due tentativi sullo stesso movimento"
	MAX_ATTEMPTS = 2
	#: "non entrare se il movimento di conferma è già eccessivamente esteso"
	MAX_BAR_ATR = 2.0

	SETUP_BARS = PULLBACK_BARS + 1

	def setup(self, args):
		M15.setup(self, args)
		self._set(args, 'fast', self.FAST)
		self._set(args, 'slow', self.SLOW)
		self._set(args, 'structureBars', self.STRUCTURE_BARS)
		self._set(args, 'pullbackBars', self.PULLBACK_BARS)
		self._set(args, 'minStopAtr', self.MIN_STOP_ATR)
		self._set(args, 'reward', self.REWARD)

	def series(self):
		state = Reading(Series(ema=(self.fast, self.slow), atr=self.atrPeriod,
							   keep=self.pullbackBars + 2),
						h1=Series(ema=(self.fast, self.slow),
								  keep=2 * self.structureBars + 1))
		#: the M15 trend, the bar it began on, and per recent bar: (reached
		#: the fast average from above, from below, closed under the slow
		#: one, closed over it)
		state.trend, state.leg, state.marks = 0, 0, []
		return state

	def feed(self, state, candle):
		M15.feed(self, state, candle)
		fast, slow = state.m15.ema(self.fast), state.m15.ema(self.slow)
		if fast is None or slow is None:
			return
		trend = 1 if fast > slow else -1 if fast < slow else 0
		if trend != state.trend:
			state.trend, state.leg = trend, state.m15.seen
		close = candle.mid['c']
		state.marks.append({1: candle.mid['l'] <= fast,
							-1: candle.mid['h'] >= fast,
							'under': close < slow, 'over': close > slow})
		del state.marks[:-self.pullbackBars]

	def allowed(self, state, side):
		"""Does the H1 context agree with this side?"""
		fast, slow = state.h1.ema(self.fast), state.h1.ema(self.slow)
		if fast is not None and slow is not None and side * (fast - slow) > 0:
			return True
		return state.structure(self.structureBars) == side

	def signal(self, state, candle):
		m = state.m15
		atr, side = m.atr(), state.trend
		window = m.window(2)
		if not atr or not side or window is None \
				or len(state.marks) < self.pullbackBars:
			return None
		if not self.allowed(state, side):
			return None
		through = 'under' if side > 0 else 'over'
		if not any(mark[side] for mark in state.marks) \
				or any(mark[through] for mark in state.marks):
			return None
		previous = window[0]
		beyond = candle.mid['c'] > previous.mid['h'] if side > 0 \
			else candle.mid['c'] < previous.mid['l']
		if not (beyond or engulfing(candle, previous, side)):
			return None

		bars = m.window(self.pullbackBars)
		extreme = min(c.mid['l'] for c in bars) if side > 0 \
			else max(c.mid['h'] for c in bars)
		entry = self.entry(candle, side)
		# under the pullback, and never nearer than the ATR floor
		structural = extreme - side * self.bufferAtr * atr
		floor = entry - side * self.minStopAtr * atr
		stop = min(structural, floor) if side > 0 else max(structural, floor)
		return {'side': side, 'stop': stop,
				'target': self.levels(candle, side, stop, self.reward),
				'key': state.leg, 'extreme': extreme}

	def exit(self, state, candle, held):
		side, close = held['side'], candle.mid['c']
		slow = state.m15.ema(self.slow)
		if slow is not None and side * (close - slow) < 0:
			return EARLY_EXIT
		if side * (close - held['extreme']) < 0:
			return EARLY_EXIT
		if not self.allowed(state, side):
			return EARLY_EXIT
		return None
