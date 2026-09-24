"""
1. Trend following con pullback su EMA (5-STRATEGIE.md, sezione 1).
"""

from parity_deriva.lib.streaming import Series
from parity_deriva.strategy.H4 import H4, bearish, bullish


class H401(H4):
	#: the name the page and the ledger use; see H4.TAG
	TAG = 'H401-PULLBACK-EMA'

	#: See AG01.DESCRIPTION for why this is on the class.
	DESCRIPTION = (
		"Trend following con pullback sulla EMA (5-STRATEGIE.md, sezione 1). "
		"Il trend lo dà la EMA 200: si compra solo sopra, si vende solo "
		"sotto. SEGNALE long: la candela appena chiusa è andata a toccare la "
		"EMA 50 con il minimo (il pullback) ed è chiusa verde; short allo "
		"specchio, massimo sulla EMA 50 e candela rossa. INGRESSO: a mercato "
		"alla chiusura della candela - nel backtest il riempimento è "
		"l'apertura della barra successiva, che è il primo prezzo esistente "
		"dopo quella decisione. STOP: 2 ATR(14) sotto la chiusura per un "
		"long, sopra per uno short. TAKE PROFIT: 2 volte la distanza dello "
		"stop, cioè 2R. Il fattore dell'ATR e il rapporto sono i due estremi "
		"che il documento propone come intervallo (1,5-2 e 2-3) e sono "
		"parametri.")

	#: the two averages the chart draws over this strategy, with the periods
	#: read off the class rather than typed twice
	FAST = 50
	SLOW = 200
	INDICATORS = ({'kind': 'ema', 'period': FAST},
				  {'kind': 'ema', 'period': SLOW})

	#: "es. fattore 1.5-2" and "es. 2-3": the ends of the two ranges the
	#: document gives, chosen and named rather than split down the middle
	ATR_STOP = 2.0
	REWARD = 2.0

	def setup(self, args):
		self._set(args, 'fast', self.FAST)
		self._set(args, 'slow', self.SLOW)
		self._set(args, 'atrStop', self.ATR_STOP)
		self._set(args, 'reward', self.REWARD)

	def series(self):
		return Series(ema=(self.fast, self.slow), atr=self.atrPeriod)

	def signal(self, state, candle):
		fast, slow, atr = state.ema(self.fast), state.ema(self.slow), state.atr()
		if fast is None or slow is None or not atr:
			return None
		close = candle.mid['c']

		# the pullback is a fact about the bar that has closed: its low
		# reached the fast average and its body still finished the right way
		if close > slow and candle.mid['l'] <= fast and bullish(candle):
			stop = close - self.atrStop * atr
			return 1, stop, self.levels(candle, 1, stop, self.reward)

		if close < slow and candle.mid['h'] >= fast and bearish(candle):
			stop = close + self.atrStop * atr
			return -1, stop, self.levels(candle, -1, stop, self.reward)

		return None
