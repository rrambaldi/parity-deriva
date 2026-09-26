"""
Where a writer's market data comes from (data/market.py). Each kind has a
source of its own, chosen on the settings page:

	manual     the page's import and the MCP pushes; nothing on a timer
	upstream   another parity server: its MCP endpoint, with its mirror
			   token, pulled for what this one does not hold (web/mcp.py PULLS)
	providers  a broker's own API with this server's keys (trading/providers.py);
			   candles only, and only a broker that serves ask and bid

A server that only reads the market folder has no source: the writer fills
it. The forexfactory scraper is not a source and is not in here: it is a
program of its own, and it pushes (push_calendar) like any other outside one.
"""
import datetime
import json
import logging
import threading
import time
import urllib.request

import pandas as pd

from parity_deriva.data import calendar, market

KINDS = market.KINDS
#: bars held in memory before they go into the store: each merge copies the file
BATCH = 100000
#: how far back a calendar pull reaches before the last event held: the outcome
#: and the revisions of an event come after it
REVISED = datetime.timedelta(days=30)

logger = logging.getLogger('parity_deriva.web')


class SourceError(Exception):
	"""A source that could not be read."""


def rpc(upstream, name, args, timeout=180):
	"""One tool of the upstream's MCP endpoint, its answer decoded."""
	body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
					   'params': {'name': name, 'arguments': args}}).encode()
	request = urllib.request.Request(upstream['url'], body, {
		'Authorization': 'Bearer %s' % upstream.get('token', ''),
		'Content-Type': 'application/json', 'Accept': 'application/json',
		'User-Agent': 'parity-deriva market mirror'})
	try:
		with urllib.request.urlopen(request, timeout=timeout) as answer:
			reply = json.load(answer)
	except (OSError, ValueError) as exc:
		raise SourceError("%s: %s" % (upstream['url'], exc))
	result = reply.get('result') or {}
	text = ((result.get('content') or [{}])[0]).get('text', '')
	if reply.get('error') or result.get('isError'):
		raise SourceError("%s %s: %s" % (upstream['url'], name,
										 text or (reply.get('error') or {}).get('message')))
	return json.loads(text)


def lastBar(path, granularity):
	"""The last bar a store keeps for a series, or None."""
	try:
		with pd.HDFStore(path, mode='r') as held:
			key = '/' + granularity
			if key not in held:
				return None
			index = held.select_column(key, 'index')
			return pd.Timestamp(index.max()) if len(index) else None
	except OSError:
		return None


