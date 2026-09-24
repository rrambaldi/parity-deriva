
import logging
import logging.config
import os
import pandas as pd
import datetime
from parity_deriva.etc.settings import *

def datetimeToString(t):
	return t.strftime('%Y-%m-%dT%H:%M:%S.%f') + "000Z"

def timestampFromString(s):
	if len(s)!=30:
		return None
	return datetime.datetime.strptime(s[:-4], "%Y-%m-%dT%H:%M:%S.%f")

def granularityToTimedelta(g):
	if g[0] == '/':
		g = g[1:]
	if g[:1] == 'S':
		return pd.Timedelta(seconds= int(g[1:]))
	if g[:1] == 'M':
		return pd.Timedelta(minutes= int(g[1:]))
	if g[:1] == 'H':
		return pd.Timedelta(hours= int(g[1:]))
	if g[:1] == 'D':
		return pd.Timedelta(days=1)
	if g[:1] == 'W':
		return pd.Timedelta(days=7)

	return None

def serieToDict(serie):
	pass


def signalNumber(tag, instrument, granularity, when):
	"""
	The identity of a signal, as a function of the data that produced it.

	This is the key a live trade and its simulated counterpart are joined on,
	so it must not depend on when the code ran: replaying the same candles has
	to yield the same keys as the live session did. It used to be
	datetime.today() to the second, which meant a replay could never be
	matched against the run it was replaying, and which collided outright
	whenever two signals landed in the same second - 116 of them shared one
	key in a single replay.

	    AG01:EUR_USD:H1:20180115T010000
	"""
	stamp = when.strftime('%Y%m%dT%H%M%S') if hasattr(when, 'strftime') else str(when)
	return "%s:%s:%s:%s" % (tag, instrument, granularity, stamp)


def pricePrecision(instrument, setup=None):
	"""
	Decimal places OANDA accepts for an order price on this instrument.

	Unknown instruments fall back to DEFAULT_PRICE_PRECISION and are logged,
	because the alternative - guessing low - silently moves the level instead
	of being rejected by the broker.
	"""
	table = INSTRUMENT_PRECISION
	default = DEFAULT_PRICE_PRECISION
	if setup is not None:
		table = getattr(setup, 'INSTRUMENT_PRECISION', table)
		default = getattr(setup, 'DEFAULT_PRICE_PRECISION', default)
	if instrument in table:
		return table[instrument]
	logging.getLogger('parity_deriva.trading.trading').warning(
		"no precision configured for %s, using %d decimals"
		% (instrument, default))
	return default


def expiryAt(when, granularity, clock):
	"""
	The instant an order issued on this candle should die.

	`clock` is (hour, minute, second) - "the end of the day", as AG01 and
	AG02 spell it - and `when` is the candle's own timestamp.

	The day is the one the candle **closes** in and not the one it opens in.
	A signal is emitted when the bar closes, so on a daily bar those are two
	different days: measured from the open, the order would be born after its
	own expiry and could never fill. Measured from the close, an order from a
	five minute bar rests until that evening exactly as it always did, and one
	from a daily bar rests for the following day - which is the same rule read
	on a chart where a bar is a day.

	Was: datetime.today(), the machine's clock. Live that is roughly the
	     candle's day; in a replay it is years away from it, so every order
	     ever issued had an expiry in the future and nothing expired at all.
	     AG01 on EUR_USD daily rested a bracket from 3 July 2022 and filled it
	     on 15 November, at a price the market had left four months earlier.
	Now: a function of the candle, like the signal's own key is - and for the
	     same reason: replaying the same candles has to give the same run.
	"""
	hour, minute, second = clock
	bar = granularityToTimedelta(granularity)
	closes = when + bar if bar is not None else when
	if hasattr(closes, 'to_pydatetime'):
		closes = closes.to_pydatetime()
	return closes.replace(hour=int(hour), minute=int(minute),
						  second=int(second), microsecond=0)


def pipSize(instrument, setup=None):
	"""
	What one pip of this instrument is worth, as a price difference.

	Derived from the precision rather than from a second table: a pip is ten
	ticks on every instrument this project knows - EUR_USD quotes five
	decimals and its pip is 0.0001, the DAX quotes one and its point is 1 -
	and a table would be the same numbers written twice, free to drift apart.
	Add a table the day an instrument breaks the rule, not before.
	"""
	return 10.0 ** -(pricePrecision(instrument, setup) - 1)


def roundPrice(instrument, value, setup=None):
	"""
	Round a derived price to what the instrument accepts.

	This exists because the strategies used to round to one decimal place
	whatever the instrument. That is the DAX's precision; on EUR_USD it
	collapsed a take profit of 1.22380 to 1.2, two figures the wrong side of
	the entry, so the stop always triggered first and a live order would have
	been rejected.
	"""
	return round(value, pricePrecision(instrument, setup))


def dctFromOanda(dct,typ='mid', onlyohlc=False):
	if onlyohlc:
		return {
			'o':float(dct[typ]['o'])
			, 'h':float(dct[typ]['h'])
			, 'l':float(dct[typ]['l'])
			, 'c':float(dct[typ]['c'])
#			, 'v':int(dct['volume'])
		}

	ret = {}
	ret[ datetime.datetime.strptime(dct['time'], "%Y-%m-%dT%H:%M:%S.%f000Z") ] = {
		'o':float(dct[typ]['o'])
		, 'h':float(dct[typ]['h'])
		, 'l':float(dct[typ]['l'])
		, 'c':float(dct[typ]['c'])
#		, 'v':int(dct['volume'])
	}
	return ret


def getLogger(config=None,confstr='parity_deriva.trading.trading'):
	if config is None or not os.path.exists(config):
		if os.path.exists(DEF_CONFIG):
			config = DEF_CONFIG
		elif 'PARITY_DERIVA_HOME' in os.environ:
			config = os.path.join(os.environ['PARITY_DERIVA_HOME'],'etc',DEF_CONFIG)
		elif 'VIRTUAL_ENV' in os.environ:
			config = os.path.join(os.environ['VIRTUAL_ENV'],'parity_deriva','etc',DEF_CONFIG)
		else:
			print("Missing logger config")
			os._exit(-1)
	logging.config.fileConfig(config)
	logger = logging.getLogger(confstr)
	return logger
