"""
The logs, read on the logs page (static/logs.html): every file this user's
crontab writes to, each live session's console, and this service's web.log.

Those and nothing else. The list is built here - from `crontab -l` and the
sessions' folders - and a file is read only if it is on it: the page names a
file by its path, and a path not on the list is refused, never opened.

	GET logs                          the page
	GET api/logs                      the list
	GET api/logs/tail?path=&lines=    the last lines of one of them
"""
import os
import re
import subprocess
import time

from parity_deriva.web import livesessions

LINES = 500
MOST = 5000
#: how much of a file's end a tail reads: a file this big gives its last 4 MB
WINDOW = 4 * 1024 * 1024
#: seconds a read of the crontab is kept
KEEP = 30
#: `>> file`, `> file`, `1>> file`, `&> file`, but not `2>&1`
REDIRECT = re.compile(r'(?:^|\s)[0-9&]?>>?\s*(?!&)([^\s;&|<>]+)')
CD = re.compile(r'^\s*cd\s+(\S+)\s*&&')


def servicePath(setup):
	"""This service's own log, which scripts/web.py writes besides the journal."""
	return os.path.join(getattr(setup, 'DATA_DIR', '') or '.', 'logs', 'web.log')


_read = {'at': 0.0, 'jobs': []}


def crontab():
	"""
	(schedule, command) for every job in this user's crontab, read at most
	every KEEP seconds: cron writes a journal line for every `crontab -l`,
	and a page following a log asks every five.
	"""
	if time.monotonic() - _read['at'] < KEEP:
		return _read['jobs']
	try:
		text = subprocess.run(['crontab', '-l'], capture_output=True, text=True,
							  timeout=10).stdout
	except (OSError, subprocess.SubprocessError):
		return []
	jobs = []
	for line in text.splitlines():
		line = line.strip()
		if not line or line.startswith('#'):
			continue
		if line.startswith('@'):
			words = line.split(None, 1)
		else:
			words = line.split(None, 5)
			words = [' '.join(words[:5])] + words[5:]
		# MAILTO=..., PATH=...: a variable, not a job
		if len(words) == 2:
			jobs.append((words[0], words[1]))
	_read.update(at=time.monotonic(), jobs=jobs)
	return jobs


def targets(command):
	"""The files a cron command writes its output to, as absolute paths."""
	where = CD.match(command)
	base = os.path.expanduser(where.group(1)) if where else os.path.expanduser('~')
	return [os.path.normpath(os.path.join(base, os.path.expanduser(p)))
			for p in REDIRECT.findall(command)]


def stat(path):
	try:
		info = os.stat(path)
		return {'size': info.st_size, 'modified': int(info.st_mtime * 1000)}
	except OSError:
		return {'size': None, 'modified': None}


def logs(service):
	"""Every log the page may read: [{group, name, path, about, size, modified}]."""
	out, seen = [], {}
	for schedule, command in crontab():
		paths = targets(command)
		if not paths:
			# its output goes to cron's mail, which no page can read
			out.append(dict({'group': 'cron', 'name': command[:60], 'path': None,
							 'about': "%s  %s  (no log file)" % (schedule, command)}, **stat('')))
		for path in paths:
			if path in seen:
				seen[path]['about'] += "\n%s  %s" % (schedule, command)
				continue
			seen[path] = dict({'group': 'cron', 'name': os.path.basename(path), 'path': path,
							   'about': "%s  %s" % (schedule, command)}, **stat(path))
			out.append(seen[path])
	path = servicePath(service.setup)
	# what it is, the page says (static/logs.js SERVICE), in the page's language
	out.append(dict({'group': 'service', 'name': 'web.log', 'path': path, 'about': ''},
					**stat(path)))
	live = service.live
	for session in live.ids():
		try:
			meta, path = live.meta(session) or {}, live.path(session, 'console.log')
		except livesessions.LiveError:
			# a row whose id is not a session's: nothing of it on disk to read
			continue
		out.append(dict({'group': 'live', 'name': session, 'path': path,
						 'about': "%s %s %s" % (meta.get('provider') or '', meta.get('account') or '',
											   (meta.get('fields') or {}).get('strategy') or '')},
						**stat(path)))
	return out


def tail(path, lines):
	"""The last `lines` lines of a file, read from its last WINDOW bytes."""
	try:
		size = os.path.getsize(path)
		with open(path, 'rb') as handle:
			handle.seek(max(0, size - WINDOW))
			text = handle.read().decode('utf-8', 'replace')
	except OSError:
		return []
	rows = text.splitlines()
	# the first line of a window that does not start at 0 is cut in two
	return (rows[1:] if size > WINDOW else rows)[-lines:]


def route(handler, method, path, query):
	"""The page and its two calls; False for any other route."""
	if method != 'GET' or path not in ('/logs', '/api/logs', '/api/logs/tail'):
		return False
	if path == '/logs':
		handler.sendFile('logs.html')
		return True
	known = logs(handler.service)
	if path == '/api/logs':
		handler.sendJSON({'logs': known})
		return True
	wanted = (query.get('path') or [''])[0]
	entry = next((e for e in known if wanted and e['path'] == wanted), None)
	if entry is None:
		handler.sendError("not one of the logs this page shows: %r" % wanted, 404)
		return True
	try:
		lines = min(MOST, max(1, int((query.get('lines') or [LINES])[0])))
	except ValueError:
		lines = LINES
	handler.sendJSON(dict(entry, lines=tail(wanted, lines)))
	return True
