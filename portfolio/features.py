"""
What the market looked like on the bar a strategy signalled on
(docs/PIANO-FASE1.md C4b): a short catalogue of features, each with a fixed
name, read a closed bar at a time off lib/streaming.Series - the entry
filter (portfolio/filters.py) reads them live and in the backtest, the entry
analysis (performance/entry.py) over a run's candles, with the same code.

	rsi14            Wilder's RSI, 14 bars
	adx14            Wilder's ADX, 14 bars: the trend's strength, either way
	atrpct14         the ATR, 14 bars, in % of the close
	dist_sma100_atr  the close's distance from the 100-bar SMA, in ATRs
	range_atr14      the bar's high to low, in ATRs
	slope100         the 100-bar SMA's move over the last 10 bars, in ATRs
	hour, weekday    the bar's UTC hour and day (0 Monday)

The indicators uploaded by an assistant are the next step (C4b2).
"""

import datetime

from parity_deriva.lib.streaming import Series

NAMES = ('rsi14', 'adx14', 'atrpct14', 'dist_sma100_atr', 'range_atr14', 'slope100', 'hour', 'weekday')


class Features(object):

	def __init__(self):
		self.series = Series(sma=(), atr=14, rsi=14, adx=14, keep=120)
		self.last = None

	def add(self, candle):
		"""One more closed bar."""
		self.series.add(candle)
		self.last = candle

	def values(self):
		"""{name: value}, None for one still warming, of the last bar added."""
		s, bar = self.series, self.last
		if bar is None:
			return dict((name, None) for name in NAMES)
		close = bar.mid['c']
		atr = s.atr()
		sma, before = s.sma(100), s.sma(100, back=10)
		when = bar.time if isinstance(bar.time, datetime.datetime) else None
		return {
			'rsi14': s.rsi(),
			'adx14': s.adx(),
			'atrpct14': atr / close * 100.0 if atr and close else None,
			'dist_sma100_atr': (close - sma) / atr if atr and sma is not None else None,
			'range_atr14': (bar.mid['h'] - bar.mid['l']) / atr if atr else None,
			'slope100': (sma - before) / atr if atr and sma is not None and before is not None else None,
			'hour': when.hour if when else None,
			'weekday': when.weekday() if when else None,
		}
