"""
The economic calendar, as a file this machine holds.

A rule that says "do not open a trade three minutes before the payrolls" has
to know when the payrolls were, and has to know it for every week the
backtest walks. So the calendar is not read from a website while a run is
going: it is imported once by scripts/import_calendar.py, kept as a CSV next
to the stores, and read from there. A backtest that fetched its own news
would give a different answer on a day the site was slow, which is not a
property a backtest may have.

The file is four columns and nothing else:

    time,currency,impact,title
    2015-01-09 13:30:00,USD,high,Non-Farm Employment Change

`time` is UTC, like every other timestamp in this project. `impact` is
ForexFactory's own word - high, medium, low, non-economic - kept as it was
given rather than mapped onto a scale of ours: "high" is their judgement and
calling it 3 would hide whose judgement it is.

Pushed over MCP (web/mcp.py push_calendar) it has seven columns more, the
ones ForexFactory gives with each event: its `id`, whether `timed` is a real
minute (0 for "All Day", "Tentative", "Day 2"...: the minute is the site's
placeholder), the `actual`, `forecast`, `previous` and `revision` as text, and
the `effect` of a higher actual on the currency, 1 or -1 (0 when it is read in
words: "more hawkish than expected"). A file of four is still a calendar.

Reading it for a run is two questions: which events matter (the currencies of
the pair, and how big), and how wide a window around each one is closed. Both
belong to whoever is asking - see Calendar - because a scalper and a swing
strategy do not agree about either, and nothing here decides for them.
"""

import bisect
import contextlib
import datetime
import os
import re

import pandas as pd

from parity_deriva.data import market
from parity_deriva.etc import settings

COLUMNS = ('time', 'currency', 'impact', 'title')
#: what a pushed event carries besides, empty on a row of four
EXTRA = ('id', 'timed', 'actual', 'forecast', 'previous', 'revision', 'effect')

@contextlib.contextmanager
def writing(where, setup=None):
	"""
	One load-merge-save at a time, across processes too: a push every two
	minutes and the page's import would lose each other's events. On the
	server that writes the market data only (data/market.py).
	"""
	where = os.path.realpath(where)
	market.guard(where, setup)
	with market.lock(os.path.dirname(where)):
		yield

#: ForexFactory's four words, as they come
HIGH = 'high'
MEDIUM = 'medium'
LOW = 'low'
NON_ECONOMIC = 'non-economic'

#: which of them is "at least as big as" which. Non-economic - bank holidays,
#: speeches with no number attached - is outside the order rather than at the
#: bottom of it: a holiday is not a small release, it is a different kind of
#: thing, and a rule that wants it asks for it by name.
RANK = {LOW: 1, MEDIUM: 2, HIGH: 3}

#: the default name, next to the stores. One file for every instrument: the
#: calendar is about currencies and not about pairs, and EUR_USD and GBP_USD
#: read the same rows.
FILENAME = 'calendar.csv'


def path(setup=None):
	return os.path.join(market.directory(setup), FILENAME)


def empty():
	"""An empty calendar, which is a calendar and not a None to guard for."""
	return pd.DataFrame(columns=list(COLUMNS)).astype({'time': 'datetime64[ns]'})


def load(where=None, setup=None):
	"""
	The calendar, or an empty one.

	A missing file is not an error: it is a machine that has not imported the
	calendar yet, and every rule that reads it is off by default anyway.
	"""
	where = where or path(setup)
	if not os.path.exists(where):
		return empty()
	frame = pd.read_csv(where, parse_dates=['time'], keep_default_na=False,
						dtype={c: str for c in EXTRA if c != 'timed'})
	for column in COLUMNS:
		if column not in frame:
			raise ValueError("%s has no %r column; it is not a calendar"
							 % (where, column))
	for column in EXTRA:
		if column not in frame:
			frame[column] = ''
	return frame.sort_values('time').reset_index(drop=True)


def merge(frame, rows):
	"""
	Add rows to a calendar, keeping one of each.

	An event is itself by (time, currency, title): the same release imported
	twice from two overlapping weeks is one event, and a revision that moves
	an event by an hour is a new one rather than an edit - the old row is what
	the calendar said at the time, and this file is not a record of what was
	believed, it is a list of what happened.
	"""
	rows = list(rows)
	added = pd.DataFrame(rows, columns=list(COLUMNS) if rows and not isinstance(rows[0], dict)
						 else None)
	for column in COLUMNS + EXTRA:
		if column not in added:
			added[column] = ''
	if len(added):
		added['time'] = pd.to_datetime(added['time'])
	for column in EXTRA:
		if column not in frame:
			frame = frame.assign(**{column: ''})
	both = pd.concat([frame, added], ignore_index=True).fillna('')
	# Was: the first row of an event kept. Now the same, but a pushed event,
	# which has an id, is itself by it and its newest row wins: the outcome
	# comes minutes after the event, and a time the site fixed later replaces
	# its placeholder. A row without an id never replaces one with.
	key = ['time', 'currency', 'title']
	pushed = both['id'].astype(str) != ''
	rich = both[pushed].drop_duplicates('id', keep='last')
	plain = both[~pushed].drop_duplicates(key)
	plain = plain[~plain.set_index(key).index.isin(rich.set_index(key).index)]
	return pd.concat([plain, rich]).sort_values('time', kind='stable').reset_index(drop=True)


