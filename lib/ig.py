"""
The IG REST trading API: session tokens, routes, pacing, and epics.

IG sits between the two brokers already here. Like OANDA it serves a real bid
and a real ask for every candle, tells a STOP from a LIMIT, and accepts an
expiry on a resting order; like eToro it answers an order with a reference
rather than an outcome, and never says which leg closed a trade. What it asks
of a client that neither of the others does:

* **a session, not a token.** OANDA and eToro authenticate every request on
  its own. IG issues session tokens from POST /session and every later
  request carries them, so the client logs in lazily on its first call and
  again when the tokens are refused. Two schemes exist and both are here:
  version 2 returns ``CST`` and ``X-SECURITY-TOKEN`` *headers*, version 3
  returns an OAuth pair whose access token is short-lived - the response says
  how short in ``expires_in``, and that figure is read rather than assumed.
* **a Version header per endpoint.** Not per API: /prices is version 3 while
  /confirms is version 1 and /positions/otc is version 2, and sending the
  wrong number gets a schema from another era. ROUTES carries the version
  next to the path so the two cannot drift apart.
* **demo and live are different hosts** - demo-api.ig.com against api.ig.com -
  and an API key belongs to one of them. This is the one place IG is simpler
  than eToro, where the two are different paths per route family.
* **epics.** IG's name for EUR/USD is something like CS.D.EURUSD.MINI.IP, it
  differs by account type and product, and nothing derives it from 'EUR_USD'.
  So it is configuration, like eToro's numeric ids.
* **a weekly budget for history.** The per-minute request limits are the
  usual kind and are paced against. The historical-price allowance is not: it
  meters *data points*, 10,000 a week, shared by everything using the key. No
  local counter can track that, so what is tracked instead is what IG reports
  back in the metadata of each price response. See Allowance in
  lib/ratelimit.py for why that is watched rather than enforced.

What is deliberately absent: any invented epic, and any Lightstreamer client.
IG does push prices and confirmations, over Lightstreamer, and that is a
protocol and a dependency this project does not carry. The provider therefore
declares price_stream and transaction_stream false - a capability describes
what this stack can do, not what the broker's documentation mentions.
"""

import hashlib
import json
import logging

import requests

from parity_deriva.etc import settings
from parity_deriva.lib.ratelimit import Allowance
from parity_deriva.lib.ratelimit import RateLimiter


class IGError(Exception):
	"""A configuration or naming problem that cannot be papered over."""


#: The two hosts. An API key is issued against one of them, so DOMAIN picks
#: the host and the key has to match it; a live key against the demo host is
#: answered with an authentication failure rather than with data.
HOSTS = {
	'real': 'api.ig.com',
	'practice': 'demo-api.ig.com',
}

#: Everything hangs off this prefix.
BASE = '/gateway/deal'


#: This project's granularities as IG spells them. IG has SECOND but no other
#: sub-minute resolution, so 'S5' cannot be served and is refused rather than
#: rounded to something near. MONTH exists at IG and has no spelling here -
#: this project's 'M' prefix already means minutes.
RESOLUTIONS = {
	'S1': 'SECOND',
	'M1': 'MINUTE',
	'M2': 'MINUTE_2',
	'M3': 'MINUTE_3',
	'M5': 'MINUTE_5',
	'M10': 'MINUTE_10',
	'M15': 'MINUTE_15',
	'M30': 'MINUTE_30',
	'H1': 'HOUR',
	'H2': 'HOUR_2',
	'H3': 'HOUR_3',
	'H4': 'HOUR_4',
	'D': 'DAY',
	'W': 'WEEK',
}


