"""
The service as an MCP server, for an AI assistant of the user's own - claude.ai,
Claude Desktop, ChatGPT, Gemini, or an editor such as Cursor or VS Code - to
read the strategies and the data, write strategies of its own and backtest
them. It cannot trade and it cannot enable a strategy: those stay on the pages.

The protocol is MCP's Streamable HTTP in its plainest form: every message is
a POST to /mcp and every answer is one JSON body, no event stream and no
session. Who may post is web/oauth.py. A strategy it writes is a draft
(strategy/uploaded.py) and every backtest it asks for runs in a child process
with a ceiling on memory and time (web/sandbox.py).

Behind a proxy that serves the service under a path, the addresses the
assistant is sent to must carry that path: set PARITY_DERIVA_PUBLIC_URL, or
have the proxy send X-Forwarded-Prefix. The assistants call from their own
servers, so these routes must be reachable without a client certificate while
every other route keeps its own protection. With nginx and the service at
/parity/:

	location = /parity/mcp { proxy_pass http://127.0.0.1:8731/mcp; ... }
	location ^~ /parity/oauth/ { proxy_pass http://127.0.0.1:8731/oauth/; ... }
	location ^~ /parity/.well-known/ { proxy_pass http://127.0.0.1:8731/.well-known/; ... }
	location = /.well-known/oauth-authorization-server/parity {
		proxy_pass http://127.0.0.1:8731/.well-known/oauth-authorization-server; ... }
	location = /.well-known/oauth-protected-resource/parity/mcp {
		proxy_pass http://127.0.0.1:8731/.well-known/oauth-protected-resource; ... }

each with `proxy_set_header X-Forwarded-Prefix /parity;` and the usual Host
and X-Forwarded-Proto. The connector's address is then https://host/parity/mcp.
"""

import ast
import base64
import datetime
import functools
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from parity_deriva.backtest import ledger
from parity_deriva.strategy import uploaded
from parity_deriva.web import oauth, sandbox

PROTOCOLS = ('2025-11-25', '2025-06-18', '2025-03-26')

GUIDE = """\
parity-deriva backtests forex strategies on stored bid/ask candles. Through this
server you can read the strategies and the data, write strategies of your own
as drafts, and backtest them. You cannot trade and you cannot enable a
strategy: the user does that on the settings page, after reading your code.

A strategy is one Python file with exactly one class, a subclass of
parity_deriva.strategy.H4.H4 (read it with get_source
"parity_deriva.strategy.H4"; a complete example is get_source
"H401-PULLBACK-EMA"; parity_deriva.strategy.M15.M15 is a richer base for
intraday entries). On the class:
- INSTRUMENT and GRANULARITY: what it is meant for, e.g. 'EUR_USD' and 'H1'.
- DESCRIPTION: its rules in plain words, for the person who reads the code.
- setup(self, args): read every tunable number with
  self._set(args, 'name', default). Those numbers are the parameters
  run_backtest accepts; PARAM_HELP = {'name': 'meaning'} explains them.
- series(self): the reading state of one instrument, usually
  parity_deriva.lib.streaming.Series(ema=(50, 200), sma=(), stdev=(), atr=14,
  rsi=None) - read that module for the rest (Swings, Daily, Hourly).
- signal(self, state, candle): called on every closed bar, after state has
  taken it. Return None, or (side, stop, target): side 1 long or -1 short,
  stop and target as prices. candle.mid, candle.bid and candle.ask are dicts
  with 'o', 'h', 'l', 'c'; candle.time is a naive UTC datetime. The entry is at
  market on the next bar's open with the stop and target as a bracket, and
  one position is open at a time.
Imports are limited to the numbers and the parts of this project a strategy
is made of (the refusal lists them); files, network, processes, eval/exec,
getattr/setattr and double-underscore attributes are refused. The TAG becomes
the strategy's name.

Work in this order: list_data, list_strategies and list_helpers to see what
exists; submit_strategy to save a version of a draft (it is imported and built
at once, and an error comes back with its traceback); run_backtest on a year or
two first, then widen. Every backtest is saved, and its link opens it on the
user's page.

Versions: submit_strategy never replaces anything. The first submit of a name
is its version 1, "MY-EMA 1"; every submit of that name after it is the next
version, and the ones before stay as they were. That code is the strategy's
name everywhere - run_backtest, the page, the runs; a bare name means its
newest version. The server ends the file with a PARITY_DERIVA line - the
version, and the version of the server it was written on. Leave it: it is
rewritten on every submit.

The public repository of strategies: when the user wants a strategy of yours
in it, call propose_public with its code and a note of what it does and how it
tested. Nothing is sent from here - the user reads it and opens the pull
request from the settings page, once that version is enabled.

Rules:
1. Build on what exists. Before writing an indicator, a candle pattern, a
   level, a filter or any helper, call list_helpers: what is there you import
   and use, and you do not copy its code into the strategy. Read one with
   get_source when its line is not enough. A built-in strategy close to yours
   is a base to subclass, not a file to copy.
2. A missing indicator or function is a feature request, not your code. Call
   request_feature with what it computes (the formula, or a reference), what
   it takes and gives, and the strategy that needs it, and tell the user the
   strategy waits for it; list_helpers shows the requests already open, so do
   not file one twice. Write it inside the strategy only when the user asks for
   that now - and then file the request all the same, and say so in a comment
   where it is written.
3. Test on years the rules were not tuned on: develop and tune on a first
   stretch (up to the end of 2021, say), then one run on the years after with
   the parameters unchanged. Give both, and say so when the later years are
   worse.
4. Report honestly: always the trades, the net, the max drawdown and the
   profit factor. Under about 100 trades say it is too few to judge, and never
   call a strategy profitable off one window.
5. A grid belongs to the page, not to you: do not call run_backtest in a loop
   over many combinations - the sandbox runs one backtest at a time on a small
   server. Try a few by hand; for a grid, give the user the values to try on
   the simulate page.
6. Pips come from pipSize(instrument) in parity_deriva.lib.utils, never from
   0.0001 written in: a JPY pair's pip is 0.01. A stop or a target is not
   narrower than a few spreads.
7. Every number that sets a rule is a parameter: read with self._set, explained
   in PARAM_HELP, about five at most, and no bare numbers in signal().
8. Talk with the user in the user's language; the code, the names, DESCRIPTION
   and PARAM_HELP are in English."""

