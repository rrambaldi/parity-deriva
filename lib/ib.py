"""
Interactive Brokers, through the Client Portal Web API.

IB offers three ways in and only one of them fits this stack. The TWS socket
API is an event-driven protocol needing a client library and a running
desktop application; FIX is institutional. The Client Portal Web API is
JSON over HTTP, which is what every other broker here speaks, so that is what
this module talks to - and the price of that choice has to be said plainly,
because it is unlike the other three brokers in one important way:

	**there is no host to point at.** The Web API is served by a gateway you
	run yourself, normally on localhost, and a human being authenticates it
	in a browser. Nothing in this module can log in. It can only ask whether
	somebody already did, and say so clearly when they did not.

That single fact shapes everything here:

* **the session is checked, not opened.** /iserver/auth/status answers
  whether the gateway holds a live session. A run that starts unauthenticated
  fails at the first request with an error naming the gateway, rather than
  filling the log with 401s.
* **the session dies of boredom.** It times out after a few minutes of
  inactivity, so the client tickles it - what IB calls the keep-alive - on a
  timer of its own rather than only when a poll happens to come round.
* **/iserver/accounts comes first.** IB documents it as a precondition for
  the order and market-data routes, so it is sent once after the session is
  confirmed. Skipping it produces failures that look like permissions.
* **an order can be answered with a question.** POST /iserver/account/{id}/
  orders may reply with a message and an id instead of an order id, and the
  order exists only once that id has been confirmed. This is not an error
  path - it is the ordinary path, and most orders draw at least one question.
  See IBAPI.place().
* **the limit is global.** Where eToro and IG meter pools of endpoints, IB
  documents one budget per user across the whole Web API. So one limiter
  covers every route, and the order routes additionally spend from a second,
  gentler one - that second budget is this project's caution, not IB's rule,
  and is marked as such.

What is deliberately absent: any default account id, and any invented
contract id. IB accounts are not interchangeable and a conid is not derivable
from 'EUR_USD', so both are configuration and an omission is a refusal to
start.
"""

import hashlib
import json
import logging
import time

import requests

from parity_deriva.etc import settings
from parity_deriva.lib.ratelimit import RateLimiter


class IBError(Exception):
	"""A configuration or naming problem that cannot be papered over."""


class IBNotAuthenticated(IBError):
	"""
	The gateway is running but nobody has logged into it.

	Its own class because it is the one failure an operator can fix in ten
	seconds, and because it is not a bug: a session that expired overnight is
	the normal state of an IB gateway in the morning.
	"""


#: The default place a Client Portal Gateway listens. Unlike eToro's host,
#: this default is not a claim about a service on the internet - it is where
#: the gateway's own documentation puts it on the machine you started it on,
#: and if yours is elsewhere, IB_GATEWAY says so.
DEFAULT_GATEWAY = 'localhost:5000'

#: Everything hangs off this prefix.
BASE = '/v1/api'


#: This project's granularities as IB spells its bars. The sub-minute ones are
#: missing because this route's smallest bar is a minute: 'S5' cannot be
#: served and is refused rather than rounded up to something a strategy did
#: not ask for.
BARS = {
	'M1': '1min',
	'M2': '2min',
	'M3': '3min',
	'M5': '5min',
	'M10': '10min',
	'M15': '15min',
	'M30': '30min',
	'H1': '1h',
	'H2': '2h',
	'H3': '3h',
	'H4': '4h',
	'H8': '8h',
	'D': '1d',
	'W': '1w',
}