#: (path, version) for every route this project uses. The version is part of
#: the route rather than a parameter of the call: /prices is version 3 and
#: /confirms is version 1, and a route that changed version without its
#: handler changing with it would parse a different schema into the same
#: fields.
ROUTES = {
	'session': ('/session', 2),
	'session_v3': ('/session', 3),
	'refresh': ('/session/refresh-token', 1),
	'logout': ('/session', 1),
	'accounts': ('/accounts', 1),
	'switch': ('/session', 1),
	'search': ('/markets', 1),
	'market': ('/markets/%s', 3),
	'prices': ('/prices/%s', 3),
	'positions': ('/positions', 2),
	'open_position': ('/positions/otc', 2),
	'close_position': ('/positions/otc', 1),
	'workingorders': ('/workingorders', 2),
	'create_order': ('/workingorders/otc', 2),
	'cancel_order': ('/workingorders/otc/%s', 2),
	'confirm': ('/confirms/%s', 1),
	'transactions': ('/history/transactions', 2),
	'activity': ('/history/activity', 3),
}


#: Which published quota each route spends from, as (limit, window seconds).
#: IG counts trading requests separately from the rest and applies the tighter
#: figure per account: 100 a minute for anything that deals, 30 a minute for
#: everything else on one account, 60 a minute for the application. The
#: non-trading pool is paced at the per-account figure because that is the
#: first one a single running stack will hit.
#:
#: What is NOT in here is the historical-price allowance. It meters data
#: points by the week, not requests by the minute, and it is shared with every
#: other application using the same key - so it is watched through what IG
#: reports back, not counted locally. See Allowance.
POOLS = {
	'trading': (100, 60),
	'nontrading': (30, 60),
	'default': (30, 60),
}

ROUTE_POOL = {
	'open_position': 'trading',
	'close_position': 'trading',
	'create_order': 'trading',
	'cancel_order': 'trading',
	# everything else, including /confirms and /prices, is non-trading
}


def resolution(granularity):
	"""
	This project's granularity as IG spells it.

	Refuses anything IG does not serve. Rounding 'S5' to 'SECOND' would hand
	a strategy bars of a period it did not ask for, and every level it
	derived from them would be wrong by an unknown amount.
	"""
	if granularity in RESOLUTIONS:
		return RESOLUTIONS[granularity]
	raise IGError(
		"IG has no price resolution for %r; it serves %s"
		% (granularity, ", ".join(sorted(RESOLUTIONS))))


def dealReference(*parts):
	"""
	A deal reference that is a function of what it identifies.

	IG lets the client name a deal - ``dealReference`` on the order body,
	matching ``[A-Za-z0-9_-]{1,30}`` - and echoes the name back on the
	confirmation. Deriving it from the signal rather than letting IG generate
	one means the confirmation arrives already carrying the key its simulated
	counterpart is filed under, and a replay of the same candles produces the
	same references as the live run, which is what makes the two comparable.

	It is NOT claimed here to be an idempotency key. eToro's x-request-id is
	one, and refuses a repeat with a 400 that names the original order;
	whether IG refuses a repeated dealReference has not been established
	against an account, so execution/ig.py does not rely on it and treats a
	resend as a resend.

	The signal number is hashed rather than trimmed because the raw key -
	'AG01:EUR_USD:H1:20180115T010000' - is both too long and full of colons
	IG would reject. Hashing keeps the property that matters, which is that
	the same signal always yields the same reference.
	"""
	if not parts:
		raise IGError("a deal reference has to be derived from something")
	raw = ":".join(str(p) for p in parts).encode('utf-8')
	return "PD" + hashlib.sha1(raw).hexdigest()[:28]


def instrument(name, setup=None):
	"""
	The IG identity of an instrument this project names OANDA-style.

	Not derivable: 'EUR_USD' is one epic on a demo CFD account and another on
	a live spread-bet account, and the difference is a product, not a
	spelling. An unmapped instrument raises, naming the script that resolves
	it, because the alternative - dealing on whatever epic a search happened
	to return first - is unbounded.
	"""
	cfg = setup if setup is not None else settings
	table = getattr(cfg, 'IG_INSTRUMENTS', {}) or {}
	if name in table:
		entry = table[name]
		if not entry.get('epic'):
			raise IGError("IG_INSTRUMENTS[%r] has no epic" % name)
		return entry
	raise IGError(
		"%s is not in IG_INSTRUMENTS. Resolve its epic with "
		"'python scripts/ig_instruments.py %s' and add it to "
		"etc/settings.py" % (name, name))