#: the modules list_helpers lays out: what a strategy is built from
HELPERS = ('parity_deriva.lib.streaming', 'parity_deriva.lib.indicators',
		   'parity_deriva.lib.utils', 'parity_deriva.strategy.H4', 'parity_deriva.strategy.M15')

#: a feature request's limits: an assistant is not a way to fill the disk
REQUEST_TITLE, REQUEST_TEXT, REQUESTS_OPEN = 120, 4000, 100

#: run_backtest's options: the page's form fields for the account's rules
OPTIONS = {
	'balance': ('number', "starting capital; the account's default when absent"),
	'risk': ('number', "percent of the capital lost at the stop, e.g. 1; absent is a fixed size"),
	'units': ('integer', "the fixed size when risk is absent"),
	'maxStop': ('number', "widest stop accepted, in pips"),
	'maxBars': ('integer', "close a trade open this many bars"),
	'slScale': ('number', "multiply the stop's distance"),
	'tpScale': ('number', "multiply the target's distance"),
	'session': ('string', "UTC hours signals are taken in, e.g. 07:00-16:00"),
	'intraday': ('boolean', "close what is open at the end of the day"),
	'inverse': ('boolean', "turn every order round"),
	'trailing': ('integer', "0 a stop that never moves, 1 one that follows"),
	'trailProfit': ('boolean', "the target becomes a floor the stop follows"),
	'trailPips': ('number', "how far behind the trailing stop follows, in pips"),
	'newsBefore': ('integer', "minutes before a calendar event to stand aside"),
	'newsAfter': ('integer', "minutes after it"),
	'newsImpacts': ('string', "which events count: high, medium, low, comma separated"),
	'leverage': ('integer', "n for n:1, for the margin report"),
}

#: the trades a backtest's answer carries; the rest are on the page
TRADES_SHOWN = 200

TOOLS = [
	{'name': 'list_strategies',
	 'description': "Every strategy that can be backtested: built in, enabled by the user, "
					"or a draft of yours, with its description, what it is meant for and "
					"its parameters.",
	 'inputSchema': {'type': 'object', 'properties': {}}},
	{'name': 'get_source',
	 'description': "The Python source of a strategy (by its name) or of a module a strategy "
					"may import, e.g. parity_deriva.strategy.H4 or parity_deriva.lib.streaming.",
	 'inputSchema': {'type': 'object', 'required': ['name'],
					 'properties': {'name': {'type': 'string'}}}},
	{'name': 'list_data',
	 'description': "The instruments with stored candles, and for each granularity how many "
					"bars there are and the first and last day.",
	 'inputSchema': {'type': 'object', 'properties': {}}},
	{'name': 'submit_strategy',
	 'description': "Save a strategy as the next version of a draft: the first submit of a "
					"name is version 1, each one after it the next, and none is ever replaced. "
					"It is checked, imported and built in a sandbox first; nothing is saved if "
					"that fails. " + GUIDE,
	 'inputSchema': {'type': 'object', 'required': ['name', 'source'], 'properties': {
		 'name': {'type': 'string', 'pattern': uploaded.NAME.pattern,
				  'description': "upper case, digits and dashes, e.g. MY-EMA-CROSS - "
								 "without a version: the server adds it"},
		 'source': {'type': 'string', 'description': "the whole Python file"}}}},
	{'name': 'list_helpers',
	 'description': "What a strategy is built from, to use rather than write again: the "
					"indicators, series, swings, candle patterns and level helpers of this "
					"project, one line each with its arguments, by module - and the feature "
					"requests already open.",
	 'inputSchema': {'type': 'object', 'properties': {}}},
	{'name': 'request_feature',
	 'description': "Ask the user for an indicator or a function a strategy needs and "
					"list_helpers does not have, instead of writing it into the strategy. "
					"The user reads the requests on the settings page.",
	 'inputSchema': {'type': 'object', 'required': ['title', 'description'], 'properties': {
		 'title': {'type': 'string', 'maxLength': REQUEST_TITLE,
				   'description': "what it is, e.g. Keltner channel on the streaming Series"},
		 'description': {'type': 'string', 'maxLength': REQUEST_TEXT,
						 'description': "what it computes (formula or reference), its inputs "
										"and outputs, and how the strategy would call it"},
		 'strategy': {'type': 'string', 'description': "the draft that needs it, if any"}}}},
	{'name': 'run_backtest',
	 'description': "Backtest a strategy on the stored candles and return its report and "
					"trades. Runs in a sandbox with a time limit: start with a year or two.",
	 'inputSchema': {'type': 'object',
					 'required': ['strategy', 'instrument', 'granularity', 'from', 'to'],
					 'properties': {
		 'strategy': {'type': 'string'},
		 'instrument': {'type': 'string', 'description': "e.g. EUR_USD"},
		 'granularity': {'type': 'string', 'description': "e.g. M15, H1, H4, D"},
		 'from': {'type': 'string', 'description': "first day, YYYY-MM-DD"},
		 'to': {'type': 'string', 'description': "last day, YYYY-MM-DD"},
		 'parameters': {'type': 'object', 'additionalProperties': {'type': 'number'},
						'description': "the strategy's own parameters (list_strategies)"},
		 'options': {'type': 'object', 'additionalProperties': False,
					 'description': "the account's rules",
					 'properties': dict((k, {'type': t, 'description': d})
										for k, (t, d) in OPTIONS.items())}}}},
	{'name': 'propose_public',
	 'description': "Propose one version of a strategy of yours for the public repository of "
					"strategies, when the user asks for it. Nothing is sent: the user reads it "
					"on the settings page and opens the pull request from there, once that "
					"version is enabled.",
	 'inputSchema': {'type': 'object', 'required': ['strategy', 'note'], 'properties': {
		 'strategy': {'type': 'string', 'description': "its code, e.g. MY-EMA 3"},
		 'note': {'type': 'string', 'maxLength': REQUEST_TEXT,
				  'description': "what it does and how it tested - windows, trades, net, max "
								 "drawdown: the pull request's text"}}}},
]