def millis(when):
	return int(pd.Timestamp(when).value // 10 ** 6)


def pullCandles(service, kept, report):
	"""Every series the upstream keeps, from the bar after the last one held here."""
	from parity_deriva.web.service import importer
	csv = importer()
	upstream = kept['upstream']
	for row in rpc(upstream, 'market_status', {})['instruments']:
		for series in row['granularities']:
			instrument, granularity = row['instrument'], series['granularity']
			last = lastBar(market.store(instrument, service.setup), granularity)
			after = millis(last) if last is not None else None
			if after is not None and after >= series['to']:
				continue
			frames, held, added, more = [], 0, 0, True
			while more:
				got = rpc(upstream, 'pull_candles', {'instrument': instrument,
													 'granularity': granularity, 'after': after})
				if got['ask']:
					frames.append(csv.combine(
						csv.indexed(pd.DataFrame(got['ask'], columns=csv.FIELDS), 'ask'),
						csv.indexed(pd.DataFrame(got['bid'], columns=csv.FIELDS), 'bid'))[0])
					held += len(frames[-1].index)
					after = got['ask'][-1][0]
				more = bool(got['more'] and got['ask'])
				if frames and (not more or held >= BATCH):
					added += service.mergeBars(instrument, granularity, pd.concat(frames))
					frames, held = [], 0
			report("%s %s: %d bars from upstream" % (instrument, granularity, added))


def pullCalendar(service, kept, report):
	"""The upstream's events from a month before the last one held here, or all of them."""
	where = calendar.path(service.setup)
	held = calendar.load(where)
	since = (held['time'].max() - REVISED).strftime('%Y-%m-%d') if len(held) else None
	rows, start = [], 0
	while start is not None:
		got = rpc(kept['upstream'], 'pull_calendar', {'since': since, 'start': start})
		rows += got['events']
		start = got['next']
	with calendar.writing(where, service.setup):
		before = calendar.load(where)
		after = calendar.merge(before, rows)
		calendar.save(after, where, service.setup)
	report("calendar: %d events from upstream, %d new" % (len(rows), len(after) - len(before)))


def fetchCandles(service, kept, report):
	"""Every series the stores keep, from the bar after the last one, off a broker's API."""
	from parity_deriva.trading import providers
	provider = providers.get_provider(kept.get('provider') or None, service.setup)
	if not provider.capabilities.bid_ask_candles:
		raise SourceError("%s serves one price a bar, and a store keeps ask and bid" % provider.name)
	for row in service.instruments():
		for series in row['granularities']:
			if series.get('derivedFrom'):
				continue
			instrument, granularity = row['instrument'], series['granularity']
			last = lastBar(market.store(instrument, service.setup), granularity)
			try:
				bars = [e for e in providers.history(
					provider, instrument, granularity,
					since=last.to_pydatetime() if last is not None else None) if e.complete]
			except Exception as exc:
				report("%s %s: %s: %s" % (instrument, granularity, type(exc).__name__, exc))
				continue
			added = 0
			if bars:
				frame = pd.DataFrame(
					[dict([('%s_%s' % (side, leg), float(getattr(e, side)[leg]))
						   for side in ('ask', 'bid', 'mid') for leg in 'ohlc'],
						  volume=int(e.volume or 0)) for e in bars],
					index=pd.DatetimeIndex([pd.Timestamp(e.time).tz_localize(None)
											if pd.Timestamp(e.time).tzinfo is None
											else pd.Timestamp(e.time).tz_convert(None)
											for e in bars]).as_unit('us'))
				added = service.mergeBars(instrument, granularity, frame)
			report("%s %s: %d bars from %s" % (instrument, granularity, added, provider.name))


RUN = {('candles', 'upstream'): pullCandles, ('calendar', 'upstream'): pullCalendar,
	   ('candles', 'providers'): fetchCandles}


class Sources(object):
	"""
	The writer's sources on a timer, each kind at its own interval, and what
	the last run of each said for the settings page.
	"""

	def __init__(self, service):
		self.service = service
		self.lock = threading.Lock()
		self.runLock = threading.Lock()
		self.state = dict((kind, {'at': None, 'ok': None, 'lines': []}) for kind in KINDS)

	def trigger(self, kind):
		"""A run of a kind's source in the background, unless one is running."""
		kept = market.config(self.service.setup)
		if kind not in KINDS or not kept['writer'] or kept[kind]['source'] == 'manual':
			raise market.MarketError("%s: no source to run on this server" % kind)
		with self.lock:
			if self.state[kind].get('running'):
				return self.state[kind]
			self.state[kind] = {'at': int(time.time() * 1000), 'ok': None, 'lines': [],
								'running': True, 'source': kept[kind]['source']}
		thread = threading.Thread(target=self.run, args=(kind, kept, self.state[kind]))
		thread.daemon = True
		thread.start()
		return self.state[kind]

	def run(self, kind, kept, state):
		"""One run of a kind's source; what it did goes in `state`."""
		source, lines = kept[kind]['source'], state['lines']
		# ponytail: one run at a time for all kinds; a lock a kind if a slow upstream holds the other up
		with self.runLock:
			try:
				RUN[kind, source](self.service, kept, lines.append)
				state['ok'] = True
			except Exception as exc:
				logger.exception("market data: %s from %s" % (kind, source))
				lines.append("%s: %s" % (type(exc).__name__, exc))
				state['ok'] = False
			finally:
				state['running'] = False

	def due(self, kind, kept):
		last = self.state[kind]['at']
		return kept['writer'] and kept[kind]['source'] != 'manual' and (
			last is None or time.time() * 1000 - last >= kept[kind]['every'] * 60000)

	def loop(self):
		while True:
			for kind in KINDS:
				try:
					if self.due(kind, market.config(self.service.setup)):
						self.trigger(kind)
				except Exception:
					logger.exception("market data: %s" % kind)
			time.sleep(60)

	def start(self):
		thread = threading.Thread(target=self.loop, name='market-sources')
		thread.daemon = True
		thread.start()