def epic(name, setup=None):
	return instrument(name, setup)['epic']


def instrumentName(ig_epic, setup=None):
	"""The project's name for an IG epic, or None."""
	cfg = setup if setup is not None else settings
	table = getattr(cfg, 'IG_INSTRUMENTS', {}) or {}
	for name, entry in table.items():
		if entry.get('epic') == ig_epic:
			return name
	return None


def expiry(name, setup=None):
	"""
	The contract an order is for.

	'-' is IG's own value for an instrument that does not expire, which is
	what a daily funded bet or a cash CFD is, and it is what most of this
	project's instruments are. A dated future has a real expiry and it has to
	be in the settings entry, because dealing the wrong contract month is
	dealing a different instrument.
	"""
	return instrument(name, setup).get('expiry', '-')


def currency(name, setup=None):
	"""
	The currency an order is denominated in.

	IG requires it on every deal. The instrument entry decides, then
	IG_CURRENCY, then the account's base currency - and none of those is
	guessed from the instrument's name: 'EUR_USD' can be dealt in either of
	them and the choice is the account's, not the pair's.
	"""
	cfg = setup if setup is not None else settings
	entry = instrument(name, cfg)
	if entry.get('currency'):
		return entry['currency']
	return (getattr(cfg, 'IG_CURRENCY', None)
			or getattr(cfg, 'BASE_CURRENCY', 'EUR'))


def scale(name, setup=None):
	"""
	What a served price has to be divided by to be a price. Normally nothing.

	This is deliberately NOT IG's ``scalingFactor``, and the difference was
	measured rather than reasoned about. On the demo account, EUR/USD reports
	scalingFactor 10000 while quoting a bid of 1.14625 and serving price rows
	of 1.14632 - so the factor is not a divisor for prices, and dividing by it
	would put every level four decimal places from where the market is. What
	it relates is *distances in points* to price units: the same market's
	minimum stop distance comes back as 2.0 POINTS, which is 0.0002. The DAX
	reports scalingFactor 1 and quotes 25630.8, which is the same rule seen
	from the other side.

	Nothing here reads that field. This is a manual override, keyed
	'priceDivisor' precisely so that pasting IG's own scalingFactor into an
	instrument entry cannot switch it on, and it is unset for every market
	this project deals. It exists for a market somebody measures a discrepancy
	on, and until then it stays out of the way.

	Orders are unaffected either way: execution/ig.py sends stopLevel and
	limitLevel, which are absolute prices. Were it to send stopDistance, the
	distance would be in points and the factor would matter.
	"""
	value = instrument(name, setup).get('priceDivisor')
	if value in (None, 0):
		return 1.0
	return float(value)


def pricePrecision(instrument_name, setup=None):
	"""
	Decimal places to round an order level to on IG.

	Precision is a property of the market rather than of the broker, so the
	shared INSTRUMENT_PRECISION table applies; an IG_INSTRUMENTS entry may
	override it per instrument where IG disagrees.
	"""
	from parity_deriva.lib.utils import pricePrecision as shared
	cfg = setup if setup is not None else settings
	table = getattr(cfg, 'IG_INSTRUMENTS', {}) or {}
	entry = table.get(instrument_name) or {}
	if entry.get('precision') is not None:
		return int(entry['precision'])
	return shared(instrument_name, cfg)