#: Every route this project uses. No demo/real split: which account the
#: gateway is logged into decides that, and IB's paper account is simply a
#: different account id - which is why IB_ACCOUNT_ID has no default and why
#: scripts/live.py still refuses a 'real' DOMAIN without --live.
ROUTES = {
	'auth_status': '/iserver/auth/status',
	'reauthenticate': '/iserver/reauthenticate',
	'tickle': '/tickle',
	'accounts': '/iserver/accounts',
	'search': '/iserver/secdef/search',
	'contract': '/iserver/contract/%s/info',
	'history': '/iserver/marketdata/history',
	'snapshot': '/iserver/marketdata/snapshot',
	'place_order': '/iserver/account/%s/orders',
	'reply': '/iserver/reply/%s',
	'cancel_order': '/iserver/account/%s/order/%s',
	'order_status': '/iserver/account/order/status/%s',
	'orders': '/iserver/account/orders',
	'trades': '/iserver/account/trades',
}

#: IB publishes one request budget per user across the whole Web API, so this
#: is one pool and not a table of them. The second entry is not IB's: it is
#: this project holding its own order traffic well under the global figure, on
#: the grounds that a market data poll being slowed is cheap and an order
#: being refused for pacing is not.
GLOBAL_LIMIT = (10, 1)
ORDER_LIMIT = (5, 1)

#: Routes that also spend from the order budget.
ORDER_ROUTES = frozenset(['place_order', 'reply', 'cancel_order'])

#: Fields of the market data snapshot, by IB's numbering: last traded, bid,
#: ask. They are numbers on the wire and the numbers are what the route wants,
#: so they are named here rather than at each call site.
FIELD_LAST = '31'
FIELD_BID = '84'
FIELD_ASK = '86'


def bar(granularity):
	"""
	This project's granularity as IB spells it.

	Refuses anything the route does not serve, for the same reason the other
	two providers do: a strategy handed bars of a period it did not ask for
	derives every level from the wrong data, and nothing downstream can tell.
	"""
	if granularity in BARS:
		return BARS[granularity]
	raise IBError(
		"IB has no bar for %r; this route serves %s"
		% (granularity, ", ".join(sorted(BARS))))


def clientOrderId(*parts):
	"""
	A client order id that is a function of what it identifies.

	IB calls it cOID, requires it to be unique, and - this is the useful part -
	reports it back as ``order_ref`` on the order list. So one poll of
	/iserver/account/orders can be joined straight back to the signals that
	produced the orders, with no bookkeeping of ids in between, and a replay
	of the same candles produces the same ids as the live run.

	It doubles as the handle a bracket's children hang off: IB takes the
	parent's cOID as ``parentId`` on a child order.
	"""
	if not parts:
		raise IBError("a client order id has to be derived from something")
	raw = ":".join(str(p) for p in parts).encode('utf-8')
	return "PD" + hashlib.sha1(raw).hexdigest()[:28]


def instrument(name, setup=None):
	"""
	The IB identity of an instrument this project names OANDA-style.

	Not derivable, and more thoroughly not derivable than at the other two
	brokers: IB's EUR/USD is a CASH contract on IDEALPRO with a numeric conid,
	and the same four letters also name futures, CFDs and a dozen instruments
	on other exchanges. An unmapped instrument raises, naming the script that
	resolves it, because dealing whatever a symbol search returned first is
	unbounded.
	"""
	cfg = setup if setup is not None else settings
	table = getattr(cfg, 'IB_INSTRUMENTS', {}) or {}
	if name in table:
		entry = table[name]
		if entry.get('conid') is None:
			raise IBError("IB_INSTRUMENTS[%r] has no conid" % name)
		return entry
	raise IBError(
		"%s is not in IB_INSTRUMENTS. Resolve its conid with "
		"'python scripts/ib_instruments.py %s' and add it to "
		"etc/settings.py" % (name, name))


def conid(name, setup=None):
	return int(instrument(name, setup)['conid'])


def instrumentName(contract_id, setup=None):
	"""The project's name for an IB conid, or None."""
	cfg = setup if setup is not None else settings
	table = getattr(cfg, 'IB_INSTRUMENTS', {}) or {}
	for name, entry in table.items():
		if entry.get('conid') is not None \
				and int(entry['conid']) == int(contract_id):
			return name
	return None


