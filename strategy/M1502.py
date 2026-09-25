"""
2. SBR - Session Breakout Range (6-STRATEGIE-M15.md, section 2).
"""

from parity_deriva.lib.streaming import Series
from parity_deriva.strategy.H4 import body, span
from parity_deriva.strategy.M15 import M15, Reading


class M1502(M15):
	#: the name the page and the ledger use; see H4.TAG
	TAG = 'M1502-SBR'

	#: See AG01.DESCRIPTION.
	DESCRIPTION = (
		"Session breakout range (6-STRATEGIE-M15.md, section 2). RANGE: the "
		"high and low of the first 4 M15 bars from 07:00 UTC, the London "
		"open, fixed and in UTC like the data. It is traded only if it is at "
		"most 5 ATR(14) wide, the ATR read before the range began: measured "
		"on EUR_USD 2016-2023 that range is 3.5 ATR at the median and 4.8 at "
		"the 80th percentile, so this drops the widest sixth. SIGNAL long: "
		"within 4 hours of the range, an M15 close at least 0.1 ATR above its "
		"high, on a bullish bar whose body is at least half its height and "
		"which is no wider than 2 ATR. Short is the mirror under the low. "
		"ENTRY: at market on the next open. STOP: 0.5 ATR back inside the "
		"range. TARGET: the range's height projected from the broken side. "
		"TIME STOP: out after 8 bars if the trade never went 0.5R its way. "
		"MONEY: one breakout per direction and session, no new trade once "
		"the day has lost 3R. Not implemented: the retest entry and the 50% "
		"partial close, which the document makes optional.")

	#: the session's first bar, in UTC, and how many bars make its range
	SESSION_HOUR = 7
	RANGE_BARS = 4
	#: how long after the range a breakout still counts, in bars
	BREAKOUT_BARS = 16
	#: "almeno un piccolo buffer ... come frazione dell'ATR"
	BREAK_ATR = 0.1
	#: "corpo consistente, non soltanto uno spike": body over the bar's height
	BODY = 0.5
	#: measured, see the description
	MAX_RANGE_ATR = 5.0
	#: "appena dentro il range"
	STOP_INSIDE_ATR = 0.5
	#: "chiudere la posizione se dopo 4-8 candele M15 ..."
	TIME_STOP_BARS = 8
	#: "un solo breakout valido per direzione e sessione"
	MAX_ATTEMPTS = 1
	#: "verificare che la candela non sia già troppo estesa"
	MAX_BAR_ATR = 2.0

	SETUP_BARS = RANGE_BARS + 1

	#: see M15.PARAM_HELP
	PARAM_HELP = {
		'sessionHour': 'session start hour, UTC',
		'rangeBars': 'bars that build the opening range',
		'breakoutBars': 'bars after the range a breakout still counts',
		'breakAtr': 'breakout buffer beyond the range, in ATR',
		'bodyShare': 'minimum candle body as a share of its range',
		'maxRangeAtr': 'max width of the opening range, in ATR',
		'stopInsideAtr': 'stop distance inside the range, in ATR',
	}

	def setup(self, args):
		M15.setup(self, args)
		self._set(args, 'sessionHour', self.SESSION_HOUR)
		self._set(args, 'rangeBars', self.RANGE_BARS)
		self._set(args, 'breakoutBars', self.BREAKOUT_BARS)
		self._set(args, 'breakAtr', self.BREAK_ATR)
		self._set(args, 'bodyShare', self.BODY)
		self._set(args, 'maxRangeAtr', self.MAX_RANGE_ATR)
		self._set(args, 'stopInsideAtr', self.STOP_INSIDE_ATR)

	def series(self):
		state = Reading(Series(atr=self.atrPeriod, keep=2))
		#: the session being read: its date, the range, the bars of it seen,
		#: and the ATR from before it began
		state.day, state.range, state.count, state.atr0 = None, None, 0, None
		return state

	def start(self, when):
		return when.replace(hour=self.sessionHour, minute=0, second=0,
							microsecond=0)

	def feed(self, state, candle):
		when = candle.time
		start = self.start(when)
		if start <= when < start + self.rangeBars * self.bar:
			if state.day != when.date():
				state.day, state.count = when.date(), 0
				state.range = [candle.mid['h'], candle.mid['l']]
				state.atr0 = state.atr()
			state.range = [max(state.range[0], candle.mid['h']),
						   min(state.range[1], candle.mid['l'])]
			state.count += 1
		M15.feed(self, state, candle)

	def signal(self, state, candle):
		when = candle.time
		# the whole range, all of its bars, and today's: a day with a bar
		# missing from the window has no range rather than a smaller one
		if state.day != when.date() or state.count < self.rangeBars:
			return None
		ends = self.start(when) + self.rangeBars * self.bar
		if not ends <= when < ends + self.breakoutBars * self.bar:
			return None
		atr = state.atr()
		high, low = state.range
		width = high - low
		if not atr or not state.atr0 or width > self.maxRangeAtr * state.atr0:
			return None
		if body(candle) < self.bodyShare * span(candle):
			return None

		close = candle.mid['c']
		for side, level in ((1, high), (-1, low)):
			if side * (close - level) < self.breakAtr * atr \
					or side * (close - candle.mid['o']) <= 0:
				continue
			# inside the range, and never past its other side
			inside = min(self.stopInsideAtr * atr, width)
			return {'side': side, 'stop': level - side * inside,
					'target': level + side * width,
					'key': (when.date(), side)}
		return None