class ToolError(Exception):
	"""A tool call refused: the text goes back to the assistant."""


def web():
	# the service imports this module; this one needs its functions only
	# when a tool runs
	from parity_deriva.web import service
	return service


def dataDir(service):
	return service.setup.DATA_DIR


def day(ms):
	return None if ms is None else datetime.datetime.fromtimestamp(
		ms / 1000.0, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')


def meta(path):
	"""What is kept beside a strategy's file, and its name and version read off the file's."""
	try:
		with open(path[:-3] + '.json') as handle:
			found = json.load(handle)
	except (OSError, ValueError):
		found = {}
	m = uploaded.FILE.fullmatch(os.path.basename(path))
	if m:
		found.update(family=m.group(1), version=int(m.group(2)))
	return found


def keepMeta(path, found):
	with open(path[:-3] + '.json.tmp', 'w') as handle:
		json.dump(dict((k, v) for k, v in found.items() if k not in ('family', 'version')), handle)
	os.replace(path[:-3] + '.json.tmp', path[:-3] + '.json')


@functools.lru_cache(maxsize=1)
def serverVersion():
	"""
	What a strategy is written on: VERSION, raised by hand when what a
	strategy builds on changes, and the commit the service runs - e.g.
	1.0.0+4483392, and .dirty after it for a tree with changes not committed.
	Once per process: a restart (scripts/web.py re-execs) reads it again.
	"""
	top = os.path.join(sandbox.TOP, 'parity_deriva')
	try:
		with open(os.path.join(top, 'VERSION')) as handle:
			version = handle.read().strip()
	except OSError:
		version = '0.0.0'
	try:
		commit = subprocess.run(['git', '-C', top, 'rev-parse', '--short=7', 'HEAD'],
								capture_output=True, text=True, timeout=10).stdout.strip()
		dirty = subprocess.run(['git', '-C', top, 'diff', '--quiet', 'HEAD'],
							   capture_output=True, timeout=10).returncode == 1
	except (OSError, subprocess.SubprocessError):
		commit, dirty = '', False
	return version + ('+%s%s' % (commit, '.dirty' if dirty else '') if commit else '')


def resolve(service, name):
	"""A strategy's code: `name` itself, or the newest version of a name of yours."""
	held = dict(uploaded.drafts(dataDir(service)), **uploaded.enabled(dataDir(service)))
	if name in held or name in web().strategies():
		return name
	return uploaded.latest(dataDir(service), name) or name


# ---------------------------------------------------------------- the tools

def listStrategies(service, args):
	w = web()
	enabled = uploaded.enabled(dataDir(service))
	descriptions, defaults = w.descriptions(), w.defaults()
	out = [dict({'name': name, 'state': 'enabled' if name in enabled else 'built in',
				 'description': descriptions.get(name),
				 'parameters': list(w.handlerFields(name))}, **defaults.get(name, {}),
				**(meta(enabled[name]) if name in enabled else {}))
		   for name in w.strategies()]
	for name, path in uploaded.drafts(dataDir(service)).items():
		out.append(dict(meta(path), name=name, state='draft'))
	return {'strategies': out}


def getSource(service, args):
	name = resolve(service, str(args.get('name') or ''))
	for held in (uploaded.drafts(dataDir(service)), uploaded.enabled(dataDir(service))):
		if name in held:
			with open(held[name]) as handle:
				return {'name': name, 'source': handle.read()}
	module = ledger.STRATEGIES.get(name, (name,))[0]
	if not module.startswith('parity_deriva.') or not uploaded.allowedModule(module):
		raise ToolError("%s: no source to show. Strategies are named by list_strategies; "
						"modules are the parity_deriva ones a strategy may import" % name)
	path = os.path.join(sandbox.TOP, *module.split('.')) + '.py'
	with open(path) as handle:
		return {'name': name, 'module': module, 'source': handle.read()}


def listData(service, args):
	return {'instruments': [
		{'instrument': row['instrument'], 'granularities': [
			dict(g, **{'from': day(g['from'])[:10], 'to': day(g['to'])[:10]})
			for g in row['granularities']]}
		for row in service.instruments()]}


# the next version is taken and written by one submit at a time
_submitLock = threading.Lock()


def submitStrategy(service, args, client):
	name, source = str(args.get('name') or ''), args.get('source')
	if not uploaded.NAME.fullmatch(name):
		raise ToolError("name: upper case, digits and dashes, 2 to 40, e.g. MY-EMA-CROSS - "
						"without a version: the server adds it")
	if not isinstance(source, str) or not source.strip():
		raise ToolError("source: the whole Python file")
	if name in web().strategies():
		raise ToolError("%s is a built-in strategy: choose another name" % name)
	problems = uploaded.check(source)
	if problems:
		raise ToolError("not saved:\n" + "\n".join(problems))
	where = os.path.join(uploaded.root(dataDir(service)), 'drafts')
	os.makedirs(where, exist_ok=True)
	# Was: a draft of the same name was replaced. Now: never - each submit is
	# the name's next version, and the one run and read before stays
	with _submitLock:
		version, server = uploaded.nextVersion(dataDir(service), name), serverVersion()
		code = uploaded.code(name, version)
		path = os.path.join(where, uploaded.fileName(code))
		trial = os.path.join(where, '_' + uploaded.fileName(code))
		with open(trial, 'w') as handle:
			handle.write(uploaded.stamp(source, name, version, server))
		try:
			answer = sandboxed(service, {'check': True, 'strategy': {'name': code, 'path': trial}},
							   timeout=60)
			os.replace(trial, path)
		finally:
			if os.path.exists(trial):
				os.remove(trial)
		found = dict(answer['strategy'], server=server, submitted=int(time.time() * 1000),
					 client=client)
		keepMeta(path, found)
	return dict(found, name=code, family=name, version=version, state='draft',
				next="run_backtest with strategy %s" % code)


def runBacktest(service, args, base):
	name = resolve(service, str(args.get('strategy') or ''))
	draft = uploaded.drafts(dataDir(service)).get(name)
	if draft:
		known = [f['name'] for f in meta(draft).get('parameters', ())]
	elif name in web().strategies():
		known = [f['name'] for f in web().handlerFields(name)]
	else:
		raise ToolError("no strategy %r: list_strategies names them" % name)
	fields = {'strategy': name}
	for key in ('instrument', 'granularity', 'from', 'to'):
		fields[key] = str(args.get(key) or '')
	for key, value in (args.get('parameters') or {}).items():
		if key not in known:
			raise ToolError("%s has no parameter %r; it has %s"
							% (name, key, ', '.join(known) or 'none'))
		fields[key] = str(value)
	for key, value in (args.get('options') or {}).items():
		if key not in OPTIONS:
			raise ToolError("no option %r; the options are %s" % (key, ', '.join(OPTIONS)))
		if value is True or value is False:
			value = '1' if value else ''
		if value not in (None, ''):
			fields[key] = str(value)
	answer = sandboxed(service, {'fields': fields, 'strategy': draft and {
		'name': name, 'path': draft}})
	payload = answer['payload']
	service.saveRun(fields, payload)
	run = service.runId(fields)
	_, summary, _ = service.simulated({'kind': 'run', 'id': run})
	summary.update({'from': day(summary['from']), 'to': day(summary['to']),
					'fine': payload.get('fine'), 'counts': payload.get('counts')})
	trades = payload.get('trades') or []
	return {'strategy': name, 'run': run, 'link': base + '/run?' + urllib.parse.urlencode(fields),
			'summary': summary,
			'trades': [{'n': t['n'], 'direction': t['direction'], 'signal': day(t['signalTime']),
						'entry': day(t['entryTime']), 'entryPrice': t['entryPrice'],
						'stopLoss': t['stopLoss'], 'takeProfit': t['takeProfit'],
						'exit': day(t['exitTime']), 'exitPrice': t['exitPrice'],
						'outcome': t['outcome'], 'pl': t['pl'], 'balance': t['balance']}
					   for t in trades[:TRADES_SHOWN]],
			'tradesShown': '%d of %d' % (min(len(trades), TRADES_SHOWN), len(trades))}


def helpers():
	"""
	HELPERS laid out from their source, not imported: every public function,
	class and method, a line each - its arguments and the first paragraph of
	its docstring. Read off the code, so a helper added is listed at once.
	"""
	def line(node, owner=''):
		if isinstance(node, ast.ClassDef):
			args = ', '.join(ast.unparse(b) for b in node.bases)
		else:
			args = ast.unparse(node.args)
		doc = ' '.join((ast.get_docstring(node) or '').split('\n\n')[0].split())
		if len(doc) > 200:
			doc = doc[:197] + '...'
		return '%s%s(%s)%s' % (owner, node.name, args, ' - ' + doc if doc else '')
	out = []
	for module in HELPERS:
		with open(os.path.join(sandbox.TOP, *module.split('.')) + '.py') as handle:
			tree = ast.parse(handle.read())
		lines = []
		for node in tree.body:
			if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and not node.name.startswith('_'):
				lines.append(line(node))
				if isinstance(node, ast.ClassDef):
					lines += [line(m, '    %s.' % node.name) for m in node.body
							  if isinstance(m, ast.FunctionDef) and not m.name.startswith('_')]
		out.append({'module': module, 'helpers': lines})
	return out


def listHelpers(service, args):
	return {'modules': helpers(), 'requested': [
		dict((k, r.get(k)) for k in ('id', 'title', 'strategy')) for r in requests(service)]}


# the requests are one small file, written whole: one writer at a time
_requestsLock = threading.Lock()


def requestsPath(service):
	return os.path.join(uploaded.root(dataDir(service)), 'requests.json')


def requests(service):
	"""The feature requests still open, oldest first."""
	try:
		with open(requestsPath(service)) as handle:
			return json.load(handle)
	except (OSError, ValueError):
		return []


def keepRequests(service, rows):
	path = requestsPath(service)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path + '.tmp', 'w') as handle:
		json.dump(rows, handle, indent=1)
	os.replace(path + '.tmp', path)


