"""
Every candle every provider served, side by side.

A live session reads its candles from its own broker, so two sessions on two
brokers see two different M5 series of the same instrument - and a third,
the paper session, sees Twelve Data's. The event logs hold them, one file per
session, which answers "what did this session see" and not "how far apart
were the brokers at 10:25". This table answers the second question: one row
per (provider, account, instrument, granularity, bar), written by every
session as the bar arrives, read back by the live page.

Why SQLite and not the HDF5 warehouse next door: the warehouse is one file
per instrument with no provider in it, and PyTables does not take appends
from several processes at once. The sessions ARE several processes. SQLite
in WAL mode takes them, and web/livesessions.py already keeps sessions.db
this way, so the live folder holds two small databases and no new dependency.

`received_at` is the moment the process saw the bar. It costs nothing and it
is the one measurement of latency this stack can make without a tick feed:
a broker whose bar arrives forty seconds after the close is forty seconds
behind the one whose bar arrives in five.

The same file keeps `api_calls`, one row per request to a metered API, so the
Twelve Data budget is counted where every session can read it - see
lib/twelvedata.py.
"""

import datetime
import json
import logging
import os
import sqlite3

from parity_deriva.etc import settings
from parity_deriva.lib.utils import granularityToTimedelta, pipSize
from parity_deriva.trading.handler import ExecutionHandler

SIDES = ('ask', 'bid', 'mid')
LEGS = ('o', 'h', 'l', 'c')
PRICES = ['%s_%s' % (s, l) for s in SIDES for l in LEGS]

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
	provider TEXT NOT NULL, account TEXT NOT NULL,
	instrument TEXT NOT NULL, granularity TEXT NOT NULL, time TEXT NOT NULL,
	%s,
	volume INTEGER, received_at TEXT NOT NULL, session TEXT,
	PRIMARY KEY (provider, account, instrument, granularity, time));
