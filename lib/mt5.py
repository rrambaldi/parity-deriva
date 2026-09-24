"""
MetaTrader 5: the connection, the credentials, and the naming it needs.

The MetaTrader5 package talks to a running terminal and exists for Windows
only. Here both run under Wine, and the package is reached through an rpyc
server started by scripts/mt5_bridge.sh on 127.0.0.1. Calls go over as one
pickled blob and come back as one: the terminal's results are namedtuples of
a module this side does not have, so the far side turns them into dicts
before they cross, and one round trip per call is what keeps the bridge from
costing a round trip per field.

Demo only, and enforced here rather than trusted to configuration: after the
login the account's trade mode is read back, and anything but a demo account
is logged out of and refused. MT5_ALLOW_REAL is the one switch, and it is a
constant in etc/settings.py on purpose - not an environment variable a shell
can leave behind.

Times. MetaTrader reports every bar and deal in the *broker's* clock, passed
off as UTC, and takes an order's expiration in that clock too. The broker's
offset is read from the newest tick when the market is open, and falls back
to MT5_SERVER_UTC_OFFSET when it is not; see serverOffset().
"""

import datetime
import logging
import os
import pickle
import threading

from parity_deriva.etc import settings


class MT5Error(Exception):
	pass


# The package's constants, fixed by the terminal's API. Written out rather
# than read from the far side so that this module imports without a bridge.
TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
TRADE_ACTION_REMOVE = 8

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5

ORDER_TIME_GTC = 0
ORDER_TIME_SPECIFIED = 2
SYMBOL_EXPIRATION_SPECIFIED = 4

ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

ORDER_STATE_CANCELED = 2
ORDER_STATE_PARTIAL = 3
ORDER_STATE_FILLED = 4
ORDER_STATE_REJECTED = 5
ORDER_STATE_EXPIRED = 6

DEAL_ENTRY_IN = 0
DEAL_REASON_SL = 4
DEAL_REASON_TP = 5
DEAL_REASON_SO = 6

TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010
RETCODE_OK = frozenset([TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE,
						TRADE_RETCODE_DONE_PARTIAL])

ACCOUNT_TRADE_MODE_DEMO = 0


# What runs on the far side: one entry point that unpickles the arguments,
# makes the call, and pickles the answer with every namedtuple made a dict.
_REMOTE = r'''
import pickle, MetaTrader5 as _mt5
def _plain(x):
	if hasattr(x, '_asdict'):
		return dict((k, _plain(v)) for k, v in x._asdict().items())
	if isinstance(x, tuple):
		return [_plain(i) for i in x]
	return x
def _pd_call(blob):
	name, args, kwargs = pickle.loads(blob)
	result = getattr(_mt5, name)(*args, **kwargs)
	return pickle.dumps((_plain(result), _mt5.last_error()))
'''


def _setting(setup, key, default=None):
	return getattr(setup if setup is not None else settings, key, default)


def terminals(setup=None):
	"""
	Every MT5 account this stack can trade, one terminal each.

	A terminal is logged into one account at a time and the package talks
	to one terminal per process, so N accounts are N terminals, each behind
	a bridge of its own (scripts/mt5_bridge.sh <port>): MT5_TERMINALS lists
	them as {'credentials' (a file) or 'login'/'password'/'server', 'bridge',
	'terminal', 'portable'}. Unset, the one terminal the MT5_* settings
	describe.
	"""
	listed = _setting(setup, 'MT5_TERMINALS', None)
	if listed:
		return [dict(entry) for entry in listed]
	return [{'login': _setting(setup, 'MT5_LOGIN', ''),
			 'password': _setting(setup, 'MT5_PASSWORD', ''),
			 'server': _setting(setup, 'MT5_SERVER', ''),
			 'credentials': _setting(setup, 'MT5_CREDENTIALS', ''),
			 'bridge': _setting(setup, 'MT5_BRIDGE', '127.0.0.1:18812'),
			 'terminal': _setting(setup, 'MT5_TERMINAL', None),
			 'portable': False}]


def terminal(setup=None):
	"""
	The terminal this process trades on: the one logged into MT5_ACCOUNT
	(which web/livesessions sets per session), else the first.
	"""
	entries = terminals(setup)
	wanted = str(_setting(setup, 'MT5_ACCOUNT', '') or '')
	if not wanted:
		return entries[0]
	for entry in entries:
		try:
			if str(credentials(setup, entry)[0]) == wanted:
				return entry
		except MT5Error:
			continue
	raise MT5Error("no MT5 terminal is set up for account %s (MT5_TERMINALS)" % wanted)


