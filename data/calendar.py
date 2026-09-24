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

Reading it for a run is two questions: which events matter (the currencies of
the pair, and how big), and how wide a window around each one is closed. Both
belong to whoever is asking - see Calendar - because a scalper and a swing
strategy do not agree about either, and nothing here decides for them.
"""

import bisect
import datetime
import os

import pandas as pd

from parity_deriva.etc import settings

COLUMNS = ('time', 'currency', 'impact', 'title')

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
	cfg = setup if setup is not None else settings
	return os.path.join(cfg.DATA_DIR, FILENAME)


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
	frame = pd.read_csv(where, parse_dates=['time'])
	for column in COLUMNS:
		if column not in frame:
			raise ValueError("%s has no %r column; it is not a calendar"
							 % (where, column))
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
	added = pd.DataFrame(list(rows), columns=list(COLUMNS))
	if len(added):
		added['time'] = pd.to_datetime(added['time'])
	both = pd.concat([frame, added], ignore_index=True)
	both = both.drop_duplicates(subset=['time', 'currency', 'title'])
	return both.sort_values('time').reset_index(drop=True)


def save(frame, where=None, setup=None):
	where = where or path(setup)
	frame.to_csv(where, index=False, columns=list(COLUMNS),
				 date_format='%Y-%m-%d %H:%M:%S')
	return where


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
