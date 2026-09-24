"""
2. Breakout di consolidamento con conferma (5-STRATEGIE.md, sezione 2).
"""

from parity_deriva.lib.streaming import Series
from parity_deriva.strategy.H4 import H4


class H402(H4):
	#: the name the page and the ledger use; see H4.TAG
	TAG = 'H402-BREAKOUT'

	#: See AG01.DESCRIPTION.
	DESCRIPTION = (
		"Breakout di un consolidamento (5-STRATEGIE.md, sezione 2). PATTERN: "
		"sulle 20 candele prima di questa, la resistenza è il massimo del "
		"gruppo e il supporto il minimo; il gruppo vale come consolidamento "
		"se è alto meno di 3,25 ATR(14), cioè stretto rispetto alla "
		"volatilità recente: su EUR_USD è circa il decimo più stretto delle "
		"finestre di venti barre, misurato, perché il documento il numero non lo "
		"dà. SEGNALE long: la chiusura supera la resistenza del gruppo; "
		"short: chiude sotto il supporto. Le conferme che il documento chiama "
		"opzionali - RSI(14) sopra 60 o sotto 40, e volume sopra la media "
		"delle ultime 20 barre - sono spente di default e si accendono con un "
		"parametro. INGRESSO: a mercato alla chiusura. STOP: il supporto del "
		"pattern per un long, la resistenza per uno short. TAKE PROFIT: 2 "
		"volte la distanza dello stop. Fra le due misure che il documento "
		"propone per il target - l'altezza del pattern proiettata o un "
		"multiplo del rischio - è preso il multiplo, perché la proiezione "
		"dall'altezza del pattern dà un rapporto sotto 1R e la regola "
		"generale del documento chiede almeno 2R. Del pattern è implementato "
		"il rettangolo e non il triangolo.")

	#: candles the pattern is looked for in, and the volume average's own
	#: window. "es. 10-30" and "media degli ultimi N periodi".
	WINDOW = 20
	VOLUME_BARS = 20
	#: "stretta rispetto alla volatilità recente (puoi usare ATR)". The
	#: document gives no factor, so this one is a conjecture - and a measured
	#: one rather than a guess: over EUR_USD the range of twenty bars is 4.6
	#: ATR(14) at the median and 3.26 at the tenth percentile, on daily and
	#: hourly bars alike, so this keeps roughly the tightest tenth of windows
	#: and calls those a consolidation. Chosen this way because the first
	#: guess, 2.0, is below the fifth percentile: the pattern never occurred
	#: once in ten years and the strategy placed no orders at all.
	NARROW_ATR = 3.25
	RSI_LONG = 60.0
	RSI_SHORT = 40.0
	REWARD = 2.0

	SETUP_BARS = WINDOW + 1

	def setup(self, args):
		self._set(args, 'windowBars', self.WINDOW)
		self._set(args, 'volumeBars', self.VOLUME_BARS)
		self._set(args, 'narrowAtr', self.NARROW_ATR)
		self._set(args, 'reward', self.REWARD)
		#: the document's "conferme opzionali", off unless asked for
		self.useRsi = False
		self.useVolume = False
		self._set(args, 'useRsi')
		self._set(args, 'useVolume')

	def series(self):
		return Series(atr=self.atrPeriod, rsi=self.atrPeriod,
					  volume=self.volumeBars,
					  keep=max(self.windowBars, self.volumeBars) + 2)

	def pattern(self, state):
		"""
		(support, resistance) of the bars before this one, or None.

		Before this one, and that is the whole of it: a breakout measured
		against a range that includes the breaking bar is not a breakout, it
		is the definition of a maximum.
		"""
		window = state.window(self.windowBars + 1)
		if window is None:
			return None
		bars = window[:-1]
		return (min(c.mid['l'] for c in bars), max(c.mid['h'] for c in bars))

	def confirmed(self, state, candle, side):
		if self.useRsi:
			rsi = state.rsi()
			if rsi is None:
				return False
			if side > 0 and rsi <= self.RSI_LONG:
				return False
			if side < 0 and rsi >= self.RSI_SHORT:
				return False
		if self.useVolume:
			average = state.averageVolume()
			volume = float(getattr(candle, 'volume', 0) or 0)
			if average is None or volume <= average:
				return False
		return True

	def signal(self, state, candle):
		found = self.pattern(state)
		atr = state.atr()
		if found is None or not atr:
			return None
		support, resistance = found
		if resistance - support > self.narrowAtr * atr:
			# not a consolidation: a range as wide as the market's own
			# movement is just the market moving
			return None

		close = candle.mid['c']
		if close > resistance and self.confirmed(state, candle, 1):
			return 1, support, self.levels(candle, 1, support, self.reward)
		if close < support and self.confirmed(state, candle, -1):
			return -1, resistance, self.levels(candle, -1, resistance,
											   self.reward)
		return None