def utcnow():
	"""
	Now, as the naive UTC datetime dealTime() produces.

	datetime.today() is local. IG's price rows carry both a local
	snapshotTime and a snapshotTimeUTC, and this project reads the UTC one,
	so anything compared against a value that came through dealTime() has to
	come through here. Mixing the two silently shifts every comparison by the
	machine's offset, which on eToro made a forming bar look complete - the
	one thing the completeness check exists to prevent.
	"""
	import datetime as _dt
	return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def dealTime(value):
	"""
	An IG timestamp as the naive UTC datetime this project uses.

	IG spells time three ways depending on the endpoint: '2020/09/01 10:00:00'
	on a price row's local snapshotTime, '2020-09-01T10:00:00' on its
	snapshotTimeUTC, and an ISO instant with milliseconds on a confirmation.
	All three are accepted, and an offset - where one is present - is honoured
	and then dropped, keeping one convention.

	An unparseable value becomes the epoch rather than raising: a timestamp
	this code cannot read is a row that will be filtered out as too old, which
	is recoverable, where an exception in a polling loop is not.
	"""
	import datetime as _dt
	if isinstance(value, _dt.datetime):
		dt = value
	else:
		text = str(value).strip()
		dt = None
		for fmt in ('%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M', '%Y-%m-%d %H:%M:%S'):
			try:
				dt = _dt.datetime.strptime(text, fmt)
				break
			except (TypeError, ValueError):
				pass
		if dt is None:
			try:
				dt = _dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
			except (TypeError, ValueError):
				return _dt.datetime(1970, 1, 1, 0, 0, 0)
	if dt.tzinfo is not None:
		dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
	return dt


def priceTime(dt):
	"""A datetime as the from/to parameters of GET /prices want it."""
	return dt.strftime('%Y-%m-%dT%H:%M:%S')


def goodTillDate(dt):
	"""
	A datetime as ``goodTillDate`` wants it: IG's own format, in UTC.

	IG documents the field as yyyy/MM/dd hh:mm:ss rather than as an ISO
	instant, and says nothing that settles which clock reads it. It used to
	be sent as a local wall clock here, on the belief that the account's
	timezone applied. That was measured on the demo account, whose own clock
	runs two hours ahead of UTC - its position list serves createdDate 00:44
	beside createdDateUTC 22:44 - and it is wrong:

	* ninety minutes ahead in UTC, which is half an hour in the *past* on the
	  account's clock, was accepted and the order lived;
	* thirty minutes ahead, which is in the past in London, was accepted too;
	* ten minutes *behind* UTC was refused at once with
	  GOOD_TILL_DATE_IN_THE_PAST - so the check is made on acceptance and the
	  two survivals above mean what they look like.

	So the instant is converted rather than its digits copied. A datetime
	that carries an offset is converted from it; a naive one is already UTC,
	because the strategies build gtdTime from the candle's own timestamp
	(lib/utils.expiryAt) and the candles are UTC.

	Was: a naive value was read as the machine's local time, which is what
	     datetime.today() returned when the expiry came from the clock rather
	     than from the data. Passing a UTC instant through that shifted it by
	     the machine's offset.
	"""
	import datetime as _dt
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=_dt.timezone.utc)
	return dt.astimezone(_dt.timezone.utc).strftime('%Y/%m/%d %H:%M:%S')


