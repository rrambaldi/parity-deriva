"""
4. Swing su livelli daily con conferma 4H (5-STRATEGIE.md, sezione 4).
"""

from parity_deriva.lib.streaming import Daily, Series, Swings
from parity_deriva.strategy.H4 import H4, engulfing, pinBar


class Reading(object):
	"""
	The 4H series, and the daily one built from it.

	The daily bars are folded out of the 4H ones rather than taken from a
	second stream: a day is complete when a bar of the next day arrives, and
	nothing reads it before. That makes the daily levels exactly as causal as
	the bars they are made of - which a second stream would have to be argued
	about, since a daily candle stamped Monday is knowledge of Monday night.
	"""

	def __init__(self, series, swings, sma):
		self.series = series
		self.swings = swings
		self.smaPeriod = sma
		self.daily = Daily()
		self.closes = []

	def add(self, candle):
		self.series.add(candle)
		done = self.daily.add(candle)
		if done is not None:
			self.swings.add(done)
			self.closes.append(done.mid['c'])
			if len(self.closes) > self.smaPeriod * 4:
				self.closes.pop(0)

	def atr(self):
		return self.series.atr()

	def trend(self):
		"""+1 above the daily average, -1 below, None while it is warming."""
		if len(self.closes) < self.smaPeriod:
			return None
		average = sum(self.closes[-self.smaPeriod:]) / float(self.smaPeriod)
		last = self.closes[-1]
		return 1 if last > average else (-1 if last < average else 0)


class H404(H4):
	#: the name the page and the ledger use; see H4.TAG
	TAG = 'H404-LIVELLI-DAILY'

	#: See AG01.DESCRIPTION.
	DESCRIPTION = (
		"Swing sui livelli daily con conferma 4H (5-STRATEGIE.md, sezione 4). "
		"I livelli daily sono gli swing confermati - 2 giorni per lato - "
		"delle barre giornaliere, che vengono costruite dalle 4H man mano che "
		"arrivano: un giorno è chiuso quando arriva una barra del giorno "
		"dopo, e prima di allora nessuno lo legge. Il bias daily è la "
		"chiusura del giorno contro la sua SMA 20. SEGNALE long: bias "
		"rialzista, il prezzo è entro 1 ATR(14) 4H da un supporto daily, e la "
		"candela 4H è un pattern di inversione - pin bar rialzista o "
		"engulfing rialzista. Short allo specchio su una resistenza. "
		"INGRESSO: a mercato alla chiusura della 4H di conferma. STOP: mezzo "
		"ATR sotto il minore fra il minimo della candela e il livello daily "
		"(sopra il maggiore, per uno short). TAKE PROFIT: il primo livello "
		"daily opposto oltre il prezzo; se non ce n'è, 2 volte la distanza "
		"dello stop. Dei pattern di inversione che il documento elenca sono "
		"implementati la pin bar e l'engulfing, non la inside bar con "
		"rottura.")

	#: days either side of a daily swing. Fewer than the 5 used intraday
	#: because a daily series is short: five per side asks for eleven days
	#: per level.
	SWING_DAYS = 2
	#: the daily trend average
	DAILY_SMA = 20
	#: "entro una tolleranza, es. 0.5-1x ATR 4H"
	TOLERANCE_ATR = 1.0
	#: how far beyond the level the stop sits
	STOP_ATR = 0.5
	REWARD = 2.0
	#: daily levels kept, in days
	KEEP_DAYS = 120
	#: the pin bar's tail, as a multiple of its body. The document names the
	#: pattern and not its proportions.
	WICK = 2.0

	#: the pattern is this bar, and the engulfing reads the one before it
	SETUP_BARS = 2

	#: see H4.PARAM_HELP
	PARAM_HELP = {
		'swingDays': 'days either side confirming a swing',
		'dailySma': 'period of the daily trend SMA, in days',
		'toleranceAtr': 'distance from a level counted as near, in ATR',
		'stopAtr': 'stop distance beyond the level, in ATR',
		'reward': 'fallback target as a multiple of the stop (R)',
		'keepDays': 'daily levels kept, in days',
		'wick': 'pin bar tail, as a multiple of its body',
	}

	def setup(self, args):
		self._set(args, 'swingDays', self.SWING_DAYS)
		self._set(args, 'dailySma', self.DAILY_SMA)
		self._set(args, 'toleranceAtr', self.TOLERANCE_ATR)
		self._set(args, 'stopAtr', self.STOP_ATR)
		self._set(args, 'reward', self.REWARD)
		self._set(args, 'keepDays', self.KEEP_DAYS)
		self._set(args, 'wick', self.WICK)

	def series(self):
		return Reading(Series(atr=self.atrPeriod, keep=4),
					   Swings(bars=self.swingDays, keep=self.keepDays),
					   self.dailySma)

	def reversal(self, state, candle, side):
		"""A pin bar or an engulfing on this bar, the way the side wants it."""
		if pinBar(candle, side, self.wick):
			return True
		window = state.series.window(2)
		return window is not None and engulfing(candle, window[0], side)

	def near(self, levels, kind, close, tolerance):
		"""The level of this kind the price is standing on, or None."""
		found = [l for l in levels
				 if l['kind'] == kind and abs(close - l['price']) <= tolerance]
		return min(found, key=lambda l: abs(close - l['price'])) if found \
			else None

	def beyond(self, levels, kind, close, side):
		"""The first level of this kind the trade would run into, or None."""
		found = [l for l in levels if l['kind'] == kind
				 and (l['price'] > close if side > 0 else l['price'] < close)]
		return min(found, key=lambda l: abs(l['price'] - close)) if found \
			else None

	def signal(self, state, candle):
		atr = state.atr()
		bias = state.trend()
		if not atr or bias is None:
			return None
		levels = state.swings.levels()
		if not levels:
			return None
		close = candle.mid['c']
		tolerance = self.toleranceAtr * atr

		for side, at, against in ((1, 'support', 'resistance'),
								  (-1, 'resistance', 'support')):
			if bias != side:
				continue
			level = self.near(levels, at, close, tolerance)
			if level is None or not self.reversal(state, candle, side):
				continue
			if side > 0:
				stop = min(candle.mid['l'], level['price']) - self.stopAtr * atr
			else:
				stop = max(candle.mid['h'], level['price']) + self.stopAtr * atr
			target = self.beyond(levels, against, close, side)
			if target is not None:
				return side, stop, target['price']
			return side, stop, self.levels(candle, side, stop, self.reward)

		return None
