"""
Tests for the MCP door: strategy/uploaded.py, web/sandbox.py, web/oauth.py
and web/mcp.py.

One loopback server over the temporary store web_test.py writes, taken the
way an assistant takes it: register, consent with the secret, trade the code
with its PKCE verifier, then the tools. The sandbox runs as the real child
process, so a loop and a memory eater are each stopped the real way.
"""

import base64
import hashlib
import html
import json
import os
import secrets
import shutil
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pandas as pd

from parity_deriva.backtest import ledger
from parity_deriva.data import calendar
from parity_deriva.strategy import uploaded
from parity_deriva.tests.web_test import StoreCase
from parity_deriva.web import mcp
from parity_deriva.web import service as service_module

GOOD = '''
from parity_deriva.lib.streaming import Series
from parity_deriva.strategy.H4 import H4, bullish


class EveryThird(H4):
	GRANULARITY = 'H1'
	DESCRIPTION = "Long on every third bullish bar, 2R."

	def setup(self, args):
		self._set(args, 'every', 3)

	def series(self):
		return Series(atr=5)

	def signal(self, state, candle):
		atr = state.atr()
		if not atr or state.seen % self.every or not bullish(candle):
			return None
		stop = candle.mid['c'] - atr
		return 1, stop, self.levels(candle, 1, stop, 2.0)
'''


class CheckTest(unittest.TestCase):

	def test_it_takes_a_strategy_made_of_this_project(self):
		self.assertEqual(uploaded.check(GOOD), [])

	def test_it_refuses_what_reaches_outside(self):
		for source, word in (("import os", "import of os"),
							 ("from parity_deriva.etc import settings", "parity_deriva.etc"),
							 ("from parity_deriva.lib import oanda", "parity_deriva.lib"),
							 ("x = open('/etc/passwd')", "open"),
							 ("x = ().__class__.__bases__[0].__subclasses__()", "__bases__"),
							 ("x = getattr(object, 'y')", "getattr"),
							 ("def f(:", "line 1")):
			problems = uploaded.check(source)
			self.assertTrue(any(word in p for p in problems), (source, problems))


class VersionTest(unittest.TestCase):

	def test_a_file_from_before_versions_is_version_one(self):
		where = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, where)
		for name in ('OLD.py', 'OLD.json', 'NEW@2.py'):
			open(os.path.join(where, name), 'w').close()
		self.assertEqual(list(uploaded._listed(where)), ['NEW 2', 'OLD 1'])
		self.assertEqual(sorted(os.listdir(where)), ['NEW@2.py', 'OLD@1.json', 'OLD@1.py'])

	def test_the_stamp_is_one_line_however_often_it_is_written(self):
		once = uploaded.stamp(GOOD, 'EVERY-THIRD', 1, '1.0.0+abc')
		twice = uploaded.stamp(once.replace("'version': 1", "'version': 9"), 'EVERY-THIRD', 2, '1.0.1')
		self.assertEqual(twice.count(uploaded.STAMP + ' ='), 1)
		self.assertIn("'version': 2, 'server': '1.0.1'", twice)
		self.assertEqual(uploaded.check(twice), [])
		self.assertEqual(uploaded.split('EVERY-THIRD 2'), ('EVERY-THIRD', 2))
		self.assertEqual(uploaded.split('H401-PULLBACK-EMA'), ('H401-PULLBACK-EMA', None))