def requestFeature(service, args):
	title = ' '.join(str(args.get('title') or '').split())
	text = str(args.get('description') or '').strip()
	strategy = str(args.get('strategy') or '').strip()[:40] or None
	if not title or len(title) > REQUEST_TITLE:
		raise ToolError("title: what is asked for, 1 to %d characters" % REQUEST_TITLE)
	if not text or len(text) > REQUEST_TEXT:
		raise ToolError("description: what it computes, its inputs and outputs, 1 to %d "
						"characters" % REQUEST_TEXT)
	with _requestsLock:
		rows = requests(service)
		same = [r for r in rows if r['title'].lower() == title.lower()]
		if same:
			raise ToolError("already requested as %s: %s. Add to it by telling the user"
							% (same[0]['id'], same[0]['title']))
		if len(rows) >= REQUESTS_OPEN:
			raise ToolError("%d requests are open already: the user has to answer some "
							"on the settings page first" % len(rows))
		row = {'id': 'R%d' % (max([int(r['id'][1:]) for r in rows] or [0]) + 1), 'title': title,
			   'description': text, 'strategy': strategy, 'submitted': int(time.time() * 1000)}
		keepRequests(service, rows + [row])
	return dict(row, next="tell the user that %s waits for it: the request is on the "
				"settings page" % (strategy or 'the strategy'))


