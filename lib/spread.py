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

Two ways to configure it. A provider's own setting (ETORO_SPREAD, IB_SPREAD,
TWELVEDATA_SPREAD) states a number and wins. Without one, the shared set in
the market folder (spread.json, built by scripts/spread_profile.py from the
sources that do quote an ask and a bid) says how wide the spread is in each
five minutes of the week - one cautious set for every one-price broker.

IG is the exception among the three and does not need this at all: its price
response carries a bid and an ask for every one of open, high, low and close.
"""
import datetime
import json
import os
from zoneinfo import ZoneInfo

FILENAME = 'spread.json'
#: minutes in one slot of the shared set, and slots in a week
SLOT = 5
SLOTS = 7 * 24 * 60 // SLOT


def slot(when, zone):
	"""The slot of the week a naive UTC time falls in, on the instrument's clock."""
	local = when.replace(tzinfo=datetime.timezone.utc).astimezone(ZoneInfo(zone))
	return (local.weekday() * 24 * 60 + local.hour * 60 + local.minute) // SLOT


class SpreadModel(object):
	"""
	A stated spread, applied half either side of the served price.

	Configure it as a number in the instrument's own units, or as a dict per
	instrument:

		ETORO_SPREAD = 0.0001
		IB_SPREAD = {'EUR_USD': 0.00008, 'DE30_EUR': 1.2}

	or leave that unset and let the shared set (`profile`, spread.json's
	instruments) say it by the time of the bar.

	Whatever you set, it stands in for something that varies bar by bar, so a
	real spread wider than the model shows up as the strategy filling at
	prices it did not expect. That is precisely what trading/parity.py
	measures - the model belongs under the alarm, not above it.
	"""

	def __init__(self, spread=None, profile=None):
		self.spread = spread
		self.profile = profile or {}

	def enabled(self):
		return self.spread is not None or bool(self.profile)

	def width(self, instrument_name, when=None, period=None):
		if isinstance(self.spread, dict):
			if instrument_name in self.spread:
				return float(self.spread[instrument_name])
		elif self.spread is not None:
			return float(self.spread)
		entry = self.profile.get(instrument_name)
		if entry is None:
			return None
		if when is None:
			return float(entry['widest'])
		# the widest slot the bar covers: one M5 bar is one slot, an H4 bar
		# takes the worst of its 48, the rollover included if it is in there
		steps = max(1, -(-int((period or datetime.timedelta(minutes=SLOT)).total_seconds()) // (SLOT * 60)))
		values = [entry['slots'][slot(when + datetime.timedelta(minutes=SLOT * i), entry['zone'])]
				  for i in range(steps)]
		values = [v for v in values if v is not None]
		# ponytail: a slot no source ever quoted (a broker open when the
		# others are shut) takes the instrument's widest, not a neighbour's
		return float(max(values)) if values else float(entry['widest'])

	def apply(self, instrument_name, ohlc, when=None, period=None):
		"""
		(bid, ask) for one served OHLC, or (None, None) when off.

		The served price is treated as the mid, so half the spread goes each
		way. Every field moves by the same amount: a model that widened the
		high and not the low would be asserting something about where in the
		bar the spread moved, which is not knowable from one series.
		"""
		width = self.width(instrument_name, when, period)
		if width is None:
			return None, None
		half = width / 2.0
		bid = dict((k, float(v) - half) for k, v in ohlc.items())
		ask = dict((k, float(v) + half) for k, v in ohlc.items())
		return bid, ask


def path(setup=None):
	from parity_deriva.data import market
	return os.path.join(market.directory(setup), FILENAME)


def profile(setup=None):
	"""spread.json's instruments, or {} when the market folder has none."""
	try:
		with open(path(setup)) as handle:
			return json.load(handle).get('instruments') or {}
	except (OSError, ValueError, AttributeError):
		return {}


def spreadModel(setup, name):
	"""
	The model a provider's own setting describes, by setting name, over the
	shared set.

	Takes the name rather than reading a fixed attribute so that each
	provider can keep its own figure: a spread measured on eToro's demo
	account says nothing about what IB's feed will quote. Unset, the shared
	set applies, which is the same for all of them on purpose.
	"""
	return SpreadModel(getattr(setup, name, None), profile(setup))
