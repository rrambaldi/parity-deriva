"""
The eToro public API: credentials, routes, pacing, and the naming it needs.

Everything in here exists because eToro asks for things OANDA does not:

* a unique **x-request-id** GUID on every single request, which on the order
  routes doubles as the idempotency key and comes back as ``referenceId``.
  That is a gift rather than a chore: derive it from the signal that produced
  the order and the same order cannot be placed twice, the fill arrives
  already carrying the key its simulated counterpart is filed under, and a
  replay of the same candles produces the same ids. See requestId().
* **demo and real are different paths**, not different hosts, and the point
  where ``/demo`` goes differs per route family
  (``/trading/execution/demo/orders`` but ``/trading/info/trade/demo/history``).
  A clever transform would get one of them wrong, so ROUTES spells out both
  variants of every route this project uses.
* **published rate limits**, shared across groups of endpoints rather than
  per endpoint. A 429 costs a candle or, worse, an order, so the client paces
  itself against the pool it is about to spend from.
* **numeric instrument ids**. The project names instruments the way OANDA
  does ('EUR_USD'); eToro wants an id, and the mapping is not derivable.

What is deliberately absent: any default for the API host, and any invented
instrument id. Both must be configured, and an omission is a refusal to
start rather than a guess - the same rule the parity thresholds follow.
"""

import collections
import json
import logging
import time
import uuid

import requests

from parity_deriva.etc import settings


class EToroError(Exception):
	"""A configuration or naming problem that cannot be papered over."""


#: Namespace for the deterministic request ids derived from a signal. Fixed
#: for the life of the project: change it and every id changes with it, which
#: would break idempotency against orders already placed.
REQUEST_NAMESPACE = uuid.UUID('6f9619ff-8b86-d011-b42d-00c04fc964ff')


#: This project's granularities as eToro spells them. The ones missing from
#: the right-hand side are missing from the API: it has no sub-minute
#: interval and no odd multiples, so 'S5' or 'H3' cannot be served and are
#: refused rather than rounded to something near.
INTERVALS = {
	'M1': 'OneMinute',
	'M5': 'FiveMinutes',
	'M10': 'TenMinutes',
	'M15': 'FifteenMinutes',
	'M30': 'ThirtyMinutes',
	'H1': 'OneHour',
	'H4': 'FourHours',
	'D': 'OneDay',
	'W': 'OneWeek',
}


#: (real, demo) path for every route this project uses. Market data is not
#: split by account type, so both entries are the same there.
ROUTES = {
	'candles': (
		'/api/v1/market-data/instruments/%s/history/candles/%s/%s/%s',
		'/api/v1/market-data/instruments/%s/history/candles/%s/%s/%s'),
	'rates': (
		'/api/v2/market-data/rates',
		'/api/v2/market-data/rates'),
	'instruments': (
		'/api/v2/market-data/instruments',
		'/api/v2/market-data/instruments'),
	'create_order': (
		'/api/v2/trading/execution/orders',
		'/api/v2/trading/execution/demo/orders'),
	'cancel_order': (
		'/api/v2/trading/execution/orders/%s',
		'/api/v2/trading/execution/demo/orders/%s'),
	'close_position': (
		'/api/v1/trading/execution/market-close-orders/positions/%s',
		'/api/v1/trading/execution/demo/market-close-orders/positions/%s'),
	'order_lookup': (
		'/api/v2/trading/info/orders:lookup',
		'/api/v2/trading/info/demo/orders:lookup'),
	'trade_history': (
		'/api/v1/trading/info/trade/history',
		'/api/v1/trading/info/trade/demo/history'),
	'portfolio': (
		'/api/v1/trading/info/portfolio',
		'/api/v1/trading/info/demo/portfolio'),
}


#: Which published quota each route spends from, as (limit, window seconds).
#: These are the documented figures, and they are shared: 'market' is one
#: budget for eleven market-data endpoints, 'write' one for every execution
#: endpoint. Pacing per pool rather than per route is therefore the only
#: pacing that corresponds to what the API actually enforces.
POOLS = {
	'market': (120, 60),
	'write': (20, 60),
	'orderinfo': (60, 60),
	'default': (60, 60),
}

ROUTE_POOL = {
	'candles': 'market',
	'rates': 'market',
	'instruments': 'market',
	'create_order': 'write',
	'cancel_order': 'write',
	'close_position': 'write',
	'order_lookup': 'orderinfo',
	'trade_history': 'default',
	'portfolio': 'default',
}


