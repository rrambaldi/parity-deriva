"""
Where the market data is kept - the candle stores (<INSTRUMENT>.hd5) and the
economic calendar (calendar.csv) - and whether this server writes it.

Two servers on one host, prod and dev, read the same folder and only one of
them writes it: two writers merging the same store lose each other's bars.
DATA_DIR/market.json says which, and the settings page writes it:

	{"dir": "/mnt/HC_Volume_37718599/rrambaldi/MARKET", "writer": true}

Without it the market data is DATA_DIR's own and this server writes it, which
is how it always was. Runs, strategies, favourites and live sessions stay in
DATA_DIR: they belong to the server, the candles to the market.
"""
import contextlib
import json
import os
import shutil

try:
	import fcntl
except ImportError:
	# ponytail: Windows has no flock; one writer process there, add msvcrt.locking if not
	fcntl = None

from parity_deriva.etc import settings

FILENAME = 'market.json'


class MarketError(Exception):
	"""A write into the market data from a server that only reads it."""


def config(setup=None):
	"""{'dir': the market folder, 'writer': whether this server writes it}."""
	data = getattr(setup if setup is not None else settings, 'DATA_DIR', '') or '.'
	try:
		with open(os.path.join(data, FILENAME)) as handle:
			kept = json.load(handle)
	except (OSError, ValueError):
		kept = {}
	if not isinstance(kept, dict):
		kept = {}
	return {'dir': kept.get('dir') or data, 'writer': kept.get('writer') is not False}


def directory(setup=None):
	return config(setup)['dir']


def writer(setup=None):
	return config(setup)['writer']


def store(instrument, setup=None):
	return os.path.join(directory(setup), '%s.hd5' % instrument)


def guard(where, setup=None):
	"""Refuse a write into the market folder from a server that only reads it."""
	kept = config(setup)
	if kept['writer']:
		return
	inside = os.path.realpath(kept['dir'])
	if os.path.commonpath([os.path.realpath(where), inside]) == inside:
		raise MarketError(
			"this server only reads the market data in %s: write it on the server "
			"that writes it, or make this one the writer on the settings page" % kept['dir'])


def save(folder, writes, setup=None):
	"""Point this server at a market folder, as its writer or as a reader."""
	folder = str(folder or '').strip()
	if not os.path.isabs(folder) or not os.path.isdir(folder):
		raise MarketError("%r is not a folder on this server" % folder)
	if not isinstance(writes, bool):
		raise MarketError("writer is true or false")
	where = os.path.join(getattr(setup if setup is not None else settings, 'DATA_DIR', '') or '.', FILENAME)
	with open(where + '.part', 'w') as handle:
		json.dump({'dir': os.path.normpath(folder), 'writer': writes}, handle)
	os.replace(where + '.part', where)
	return config(setup)


@contextlib.contextmanager
def lock(folder):
	"""
	One write into the folder at a time, across processes: the service's
	import and pushes, and a script run by hand, would lose each other's rows.
	"""
	with open(os.path.join(folder, '.market.lock'), 'a') as handle:
		if fcntl:
			fcntl.flock(handle, fcntl.LOCK_EX)
		yield


@contextlib.contextmanager
def rewrite(path):
	"""
	A store written as a copy that then replaces it whole. A reader in another
	process (dev, a sandbox) keeps the file it opened and never meets a
	half-written one, and a writer killed halfway - this host's RAM is short -
	leaves the store as it was instead of a broken HDF5.
	"""
	part = path + '.part'
	if os.path.exists(path):
		shutil.copyfile(path, part)
	try:
		yield part
		os.replace(part, path)
	finally:
		if os.path.exists(part):
			os.remove(part)
