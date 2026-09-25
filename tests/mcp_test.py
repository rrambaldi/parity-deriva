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
import json
import os
import secrets
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from parity_deriva.backtest import ledger
from parity_deriva.strategy import uploaded
from parity_deriva.tests.web_test import StoreCase
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
		self.addCleanup(lambda: [ledger.STRATEGIES.pop(n, None) for n in ('EVERY-THIRD',)])
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
		self.assertEqual(names, ['list_strategies', 'get_source', 'list_data',
								 'submit_strategy', 'run_backtest'])
		data, _ = self.tool('list_data')
		self.assertEqual(data['instruments'][0]['instrument'], 'EUR_USD')
		source, failed = self.tool('get_source', name='parity_deriva.strategy.H4')
		self.assertFalse(failed)
		self.assertIn('class H4', source['source'])
		self.assertTrue(self.tool('get_source', name='parity_deriva.etc.settings')[1])

		saved, failed = self.tool('submit_strategy', name='EVERY-THIRD', source=GOOD)
		self.assertFalse(failed, saved)
		self.assertIn('every', [p['name'] for p in saved['parameters']])
		self.assertNotIn('EVERY-THIRD', ledger.STRATEGIES)  # a draft is never imported here
		listed, _ = self.tool('list_strategies')
		self.assertIn(('EVERY-THIRD', 'draft'),
					  [(s['name'], s['state']) for s in listed['strategies']])

		run, failed = self.tool('run_backtest', strategy='EVERY-THIRD', instrument='EUR_USD',
								granularity='H1', parameters={'every': 4},
								options={'risk': 1}, **{'from': '2017-02-01', 'to': '2017-02-09'})
		self.assertFalse(failed, run)
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

		# enabled from the settings page: a strategy like the others from then on
		status, raw, _ = self.http('/api/mcp/strategy', {'name': 'EVERY-THIRD', 'action': 'enable'},
								   {'X-Parity-Deriva': '1'})
		self.assertEqual(status, 200, raw)
		self.assertIn('EVERY-THIRD', ledger.STRATEGIES)
		refused, failed = self.tool('submit_strategy', name='EVERY-THIRD', source=GOOD)
		self.assertTrue(failed)
		self.assertIn('enabled', refused)

		# a new secret throws every token away
		self.service.oauth.newSecret()
		self.assertEqual(self.http('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
								   {'Authorization': 'Bearer %s' % self.bearer})[0], 401)

	def test_a_loop_and_a_memory_eater_are_stopped_in_the_sandbox(self):
		self.bearer = self.service.oauth.newSecret()
		self.settings.MCP_SANDBOX_SECONDS = 15
		for name, body, word in (('FOREVER', 'while True:\n\t\t\tpass', 'stopped after'),
								 ('GREEDY', 'x = [0] * 10 ** 9', 'memory')):
			source = GOOD.replace("atr = state.atr()", body).replace('EveryThird', 'Bad')
			saved, failed = self.tool('submit_strategy', name=name, source=source)
			self.assertFalse(failed, saved)
			answer, failed = self.tool('run_backtest', strategy=name, instrument='EUR_USD',
									   granularity='H1', **{'from': '2017-02-01', 'to': '2017-02-09'})
			self.assertTrue(failed)
			self.assertIn(word, answer)
		# and the service is still here
		self.assertEqual(self.rpc('ping'), {})


class NoRedirect(urllib.request.HTTPRedirectHandler):
	def redirect_request(self, *args, **kwargs):
		return None


if __name__ == '__main__':
	unittest.main()
