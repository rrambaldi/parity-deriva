"""
What this server is, and the servers it deals with.

Its roles are in DATA_DIR/server.json, {"roles": [...]}, set on the settings
page or by a setup wizard:

	archive  keeps what the others make - strategies, indicators, simulations,
			 the market data - takes a Test's pushes (scripts/sync.py) and
			 pushes forms to the trade servers, whose trades it reads
	test     simulates - backtests, sets, mixes - and takes the AI
			 assistants' drafts
	trade    trades on the accounts

No file is all three: one server as it always was. Whether a trade server
trades demo accounts or real money is not a role and is not set here: it is
PARITY_DERIVA_ACCOUNTS in .env, which no page changes (etc/settings.py).
The file may hold other keys (a setup wizard's): they are kept as they are.

An archive's trade servers are in DATA_DIR/trade-servers.json, each {name,
url, token}: its MCP address and a promote token made on it. The archive
pushes forms with that token (push) and reads the server's sessions with it
(live_status) every POLL minutes: the demo servers' records are what a
promotion to a real money one carries. The archive reads them, rather than
they push here, because a real money server has to be reachable from the
archive anyway, for the promotions: one token, one direction to open.
"""

import json
import logging
import os
import threading
import time

from parity_deriva.data import sources
from parity_deriva.web import cards, journal, livesessions

ROLES = ('archive', 'test', 'trade')

#: minutes between two reads of the trade servers' sessions
POLL = 5

_lock = threading.Lock()


def dataDir(setup):
	return getattr(setup, 'DATA_DIR', '') or '.'


def _read(path):
	try:
		with open(path) as handle:
			found = json.load(handle)
	except (OSError, ValueError):
		return {}
	return found if isinstance(found, dict) else {}


def _write(path, kept, private=False):
	# aside and renamed; a file holding tokens readable by its owner only
	part = path + '.part'
	with open(os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644), 'w') as handle:
		json.dump(kept, handle, indent=1)
	os.replace(part, path)


# ------------------------------------------------------------------ roles

def roles(setup):
	"""This server's roles, sorted: all of them when server.json names none."""
	held = _read(os.path.join(dataDir(setup), 'server.json')).get('roles')
	found = sorted(set(r for r in held if r in ROLES)) if isinstance(held, list) else []
	return found or list(ROLES)


def read(setup):
	"""The whole of server.json: the roles, and what else a setup keeps there."""
	return _read(os.path.join(dataDir(setup), 'server.json'))


def keep(key, value, setup):
	"""Keep one key of server.json, the others as they are."""
	path = os.path.join(dataDir(setup), 'server.json')
	with _lock:
		kept = _read(path)
		kept[key] = value
		_write(path, kept)


def saveRoles(asked, setup):
	"""Keep `asked` as this server's roles; ValueError for an unknown one or none."""
	if not isinstance(asked, (list, tuple)) or not asked or any(r not in ROLES for r in asked):
		raise ValueError("the roles are one or more of %s" % ', '.join(ROLES))
	keep('roles', sorted(set(asked)), setup)
	return roles(setup)


def missing(setup, *wanted):
	"""None when this server has one of the roles `wanted`, else why not, to refuse with."""
	if set(wanted) & set(roles(setup)):
		return None
	return "this server has no %s role (settings, this server)" % ' or '.join(wanted)


# ---------------------------------------------------------- trade servers

def tradePath(setup):
	return os.path.join(dataDir(setup), 'trade-servers.json')


def tradeServers(setup, tokens=False):
	"""The trade servers, with what each last said; the token only with `tokens`."""
	out = []
	for row in _read(tradePath(setup)).get('servers') or []:
		row = dict(row)
		if not tokens:
			row['token'] = bool(row.get('token'))
		out.append(row)
	return out


def one(setup, name):
	found = next((s for s in tradeServers(setup, tokens=True) if s['name'] == name), None)
	if found is None:
		raise ValueError("no trade server %r" % name)
	return found


def saveTradeServer(asked, setup):
	"""
	Add a trade server, or change one by its name: {name, url, token}, the
	token kept when none is given. {name, drop: true} removes it.
	"""
	name = ' '.join(str(asked.get('name') or '').split())[:40]
	if not name:
		raise ValueError("a trade server has a name")
	with _lock:
		kept = _read(tradePath(setup))
		rows = kept.get('servers') or []
		held = next((r for r in rows if r.get('name') == name), None)
		if asked.get('drop'):
			rows = [r for r in rows if r is not held]
		else:
			url = str(asked.get('url') or '').strip()
			if not url.startswith(('https://', 'http://')):
				raise ValueError("the trade server's MCP address, https://.../mcp")
			token = str(asked.get('token') or '') or (held or {}).get('token')
			if not token:
				raise ValueError("a promote token made on the trade server's settings page")
			row = {'name': name, 'url': url, 'token': token,
				   'status': (held or {}).get('status') if (held or {}).get('url') == url else None}
			# changed where it is in the list, a new one at its end
			rows = [row if r is held else r for r in rows] + ([] if held else [row])
		kept['servers'] = rows
		_write(tradePath(setup), kept, private=True)
	return tradeServers(setup)