def dropRequest(service, id):
	"""A request answered, from the settings page: it leaves the list."""
	with _requestsLock:
		rows = requests(service)
		if not any(r['id'] == id for r in rows):
			raise ToolError("no request %r" % id)
		keepRequests(service, [r for r in rows if r['id'] != id])
	return status(service)


def proposePublic(service, args):
	code = resolve(service, str(args.get('strategy') or ''))
	note = str(args.get('note') or '').strip()
	enabled = uploaded.enabled(dataDir(service))
	path = uploaded.drafts(dataDir(service)).get(code) or enabled.get(code)
	if not path:
		raise ToolError("%s is not one of yours: only a strategy written here goes to the "
						"public repository (list_strategies)" % code)
	if not note or len(note) > REQUEST_TEXT:
		raise ToolError("note: what it does and how it tested, 1 to %d characters" % REQUEST_TEXT)
	found = meta(path)
	if found.get('pull'):
		raise ToolError("%s has its pull request already: %s" % (code, found['pull']['url']))
	found['proposed'] = {'note': note, 'at': int(time.time() * 1000)}
	keepMeta(path, found)
	return {'strategy': code, 'proposed': True, 'next': "tell the user it waits on the settings "
			"page: " + ("the pull request is opened there" if code in enabled else
						"enable %s there, then open the pull request" % code)}


def github(token, method, path, body=None):
	"""One call to GitHub's REST API: its answer, or ToolError in GitHub's words."""
	request = urllib.request.Request(
		'https://api.github.com' + path, method=method,
		data=None if body is None else json.dumps(body).encode(),
		headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
				 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'parity-deriva'})
	try:
		with urllib.request.urlopen(request, timeout=30) as response:
			raw = response.read()
	except urllib.error.HTTPError as error:
		try:
			said = json.loads(error.read() or b'{}').get('message') or ''
		except ValueError:
			said = ''
		raise ToolError("GitHub said %s to %s %s: %s" % (error.code, method, path, said))
	except (urllib.error.URLError, OSError) as error:
		raise ToolError("GitHub cannot be reached: %s" % error)
	return json.loads(raw) if raw else {}


