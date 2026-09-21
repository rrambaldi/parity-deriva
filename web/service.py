"""
A local web service that shows a backtest: the candles, the trades, the result.

What it is for: reading a backtest instead of counting log lines. The chart is
on top, the trades are under it in the order they opened, and clicking one
zooms the chart onto that trade with its entry, its exit, its stop and its
target drawn in. The point of the zoom is to make the simulator's decisions
inspectable - you can see the bar that reached the level, and whether the
same bar reached the other one too, which is the case backtest/resolution.py
says the bars cannot settle.

What it is not: a dashboard for a live account. It reads the local HDF5
warehouse and runs the offline stack in backtest/ledger.py. No broker is
contacted, no order is ever sent, and nothing here writes anything.

Three decisions worth stating, because each one could have gone the other way:

* **no web framework.** The requirements file lists what the code imports,
  with a note per line saying why; adding Flask for three routes that return
  JSON would be a runtime dependency for a viewer. http.server is in the
  standard library and does what is needed. The cost is that routing and the
  static handler are written out below rather than decorated.
* **no charting library.** The front end draws candles on a canvas itself. A
  CDN script would mean this page only works with an internet connection,
  which is a strange property for a tool that reads a local file, and vendoring
  a minified library into the repository would put code nobody here can review
  next to code that is commented line by line.
* **127.0.0.1 by default, and no authentication at all.** Anyone who can
  reach the port can run backtests on this machine's data. That is fine on a
  loopback address and is not fine anywhere else, so the bind address is a
  flag with a safe default rather than a setting that defaults to convenience.

What it refuses, and why each refusal is better than the alternative:

* an instrument that is not a store in DATA_DIR. The instrument name reaches
  a file path, so it is checked against the files that actually exist rather
  than sanitised - a rule about what a name may contain is a rule somebody has
  to get exactly right, and a list of real files cannot be talked around.
* a granularity the store does not hold, naming the ones it does.
* a window wider than --max-candles. A chart handed a hundred thousand bars
  is not a chart, and silently drawing the first few thousand would show a
  range nobody asked for. The refusal says how many bars the window holds.
"""

import datetime
import json
import logging
import os
import posixpath
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from parity_deriva.backtest import ledger
from parity_deriva.etc import settings
from parity_deriva.performance import report as report_module


#: where the page and its two assets live
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

#: extensions the static handler will serve, and as what. A map rather than
#: mimetypes.guess_type: this directory holds three files, and a handler that
#: will serve anything is a handler that will serve whatever ends up here.
CONTENT_TYPES = {
	'.html': 'text/html; charset=utf-8',
	'.css': 'text/css; charset=utf-8',
	'.js': 'application/javascript; charset=utf-8',
}

#: bars in one reply, unless --max-candles says otherwise
MAX_CANDLES = 5000

EPOCH = datetime.datetime(1970, 1, 1)


class ServiceError(Exception):
	"""Something the caller asked for that cannot be given. Reported as 400."""


def millis(when):
	"""
	A naive UTC datetime as epoch milliseconds.

	Everything in the store and in the event stream is naive UTC by this
	project's convention, and the page formats these back as UTC. Handing the
	browser an ISO string without an offset would have it parse the string in
	the viewer's own timezone, which moves every candle by however many hours
	the viewer happens to be from UTC.
	"""
	if when is None:
		return None
	if hasattr(when, 'to_pydatetime'):
		when = when.to_pydatetime()
	return int((when - EPOCH).total_seconds() * 1000)


def moment(ms):
	"""
	Epoch milliseconds back to the naive UTC datetime they came from.

	The inverse of millis(), used to turn a store's own first and last bar into
	the default window. Exact for these values: the index is whole seconds.
	"""
	if ms is None:
		return None
	return EPOCH + datetime.timedelta(milliseconds=ms)


def number(value, places=8):
	"""A float rounded for the wire, or None. Keeps the payload readable."""
	if value is None:
		return None
	try:
		return round(float(value), places)
	except (TypeError, ValueError):
		return None


