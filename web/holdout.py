"""
The holdout (docs/PIANO-FASE1.md C3, D4, D7): the last stretch of an
instrument's history that no simulation reads but the gate, once a version.

One cut an instrument, the same for every timeframe - the market is one - and
for every strategy, unless a strategy has a cut of its own. The first backtest
or set on an instrument fixes it, before anything has read past it:
min(end - HOLDOUT_SHARE of the history, end - HOLDOUT_MIN_DAYS), on the
timeframe that run is on. Data that comes later lengthens the holdout, it does
not move the cut; only the user does (move), either way, always leaving
HOLDOUT_MIN_DAYS after it. A cut moved starts with no openings.

	DATA_DIR/holdout.json
	{"EUR_USD": {"cut": "2023-09-01", "openings": 3,
	             "strategies": {"M1502": {"cut": "2024-06-01", "openings": 1}}}}

A strategy is its journal's name (web/journal.py family): an uploaded
strategy's versions share one cut.
"""

import datetime
import json
import os
import threading

from parity_deriva.etc import settings
from parity_deriva.web import journal

DAY = 86400000
_lock = threading.Lock()


class HoldoutError(Exception):
	"""A cut that cannot be: the page shows why."""


def path(setup):
	return os.path.join(getattr(setup, 'DATA_DIR', '') or '.', 'holdout.json')


def registry(setup):
	try:
		with open(path(setup)) as handle:
			return json.load(handle)
	except (OSError, ValueError):
		return {}


def _save(setup, held):
	name = path(setup)
	os.makedirs(os.path.dirname(name) or '.', exist_ok=True)
	with open(name + '.part', 'w') as handle:
		json.dump(held, handle, indent=1, sort_keys=True)
	os.replace(name + '.part', name)


def day(ms):
	return datetime.datetime.fromtimestamp(ms / 1000.0, datetime.timezone.utc).strftime('%Y-%m-%d')


def millis(text):
	return int(datetime.datetime.strptime(text, '%Y-%m-%d').replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def setting(setup, name):
	"""A number of the setup's, else of etc/settings.py: a setup without one (or a mock) has the default."""
	value = getattr(setup, name, None)
	return value if isinstance(value, (int, float)) and not isinstance(value, bool) else getattr(settings, name)


def minDays(setup):
	return int(setting(setup, 'HOLDOUT_MIN_DAYS'))


def default(first, last, setup=None):
	"""
	The cut a history from `first` to `last` (epoch ms) gets: None when it is too
	short to keep HOLDOUT_MIN_DAYS aside and as much again to develop on.
	"""
	share = float(setting(setup, 'HOLDOUT_SHARE'))
	aside = minDays(setup) * DAY
	if last - first < 2 * aside:
		return None
	return day(min(last - share * (last - first), last - aside))


def cut(setup, instrument, strategy, held):
	"""
	The cut a run on `instrument` stops at: the strategy's own, else the
	instrument's, fixed now from `held` (the store's {from, to} of the run's
	timeframe) when this is the first run on it. None for a history too short.
	"""
	with _lock:
		book = registry(setup)
		one = book.get(instrument)
		if one is None:
			found = default(held['from'], held['to'], setup)
			if found is None:
				return None
			one = book[instrument] = {'cut': found, 'openings': 0, 'strategies': {}}
			_save(setup, book)
		own = (one.get('strategies') or {}).get(journal.family(strategy or ''))
		return (own or one)['cut']


def status(setup, instrument, strategy=None):
	"""{cut, openings, own} in force for a strategy on an instrument, or None."""
	one = registry(setup).get(instrument)
	if one is None:
		return None
	own = (one.get('strategies') or {}).get(journal.family(strategy or '')) if strategy else None
	held = own or one
	return {'cut': held['cut'], 'openings': held.get('openings', 0), 'own': bool(own),
			'instrument': instrument}


def opened(setup, instrument, strategy):
	"""One opening more of the cut in force: how many it has had, this one included."""
	with _lock:
		book = registry(setup)
		one = book.get(instrument)
		if one is None:
			raise HoldoutError("%s has no holdout" % instrument)
		held = (one.get('strategies') or {}).get(journal.family(strategy or '')) or one
		held['openings'] = held.get('openings', 0) + 1
		_save(setup, book)
		return held['openings']


def move(setup, instrument, when, last, first=None, strategy=None, by=None):
	"""
	The instrument's cut, or with `strategy` that strategy's own, moved to
	`when` (YYYY-MM-DD) by the user: either way, leaving HOLDOUT_MIN_DAYS to
	`last` (the store's last bar, epoch ms). Its openings start again at 0; an
	empty `when` with a strategy drops its own cut. Written in the journal.
	"""
	if strategy and not when:
		with _lock:
			book = registry(setup)
			dropped = ((book.get(instrument) or {}).get('strategies') or {}).pop(journal.family(strategy), None)
			_save(setup, book)
		if dropped:
			journal.record(setup, strategy, 'holdout', 'milestone', {'instrument': instrument, 'cut': None,
									'was': dropped['cut'], 'own': False}, {'instrument': instrument}, by=by)
		return status(setup, instrument, strategy)
	try:
		at = millis(str(when))
	except ValueError:
		raise HoldoutError("a cut is a date, YYYY-MM-DD") from None
	if last - at < minDays(setup) * DAY:
		raise HoldoutError("the holdout keeps at least %d days: from %s to the last bar, %s, is less"
						   % (minDays(setup), when, day(last)))
	if first is not None and at <= first:
		raise HoldoutError("the cut is after the first bar, %s" % day(first))
	with _lock:
		book = registry(setup)
		one = book.setdefault(instrument, {'cut': str(when), 'openings': 0, 'strategies': {}})
		if strategy:
			was = (one.setdefault('strategies', {}).get(journal.family(strategy)) or {}).get('cut')
			one['strategies'][journal.family(strategy)] = {'cut': str(when), 'openings': 0}
		else:
			was, one['cut'], one['openings'] = one.get('cut'), str(when), 0
		_save(setup, book)
	data = {'instrument': instrument, 'cut': str(when), 'was': was, 'own': bool(strategy)}
	# the instrument's cut is every strategy's on it that has none of its own
	for name in [strategy] if strategy else [j['strategy'] for j in journal.journals(setup)
											 if instrument in j['instruments']]:
		journal.record(setup, name, 'holdout', 'milestone', data, {'instrument': instrument}, by=by)
	return status(setup, instrument, strategy)