CREATE TABLE IF NOT EXISTS api_calls (
	source TEXT NOT NULL, at TEXT NOT NULL, symbol TEXT, params TEXT,
	status INTEGER, latency_ms INTEGER, credits INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS api_calls_at ON api_calls (source, at);
""" % ",\n\t".join("%s REAL" % p for p in PRICES)

#: the feed that is the reference when it is there: the paper session's
#: candles, which no broker priced
REFERENCE = 'twelvedata:paper'


def utcnow():
	return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def iso(when):
	return when.isoformat() if hasattr(when, 'isoformat') else str(when)


def millis(text):
	when = datetime.datetime.fromisoformat(str(text).replace('Z', ''))
	return int(when.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def feedName(provider, account):
	return "%s:%s" % (provider, account)


class CandleDB(object):

	def __init__(self, path):
		self.path = path

	def connect(self):
		"""
		A connection of its own for every operation. The recorder writes from
		the engine's thread and the Twelve Data client logs its calls from the
		poller's, and a sqlite3 connection belongs to the thread that opened
		it - so none is kept. The same shape as LiveSessions.db().
		"""
		folder = os.path.dirname(os.path.abspath(self.path))
		os.makedirs(folder, exist_ok=True)
		connection = sqlite3.connect(self.path, timeout=10)
		connection.execute("PRAGMA journal_mode=WAL")
		connection.executescript(SCHEMA)
		return connection

	# --------------------------------------------------------------- candles

	def write(self, event, provider, account, session=None, received=None):
		"""
		One bar in. Returns 'new', 'same' or 'revised': the third is a bar
		this feed had already served with other prices, which is worth a log
		line - the strategy acted on the first version.
		"""
		values = []
		for side in SIDES:
			prices = getattr(event, side, None) or {}
			for leg in LEGS:
				value = prices.get(leg)
				values.append(None if value in (None, '', '0.0') else float(value))
		key = (provider, str(account), event.instrument, event.granularity,
			   iso(event.time))
		with self.connect() as connection:
			row = connection.execute(
				"SELECT %s FROM candles WHERE provider=? AND account=? AND "
				"instrument=? AND granularity=? AND time=?" % ", ".join(PRICES),
				key).fetchone()
			outcome = 'new' if row is None else (
				'same' if list(row) == values else 'revised')
			connection.execute(
				"INSERT OR REPLACE INTO candles VALUES (%s)"
				% ",".join("?" * (5 + len(PRICES) + 3)),
				key + tuple(values) + (int(getattr(event, 'volume', 0) or 0),
									   iso(received or utcnow()), session))
		return outcome

	def feeds(self, instrument, granularity):
		with self.connect() as connection:
			rows = connection.execute(
				"SELECT provider, account, MIN(time), MAX(time), COUNT(*) FROM candles "
				"WHERE instrument=? AND granularity=? GROUP BY provider, account "
				"ORDER BY provider, account", (instrument, granularity)).fetchall()
		return [{'provider': p, 'account': a, 'feed': feedName(p, a),
				 'first': millis(lo), 'last': millis(hi), 'n': n}
				for p, a, lo, hi, n in rows]

	def select(self, instrument, granularity, dtfrom=None, dtto=None):
		"""The rows of a window, as dicts, oldest first."""
		terms, args = ["instrument=?", "granularity=?"], [instrument, granularity]
		if dtfrom is not None:
			terms.append("time>=?")
			args.append(iso(dtfrom))
		if dtto is not None:
			terms.append("time<=?")
			args.append(iso(dtto))
		columns = ['provider', 'account', 'time'] + PRICES + ['volume', 'received_at']
		with self.connect() as connection:
			rows = connection.execute(
				"SELECT %s FROM candles WHERE %s ORDER BY provider, account, time"
				% (", ".join(columns), " AND ".join(terms)), args).fetchall()
		return [dict(zip(columns, row)) for row in rows]

	def rows(self, instrument, granularity, dtfrom=None, dtto=None):
		"""
		Per feed, the bars in the nine numbers web/service.Service.series()
		serves - [t, o, h, l, c, askH, askL, bidH, bidL] - so the live page
		draws them with the code that draws a backtest. A feed without a bid
		and an ask quotes its mid for them, as the store does.
		"""
		out = {}
		for row in self.select(instrument, granularity, dtfrom, dtto):
			feed = out.setdefault(feedName(row['provider'], row['account']), {
				'feed': feedName(row['provider'], row['account']),
				'provider': row['provider'], 'account': row['account'], 'candles': []})
			mid = [row['mid_' + leg] for leg in LEGS]
			if None in mid:
				continue
			def side(name, leg):
				value = row['%s_%s' % (name, leg)]
				return round(value if value is not None else row['mid_' + leg], 8)
			feed['candles'].append(
				[millis(row['time'])] + [round(v, 8) for v in mid]
				+ [side('ask', 'h'), side('ask', 'l'), side('bid', 'h'), side('bid', 'l')])
		feeds = sorted(out.values(), key=lambda f: (f['feed'] != REFERENCE, f['feed']))
		return {'feeds': feeds,
				'reference': feeds[0]['feed'] if feeds else None}

	def skew(self, instrument, granularity, dtfrom=None, dtto=None,
			 reference=None, setup=None):
		"""
		How far each feed's bars sit from the reference's, in pips.

		The reference is the paper feed when it is there - Twelve Data priced
		nothing anybody traded on - else the first feed by name. Every other
		feed is joined to it bar by bar on the open time: dClose is its mid
		close minus the reference's, dHigh and dLow the same on the extremes,
		dLatency the seconds between the two processes seeing the bar. A bar
		the reference has and the feed has not is `missing`, which is a
		finding of its own: the broker skipped a bar or served it late.

		Nothing here judges. There is no threshold and no colour: measure
		first, and set an alarm once the numbers have been looked at - the
		rule PARITY_ALARM follows.
		"""
		import pandas as pd
		pip = pipSize(instrument, setup if setup is not None else settings)
		period = granularityToTimedelta(granularity)
		rows = self.select(instrument, granularity, dtfrom, dtto)
		empty = {'instrument': instrument, 'granularity': granularity, 'pip': pip,
				 'reference': None, 'feeds': [], 'matrix': {'feeds': [], 'values': []}}
		if not rows:
			return empty
		frame = pd.DataFrame(rows)
		frame['feed'] = frame['provider'] + ':' + frame['account']
		frame['time'] = pd.to_datetime(frame['time'])
		frame['received_at'] = pd.to_datetime(frame['received_at'])
		# seconds from the bar's close to the moment the process saw it
		frame['lag'] = (frame['received_at'] - (frame['time'] + period)).dt.total_seconds()
		feeds = sorted(frame['feed'].unique(), key=lambda f: (f != REFERENCE, f))
		if reference is None or reference not in feeds:
			reference = feeds[0]
		byFeed = dict((name, part.set_index('time').sort_index())
					  for name, part in frame.groupby('feed'))
		ref = byFeed[reference]

		def stat(series, how):
			series = series.dropna()
			if series.empty:
				return None
			if how == 'p95':
				return round(float(series.abs().quantile(0.95)), 3)
			if how == 'max':
				return round(float(series.abs().max()), 3)
			if how == 'mean':
				return round(float(series.abs().mean()), 3)
			if how == 'median':
				return round(float(series.abs().median()), 3)
			if how == 'bias':
				return round(float(series.mean()), 3)
			if how == 'last':
				return round(float(series.iloc[-1]), 3)
			raise ValueError(how)

		out = []
		for name in feeds:
			part = byFeed[name]
			spread = (part['ask_c'] - part['bid_c']).dropna() / pip
			row = {'feed': name, 'provider': part['provider'].iloc[0],
				   'account': part['account'].iloc[0], 'n': int(len(part)),
				   'spread': round(float(spread.median()), 3) if not spread.empty else None,
				   'latency': round(float(part['lag'].median()), 1),
				   'missing': None, 'mean': None, 'median': None, 'p95': None,
				   'max': None, 'last': None, 'bias': None, 'series': []}
			if name != reference:
				joined = ref[['mid_c', 'mid_h', 'mid_l', 'lag']].join(
					part[['mid_c', 'mid_h', 'mid_l', 'lag']], how='left',
					rsuffix='_f')
				row['missing'] = int(joined['mid_c_f'].isna().sum())
				both = joined.dropna(subset=['mid_c_f'])
				dClose = (both['mid_c_f'] - both['mid_c']) / pip
				dHigh = (both['mid_h_f'] - both['mid_h']) / pip
				dLow = (both['mid_l_f'] - both['mid_l']) / pip
				dLag = both['lag_f'] - both['lag']
				for how in ('mean', 'median', 'p95', 'max', 'last', 'bias'):
					row[how] = stat(dClose, how)
				row['series'] = [[millis(t), round(float(a), 3), round(float(b), 3),
								  round(float(c), 3), round(float(d), 1)]
								 for t, a, b, c, d in zip(both.index, dClose, dHigh, dLow, dLag)]
			out.append(row)

		# every feed against every other, mean |dClose| in pips: the board
		# that says at a glance which pair of brokers disagree the most
		closes = frame.pivot_table(index='time', columns='feed', values='mid_c')
		values = []
		for a in feeds:
			line = []
			for b in feeds:
				if a == b:
					line.append(0.0)
					continue
				diff = ((closes[a] - closes[b]).dropna().abs() / pip)
				line.append(round(float(diff.mean()), 3) if not diff.empty else None)
			values.append(line)
		return {'instrument': instrument, 'granularity': granularity, 'pip': pip,
				'reference': reference, 'feeds': out,
				'matrix': {'feeds': list(feeds), 'values': values}}

	# ------------------------------------------------------------- api calls

	def logCall(self, source, symbol, params, status, latency_ms, credits=1):
		with self.connect() as connection:
			connection.execute(
				"INSERT INTO api_calls VALUES (?, ?, ?, ?, ?, ?, ?)",
				(source, iso(utcnow()), symbol,
				 json.dumps(params, sort_keys=True) if params is not None else None,
				 status, int(latency_ms) if latency_ms is not None else None,
				 int(credits)))

	def spentToday(self, source, now=None):
		"""Credits this source spent since midnight UTC, as counted here."""
		day = (now or utcnow()).strftime('%Y-%m-%dT00:00:00')
		with self.connect() as connection:
			row = connection.execute(
				"SELECT COALESCE(SUM(credits), 0) FROM api_calls "
				"WHERE source=? AND at>=?", (source, day)).fetchone()
		return int(row[0])

	def lastCall(self, source):
		with self.connect() as connection:
			row = connection.execute(
				"SELECT MAX(at) FROM api_calls WHERE source=?", (source,)).fetchone()
		return millis(row[0]) if row and row[0] else None


class CandleRecorder(ExecutionHandler):
	"""
	Write every candle on the bus into the database, and never stop the bus.

	The engine exits the process on any exception a handler lets out
	(trading/engine.py), which is right for a strategy that has lost its
	footing and wrong for a recorder: a locked database file must not cost a
	live session its trades. So everything here is caught and logged.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'path', getattr(self.setup, 'CANDLE_DB', None)
				  or os.path.join(self.setup.DATA_DIR, 'live', 'candles.db'))
		self.provider = None
		self.account = None
		self.session = None
		self._set(args, 'provider')
		self._set(args, 'account')
		self._set(args, 'session')
		self.db = CandleDB(self.path)
		self.written = 0
		self.failed = 0

	def execute_event(self, event):
		if str(event) != 'CANDLE':
			return
		try:
			outcome = self.db.write(event, self.provider, self.account, self.session)
		except Exception as exc:
			self.failed += 1
			self.logger.error("candle not recorded (%s %s): %s"
							  % (getattr(event, 'instrument', '?'),
								 getattr(event, 'time', '?'), exc))
			return
		self.written += 1
		if outcome == 'revised':
			self.logger.warning("REVISED %s %s %s: the feed re-served this bar "
								"with other prices; stored the new ones"
								% (self.provider, event.instrument, event.time))