def pricePrecision(instrument_name, setup=None):
	"""
	Decimal places to round an order price to on IB.

	Precision is a property of the market rather than of the broker, so the
	shared INSTRUMENT_PRECISION table applies; an IB_INSTRUMENTS entry may
	override it per instrument where IB's minimum tick disagrees.
	"""
	from parity_deriva.lib.utils import pricePrecision as shared
	cfg = setup if setup is not None else settings
	table = getattr(cfg, 'IB_INSTRUMENTS', {}) or {}
	entry = table.get(instrument_name) or {}
	if entry.get('precision') is not None:
		return int(entry['precision'])
	return shared(instrument_name, cfg)


def spreadModel(setup=None):
	from parity_deriva.lib.spread import spreadModel as _model
	cfg = setup if setup is not None else settings
	return _model(cfg, 'IB_SPREAD')


def utcnow():
	"""Now, as the naive UTC datetime barTime() produces."""
	import datetime as _dt
	return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def barTime(value):
	"""
	An IB bar timestamp as the naive UTC datetime this project uses.

	The history route stamps each point with 't', milliseconds since the
	epoch. Seconds are accepted too, told apart by magnitude rather than by a
	flag, because a value that is actually seconds interpreted as
	milliseconds lands in 1970 and would be silently discarded as too old.
	"""
	import datetime as _dt
	if isinstance(value, _dt.datetime):
		return value
	try:
		number = float(value)
	except (TypeError, ValueError):
		return _dt.datetime(1970, 1, 1, 0, 0, 0)
	if number > 1e11:
		number = number / 1000.0
	return _dt.datetime.fromtimestamp(number, _dt.timezone.utc).replace(tzinfo=None)


def startTime(dt):
	"""A datetime as the history route's startTime wants it."""
	return dt.strftime('%Y%m%d-%H:%M:%S')