class IGAPI(object):
	"""
	One HTTP client for the IG REST API, with the session it needs.

	Responses come back as (status, payload) and are never raised on: a 4xx
	is a fact about the account or the deal, and the handlers above turn it
	into a log line and a StatusEvent the way they already do for every
	other broker here. What *is* raised on is a configuration that cannot produce a
	request at all - no API key, or no credentials to open a session with.

	The session is opened on the first call rather than in the constructor,
	so that building a client costs nothing and a wiring can be assembled and
	inspected - scripts/live.py --dry-run - without authenticating.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		setup = args.get('setup')
		self.setup = setup if setup is not None else settings

		self.domain = str(getattr(self.setup, 'DOMAIN', 'practice'))
		self.demo = self.domain != 'real'
		self.host = (getattr(self.setup, 'IG_API_DOMAIN', '')
					 or HOSTS['practice' if self.demo else 'real'])

		self.key = getattr(self.setup, 'IG_API_KEY', '') or ''
		self.identifier = getattr(self.setup, 'IG_IDENTIFIER', '') or ''
		self.password = getattr(self.setup, 'IG_PASSWORD', '') or ''
		self.account = getattr(self.setup, 'IG_ACCOUNT_ID', '') or ''
		if not self.key:
			raise IGError(
				"IG_API_KEY is not set. Every IG request carries the "
				"application key, and a key belongs to one of the two hosts: "
				"take the demo key from your demo account's API settings and "
				"the live key from the live one.")
		if not (self.identifier and self.password):
			raise IGError(
				"IG_IDENTIFIER and IG_PASSWORD are both needed: IG issues "
				"session tokens from a login rather than accepting a "
				"long-lived token the way OANDA does.")

		#: 2 (CST / X-SECURITY-TOKEN) or 3 (OAuth). Two is the default because
		#: its tokens last hours where version 3's access token is measured in
		#: seconds, and a refresh that fails mid-session costs an order.
		self.version = int(getattr(self.setup, 'IG_SESSION_VERSION', 2) or 2)
		if self.version not in (2, 3):
			raise IGError("IG_SESSION_VERSION is 2 or 3, not %r" % self.version)

		self.verify = bool(getattr(self.setup, 'IG_VERIFY_TLS', True))
		self.limiters = dict(
			(name, RateLimiter(limit, window))
			for name, (limit, window) in POOLS.items())
		#: the historical-price budget, as IG last reported it
		self.allowance = Allowance()

		# session state, filled in by login()
		self.cst = None
		self.security_token = None
		self.oauth = None
		self.refresh_token = None
		self.oauth_expires = 0.0
		self.lightstreamer = None

	# ---------------------------------------------------------------- routes

	def route(self, key, *parts):
		if key not in ROUTES:
			raise IGError("unknown IG route %r" % key)
		path, version = ROUTES[key]
		if parts:
			path = path % tuple(str(p) for p in parts)
		return BASE + path, version

	def url(self, key, *parts):
		path, _version = self.route(key, *parts)
		return "https://%s%s" % (self.host, path)

	# ------------------------------------------------------------- session

	def loggedIn(self):
		if self.version == 2:
			return bool(self.cst and self.security_token)
		return bool(self.oauth and self.oauth_expires > _clock())

	def login(self):
		"""
		Open a session, or refuse in a way that names what went wrong.

		Returns True when the client can go on to make a request. A failed
		login is logged and returns False rather than raising: the handlers
		above poll in a loop, and a credential that is wrong at eight in the
		morning is a run of error events, not a traceback that takes the
		engine's thread with it.
		"""
		body = {'identifier': self.identifier, 'password': self.password}
		key = 'session' if self.version == 2 else 'session_v3'
		status, payload, headers = self.send('POST', key, body=body,
											 authenticated=False)
		if status != 200 or payload is None:
			self.logger.error(
				"IG login failed: status %s %s" % (
					status, (payload or {}).get('errorCode', '')))
			return False

		self.lightstreamer = payload.get('lightstreamerEndpoint')
		if self.version == 2:
			# These arrive as headers, not in the body. A 200 without them is
			# not a session, so it is treated as a failed login rather than
			# letting every later request 401 with no explanation.
			self.cst = headers.get('CST')
			self.security_token = headers.get('X-SECURITY-TOKEN')
			if not (self.cst and self.security_token):
				self.logger.error(
					"IG login returned 200 without CST / X-SECURITY-TOKEN")
				return False
			current = (payload.get('currentAccountId')
					   or (payload.get('accountInfo') or {}).get('accountId') or '')
			if self.account and current and self.account != current:
				# a v2 session deals on the login's current account whatever
				# IG_ACCOUNT_ID says, and switching it (PUT /session) moves
				# every other session of the login too - measured on the demo
				# on 2026-09-24. Version 3 names the account on each request
				self.logger.error(
					"IG session is on %s, not %s: a version 2 session cannot "
					"deal on another account safely; set IG_SESSION_VERSION=3"
					% (current, self.account))
				self.cst = self.security_token = None
				return False
			if not self.account:
				self.account = current
		else:
			token = payload.get('oauthToken') or {}
			self.oauth = token.get('access_token')
			self.refresh_token = token.get('refresh_token')
			if not self.oauth:
				self.logger.error("IG login returned 200 without an access token")
				return False
			# expires_in comes back as a string of seconds and is short - the
			# figure is read rather than assumed, and a margin is kept so a
			# request is not sent with a token that expires in flight.
			self.oauth_expires = _clock() + _seconds(token.get('expires_in'), 60) - 5
			self.account = self.account or payload.get('accountId') or ''

		self.logger.info("IG session open on %s (%s account%s)"
						 % (self.host, "demo" if self.demo else "LIVE",
							", " + self.account if self.account else ""))
		return True

	def refresh(self):
		"""Renew a version 3 access token, or fall back to logging in again."""
		if self.version != 3 or not getattr(self, 'refresh_token', None):
			return self.login()
		status, payload, _headers = self.send(
			'POST', 'refresh', body={'refresh_token': self.refresh_token},
			authenticated=False)
		if status != 200 or not payload or not payload.get('access_token'):
			self.logger.warning("IG token refresh failed (%s); logging in again"
								% status)
			return self.login()
		self.oauth = payload['access_token']
		self.refresh_token = payload.get('refresh_token', self.refresh_token)
		self.oauth_expires = _clock() + _seconds(payload.get('expires_in'), 60) - 5
		return True

	def logout(self):
		if not self.loggedIn():
			return
		self.send('DELETE', 'logout')
		self.cst = self.security_token = self.oauth = None

	def headers(self, key, authenticated=True):
		_path, version = self.route(key)
		out = {
			'Content-Type': 'application/json; charset=UTF-8',
			'Accept': 'application/json; charset=UTF-8',
			'X-IG-API-KEY': self.key,
			'Version': str(version),
		}
		if not authenticated:
			return out
		if self.version == 2:
			if self.cst:
				out['CST'] = self.cst
			if self.security_token:
				out['X-SECURITY-TOKEN'] = self.security_token
		else:
			if self.oauth:
				out['Authorization'] = 'Bearer ' + self.oauth
			if self.account:
				out['IG-ACCOUNT-ID'] = self.account
		return out

	# ----------------------------------------------------------------- calls

	def send(self, method, key, parts=(), params=None, body=None,
			 authenticated=True, override=None):
		"""
		One request, with no session handling. Returns (status, payload,
		headers), payload None when there is nothing parseable and
		(None, None, {}) when the request could not be made at all.
		"""
		pool = self.limiters[ROUTE_POOL.get(key, 'default')]
		pool.take()

		headers = self.headers(key, authenticated)
		if override:
			# IG tunnels some deletes through POST with a _method header
			# rather than accepting a body on DELETE.
			headers['_method'] = override

		url = self.url(key, *parts)
		try:
			requests.packages.urllib3.disable_warnings()
			session = requests.Session()
			req = requests.Request(method, url, headers=headers, params=params,
								   data=json.dumps(body) if body is not None else None)
			resp = session.send(req.prepare(), stream=False, verify=self.verify)
		except Exception as exc:
			self.logger.error("IG %s %s failed: %s" % (method, key, str(exc)))
			return None, None, {}

		status = getattr(resp, 'status_code', None)
		resp_headers = dict(getattr(resp, 'headers', {}) or {})
		if status == 429:
			retry = resp_headers.get('Retry-After')
			pool.penalise(retry if retry is not None else POOLS['default'][1])
			self.logger.error("IG %s rate limited, holding off %s s"
							  % (key, retry))

		payload = None
		text = getattr(resp, 'text', '') or ''
		if text:
			try:
				payload = json.loads(text)
			except ValueError:
				self.logger.error("IG %s returned unparseable body" % key)
		return status, payload, resp_headers

	def call(self, method, key, parts=(), params=None, body=None,
			 override=None):
		"""
		One request, logging in first and once more if the session is refused.

		The retry is deliberately limited to one: a token that is rejected
		twice is a credential problem, and hammering a login endpoint with
		bad credentials is how an account gets locked.
		"""
		if not self.loggedIn() and not self.login():
			return None, None

		status, payload, _headers = self.send(method, key, parts, params, body,
											  override=override)
		if status in (401, 403) and _sessionExpired(payload):
			self.logger.info("IG session refused (%s); opening a new one" % status)
			if self.refresh():
				status, payload, _headers = self.send(method, key, parts,
													  params, body,
													  override=override)
		return status, payload

	def get(self, key, parts=(), params=None):
		return self.call('GET', key, parts, params=params)

	def post(self, key, parts=(), body=None):
		return self.call('POST', key, parts, body=body)

	def delete(self, key, parts=(), body=None):
		"""
		A delete, tunnelled through POST where IG asks for that.

		/workingorders/otc/{dealId} takes a real DELETE and no body;
		/positions/otc takes a body, which IG accepts only on a POST carrying
		a _method header saying DELETE.
		"""
		if body is None:
			return self.call('DELETE', key, parts)
		return self.call('POST', key, parts, body=body, override='DELETE')

	# ------------------------------------------------------------ allowance

	def noteAllowance(self, payload):
		"""
		Record what IG just said is left of the weekly history budget.

		Called with the metadata of a price response. Worth a warning when it
		runs low because the failure it precedes is silent: the route starts
		answering with an error and a backfill simply stops, hours before
		anyone looks at why.
		"""
		meta = (payload or {}).get('metadata') or {}
		allowance = meta.get('allowance') or {}
		if not self.allowance.update(allowance.get('remainingAllowance'),
									 allowance.get('totalAllowance'),
									 allowance.get('allowanceExpiry')):
			return False
		if self.allowance.low():
			self.logger.warning(
				"IG historical price allowance is running out: %s. It meters "
				"data points by the week and is shared by everything using "
				"this API key." % self.allowance)
		return True

	# ------------------------------------------------------------ resolution

	def resolve(self, term):
		"""
		Search the markets, so an epic can be recorded rather than invented.

		Used by scripts/ig_instruments.py: the result is meant to be read by a
		person and pasted into IG_INSTRUMENTS, not cached and trusted at
		runtime. A search for 'EURUSD' returns the mini, the standard and the
		spread bet, and picking one of those at runtime is picking which
		market the money goes into.
		"""
		status, payload = self.get('search', params={'searchTerm': term})
		if status != 200 or not payload:
			self.logger.error("IG market search for %s: status %s"
							  % (term, status))
			return []
		return payload.get('markets') or []

	def market(self, ig_epic):
		"""Everything IG knows about one epic, dealing rules included."""
		status, payload = self.get('market', parts=(ig_epic,))
		if status != 200 or not payload:
			self.logger.error("IG market %s: status %s" % (ig_epic, status))
			return None
		return payload


def _clock():
	import time as _time
	return _time.time()


def _seconds(value, default):
	try:
		return float(value)
	except (TypeError, ValueError):
		return float(default)


def _sessionExpired(payload):
	"""
	Is this 401 about the session, rather than about the account?

	Only a session problem is worth logging in again for. A permission error
	would repeat forever, and retrying it just doubles the requests before
	the same refusal.
	"""
	code = str((payload or {}).get('errorCode', ''))
	return ('token' in code or 'security' in code or 'session' in code
			or code == '')