class MCPTest(StoreCase):

	def setUp(self):
		super(MCPTest, self).setUp()
		self.settings.MCP_SANDBOX_SECONDS = 30
		self.settings.MCP_SANDBOX_MB = 1024
		handler = type('TestHandler', (service_module.Handler,), {'service': self.service})
		self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
		self.base = 'http://127.0.0.1:%d' % self.server.server_address[1]
		thread = threading.Thread(target=self.server.serve_forever, daemon=True)
		thread.start()
		self.addCleanup(self.server.server_close)
		self.addCleanup(self.server.shutdown)
		self.addCleanup(lambda: [ledger.STRATEGIES.pop(n, None) for n in ('EVERY-THIRD 1',)])
		self.bearer = None

	def http(self, path, data=None, headers=None, kind='application/json'):
		if isinstance(data, dict):
			data = json.dumps(data).encode() if kind == 'application/json' \
				else urllib.parse.urlencode(data).encode()
		request = urllib.request.Request(self.base + path, data=data,
										 headers=dict({'Content-Type': kind}, **(headers or {})))
		opener = urllib.request.build_opener(NoRedirect)
		try:
			with opener.open(request, timeout=120) as response:
				return response.status, response.read(), response.headers
		except urllib.error.HTTPError as error:
			return error.code, error.read(), error.headers

	def rpc(self, method, params=None):
		status, raw, _ = self.http('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': method,
											'params': params or {}},
								   {'Authorization': 'Bearer %s' % self.bearer})
		self.assertEqual(status, 200, raw)
		return json.loads(raw)['result']

	def tool(self, tool, **args):
		result = self.rpc('tools/call', {'name': tool, 'arguments': args})
		text = result['content'][0]['text']
		return (text, True) if result.get('isError') else (json.loads(text), False)

	def connect(self):
		"""The whole OAuth dance an assistant does, to an access token."""
		secret = self.service.oauth.newSecret()
		status, raw, headers = self.http('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'})
		self.assertEqual(status, 401)
		self.assertIn('/.well-known/oauth-protected-resource', headers['WWW-Authenticate'])
		meta = json.loads(self.http('/.well-known/oauth-authorization-server')[1])
		self.assertEqual(meta['code_challenge_methods_supported'], ['S256'])
		client = json.loads(self.http('/oauth/register', {
			'client_name': 'Test', 'redirect_uris': ['https://claude.ai/api/mcp/auth_callback']})[1])
		verifier = secrets.token_urlsafe(40)
		challenge = base64.urlsafe_b64encode(
			hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
		asked = {'client_id': client['client_id'], 'response_type': 'code', 'state': 'xyz',
				 'redirect_uri': 'https://claude.ai/api/mcp/auth_callback',
				 'code_challenge': challenge, 'code_challenge_method': 'S256'}
		status, page, _ = self.http('/oauth/authorize?' + urllib.parse.urlencode(asked))
		self.assertEqual(status, 200)
		self.assertIn(b'Connect Test to parity-deriva', page)
		wrong = self.http('/oauth/authorize', dict(asked, secret='nope', decision='allow'),
						  kind='application/x-www-form-urlencoded')
		self.assertEqual(wrong[0], 403)
		status, _, headers = self.http('/oauth/authorize', dict(asked, secret=secret, decision='allow'),
									   kind='application/x-www-form-urlencoded')
		self.assertEqual(status, 302)
		back = urllib.parse.parse_qs(urllib.parse.urlsplit(headers['Location']).query)
		self.assertEqual(back['state'], ['xyz'])
		trade = {'grant_type': 'authorization_code', 'code': back['code'][0],
				 'client_id': client['client_id'], 'code_verifier': verifier,
				 'redirect_uri': 'https://claude.ai/api/mcp/auth_callback'}
		self.assertEqual(self.http('/oauth/token', dict(trade, code_verifier='bad'),
								   kind='application/x-www-form-urlencoded')[0], 400)
		# the code went with the failed try: a code is good once
		self.assertEqual(self.http('/oauth/token', trade,
								   kind='application/x-www-form-urlencoded')[0], 400)
		return secret, client, asked, verifier

	def test_an_assistant_connects_writes_a_strategy_and_backtests_it(self):
		secret, client, asked, verifier = self.connect()
		# a second consent, traded properly this time
		status, _, headers = self.http('/oauth/authorize', dict(asked, secret=secret, decision='allow'),
									   kind='application/x-www-form-urlencoded')
		code = urllib.parse.parse_qs(urllib.parse.urlsplit(headers['Location']).query)['code'][0]
		tokens = json.loads(self.http('/oauth/token', {
			'grant_type': 'authorization_code', 'code': code, 'client_id': client['client_id'],
			'code_verifier': verifier}, kind='application/x-www-form-urlencoded')[1])
		refreshed = json.loads(self.http('/oauth/token', {
			'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'],
			'client_id': client['client_id']}, kind='application/x-www-form-urlencoded')[1])
		self.bearer = refreshed['access_token']

		self.assertEqual(self.rpc('initialize', {'protocolVersion': '2025-06-18'})['protocolVersion'],
						 '2025-06-18')
		names = [t['name'] for t in self.rpc('tools/list')['tools']]
		# Was: five tools. Now: list_helpers and request_feature too, which
		# the rules in the instructions send an assistant to, and propose_public
		# and get_news first, the one the instructions send it to before the rest;
		# then the two the data scripts push with
		self.assertEqual(names, ['get_news', 'list_strategies', 'get_source', 'list_data',
								 'submit_strategy', 'list_helpers', 'request_feature',
								 'run_backtest', 'propose_public', 'push_calendar', 'push_candles'])
		data, _ = self.tool('list_data')
		self.assertEqual(data['instruments'][0]['instrument'], 'EUR_USD')
		source, failed = self.tool('get_source', name='parity_deriva.strategy.H4')
		self.assertFalse(failed)
		self.assertIn('class H4', source['source'])
		self.assertTrue(self.tool('get_source', name='parity_deriva.etc.settings')[1])

		saved, failed = self.tool('submit_strategy', name='EVERY-THIRD', source=GOOD)
		self.assertFalse(failed, saved)
		self.assertIn('every', [p['name'] for p in saved['parameters']])
		# its first version, stamped with the server it was written on
		self.assertEqual((saved['name'], saved['version'], saved['server']),
						 ('EVERY-THIRD 1', 1, mcp.serverVersion()))
		self.assertIn("PARITY_DERIVA = {'name': 'EVERY-THIRD', 'version': 1",
					  self.tool('get_source', name='EVERY-THIRD 1')[0]['source'])
		# who wrote it: the name the OAuth client registered with
		self.assertEqual(saved['client'], 'Test')
		self.assertNotIn('EVERY-THIRD', ledger.STRATEGIES)  # a draft is never imported here
		listed, _ = self.tool('list_strategies')
		self.assertIn(('EVERY-THIRD 1', 'draft'),
					  [(s['name'], s['state']) for s in listed['strategies']])

		# a bare name is its newest version
		run, failed = self.tool('run_backtest', strategy='EVERY-THIRD', instrument='EUR_USD',
								granularity='H1', parameters={'every': 4},
								options={'risk': 1}, **{'from': '2017-02-01', 'to': '2017-02-09'})
		self.assertFalse(failed, run)
		self.assertEqual(run['strategy'], 'EVERY-THIRD 1')
		self.assertGreater(run['summary']['trades'], 0)
		self.assertIn('every=4', run['link'])
		# saved: the page reopens it, though the service does not import it
		status, raw, _ = self.http('/api/backtest', dict(
			urllib.parse.parse_qsl(urllib.parse.urlsplit(run['link']).query), cachedOnly=1),
			{'X-Parity-Deriva': '1'})
		self.assertEqual(json.loads(raw)['runId'], run['run'])
		self.assertTrue(self.tool('run_backtest', strategy='EVERY-THIRD', instrument='EUR_USD',
								  granularity='H1', parameters={'nope': 1},
								  **{'from': '2017-02-01', 'to': '2017-02-09'})[1])

		# the code downloaded from the settings page comes back as a draft
		self.assertEqual(self.http('/api/mcp/import', {'name': 'COPY', 'source': GOOD})[0], 403)
		# under another name its class is another strategy's: refused until renamed
		status, raw, _ = self.http('/api/mcp/import', {'name': 'EVERY-THIRD-COPY', 'source': GOOD},
								   {'X-Parity-Deriva': '1'})
		self.assertEqual(status, 400, raw)
		self.assertIn('class EveryThird is already the class of EVERY-THIRD 1', raw.decode())
		status, raw, _ = self.http('/api/mcp/import', {'name': 'EVERY-THIRD-COPY', 'source': GOOD.replace(
			'EveryThird', 'EveryThirdCopy')}, {'X-Parity-Deriva': '1'})
		self.assertEqual(status, 200, raw)
		# a built-in strategy's class, the same
		self.assertIn('class AG02 is already the class of AG02', self.tool(
			'submit_strategy', name='NOT-AG02', source=GOOD.replace('EveryThird', 'AG02'))[0])
		self.assertIn(('EVERY-THIRD-COPY', 'draft', 'imported from a file'),
					  [(s['name'].split(' ')[0], s['state'], s.get('client'))
					   for s in json.loads(raw)['strategies']])
		status, raw, _ = self.http('/api/mcp/import', {'name': 'bad name', 'source': GOOD},
								   {'X-Parity-Deriva': '1'})
		self.assertEqual(status, 400, raw)

		# enabled from the settings page: a strategy like the others from then on
		status, raw, _ = self.http('/api/mcp/strategy', {'name': 'EVERY-THIRD 1', 'action': 'enable'},
								   {'X-Parity-Deriva': '1'})
		self.assertEqual(status, 200, raw)
		klass = ledger.load_strategy('EVERY-THIRD 1')
		self.assertEqual((klass.TAG, klass.VERSION, klass.SERVER_VERSION),
						 ('EVERY-THIRD 1', 1, mcp.serverVersion()))
		# Was: a name enabled was refused until disabled. Now: the next version,
		# a draft, and the one enabled stays as the user read it
		# the same code again is no new version
		again, failed = self.tool('submit_strategy', name='EVERY-THIRD', source=GOOD)
		self.assertTrue(failed)
		self.assertIn('the same code as EVERY-THIRD 1', again)
		again, failed = self.tool('submit_strategy', name='EVERY-THIRD', source=GOOD + '# v2\n')
		self.assertFalse(failed, again)
		self.assertEqual(again['name'], 'EVERY-THIRD 2')
		self.assertIn("'version': 2", self.tool('get_source', name='EVERY-THIRD')[0]['source'])
		self.assertIn('EVERY-THIRD 1', ledger.STRATEGIES)

		# proposed for the public repository by the assistant; the pull request
		# is the user's, from the settings page, on an enabled version only
		self.assertTrue(self.tool('propose_public', strategy='EVERY-THIRD 1', note='')[1])
		proposed, failed = self.tool('propose_public', strategy='EVERY-THIRD 1',
									 note='every third bullish bar; 2017, 40 trades')
		self.assertFalse(failed, proposed)
		pull = lambda name: self.http('/api/mcp/pull', {'name': name}, {'X-Parity-Deriva': '1'})
		status, raw, _ = pull('EVERY-THIRD 1')
		self.assertEqual(status, 400)
		self.assertIn(b'no public repository', raw)
		self.settings.PUBLIC_REPO, self.settings.GITHUB_TOKEN = 'pub/strats', 'secret'
		self.assertEqual(pull('EVERY-THIRD 2')[0], 400)  # a draft: nobody read it yet
		calls = []

		def github(token, method, path, body=None):
			calls.append((method, path, body))
			return {('GET', '/repos/pub/strats'): {'default_branch': 'main', 'permissions': {'push': False}},
					('POST', '/repos/pub/strats/forks'): {'full_name': 'me/strats'},
					('GET', '/repos/pub/strats/git/ref/heads/main'): {'object': {'sha': 'abc'}},
					('POST', '/repos/pub/strats/pulls'): {'html_url': 'https://github.com/pub/strats/pull/7',
														  'number': 7}}.get((method, path), {})
		with unittest.mock.patch.object(mcp, 'github', github):
			status, raw, _ = pull('EVERY-THIRD 1')
		self.assertEqual(status, 200, raw)
		row = [s for s in json.loads(raw)['strategies'] if s['name'] == 'EVERY-THIRD 1'][0]
		self.assertEqual(row['pull']['number'], 7)
		files = dict((path, base64.b64decode(body['content']).decode())
					 for method, path, body in calls if method == 'PUT')
		self.assertIn("'version': 1", files['/repos/me/strats/contents/strategies/EVERY-THIRD/EVERY-THIRD%401.py'])
		card = json.loads(files['/repos/me/strats/contents/strategies/EVERY-THIRD/EVERY-THIRD%401.json'])
		self.assertEqual((card['code'], card['server']), ('EVERY-THIRD 1', mcp.serverVersion()))
		opened = [body for method, path, body in calls if path == '/repos/pub/strats/pulls'][0]
		self.assertTrue(opened['head'].startswith('me:strategy/EVERY-THIRD-v1-'), opened)
		self.assertEqual(pull('EVERY-THIRD 1')[0], 400)  # one pull request a version

		# what runs it, said on the page before it is turned off: a sweep here
		uses = lambda: json.loads(self.http('/api/mcp/uses?name=EVERY-THIRD%201')[1])['uses']
		self.assertEqual(uses(), [])
		self.service._sweep = {'running': True, 'fields': {'strategy': 'EVERY-THIRD 1'}, 'grid': {},
							   'total': 3, 'done': [{}]}
		self.assertEqual(uses(), ["a sweep with it, 1 of 3 runs done: the runs left will fail"])
		self.service._sweep = None
		live = self.service.live
		with unittest.mock.patch.multiple(
				live, ids=lambda: ['S1'], alive=lambda meta: True,
				meta=lambda s: {'fields': {'strategy': 'EVERY-THIRD 1'}, 'provider': 'oanda',
								'accountName': 'Demo'},
				summary=lambda s: {'open': [{}, {}]}):
			self.assertEqual(uses(), ["live session S1 on oanda Demo: 2 open trades - it keeps "
									  "trading it until stopped on the live page"])
		# deleted while enabled: disabled first, then gone
		status, raw, _ = self.http('/api/mcp/strategy', {'name': 'EVERY-THIRD 1', 'action': 'delete'},
								   {'X-Parity-Deriva': '1'})
		self.assertEqual(status, 200, raw)
		self.assertNotIn('EVERY-THIRD 1', ledger.STRATEGIES)
		self.assertNotIn('EVERY-THIRD 1', [s['name'] for s in json.loads(raw)['strategies']])

		# a new secret throws every token away
		self.service.oauth.newSecret()
		self.assertEqual(self.http('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
								   {'Authorization': 'Bearer %s' % self.bearer})[0], 401)

	def test_an_assistant_is_sent_to_the_helpers_and_asks_for_what_is_missing(self):
		self.bearer = self.service.oauth.newSecret()
		rules = self.rpc('initialize', {'protocolVersion': '2025-06-18'})['instructions']
		self.assertIn('list_helpers', rules)
		self.assertIn('request_feature', rules)
		# what exists, read off the code: a helper added is listed at once
		found, failed = self.tool('list_helpers')
		self.assertFalse(failed, found)
		lines = dict((m['module'], m['helpers']) for m in found['modules'])
		self.assertTrue(any(l.startswith('bullish(candle)') for l in lines['parity_deriva.strategy.H4']))
		self.assertTrue(any('Series.ema(self, period) - The EMA' in l
							for l in lines['parity_deriva.lib.streaming']))
		self.assertEqual(found['requested'], [])
		# what does not, asked for rather than written
		asked, failed = self.tool('request_feature', title='Keltner channel',
								  description='EMA(20) +- 2 ATR(10), on the streaming Series',
								  strategy='MY-KELTNER')
		self.assertFalse(failed, asked)
		self.assertEqual(asked['id'], 'R1')
		again, failed = self.tool('request_feature', title='keltner  CHANNEL', description='again')
		self.assertTrue(failed)
		self.assertIn('R1', again)
		self.assertTrue(self.tool('request_feature', title='', description='x')[1])
		self.assertEqual([r['id'] for r in self.tool('list_helpers')[0]['requested']], ['R1'])
		# the settings page lists it, and answering it takes it off
		status, raw, _ = self.http('/api/mcp')
		self.assertEqual([r['title'] for r in json.loads(raw)['requests']], ['Keltner channel'])
		status, raw, _ = self.http('/api/mcp/request', {'id': 'R1'}, {'X-Parity-Deriva': '1'})
		self.assertEqual(status, 200, raw)
		self.assertEqual(json.loads(raw)['requests'], [])
		self.assertEqual(self.http('/api/mcp/request', {'id': 'R1'}, {'X-Parity-Deriva': '1'})[0], 400)

	def test_the_user_posts_news_and_an_assistant_reads_what_is_left_to_update(self):
		self.bearer = self.service.oauth.newSecret()
		self.assertEqual(self.tool('get_news')[0]['news'], [])
		self.assertFalse(self.tool('submit_strategy', name='EVERY-THIRD', source=GOOD)[1])
		post = lambda body: self.http('/api/mcp/news', body, {'X-Parity-Deriva': '1'})
		self.assertEqual(post({'title': 'x', 'text': 'y', 'strategies': 'not-a-name'})[0], 400)
		self.assertEqual(self.http('/api/mcp/news', {'title': 'x', 'text': 'y'})[0], 403)
		status, raw, _ = post({'title': 'New helpers', 'text': 'Rewrite it on Weekly.',
							   'strategies': 'EVERY-THIRD 1, OTHER-ONE'})
		self.assertEqual(status, 200, raw)
		self.assertEqual(json.loads(raw)['news'][0]['id'], 'N1')
		# told on connecting, and read in full with the tool
		self.assertIn('N1: New helpers (new)', self.rpc('initialize', {})['instructions'])
		# every answer carries it on top until get_news is read - a server
		# cannot speak first over MCP here, and a client reads the
		# instructions only when it connects
		answer = self.rpc('tools/call', {'name': 'list_data', 'arguments': {}})['content']
		self.assertEqual(len(answer), 2)
		self.assertIn('call get_news before going on: N1: New helpers', answer[1]['text'])
		told = self.tool('get_news')[0]['news'][0]
		self.assertEqual(len(self.rpc('tools/call', {'name': 'list_data', 'arguments': {}})['content']), 1)
		self.assertNotIn('(new)', self.rpc('initialize', {})['instructions'])
		# a client back after the server was updated is told it had another version
		client = [c for c in mcp.clients(self.service) if c.startswith('token: ')][0]
		mcp.remember(self.service, client, version='0.9.0+old')
		self.assertIn('you knew 0.9.0+old, it is now %s' % mcp.serverVersion(),
					  self.rpc('initialize', {})['instructions'])
		self.assertNotIn('you knew', self.rpc('initialize', {})['instructions'])
		self.assertEqual(told['strategies'], [
			{'name': 'EVERY-THIRD', 'latest': 'EVERY-THIRD 1', 'updated': False},
			{'name': 'OTHER-ONE', 'latest': None, 'updated': False}])
		# a new version after the news is the strategy done
		self.assertFalse(self.tool('submit_strategy', name='EVERY-THIRD', source=GOOD + '# on Weekly\n')[1])
		self.assertEqual(self.tool('get_news')[0]['news'][0]['strategies'][0],
						 {'name': 'EVERY-THIRD', 'latest': 'EVERY-THIRD 2', 'updated': True})
		# done with: removed on the page, and no longer in the instructions
		status, raw, _ = self.http('/api/mcp/news/drop', {'id': 'N1'}, {'X-Parity-Deriva': '1'})
		self.assertEqual((status, json.loads(raw)['news']), (200, []))
		self.assertNotIn('News from the user', self.rpc('initialize', {})['instructions'])

	def test_the_docs_page_s_example_is_a_strategy_the_server_takes(self):
		"""The example on /docs, as it is written there: kept honest by this."""
		page = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
							'web', 'static', 'docs.html')
		with open(page) as handle:
			text = handle.read()
		example = html.unescape(text.split('<pre id="docs-example">')[1].split('</pre>')[0])
		self.assertEqual(uploaded.check(example), [])
		self.bearer = self.service.oauth.newSecret()
		saved, failed = self.tool('submit_strategy', name='EMA-CROSS-DEMO', source=example)
		self.assertFalse(failed, saved)
		self.assertLessEqual({'fast', 'slow', 'atrStop', 'reward'},
							 set(p['name'] for p in saved['parameters']))
		run, failed = self.tool('run_backtest', strategy='EMA-CROSS-DEMO', instrument='EUR_USD',
								granularity='H1', parameters={'fast': 5, 'slow': 12},
								**{'from': '2017-02-01', 'to': '2017-02-09'})
		self.assertFalse(failed, run)
		# and the page's reference is read off the helpers' source
		status, raw, _ = self.http('/api/mcp/docs')
		docs = json.loads(raw)
		self.assertIn('Rules:', docs['guide'])
		streaming = [m for m in docs['reference'] if m['module'] == 'parity_deriva.lib.streaming'][0]
		series = [i for i in streaming['items'] if i['name'] == 'Series'][0]
		self.assertIn('channel', [m['name'] for m in series['methods']])
		self.assertIn('parity_deriva.lib.streaming', docs['allowed'])

	def test_a_loop_and_a_memory_eater_are_stopped_in_the_sandbox(self):
		self.bearer = self.service.oauth.newSecret()
		self.settings.MCP_SANDBOX_SECONDS = 15
		for name, body, word in (('FOREVER', 'while True:\n\t\t\tpass', 'stopped after'),
								 ('GREEDY', 'x = [0] * 10 ** 9', 'memory')):
			# a class of its own each: one name for two strategies is refused
			source = GOOD.replace("atr = state.atr()", body).replace('EveryThird', name.title())
			saved, failed = self.tool('submit_strategy', name=name, source=source)
			self.assertFalse(failed, saved)
			# the secret names no client: the program's User-Agent does
			self.assertTrue(saved['client'].startswith('token: Python-urllib'), saved['client'])
			answer, failed = self.tool('run_backtest', strategy=name, instrument='EUR_USD',
									   granularity='H1', **{'from': '2017-02-01', 'to': '2017-02-09'})
			self.assertTrue(failed)
			self.assertIn(word, answer)
		# and the service is still here
		self.assertEqual(self.rpc('ping'), {})

	def test_a_data_script_pushes_the_calendar_and_candles(self):
		self.bearer = self.service.oauth.newSecret()
		event = {'id': 7, 'datetime_utc': '2025-09-11T12:15:00+00:00', 'time': '2:15pm',
				 'currency': 'EUR', 'impact': 'high', 'event': 'Main Refinancing Rate',
				 'actual': '', 'forecast': '2.15%', 'previous': '2.15%', 'revision': '',
				 'detail': {'Usual Effect': "'Actual' greater than 'Forecast' is good for currency;"}}
		told, failed = self.tool('push_calendar', events=[event])
		self.assertFalse(failed, told)
		self.assertEqual((told['received'], told['added']), (1, 1))
		# the outcome, minutes later: the same event, not a second one
		told, failed = self.tool('push_calendar', events=[dict(event, actual='2.40%')])
		self.assertEqual((told['added'], told['events']), (0, 1), told)
		held = calendar.load(calendar.path(self.settings))
		self.assertEqual(held.iloc[0]['actual'], '2.40%')
		self.assertTrue(self.tool('push_calendar', events=[dict(event, id='')])[1])

		bar = lambda ms, price: [ms, price, price + 0.001, price - 0.001, price, 5]
		first = [bar(1757592000000 + i * 300000, 1.17) for i in range(3)]
		told, failed = self.tool('push_candles', instrument='EUR/USD', granularity='m5',
								 ask=[b[:1] + [v + 0.0001 for v in b[1:5]] + b[5:] for b in first],
								 bid=first)
		self.assertFalse(failed, told)
		self.assertEqual((told['instrument'], told['granularity'], told['added']), ('EUR_USD', 'M5', 3))
		# again, other prices and one bar more: the three held stay as they were
		again = [bar(1757592000000 + i * 300000, 1.30) for i in range(4)]
		told, failed = self.tool('push_candles', instrument='EUR_USD', granularity='M5',
								 ask=again, bid=again)
		self.assertEqual((told['added'], told['kept']), (1, 3), told)
		stored = pd.read_hdf(os.path.join(self.settings.DATA_DIR, 'EUR_USD.hd5'), 'M5')
		self.assertEqual(list(stored['bid_c'].round(2)), [1.17, 1.17, 1.17, 1.30])
		# a name that is not a store, and an assistant connected through OAuth
		self.assertIn('instrument', self.tool('push_candles', instrument='../x', granularity='M5',
											  ask=first, bid=first)[0])
		with self.assertRaises(mcp.ToolError):
			mcp.call(self.service, 'push_calendar', {'events': [event]}, self.base, 'claude.ai')


class NoRedirect(urllib.request.HTTPRedirectHandler):
	def redirect_request(self, *args, **kwargs):
		return None


if __name__ == '__main__':
	unittest.main()
