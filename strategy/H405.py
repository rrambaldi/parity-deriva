"""
5. Momentum con RSI e rottura di swing recenti (5-STRATEGIE.md, sezione 5).
"""

from parity_deriva.lib.streaming import Series, Swings
from parity_deriva.strategy.H4 import H4


class Reading(object):
	"""The indicators and the swings of one instrument, fed together."""

	def __init__(self, series, swings):
		self.series = series
		self.swings = swings

	def add(self, candle):
		self.series.add(candle)
		self.swings.add(candle)

	def atr(self):
		return self.series.atr()


class H405(H4):
	#: the name the page and the ledger use; see H4.TAG
	TAG = 'H405-MOMENTUM-RSI'

	#: See AG01.DESCRIPTION.
	DESCRIPTION = (
		"Momentum con RSI e rottura di uno swing (5-STRATEGIE.md, sezione 5). "
		"SEGNALE long: RSI(14) sopra 60 e la chiusura supera l'ultimo swing "
		"high confermato; short: RSI sotto 40 e la chiusura rompe l'ultimo "
		"swing low. Uno swing high è una candela il cui massimo nessuna delle "
		"5 per lato raggiunge, e non è uno swing finché non sono stampate le "
		"5 barre dopo: al momento della rottura il livello è già vecchio di "
		"cinque barre, che è l'unico modo onesto di leggerlo. Il filtro SMA "
		"50 - chiusura dalla parte giusta e swing dalla stessa parte - che il "
		"documento chiama opzionale è spento di default. INGRESSO: a mercato "
		"alla chiusura. STOP: l'ultimo swing low confermato per un long, "
		"l'ultimo swing high per uno short; senza quel livello non si entra, "
		"perché lo stop è obbligatorio e non si inventa. TAKE PROFIT: 2 volte "
		"la distanza dello stop - delle due misure del documento è preso il "
		"multiplo del rischio e non la distanza fra SMA e swing.")

	SWING_BARS = 5
	SMA_PERIOD = 50
	RSI_LONG = 60.0
	RSI_SHORT = 40.0
	REWARD = 2.0
	#: how long a swing stays on the books, in bars
	KEEP = 400

	INDICATORS = ({'kind': 'sma', 'period': SMA_PERIOD},)

	def setup(self, args):
		self._set(args, 'swingBars', self.SWING_BARS)
		self._set(args, 'smaPeriod', self.SMA_PERIOD)
		self._set(args, 'reward', self.REWARD)
		self._set(args, 'keepBars', self.KEEP)
		self.useSma = False
		self._set(args, 'useSma')

	def series(self):
		return Reading(Series(sma=(self.smaPeriod,), rsi=self.atrPeriod,
							  atr=self.atrPeriod, keep=self.smaPeriod + 2),
					   Swings(bars=self.swingBars, keep=self.keepBars))

	def signal(self, state, candle):
		rsi = state.series.rsi()
		if rsi is None:
			return None
		close = candle.mid['c']
		average = state.series.sma(self.smaPeriod)
		if self.useSma and average is None:
			return None

		high = state.swings.last('resistance')
		low = state.swings.last('support')

		if rsi > self.RSI_LONG and high is not None and low is not None \
				and close > high['price']:
			if self.useSma and (close <= average or high['price'] <= average):
				return None
			stop = low['price']
			if stop >= close:
				# the last swing low is above the price: there is no stop to
				# be had on that side, and one is not invented
				return None
			return 1, stop, self.levels(candle, 1, stop, self.reward)

		if rsi < self.RSI_SHORT and low is not None and high is not None \
				and close < low['price']:
			if self.useSma and (close >= average or low['price'] >= average):
				return None
			stop = high['price']
			if stop <= close:
				return None
			return -1, stop, self.levels(candle, -1, stop, self.reward)

		return None