def credentials(setup=None, entry=None):
	"""
	(login, password, server) for one terminal (the first by default).

	The entry's login, password and server when set; otherwise the file its
	'credentials' names, read as `key: value` or `key=value` lines (login,
	password, server - any case) or, failing that, as three bare lines in
	that order. Nothing read is ever put in a message: an error names the
	field that is missing, not what was found.
	"""
	entry = entry if entry is not None else terminals(setup)[0]
	login = entry.get('login') or ''
	password = entry.get('password') or ''
	server = entry.get('server') or ''
	path = entry.get('credentials') or ''
	if not (login and password and server) and path and os.path.exists(path):
		with open(path) as handle:
			lines = [l.strip() for l in handle if l.strip()]
		found = {}
		for line in lines:
			for sep in (':', '='):
				if sep in line:
					key, value = line.split(sep, 1)
					found[key.strip().lower()] = value.strip()
					break
		if not {'login', 'password', 'server'} <= set(found) and len(lines) == 3:
			found = dict(zip(('login', 'password', 'server'), lines))
		login = login or found.get('login', '')
		password = password or found.get('password', '')
		server = server or found.get('server', '')
	missing = [n for n, v in (('login', login), ('password', password),
							  ('server', server)) if not v]
	if missing:
		raise MT5Error("MT5 credentials missing: %s (MT5_LOGIN/MT5_PASSWORD/"
					   "MT5_SERVER, or the file MT5_CREDENTIALS)" % ", ".join(missing))
	try:
		login = int(login)
	except ValueError:
		raise MT5Error("MT5 login is not a number")
	return login, password, server


class MT5(object):
	"""
	The terminal, through the bridge: `api.order_send(request)` and so on,
	each answering (result, last_error) with results as plain dicts/lists.

	One per process (see connect()), behind a lock: a stack polls from
	several threads, and one rpyc connection serves one request at a time.
	"""

	def __init__(self, setup=None, entry=None):
		self.setup = setup if setup is not None else settings
		self.entry = entry if entry is not None else terminal(self.setup)
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self.lock = threading.RLock()
		self.conn = None
		self.offset = None
		self.demo = None

	def open(self):
		import rpyc
		host, _, port = str(self.entry.get('bridge') or '127.0.0.1:18812').partition(':')
		try:
			# the first initialize starts the terminal, which takes a while
			self.conn = rpyc.classic.connect(host, int(port or 18812))
			self.conn._config['sync_request_timeout'] = 180
		except OSError as exc:
			raise MT5Error("no MT5 bridge on %s:%s (%s) - start scripts/mt5_bridge.sh %s"
						   % (host, port, exc, port))
		self.conn.execute(_REMOTE)
		self.remote = self.conn.namespace['_pd_call']

		login, password, server = credentials(self.setup, self.entry)
		ok, error = self.call('initialize', self.entry.get('terminal'),
							  login=login, password=password, server=server,
							  timeout=60000, portable=bool(self.entry.get('portable')))
		if not ok:
			raise MT5Error("MT5 initialize/login failed: %s" % (error,))
		account, _ = self.call('account_info')
		self.demo = (account or {}).get('trade_mode') == ACCOUNT_TRADE_MODE_DEMO
		if not self.demo and not _setting(self.setup, 'MT5_ALLOW_REAL', False):
			self.call('shutdown')
			raise MT5Error("MT5 account is not a demo account; refusing it "
						   "(MT5_ALLOW_REAL is False)")
		self.logger.info("MT5 connected, %s account, %s"
						 % ("demo" if self.demo else "REAL",
							(account or {}).get('currency')))
		return self

	def call(self, name, *args, **kwargs):
		with self.lock:
			blob = self.remote(pickle.dumps((name, args, kwargs)))
			return pickle.loads(blob)

	# The documented order_send(request) is refused by this build of the
	# package ("Unnamed arguments not allowed"), and order_send(request=...)
	# is taken as an empty request; the fields have to go as keywords.
	def order_send(self, request):
		return self.call('order_send', **request)

	def order_check(self, request):
		return self.call('order_check', **request)

	def __getattr__(self, name):
		if name.startswith('_'):
			raise AttributeError(name)
		return lambda *a, **k: self.call(name, *a, **k)

	# ------------------------------------------------------------------ time

	def serverOffset(self, symbol):
		"""
		Broker clock minus UTC, as a timedelta.

		Read off the newest tick while the market is open and kept: brokers
		move by an hour at DST, which a restart picks up. With the market shut
		the tick is stale, so MT5_SERVER_UTC_OFFSET (hours) answers instead.
		"""
		if self.offset is not None:
			return self.offset
		tick, _ = self.call('symbol_info_tick', symbol)
		now = datetime.datetime.now(datetime.timezone.utc).timestamp()
		if tick and tick.get('time'):
			delta = tick['time'] - now
			# a live tick is at most seconds old; the offset is whole
			# half-hours, so rounding to those removes the tick's own age
			half_hours = round(delta / 1800.0)
			if abs(half_hours) <= 28 and abs(delta - half_hours * 1800) < 600:
				self.offset = datetime.timedelta(minutes=30 * half_hours)
				self.logger.info("MT5 server clock is UTC%+.1fh" % (half_hours / 2.0))
				return self.offset
		hours = float(_setting(self.setup, 'MT5_SERVER_UTC_OFFSET', 0) or 0)
		self.logger.warning("MT5 market looks closed; server offset from "
							"MT5_SERVER_UTC_OFFSET = %s h" % hours)
		return datetime.timedelta(hours=hours)

	def toUTC(self, seconds, symbol):
		"""A broker timestamp as a naive UTC datetime."""
		return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(seconds)) \
			- self.serverOffset(symbol)

	def toServer(self, when, symbol):
		"""A naive UTC datetime as a broker timestamp."""
		return int(((when + self.serverOffset(symbol)) - datetime.datetime(1970, 1, 1))
				   .total_seconds())


