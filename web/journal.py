"""
The journal of a strategy (docs/PIANO-FASE1.md C2): what was done with it,
kept by parity-deriva from every action - its sets and runs, its favourites,
its code changing, the mixes it went into, verify, its versions and their
states, what went to a trade server, the sessions, the alerts - from the idea
to live or to the bin. Nobody has to write a line of it; a note on any entry
is there for whoever wants one.

One file a strategy, by its code's name - the journal of M1502 holds every
instrument and timeframe it was tried on, and the page filters them; an
uploaded strategy's versions (MY-EMA 1, MY-EMA 2) share the journal of their
name, each entry saying which it was: DATA_DIR/journal/<strategy>.jsonl, a
line an entry, only ever added to. A note
that corrects another is a note of its own. An entry is

	{id, at, kind, level, strategy, instrument, granularity, version, data, link, by}

with its numbers in `data` and not a sentence: the page writes the sentence,
in the page's language, and an entry still reads once its set is deleted. The
id is random (uuid4): when the journals of several servers are put together
(docs/ROADMAP.md) two entries cannot collide.

This server's journal only: what goes to another server is an entry here
(the push, and the verdict that comes back); what happens there is in that
server's journal.
"""

import json
import logging
import os
import threading
import time
import urllib.parse
import uuid

#: a milestone is a step of the strategy's life (a mix, a version, a session),
#: an experiment one of the tries of the simulation (a set, a run, a favourite)
LEVELS = ('milestone', 'experiment')
MARKS = ('up', 'down', 'flat')
MACHINE = 'parity-deriva'
#: the entries a search answers with, at most
FOUND = 200

logger = logging.getLogger('parity_deriva.web')
_lock = threading.Lock()


def folder(setup):
	return os.path.join(getattr(setup, 'DATA_DIR', '') or '.', 'journal')


def family(strategy):
	"""The journal a strategy writes to: an uploaded one's versions share their name's."""
	from parity_deriva.strategy import uploaded
	return uploaded.split(strategy)[0]


def path(setup, strategy):
	# quoted, so that no name reaches outside the folder
	return os.path.join(folder(setup), urllib.parse.quote(family(strategy), safe='') + '.jsonl')


def now():
	return int(time.time() * 1000)


def entry(strategy, kind, level, data=None, fields=None, version=None, link=None, by=None, at=None,
		  ident=None):
	fields = fields or {}
	return {'id': ident or uuid.uuid4().hex, 'at': int(at or now()), 'kind': kind, 'level': level,
			'strategy': strategy, 'instrument': fields.get('instrument'),
			'granularity': fields.get('granularity'), 'version': version, 'data': data or {},
			'link': link, 'by': by or MACHINE}


def idea(strategy, at):
	"""The first entry of a journal: the strategy's DESCRIPTION, the idea it trades."""
	description = None
	try:
		from parity_deriva.backtest import ledger
		description = getattr(ledger.load_strategy(strategy), 'DESCRIPTION', None)
	except Exception:
		pass
	return entry(strategy, 'idea', 'milestone', {'description': description}, at=at - 1)


def add(setup, strategy, kind, level, data=None, fields=None, version=None, link=None, by=None,
		at=None, ident=None):
	"""One entry at the end of the strategy's journal, which starts with its idea."""
	if not strategy:
		return None
	if level not in LEVELS + (None,):
		raise ValueError("a level is %s" % ' or '.join(LEVELS))
	one = entry(strategy, kind, level, data, fields, version, link, by, at, ident)
	name = path(setup, strategy)
	with _lock:
		os.makedirs(folder(setup), exist_ok=True)
		fresh = not os.path.exists(name)
		with open(name, 'a') as handle:
			if fresh and kind != 'idea':
				handle.write(json.dumps(idea(strategy, one['at'])) + '\n')
			handle.write(json.dumps(one) + '\n')
	return one


def record(setup, strategy, kind, level, data=None, fields=None, **more):
	"""add(), never in the way of the action it writes down: a journal it cannot write is a log line."""
	try:
		return add(setup, strategy, kind, level, data, fields, **more)
	except Exception:
		logger.exception("journal of %s: %s not written" % (strategy, kind))
		return None


def read(setup, strategy):
	"""A strategy's entries in the order they happened; [] for one with no journal."""
	out = []
	try:
		with open(path(setup, strategy)) as handle:
			for line in handle:
				try:
					out.append(json.loads(line))
				except ValueError:
					continue
	except OSError:
		return []
	return sorted(out, key=lambda e: e.get('at') or 0)


def journals(setup):
	"""Every journal: its strategy, how many entries, the first and the last."""
	out = []
	where = folder(setup)
	for name in sorted(os.listdir(where)) if os.path.isdir(where) else ():
		if not name.endswith('.jsonl'):
			continue
		strategy = urllib.parse.unquote(name[:-len('.jsonl')])
		entries = read(setup, strategy)
		if entries:
			out.append({'strategy': strategy, 'entries': len(entries), 'first': entries[0]['at'],
						'last': entries[-1]['at'],
						'instruments': sorted(set(e['instrument'] for e in entries if e.get('instrument')))})
	return sorted(out, key=lambda j: -j['last'])


def search(setup, text):
	"""The entries and notes of every journal with `text` in them, newest first."""
	text = (text or '').strip().lower()
	if not text:
		return []
	found = [e for j in journals(setup) for e in read(setup, j['strategy'])
			 if text in json.dumps(e, ensure_ascii=False).lower()]
	return sorted(found, key=lambda e: -e['at'])[:FOUND]


def note(setup, strategy, text, about=None, mark=None, by=None):
	"""A note of the user's, free or on an entry (`about`, its id), with an optional mark."""
	text = (text or '').strip()
	if mark not in MARKS + (None, ''):
		raise ValueError("a mark is %s" % ', '.join(MARKS))
	if not text and not mark:
		raise ValueError("a note has a text or a mark")
	if len(text) > 4000:
		raise ValueError("a note is 4000 characters at most")
	entries = read(setup, strategy)
	if not entries:
		raise ValueError("no journal for %s" % strategy)
	target = None
	if about:
		target = next((e for e in entries if e['id'] == about), None)
		if target is None:
			raise ValueError("no entry %s in the journal of %s" % (about, strategy))
	return add(setup, strategy, 'note', None, {'about': about, 'mark': mark or None, 'text': text},
			   fields=target, version=(target or {}).get('version'), by=by or 'user')