def interval(granularity):
	"""
	This project's granularity as eToro spells it.

	Refuses anything the API does not serve. Rounding 'H3' to 'FourHours'
	would hand a strategy bars of a period it did not ask for, and every
	level it derived from them would be wrong by an unknown amount.
	"""
	if granularity in INTERVALS:
		return INTERVALS[granularity]
	raise EToroError(
		"eToro has no candle interval for %r; it serves %s"
		% (granularity, ", ".join(sorted(INTERVALS))))


def requestId(*parts):
	"""
	A GUID that is a function of what it identifies.

	eToro requires an x-request-id GUID on every request and treats it as the
	idempotency key on the order routes, echoing it back as referenceId. A
	uuid4 would satisfy the format and throw away everything useful: a
	deterministic id means the same order cannot be placed twice by a retry,
	and a replay of the same candles produces the same ids as the live run,
	which is what makes the two comparable at all.

	The first of those is enforced by eToro rather than merely intended.
	Re-sending an order with the same derived key was answered:

	    400 Validation failed: ReferenceID <key> may already exists
	        for CID <account> and OrderID <the original>

	so a retry is refused, not duplicated, and execution/etoro.py treats that
	400 as a rejection and publishes no second acknowledgement.

	What does NOT work, though the OpenAPI offers it, is finding the order
	again by that key: GET .../orders:lookup?referenceId=<key> answered 404
	"No external operation was found" on the demo account, minutes after the
	order was accepted, while the same route answered 200 for the same order
	by orderId. Note also that the 400 above named a different order id than
	the create call returned, so eToro appears to key its external-operation
	record on something other than the public order id. Nothing here relies
	on it - data/etoro.EToroTransactions polls by orderId, which it keeps
	from the acknowledgement.

	Called with no parts it does return a uuid4, for the reads where there is
	nothing to be idempotent about.
	"""
	if not parts:
		return str(uuid.uuid4())
	return str(uuid.uuid5(REQUEST_NAMESPACE, ":".join(str(p) for p in parts)))


def instrument(name, setup=None):
	"""
	The eToro identity of an instrument this project names OANDA-style.

	Not derivable: 'DE30_EUR' is eToro's 'GER40' with a numeric id that only
	eToro knows, so ETORO_INSTRUMENTS has to be filled in. An unmapped
	instrument raises, naming the script that resolves it, because the
	alternative - trading whatever id happened to be nearby - is unbounded.
	"""
	cfg = setup if setup is not None else settings
	table = getattr(cfg, 'ETORO_INSTRUMENTS', {}) or {}
	if name in table:
		entry = table[name]
		if entry.get('instrumentId') is None and not entry.get('symbol'):
			raise EToroError(
				"ETORO_INSTRUMENTS[%r] has neither instrumentId nor symbol" % name)
		return entry
	raise EToroError(
		"%s is not in ETORO_INSTRUMENTS. Resolve its eToro id with "
		"'python scripts/etoro_instruments.py %s' and add it to "
		"etc/settings.py" % (name, name))


def instrumentId(name, setup=None):
	entry = instrument(name, setup)
	if entry.get('instrumentId') is None:
		raise EToroError(
			"ETORO_INSTRUMENTS[%r] has no instrumentId; the candle and rate "
			"routes are keyed by id, not by symbol" % name)
	return int(entry['instrumentId'])


def instrumentName(identifier, setup=None):
	"""The project's name for an eToro instrument id, or None."""
	cfg = setup if setup is not None else settings
	table = getattr(cfg, 'ETORO_INSTRUMENTS', {}) or {}
	for name, entry in table.items():
		if entry.get('instrumentId') is not None \
				and int(entry['instrumentId']) == int(identifier):
			return name
	return None


def pricePrecision(instrument_name, setup=None):
	"""
	Decimal places to round an order price to on eToro.

	Precision is a property of the market rather than of the broker, so the
	shared INSTRUMENT_PRECISION table applies; an ETORO_INSTRUMENTS entry may
	override it per instrument where eToro disagrees.
	"""
	from parity_deriva.lib.utils import pricePrecision as shared
	cfg = setup if setup is not None else settings
	table = getattr(cfg, 'ETORO_INSTRUMENTS', {}) or {}
	entry = table.get(instrument_name) or {}
	if entry.get('precision') is not None:
		return int(entry['precision'])
	return shared(instrument_name, cfg)


