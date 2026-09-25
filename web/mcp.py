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

import datetime
import json
import os
import sys
import time
import urllib.parse

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

Work in this order: list_data and list_strategies to see what exists;
submit_strategy to save or replace a draft (it is imported and built at once,
and an error comes back with its traceback); run_backtest on a year or two
first, then widen. Every backtest is saved, and its link opens it on the
user's page."""

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
	 'description': "Save a strategy as a draft, or replace your draft of that name. It is "
					"checked, imported and built in a sandbox first; nothing is saved if that "
					"fails. " + GUIDE,
	 'inputSchema': {'type': 'object', 'required': ['name', 'source'], 'properties': {
		 'name': {'type': 'string', 'pattern': uploaded.NAME.pattern,
				  'description': "upper case, digits and dashes, e.g. MY-EMA-CROSS"},
		 'source': {'type': 'string', 'description': "the whole Python file"}}}},
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
	try:
		with open(path[:-3] + '.json') as handle:
			return json.load(handle)
	except (OSError, ValueError):
		return {}


# ---------------------------------------------------------------- the tools

def listStrategies(service, args):
	w = web()
	enabled = uploaded.enabled(dataDir(service))
	descriptions, defaults = w.descriptions(), w.defaults()
	out = [dict({'name': name, 'state': 'enabled' if name in enabled else 'built in',
				 'description': descriptions.get(name),
				 'parameters': list(w.handlerFields(name))}, **defaults.get(name, {}))
		   for name in w.strategies()]
	for name, path in uploaded.drafts(dataDir(service)).items():
		out.append(dict(meta(path), name=name, state='draft'))
	return {'strategies': out}


def getSource(service, args):
	name = str(args.get('name') or '')
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


def submitStrategy(service, args):
	name, source = str(args.get('name') or ''), args.get('source')
	if not uploaded.NAME.fullmatch(name):
		raise ToolError("name: upper case, digits and dashes, 2 to 40, e.g. MY-EMA-CROSS")
	if not isinstance(source, str) or not source.strip():
		raise ToolError("source: the whole Python file")
	if name in uploaded.enabled(dataDir(service)):
		raise ToolError("%s is enabled: the user has to disable it on the settings page "
						"before it can be replaced. Submit it under another name." % name)
	if name in web().strategies():
		raise ToolError("%s is a built-in strategy: choose another name" % name)
	problems = uploaded.check(source)
	if problems:
		raise ToolError("not saved:\n" + "\n".join(problems))
	where = os.path.join(uploaded.root(dataDir(service)), 'drafts')
	os.makedirs(where, exist_ok=True)
	trial = os.path.join(where, '_%s.py' % name)
	with open(trial, 'w') as handle:
		handle.write(source)
	try:
		answer = sandboxed(service, {'check': True, 'strategy': {'name': name, 'path': trial}},
						   timeout=60)
		os.replace(trial, os.path.join(where, name + '.py'))
	finally:
		if os.path.exists(trial):
			os.remove(trial)
	found = dict(answer['strategy'], submitted=int(time.time() * 1000))
	with open(os.path.join(where, name + '.json'), 'w') as handle:
		json.dump(found, handle)
	return dict(found, name=name, state='draft',
				next="run_backtest with strategy %s" % name)


def runBacktest(service, args, base):
	name = str(args.get('strategy') or '')
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
	return {'run': run, 'link': base + '/run?' + urllib.parse.urlencode(fields),
			'summary': summary,
			'trades': [{'n': t['n'], 'direction': t['direction'], 'signal': day(t['signalTime']),
						'entry': day(t['entryTime']), 'entryPrice': t['entryPrice'],
						'stopLoss': t['stopLoss'], 'takeProfit': t['takeProfit'],
						'exit': day(t['exitTime']), 'exitPrice': t['exitPrice'],
						'outcome': t['outcome'], 'pl': t['pl'], 'balance': t['balance']}
					   for t in trades[:TRADES_SHOWN]],
			'tradesShown': '%d of %d' % (min(len(trades), TRADES_SHOWN), len(trades))}


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


def call(service, name, args, base):
	if name == 'list_strategies':
		return listStrategies(service, args)
	if name == 'get_source':
		return getSource(service, args)
	if name == 'list_data':
		return listData(service, args)
	if name == 'submit_strategy':
		return submitStrategy(service, args)
	if name == 'run_backtest':
		return runBacktest(service, args, base)
	raise ToolError("no tool %r" % name)


# ------------------------------------------------------------- the protocol

def handle(service, message, base):
	"""One JSON-RPC message: its answer, or None for a notification."""
	# a notification, or an answer to a request this server never makes
	if not isinstance(message, dict) or 'id' not in message or 'method' not in message:
		return None
	method, params = message.get('method'), message.get('params') or {}
	answer = {'jsonrpc': '2.0', 'id': message['id']}
	if method == 'initialize':
		asked = params.get('protocolVersion')
		answer['result'] = {'protocolVersion': asked if asked in PROTOCOLS else PROTOCOLS[0],
							'capabilities': {'tools': {}},
							'serverInfo': {'name': 'parity-deriva', 'version': '1'},
							'instructions': GUIDE}
	elif method == 'ping':
		answer['result'] = {}
	elif method == 'tools/list':
		answer['result'] = {'tools': TOOLS}
	elif method == 'tools/call':
		try:
			found = call(service, params.get('name'), params.get('arguments') or {}, base)
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
		rows, key=lambda r: r.get('submitted') or 0, reverse=True))


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
		target = os.path.join(uploaded.root(dataDir(service)), name + '.py')
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
		if not authority.allowed(auth[7:].strip() if auth[:7].lower() == 'bearer ' else None):
			return reply(handler, 401, {'error': 'invalid_token'}, headers=[(
				'WWW-Authenticate', 'Bearer resource_metadata="%s/.well-known/oauth-protected-resource"'
				% base)])
		try:
			message = json.loads(body(handler) or b'null')
		except ValueError:
			return reply(handler, 400, {'jsonrpc': '2.0', 'id': None,
										'error': {'code': -32700, 'message': "not JSON"}})
		if isinstance(message, list):
			answers = [a for a in (handle(handler.service, m, base) for m in message) if a]
		else:
			answers = handle(handler.service, message, base)
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
	except ToolError as exc:
		return reply(handler, 400, {'error': str(exc)})
	return reply(handler, 404, {'error': "no route %s" % path})