_connections = {}
_connection_lock = threading.Lock()


def connect(setup=None, entry=None):
	"""
	A logged-in terminal, opened on first use and kept per bridge: `entry`
	(one of terminals()), or the one this process trades on.
	"""
	entry = entry if entry is not None else terminal(setup)
	key = entry.get('bridge') or '127.0.0.1:18812'
	with _connection_lock:
		if key not in _connections:
			_connections[key] = MT5(setup, entry).open()
		return _connections[key]


# ---------------------------------------------------------------- naming

def symbol(instrument, setup=None):
	"""
	The broker's symbol for this project's instrument name.

	'EUR_USD' is 'EURUSD' at most brokers; where one adds a suffix or names
	an index its own way, MT5_INSTRUMENTS says so.
	"""
	entry = (_setting(setup, 'MT5_INSTRUMENTS', {}) or {}).get(instrument) or {}
	return entry.get('symbol') or instrument.replace('_', '') + _suffix(setup)


def _suffix(setup=None):
	"""What the trading terminal's broker adds to a symbol (OANDA: '.pro')."""
	try:
		return terminal(setup).get('suffix') or ''
	except MT5Error:
		return ''


def instrumentName(sym, setup=None):
	suffix = _suffix(setup)
	if suffix and sym.endswith(suffix):
		sym = sym[:-len(suffix)]
	for name, entry in (_setting(setup, 'MT5_INSTRUMENTS', {}) or {}).items():
		if (entry or {}).get('symbol') == sym:
			return name
	if len(sym) == 6 and sym.isalpha():
		return sym[:3] + '_' + sym[3:]
	return sym


def pricePrecision(instrument, setup=None):
	from parity_deriva.lib.utils import pricePrecision as shared
	entry = (_setting(setup, 'MT5_INSTRUMENTS', {}) or {}).get(instrument) or {}
	if entry.get('precision') is not None:
		return int(entry['precision'])
	return shared(instrument, setup)


def timeframe(granularity):
	"""This project's granularity ('M5', 'H1', 'D') as an MT5 timeframe."""
	g = str(granularity).lstrip('/')
	n = g[1:]
	if g[:1] == 'M' and n in ('1', '2', '3', '4', '5', '6', '10', '12', '15', '20', '30'):
		return int(n)
	if g[:1] == 'H' and n in ('1', '2', '3', '4', '6', '8', '12'):
		return 0x4000 | int(n)
	if g in ('D', 'D1'):
		return 0x4000 | 24
	if g in ('W', 'W1'):
		return 0x8000 | 1
	raise MT5Error("MT5 has no timeframe for %r" % granularity)


def volume(units, info):
	"""
	Units as lots for this symbol: contract size and step from the broker's
	own symbol_info, so nothing about lot sizes is configured by hand.
	Rounded down to the step; None when that leaves less than the minimum.
	"""
	size = float(info.get('trade_contract_size') or 1)
	step = float(info.get('volume_step') or 0.01)
	lots = int(abs(float(units)) / size / step + 1e-9) * step
	lots = min(round(lots, 8), float(info.get('volume_max') or lots))
	if lots < float(info.get('volume_min') or step):
		return None
	return lots


if __name__ == '__main__':
	# self-check of the pure parts; no bridge needed
	assert symbol('EUR_USD') == 'EURUSD' and instrumentName('EURUSD') == 'EUR_USD'
	assert timeframe('M5') == 5 and timeframe('H1') == 16385 and timeframe('D') == 16408
	info = {'trade_contract_size': 100000, 'volume_step': 0.01,
			'volume_min': 0.01, 'volume_max': 100}
	assert volume(12345, info) == 0.12 and volume(-100000, info) == 1.0
	assert volume(500, info) is None
	print("ok")