def keepStatus(setup, name, status):
	with _lock:
		kept = _read(tradePath(setup))
		for row in kept.get('servers') or []:
			if row.get('name') == name:
				row['status'] = status
		_write(tradePath(setup), kept, private=True)


def poll(service, name=None):
	"""
	Read each trade server's sessions now (live_status): what it trades,
	demo or real money, and every session of it. A server that does not
	answer keeps what it said last, with the error beside it.
	"""
	for server in tradeServers(service.setup, tokens=True):
		if name and server['name'] != name:
			continue
		status = dict(server.get('status') or {}, at=int(time.time() * 1000))
		try:
			got = sources.rpc(server, 'live_status', {}, timeout=60)
			status.update(ok=True, error=None, server=got.get('server'), halted=got.get('halted'),
						  sessions=got.get('sessions') or [])
		except sources.SourceError as exc:
			status.update(ok=False, error=str(exc))
		keepStatus(service.setup, server['name'], status)
	return tradeServers(service.setup)


def start(service):
	"""poll() every POLL minutes, in the background, on an archive with trade servers."""
	def loop():
		while True:
			try:
				if not missing(service.setup, 'archive') and tradeServers(service.setup):
					poll(service)
			except Exception:
				logging.getLogger('parity_deriva.web').exception("trade servers")
			time.sleep(POLL * 60)
	thread = threading.Thread(target=loop, name='trade-servers')
	thread.daemon = True
	thread.start()


def record(service, fields):
	"""
	What a form did on demo: its sessions here (livesessions.record) and on
	every demo trade server, as each said last - what a promotion to a real
	money server carries, for that server to judge.
	"""
	key = livesessions.groupKey(fields)
	sessions = list(service.live.record(fields)['sessions']) if livesessions.serverAccounts() == 'demo' else []
	for server in tradeServers(service.setup):
		status = server.get('status') or {}
		if (status.get('server') or {}).get('accounts') != 'demo':
			continue
		for s in status.get('sessions') or []:
			if livesessions.groupKey(s.get('fields')) == key:
				sessions.append({'id': '%s/%s' % (server['name'], s.get('id')), 'provider': s.get('provider'),
								 'account': s.get('account'), 'demo': s.get('demo'),
								 'started': s.get('started'), 'stopped': s.get('stopped'),
								 'closed': [{'time': t.get('time'), 'pl': t.get('pl')} for t in s.get('closed') or []],
								 'parity': s.get('parity') or {'divergences': 0, 'alarms': []}})
	return {'fields': fields, 'sessions': sessions, **livesessions.judge(sessions)}


def push(service, name, fields):
	"""
	A form to a trade server: the uploaded strategy it trades, with the
	indicators that takes (drafts there, enabled on its settings page), and
	the run of a set it was starred from, which the live page there starts it
	from. To a real money server also its record on demo (record()), judged
	there by that server's own minimums: its verdict is in the answer.
	"""
	from parity_deriva.scripts import sync
	target = one(service.setup, name)
	status = target.get('status') or {}
	if not (status.get('server') or {}).get('accounts'):
		poll(service, name)
		target = one(service.setup, name)
		status = target.get('status') or {}
	kind = (status.get('server') or {}).get('accounts')
	if not kind:
		raise sources.SourceError("%s did not say what it trades: %s" % (name, status.get('error') or 'no answer'))
	fields = dict(fields or {})
	if not fields.get('strategy'):
		raise ValueError("a form has a strategy")
	lines, codes = [], {}
	code = fields['strategy']
	codes[code] = sync.submitted(target, code, dataDir(service.setup), lines.append)
	sent = dict(fields, strategy=codes[code])
	key = livesessions.groupKey(fields)
	starred = next((f for f in service.favourites()
					if (f.get('source') or {}).get('kind') == 'sweep'
					and livesessions.groupKey(f.get('fields')) == key), None)
	if starred:
		sync.pushRuns(target, service, [{'sweep': starred['source']['id'], 'n': starred['source']['n']}],
					  codes, {}, lines.append)
	else:
		lines.append("no starred run of a set with this form: the strategy went, the form did not")
	# the version's card, the gate's: a demo takes a form without one (D6), a
	# real money server judges it by it (web/livesessions.py promote)
	card = cards.forFields(service.setup, fields)
	verdict = None
	if kind == 'real':
		verdict = sources.rpc(target, 'push_record', {'record': dict(record(service, fields), fields=sent,
																	  card=card and dict(card, fields=sent))})
	journal.record(service.setup, code, 'push', 'milestone',
				   {'server': name, 'accounts': kind, 'card': card and card['label'], 'gate': bool(card),
					'verdict': verdict and {'ok': verdict.get('ok'), 'need': verdict.get('need')}},
				   fields, version=card and card['id'])
	return {'server': name, 'accounts': kind, 'lines': lines, 'verdict': verdict, 'gate': bool(card)}
