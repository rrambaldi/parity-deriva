
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