def pullRequest(service, code):
	"""
	The pull request of a version the assistant proposed and the user enabled,
	opened on PUBLIC_REPO with GITHUB_TOKEN from the settings page: its file as
	it is, stamp included, and a card of what the page knows of it beside it,
	under strategies/NAME/, on a branch of its own - of the repository when
	the token may push to it, else of the token's fork of it.
	"""
	setup = service.setup
	repo, token = getattr(setup, 'PUBLIC_REPO', None), getattr(setup, 'GITHUB_TOKEN', None)
	if not repo or not token:
		raise ToolError("no public repository: set PARITY_DERIVA_PUBLIC_REPO (owner/name) and "
						"PARITY_DERIVA_GITHUB_TOKEN in parity_deriva/.env, then restart")
	path = uploaded.enabled(dataDir(service)).get(code)
	if not path:
		raise ToolError("%s is not enabled: a pull request carries a version somebody read" % code)
	found = meta(path)
	if not found.get('proposed'):
		raise ToolError("%s was not proposed: the assistant proposes it with a note "
						"(propose_public)" % code)
	if found.get('pull'):
		raise ToolError("%s has its pull request already: %s" % (code, found['pull']['url']))
	with open(path) as handle:
		source = handle.read()
	upstream = github(token, 'GET', '/repos/' + repo)
	base, head = upstream['default_branch'], repo
	if not (upstream.get('permissions') or {}).get('push'):
		head = github(token, 'POST', '/repos/%s/forks' % repo, {'default_branch_only': True})['full_name']
		# GitHub makes a fork in the background: wait for it, then bring it level
		for _ in range(15):
			try:
				github(token, 'GET', '/repos/' + head)
				break
			except ToolError:
				time.sleep(2)
		github(token, 'POST', '/repos/%s/merge-upstream' % head, {'branch': base})
	sha = github(token, 'GET', '/repos/%s/git/ref/heads/%s' % (repo, base))['object']['sha']
	# the time in it: a try that failed half way leaves its branch, and the next is another
	branch = 'strategy/%s-v%d-%d' % (found['family'], found['version'], time.time())
	github(token, 'POST', '/repos/%s/git/refs' % head, {'ref': 'refs/heads/' + branch, 'sha': sha})
	card = dict((k, found.get(k)) for k in ('family', 'version', 'server', 'description',
											'instrument', 'granularity', 'parameters', 'submitted'))
	card.update(code=code, note=found['proposed']['note'])
	folder = 'strategies/%s/' % found['family']
	for name, text in ((uploaded.fileName(code), source),
					   (uploaded.fileName(code)[:-3] + '.json', json.dumps(card, indent=1) + '\n')):
		github(token, 'PUT', '/repos/%s/contents/%s' % (head, urllib.parse.quote(folder + name)),
			   {'message': 'Add %s' % code, 'branch': branch,
				'content': base64.b64encode(text.encode()).decode()})
	pull = github(token, 'POST', '/repos/%s/pulls' % repo, {
		'title': 'Add %s' % code, 'base': base,
		'head': branch if head == repo else '%s:%s' % (head.split('/')[0], branch),
		'body': '**%s**: %s\n\n- written on parity-deriva %s\n- meant for %s %s\n'
				'- parameters: %s\n\n%s\n' % (
					code, found.get('description') or '', found.get('server') or 'a server before versions',
					found.get('instrument') or 'any instrument', found.get('granularity') or '',
					', '.join(p.get('name', '') for p in found.get('parameters') or ()) or 'none',
					found['proposed']['note'])})
	found['pull'] = {'url': pull['html_url'], 'number': pull['number'], 'at': int(time.time() * 1000)}
	keepMeta(path, found)
	return status(service)


def sandboxed(service, job, timeout=None):
	"""The sandbox's answer, one at a time: a second call waits for the first."""
	setup = service.setup
	job = dict(job, memoryMb=getattr(setup, 'MCP_SANDBOX_MB', 1024))
	# ponytail: one sandbox at a time for the whole service; a queue per
	# assistant if several ever share one machine
	with service._sandbox:
		answer = sandbox.run(job, dataDir(service),
							 timeout or getattr(setup, 'MCP_SANDBOX_SECONDS', 240))
	if 'error' in answer:
		raise ToolError(answer['error'] + ('\n' + answer['trace'] if answer.get('trace') else ''))
	return answer


def call(service, name, args, base, client):
	if name == 'list_strategies':
		return listStrategies(service, args)
	if name == 'get_source':
		return getSource(service, args)
	if name == 'list_data':
		return listData(service, args)
	if name == 'list_helpers':
		return listHelpers(service, args)
	if name == 'request_feature':
		return requestFeature(service, args)
	if name == 'submit_strategy':
		return submitStrategy(service, args, client)
	if name == 'run_backtest':
		return runBacktest(service, args, base)
	if name == 'propose_public':
		return proposePublic(service, args)
	raise ToolError("no tool %r" % name)


# ------------------------------------------------------------- the protocol