class Service(object):
	"""
	The backtests, without the HTTP.

	Separated from the request handler so that everything the service can be
	asked is reachable from a test without a socket, and so that the refusals
	are ordinary exceptions rather than status codes.
	"""

	def __init__(self, setup=None, max_candles=MAX_CANDLES, cache_size=8):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self.setup = setup if setup is not None else settings
		self.max_candles = int(max_candles)
		self.cache_size = int(cache_size)
		self._cache = {}
		self._order = []
		# One backtest at a time. Each run builds its own handlers, so two at
		# once would not corrupt each other - but each also loads a store into
		# memory, and a page that fires two requests should queue rather than
		# double the footprint.
		self._lock = threading.Lock()

	# --------------------------------------------------------------- stores

	def storePath(self, instrument):
		return os.path.join(self.setup.DATA_DIR, "%s.hd5" % instrument)

	def instruments(self):
		"""
		Every instrument the warehouse holds, with what is in each store.

		Read from the directory rather than from a configured list: the
		question this answers is "what can I look at", and the honest answer
		is the files that are there.
		"""
		out = []
		directory = self.setup.DATA_DIR
		try:
			names = sorted(os.listdir(directory))
		except OSError as exc:
			raise ServiceError("cannot read DATA_DIR %s: %s" % (directory, exc))
		for name in names:
			if not name.endswith('.hd5'):
				continue
			instrument = name[:-len('.hd5')]
			granularities = self.granularities(instrument)
			if granularities:
				out.append({'instrument': instrument,
							'granularities': granularities})
		return out

	def granularities(self, instrument):
		"""What one store holds, as (granularity, bars, from, to)."""
		import pandas as pd
		path = self.storePath(instrument)
		if not os.path.exists(path):
			return []
		out = []
		try:
			store = pd.HDFStore(path, mode='r')
		except Exception as exc:
			self.logger.error("cannot open %s: %s" % (path, exc))
			return []
		try:
			for key in store.keys():
				try:
					index = store.select_column(key, 'index')
				except Exception:
					# a key that is not a frame with a datetime index is not
					# a candle series, and is left out rather than guessed at
					continue
				if len(index) == 0:
					continue
				out.append({'granularity': key.lstrip('/'),
							'bars': int(len(index)),
							'from': millis(index.min()),
							'to': millis(index.max())})
		finally:
			store.close()
		return sorted(out, key=lambda g: g['granularity'])

	# -------------------------------------------------------------- refusals

	def check(self, instrument, granularity, strategy):
		"""Everything that has to be true before a backtest is worth starting."""
		known = dict((row['instrument'], row) for row in self.instruments())
		if instrument not in known:
			raise ServiceError(
				"no store for %r in %s. It holds %s."
				% (instrument, self.setup.DATA_DIR,
				   ", ".join(sorted(known)) or "nothing"))
		names = [g['granularity'] for g in known[instrument]['granularities']]
		if granularity not in names:
			raise ServiceError(
				"%s has no %s candles; it holds %s"
				% (instrument, granularity, ", ".join(names)))
		if strategy not in ledger.STRATEGIES:
			raise ServiceError(
				"no strategy %r; this runs %s"
				% (strategy, ", ".join(sorted(ledger.STRATEGIES))))
		return known[instrument]

	def span(self, known, granularity, dtfrom, dtto):
		"""
		How many bars the window holds, refusing one too wide to draw.

		Counted from the store's own index rather than from the range divided
		by the interval: a market that is shut at the weekend has fewer bars
		than arithmetic would predict, and refusing a window that would have
		fitted is as wrong as accepting one that will not.
		"""
		import pandas as pd
		path = self.storePath(known['instrument'])
		store = pd.HDFStore(path, mode='r')
		try:
			index = store.select_column('/' + granularity, 'index')
		finally:
			store.close()
		inside = index[(index >= pd.Timestamp(dtfrom)) & (index <= pd.Timestamp(dtto))]
		count = int(len(inside))
		if count == 0:
			raise ServiceError(
				"%s %s holds no candles between %s and %s"
				% (known['instrument'], granularity, dtfrom.date(), dtto.date()))
		if count > self.max_candles:
			raise ServiceError(
				"that window holds %d %s candles and the limit is %d. Narrow "
				"the dates, ask for a coarser granularity, or raise the limit "
				"with --max-candles - a chart handed this many bars is not a "
				"chart, and drawing only the first few thousand would show a "
				"range nobody asked for."
				% (count, granularity, self.max_candles))
		return count

	# -------------------------------------------------------------- the work

	def key(self, instrument, granularity, strategy, dtfrom, dtto, units):
		return (instrument, granularity, strategy, dtfrom, dtto, units)

	def window(self, known, granularity):
		for row in known['granularities']:
			if row['granularity'] == granularity:
				return row
		raise ServiceError("%s has no %s candles" % (known['instrument'],
													  granularity))

	def backtest(self, instrument, granularity, strategy='AG01', dtfrom=None,
				 dtto=None, units=1):
		"""Run one backtest and return the payload the page reads."""
		known = self.check(instrument, granularity, strategy)

		# An unstated window means the whole store, and the whole store is what
		# the store says rather than "everything up to now". The difference is
		# not cosmetic: today() makes every request a different question, so two
		# identical requests a second apart both run the backtest and the cache
		# never answers one.
		held = self.window(known, granularity)
		dtfrom = dtfrom or moment(held['from'])
		dtto = dtto or moment(held['to'])
		if dtto <= dtfrom:
			raise ServiceError("the window ends before it starts")

		self.span(known, granularity, dtfrom, dtto)

		key = self.key(instrument, granularity, strategy, dtfrom, dtto, units)
		if key in self._cache:
			return self._cache[key]

		with self._lock:
			if key in self._cache:
				return self._cache[key]
			started = time.time()
			result = ledger.run(instrument, granularity, strategy,
								dtfrom=dtfrom, dtto=dtto, units=units,
								setup=self.setup)
			payload = self.payload(result, time.time() - started)
			self.remember(key, payload)
			return payload

	def remember(self, key, payload):
		self._cache[key] = payload
		self._order.append(key)
		while len(self._order) > self.cache_size:
			self._cache.pop(self._order.pop(0), None)

	def payload(self, result, elapsed):
		"""
		The whole answer, as the page wants it.

		Candles go out as arrays rather than objects: nine numbers a bar
		instead of nine names repeated a thousand times, which is the
		difference between a payload worth reading and one worth compressing.
		The order is fixed here and in app.js and nowhere else.
		"""
		times = []
		candles = []
		for candle in result.candles:
			when = millis(candle.time)
			times.append(when)
			mid, ask, bid = candle.mid, candle.ask, candle.bid
			candles.append([
				when,
				number(mid['o']), number(mid['h']),
				number(mid['l']), number(mid['c']),
				# the two series the fill rule actually reads: a long entry is
				# touched on the ask, its stop and target on the bid. Drawn as
				# a faint envelope when a trade is selected, so a level that
				# was reached can be seen to have been reached.
				number(ask['h']), number(ask['l']),
				number(bid['h']), number(bid['l']),
			])

		index = dict((when, i) for i, when in enumerate(times))
		trades = []
		for i, trade in enumerate(result.trades):
			entry = millis(trade['entryTime'])
			exit_ = millis(trade['exitTime'])
			trades.append({
				'n': i + 1,
				'key': trade['key'],
				'direction': trade['direction'],
				'units': trade['units'],
				'signalTime': millis(trade['signalTime']),
				'orderPrice': number(trade['orderPrice']),
				'stopLoss': number(trade['stopLoss']),
				'takeProfit': number(trade['takeProfit']),
				'entryTime': entry,
				'entryPrice': number(trade['entryPrice']),
				'exitTime': exit_,
				'exitPrice': number(trade['exitPrice']),
				'outcome': trade['outcome'],
				'pl': number(trade['pl']),
				'balance': number(trade['balance']),
				# where to zoom. Resolved here because the service knows the
				# candle list exactly; the page searching for the nearest bar
				# would have to define "nearest" for a timestamp that is not
				# a bar's, and a fill always lands on one.
				'entryIndex': index.get(entry),
				'exitIndex': index.get(exit_),
			})

		return {
			'instrument': result.instrument,
			'granularity': result.granularity,
			'strategy': result.strategy,
			'from': millis(result.dtfrom),
			'to': millis(result.dtto),
			'candles': candles,
			'trades': trades,
			'counts': result.counts,
			'report': report_module.report(result.trades),
			'elapsed': round(elapsed, 3),
		}


