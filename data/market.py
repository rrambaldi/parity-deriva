"""
Where the market data is kept - the candle stores (<INSTRUMENT>.hd5) and the
economic calendar (calendar.csv) - and whether this server writes it.

Two servers on one host, prod and dev, read the same folder and only one of
them writes it: two writers merging the same store lose each other's bars.
DATA_DIR/market.json says which, and the settings page writes it:

	{"dir": "/mnt/HC_Volume_37718599/rrambaldi/MARKET", "writer": true,
	 "candles": {"source": "upstream", "every": 60},
	 "calendar": {"source": "manual", "every": 60},
	 "upstream": {"url": "https://host/parity/mcp", "token": "<its mirror token>"},
	 "provider": "oanda"}

Without it the market data is DATA_DIR's own and this server writes it, which
is how it always was. Where the writer's data comes from is data/sources.py. Runs, strategies, favourites and live sessions stay in
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
#: the sources each kind of market data can have (data/sources.py)
KINDS = {'candles': ('manual', 'upstream', 'providers'), 'calendar': ('manual', 'upstream')}


class MarketError(Exception):
	"""A write into the market data from a server that only reads it."""


def home(setup=None):
	return getattr(setup if setup is not None else settings, 'DATA_DIR', '') or '.'


def _read(setup):
	try:
		with open(os.path.join(home(setup), FILENAME)) as handle:
			kept = json.load(handle)
	except (OSError, ValueError):
		kept = {}
	return kept if isinstance(kept, dict) else {}


def config(setup=None):
	"""
	{'dir': the market folder, 'writer': whether this server writes it, and
	for the writer each kind's {'source', 'every' minutes}, 'upstream' and
	'provider'}.
	"""
	kept = _read(setup)
	out = {'dir': kept.get('dir') or home(setup), 'writer': kept.get('writer') is not False,
		   'upstream': dict({'url': '', 'token': ''}, **(kept.get('upstream') or {})),
		   'provider': kept.get('provider') or ''}
	for kind in KINDS:
		out[kind] = dict({'source': 'manual', 'every': 60}, **(kept.get(kind) or {}))
	return out


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


def save(changes, setup=None):
	"""
	Change what market.json says, each value checked first: the folder, the
	role, a kind's source, the upstream, the provider. An upstream without a
	token keeps the token it had.
	"""
	kept = _read(setup)
	for key, value in changes.items():
		if key == 'dir':
			value = str(value or '').strip()
			if not os.path.isabs(value) or not os.path.isdir(value):
				raise MarketError("%r is not a folder on this server" % value)
			value = os.path.normpath(value)
		elif key == 'writer':
			if not isinstance(value, bool):
				raise MarketError("writer is true or false")
		elif key in KINDS:
			if not isinstance(value, dict) or value.get('source') not in KINDS[key]:
				raise MarketError("%s: a source among %s" % (key, ', '.join(KINDS[key])))
			every = value.get('every', 60)
			if not isinstance(every, int) or isinstance(every, bool) or not 1 <= every <= 10080:
				raise MarketError("%s: every is minutes, 1 to 10080" % key)
			value = {'source': value['source'], 'every': every}
		elif key == 'upstream':
			url = str((value or {}).get('url') or '').strip() if isinstance(value, dict) else ''
			if not url.startswith(('https://', 'http://')):
				raise MarketError("upstream: the other server's MCP address, https://.../mcp")
			token = str(value.get('token') or '') or (kept.get('upstream') or {}).get('token', '')
			value = {'url': url, 'token': token}
		elif key == 'provider':
			from parity_deriva.trading import providers
			if value not in providers.available():
				raise MarketError("provider: one of %s" % ', '.join(providers.available()))
		else:
			raise MarketError("no market setting %r" % key)
		kept[key] = value
	where = os.path.join(home(setup), FILENAME)
	# the upstream's token is in it
	with open(os.open(where + '.part', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
		json.dump(kept, handle)
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
