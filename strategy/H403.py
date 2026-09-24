"""
3. Mean reversion con bande di Bollinger (5-STRATEGIE.md, sezione 3).
"""

from parity_deriva.lib.streaming import Series
from parity_deriva.strategy.H4 import H4


class H403(H4):
	#: the name the page and the ledger use; see H4.TAG
	TAG = 'H403-BOLLINGER'

	#: See AG01.DESCRIPTION.
	DESCRIPTION = (
		"Mean reversion sulle bande di Bollinger (5-STRATEGIE.md, sezione 3). "
		"Bande a 20 periodi e 2 deviazioni standard sulla chiusura. SEGNALE "
		"long: la candela ha bucato la banda inferiore con il minimo ed è "
		"rientrata, cioè ha chiuso sopra la banda; short allo specchio sulla "
		"banda superiore. Il filtro RSI(14) sotto 30 o sopra 70, che il "
		"documento chiama opzionale, è spento di default. INGRESSO: a mercato "
		"alla chiusura. STOP: mezzo ATR(14) oltre la banda che è stata "
		"bucata. TAKE PROFIT: la media centrale, cioè la SMA 20 - delle due "
		"mete che il documento propone (media o banda opposta) è presa la "
		"più vicina, che è quella che il nome della strategia promette: il "
		"ritorno alla media. Attenzione al rapporto: qui il target non è 2R, "
		"è quanto dista la media, e su una banda molto larga può essere meno "
		"del rischio.")

	PERIOD = 20
	DEVIATIONS = 2.0
	#: "sotto la banda inferiore di una quantità fissa (es. 0.5-1x ATR)"
	ATR_STOP = 0.5
	RSI_LOW = 30.0
	RSI_HIGH = 70.0

	INDICATORS = ({'kind': 'bollinger', 'period': PERIOD,
				   'deviations': DEVIATIONS},)

	def setup(self, args):
		self._set(args, 'period', self.PERIOD)
		self._set(args, 'deviations', self.DEVIATIONS)
		self._set(args, 'atrStop', self.ATR_STOP)
		self.useRsi = False
		self._set(args, 'useRsi')

	def series(self):
		return Series(sma=(self.period,), stdev=(self.period,),
					  atr=self.atrPeriod, rsi=self.atrPeriod,
					  keep=self.period + 2)

	def signal(self, state, candle):
		bands = state.bands(self.period, self.deviations)
		atr = state.atr()
		if bands is None or not atr:
			return None
		lower, middle, upper = bands
		rsi = state.rsi()
		close = candle.mid['c']

		if candle.mid['l'] <= lower and close > lower:
			if self.useRsi and (rsi is None or rsi >= self.RSI_LOW):
				return None
			return 1, lower - self.atrStop * atr, middle

		if candle.mid['h'] >= upper and close < upper:
			if self.useRsi and (rsi is None or rsi <= self.RSI_HIGH):
				return None
			return -1, upper + self.atrStop * atr, middle

		return None