if __name__ == '__main__':
	# self-check: two feeds three pips apart, one bar missing on the second
	import tempfile
	import types
	from parity_deriva.event.event import CandleEvent
	path = os.path.join(tempfile.mkdtemp(), 'candles.db')
	db = CandleDB(path)
	cfg = types.SimpleNamespace(INSTRUMENT_PRECISION={'EUR_USD': 5},
								DEFAULT_PRICE_PRECISION=5)
	t0 = datetime.datetime(2026, 9, 24, 10, 0)

	def bar(when, price):
		ohlc = {'o': price, 'h': price + 0.0002, 'l': price - 0.0002, 'c': price}
		event = CandleEvent({'time': when, 'mid': ohlc, 'bid': ohlc, 'ask': ohlc,
							 'volume': 0, 'complete': True})
		event.instrument, event.granularity = 'EUR_USD', 'M5'
		return event

	for i in range(4):
		when = t0 + datetime.timedelta(minutes=5 * i)
		assert db.write(bar(when, 1.1000), 'twelvedata', 'paper', 's1') == 'new'
		if i != 2:
			assert db.write(bar(when, 1.1003), 'ig', 'Z1', 's2') == 'new'
	assert db.write(bar(t0, 1.1000), 'twelvedata', 'paper', 's1') == 'same'
	assert db.write(bar(t0, 1.1009), 'ig', 'Z1', 's2') == 'revised'
	feeds = db.feeds('EUR_USD', 'M5')
	assert [f['n'] for f in feeds] == [3, 4], feeds
	rows = db.rows('EUR_USD', 'M5')
	assert rows['reference'] == 'twelvedata:paper'
	assert len(rows['feeds'][0]['candles'][0]) == 9
	skew = db.skew('EUR_USD', 'M5', setup=cfg)
	ig = [f for f in skew['feeds'] if f['feed'] == 'ig:Z1'][0]
	assert ig['missing'] == 1 and ig['n'] == 3, ig
	assert ig['last'] == 3.0 and ig['max'] == 9.0, ig
	assert skew['matrix']['values'][0][1] == skew['matrix']['values'][1][0]
	db.logCall('twelvedata', 'EUR/USD', {'interval': '5min'}, 200, 120)
	assert db.spentToday('twelvedata') == 1 and db.lastCall('twelvedata')
	print("ok")