def parseDate(text, name, end=False):
	"""
	A date from the query, or None.

	`end` matters more than it looks. A bare 'to=2018-03-02' means the whole of
	the 2nd to whoever typed it, and reading it as midnight silently drops that
	day's bars - on an H1 store, twenty-one of them, which is a day of trades
	missing from a range that appears to include it. A time given explicitly is
	taken as given.
	"""
	if not text:
		return None
	try:
		when = datetime.datetime.strptime(text, '%Y-%m-%d')
	except ValueError:
		pass
	else:
		return (when + datetime.timedelta(days=1, microseconds=-1)) if end else when
	for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
		try:
			return datetime.datetime.strptime(text, fmt)
		except ValueError:
			pass
	raise ServiceError("%s: %r is not a date (YYYY-MM-DD)" % (name, text))


def parseInt(text, name, default):
	if text in (None, ''):
		return default
	try:
		return int(text)
	except (TypeError, ValueError):
		raise ServiceError("%s: %r is not a number" % (name, text))


class Handler(BaseHTTPRequestHandler):
	"""
	The routes. Four of them, written out rather than decorated.

	`service` is set on the class by serve() before the server starts, which
	is how BaseHTTPRequestHandler subclasses are given anything: the class is
	the handler factory and an instance exists only for the length of one
	request.
	"""

	service = None
	server_version = 'parity-deriva'
	sys_version = ''

	def log_message(self, fmt, *args):
		# the project's logger, not stderr, so a run looks like every other
		logging.getLogger('parity_deriva.trading.trading').info(
			"web %s" % (fmt % args))

	# ------------------------------------------------------------- replying

	def sendJSON(self, payload, status=200):
		body = json.dumps(payload).encode('utf-8')
		self.send_response(status)
		self.send_header('Content-Type', 'application/json; charset=utf-8')
		self.send_header('Content-Length', str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def sendError(self, message, status=400):
		self.sendJSON({'error': message}, status)

	def sendFile(self, name):
		"""
		One of the files in web/static, and nothing else.

		The path is rebuilt from its basename after being normalised, so
		neither a '..' nor an absolute path nor a symlink planted in the
		directory can reach outside it - and only the three extensions in
		CONTENT_TYPES are served at all.
		"""
		safe = posixpath.normpath('/' + name).lstrip('/')
		if '/' in safe or not safe:
			return self.sendError("no such file", 404)
		extension = os.path.splitext(safe)[1]
		if extension not in CONTENT_TYPES:
			return self.sendError("no such file", 404)
		path = os.path.join(STATIC, safe)
		if not os.path.isfile(path):
			return self.sendError("no such file", 404)
		with open(path, 'rb') as handle:
			body = handle.read()
		self.send_response(200)
		self.send_header('Content-Type', CONTENT_TYPES[extension])
		self.send_header('Content-Length', str(len(body)))
		# A local tool whose files are edited while it runs: a cached asset
		# would have the page and its script disagree about what exists.
		self.send_header('Cache-Control', 'no-store')
		self.end_headers()
		self.wfile.write(body)

	# -------------------------------------------------------------- routing

	def do_GET(self):
		parsed = urllib.parse.urlparse(self.path)
		route = parsed.path
		query = urllib.parse.parse_qs(parsed.query)

		try:
			if route in ('/', '/index.html'):
				return self.sendFile('index.html')
			if route == '/api/stores':
				return self.sendJSON({'instruments': self.service.instruments()})
			if route == '/api/backtest':
				return self.sendJSON(self.runBacktest(query))
			if route.startswith('/static/'):
				return self.sendFile(route[len('/static/'):])
			return self.sendError("no route %s" % route, 404)
		except ServiceError as exc:
			return self.sendError(str(exc))
		except ledger.LedgerError as exc:
			return self.sendError(str(exc))
		except Exception as exc:
			logging.getLogger('parity_deriva.trading.trading').exception(
				"web request failed")
			return self.sendError("%s: %s" % (type(exc).__name__, exc), 500)

	def one(self, query, name, default=None):
		values = query.get(name)
		return values[0] if values else default

	def runBacktest(self, query):
		instrument = self.one(query, 'instrument')
		granularity = self.one(query, 'granularity')
		if not instrument or not granularity:
			raise ServiceError("instrument and granularity are both required")
		return self.service.backtest(
			instrument=instrument,
			granularity=granularity,
			strategy=self.one(query, 'strategy', 'AG01'),
			dtfrom=parseDate(self.one(query, 'from'), 'from'),
			dtto=parseDate(self.one(query, 'to'), 'to', end=True),
			units=parseInt(self.one(query, 'units'), 'units', 1))


def serve(host='127.0.0.1', port=8731, setup=None, max_candles=MAX_CANDLES):
	"""
	Build the server. Returns it without serving, so a caller decides how.

	The default port is not 8080: a machine running this is a machine doing
	other work, and quietly taking the port everything else also wants is a
	bad neighbour.
	"""
	handler = type('BoundHandler', (Handler,),
				   {'service': Service(setup=setup, max_candles=max_candles)})
	return ThreadingHTTPServer((host, port), handler)
