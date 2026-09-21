"""
Bid and ask for a broker that serves one price per candle.

OANDA serves three OHLC series and the strategies read them: AG01 buys the
high of the ask and stops out at the low of the bid. eToro serves one series,
and so does Interactive Brokers' history endpoint. Those two prices do not
exist in that data and no measurement recovers them - one number cannot be
told how far apart two others were.

So this is a *model*, and it is off unless configured. That rule is the same
one PARITY_ALARM follows: a value nobody measured is off, not filled in with
something plausible. With it off, candles carry mid only, the provider
declines bid_ask_candles, and a wiring that needs them refuses to start and
says why.

IG is the exception among the three and does not need this at all: its price
response carries a bid and an ask for every one of open, high, low and close.
"""


class SpreadModel(object):
	"""
	A stated spread, applied half either side of the served price.

	Configure it as a number in the instrument's own units, or as a dict per
	instrument:

		ETORO_SPREAD = 0.0001
		IB_SPREAD = {'EUR_USD': 0.00008, 'DE30_EUR': 1.2}

	Whatever you set, it is a constant standing in for something that varies
	by the hour, so a real spread wider than the one configured shows up as
	the strategy filling at prices it did not expect. That is precisely what
	trading/parity.py measures - the model belongs under the alarm, not above
	it.
	"""

	def __init__(self, spread=None):
		self.spread = spread

	def enabled(self):
		return self.spread is not None

	def width(self, instrument_name):
		if self.spread is None:
			return None
		if isinstance(self.spread, dict):
			if instrument_name in self.spread:
				return float(self.spread[instrument_name])
			return None
		return float(self.spread)

	def apply(self, instrument_name, ohlc):
		"""
		(bid, ask) for one served OHLC, or (None, None) when off.

		The served price is treated as the mid, so half the configured spread
		goes each way. Every field moves by the same amount: a model that
		widened the high and not the low would be asserting something about
		where in the bar the spread moved, which is not knowable from one
		series.
		"""
		width = self.width(instrument_name)
		if width is None:
			return None, None
		half = width / 2.0
		bid = dict((k, float(v) - half) for k, v in ohlc.items())
		ask = dict((k, float(v) + half) for k, v in ohlc.items())
		return bid, ask


def spreadModel(setup, name):
	"""
	The model a provider's own setting describes, by setting name.

	Takes the name rather than reading a fixed attribute so that each
	provider keeps its own figure: a spread measured on eToro's demo account
	says nothing about what IB's feed will quote.
	"""
	return SpreadModel(getattr(setup, name, None))