def duration(delta):
	"""
	A timedelta as one of IB's period strings.

	IB takes a period rather than an end time, so a date range has to be
	expressed as "how much". Rounded *up* to the next whole unit: asking for
	more than the range and filtering the surplus locally costs one request,
	where asking for less silently loses the oldest bars of the window
	somebody asked for.
	"""
	seconds = max(int(delta.total_seconds()), 60)
	if seconds <= 3600:
		return "%dmin" % -(-seconds // 60)
	if seconds <= 86400:
		return "%dh" % -(-seconds // 3600)
	if seconds <= 7 * 86400:
		return "%dd" % -(-seconds // 86400)
	if seconds <= 365 * 86400:
		return "%dw" % -(-seconds // (7 * 86400))
	return "%dy" % -(-seconds // (365 * 86400))


class IBAPI(object):
	"""
	One HTTP client for a Client Portal Gateway.

	Responses come back as (status, payload) and are never raised on, the way
	the other two clients work - with one exception. A gateway with no session
	raises IBNotAuthenticated, because that is not a fact about an order, it
	is the whole stack having nowhere to send orders to, and the operator
	needs to be told in those words rather than through a run of 401s.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		setup = args.get('setup')
		self.setup = setup if setup is not None else settings

		self.gateway = (getattr(self.setup, 'IB_GATEWAY', '')
						or DEFAULT_GATEWAY)
		self.account = getattr(self.setup, 'IB_ACCOUNT_ID', '') or ''
		if not self.account:
			raise IBError(
				"IB_ACCOUNT_ID is not set. IB keys the order routes by "
				"account and a login can hold several - a paper account and a "
				"live one among them - so which one this stack deals on is "
				"not something to be picked for you.")

		# The gateway serves HTTPS with a certificate it generated for itself,
		# so verification is off by default. That is only defensible because
		# of where it is talking: a loopback address on the machine the
		# gateway runs on. Point IB_GATEWAY at another host and you are
		# trusting whatever answers, so IB_VERIFY_TLS is there to be turned
		# back on with a certificate you installed.
		self.verify = bool(getattr(self.setup, 'IB_VERIFY_TLS', False))

		self.limiter = RateLimiter(*GLOBAL_LIMIT)
		self.orders_limiter = RateLimiter(*ORDER_LIMIT)

		#: how long the gateway's session may go untouched, and when it was
		#: last touched. IB documents the session as timing out on inactivity;
		#: the interval here is comfortably inside that and is a setting
		#: because a slower poll is otherwise enough to lose the session
		#: between two candles.
		self.keepalive = int(getattr(self.setup, 'IB_TICKLE_SECONDS', 60))
		self.tickled = 0.0
		self.prepared = False

	# ---------------------------------------------------------------- routes

	def route(self, key, *parts):
		if key not in ROUTES:
			raise IBError("unknown IB route %r" % key)
		path = ROUTES[key]
		if parts:
			path = path % tuple(str(p) for p in parts)
		return BASE + path

	def url(self, key, *parts):
		return "https://%s%s" % (self.gateway, self.route(key, *parts))

	# --------------------------------------------------------------- session

	def authenticated(self):
		"""
		Does the gateway hold a live session?

		Answers from what the gateway says rather than from whether the last
		request worked. A gateway that is running but logged out answers this
		route perfectly well, which is exactly the state worth telling apart
		from a gateway that is not running at all.
		"""
		status, payload = self.send('POST', 'auth_status')
		if status is None:
			return False
		if status != 200 or not isinstance(payload, dict):
			return False
		if payload.get('authenticated'):
			return True
		if payload.get('connected') and not payload.get('competing'):
			# Connected but not authenticated is the state a session drops
			# into when it has been idle or when the brokerage session was
			# taken elsewhere; IB documents reauthenticate for precisely this.
			self.logger.info("IB gateway is connected but not authenticated; "
							 "asking it to reauthenticate")
			self.send('POST', 'reauthenticate')
			status, payload = self.send('POST', 'auth_status')
			return bool((payload or {}).get('authenticated'))
		return False

	def tickle(self, force=False):
		"""
		Keep the gateway's session alive.

		Called before every request rather than on a thread of its own: a
		timer would keep a session alive for a stack that had stopped using
		it, and the cost here is one cheap request a minute at most.
		"""
		now = time.time()
		if not force and now - self.tickled < self.keepalive:
			return True
		status, _payload = self.send('POST', 'tickle')
		self.tickled = now
		return status == 200

	def prepare(self):
		"""
		Everything that has to be true before the first real request.

		IB documents /iserver/accounts as a precondition for the order and
		market-data routes, and a stack that skips it sees failures that look
		like a permissions problem. It is sent once, after the session is
		confirmed, and the result is remembered so the precondition does not
		become a request per poll.
		"""
		if self.prepared:
			return True
		if not self.authenticated():
			raise IBNotAuthenticated(
				"the IB gateway at %s has no authenticated session. Start the "
				"Client Portal Gateway and log in at https://%s - nothing in "
				"this project can do that for you, because IB authenticates a "
				"human in a browser." % (self.gateway, self.gateway))
		status, payload = self.send('GET', 'accounts')
		if status != 200:
			self.logger.error("IB /iserver/accounts: status %s" % status)
			return False
		known = (payload or {}).get('accounts') or []
		if known and self.account not in known:
			raise IBError(
				"IB_ACCOUNT_ID is %r but this gateway session holds %s. "
				"Dealing on an account the session does not hold is not "
				"something to discover from a rejection."
				% (self.account, ", ".join(str(a) for a in known)))
		self.prepared = True
		return True

	# ----------------------------------------------------------------- calls

	def send(self, method, key, parts=(), params=None, body=None):
		"""
		One request, with no session handling. Returns (status, payload),
		payload None when there is nothing parseable and (None, None) when the
		request could not be made at all.
		"""
		self.limiter.take()
		if key in ORDER_ROUTES:
			self.orders_limiter.take()

		url = self.url(key, *parts)
		try:
			requests.packages.urllib3.disable_warnings()
			session = requests.Session()
			req = requests.Request(
				method, url,
				headers={'Content-Type': 'application/json',
						 'Accept': 'application/json'},
				params=params,
				data=json.dumps(body) if body is not None else None)
			resp = session.send(req.prepare(), stream=False, verify=self.verify)
		except Exception as exc:
			self.logger.error("IB %s %s failed: %s" % (method, key, str(exc)))
			return None, None

		status = getattr(resp, 'status_code', None)
		payload = None
		text = getattr(resp, 'text', '') or ''
		if text:
			try:
				payload = json.loads(text)
			except ValueError:
				self.logger.error("IB %s returned unparseable body" % key)
		return status, payload

	def call(self, method, key, parts=(), params=None, body=None):
		"""One request, with the session confirmed and kept alive first."""
		if not self.prepare():
			return None, None
		self.tickle()
		return self.send(method, key, parts, params=params, body=body)

	def get(self, key, parts=(), params=None):
		return self.call('GET', key, parts, params=params)

	def post(self, key, parts=(), body=None):
		return self.call('POST', key, parts, body=body)

	def delete(self, key, parts=()):
		return self.call('DELETE', key, parts)

	# ---------------------------------------------------------------- orders

	def place(self, orders, confirm=True, refuse=()):
		"""
		Submit orders, answering whatever IB asks back.

		This is the part of IB that has no counterpart at the other two
		brokers. The reply to an order submission is a *list*, and an entry in
		it is one of three things: an order that now exists, an error, or a
		question - a message and an id, where the order exists only once that
		id has been confirmed. Questions are the ordinary case rather than the
		exception; most orders draw at least one.

		Every question is logged at warning level whether or not it is
		confirmed, because a question answered silently is a warning IB raised
		and nobody read. `refuse` is a list of fragments that must never be
		auto-confirmed - put anything in it whose answer you want to be a
		person, and the submission stops there with the question in the log.

		Returns the list IB last answered with, so the caller can tell an
		order id from a refusal.
		"""
		status, payload = self.post('place_order', parts=(self.account,),
									body={'orders': orders})
		rounds = 0
		while status == 200 and isinstance(payload, list) and payload \
				and isinstance(payload[0], dict) and payload[0].get('id') \
				and payload[0].get('message'):
			question = " ".join(str(m) for m in payload[0].get('message') or [])
			reply_id = payload[0]['id']
			self.logger.warning("IB asks: %s" % question)

			blocked = [f for f in refuse if f and f.lower() in question.lower()]
			if blocked or not confirm:
				self.logger.error(
					"IB question not answered here (%s); the order has NOT "
					"been placed"
					% ("matched %s" % blocked[0] if blocked
					   else "IB_CONFIRM_ORDER_QUESTIONS is off"))
				return payload
			rounds += 1
			if rounds > 5:
				# A question that keeps coming back is not a question this
				# loop understands, and confirming forever is how an order
				# gets placed by accident.
				self.logger.error("IB is still asking after %d confirmations; "
								  "giving up on this order" % rounds)
				return payload
			status, payload = self.post('reply', parts=(reply_id,),
										body={'confirmed': True})

		if status != 200:
			self.logger.error("IB order submission: status %s" % status)
			return payload if isinstance(payload, list) else []
		return payload if isinstance(payload, list) else []

	# ------------------------------------------------------------ resolution

	def resolve(self, symbol, sec_type=None):
		"""
		Search the contract definitions, so a conid can be recorded rather
		than invented.

		Used by scripts/ib_instruments.py. The result is meant to be read by a
		person and pasted into IB_INSTRUMENTS: a search for 'EUR' returns the
		cash pair, the futures, and a fund or two, and choosing between those
		at runtime is choosing which market the money goes into.
		"""
		body = {'symbol': symbol}
		if sec_type:
			body['secType'] = sec_type
		status, payload = self.post('search', body=body)
		if status != 200 or not payload:
			self.logger.error("IB contract search for %s: status %s"
							  % (symbol, status))
			return []
		if isinstance(payload, dict):
			payload = payload.get('results') or []
		return payload or []