def save(frame, where=None, setup=None):
	# the file itself, not a link to it: dev's calendar.csv is prod's, and a
	# replace on the link's name would put a copy of its own in its place
	where = os.path.realpath(where or path(setup))
	market.guard(where, setup)
	columns = list(COLUMNS) + [c for c in EXTRA if c in frame]
	# whole or not at all: a run or a live session may be reading it
	frame.to_csv(where + '.part', index=False, columns=columns, date_format='%Y-%m-%d %H:%M:%S')
	os.replace(where + '.part', where)
	return where


#: a real minute, as ForexFactory writes one: "12:45am", "1:30pm"
CLOCK = re.compile(r'^\d{1,2}:\d{2}[ap]m$')


def fromForexFactory(event):
	"""
	One of ForexFactory's events - as the calendar scraper keeps them, a
	month a file - as a row of this calendar. Its datetime_utc is the time;
	`time` only says whether that is a real minute. Their "holiday" is the
	"non-economic" this file has always called it.
	"""
	for key in ('id', 'datetime_utc', 'currency', 'impact', 'event'):
		if event.get(key) in (None, ''):
			raise ValueError("an event without %r: %r" % (key, event)[:200])
	when = datetime.datetime.fromisoformat(str(event['datetime_utc']))
	if when.tzinfo is not None:
		when = when.astimezone(datetime.timezone.utc).replace(tzinfo=None)
	said = str((event.get('detail') or {}).get('Usual Effect') or '')
	impact = str(event['impact']).lower()
	return {'time': when, 'currency': str(event['currency']).upper(),
			'impact': NON_ECONOMIC if impact == 'holiday' else impact,
			'title': str(event['event']), 'id': str(event['id']),
			'timed': 1 if CLOCK.match(str(event.get('time') or '').strip()) else 0,
			'actual': str(event.get('actual') or ''), 'forecast': str(event.get('forecast') or ''),
			'previous': str(event.get('previous') or ''), 'revision': str(event.get('revision') or ''),
			'effect': 1 if 'greater than' in said else -1 if 'less than' in said else 0}


def currencies(instrument):
	"""
	The currencies an instrument is quoted in, for picking its events.

	'EUR_USD' is EUR and USD. 'DE30_EUR' is DE30 and EUR, and DE30 is not a
	currency - it simply matches no row, which is the right answer rather
	than a guess about which index follows which country's releases. A name
	with no separator at all matches nothing, and a rule whose calendar
	matches nothing blocks nothing.
	"""
	if not instrument:
		return ()
	return tuple(part.upper() for part in str(instrument).split('_') if part)


class Calendar(object):
	"""
	The events a rule cares about, as the windows they close.

	Built once and asked many times: a backtest asks it per signal, which over
	eleven years is a few hundred thousand questions, so the events are turned
	into merged intervals at build time and each question is a binary search.

	Nothing here has a default width. `before` and `after` are minutes and
	they are the whole of the rule - a calendar with both at zero blocks
	nothing at all, however many events it holds - because how long before the
	payrolls a strategy should stand aside is a decision about that strategy
	and not a fact about the payrolls.
	"""

	def __init__(self, frame, currencies=(), impacts=(HIGH,), before=0,
				 after=0):
		self.currencies = tuple(c.upper() for c in currencies)
		self.impacts = tuple(impacts)
		self.before = int(before)
		self.after = int(after)
		self.events = self._wanted(frame)
		self.windows = self._windows(self.events)
		self._starts = [start for start, _ in self.windows]

	def _wanted(self, frame):
		if frame is None or not len(frame):
			return empty()
		kept = frame
		# a placeholder minute is no minute to close a window around
		if 'timed' in kept:
			kept = kept[kept['timed'].astype(str) != '0']
		if self.currencies:
			kept = kept[kept['currency'].isin(self.currencies)]
		if self.impacts:
			kept = kept[kept['impact'].isin(self.impacts)]
		return kept.sort_values('time').reset_index(drop=True)

	def _windows(self, events):
		"""
		The closed stretches, merged.

		Two releases at half past the same hour with a ten minute window each
		are one twenty minute hole and not two, and a rule that asked twice
		would count the overlap twice.
		"""
		out = []
		if not self.before and not self.after:
			return out
		before = datetime.timedelta(minutes=self.before)
		after = datetime.timedelta(minutes=self.after)
		for when in events['time']:
			when = when.to_pydatetime() if hasattr(when, 'to_pydatetime') else when
			start, end = when - before, when + after
			if out and start <= out[-1][1]:
				out[-1] = (out[-1][0], max(out[-1][1], end))
			else:
				out.append((start, end))
		return out

	def blocked(self, when):
		"""Is this instant inside one of the windows?"""
		if not self.windows:
			return False
		where = bisect.bisect_right(self._starts, when) - 1
		if where < 0:
			return False
		start, end = self.windows[where]
		return start <= when <= end

	def next(self, when):
		"""The first event at or after an instant, or None. For a report."""
		later = self.events[self.events['time'] >= pd.Timestamp(when)]
		if not len(later):
			return None
		row = later.iloc[0]
		return {'time': row['time'].to_pydatetime(), 'currency': row['currency'],
				'impact': row['impact'], 'title': row['title']}

	def __len__(self):
		return len(self.events)
