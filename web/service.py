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
contacted and no order is ever sent. The one thing it writes is data: the
data dialog uploads candle CSVs into IMPORT_DIR and runs scripts/import_csv.py
over that directory, which merges them into the stores.

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

import bisect
import datetime
import functools
import gzip
import hashlib
import ast
import importlib.util
import inspect
import io
import itertools
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import tempfile
import textwrap
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from parity_deriva.backtest import ledger
from parity_deriva.backtest import shadow
from parity_deriva.backtest.driver import Cancelled
from parity_deriva.data import calendar as calendar_module
from parity_deriva.data import store
from parity_deriva.data.candledb import CandleDB
from parity_deriva.etc import settings
from parity_deriva.lib import indicators
from parity_deriva.performance import report as report_module
from parity_deriva.strategy import plugins
from parity_deriva.web import livesessions


def strategies():
	"""
	Every strategy the page may offer, which is every one check() accepts.

	Two kinds, and they are not interchangeable. backtest/ledger.py's are
	handlers the live stack runs, replayed here through the same simulator the
	account trades against. A viewer plugin (strategy/plugins.py) is a package
	with its own bar loop, and it is here because a backtest of one is the same
	thing to look at: candles, trades, a report. A plugin may have free
	parameters, which ride in the query string; everything else on this page
	works the same for both.
	"""
	return sorted(ledger.STRATEGIES) + sorted(plugins.viewers())


def descriptions():
	"""
	strategy -> the line the page prints over its chart.

	Read off the strategy - DESCRIPTION on a handler class, 'description' on
	a plugin - for the same reason indicatorSpecs() reads INDICATORS off it:
	a description kept in the page is a description that stops being true the
	first time a rule changes and nobody thinks to look here.

	A strategy that says nothing gets no line, rather than a placeholder.
	"""
	out = {}
	for name, plugin in plugins.viewers().items():
		text = plugin.get('description')
		if text:
			out[name] = ' '.join(text.split())
	for name in ledger.STRATEGIES:
		try:
			handler = ledger.load_strategy(name)
		except ledger.LedgerError:
			# the same silence indicatorSpecs() keeps: a strategy that will
			# not import is a failure the backtest route reports, and the
			# list of names is not the place to report it twice
			continue
		text = getattr(handler, 'DESCRIPTION', None)
		if text:
			out[name] = ' '.join(text.split())
	return out


def defaults():
	"""
	strategy -> {instrument, granularity, trailing}: what the page selects
	when it is picked. Read off the strategy - INSTRUMENT, GRANULARITY and
	TRAILING on a handler class, the same keys on a plugin - like
	descriptions(), and for the same reason. TRAILING says the strategy walks
	a stop of its own, so the page's trailing stop starts on for it. A
	strategy that declares none of them gets no entry.
	"""
	keys = ('instrument', 'granularity', 'trailing')
	out = {}
	for name, plugin in plugins.viewers().items():
		found = dict((k, plugin[k]) for k in keys if plugin.get(k))
		if found:
			out[name] = found
	for name in ledger.STRATEGIES:
		try:
			handler = ledger.load_strategy(name)
		except ledger.LedgerError:
			continue
		found = dict((k, getattr(handler, k.upper())) for k in keys
					 if getattr(handler, k.upper(), None))
		if found:
			out[name] = found
	return out


#: The service's own logger, separate from the trading one every other
#: component here uses. A backtest logs a few lines per fill at INFO, which is
#: right for a run somebody is watching and wrong for a server: one request
#: would put several hundred lines in the journal. Keeping the two apart lets
#: scripts/web.py quiet the loud one and still report what it served.
LOGGER = 'parity_deriva.web'

#: where the page and its two assets live
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

#: extensions the static handler will serve, and as what. A map rather than
#: mimetypes.guess_type: a handler that will serve anything is a handler that
#: will serve whatever ends up here.
#: Was: three - the page, its style, its script. Now: six - the fonts, the logo
#: and the icons of web/DESIGN.md are served from here too, since nothing comes
#: from a CDN. Still no .txt: the fonts' licence sits next to them unserved.
CONTENT_TYPES = {
	'.html': 'text/html; charset=utf-8',
	'.css': 'text/css; charset=utf-8',
	'.js': 'application/javascript; charset=utf-8',
	'.woff2': 'font/woff2',
	'.svg': 'image/svg+xml',
	'.png': 'image/png',
}

#: the one origin allowed to push to this service, and the one route it may
#: push to. The collector runs on forexfactory's own calendar page - it has
#: to, since a page may not read another site's pages - so its POST is cross
#: origin and needs saying so out loud.
#:
#: Narrow on purpose, and in three ways at once: this origin only, the
#: calendar route only, and only with the token below. Everything else on
#: this port still answers no preflight at all, so a site open in the same
#: browser cannot reach the stores, the imports or a backtest.
COLLECTOR_ORIGIN = 'https://www.forexfactory.com'
COLLECTOR_ROUTE = '/api/calendar'

#: bars a second the replay gets through, for the estimate the page warns
#: with. Measured here on EUR_USD: 1 617 a second over eleven years and 5 905
#: over four, which is why the estimate is called an estimate - the per-bar
#: cost grows with what the run is carrying. The slower of the two is the
#: default so that the warning does not promise a minute and take five, and
#: it is only a seed: every run that finishes replaces it with its own rate.
TICKS_A_SECOND = 1500

#: bars in one reply, unless --max-candles says otherwise.
#:
#: Twenty-five thousand because the whole of a EUR_USD store is 18 878 H4
#: bars, and a report over the whole history is a thing somebody asks for. It
#: was five thousand while the chart drew one bar per candle and nothing else:
#: a window wider than the screen was a window that could not be read. The
#: chart now picks its own granularity under the zoom (/api/candles), so the
#: run's own bars are a starting point rather than the only view of them.
#:
#: It still refuses eleven years of H1 - 73 000 bars, a payload of megabytes
#: for a line nobody can see the shape of - and that is the line this draws:
#: the coarse series over the whole history, yes; every fine series over it,
#: no, ask for the stretch you mean.
MAX_CANDLES = 25000

EPOCH = datetime.datetime(1970, 1, 1)

#: the largest CSV one upload may carry. An M5 decade is under 50 MB a side.
MAX_UPLOAD = 1024 * 1024 * 1024

#: the first line import_csv.read_side expects of every export
CSV_HEADER = b'timestamp,open,high,low,close,volume'