def handle(service, message, base, client):
	"""One JSON-RPC message: its answer, or None for a notification. `client` is
	who sent it, kept with the strategies it submits."""
	# a notification, or an answer to a request this server never makes
	if not isinstance(message, dict) or 'id' not in message or 'method' not in message:
		return None
	method, params = message.get('method'), message.get('params') or {}
	answer = {'jsonrpc': '2.0', 'id': message['id']}
	if method == 'initialize':
		asked = params.get('protocolVersion')
		answer['result'] = {'protocolVersion': asked if asked in PROTOCOLS else PROTOCOLS[0],
							'capabilities': {'tools': {}},
							'serverInfo': {'name': 'parity-deriva', 'version': serverVersion()},
							'instructions': GUIDE}
	elif method == 'ping':
		answer['result'] = {}
	elif method == 'tools/list':
		answer['result'] = {'tools': TOOLS}
	elif method == 'tools/call':
		try:
			found = call(service, params.get('name'), params.get('arguments') or {}, base, client)
			answer['result'] = {'content': [{'type': 'text', 'text': json.dumps(found, indent=1)}]}
		except (ToolError, uploaded.UploadError, sandbox.SandboxError, ledger.LedgerError,
				web().ServiceError, OSError) as exc:
			answer['result'] = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
	else:
		answer['error'] = {'code': -32601, 'message': "no method %s" % method}
	return answer


# ------------------------------------------------- the settings page's side

def status(service):
	"""The settings page's box: the secret, the clients, the strategies."""
	rows = []
	for state, held in (('draft', uploaded.drafts(dataDir(service))),
						('enabled', uploaded.enabled(dataDir(service)))):
		for name, path in held.items():
			rows.append(dict(meta(path), name=name, state=state))
	return dict(service.oauth.status(), strategies=sorted(
		rows, key=lambda r: r.get('submitted') or 0, reverse=True),
		requests=requests(service)[::-1])


def source(service, name):
	for held in (uploaded.drafts(dataDir(service)), uploaded.enabled(dataDir(service))):
		if name in held:
			with open(held[name]) as handle:
				text = handle.read()
			return {'name': name, 'source': text, 'problems': uploaded.check(text)}
	raise ToolError("no uploaded strategy %r" % name)


def act(service, name, action):
	"""enable, disable or delete an uploaded strategy, from the settings page."""
	draft = uploaded.drafts(dataDir(service)).get(name)
	live = uploaded.enabled(dataDir(service)).get(name)
	if action == 'enable':
		if not draft:
			raise ToolError("no draft %r" % name)
		if name in ledger.STRATEGIES:
			raise ToolError("a strategy is already called %s" % name)
		target = os.path.join(uploaded.root(dataDir(service)), uploaded.fileName(name))
		moves = [(draft, target), (draft[:-3] + '.json', target[:-3] + '.json')]
		for a, b in moves:
			if os.path.exists(a):
				os.replace(a, b)
		try:
			ledger.STRATEGIES[name] = uploaded.load(name, target)
		except BaseException as exc:
			for a, b in moves:
				if os.path.exists(b):
					os.replace(b, a)
			raise ToolError("%s does not load, so it stays a draft: %s: %s"
							% (name, type(exc).__name__, exc))
	elif action == 'disable':
		if not live:
			raise ToolError("%s is not enabled" % name)
		ledger.STRATEGIES.pop(name, None)
		sys.modules.pop(uploaded.moduleName(name), None)
		where = os.path.join(uploaded.root(dataDir(service)), 'drafts')
		os.makedirs(where, exist_ok=True)
		for a in (live, live[:-3] + '.json'):
			if os.path.exists(a):
				os.replace(a, os.path.join(where, os.path.basename(a)))
	elif action == 'delete':
		if not draft:
			raise ToolError("only a draft is deleted; disable %s first" % name)
		for a in (draft, draft[:-3] + '.json'):
			if os.path.exists(a):
				os.remove(a)
	else:
		raise ToolError("the action is enable, disable or delete")
	return status(service)


# --------------------------------------------------------------- the routes

def reply(handler, status, body, kind='application/json', headers=()):
	data = body if isinstance(body, bytes) else (
		body.encode('utf-8') if isinstance(body, str) else json.dumps(body).encode('utf-8'))
	handler.send_response(status)
	handler.send_header('Content-Type', kind + '; charset=utf-8')
	handler.send_header('Content-Length', str(len(data)))
	handler.send_header('Cache-Control', 'no-store')
	for key, value in headers:
		handler.send_header(key, value)
	handler.end_headers()
	handler.wfile.write(data)
	return True


def publicBase(handler):
	"""Where the assistant reaches this service: see the module docstring."""
	configured = getattr(handler.service.setup, 'PUBLIC_URL', None)
	if configured:
		return configured.rstrip('/')
	return '%s://%s%s' % (handler.headers.get('X-Forwarded-Proto') or 'http',
						  handler.headers.get('X-Forwarded-Host') or handler.headers.get('Host')
						  or 'localhost', (handler.headers.get('X-Forwarded-Prefix') or '').rstrip('/'))


