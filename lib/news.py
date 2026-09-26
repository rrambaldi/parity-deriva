"""
The economic calendar for a strategy: what is on around the bar it reads,
and how what is already out came out.

    news.around('EUR_USD', now, before=60, after=60)      # or self.news(candle) in H4

`now` is the close of the bar being read. An event at or after it has not
happened yet: it comes with its forecast and previous only - no actual, no
surprise - so a backtest cannot trade on a number it would not have had. One
before it comes with its outcome.

A central bank's meeting is several rows, each at its own minute: the
decision with its number (the ECB's rate at 12:15), then the statement and
the press conference with none (12:45). `kind` tells them apart: 'release'
has a number, 'talk' has words only, 'holiday' is a day. The number is the
release's minute, not the conference's.

The file is data/calendar.py's, read again when it changes: a live session
sees the outcomes the scraper pushes (web/mcp.py push_calendar) as they come.
"""

import datetime
import os
import re

import numpy as np

from parity_deriva.data import calendar

#: '22K', '2.15%', '-0.1%', '<0.50%', '7.66M': a number and its unit. Votes
#: ('0-2-7'), auctions ('1.61|3.9') and words are not numbers
NUMBER = re.compile(r'^[<>]?(-?\d+(?:\.\d+)?)\s*([%KMBT]?)$')

#: path -> (its mtime, the frame, its times)
_held = {}


def number(text):
	"""(value, unit) of a calendar figure, or None."""
	found = NUMBER.match(str(text or '').strip())
	return (float(found.group(1)), found.group(2)) if found else None


def _events(where):
	stamp = os.stat(where).st_mtime_ns if os.path.exists(where) else None
	held = _held.get(where)
	if held is None or held[0] != stamp:
		frame = calendar.load(where)
		held = _held[where] = (stamp, frame, frame['time'].values.astype('datetime64[us]'))
	return held[1], held[2]


def event(row, now):
	"""One row as a strategy reads it at `now`."""
	known = row.time < now
	actual, forecast = number(row.actual) if known else None, number(row.forecast)
	previous = number(row.previous)
	revision = number(row.revision) if known else None
	surprise = actual[0] - forecast[0] if actual and forecast and actual[1] == forecast[1] else None
	effect = str(row.effect)
	return {
		'time': row.time.to_pydatetime(),
		# a placeholder minute ("All Day", "Tentative"): the day is right, not the minute
		'timed': str(row.timed) != '0',
		'currency': row.currency, 'impact': row.impact, 'title': row.title,
		'kind': 'holiday' if row.impact == calendar.NON_ECONOMIC
		else 'release' if forecast or previous or number(row.actual) else 'talk',
		'known': known,
		'unit': (actual or forecast or previous or (None, None))[1],
		'actual': actual and actual[0], 'forecast': forecast and forecast[0],
		'previous': previous and previous[0], 'revision': revision and revision[0],
		'surprise': surprise,
		# 1 good for the currency, -1 bad, 0 as expected, None not read in numbers
		'good': None if surprise is None or effect not in ('1', '-1')
		else int(np.sign(surprise)) * int(effect),
	}


def around(instrument, now, before=60, after=60, impacts=(calendar.HIGH,), where=None):
	"""
	The events of the instrument's currencies (and of all of them) from
	`before` minutes before `now` to `after` minutes after, oldest first.
	`impacts` are ForexFactory's words; empty is every event.
	"""
	frame, times = _events(where or calendar.path())
	start = np.datetime64(now - datetime.timedelta(minutes=before), 'us')
	end = np.datetime64(now + datetime.timedelta(minutes=after), 'us')
	wanted = set(calendar.currencies(instrument)) | {'ALL'}
	out = []
	for row in frame.iloc[np.searchsorted(times, start, 'left'):
						  np.searchsorted(times, end, 'right')].itertuples(index=False):
		if str(row.currency).upper() in wanted and (not impacts or row.impact in impacts):
			out.append(event(row, now))
	return out