@functools.lru_cache(maxsize=None)
def importer():
	"""scripts/import_csv.py, which is a script rather than a package module."""
	path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
						'scripts', 'import_csv.py')
	spec = importlib.util.spec_from_file_location('import_csv', path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


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
		self.logger = logging.getLogger(LOGGER)
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
		# the import running in the background, or the last one to finish
		self._job = {'running': False}
		self._jobLock = threading.Lock()
		# where the backtest running now has got to, or the last one to
		# finish. One dict updated in place: the run writes it from the
		# thread serving its own request and /api/progress reads it from
		# another, and a dict update is atomic enough for a status line.
		self._progress = {'running': False}
		# cleared by pauseSweep: the sweep's runs wait on it at every
		# progress report, and the sweep itself between two runs
		self._sweepGoing = threading.Event()
		self._sweepGoing.set()
		# what the last run managed, bars a second. Seeded with a measured
		# figure and replaced by this machine's own as soon as it has one
		self._rate = float(TICKS_A_SECOND)
		# the live sessions: a backtest's form trading on an account
		self.live = livesessions.LiveSessions(
			os.path.join(getattr(self.setup, 'DATA_DIR', '') or '.', 'live'))
		# every bar every session saw, one row per provider and bar
		# (data/candledb.py). Opened per request, like sessions.db
		self.candles = CandleDB(getattr(self.setup, 'CANDLE_DB', None) or os.path.join(
			getattr(self.setup, 'DATA_DIR', '') or '.', 'live', 'candles.db'))
		# what the collector has to carry to be listened to. New every time
		# the service starts, handed out by the page that prints the
		# collector, and never in a page anybody else can read: an ad on the
		# calendar page cannot guess it, so the allowance above is an
		# allowance for the script you pasted and not for that site.
		self.token = secrets.token_urlsafe(18)

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
		"""
		What one store holds, as (granularity, bars, from, to). A granularity
		it can build from a finer series it holds is listed too, with
		'derivedFrom' naming that series - see data/store.py.
		"""
		path = self.storePath(instrument)
		if not os.path.exists(path):
			return []
		try:
			series = store.indexes(path)
		except Exception as exc:
			self.logger.error("cannot open %s: %s" % (path, exc))
			return []
		out = []
		for name, index in series.items():
			row = {'granularity': name,
				   'bars': int(len(index)),
				   'from': millis(index.min()),
				   'to': millis(index.max())}
			origin = store.derived_from(path, name)
			if origin:
				row['derivedFrom'] = origin
			out.append(row)
		return sorted(out, key=lambda g: g['granularity'])

	# -------------------------------------------------------------- imports

	def importDir(self):
		return getattr(self.setup, 'IMPORT_DIR', None) \
			or os.path.join(self.setup.DATA_DIR, 'import')

	def pending(self):
		"""
		The import sets waiting in IMPORT_DIR: one per -ASK/-BID pair, which
		is the unit the import reads, with the size of each side present.
		A set missing a side is listed too, since the import will skip it.
		Other CSVs sharing the folder - a strategy's own output, say - are
		not the import's business and are left out, as the import skips them.
		"""
		directory = self.importDir()
		sets = {}
		names = sorted(os.listdir(directory)) if os.path.isdir(directory) else []
		for name in names:
			parsed = importer().parse_name(name) if name.lower().endswith('.csv') else None
			if parsed is None:
				continue
			instrument, granularity, side = parsed
			row = sets.setdefault(importer().set_name(name), {
				'set': importer().set_name(name), 'instrument': instrument,
				'granularity': granularity, 'sides': {}, 'files': {}})
			row['sides'][side] = os.path.getsize(os.path.join(directory, name))
			row['files'][side] = name
		for row in sets.values():
			row['complete'] = set(row['sides']) == {'ASK', 'BID'}
		return {'directory': directory, 'sets': [sets[k] for k in sorted(sets)]}

	def upload(self, name, stream, length):
		"""
		Write one uploaded CSV into IMPORT_DIR under its own name.

		The name has to be the one import_csv reads everything from, so it is
		checked against that pattern rather than sanitised - which also keeps
		it a bare file name that cannot reach outside the directory. The body
		is read in full first, into a .part file with a name of its own, so a
		refusal reaches the page as a message rather than a reset connection,
		and a broken upload never looks like an export waiting to be imported.
		"""
		if length <= 0 or length > MAX_UPLOAD:
			raise ServiceError("an upload is 1 byte to %d MB, this is %d bytes"
							   % (MAX_UPLOAD // 2 ** 20, length))
		directory = self.importDir()
		os.makedirs(directory, exist_ok=True)
		handle, part = tempfile.mkstemp(dir=directory, suffix='.part')
		received = 0
		try:
			with os.fdopen(handle, 'wb') as out:
				while received < length:
					chunk = stream.read(min(1 << 20, length - received))
					if not chunk:
						break
					out.write(chunk)
					received += len(chunk)
			with open(part, 'rb') as check:
				header = check.readline().strip()
			name = name or ''
			if name != os.path.basename(name) or not name.lower().endswith('.csv') \
					or importer().parse_name(name) is None:
				raise ServiceError(
					"%r is not <instrument>_<tf>_<from>_<to>-<ASK|BID>.csv, "
					"which is the name the import reads the series from" % name)
			if received != length:
				raise ServiceError("upload cut short: %d of %d bytes"
								   % (received, length))
			if header != CSV_HEADER:
				raise ServiceError("%s starts with %r, not %r"
								   % (name, header[:80].decode('utf-8', 'replace'),
									  CSV_HEADER.decode()))
			os.replace(part, os.path.join(directory, name))
		finally:
			if os.path.exists(part):
				os.remove(part)
		return {'name': name, 'bytes': received}

	def startImport(self, sets):
		"""
		Start scripts/import_csv.py on the chosen import sets in the
		background, and answer at once with its status; importStatus()
		follows it from there. A decade of M5 takes a while, and a request
		that held the page for all of it would have nothing to show but a
		spinner.

		The sets are named, not given as paths, and each name has to be a
		complete set pending() lists - so a request cannot point the import
		at a file outside IMPORT_DIR, or at a set it would only skip.
		"""
		directory = self.importDir()
		if not os.path.isdir(directory):
			raise ServiceError("%s does not exist yet; upload a file or create "
							   "it and put the exports there" % directory)
		if not sets:
			raise ServiceError("select at least one import set")
		known = dict((row['set'], row) for row in self.pending()['sets'])
		paths = []
		for name in sets:
			row = known.get(name)
			if row is None:
				raise ServiceError("no import set %r in %s" % (name, directory))
			if not row['complete']:
				raise ServiceError("%s has only its %s side; the import needs both"
								   % (name, '/'.join(sorted(row['files']))))
			paths.extend(os.path.join(directory, row['files'][side]) for side in ('ASK', 'BID'))
		with self._jobLock:
			if self._job.get('running'):
				raise ServiceError("an import is already running")
			self._job = {'running': True, 'directory': directory, 'paths': paths,
						 'done': 0, 'total': 0, 'text': 'starting', 'lines': [],
						 'ok': None}
		thread = threading.Thread(target=self._runImport, args=(self._job,))
		thread.daemon = True
		thread.start()
		return self.importStatus()

	def _runImport(self, job):
		"""
		The import itself. Under the backtest lock, so a run never reads a
		store halfway through a merge, and the cached runs are dropped: they
		were computed from candles that may just have changed.
		"""
		def progress(done, total, text):
			job.update(done=done, total=total, text=text)
		try:
			with self._lock:
				status = importer().main(
					job['paths'] + ['--data-dir', self.setup.DATA_DIR],
					report=job['lines'].append, progress=progress)
				self._cache.clear()
				del self._order[:]
			job['ok'] = status == 0
		except Exception as exc:
			self.logger.exception("import failed")
			job['lines'].append("%s: %s" % (type(exc).__name__, exc))
			job['ok'] = False
		finally:
			if not job['lines']:
				job['lines'].append("nothing to import in %s" % job['directory'])
			job['running'] = False

	def importStatus(self):
		"""Where the last import got to: running, bytes done of total, lines."""
		job = self._job
		out = dict(job, lines=list(job.get('lines', [])))
		out.pop('paths', None)
		return out

	# -------------------------------------------------------------- refusals

	def known(self, instrument, granularity=None):
		"""
		The store's row for an instrument, refusing one it does not hold.

		Its own method because two things ask it now: a backtest, and the
		chart asking for bars at a granularity of its own. The refusals are
		the same refusals, worded once.
		"""
		rows = dict((row['instrument'], row) for row in self.instruments())
		if instrument not in rows:
			raise ServiceError(
				"no store for %r in %s. It holds %s."
				% (instrument, self.setup.DATA_DIR,
				   ", ".join(sorted(rows)) or "nothing"))
		names = [g['granularity'] for g in rows[instrument]['granularities']]
		if granularity is not None and granularity not in names:
			raise ServiceError(
				"%s has no %s candles; it holds %s"
				% (instrument, granularity, ", ".join(names)))
		return rows[instrument]

	def check(self, instrument, granularity, strategy):
		"""Everything that has to be true before a backtest is worth starting."""
		known = self.known(instrument, granularity)
		if strategy not in strategies():
			raise ServiceError(
				"no strategy %r; this runs %s"
				% (strategy, ", ".join(strategies())))
		return known

	def span(self, known, granularity, dtfrom, dtto, capped=True):
		"""
		How many bars the window holds, refusing one too wide to draw unless
		capped is False - a run somebody was warned about and confirmed.

		Counted from the store's own index rather than from the range divided
		by the interval: a market that is shut at the weekend has fewer bars
		than arithmetic would predict, and refusing a window that would have
		fitted is as wrong as accepting one that will not.
		"""
		import pandas as pd
		index = store.indexes(self.storePath(known['instrument']))[granularity]
		inside = index[(index >= pd.Timestamp(dtfrom)) & (index <= pd.Timestamp(dtto))]
		count = int(len(inside))
		if count == 0:
			raise ServiceError(
				"%s %s holds no candles between %s and %s"
				% (known['instrument'], granularity, dtfrom.date(), dtto.date()))
		if capped and count > self.max_candles:
			raise ServiceError(
				"that window holds %d %s candles and the limit is %d. Narrow "
				"the dates, ask for a coarser granularity, or raise the limit "
				"with --max-candles - a chart handed this many bars is not a "
				"chart, and drawing only the first few thousand would show a "
				"range nobody asked for."
				% (count, granularity, self.max_candles))
		return count

	# -------------------------------------------------------------- saved runs

	#: how many runs are kept on disk; the oldest goes when one more is saved
	RUNS_KEEP = 50

	def runsDir(self):
		return getattr(self.setup, 'RUNS_DIR', None) \
			or os.path.join(self.setup.DATA_DIR, 'runs')

	@staticmethod
	def runId(fields):
		"""The same form is the same run: its id is a hash of the fields."""
		fields = dict((k, str(v)) for k, v in fields.items()
					  if v is not None and v != ''
					  and k not in ('cachedOnly', 'confirmed'))
		return hashlib.sha1(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]

	def saveRun(self, fields, payload):
		"""
		Keep a finished run on disk, so it can be reopened after the cache
		has let it go or the service has restarted. The payload is gzipped
		next to a small summary the list reads without opening it.
		"""
		where = self.runsDir()
		os.makedirs(where, exist_ok=True)
		run = self.runId(fields)
		trades = payload.get('trades') or []
		summary = {'id': run, 'saved': int(time.time() * 1000),
				   'fields': fields, 'strategy': payload.get('strategy'),
				   'instrument': payload.get('instrument'),
				   'granularity': payload.get('granularity'),
				   'fine': payload.get('fine'), 'from': payload.get('from'),
				   'to': payload.get('to'), 'trades': len(trades),
				   'balance': next((t['balance'] for t in reversed(trades)
									if t.get('balance') is not None), None)}
		# written aside and renamed, so a list read mid-write never sees half
		for name, body in ((run + '.json.gz', gzip.compress(json.dumps(
								{'fields': fields, 'payload': payload}).encode())),
						   (run + '.meta.json', json.dumps(summary).encode())):
			part = os.path.join(where, name + '.part')
			with open(part, 'wb') as handle:
				handle.write(body)
			os.replace(part, os.path.join(where, name))
		for old in self.runs()[self.RUNS_KEEP:]:
			for name in (old['id'] + '.json.gz', old['id'] + '.meta.json'):
				try:
					os.remove(os.path.join(where, name))
				except OSError:
					pass

	def runs(self):
		"""The saved runs' summaries, newest first."""
		where = self.runsDir()
		if not os.path.isdir(where):
			return []
		out = []
		for name in os.listdir(where):
			if not name.endswith('.meta.json'):
				continue
			try:
				with open(os.path.join(where, name)) as handle:
					out.append(json.load(handle))
			except (OSError, ValueError):
				continue
		return sorted(out, key=lambda r: r.get('saved', 0), reverse=True)

	def savedRun(self, run):
		"""{'fields', 'payload'} of one saved run, or None."""
		if not re.fullmatch(r'[0-9a-f]{16}', run or ''):
			raise ServiceError("no such run")
		path = os.path.join(self.runsDir(), run + '.json.gz')
		if not os.path.exists(path):
			return None
		with gzip.open(path, 'rb') as handle:
			return json.loads(handle.read())

	# ------------------------------------------------------------ favourites

	def favouritesPath(self):
		return os.path.join(getattr(self.setup, 'DATA_DIR', '') or '.', 'favourites.json')

	def favourites(self):
		"""
		The forms marked as favourites, newest first.

		A favourite is a simulated form - a saved backtest run or one run of
		a sweep - that somebody starred, with a snapshot of what its
		simulation made. The live page offers these as the strategies to
		trade, so what goes live is a form somebody looked at the result of,
		not a strategy name with its defaults.
		"""
		try:
			with open(self.favouritesPath()) as handle:
				rows = json.load(handle)
		except (OSError, ValueError):
			return []
		return sorted(rows, key=lambda r: r.get('added', 0), reverse=True)

	def _writeFavourites(self, rows):
		path = self.favouritesPath()
		os.makedirs(os.path.dirname(path), exist_ok=True)
		self._write(path, json.dumps(rows, indent=1).encode())

	def summarise(self, fields, report, start, final, dtfrom, dtto, kpi=None, curve=None):
		"""One shape for what a simulation made, whichever page it ran on."""
		report = report or {}
		if kpi is None and curve and start is not None and dtfrom is not None and dtto is not None:
			kpi = report_module.kpis([(moment(t), b) for t, b in curve], float(start),
									 moment(dtfrom), moment(dtto), report)
		kpi = kpi or {}
		return {'strategy': fields.get('strategy'), 'instrument': fields.get('instrument'),
				'granularity': fields.get('granularity'), 'from': dtfrom, 'to': dtto,
				'trades': report.get('closedTrades'), 'net': report.get('net'),
				'winRate': report.get('winRate'), 'profitFactor': report.get('profitFactor'),
				'expectancy': report.get('expectancy'), 'maxDrawdown': report.get('maxDrawdown'),
				'start': start, 'final': final, 'roi': kpi.get('roi'), 'car': kpi.get('car'),
				'maxDrawdownPct': kpi.get('maxDrawdownPct'), 'sharpe': kpi.get('sharpe')}

	def simulated(self, source, job=None):
		"""
		(fields, summary, name) of a simulated form, read off what the run
		left on disk: {'kind': 'run', 'id'} is a saved backtest,
		{'kind': 'sweep', 'id', 'n'} one run of a saved sweep - of `job`,
		when the sweep is already open.
		"""
		kind = (source or {}).get('kind')
		if kind == 'run':
			saved = self.savedRun(source.get('id'))
			if saved is None:
				raise ServiceError("no such run")
			fields, payload = saved['fields'], saved['payload']
			trades = payload.get('trades') or []
			final = next((t['balance'] for t in reversed(trades)
						  if t.get('balance') is not None), None)
			curve = [(t.get('exitTime'), t.get('balance')) for t in trades
					 if t.get('exitTime') is not None and t.get('balance') is not None]
			# a viewer plugin keeps a balance per trade and declares no
			# opening one: the last balance less what the trades made, as
			# rowKpi() reads it for a sweep
			start = payload.get('balance')
			net = (payload.get('report') or {}).get('net')
			if start is None and final is not None and net is not None:
				start = final - net
			return fields, self.summarise(fields, payload.get('report'), start,
										  final, payload.get('from'), payload.get('to'),
										  curve=curve), None
		if kind == 'sweep':
			job = job or self.savedSweep(source.get('id'))
			try:
				n = int(source.get('n'))
			except (TypeError, ValueError):
				raise ServiceError("which run of the sweep? give its number")
			row = next((r for r in job['done'] if r.get('n') == n), None)
			if row is None or row.get('error'):
				raise ServiceError("sweep %s has no run %d" % (source.get('id'), n))
			fields = dict(job.get('fields') or {}, **(row.get('params') or {}))
			dtfrom = millis(parseDate(fields.get('from'), 'from'))
			dtto = millis(parseDate(fields.get('to'), 'to', end=True))
			start = row.get('balance')
			if start is None and row.get('final') is not None \
					and (row.get('report') or {}).get('net') is not None:
				start = row['final'] - row['report']['net']
			return fields, self.summarise(fields, row.get('report'), start, row.get('final'),
										  dtfrom, dtto, kpi=row.get('kpi')), job.get('name')
		raise ServiceError("a favourite is a saved run or a run of a sweep")

	def addFavourite(self, source, note=None):
		"""
		Star a simulated form. Starring it again refreshes the snapshot: the
		form is the identity, the same way a saved run's id is.
		"""
		fields, summary, name = self.simulated(source)
		entry = {'id': self.runId(fields), 'added': int(time.time() * 1000),
				 'note': (note or '').strip(), 'fields': fields,
				 # n as a number: the pages match a row on it with ===
				 'source': {'kind': source['kind'], 'id': source.get('id'),
							'n': int(source['n']) if source['kind'] == 'sweep' else None,
							'name': name},
				 'summary': summary}
		with self._jobLock:
			rows = [r for r in self.favourites() if r.get('id') != entry['id']]
			old = next((r for r in self.favourites() if r.get('id') == entry['id']), None)
			if old is not None and not entry['note']:
				entry['note'] = old.get('note') or ''
			rows.append(entry)
			self._writeFavourites(rows)
		return entry

	def dropFavourite(self, favourite):
		with self._jobLock:
			rows = [r for r in self.favourites() if r.get('id') != favourite]
			self._writeFavourites(rows)

	def noteFavourite(self, favourite, note):
		with self._jobLock:
			rows = self.favourites()
			row = next((r for r in rows if r.get('id') == favourite), None)
			if row is None:
				raise ServiceError("no such favourite")
			row['note'] = (note or '').strip()
			self._writeFavourites(rows)

	# ----------------------------------------------------------------- mixes

	def mixesPath(self):
		return os.path.join(getattr(self.setup, 'DATA_DIR', '') or '.', 'mixes.json')

	def mixes(self):
		"""
		The mixes kept, newest first: {id, name, saved, items}, an item being
		one run of a saved sweep, {sweep, n}. A mix is those runs traded side
		by side, each on its own capital: see mix().
		"""
		try:
			with open(self.mixesPath()) as handle:
				rows = json.load(handle)
		except (OSError, ValueError):
			return []
		return sorted(rows, key=lambda r: r.get('saved', 0), reverse=True)

	def _writeMixes(self, rows):
		path = self.mixesPath()
		os.makedirs(os.path.dirname(path), exist_ok=True)
		self._write(path, json.dumps(rows, indent=1).encode())

	def saveMix(self, mix):
		"""Keep a mix, a new one when it comes without an id; every run is checked."""
		items = []
		for item in mix.get('items') or []:
			if not isinstance(item, dict):
				raise ServiceError("an item of a mix is {sweep, n}")
			self.simulated({'kind': 'sweep', 'id': item.get('sweep'), 'n': item.get('n')})
			items.append({'sweep': item['sweep'], 'n': int(item['n'])})
		mix_id = mix.get('id') or secrets.token_hex(8)
		if not re.fullmatch(r'[0-9a-f]{16}', str(mix_id)):
			raise ServiceError("no such mix")
		entry = {'id': mix_id, 'name': (mix.get('name') or '').strip()[:120],
				 'saved': int(time.time() * 1000), 'items': items,
				 # the account's n:1, None the setting's (report.margin)
				 'leverage': parseLeverage(_text(mix.get('leverage')))}
		with self._jobLock:
			self._writeMixes([r for r in self.mixes() if r.get('id') != mix_id] + [entry])
		return entry

	def dropMix(self, mix):
		with self._jobLock:
			self._writeMixes([r for r in self.mixes() if r.get('id') != mix])

	def mix(self, mix):
		"""
		A mix drawn: each run's capital curve, and their profits added up on
		one account of the default capital (setup.EQUITY) - not their
		capitals added up. Before a run starts it has made nothing, after it
		ends its last. The account's margin (report.margin) is checked on
		every run's trades at once, at the mix's leverage.

		The trade figures of the whole are read off the curves' steps, which
		are money: a report's own are price x units, and those of two
		instruments do not add up.
		"""
		saved = next((m for m in self.mixes() if m.get('id') == mix), None)
		if saved is None:
			raise ServiceError("no such mix")
		leverage = saved.get('leverage') or self.leverage()
		runs, jobs = [], {}
		for item in saved['items']:
			try:
				if item['sweep'] not in jobs:
					jobs[item['sweep']] = self.savedSweep(item['sweep'])
				job = jobs[item['sweep']]
				fields, summary, name = self.simulated(
					{'kind': 'sweep', 'id': item['sweep'], 'n': item['n']}, job)
			except ServiceError as exc:
				runs.append(dict(item, error=str(exc)))
				continue
			row = next(r for r in job['done'] if r.get('n') == item['n'])
			runs.append(dict(item, name=name, fields=fields, summary=summary,
							 params=row.get('params'), varied=job.get('varied'),
							 margin=row.get('margin'),
							 # by time alone: two trades closing on one bar keep
							 # their order, which sorting on the balance too lost
							 curve=sorted(row.get('curve') or [], key=lambda p: p[0])))
		ok = [r for r in runs if not r.get('error') and r['summary']['start'] is not None]
		out = dict(saved, leverage=leverage, runs=runs, total=None, missing=[])
		if not ok:
			return out
		start = float(self.setup.EQUITY)
		now = [r['summary']['start'] for r in ok]
		curve, steps, total = [], [], start
		for t, k, balance in sorted(((t, k, b) for k, r in enumerate(ok) for t, b in r['curve']),
									key=lambda e: e[0]):
			steps.append(balance - now[k])
			total += balance - now[k]
			now[k] = balance
			curve.append([t, total])
		trades = [self.sweepTrades(r['sweep'], r['n']) for r in ok]
		out['missing'] = [{'sweep': r['sweep'], 'n': r['n']} for r, t in zip(ok, trades) if t is None]
		margin = None if out['missing'] else report_module.margin(
			[t for some in trades for t in some], start, leverage)
		out['total'] = self._whole(curve, steps, start,
								   min(r['summary']['from'] for r in ok),
								   max(r['summary']['to'] for r in ok), margin)
		out['analysis'] = self._mixAnalysis(
			ok, out['total'], [(r['curve'], r['summary']['start']) for r in ok])
		return out

	def _whole(self, curve, steps, start, dtfrom, dtto, margin):
		"""The figures of a mix's account, from its curve and the money of each close."""
		wins = [s for s in steps if s > 0]
		losses = [-s for s in steps if s < 0]
		summary = {'winRate': len(wins) / len(steps) if steps else None,
				   'profitFactor': sum(wins) / sum(losses) if losses else None,
				   'expectancy': sum(steps) / len(steps) if steps else None,
				   'averageWin': sum(wins) / len(wins) if wins else None,
				   'averageLoss': sum(losses) / len(losses) if losses else None,
				   'closedTrades': len(steps), 'margin': margin}
		final = curve[-1][1] if curve else start
		kpi = report_module.kpis([(moment(t), b) for t, b in curve], float(start),
								 moment(dtfrom), moment(dtto), summary)
		return dict(summary, kpi=kpi, start=start, final=final, net=final - start,
					trades=len(steps), wins=len(wins), losses=len(losses),
					maxDrawdown=report_module.drawdown([b for _, b in curve], start)[0],
					curve=curve, **{'from': dtfrom, 'to': dtto})

	def _mixAnalysis(self, runs, total, parts):
		"""
		The mix read as a whole: what each run made of it, how their days
		move together, and what trading them together saved on the drawdown.
		`parts` is each run's (curve, opening capital), in the order of `runs`.
		"""
		dtfrom, dtto = moment(total['from']), moment(total['to'])
		daily = []
		for curve, start in parts:
			days = [b for _, b in report_module.days(
				[(moment(t), b) for t, b in curve], start, dtfrom, dtto)]
			daily.append([b - a for a, b in zip([start] + days, days)])
		rows = []
		for i, (run, (curve, start)) in enumerate(zip(runs, parts)):
			net = (curve[-1][1] if curve else start) - start
			rest = [sum(d[j] for k, d in enumerate(daily) if k != i) for j in range(len(daily[i]))]
			rows.append({'sweep': run['sweep'], 'n': run['n'], 'net': net,
						 'share': net / total['net'] * 100 if total['net'] else None,
						 'maxDrawdown': report_module.drawdown([b for _, b in curve], start)[0],
						 'withRest': report_module.correlation(daily[i], rest) if len(runs) > 1 else None})
		alone = sum(r['maxDrawdown'] for r in rows)
		# ponytail: P&L is in each instrument's quote currency and is added
		# up as it is; convert at the day's rate if the mixes ever span them
		currencies = sorted({(r['summary'].get('instrument') or '').rpartition('_')[2] for r in runs} - {''})
		return {'runs': rows,
				'matrix': [[report_module.correlation(a, b) if i != j else 1.0
							for j, b in enumerate(daily)] for i, a in enumerate(daily)],
				'drawdownAlone': alone,
				'diversification': (1 - total['maxDrawdown'] / alone) * 100 if alone else None,
				'currencies': currencies}

	def sweepTrades(self, sweep, n):
		"""
		The trades of a saved sweep run, only what report.margin and
		report.together read; None when the run has no file.
		"""
		path = self.sweepRunPath(sweep, n)
		try:
			stamp = os.path.getmtime(path)
		except OSError:
			return None
		return _slimTrades(path, stamp)

	def mixTogether(self, mix):
		"""
		The mix's runs traded on one account (report.together), drawn in
		the shape mix() draws the summed one. Every run needs its trades.
		"""
		drawn = self.mix(mix)
		if drawn['missing'] or not drawn['total']:
			raise ServiceError("some runs of the mix have no saved trades")
		ok = [r for r in drawn['runs'] if not r.get('error') and r['summary']['start'] is not None]
		start = float(self.setup.EQUITY)
		got = report_module.together(
			[{'trades': self.sweepTrades(r['sweep'], r['n']), 'start': r['summary']['start'],
			  # sized off its capital: a risk, or a plugin's engine, which sizes on its own
			  'scaled': bool(r['fields'].get('risk'))
						or r['fields'].get('strategy') in plugins.viewers()} for r in ok],
			start, drawn['leverage'])
		total = self._whole(got['curve'], got['steps'], start,
							drawn['total']['from'], drawn['total']['to'], got['margin'])
		parts = [{'sweep': r['sweep'], 'n': r['n'], 'curve': got['parts'][i], 'start': start,
				  'taken': got['taken'][i], 'refused': got['refused'][i]}
				 for i, r in enumerate(ok)]
		analysis = self._mixAnalysis(ok, total, [(p['curve'], start) for p in parts])
		for row, part in zip(analysis['runs'], parts):
			row['refused'] = part['refused']
		return {'id': mix, 'total': total, 'runs': parts, 'analysis': analysis,
				'refused': sum(got['refused'])}

	def startTogether(self, mix):
		"""
		Simulate the mix together: at once when every run has its trades
		saved, else in the background, running the runs that have none
		first (as the run page does) - togetherStatus() follows it.
		"""
		drawn = self.mix(mix)
		if not drawn['missing']:
			return {'running': False, 'mix': mix, 'result': self.mixTogether(mix)}
		with self._jobLock:
			if getattr(self, '_together', None) and self._together.get('running'):
				raise ServiceError("a mix is being simulated already")
			self._together = {'running': True, 'mix': mix, 'total': len(drawn['missing']),
							  'done': 0, 'current': None}
		thread = threading.Thread(target=self._runTogether,
								  args=(self._together, drawn['missing']))
		thread.daemon = True
		thread.start()
		return self.togetherStatus()

	def _runTogether(self, job, missing):
		try:
			for item in missing:
				job['current'] = item
				fields, _, _ = self.simulated({'kind': 'sweep', 'id': item['sweep'], 'n': item['n']})
				payload = self.backtest(confirmed=True,
										**backtestArgs(lambda name: _text(fields.get(name))))
				self.saveSweepRun(item['sweep'], item['n'], payload)
				job['done'] += 1
			job['result'] = self.mixTogether(job['mix'])
		except ServiceError as exc:
			job['error'] = str(exc)
		except Exception as exc:
			self.logger.exception("mix %s together failed" % job['mix'])
			job['error'] = "%s: %s" % (type(exc).__name__, exc)
		finally:
			job['current'] = None
			job['running'] = False

	def togetherStatus(self):
		"""Where simulating a mix together has got to, and its result when done."""
		job = getattr(self, '_together', None)
		if job is None:
			return {'running': False}
		out = dict(job)
		if job.get('current'):
			out['progress'] = self.progress()
		return out

	# -------------------------------------------------------------- the work

	def indicatorSpecs(self, strategy, params=None):
		"""
		The curves the chosen strategy wants drawn over its candles.

		Declared by the strategy and read off it - `INDICATORS` on the class,
		or 'indicators' on a plugin - because a period typed into the viewer
		is a period that drifts from the one being traded. A plugin may give a
		callable instead of a list, since its curves can depend on the
		parameters the run was asked for.

		A strategy that declares nothing gets nothing. That is a strategy
		reading no average, not a strategy whose average is missing.
		"""
		plugin = plugins.viewers().get(strategy)
		if plugin is not None:
			declared = plugin.get('indicators')
			return list(declared(params) if callable(declared) else (declared or []))
		try:
			handler = ledger.load_strategy(strategy)
		except ledger.LedgerError:
			return []
		return list(getattr(handler, 'INDICATORS', ()) or [])

	def setupBars(self, strategy):
		"""
		How many bars back from the signal the entry rule reads.

		Declared by the strategy, like INDICATORS and DESCRIPTION are, and for
		the same reason: a box the page drew to a size of its own choosing
		would be the page inventing what the rule looked at. A strategy that
		declares nothing gets no box.
		"""
		plugin = plugins.viewers().get(strategy)
		if plugin is not None:
			return plugin.get('setupBars')
		try:
			handler = ledger.load_strategy(strategy)
		except ledger.LedgerError:
			return None
		bars = getattr(handler, 'SETUP_BARS', None)
		return int(bars) if bars else None

	def key(self, instrument, granularity, strategy, dtfrom, dtto, units,
			params=None, balance=None, risk=None, maxStopPips=None,
			session=None, intraday=False, news=None, newsImpacts=None,
			maxBars=None, strategyArgs=None, slScale=None, tpScale=None,
			inverse=False, trailing=None, trailProfit=False, trailPips=None):
		# a plugin's parameters are part of the question, so they are part of
		# the key. Leaving them out would serve the first combination asked
		# for to every later request for a different one - and the same goes
		# for the hours, the overnight rule and the news windows: each of
		# them is a different run of the same strategy
		return (instrument, granularity, strategy, dtfrom, dtto, units,
				params.label() if params is not None else None, balance, risk,
				maxStopPips, session, bool(intraday), news,
				tuple(newsImpacts) if newsImpacts else None, maxBars,
				tuple(sorted(strategyArgs.items())) if strategyArgs else None,
				slScale, tpScale, bool(inverse), trailing, bool(trailProfit), trailPips)

	def window(self, known, granularity):
		for row in known['granularities']:
			if row['granularity'] == granularity:
				return row
		raise ServiceError("%s has no %s candles" % (known['instrument'],
													  granularity))

	def backtest(self, instrument, granularity, strategy='AG01', dtfrom=None,
				 dtto=None, units=1, params=None, balance=None, risk=None,
				 maxStopPips=None, session=None, intraday=False, news=None,
				 newsImpacts=None, maxBars=None, strategyArgs=None,
				 slScale=None, tpScale=None, inverse=False, trailing=None,
				 trailProfit=False, trailPips=None, cachedOnly=False, confirmed=False,
				 leverage=None, hold=None):
		"""
		Run one backtest and return the payload the page reads, with the
		margin its account needed at `leverage` (withMargin). cachedOnly
		answers from the cache or with None, and never starts a run: it is
		what a reloaded page asks, so a refresh redraws the last run rather
		than running it again. confirmed lifts the candle ceiling: the page
		has already told somebody how big the chart would be and they said
		yes, and a reload of that run is the same run. hold, an Event, pauses
		the run at its next progress report while it is clear (a sweep's
		pause): the time it waits counts neither in elapsed nor in the rate.
		"""
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

		bars = self.span(known, granularity, dtfrom, dtto,
						 capped=not (confirmed or cachedOnly))

		key = self.key(instrument, granularity, strategy, dtfrom, dtto, units,
					   params, balance, risk, maxStopPips, session, intraday,
					   news, newsImpacts, maxBars, strategyArgs, slScale, tpScale,
				   inverse, trailing, trailProfit, trailPips)
		if key in self._cache or cachedOnly:
			return self.withMargin(self._cache.get(key), leverage)

		with self._lock:
			if key in self._cache:
				return self.withMargin(self._cache[key], leverage)
			started = time.time()
			ahead = self.estimate(instrument, granularity, dtfrom, dtto)
			state = {'running': True, 'instrument': instrument,
					 'granularity': granularity, 'strategy': strategy,
					 'total': bars, 'bars': 0, 'at': millis(dtfrom),
					 'balance': None, 'started': started,
					 # the bars the simulator will walk, fine ones included:
					 # the size of the job, which the chart's count is not
					 'ticks': ahead['ticks'],
					 # eleven years of M5 is the best part of a minute of
					 # reading the store before the first bar reaches the bus,
					 # and 0% for a minute reads as a run that is stuck
					 'loading': True}
			self._progress = state

			def report(where):
				"""Where it is, and whether it should carry on.

				False stops the replay: see ledger.Progress. The flag is set
				by stop() from another request's thread, and read here, which
				is the only place a run can be interrupted between two bars
				rather than in the middle of one.
				"""
				nonlocal started
				if where.get('loading'):
					# still reading the candles: no bar has been simulated
					# yet, so there is no time and no balance to report
					state.update(where)
				else:
					state.update(where, at=millis(where.get('at')),
								 curve=[[millis(when), balance] for when, balance
										in where.get('curve') or ()],
								 loading=False)
				if hold is not None and not hold.is_set():
					since = time.time()
					hold.wait()
					started += time.time() - since
					state['started'] = started
				return not state.get('cancel')

			plugin = plugins.viewers().get(strategy)
			try:
				if plugin is not None:
					# a plugin that reports where it is gets the same line,
					# and a stop raised from inside its loop
					takes = inspect.signature(plugin['run']).parameters
					extra = {}
					if 'progress' in takes:
						def pluginReport(where):
							if report(where) is False:
								raise Cancelled("stopped after %d bars, at %s"
												% (where['bars'], where['at']))
						extra['progress'] = pluginReport
					# the account's rules the plugin's engine applies itself,
					# and a refusal for the ones it does not: a field that
					# changes nothing would read as a strategy it does not
					# move
					for name, label, value in (
							('slScale', 'SL x', slScale), ('tpScale', 'TP x', tpScale),
							('maxStopPips', 'max stop', maxStopPips),
							('session', 'hours', session), ('intraday', 'intraday', intraday),
							('maxBars', 'max bars', maxBars), ('news', 'news', news),
						('inverse', 'inverse', inverse),
						('trailing', 'trailing stop', trailing),
						('trailProfit', 'trailing profit', trailProfit),
						('trailPips', 'trail pips', trailPips)):
						if not value:
							continue
						if name not in takes:
							raise ServiceError("%s runs its own engine, which has no %s:"
											   " leave it empty" % (strategy, label))
						extra[name] = value
					result = plugin['run'](
						instrument, granularity,
						params if params is not None else plugin['params'](_none),
						dtfrom=dtfrom, dtto=dtto, setup=self.setup, **extra)
				else:
					result = ledger.run(instrument, granularity, strategy,
										dtfrom=dtfrom, dtto=dtto, units=units,
										setup=self.setup, balance=balance,
										risk=risk, maxStopPips=maxStopPips,
										session=session, intraday=intraday,
										news=news, newsImpacts=newsImpacts,
										maxBars=maxBars,
										strategyArgs=strategyArgs,
										slScale=slScale, tpScale=tpScale,
										inverse=inverse, trailing=trailing,
										trailProfit=trailProfit,
										trailPips=trailPips,
										progress=report)
			except Cancelled as stopped:
				# nothing is kept and nothing is cached: half a run drawn as a
				# run is the one outcome worse than no run
				raise ServiceError(
					"%s. Nothing was kept: a backtest over part of a window "
					"is not that backtest." % stopped)
			finally:
				# whatever happened, nothing is running any more. A status
				# line left saying 43% after a failure is worse than none
				state['running'] = False
			spent = time.time() - started
			if spent > 0 and ahead['ticks']:
				# what this machine actually manages, for the next estimate.
				# The last run and not an average over every run: a machine
				# that has just been given more cores should not be talked
				# out of it by a week of slower ones
				self._rate = ahead['ticks'] / spent
			payload = self.payload(result, time.time() - started,
								   self.indicatorSpecs(strategy, params),
								   self.setupBars(strategy))
			payload = self.withMargin(payload, leverage)
			self.remember(key, payload)
			return payload

	def withMargin(self, payload, leverage=None):
		"""
		The payload with what its account needed on margin (report.margin),
		at `leverage` or the setting's. Not part of the cache's key, as the
		trades do not depend on it: the cached payload is answered as it is
		when its leverage is the one asked, a copy with its own when not.
		"""
		leverage = leverage or self.leverage()
		if not payload or (payload.get('margin') or {}).get('leverage') == leverage:
			return payload
		trades = payload.get('trades') or []
		start = opening(payload.get('balance'), next(
			(t['balance'] for t in reversed(trades) if t.get('balance') is not None), None),
			(payload.get('report') or {}).get('net'))
		if start is None:
			return payload
		return dict(payload, margin=report_module.margin(trades, start, leverage))

	def leverage(self):
		return getattr(self.setup, 'LEVERAGE', None) or 30

	def series(self, instrument, granularity, dtfrom=None, dtto=None):
		"""
		The bars themselves, at whatever granularity is asked for.

		This is what the chart's zoom reads. A backtest is run on the bars the
		strategy was written for and nothing here changes that: the trades,
		the report and the levels stay the ones that run produced. What this
		serves is the same stretch of market drawn finer or coarser, so that
		"what happened inside that four hour candle" is a question the page
		can answer without re-running anything.

		The window is the chart's, and it goes to the store as a window: the
		same ceiling as a backtest applies to it, so a zoom cannot ask for a
		million bars any more than a run can.
		"""
		known = self.known(instrument, granularity)
		held = self.window(known, granularity)
		dtfrom = dtfrom or moment(held['from'])
		dtto = dtto or moment(held['to'])
		if dtto <= dtfrom:
			raise ServiceError("the window ends before it starts")
		self.span(known, granularity, dtfrom, dtto)

		frame = store.load(self.storePath(instrument), granularity,
						   dtfrom, dtto)
		# the same nine numbers in the same order as a backtest's candles, so
		# the page draws both with one function. A store without the two sides
		# quotes the mid for them rather than leaving a hole the page would
		# have to learn about
		def column(name):
			return frame[name] if name in frame else frame['mid_' + name[-1]]
		rows = [[millis(when), number(o), number(h), number(l), number(c),
				 number(ah), number(al), number(bh), number(bl)]
				for when, o, h, l, c, ah, al, bh, bl
				in zip(frame.index, column('mid_o'), column('mid_h'),
					   column('mid_l'), column('mid_c'), column('ask_h'),
					   column('ask_l'), column('bid_h'), column('bid_l'))]
		return {
			'instrument': instrument,
			'granularity': granularity,
			'from': millis(dtfrom),
			'to': millis(dtto),
			'candles': rows,
			# what else this instrument could be drawn at, so the page's
			# ladder is the store's and not a list typed into the JavaScript
			'granularities': [row['granularity']
							  for row in known['granularities']],
		}

	# ------------------------------------------------------------------ live

	def liveWindow(self, dtfrom, dtto, granularity):
		"""
		The window a live query covers: the last day unless asked otherwise,
		and never more bars than a backtest may draw.
		"""
		dtto = dtto or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
		dtfrom = dtfrom or dtto - datetime.timedelta(days=1)
		if dtto <= dtfrom:
			raise ServiceError("the window ends before it starts")
		step = store.granularityToTimedelta(granularity)
		if step is None:
			raise ServiceError("unknown granularity %r" % granularity)
		bars = (dtto - dtfrom) / step.to_pytimedelta()
		if bars > self.max_candles:
			raise ServiceError("%d bars of %s in that window, and one reply may "
							   "carry %d: narrow it" % (bars, granularity, self.max_candles))
		return dtfrom, dtto

	def liveCandles(self, instrument, granularity, dtfrom=None, dtto=None):
		"""Every feed's bars of one instrument, for the live page's chart."""
		dtfrom, dtto = self.liveWindow(dtfrom, dtto, granularity)
		out = self.candles.rows(instrument, granularity, dtfrom, dtto)
		out.update({'instrument': instrument, 'granularity': granularity,
					'from': millis(dtfrom), 'to': millis(dtto)})
		return out

	def liveSkew(self, instrument, granularity, dtfrom=None, dtto=None):
		"""Each feed against the reference, and the Twelve Data budget."""
		dtfrom, dtto = self.liveWindow(dtfrom, dtto, granularity)
		out = self.candles.skew(instrument, granularity, dtfrom, dtto, setup=self.setup)
		out['twelvedata'] = {
			'calls_today': self.candles.spentToday('twelvedata'),
			'limit': int(getattr(self.setup, 'TWELVEDATA_DAILY_LIMIT', 800)),
			'reserve': int(getattr(self.setup, 'TWELVEDATA_RESERVE', 40)),
			'last_call': self.candles.lastCall('twelvedata'),
		}
		return out

	def liveTradeSkew(self):
		"""Every broker session's trades against its group's paper session."""
		return livesessions.tradeSkew(self.live.sessions(), self.setup)

	def collector(self, origin=None, dtfrom=None):
		"""
		The collector script, with this service's address and token in it.

		Handed out rather than served flat, because the three things it needs
		- where to push, what to carry, and which week to start at - belong
		to this running service and not to a file on disk.

		The starting week is the caller's, or the first one worth reading:
		the day after the calendar already covers, and failing that the day
		the stores start. Nobody needs news from before the candles.
		"""
		here = os.path.join(os.path.dirname(os.path.abspath(__file__)),
							'static', 'ff-calendar.js')
		with open(here, encoding='utf-8') as handle:
			source = handle.read()
		start = dtfrom or self.calendar()['start']
		return (source.replace('__PUSH_TO__', origin or '')
					  .replace('__FROM__', start)
					  .replace('__TOKEN__', self.token))

	def calendarStart(self, held):
		"""
		The first week worth reading, as a date.

		The history comes first. A calendar that starts after the candles do
		is a calendar with the past missing, and the past is what a backtest
		reads - so the answer there is the day the stores start, not the day
		after the last event imported. It is the day after the last event
		only when the beginning is already covered, which is the case where
		what is missing is the weeks since.

		Never before the stores start either: news with no candles under it
		answers nothing.
		"""
		earliest = None
		for row in self.instruments():
			for series in row['granularities']:
				when = moment(series['from'])
				if earliest is None or when < earliest:
					earliest = when
		earliest = earliest or datetime.datetime(2015, 1, 1)
		# by the day and not by the instant: the collector reads whole weeks,
		# so a calendar whose first event is the afternoon of the day the
		# candles start has that day, and asking for it again would be a
		# fortnight of requests to learn nothing
		if not held['events'] or moment(held['from']).date() > earliest.date():
			return earliest.strftime('%Y-%m-%d')
		return (moment(held['to'])
				+ datetime.timedelta(days=1)).strftime('%Y-%m-%d')

	def calendar(self):
		"""
		What the economic calendar file holds, or that there is none.

		A missing file is not an error here either: it is a machine that has
		not imported one, and every rule that reads it is off by default.
		"""
		where = calendar_module.path(self.setup)
		frame = calendar_module.load(where)
		out = {'path': where, 'events': int(len(frame)), 'from': None,
			   'to': None, 'impacts': {}}
		if len(frame):
			out['from'] = millis(frame['time'].iloc[0])
			out['to'] = millis(frame['time'].iloc[-1])
			out['impacts'] = dict((str(k), int(v)) for k, v
								  in frame['impact'].value_counts().items())
		# and where a collector should start, which is not the same question:
		# see calendarStart
		out['start'] = self.calendarStart(out)
		return out

	def importCalendar(self, text):
		"""
		Merge an uploaded calendar into the file.

		Merged and not replaced: the collector that runs in a browser is
		pointed at a stretch of weeks at a time, and the second stretch must
		not throw away the first. What arrives is four columns - the same
		four scripts/import_calendar.py writes - and anything else is refused
		rather than half read.
		"""
		import pandas as pd

		if not text or not text.strip():
			raise ServiceError("nothing to import: the file is empty")
		try:
			added = pd.read_csv(io.StringIO(text))
		except Exception as exc:
			raise ServiceError("that file is not a CSV: %s" % exc)
		# the columns first and the dates after, so a file that is not a
		# calendar is told what a calendar is rather than being handed
		# pandas' opinion about a column it could not find
		missing = [c for c in calendar_module.COLUMNS if c not in added]
		if missing:
			raise ServiceError(
				"that file is missing %s; a calendar is %s"
				% (", ".join(missing), ", ".join(calendar_module.COLUMNS)))
		try:
			added['time'] = pd.to_datetime(added['time'])
		except Exception as exc:
			raise ServiceError("its time column is not made of times: %s" % exc)

		where = calendar_module.path(self.setup)
		before = len(calendar_module.load(where))
		frame = calendar_module.merge(
			calendar_module.load(where),
			added[list(calendar_module.COLUMNS)].itertuples(index=False,
															name=None))
		calendar_module.save(frame, where)
		out = self.calendar()
		out['added'] = int(len(frame) - before)
		out['read'] = int(len(added))
		return out

	def estimate(self, instrument, granularity, dtfrom=None, dtto=None):
		"""
		How big a job this run is, before anybody waits for it.

		The number that matters is not the chart's candles but the bars the
		simulator will actually walk: the fine series the orders rest on is
		forty-eight times the H4 one under it, and it is what turns ten
		seconds into ten minutes. The page warns on it and asks.

		The seconds are an estimate and are called one. The rate is whatever
		the last run on this machine managed, which is the best guess
		available and still only a guess: a strategy carrying more orders is
		slower per bar than one carrying fewer.
		"""
		known = self.known(instrument, granularity)
		held = self.window(known, granularity)
		dtfrom = dtfrom or moment(held['from'])
		dtto = dtto or moment(held['to'])
		fine = shadow.finer(instrument, granularity, dtfrom, dtto, self.setup)
		bars, total = shadow.ticks(instrument, granularity, fine, dtfrom,
								   dtto, self.setup)
		return {'instrument': instrument, 'granularity': granularity,
				'fine': fine, 'bars': bars, 'ticks': total,
				'seconds': round(total / self._rate, 1),
				'rate': round(self._rate),
				# the chart's side of the size: over the limit the page asks
				# before running, and sends confirmed when told yes
				'limit': self.max_candles}

	def stop(self):
		"""
		Ask the backtest running now to stop.

		Cooperative, because the run is a loop over bars in another thread and
		killing a thread mid-bar would leave the simulator's book in a state
		nobody reasons about. The flag is read where the progress is written,
		so it takes effect at the next report: a quarter of a second of bars
		while the replay is going, and a hundredth of the reading while the
		candles are still being read.
		"""
		state = self._progress
		if not state.get('running'):
			raise ServiceError("no backtest is running")
		state['cancel'] = True
		return {'stopping': True, 'bars': state.get('bars'),
				'total': state.get('total'), 'at': state.get('at')}

	def busy(self):
		"""
		What the menu lights up on every page: a sweep or a backtest going,
		and how many live sessions have their process up. Asked every few
		seconds by every page open, so it reads flags and pids and nothing
		else - no event log, no candles.
		"""
		sweep = getattr(self, '_sweep', None) or {}
		together = getattr(self, '_together', None) or {}
		return {'simulate': bool(sweep.get('running') or together.get('running')
								 or self._progress.get('running')),
				'live': self.live.running()}

	def progress(self):
		"""
		Where the backtest running now has got to.

		Polled by the page while it waits. A run over eleven years of M5 is a
		million bars through the bus and several minutes of somebody watching
		nothing happen - the two things worth showing are which bar it is on
		and what the account is worth there, which is what ledger.Progress
		reports.
		"""
		return dict(self._progress)

	# ---------------------------------------------------------------- sweeps

	def startSweep(self, fields, grid, name=None):
		"""
		Run one backtest per combination of the grid, one after the other,
		in the background; sweepStatus() follows it.

		`fields` is the form a single run is made of, `grid` is {field: text}
		with the values each varied field takes (see gridValues). Every
		combination is parsed before the first one runs, so a bad value is a
		refusal now and not a failure forty runs in.

		Each run is an ordinary backtest(): same lock, same cache, same
		progress line and the same stop - which is what lets the page draw
		the capital curve of the one running now.
		"""
		combos = expandGrid(grid)
		runs = []
		for combo in combos:
			merged = dict(fields, **combo)
			runs.append((combo, backtestArgs(lambda name: _text(merged.get(name)))))
		self.check(runs[0][1]['instrument'], runs[0][1]['granularity'],
				   runs[0][1]['strategy'])
		with self._jobLock:
			if getattr(self, '_sweep', {}).get('running'):
				raise ServiceError("a sweep is already running")
			self._sweep = {'running': True, 'total': len(runs), 'done': [],
						   'fields': fields, 'grid': grid,
						   'id': time.strftime('%Y%m%d-%H%M%S-')
								 + secrets.token_hex(3),
						   'name': (name or '').strip()[:120],
						   'current': None, 'cancel': False,
						   'varied': [name for name in grid
									  if len(gridValues(name, grid[name])) > 1],
						   'started': time.time()}
			self._sweepGoing.set()
		thread = threading.Thread(target=self._runSweep, args=(self._sweep, runs))
		thread.daemon = True
		thread.start()
		return self.sweepStatus()

	def _runSweep(self, job, runs):
		try:
			for n, (combo, args) in enumerate(runs, 1):
				# paused between two runs, or in one (hold): a plugin that
				# reports no progress only stops here
				self._sweepGoing.wait()
				if job['cancel']:
					break
				job['current'] = {'n': n, 'params': combo}
				row = {'n': n, 'params': combo}
				try:
					payload = self.backtest(confirmed=True, hold=self._sweepGoing, **args)
				except (ServiceError, ledger.LedgerError) + _pluginErrors() as exc:
					if job['cancel']:
						break
					row['error'] = str(exc)
				else:
					trades = payload['trades']
					row.update(report=payload['report'], counts=payload['counts'],
							   balance=payload['balance'],
							   final=next((t['balance'] for t in reversed(trades)
										   if t['balance'] is not None),
										  payload['balance']),
							   curve=[[t['exitTime'], t['balance']] for t in trades
									  if t['exitTime'] is not None
									  and t['balance'] is not None],
							   elapsed=payload['elapsed'], margin=payload.get('margin'))
					row['kpi'] = rowKpi(row, payload['from'], payload['to'])
					try:
						self.saveSweepRun(job['id'], n, payload)
					except Exception:
						self.logger.exception("cannot save run %d of sweep %s"
											  % (n, job['id']))
				job['done'].append(row)
		except Exception as exc:
			self.logger.exception("sweep failed")
			job['error'] = "%s: %s" % (type(exc).__name__, exc)
		finally:
			job['current'] = None
			job['running'] = False
			job['finished'] = time.time()
			if job['done']:
				try:
					self.saveSweep(job)
				except Exception:
					self.logger.exception("cannot save sweep %s" % job['id'])

	# ------------------------------------------------------ saved sweeps

	def sweepsDir(self):
		return os.path.join(self.runsDir(), 'sweeps')

	def sweepPath(self, sweep, suffix):
		"""A saved sweep's file. The id is checked, as it reaches a path."""
		if not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{6}', sweep or ''):
			raise ServiceError("no such sweep")
		return os.path.join(self.sweepsDir(), sweep + suffix)

	def sweepSummary(self, job):
		"""What the list shows, without opening the whole set."""
		ok = [row for row in job['done'] if not row.get('error')]
		top = max(ok, key=lambda row: row.get('final') or 0) if ok else None
		fields = job.get('fields') or {}
		return {'id': job['id'], 'name': job.get('name') or '',
				'saved': int(job.get('finished', time.time()) * 1000),
				'strategy': fields.get('strategy'),
				'instrument': fields.get('instrument'),
				'granularity': fields.get('granularity'),
				'from': fields.get('from'), 'to': fields.get('to'),
				'runs': len(job['done']), 'total': job['total'],
				'leverage': fields.get('leverage'),
				'stopped': bool(job.get('cancel')), 'varied': job.get('varied'),
				'best': top and top.get('final'),
				'bestParams': top and top.get('params')}

	def _write(self, path, body):
		# aside and renamed, so the list never reads half a file
		with open(path + '.part', 'wb') as handle:
			handle.write(body)
		os.replace(path + '.part', path)

	def saveSweep(self, job):
		"""Keep a finished (or stopped) sweep on disk, next to its summary."""
		os.makedirs(self.sweepsDir(), exist_ok=True)
		kept = dict((k, v) for k, v in job.items()
					if k not in ('current', 'progress', 'running'))
		self._write(self.sweepPath(job['id'], '.json.gz'),
					gzip.compress(json.dumps(kept).encode()))
		self._write(self.sweepPath(job['id'], '.meta.json'),
					json.dumps(self.sweepSummary(job)).encode())

	def sweeps(self):
		"""The saved sweeps' summaries, newest first."""
		where = self.sweepsDir()
		out = []
		for name in (os.listdir(where) if os.path.isdir(where) else ()):
			if not name.endswith('.meta.json'):
				continue
			try:
				with open(os.path.join(where, name)) as handle:
					out.append(json.load(handle))
			except (OSError, ValueError):
				continue
		return sorted(out, key=lambda row: row.get('saved', 0), reverse=True)

	def savedSweep(self, sweep):
		"""One saved sweep, in the shape sweepStatus() answers with."""
		path = self.sweepPath(sweep, '.json.gz')
		if not os.path.exists(path):
			raise ServiceError("no such sweep")
		with gzip.open(path, 'rb') as handle:
			job = json.loads(handle.read())
		# a set saved before the KPIs existed gets them now, from its curves
		for row in job['done']:
			if not row.get('kpi') and not row.get('error'):
				fields = job.get('fields') or {}
				row['kpi'] = rowKpi(row, millis(parseDate(fields.get('from'), 'from')),
									millis(parseDate(fields.get('to'), 'to', end=True)))
			# and one saved before the score, the score from its KPIs
			if row.get('kpi') and 'score' not in row['kpi']:
				row['kpi']['score'] = report_module.score(
					row['kpi'], (row.get('report') or {}).get('closedTrades'))
		# a set saved before the margin check gets it from its saved runs,
		# and keeps it; a run with no file is looked for again next time
		late = [row for row in job['done'] if row.get('margin') is None and not row.get('error')]
		found = False
		leverage = parseLeverage(_text((job.get('fields') or {}).get('leverage')))
		for row in late:
			payload = self.withMargin(self.sweepPayload(sweep, row['n']), leverage)
			row['margin'] = (payload or {}).get('margin')
			found = found or row['margin'] is not None
			if row['margin'] and not row['margin']['ok'] and row.get('kpi'):
				row['kpi']['score'] = 0.0
		if found:
			try:
				self.saveSweep(job)
			except OSError:
				self.logger.exception("cannot keep the margins of sweep %s" % sweep)
		return dict(job, running=False, since=0, count=len(job['done']))

	def renameSweep(self, sweep, name):
		job = self.savedSweep(sweep)
		job['name'] = (name or '').strip()[:120]
		self.saveSweep(job)
		live = getattr(self, '_sweep', None)
		if live and live.get('id') == sweep:
			live['name'] = job['name']
		return self.sweepSummary(job)

	def deleteSweep(self, sweep):
		# the set the page picks up again (api/sweep) is the one held here:
		# deleted, it is forgotten too, or a reload would show it back. One
		# still running is refused - its thread would save it again
		live = getattr(self, '_sweep', None)
		if live and live.get('id') == sweep:
			if live.get('running'):
				raise ServiceError("the set is still running: stop it first")
			self._sweep = None
		for suffix in ('.json.gz', '.meta.json'):
			try:
				os.remove(self.sweepPath(sweep, suffix))
			except FileNotFoundError:
				pass
		shutil.rmtree(self.sweepPath(sweep, ''), ignore_errors=True)
		return {'deleted': sweep}

	# ponytail: every run's whole payload, a few MB each on years of H1;
	# share the candles between runs if the disk ever minds
	def saveSweepRun(self, sweep, n, payload):
		"""
		One run of a sweep as the run page draws it, in the sweep's own
		folder: its dialog reopens it after a restart, instead of running it
		again. Goes with the sweep when it is deleted.
		"""
		os.makedirs(self.sweepPath(sweep, ''), exist_ok=True)
		self._write(self.sweepRunPath(sweep, n),
					gzip.compress(json.dumps(payload).encode()))

	def sweepRunPath(self, sweep, n):
		return os.path.join(self.sweepPath(sweep, ''), '%d.json.gz' % int(n))

	def sweepRun(self, sweep, n):
		"""A run saved by saveSweepRun, with its margin, or {'cached': False}."""
		payload = self.sweepPayload(sweep, n)
		if payload is None:
			return {'cached': False}
		try:
			with open(self.sweepPath(sweep, '.meta.json')) as handle:
				leverage = json.load(handle).get('leverage')
		except (OSError, ValueError):
			leverage = None
		return self.withMargin(payload, parseLeverage(_text(leverage)))

	def sweepPayload(self, sweep, n):
		"""A run saved by saveSweepRun as it was saved, or None."""
		try:
			with gzip.open(self.sweepRunPath(sweep, n), 'rb') as handle:
				return json.loads(handle.read())
		except FileNotFoundError:
			return None

	def sweepStatus(self, since=0):
		"""
		Where the sweep has got to. `since` is how many finished runs the
		page already holds: their curves are not sent again every poll.
		"""
		job = getattr(self, '_sweep', None)
		if job is None:
			return {'running': False, 'total': 0, 'done': [], 'since': 0}
		out = dict(job, done=job['done'][since:], since=since, count=len(job['done']),
				   paused=bool(job.get('running')) and not self._sweepGoing.is_set())
		if job.get('current'):
			out['progress'] = self.progress()
		return out

	def stopSweep(self):
		"""Stop the sweep: the run going now, and every one after it."""
		job = getattr(self, '_sweep', None)
		if not job or not job.get('running'):
			raise ServiceError("no sweep is running")
		job['cancel'] = True
		if self._progress.get('running'):
			self._progress['cancel'] = True
		# a paused sweep wakes up to see it has been stopped
		self._sweepGoing.set()
		return {'stopping': True, 'done': len(job['done']), 'total': job['total']}

	def pauseSweep(self, paused):
		"""
		Hold the sweep where it is, or let it go on. The run going now waits
		at its next progress report, a quarter of a second of bars, with the
		backtest lock held: any other backtest asked meanwhile waits too.
		"""
		job = getattr(self, '_sweep', None)
		if not job or not job.get('running'):
			raise ServiceError("no sweep is running")
		if job['cancel']:
			raise ServiceError("the sweep is stopping")
		(self._sweepGoing.clear if paused else self._sweepGoing.set)()
		return {'paused': bool(paused)}

	def remember(self, key, payload):
		self._cache[key] = payload
		self._order.append(key)
		while len(self._order) > self.cache_size:
			self._cache.pop(self._order.pop(0), None)

	def payload(self, result, elapsed, specs=None, setupBars=None):
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

		closes = [c[4] for c in candles]
		highs = [c[2] for c in candles]
		lows = [c[3] for c in candles]

		def index(when):
			# the bar covering an instant, not only one stamped with it: a run
			# filled on a finer series (backtest/shadow.py) fills mid-bar
			if when is None or not times or when < times[0]:
				return None
			return bisect.bisect_right(times, when) - 1

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
				# where a walking stop ended up, or None for one that never
				# moved. The page shows both: a trade that exited on a stop
				# 70 pips from the one it was ordered with is not a mystery
				# once the table says the stop had been moved there.
				'stopFinal': number(trade.get('stopFinal')),
				'takeProfit': number(trade['takeProfit']),
				'entryTime': entry,
				'entryPrice': number(trade['entryPrice']),
				'exitTime': exit_,
				'exitPrice': number(trade['exitPrice']),
				'outcome': trade['outcome'],
				'pl': number(trade['pl']),
				'balance': number(trade['balance']),
				# where to zoom: the run's bar the fill fell in
				'entryIndex': index(entry),
				'exitIndex': index(exit_),
				# the bar the rule fired on, which is where the box of setup
				# bars ends. Not the entry: a pending order can be filled days
				# later, and the candles that made the decision are the ones
				# up to here
				'signalIndex': index(millis(trade['signalTime'])),
			})

		return {
			'instrument': result.instrument,
			'granularity': result.granularity,
			# the finer series the orders were filled against, or None when
			# they were filled against the strategy's own bars. On the page
			# because the two are not the same measurement: see
			# backtest/shadow.py.
			'fine': getattr(result, 'fine', None),
			'strategy': result.strategy,
			'from': millis(result.dtfrom),
			'to': millis(result.dtto),
			'candles': candles,
			'trades': trades,
			'counts': result.counts,
			# the declared curves, computed here rather than in the page: the
			# maths is testable with the rest of the suite, and the page stays
			# something that draws what it is handed
			# highs and lows as well as closes: an ATR of the closes is a
			# different number wearing the same name
			'indicators': indicators.curves(specs, closes, highs, lows),
			# RG2 - the slope measure and the percentiles of it, for the
			# shading the chart offers. Not a signal and not a strategy's
			# curve: it is the reading REGIME_SLOPE_MIN will be chosen from,
			# drawn on the instrument the reader picked. None when the run is
			# too short for SMA(100) to warm up.
			'slope': slopeOverlay(closes, highs, lows),
			# how many bars back from the signal the entry rule reads, so the
			# page can box them. None for a strategy that does not say.
			'setupBars': setupBars,
			# what the account opened with. A curve of balances cannot be read
			# without it, and a viewer plugin that runs its own engine has no
			# account at all, which is None rather than a number invented here.
			'balance': getattr(result, 'balance', None),
			# the fraction of capital a trade risks, or None when the size was
			# a fixed number of units. The page says which it was, because the
			# same curve means two different things under the two rules.
			'risk': getattr(result, 'risk', None),
			# the widest stop a signal was allowed to carry, in pips, or None.
			# Like the risk, it decides which trades are in the list: a page
			# that did not say so would show a strategy trading less than it
			# does and call it the strategy.
			'maxStopPips': getattr(result, 'maxStopPips', None),
			'report': report_module.report(result.trades),
			'elapsed': round(elapsed, 3),
		}


#: how many bars at the front of a run the slope percentiles are read on. The
#: same rule the thresholds are chosen under: a percentile taken over the
#: whole run is a number that knows how the run ends, and shading the chart
#: with it would show a regime nobody could have been in at the time. A run
#: shorter than this is read on all of it and says so.
SLOPE_TRAINING = 1500

#: which percentiles the page offers as thresholds. Percentiles and not
#: values: the threshold itself has not been decided, and offering 0.11 here
#: would be deciding it in the viewer.
SLOPE_PERCENTILES = (75, 90, 95)


def slopeOverlay(closes, highs, lows):
	"""
	The slope measure per bar, and the thresholds the page can shade with.

	Sent with every backtest because it costs one float a bar and it answers
	the question the shading exists for - which bars a given threshold would
	call directional, on the instrument actually being looked at.
	"""
	values = indicators.slope(closes, highs, lows)
	warm = [abs(v) for v in values[:SLOPE_TRAINING] if v is not None]
	if len(warm) < 2:
		return None
	return {
		'values': [None if v is None else round(v, 6) for v in values],
		'period': indicators.SLOPE_PERIOD,
		'window': indicators.SLOPE_WINDOW,
		'atr': indicators.SLOPE_ATR,
		# how many bars the percentiles were read on, so the page can say it
		'trained': min(len(closes), SLOPE_TRAINING),
		'thresholds': [{'percentile': p,
						'value': round(indicators.percentile(warm, p), 5)}
					   for p in SLOPE_PERCENTILES],
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


def parseSession(text):
	"""
	'07:00-16:00' as the pair the money manager takes, or None.

	UTC, because everything here is UTC. A window whose end is before its
	start is not refused: it wraps midnight, which is what the Asian session
	looks like written down.
	"""
	if not text or not text.strip():
		return None
	start, _, end = text.partition('-')
	pair = (start.strip(), end.strip())
	if not pair[0] or not pair[1]:
		raise ServiceError("session: %r is not from-to, e.g. 07:00-16:00" % text)
	for one in pair:
		try:
			hours, _, minutes = one.partition(':')
			datetime.time(hour=int(hours), minute=int(minutes or 0))
		except (TypeError, ValueError):
			raise ServiceError("session: %r is not a time of day" % one)
	return pair


def parseNews(before, after):
	"""(minutes before, minutes after), or None when both are nothing."""
	pair = (parseInt(before, 'newsBefore', 0), parseInt(after, 'newsAfter', 0))
	if pair[0] < 0 or pair[1] < 0:
		raise ServiceError("news: minutes cannot be negative")
	return None if not pair[0] and not pair[1] else pair


def parseImpacts(text):
	"""
	Which events count, as the calendar spells them.

	Refused by name rather than ignored: a run asked to stand aside for
	'HIGH' and given one that stood aside for nothing would be a different
	backtest wearing the same label.
	"""
	if not text or not text.strip():
		return None
	wanted = tuple(one.strip().lower() for one in text.split(',') if one.strip())
	known = (calendar_module.HIGH, calendar_module.MEDIUM, calendar_module.LOW,
			 calendar_module.NON_ECONOMIC)
	for one in wanted:
		if one not in known:
			raise ServiceError("newsImpacts: %r is not one of %s"
							   % (one, ", ".join(known)))
	return wanted or None


def parseInt(text, name, default):
	if text in (None, ''):
		return default
	try:
		return int(text)
	except (TypeError, ValueError):
		raise ServiceError("%s: %r is not a number" % (name, text))


def parseAmount(text, name, default):
	"""
	A positive amount from the query, or the default.

	Zero and negative are refused rather than clamped: a starting balance of
	nothing is not a smaller account, it is a question nobody meant to ask,
	and a curve drawn from it says a trade multiplied the account by infinity.
	"""
	if text in (None, ''):
		return default
	try:
		value = float(text)
	except (TypeError, ValueError):
		raise ServiceError("%s: %r is not a number" % (name, text))
	if not value > 0:
		raise ServiceError("%s: %s is not a positive amount" % (name, text))
	return value


def parsePips(text, name, default=None):
	"""
	A distance in pips from the query, or the default for an empty field.

	Zero is not a ceiling, it is a rule that refuses every trade, so it is
	refused here rather than served as an empty backtest somebody has to work
	out the reason for.
	"""
	if text in (None, ''):
		return default
	try:
		value = float(text)
	except (TypeError, ValueError):
		raise ServiceError("%s: %r is not a number" % (name, text))
	if value <= 0:
		raise ServiceError(
			"%s: %s is not a distance in pips above zero" % (name, text))
	return value


def parsePercent(text, name, default):
	"""
	A percentage from the query, as the fraction the engine works in.

	Bounded above at 100: a trade risking more than the whole account is not
	a larger bet, it is an account that cannot pay for its own stop, and
	every number after it would be arithmetic about money nobody has.
	"""
	if text in (None, ''):
		return default
	try:
		value = float(text)
	except (TypeError, ValueError):
		raise ServiceError("%s: %r is not a number" % (name, text))
	if not 0 < value <= 100:
		raise ServiceError(
			"%s: %s is not a percentage between 0 and 100" % (name, text))
	return value / 100.0


def _none(name):
	"""A query string that says nothing, so a plugin builds its defaults."""
	return None


def _forms():
	"""strategy -> the parameter form it wants, for the page to build."""
	out = dict((name, plugin['fields']())
			   for name, plugin in plugins.viewers().items())
	for name in ledger.STRATEGIES:
		fields = handlerFields(name)
		if fields:
			out[name] = list(fields)
	return out


def _pluginErrors():
	"""Every refusal a plugin can raise, which is a 400 and not a crash."""
	out = ()
	for plugin in plugins.viewers().values():
		out = out + tuple(plugin['errors'])
	return out


#: constructor arguments that are wiring rather than a rule worth varying
NOT_PARAMS = ('pairs', 'granularity', 'pipSize')

#: the form's own fields. A strategy argument spelled like one of these would
#: be read twice from the same key, so it is not offered
FORM_FIELDS = ('instrument', 'granularity', 'strategy', 'from', 'to', 'units',
			   'balance', 'risk', 'maxStop', 'maxBars', 'slScale', 'tpScale',
			   'session', 'intraday', 'inverse', 'trailing', 'trailProfit',
			   'trailPips', 'newsBefore', 'newsAfter', 'newsImpacts')


@functools.lru_cache(maxsize=None)
def handlerFields(strategy):
	"""
	The numbers a handler strategy takes in its constructor, as a form.

	Read off its own source: every `self._set(args, 'name', default)` with a
	number for a default, walking the class and the classes it extends, the
	nearest one winning. That is where a strategy already declares what it
	accepts, so this is a reading of it and not a second list to keep in step.
	A default spelled `self.FAST` is looked up on the class. The line the
	page prints under a field is the strategy's PARAM_HELP, merged over the
	classes it extends in the same order.
	"""
	try:
		handler = ledger.load_strategy(strategy)
	except ledger.LedgerError:
		return ()
	found = {}
	helps = {}
	for klass in handler.__mro__:
		if not klass.__module__.startswith('parity_deriva.strategy'):
			continue
		for name, text in vars(klass).get('PARAM_HELP', {}).items():
			helps.setdefault(name, text)
		try:
			tree = ast.parse(textwrap.dedent(inspect.getsource(klass)))
		except (OSError, TypeError, SyntaxError):
			continue
		for node in ast.walk(tree):
			if not (isinstance(node, ast.Call)
					and isinstance(node.func, ast.Attribute)
					and node.func.attr == '_set' and len(node.args) >= 3
					and isinstance(node.args[1], ast.Constant)
					and isinstance(node.args[1].value, str)):
				continue
			name, default = node.args[1].value, node.args[2]
			if name in found or name in NOT_PARAMS or name in FORM_FIELDS:
				continue
			if isinstance(default, ast.Constant):
				value = default.value
			elif isinstance(default, ast.Attribute):
				value = getattr(handler, default.attr, None)
			else:
				continue
			if isinstance(value, (int, float)) and not isinstance(value, bool):
				found[name] = value
	return tuple({'name': name, 'label': name, 'value': value,
				  'step': 1 if isinstance(value, int) else 'any',
				  'help': helps.get(name, '')}
				 for name, value in found.items())


def handlerArgs(strategy, get):
	"""The constructor arguments the query sets, or None for the defaults."""
	out = {}
	for field in handlerFields(strategy):
		text = get(field['name'])
		if text in (None, ''):
			continue
		try:
			value = float(text)
		except (TypeError, ValueError):
			raise ServiceError("%s: %r is not a number" % (field['name'], text))
		if isinstance(field['value'], int):
			if value != int(value):
				raise ServiceError("%s: %s is not a whole number"
								   % (field['name'], text))
			value = int(value)
		if value != field['value']:
			out[field['name']] = value
	return out or None


def pluginParams(strategy, get):
	"""
	A plugin's free parameters, or None for a strategy that has none.

	The plugin builds them, so a value outside its range is the same
	refusal here as on the command line - a page cannot ask for a lookback
	of 7 and get a run that quietly did something else - and the service
	does not learn what any of them mean.
	"""
	plugin = plugins.viewers().get(strategy)
	if plugin is None:
		return None
	try:
		return plugin['params'](get)
	except plugin['errors'] as exc:
		raise ServiceError(str(exc))


def parseBars(text):
	"""How many bars a trade may stay open, or None for no limit."""
	value = parseInt(text, 'maxBars', None)
	if value is not None and value < 1:
		raise ServiceError("maxBars: %s is not a number of bars above zero" % text)
	return value


def parseScale(text, name):
	"""A multiple of the stop's or target's distance, or None for 1."""
	if text in (None, ''):
		return None
	try:
		value = float(text)
	except (TypeError, ValueError):
		raise ServiceError("%s: %r is not a number" % (name, text))
	if not value > 0:
		raise ServiceError("%s: %s is not a multiple above zero" % (name, text))
	return None if value == 1 else value


#: what the name of a strategy turned round ended in, before `inverse`
INVERSA = '-INVERSA'


def parseSwitch(text, name):
	"""'' for the strategy's own, '0' off, '1' on: None, 0 or 1."""
	if text in (None, ''):
		return None
	if text not in ('0', '1'):
		raise ServiceError("%s: %r is not empty, 0 or 1" % (name, text))
	return int(text)


def backtestArgs(get):
	"""
	The form as Service.backtest's keyword arguments. `get` takes a field
	name and returns its text or None, so a query string and a sweep's
	merged fields are read by the same code.
	"""
	instrument = get('instrument')
	granularity = get('granularity')
	if not instrument or not granularity:
		raise ServiceError("instrument and granularity are both required")
	strategy = get('strategy') or 'AG01'
	inverse = get('inverse') in ('1', 'true', 'on')
	# the saved runs and sweeps of before inverse was a rule of the account:
	# AB-INVERSA was AB turned round, and is read as that
	if strategy.endswith(INVERSA) and strategy[:-len(INVERSA)] in strategies():
		strategy, inverse = strategy[:-len(INVERSA)], True
	return dict(
		instrument=instrument,
		granularity=granularity,
		strategy=strategy,
		dtfrom=parseDate(get('from'), 'from'),
		dtto=parseDate(get('to'), 'to', end=True),
		units=parseInt(get('units'), 'units', 1),
		balance=parseAmount(get('balance'), 'balance', None),
		risk=parsePercent(get('risk'), 'risk', None),
		maxStopPips=parsePips(get('maxStop'), 'maxStop'),
		maxBars=parseBars(get('maxBars')),
		slScale=parseScale(get('slScale'), 'slScale'),
		tpScale=parseScale(get('tpScale'), 'tpScale'),
		session=parseSession(get('session')),
		intraday=get('intraday') in ('1', 'true', 'on'),
		inverse=inverse,
		trailing=parseSwitch(get('trailing'), 'trailing'),
		trailProfit=get('trailProfit') in ('1', 'true', 'on'),
		trailPips=parsePips(get('trailPips'), 'trailPips'),
		news=parseNews(get('newsBefore'), get('newsAfter')),
		newsImpacts=parseImpacts(get('newsImpacts')),
		params=pluginParams(strategy, get),
		strategyArgs=handlerArgs(strategy, get),
		leverage=parseLeverage(get('leverage')))


def parseLeverage(text):
	"""The account's leverage, n for n:1; None is the setting's."""
	value = parseInt(text, 'leverage', None)
	if value is not None and value < 1:
		raise ServiceError("leverage: %d is not n:1 with n at least 1" % value)
	return value


def opening(balance, final, net):
	"""
	A run's opening capital. A viewer plugin keeps a balance per trade but
	declares no opening one: that is the last balance less what the trades
	made, which is exact.
	"""
	if balance is None and final is not None and net is not None:
		return final - net
	return balance


# ponytail: 256 runs' trades held, a few hundred KB each; smaller if memory minds
@functools.lru_cache(maxsize=256)
def _slimTrades(path, stamp):
	"""A saved run's trades, the fields a margin reads; `stamp` is the file's
	mtime, so a run saved again is read again. Shared: never changed."""
	with gzip.open(path, 'rb') as handle:
		trades = json.loads(handle.read()).get('trades') or ()
	keep = ('entryTime', 'exitTime', 'units', 'entryPrice', 'stopLoss', 'pl')
	return tuple(dict((k, t.get(k)) for k in keep) for t in trades)


def rowKpi(row, dtfrom, dtto):
	"""
	A sweep row's KPIs (report_module.kpis), from its curve in millis.

	A viewer plugin keeps a balance per trade but declares no opening one;
	that is the last balance less what the trades made, which is exact.
	"""
	start = opening(row.get('balance'), row.get('final'), (row.get('report') or {}).get('net'))
	if start is None or dtfrom is None or dtto is None:
		return None
	return report_module.kpis([(moment(t), b) for t, b in row.get('curve') or ()],
							  float(start), moment(dtfrom), moment(dtto),
							  dict(row.get('report') or {}, margin=row.get('margin')))


def _text(value):
	"""A JSON field as the text a query string would have carried."""
	if value is None or value == '':
		return None
	if value is True:
		return '1'
	if value is False:
		return None
	return str(value)


#: the most runs one sweep may ask for. Each is a whole backtest, and a grid
#: of five fields with ten values each is a hundred thousand of them
MAX_COMBOS = 500

_RANGE = re.compile(r'^(-?\d+(?:\.\d+)?)\.\.(-?\d+(?:\.\d+)?)(?:/(\d+(?:\.\d+)?))?$')


def gridValues(name, text):
	"""
	The values one field of a sweep takes, as text.

	Comma separated. `a..b/step` is a range with both ends included (step 1
	when it is left out), and `none` is the field left empty - no ceiling,
	no hours, no limit - so "none, 30, 50" compares the rule with its
	absence. An empty box is one run with the field empty.
	"""
	out = []
	for part in str(text or '').split(','):
		part = part.strip()
		if not part:
			continue
		if part.lower() == 'none':
			out.append('')
			continue
		match = _RANGE.match(part)
		if not match:
			out.append(part)
			continue
		first, last, step = (float(g) if g else None for g in match.groups())
		step = step or 1.0
		if step <= 0 or last < first:
			raise ServiceError("%s: %r is not a range from..to/step" % (name, part))
		count = int((last - first) / step + 1e-9) + 1
		if count > MAX_COMBOS:
			raise ServiceError("%s: %r is %d values" % (name, part, count))
		whole = all(g is None or '.' not in g for g in match.groups())
		for i in range(count):
			value = round(first + i * step, 10)
			out.append(str(int(value)) if whole else repr(value))
	unique = list(dict.fromkeys(out))
	return unique or ['']


def expandGrid(grid):
	"""{field: text} -> one {field: value} per combination, refusing too many."""
	if not isinstance(grid, dict) or not grid:
		raise ServiceError('the grid is {"field": "values", ...}')
	axes = [[(name, value) for value in gridValues(name, text)]
			for name, text in grid.items()]
	total = 1
	for axis in axes:
		total *= len(axis)
	if total > MAX_COMBOS:
		raise ServiceError("that grid is %d runs and the limit is %d: fewer "
						   "values, or fewer fields varied at once"
						   % (total, MAX_COMBOS))
	return [dict(combo) for combo in itertools.product(*axes)]


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
		# the project's logging, not stderr, so a run looks like every other
		logging.getLogger(LOGGER).info(fmt % args)

	# ------------------------------------------------------------- replying

	def sendJSON(self, payload, status=200):
		body = json.dumps(payload).encode('utf-8')
		self.send_response(status)
		self.send_header('Content-Type', 'application/json; charset=utf-8')
		self.send_header('Content-Length', str(len(body)))
		self.allowCollector()
		self.end_headers()
		self.wfile.write(body)

	def allowCollector(self):
		"""
		Let the collector read the answer it just got.

		Only for its own origin and only on its own route: a reply this
		service sends to anything else carries no allowance at all, which is
		what keeps the rest of the port unreachable from a browser tab.
		"""
		origin = self.headers.get('Origin')
		route = urllib.parse.urlparse(self.path).path
		if origin == COLLECTOR_ORIGIN and route == COLLECTOR_ROUTE:
			self.send_header('Access-Control-Allow-Origin', origin)

	def do_OPTIONS(self):
		"""
		The preflight, answered for the collector and for nothing else.

		Chrome asks twice over: once for the cross-origin POST, and once more
		because a public page reaching 127.0.0.1 is a private network
		request, which wants its own permission. Both are answered here, both
		for one origin and one route.
		"""
		origin = self.headers.get('Origin')
		route = urllib.parse.urlparse(self.path).path
		if origin != COLLECTOR_ORIGIN or route != COLLECTOR_ROUTE:
			return self.sendError("no", 403)
		self.send_response(204)
		self.send_header('Access-Control-Allow-Origin', origin)
		self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
		self.send_header('Access-Control-Allow-Headers', 'Content-Type')
		# the private network permission Chrome asks for when a page on the
		# public internet reaches an address on this machine
		self.send_header('Access-Control-Allow-Private-Network', 'true')
		self.send_header('Access-Control-Max-Age', '600')
		self.send_header('Content-Length', '0')
		self.end_headers()

	def sendError(self, message, status=400):
		self.sendJSON({'error': message}, status)

	def sendFile(self, name):
		"""
		One of the files in web/static, and nothing else.

		The path is rebuilt from its basename after being normalised, so
		neither a '..' nor an absolute path nor a symlink planted in the
		directory can reach outside it - and only the extensions in
		CONTENT_TYPES are served at all. Was: three extensions, for three
		files. Now: six, for the fonts, logo and icons as well; still no
		subdirectory, so those sit flat next to the page.
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
			# the simulation is the home page: one run is a set of one. /sim
			# is kept for the links made before it was
			if route in ('/', '/sim'):
				return self.sendFile('sim.html')
			if route == '/run':
				return self.sendFile('run.html')
			if route == '/settings':
				return self.sendFile('settings.html')
			if route == '/live':
				return self.sendFile('live.html')
			if route == '/mix':
				return self.sendFile('mix.html')
			if route == '/api/live':
				return self.sendJSON({'sessions': self.service.live.sessions()})
			if route == '/api/live/targets':
				return self.sendJSON({'targets': self.service.live.targets(
					fresh=bool(self.one(query, 'fresh')))})
			# before the session route below, which would read 'candles' as
			# a session id and answer "no such session"
			if route == '/api/live/candles':
				return self.sendJSON(self.service.liveCandles(
					self.one(query, 'instrument', 'EUR_USD'),
					self.one(query, 'granularity', 'M5'),
					parseDate(self.one(query, 'from'), 'from'),
					parseDate(self.one(query, 'to'), 'to', end=True)))
			if route == '/api/live/skew':
				return self.sendJSON(self.service.liveSkew(
					self.one(query, 'instrument', 'EUR_USD'),
					self.one(query, 'granularity', 'M5'),
					parseDate(self.one(query, 'from'), 'from'),
					parseDate(self.one(query, 'to'), 'to', end=True)))
			if route == '/api/live/skew/trades':
				return self.sendJSON(self.service.liveTradeSkew())
			if route == '/api/favourites':
				return self.sendJSON({'favourites': self.service.favourites()})
			if route == '/api/mixes':
				return self.sendJSON({'mixes': self.service.mixes()})
			if route == '/api/mixes/together':
				return self.sendJSON(self.service.togetherStatus())
			if route.startswith('/api/mixes/'):
				return self.sendJSON(self.service.mix(route[len('/api/mixes/'):]))
			if route.startswith('/api/live/'):
				return self.sendJSON(self.service.live.summary(
					route[len('/api/live/'):], detail=True))
			if route == '/api/sweep':
				return self.sendJSON(self.service.sweepStatus(
					parseInt(self.one(query, 'since'), 'since', 0)))
			if route == '/api/sweeps':
				return self.sendJSON({'sweeps': self.service.sweeps()})
			if route.startswith('/api/sweeps/'):
				# <id> is the set, <id>/<n> one run of it, whole
				sweep, _, n = route[len('/api/sweeps/'):].partition('/')
				if n:
					if not n.isdigit():
						raise ServiceError("no such run")
					return self.sendJSON(self.service.sweepRun(sweep, n))
				return self.sendJSON(self.service.savedSweep(sweep))
			if route == '/api/stores':
				# the strategies come from the same place check() refuses
				# against, so the page cannot offer one the service would
				# then reject
				return self.sendJSON({
					'instruments': self.service.instruments(),
					'strategies': strategies(),
					# the parameter form of every strategy that has one. The
					# page builds the controls from this rather than holding a
					# copy in the JavaScript that would drift from the
					# strategy's own defaults
					'params': _forms(),
					# what each strategy says it does, so the page can print
					# it over the chart without holding its own copy
					'descriptions': descriptions(),
					# strategy -> the instrument and timeframe it is written
					# for, which the page selects when it is picked
					'defaults': defaults(),
					# the starting balance the page puts in its field, read
					# from the setting rather than typed into the markup,
					# where it would be a second figure to keep in step
					'equity': float(self.service.setup.EQUITY),
					# and the leverage its account is checked on (report.margin)
					'leverage': self.service.leverage()})
			if route == '/api/candles':
				return self.sendJSON(self.service.series(
					self.one(query, 'instrument', 'EUR_USD'),
					self.one(query, 'granularity', 'H1'),
					parseDate(self.one(query, 'from'), 'from'),
					parseDate(self.one(query, 'to'), 'to', end=True)))
			if route == '/api/calendar':
				return self.sendJSON(self.service.calendar())
			if route == '/api/collector':
				# the script itself, with this service's address and token in
				# it. Served as text because it is pasted into a console
				body = self.service.collector(
					self.one(query, 'origin', ''),
					self.one(query, 'from', None)).encode('utf-8')
				self.send_response(200)
				self.send_header('Content-Type',
								 'application/javascript; charset=utf-8')
				self.send_header('Content-Length', str(len(body)))
				self.end_headers()
				return self.wfile.write(body)
			if route == '/api/estimate':
				return self.sendJSON(self.service.estimate(
					self.one(query, 'instrument', 'EUR_USD'),
					self.one(query, 'granularity', 'H1'),
					parseDate(self.one(query, 'from'), 'from'),
					parseDate(self.one(query, 'to'), 'to', end=True)))
			if route == '/api/progress':
				return self.sendJSON(self.service.progress())
			if route == '/api/busy':
				return self.sendJSON(self.service.busy())
			if route == '/api/imports':
				return self.sendJSON(self.service.pending())
			if route == '/api/imports/status':
				return self.sendJSON(self.service.importStatus())
			if route == '/api/runs':
				return self.sendJSON({'runs': self.service.runs()})
			if route.startswith('/api/runs/'):
				saved = self.service.savedRun(route[len('/api/runs/'):])
				if saved is None:
					return self.sendError("no such run", 404)
				return self.sendJSON(dict(saved, payload=self.service.withMargin(
					saved['payload'], parseLeverage(_text(saved['fields'].get('leverage'))))))
			if route.startswith('/static/'):
				return self.sendFile(route[len('/static/'):])
			return self.sendError("no route %s" % route, 404)
		except (ServiceError, livesessions.LiveError) as exc:
			return self.sendError(str(exc))
		except ledger.LedgerError as exc:
			return self.sendError(str(exc))
		except _pluginErrors() as exc:
			return self.sendError(str(exc))
		except Exception as exc:
			logging.getLogger(LOGGER).exception("web request failed")
			return self.sendError("%s: %s" % (type(exc).__name__, exc), 500)

	def do_POST(self):
		"""
		The backtest and the data dialog's writes. All need the X-Parity-Deriva header,
		which a page on another site cannot send to this port without a CORS
		preflight that is never answered - so a site open in the same browser
		cannot upload into the stores or start an import.
		"""
		parsed = urllib.parse.urlparse(self.path)
		route = parsed.path
		query = urllib.parse.parse_qs(parsed.query)
		try:
			# the calendar route also takes the collector's token, because a
			# script running on forexfactory's page cannot send a custom
			# header to this port. Everything else needs the header.
			pushed = (route == COLLECTOR_ROUTE
					  and self.one(query, 'token') == self.service.token)
			if not pushed and self.headers.get('X-Parity-Deriva') != '1':
				return self.sendError("missing X-Parity-Deriva header", 403)
			if route == '/api/imports/upload':
				try:
					length = int(self.headers.get('Content-Length') or 0)
				except ValueError:
					raise ServiceError("Content-Length is not a number")
				return self.sendJSON(self.service.upload(
					self.one(query, 'name'), self.rfile, length))
			if route == '/api/backtest':
				# a POST and not a GET: running a backtest is an action, and
				# a GET is what a reload, a prefetch or a link repeats
				try:
					length = int(self.headers.get('Content-Length') or 0)
					fields = json.loads(self.rfile.read(length) or b'{}')
				except ValueError:
					raise ServiceError("the body is a JSON object of form fields")
				if not isinstance(fields, dict):
					raise ServiceError("the body is a JSON object of form fields")
				cachedOnly = bool(fields.get('cachedOnly'))
				# a run of a simulation set run again because the set was
				# saved without it: it is kept with the set, once
				sweep, sweepRun = fields.pop('sweep', None), fields.pop('sweepRun', None)
				payload = self.runBacktest(
					dict((k, [str(v)]) for k, v in fields.items()
						 if v is not None and v != ''
						 and k not in ('cachedOnly', 'confirmed')),
					cachedOnly=cachedOnly,
					confirmed=bool(fields.get('confirmed')))
				form = dict((k, v) for k, v in fields.items()
							if k not in ('cachedOnly', 'confirmed'))
				if payload is None:
					# a reload after the service restarted: the run on disk
					saved = self.service.savedRun(self.service.runId(form))
					payload = saved and self.service.withMargin(
						saved['payload'], parseLeverage(_text(form.get('leverage'))))
				elif not cachedOnly:
					self.service.saveRun(form, payload)
					if sweep and str(sweepRun).isdigit() and os.path.exists(
							self.service.sweepPath(sweep, '.meta.json')):
						self.service.saveSweepRun(sweep, sweepRun, payload)
				# the id the run is saved under: the runs dialog lists it
				return self.sendJSON(dict(payload, runId=self.service.runId(form))
									 if payload is not None else {'cached': False})
			if route == '/api/sweep':
				try:
					length = int(self.headers.get('Content-Length') or 0)
					body = json.loads(self.rfile.read(length) or b'{}')
				except ValueError:
					raise ServiceError('the body is {"fields": {...}, "grid": {...}}')
				if not isinstance(body, dict) or not isinstance(body.get('fields'), dict):
					raise ServiceError('the body is {"fields": {...}, "grid": {...}}')
				if body.get('dry'):
					return self.sendJSON({'combos': len(expandGrid(body.get('grid')))})
				return self.sendJSON(self.service.startSweep(
					body['fields'], body.get('grid'), body.get('name')))
			if route.startswith('/api/sweeps/'):
				# {"name": "..."} renames a saved sweep, {"delete": true}
				# removes it
				sweep = route[len('/api/sweeps/'):]
				try:
					length = int(self.headers.get('Content-Length') or 0)
					body = json.loads(self.rfile.read(length) or b'{}')
				except ValueError:
					raise ServiceError('the body is {"name": ...} or {"delete": true}')
				if not isinstance(body, dict):
					raise ServiceError('the body is {"name": ...} or {"delete": true}')
				if body.get('delete'):
					return self.sendJSON(self.service.deleteSweep(sweep))
				return self.sendJSON(self.service.renameSweep(sweep, body.get('name')))
			if route == '/api/sweep/stop':
				return self.sendJSON(self.service.stopSweep())
			if route in ('/api/sweep/pause', '/api/sweep/resume'):
				return self.sendJSON(self.service.pauseSweep(route == '/api/sweep/pause'))
			if route == '/api/mixes' or route.startswith('/api/mixes/'):
				# {"id"?, "name", "items": [{"sweep", "n"}]} keeps a mix,
				# {"delete": true} on /api/mixes/<id> removes one
				length = int(self.headers.get('Content-Length') or 0)
				try:
					body = json.loads(self.rfile.read(length) or b'{}')
				except ValueError:
					body = None
				if not isinstance(body, dict):
					raise ServiceError('the body is {"name": ..., "items": [{"sweep": ..., "n": ...}]}')
				if route == '/api/mixes':
					return self.sendJSON({'mix': self.service.saveMix(body),
										  'mixes': self.service.mixes()})
				if route.endswith('/together'):
					# {} simulates the mix's runs on one account (startTogether)
					return self.sendJSON(self.service.startTogether(
						route[len('/api/mixes/'):-len('/together')]))
				if body.get('delete'):
					self.service.dropMix(route[len('/api/mixes/'):])
					return self.sendJSON({'mixes': self.service.mixes()})
				return self.sendError("no route %s" % route, 404)
			if route == '/api/live':
				# {"fields": the backtest form, "targets": [{provider, account}]}
				length = int(self.headers.get('Content-Length') or 0)
				try:
					body = json.loads(self.rfile.read(length) or b'{}')
				except ValueError:
					raise ServiceError('the body is {"fields": {...}, "targets": [...]}')
				if not isinstance(body, dict) or not isinstance(body.get('fields'), dict):
					raise ServiceError('the body is {"fields": {...}, "targets": [...]}')
				return self.sendJSON({'started': self.service.live.start(
					body['fields'], body.get('targets') or [])})
			if route == '/api/favourites' or route.startswith('/api/favourites/'):
				# star a simulated form, or unstar / annotate one by its id
				length = int(self.headers.get('Content-Length') or 0)
				try:
					body = json.loads(self.rfile.read(length) or b'{}')
				except ValueError:
					raise ServiceError('the body is {"source": {"kind": ..., "id": ...}}')
				if not isinstance(body, dict):
					raise ServiceError('the body is {"source": {"kind": ..., "id": ...}}')
				if route == '/api/favourites':
					if not isinstance(body.get('source'), dict):
						raise ServiceError('the body is {"source": {"kind": ..., "id": ...}}')
					added = self.service.addFavourite(body['source'], body.get('note'))
					return self.sendJSON({'favourites': self.service.favourites(),
										  'added': added})
				favourite, _, action = route[len('/api/favourites/'):].partition('/')
				if not re.fullmatch(r'[0-9a-f]{16}', favourite or ''):
					raise ServiceError("no such favourite")
				if action == 'delete':
					self.service.dropFavourite(favourite)
				elif not action:
					self.service.noteFavourite(favourite, body.get('note'))
				else:
					return self.sendError("no route %s" % route, 404)
				return self.sendJSON({'favourites': self.service.favourites()})
			if route.startswith('/api/live/'):
				# <id>/stop ends the process, <id>/delete removes a stopped one
				session, _, action = route[len('/api/live/'):].partition('/')
				if action == 'stop':
					return self.sendJSON(self.service.live.stop(session))
				if action == 'delete':
					return self.sendJSON(self.service.live.delete(session))
				return self.sendError("no route %s" % route, 404)
			if route == '/api/calendar':
				length = int(self.headers.get('Content-Length') or 0)
				body = self.rfile.read(length).decode('utf-8', 'replace')
				return self.sendJSON(self.service.importCalendar(body))
			if route == '/api/imports/run':
				try:
					length = int(self.headers.get('Content-Length') or 0)
					sets = json.loads(self.rfile.read(length) or b'{}').get('sets')
				except (ValueError, AttributeError):
					raise ServiceError('the body is {"sets": [names]}')
				if not isinstance(sets, list) or not all(isinstance(n, str) for n in sets):
					raise ServiceError('the body is {"sets": [names]}')
				return self.sendJSON(self.service.startImport(sets))
			return self.sendError("no route %s" % route, 404)
		except (ServiceError, livesessions.LiveError) as exc:
			return self.sendError(str(exc))
		except ledger.LedgerError as exc:
			return self.sendError(str(exc))
		except _pluginErrors() as exc:
			return self.sendError(str(exc))
		except Exception as exc:
			logging.getLogger(LOGGER).exception("web request failed")
			return self.sendError("%s: %s" % (type(exc).__name__, exc), 500)

	def one(self, query, name, default=None):
		values = query.get(name)
		return values[0] if values else default

	def runBacktest(self, query, cachedOnly=False, confirmed=False):
		return self.service.backtest(
			cachedOnly=cachedOnly, confirmed=confirmed,
			**backtestArgs(lambda name: self.one(query, name)))


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