def body(handler):
	try:
		length = int(handler.headers.get('Content-Length') or 0)
	except ValueError:
		length = 0
	return handler.rfile.read(min(length, 4 << 20)) if length > 0 else b''


def form(handler):
	return dict((k, v[0]) for k, v in urllib.parse.parse_qs(
		body(handler).decode('utf-8', 'replace'), keep_blank_values=True).items())


def route(handler, method, path, query):
	"""Serve an MCP, OAuth or settings route; False for any other."""
	authority = handler.service.oauth
	if path == '/mcp':
		if method != 'POST':
			return reply(handler, 405, {'error': "POST only"}, headers=[('Allow', 'POST')])
		base = publicBase(handler)
		auth = handler.headers.get('Authorization') or ''
		bearer = auth[7:].strip() if auth[:7].lower() == 'bearer ' else None
		if not authority.allowed(bearer):
			return reply(handler, 401, {'error': 'invalid_token'}, headers=[(
				'WWW-Authenticate', 'Bearer resource_metadata="%s/.well-known/oauth-protected-resource"'
				% base)])
		try:
			message = json.loads(body(handler) or b'null')
		except ValueError:
			return reply(handler, 400, {'jsonrpc': '2.0', 'id': None,
										'error': {'code': -32700, 'message': "not JSON"}})
		# the OAuth client's own name; with the secret in the header, the only
		# name there is is the program's User-Agent
		client = authority.holder(bearer) or 'token: %s' % (
			handler.headers.get('User-Agent') or 'no user agent')[:60]
		if isinstance(message, list):
			answers = [a for a in (handle(handler.service, m, base, client) for m in message) if a]
		else:
			answers = handle(handler.service, message, base, client)
		return reply(handler, 200, answers) if answers else reply(handler, 202, b'')
	if path.startswith('/.well-known/oauth-protected-resource'):
		return reply(handler, 200, authority.resourceMetadata(publicBase(handler)))
	if path.startswith(('/.well-known/oauth-authorization-server',
						'/.well-known/openid-configuration')):
		return reply(handler, 200, authority.serverMetadata(publicBase(handler)))
	try:
		if path == '/oauth/register' and method == 'POST':
			try:
				asked = json.loads(body(handler) or b'{}')
			except ValueError:
				asked = None
			return reply(handler, 201, authority.register(asked))
		if path == '/oauth/authorize':
			params = form(handler) if method == 'POST' else dict((k, v[0]) for k, v in query.items())
			client, _ = authority.request(params)
			if method == 'POST':
				back = authority.decide(params, publicBase(handler))
				if back:
					return reply(handler, 302, b'', headers=[('Location', back)])
			return reply(handler, 403 if method == 'POST' else 200,
						 authority.consentPage(client, params, wrong=method == 'POST'), 'text/html')
		if path == '/oauth/token' and method == 'POST':
			return reply(handler, 200, authority.token(form(handler)))
	except oauth.OAuthError as exc:
		if path == '/oauth/authorize':
			return reply(handler, exc.status, '<!doctype html><title>parity-deriva</title><p>%s</p>'
						 % oauth.html.escape(str(exc)), 'text/html')
		return reply(handler, exc.status, {'error': exc.code, 'error_description': str(exc)})
	if not path.startswith('/api/mcp'):
		return False
	# the settings page's own routes, behind whatever guards the pages
	try:
		if method == 'GET' and path == '/api/mcp':
			return reply(handler, 200, status(handler.service))
		if method == 'GET' and path == '/api/mcp/source':
			return reply(handler, 200, source(handler.service, handler.one(query, 'name') or ''))
		if method != 'POST' or handler.headers.get('X-Parity-Deriva') != '1':
			return reply(handler, 403, {'error': "missing X-Parity-Deriva header"})
		if path == '/api/mcp/secret':
			return reply(handler, 200, {'secret': authority.newSecret()})
		if path == '/api/mcp/disconnect':
			authority.disconnect()
			return reply(handler, 200, status(handler.service))
		if path == '/api/mcp/strategy':
			try:
				asked = json.loads(body(handler) or b'{}')
			except ValueError:
				asked = {}
			return reply(handler, 200, act(handler.service, str(asked.get('name') or ''),
										   asked.get('action')))
		if path == '/api/mcp/import':
			# a file saved from the code's dialog, back as a draft: the same
			# checks as an assistant's submit_strategy
			try:
				asked = json.loads(body(handler) or b'{}')
			except ValueError:
				asked = {}
			submitStrategy(handler.service, asked, 'imported from a file')
			return reply(handler, 200, status(handler.service))
		if path == '/api/mcp/request':
			try:
				asked = json.loads(body(handler) or b'{}')
			except ValueError:
				asked = {}
			return reply(handler, 200, dropRequest(handler.service, str(asked.get('id') or '')))
		if path == '/api/mcp/pull':
			try:
				asked = json.loads(body(handler) or b'{}')
			except ValueError:
				asked = {}
			return reply(handler, 200, pullRequest(handler.service, str(asked.get('name') or '')))
	except (ToolError, uploaded.UploadError, sandbox.SandboxError) as exc:
		return reply(handler, 400, {'error': str(exc)})
	return reply(handler, 404, {'error': "no route %s" % path})