class SpreadModel(object):
	"""
	Bid and ask for a broker that only serves one price per candle.

	OANDA serves three OHLC series per candle and the strategies read them:
	AG01 buys the high of the ask and stops out at the low of the bid. eToro
	serves one series, so those two prices do not exist in its data and
	there is no measurement that recovers them - one number cannot be told
	how far apart two others were.

	So this is a *model*, and it is off unless configured. ETORO_SPREAD is
	None by default, which leaves candles carrying mid only and makes the
	provider decline bid_ask_candles, so a wiring that needs them refuses to
	start and says why. Set it and the same wiring runs on a stated
	assumption instead of a silent one.

	Configure it as a number in the instrument's own units, applied as half
	either side of the served price, or as a dict per instrument:

	    ETORO_SPREAD = 0.0001
	    ETORO_SPREAD = {'EUR_USD': 0.00008, 'DE30_EUR': 1.2}

	Whatever you set, it is a constant standing in for something that varies
	by the hour, so a real spread wider than the one configured shows up as
	the strategy filling at prices it did not expect. That is precisely what
	trading/parity.py measures - the model belongs under the alarm, not
	above it.
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


def spreadModel(setup=None):
	cfg = setup if setup is not None else settings
	return SpreadModel(getattr(cfg, 'ETORO_SPREAD', None))


class RateLimiter(object):
	"""
	Keeps a pool's request rate under its published limit.

	Counts what it has spent in the trailing window and sleeps before the
	call that would exceed it, rather than discovering the limit by being
	refused: on the write pool a 429 is a lost order, and on the market pool
	it is a missing candle that a strategy will never see.

	Retry-After from a 429 is honoured through penalise(), so a limit
	enforced ahead of the API - where no RateLimit headers reach us - still
	slows the client down.
	"""

	def __init__(self, limit, window, sleep=time.sleep, clock=time.time):
		self.limit = limit
		self.window = window
		self.calls = collections.deque()
		self.until = 0.0
		self._sleep = sleep
		self._clock = clock

	def _prune(self, now):
		while self.calls and now - self.calls[0] >= self.window:
			self.calls.popleft()

	def take(self):
		now = self._clock()
		if self.until > now:
			self._sleep(self.until - now)
			now = self._clock()
		self._prune(now)
		if len(self.calls) >= self.limit:
			wait = self.window - (now - self.calls[0])
			if wait > 0:
				self._sleep(wait)
				now = self._clock()
				self._prune(now)
		self.calls.append(now)

	def penalise(self, seconds):
		"""Refuse to spend anything for this many seconds (a 429's Retry-After)."""
		try:
			seconds = float(seconds)
		except (TypeError, ValueError):
			return
		if seconds > 0:
			self.until = self._clock() + seconds


class EToroAPI(object):
	"""
	One HTTP client for the eToro public API.

	Responses come back as (status, payload) and are never raised on: a 4xx
	is a fact about the account or the order, and the handlers above turn it
	into a log line and a StatusEvent the way they already do for OANDA. What
	*is* raised on is a configuration that cannot produce a request at all -
	no host, or both authentication modes at once, which eToro answers with a
	422 that says nothing useful.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		setup = args.get('setup')
		self.setup = setup if setup is not None else settings

		self.domain = getattr(self.setup, 'ETORO_API_DOMAIN', '') or ''
		if not self.domain:
			raise EToroError(
				"ETORO_API_DOMAIN is not set. The public API host is not "
				"guessable and is not going to be guessed here: take it from "
				"your eToro developer account and export ETORO_API_DOMAIN.")

		self.token = getattr(self.setup, 'ETORO_ACCESS_TOKEN', '') or ''
		self.user_key = getattr(self.setup, 'ETORO_USER_KEY', '') or ''
		self.api_key = getattr(self.setup, 'ETORO_API_KEY', '') or ''
		if self.token and (self.user_key or self.api_key):
			raise EToroError(
				"set either ETORO_ACCESS_TOKEN or the ETORO_USER_KEY / "
				"ETORO_API_KEY pair, not both: a request carrying an "
				"Authorization header together with a key header is rejected "
				"with 422.")

		# 'practice' is the demo account, matching DOMAIN's existing meaning
		self.demo = str(getattr(self.setup, 'DOMAIN', 'practice')) != 'real'
		self.limiters = dict(
			(name, RateLimiter(limit, window))
			for name, (limit, window) in POOLS.items())
		self.verify = bool(getattr(self.setup, 'ETORO_VERIFY_TLS', True))

	# ---------------------------------------------------------------- routes

	def route(self, key, *parts):
		"""The path for a route, demo or real, with its parameters filled in."""
		if key not in ROUTES:
			raise EToroError("unknown eToro route %r" % key)
		path = ROUTES[key][1 if self.demo else 0]
		if parts:
			path = path % tuple(str(p) for p in parts)
		return path

	def url(self, key, *parts):
		return "https://%s%s" % (self.domain, self.route(key, *parts))

	def headers(self, request_id):
		out = {
			'Content-Type': 'application/json',
			'x-request-id': request_id,
		}
		if self.token:
			out['Authorization'] = 'Bearer ' + self.token
		elif self.user_key or self.api_key:
			out['x-user-key'] = self.user_key
			out['x-api-key'] = self.api_key
		# Neither configured is left to fail as a 401, the way an empty
		# OANDA token does, so a misconfigured account reports an error
		# through the event stream instead of refusing to boot.
		return out

	# ----------------------------------------------------------------- calls

	def call(self, method, key, parts=(), params=None, body=None,
			 request_id=None):
		"""
		One request. Returns (status_code, payload) with payload None when
		there is nothing parseable, and (None, None) when the request could
		not be made at all.
		"""
		rid = request_id or requestId()
		pool = self.limiters[ROUTE_POOL.get(key, 'default')]
		pool.take()

		url = self.url(key, *parts)
		try:
			requests.packages.urllib3.disable_warnings()
			session = requests.Session()
			req = requests.Request(method, url,
								   headers=self.headers(rid),
								   params=params,
								   data=json.dumps(body) if body is not None else None)
			resp = session.send(req.prepare(), stream=False, verify=self.verify)
		except Exception as exc:
			self.logger.error("eToro %s %s failed: %s" % (method, key, str(exc)))
			return None, None

		status = getattr(resp, 'status_code', None)
		if status == 429:
			retry = (getattr(resp, 'headers', {}) or {}).get('Retry-After')
			pool.penalise(retry if retry is not None else POOLS['default'][1])
			self.logger.error(
				"eToro %s rate limited, holding off %s s" % (key, retry))

		payload = None
		text = getattr(resp, 'text', '') or ''
		if text:
			try:
				payload = json.loads(text)
			except ValueError:
				self.logger.error("eToro %s returned unparseable body" % key)
		return status, payload

	def get(self, key, parts=(), params=None, request_id=None):
		return self.call('GET', key, parts, params=params, request_id=request_id)

	def post(self, key, parts=(), body=None, request_id=None):
		return self.call('POST', key, parts, body=body, request_id=request_id)

	def delete(self, key, parts=(), request_id=None):
		return self.call('DELETE', key, parts, request_id=request_id)

	# ------------------------------------------------------------ resolution

	def resolve(self, symbol):
		"""
		Look a ticker up, so a mapping can be recorded rather than invented.

		Used by scripts/etoro_instruments.py: the result is meant to be read
		by a person and pasted into ETORO_INSTRUMENTS, not cached and trusted
		at runtime, because an id that silently changed between sessions
		would move the trading to a different market.
		"""
		status, payload = self.get('instruments', params={'symbols': symbol})
		if status not in (200, 206) or not payload:
			self.logger.error("eToro instrument lookup for %s: status %s"
							  % (symbol, status))
			return []
		rows = payload.get('instruments') or payload.get('results') or payload
		if isinstance(rows, dict):
			rows = [rows]
		return rows or []


def utcnow():
	"""
	Now, as the naive UTC datetime candleTime() produces.

	datetime.today() is local, and eToro timestamps everything in UTC. Mixing
	the two silently shifts every comparison by the machine's offset: on a
	CEST host it made every candle look two hours older than it was, so the
	bar still forming was emitted as complete and a strategy would have
	signalled on a high that was not yet the high. Anything comparing against
	a value that came through candleTime() has to come through here.
	"""
	import datetime as _dt
	return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def candleTime(value):
	"""
	An eToro ISO timestamp as the naive UTC datetime this project uses.

	OANDA's "...Z" times are parsed into naive datetimes everywhere else, and
	those get compared against datetime.today(). eToro's timestamps carry an
	offset, and fromisoformat() honours it, so parsing them the obvious way
	yields aware datetimes that raise TypeError the first time they meet a
	naive one. Convert to UTC and drop the offset, keeping one convention.
	"""
	import datetime as _dt
	if isinstance(value, _dt.datetime):
		dt = value
	else:
		try:
			dt = _dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
		except (TypeError, ValueError):
			return _dt.datetime(1970, 1, 1, 0, 0, 0)
	if dt.tzinfo is not None:
		dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
	return dt
