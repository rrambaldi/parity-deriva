"""
Tests for web/access.py: who may open the pages in each mode, the sign-in
with a provider (its answers made up here), the first start's setup, the
certificates (openssl for real) and the Caddyfile.

A loopback server the tests start themselves; no provider is called.
"""

import base64
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
from unittest import mock

from parity_deriva.data import sources
from parity_deriva.scripts import sync
from parity_deriva.web import access, servers
from parity_deriva.web import service as service_module

#: what the made-up Archive answers, by tool
ARCHIVE = {
    'market_status': {'instruments': [{'instrument': 'EUR_USD', 'granularities': []}],
                      'calendar': {'events': 12}},
    'pull_spread': {'instruments': {'EUR_USD': {'etoro': 0.0001}}},
    'pull_code': {'strategies': [{'code': 'MY-EMA 3', 'source': 'class MyEma(object):\n    pass\n', 'meta': {}}],
                  'indicators': []}}


def archive(upstream, name, args, timeout=180):
    if upstream['token'] == 'mirror-token' and name == 'pull_code':
        raise sources.SourceError("a mirror token may not call pull_code")
    return json.loads(json.dumps(ARCHIVE[name]))


class Answer(object):
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class Case(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='parity-deriva-access-')
        self.addCleanup(shutil.rmtree, self.home, True)
        self.setup = types.SimpleNamespace(DATA_DIR=self.home)
        access._setup['done'] = False
        # the process must not go at the end of a setup
        timer = mock.patch.object(access.threading, 'Timer')
        self.timer = timer.start()
        self.addCleanup(timer.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        # no Archive is called: this one answers
        for patch in (mock.patch.object(sources, 'rpc', archive), mock.patch.object(sync, 'rpc', archive)):
            patch.start()
            self.addCleanup(patch.stop)
        for key in ('PARITY_DERIVA_WIZARD', 'PARITY_DERIVA_DOMAIN', 'X_PARITY'):
            os.environ.pop(key, None)

    def serve(self):
        self.server = service_module.serve(host='127.0.0.1', port=0, setup=self.setup)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        return self.server.RequestHandlerClass.service

    def call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=20)
        sent = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            sent.update({'X-Parity-Deriva': '1', 'Content-Type': 'application/json'})
        connection.request(method, path, body=data, headers=sent)
        answer = connection.getresponse()
        raw = answer.read()
        connection.close()
        return answer.status, dict((k.lower(), v) for k, v in answer.getheaders()), raw

    def auth(self, kept):
        servers.keep('auth', kept, self.setup)


class ListTest(unittest.TestCase):

    def test_an_address_a_domain_and_a_github_login(self):
        allow = ['Mario@Example.it', '@digithera.it', 'github:rrambaldi']
        self.assertEqual(access.allowed(['mario@example.it'], allow), 'mario@example.it')
        self.assertEqual(access.allowed(['x@digithera.it'], allow), 'x@digithera.it')
        self.assertEqual(access.allowed(['github:RRambaldi', 'a@b.c'], allow), 'github:rrambaldi')
        self.assertIsNone(access.allowed(['x@evil-digithera.it'], allow))
        self.assertIsNone(access.allowed(['x@digithera.it.evil.com'], allow))
        self.assertIsNone(access.allowed(['github:someone'], allow))
        self.assertIsNone(access.allowed(['a@b.c'], []))

    def test_an_exact_entry_before_a_domain_and_the_certificates_rule(self):
        allow = [{'who': '@b.it', 'can': 'read'}, {'who': 'boss@b.it', 'can': 'write'}]
        self.assertEqual(access.match(['boss@b.it'], allow), ('boss@b.it', 'write'))
        self.assertEqual(access.match(['x@b.it'], allow), ('x@b.it', 'read'))
        # no certificate named: every one of the authority writes
        self.assertEqual(access.certCan('Anyone', allow), 'write')
        allow.append({'who': 'cert:Anna', 'can': 'read'})
        self.assertEqual(access.certCan('anna', allow), 'read')
        self.assertIsNone(access.certCan('Anyone', allow))

    def test_a_cookie_signed_here_unexpired_and_untouched(self):
        key = b'k' * 32
        value = access.sign(key, {'ids': ['a@b.c'], 'until': time.time() + 60})
        self.assertEqual(access.unsign(key, value)['ids'], ['a@b.c'])
        self.assertIsNone(access.unsign(b'other' * 8, value))
        body, _, mac = value.rpartition('.')
        forged = base64.urlsafe_b64encode(json.dumps({'ids': ['x@y.z'], 'until': time.time() + 60})
                                          .encode()).rstrip(b'=').decode()
        self.assertIsNone(access.unsign(key, forged + '.' + mac))
        self.assertIsNone(access.unsign(key, access.sign(key, {'until': time.time() - 1})))
        self.assertIsNone(access.unsign(key, 'garbage'))
        self.assertIsNone(access.unsign(key, value[:-3] + 'àèì'))

    def test_the_list_is_checked_on_the_way_in(self):
        self.assertEqual(access.cleanAllow([' A@B.it ', {'who': '@x.it', 'can': 'read'}, 'github:me', '',
                                            {'who': 'CERT:Anna  <Bianchi>'}]),
                         [{'who': '@x.it', 'can': 'read'}, {'who': 'a@b.it', 'can': 'write'},
                          {'who': 'cert:Anna Bianchi', 'can': 'write'}, {'who': 'github:me', 'can': 'write'}])
        self.assertEqual(access.cleanAllow([], empty=True), [])
        for bad in ([], ['nobody'], ['github:'], 'a@b.it', ['cert:'], [{'who': 'a@b.it', 'can': 'admin'}]):
            with self.assertRaises(access.AccessError):
                access.cleanAllow(bad)


class EnvTest(Case):

    def test_keys_set_replaced_and_dropped_the_others_kept(self):
        access.envKeep({'A': "it's $ecret!", 'B': '1'}, self.setup)
        with open(access.envPath(self.setup), 'a') as handle:
            handle.write('# a line of somebody\nexport OTHER=x\n')
        access.envKeep({'B': None, 'A': 'two'}, self.setup)
        self.assertEqual(access.envRead(self.setup), {'A': 'two', 'OTHER': 'x'})
        self.assertEqual(os.stat(access.envPath(self.setup)).st_mode & 0o777, 0o600)
        access.envKeep({'A': "it's $ecret!"}, self.setup)
        # what a shell reads, as the Docker image sources it
        seen = subprocess.run(['sh', '-c', '. "$0"; printf %s "$A"', access.envPath(self.setup)],
                              capture_output=True, text=True).stdout
        self.assertEqual(seen, "it's $ecret!")


class GateTest(Case):

    def test_no_auth_and_no_wizard_is_how_it_always_was(self):
        self.serve()
        self.assertEqual(self.call('GET', '/api/favourites')[0], 200)
        self.assertEqual(json.loads(self.call('GET', '/api/access')[2])['mode'], None)

    def test_none_lets_this_pc_in_and_nothing_through_a_proxy(self):
        self.auth({'mode': 'none'})
        self.serve()
        self.assertEqual(self.call('GET', '/api/favourites')[0], 200)
        self.assertEqual(self.call('GET', '/api/favourites', headers={'X-Forwarded-For': '1.2.3.4'})[0], 403)
        # the assistants' door is not this module's
        self.assertEqual(self.call('POST', '/mcp', headers={'X-Forwarded-For': '1.2.3.4'})[0], 401)

    def test_cert_wants_the_proxys_subject(self):
        self.auth({'mode': 'cert', 'ca': 'own'})
        self.serve()
        self.assertEqual(self.call('GET', '/api/favourites')[0], 403)
        status, headers, _ = self.call('GET', '/', headers={'X-Client-Subject': ''})
        self.assertEqual((status, headers['location']), (302, 'http://127.0.0.1:%d/login?error=certificate' % self.port))
        status, _, raw = self.call('GET', '/api/access', headers={'X-Client-Subject': 'CN=Mario Rossi,O=parity-deriva'})
        self.assertEqual(json.loads(raw)['user'], 'Mario Rossi')
        self.assertEqual(self.call('GET', '/api/favourites', headers={'X-Client-Subject': 'CN=Mario Rossi'})[0], 200)
        # the pages' files and the language are public: the login page needs them
        self.assertEqual(self.call('GET', '/static/app.css')[0], 200)
        self.assertEqual(self.call('GET', '/api/i18n')[0], 200)
        self.assertEqual(self.call('POST', '/api/i18n/xx', {'texts': {}})[0], 403)

    def test_oauth_sends_a_page_to_the_login_and_an_api_call_away(self):
        self.auth({'mode': 'oauth', 'allow': ['a@b.it'], 'providers': {'google': {'id': 'gid'}}})
        access.envKeep({access.SECRET % 'GOOGLE': 's'}, self.setup)
        service = self.serve()
        status, headers, _ = self.call('GET', '/run?id=3')
        self.assertEqual(status, 302)
        self.assertEqual(headers['location'], 'http://127.0.0.1:%d/login?next=%%2Frun%%3Fid%%3D3' % self.port)
        self.assertEqual(self.call('GET', '/api/favourites')[0], 401)
        self.assertEqual(self.call('POST', '/api/favourites', {'source': {}})[0], 401)
        self.assertEqual(self.call('GET', '/login')[0], 200)
        self.assertEqual(json.loads(self.call('GET', '/api/access')[2])['providers'], ['google'])
        key = access.cookieKey(self.setup)
        good = access.sign(key, {'ids': ['a@b.it'], 'until': time.time() + 60})
        self.assertEqual(self.call('GET', '/api/favourites', headers={'Cookie': 'pd_session=' + good})[0], 200)
        # taken off the list: out at the next click, whatever the cookie says
        other = access.sign(key, {'ids': ['c@d.it'], 'until': time.time() + 60})
        self.assertEqual(self.call('GET', '/api/favourites', headers={'Cookie': 'pd_session=' + other})[0], 401)
        # the collector on forexfactory carries its token, not a cookie
        status = self.call('POST', '/api/calendar?token=' + service.token, {'events': []})[0]
        self.assertNotIn(status, (401, 403))
        self.assertEqual(self.call('POST', '/api/calendar?token=wrong', {'events': []})[0], 401)
        status, headers, _ = self.call('GET', '/logout')
        self.assertIn('Max-Age=0', headers['set-cookie'])


class SignInTest(Case):

    def setUp(self):
        Case.setUp(self)
        self.auth({'mode': 'oauth', 'allow': ['@b.it', 'github:me'],
                   'providers': {'google': {'id': 'gid'}, 'github': {'id': 'hid'}}})
        access.envKeep({access.SECRET % 'GOOGLE': 'gs', access.SECRET % 'GITHUB': 'hs'}, self.setup)
        self.serve()

    def go(self, provider, token, who):
        status, headers, _ = self.call('GET', '/auth/start/%s?next=%%2Fmix' % provider)
        self.assertEqual(status, 302)
        sent = urllib.parse.parse_qs(urllib.parse.urlsplit(headers['location']).query)
        self.assertEqual(sent['redirect_uri'], ['http://127.0.0.1:%d/auth/callback/%s' % (self.port, provider)])
        with mock.patch.object(access.requests, 'post', return_value=Answer(token)) as post, \
                mock.patch.object(access.requests, 'get', side_effect=[Answer(w) for w in who]):
            answer = self.call('GET', '/auth/callback/%s?code=c&state=%s' % (provider, sent['state'][0]))
        return answer, sent, post

    def test_google_with_a_verified_address_on_the_list(self):
        (status, headers, _), sent, post = self.go('google', {'access_token': 't'},
                                                   [{'email': 'X@b.it', 'email_verified': True}])
        self.assertEqual(sent['code_challenge_method'], ['S256'])
        self.assertEqual(post.call_args[1]['data']['client_secret'], 'gs')
        self.assertEqual(status, 302)
        self.assertEqual(headers['location'], 'http://127.0.0.1:%d/mix' % self.port)
        cookie = headers['set-cookie'].split(';')[0]
        self.assertEqual(json.loads(self.call('GET', '/api/access', headers={'Cookie': cookie})[2])['user'], 'x@b.it')

    def test_an_unverified_address_and_one_off_the_list_stay_out(self):
        (status, headers, _), _, _ = self.go('google', {'access_token': 't'},
                                             [{'email': 'x@b.it', 'email_verified': False}])
        self.assertTrue(headers['location'].endswith('/login?error=not-allowed'))
        (status, headers, _), _, _ = self.go('google', {'access_token': 't'},
                                             [{'email': 'x@c.it', 'email_verified': True}])
        self.assertTrue(headers['location'].endswith('/login?error=not-allowed'))
        self.assertNotIn('set-cookie', headers)

    def test_github_by_its_login(self):
        (status, headers, _), _, _ = self.go('github', {'access_token': 't'},
                                             [{'login': 'me'}, [{'email': 'z@q.it', 'verified': False}]])
        self.assertTrue(headers['location'].endswith('/mix'))

    def test_a_state_is_good_once_and_a_provider_error_fails(self):
        status, headers, _ = self.call('GET', '/auth/callback/google?code=c&state=made-up')
        self.assertTrue(headers['location'].endswith('/login?error=failed'))
        (status, headers, _), _, _ = self.go('google', {'error': 'invalid_grant'}, [])
        self.assertTrue(headers['location'].endswith('/login?error=failed'))
        # a next that leaves the server goes home instead
        status, headers, _ = self.call('GET', '/auth/start/google?next=%2F%2Fevil.com')
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(headers['location']).query)['state'][0]
        self.assertEqual(access._pending[state]['next'], '/')

    def test_the_list_that_leaves_out_who_saves_it_is_refused(self):
        cookie = 'pd_session=' + access.sign(access.cookieKey(self.setup), {'ids': ['x@b.it'], 'until': time.time() + 60})
        status, _, raw = self.call('POST', '/api/access/settings', {'allow': ['y@c.it']}, headers={'Cookie': cookie})
        self.assertEqual(status, 400)
        self.assertIn('the list lets in', json.loads(raw)['error'])
        status, _, raw = self.call('POST', '/api/access/settings', {'allow': ['x@b.it', 'y@c.it']},
                                   headers={'Cookie': cookie})
        self.assertEqual(json.loads(raw)['allow'], [{'who': 'x@b.it', 'can': 'write'}, {'who': 'y@c.it', 'can': 'write'}])
        self.assertEqual(json.loads(raw)['signed'], 'x@b.it')
        self.assertEqual(json.loads(raw)['providers']['google'], {'id': 'gid', 'secret': True})
        status, _, raw = self.call('POST', '/api/access/settings',
                                   {'allow': [{'who': 'x@b.it', 'can': 'read'}]}, headers={'Cookie': cookie})
        self.assertIn('only look', json.loads(raw)['error'])


class SetupTest(Case):

    def setUp(self):
        Case.setUp(self)
        os.environ['PARITY_DERIVA_WIZARD'] = '1'
        self.service = self.serve()

    def enter(self):
        status, headers, _ = self.call('POST', '/api/setup/code', {'code': access._setup['code'].lower()})
        self.assertEqual(status, 200)
        return {'Cookie': headers['set-cookie'].split(';')[0]}

    def test_every_page_is_the_setups_and_its_code_opens_it(self):
        status, headers, _ = self.call('GET', '/')
        self.assertEqual((status, headers['location']), (302, 'http://127.0.0.1:%d/setup' % self.port))
        self.assertEqual(self.call('GET', '/api/favourites')[0], 403)
        self.assertEqual(self.call('GET', '/setup')[0], 200)
        self.assertEqual(self.call('GET', '/api/setup/state')[0], 403)
        with mock.patch.object(access.time, 'sleep'):
            self.assertEqual(self.call('POST', '/api/setup/code', {'code': 'AAAA-AAAA'})[0], 403)
            self.assertEqual(self.call('POST', '/api/setup/code', {'code': 'ÀÈÌ'})[0], 403)
        state = json.loads(self.call('GET', '/api/setup/state', headers=self.enter())[2])
        self.assertEqual(state['roles'], ['archive', 'test', 'trade'])
        self.assertIn('pc.json', [p['file'] for p in state['profiles']])
        self.assertTrue(json.loads(self.call('GET', '/api/access')[2])['setup'])

    def test_a_test_pc_with_the_archives_data(self):
        cookie = self.enter()
        found = json.loads(self.call('POST', '/api/setup/archive', {
            'url': 'https://archive.example/parity/mcp', 'token': 'pc-token'}, headers=cookie)[2])
        self.assertEqual(found, {'ok': True, 'instruments': 1, 'events': 12, 'spread': 1,
                                 'strategies': 1, 'indicators': 0})
        answer = {'name': ' PC  at home ', 'auth': {'mode': 'none'}, 'roles': ['test'],
                  'archive': {'url': 'https://archive.example/parity/mcp', 'token': 'pc-token'},
                  'market': {'candles': 'upstream', 'calendar': 'upstream'}}
        status, _, raw = self.call('POST', '/api/setup/finish', answer, headers=cookie)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)['promote'], None)
        # the rest of what the Archive holds, taken before the restart
        self.assertEqual(json.loads(raw)['taken'], [
            'spread set: 1 instruments from upstream',
            'MY-EMA 3: pulled, a draft here - enable it on the settings page', '1 pulled'])
        from parity_deriva.strategy import uploaded
        self.assertEqual(list(uploaded.drafts(self.home)), ['MY-EMA 3'])
        with open(os.path.join(self.home, 'spread.json')) as handle:
            self.assertEqual(json.load(handle), ARCHIVE['pull_spread'])
        self.assertEqual(servers.read(self.setup), {'roles': ['test'], 'auth': {'mode': 'none'}, 'name': 'PC at home'})
        env = access.envRead(self.setup)
        self.assertEqual((env['PARITY_DERIVA_ARCHIVE_URL'], env['PARITY_DERIVA_SYNC_TOKEN']),
                         ('https://archive.example/parity/mcp', 'pc-token'))
        with open(os.path.join(self.home, 'market.json')) as handle:
            kept = json.load(handle)
        self.assertEqual((kept['candles']['source'], kept['upstream']['token']), ('upstream', 'pc-token'))
        self.timer.assert_called_once()
        # on its way out, still the setup's: its environment is the old one
        self.assertTrue(json.loads(self.call('GET', '/api/access')[2])['setup'])
        self.assertEqual(self.call('POST', '/api/setup/finish', answer, headers=cookie)[0], 409)
        # started again: the pages are open to this PC, the setup is gone
        access._setup['done'] = False
        self.assertEqual(self.call('GET', '/api/favourites')[0], 200)
        self.assertEqual(self.call('POST', '/api/setup/finish', answer, headers=cookie)[0], 404)
        profile = json.loads(self.call('GET', '/api/access/profile')[2])
        self.assertEqual(profile['archive'], {'url': 'https://archive.example/parity/mcp'})
        self.assertEqual(profile['name'], 'PC at home')
        self.assertNotIn('pc-token', json.dumps(profile))

    def test_a_real_money_trade_server_behind_certificates(self):
        cookie = self.enter()
        # a mirror token: the Archive's code is not for it
        self.assertEqual(json.loads(self.call('POST', '/api/setup/archive', {
            'url': 'https://archive.example/parity/mcp', 'token': 'mirror-token'}, headers=cookie)[2])['strategies'], None)
        answer = {'auth': {'mode': 'cert'}, 'ca': {'make': True, 'name': 'Mario Rossi'},
                  'roles': ['trade'], 'accounts': 'real',
                  'archive': {'url': 'https://archive.example/parity/mcp', 'token': 'mirror-token'},
                  'market': {'candles': 'upstream', 'calendar': 'upstream'}}
        status, _, raw = self.call('POST', '/api/setup/finish', answer, headers=cookie)
        self.assertIn('proxy in front', json.loads(raw)['error'])
        status, _, raw = self.call('POST', '/api/setup/finish', answer,
                                   headers=dict(cookie, **{'X-Forwarded-For': '1.2.3.4'}))
        self.assertEqual(status, 200, raw)
        done = json.loads(raw)
        # a trade server takes the spread set, and no code: that comes by the Archive's push
        self.assertEqual(done['taken'], ['spread set: 1 instruments from upstream'])
        self.assertEqual(access.envRead(self.setup)['PARITY_DERIVA_ACCOUNTS'], 'real')
        self.assertNotIn('PARITY_DERIVA_SYNC_TOKEN', access.envRead(self.setup))
        self.assertEqual(self.service.oauth.keyOf(done['promote'])['role'], 'promote')
        p12 = os.path.join(self.home, 'first.p12')
        with open(p12, 'wb') as handle:
            handle.write(base64.b64decode(done['p12']))
        seen = subprocess.run(['openssl', 'pkcs12', '-in', p12, '-nokeys', '-passin', 'env:P'],
                              capture_output=True, text=True, env=dict(os.environ, P=done['password']))
        self.assertIn('CN=Mario Rossi', seen.stdout.replace(' = ', '='))
        self.assertEqual(servers.read(self.setup)['auth'], {'mode': 'cert', 'ca': 'own'})
        access._setup['done'] = False

    def test_paranoid_both_a_certificate_and_an_account(self):
        cookie = self.enter()
        answer = {'auth': {'mode': 'both', 'allow': [{'who': 'a@b.it', 'can': 'write'}, 'c@d.it'],
                           'public_url': 'https://parity.example.com',
                           'providers': {'github': {'id': 'h', 'secret': 'hs'}}},
                  'ca': {'make': True, 'name': 'Claudia'}, 'roles': ['archive'],
                  'market': {'candles': 'manual', 'calendar': 'manual'}}
        status, _, raw = self.call('POST', '/api/setup/finish', answer,
                                   headers=dict(cookie, **{'X-Forwarded-For': '1.2.3.4'}))
        self.assertEqual(status, 200, raw)
        self.assertTrue(json.loads(raw)['p12'])
        kept = servers.read(self.setup)['auth']
        self.assertEqual((kept['mode'], kept['allow'][1]), ('both', {'who': 'c@d.it', 'can': 'write'}))
        self.assertEqual(access.envRead(self.setup)[access.SECRET % 'GITHUB'], 'hs')

    def test_answers_refused_leave_the_server_in_setup(self):
        cookie = self.enter()
        for answer, why in (
                ({'auth': {'mode': 'oauth', 'allow': []}, 'roles': ['archive']}, 'who may enter'),
                ({'auth': {'mode': 'none'}, 'roles': ['trade']}, "archive's MCP address"),
                ({'auth': {'mode': 'none'}, 'roles': ['boss']}, 'the roles'),
                ({'auth': {'mode': 'oauth', 'allow': ['a@b.it'], 'public_url': 'https://x.it',
                           'providers': {'google': {'id': 'g'}}}, 'roles': ['archive']}, 'client secret')):
            status, _, raw = self.call('POST', '/api/setup/finish', answer, headers=cookie)
            self.assertEqual(status, 400, answer)
            self.assertIn(why, json.loads(raw)['error'])
        status, _, raw = self.call('POST', '/api/setup/finish', {'auth': {'mode': 'none'}, 'roles': ['archive']},
                                   headers=dict(cookie, **{'X-Forwarded-For': '1.2.3.4'}))
        self.assertIn('proxy', json.loads(raw)['error'])
        self.assertIsNone(access.config(self.setup))
        self.timer.assert_not_called()


class CertificateTest(Case):

    def test_an_own_authority_issues_and_a_foreign_one_does_not(self):
        access.makeCA(self.setup)
        blob, password = access.issue('Anna Bianchi <x/CN=evil>', self.setup)
        self.assertTrue(blob and password)
        with open(os.path.join(access.caDir(self.setup), 'issued.log')) as handle:
            self.assertIn('Anna Bianchi xCNevil', handle.read())
        with open(os.path.join(access.caDir(self.setup), 'ca.crt')) as handle:
            pem = handle.read()
        access.keepCA(pem, self.setup)
        with self.assertRaises(access.AccessError):
            access.issue('Anna', self.setup)
        with self.assertRaises(access.AccessError):
            access.keepCA('not a certificate', self.setup)

    def test_the_caddyfile_asks_for_a_certificate_in_cert_mode_only(self):
        self.assertIsNone(access.caddyfile(self.setup))
        os.environ['PARITY_DERIVA_DOMAIN'] = 'parity.example.com'
        self.auth({'mode': 'oauth'})
        with open(access.caddyfile(self.setup)) as handle:
            text = handle.read().split('\nparity.example.com {', 1)[1]
        self.assertNotIn('client_auth', text)
        self.assertNotIn('@@', text)
        # an authority, whatever the mode: the certificate is asked for, so it can be tried
        access.makeCA(self.setup)
        with open(access.caddyfile(self.setup)) as handle:
            self.assertIn('client_auth', handle.read().split('\nparity.example.com {', 1)[1])
        self.auth({'mode': 'cert', 'ca': 'own'})
        with open(access.caddyfile(self.setup)) as handle:
            text = handle.read().split('\nparity.example.com {', 1)[1]
        self.assertIn('\t\tclient_auth {\n\t\t\tmode verify_if_given\n\t\t\ttrust_pool file /parity/ca/ca.crt', text)
        os.environ['PARITY_DERIVA_DOMAIN'] = 'bad domain; import /etc'
        self.assertIsNone(access.caddyfile(self.setup))



class PermissionTest(Case):

    def cookie(self, who):
        return {'Cookie': 'pd_session=' + access.sign(access.cookieKey(self.setup),
                                                      {'ids': [who], 'until': time.time() + 60})}

    def test_read_only_looks_and_changes_nothing(self):
        self.auth({'mode': 'oauth', 'allow': [{'who': '@b.it', 'can': 'read'}, {'who': 'boss@b.it', 'can': 'write'}],
                   'providers': {'google': {'id': 'g'}}})
        access.envKeep({access.SECRET % 'GOOGLE': 's'}, self.setup)
        self.serve()
        reader, boss = self.cookie('x@b.it'), self.cookie('boss@b.it')
        self.assertEqual(json.loads(self.call('GET', '/api/access', headers=reader)[2])['can'], 'read')
        self.assertEqual(self.call('GET', '/api/favourites', headers=reader)[0], 200)
        status, _, raw = self.call('POST', '/api/favourites', {'source': {}}, headers=reader)
        self.assertEqual(status, 403)
        self.assertIn('read only', json.loads(raw)['error'])
        # the POSTs that only read go on to their route, with their body
        for path, body in (('/api/backtest', {'cachedOnly': True}), ('/api/sweep', {'dry': True, 'grid': {}}),
                           ('/api/market/compare', {})):
            status, _, raw = self.call('POST', path, body, headers=reader)
            self.assertNotIn('read only', raw.decode(), path)
        self.assertEqual(self.call('POST', '/api/sweep', {'dry': False}, headers=reader)[0], 403)
        self.assertNotEqual(self.call('POST', '/api/favourites', {'source': {}}, headers=boss)[0], 403)

    def test_certificates_by_name_once_the_list_names_one(self):
        self.auth({'mode': 'cert', 'allow': [{'who': 'cert:Anna', 'can': 'read'}, {'who': 'cert:Boss', 'can': 'write'}]})
        self.serve()
        status, headers, _ = self.call('GET', '/', headers={'X-Client-Subject': 'CN=Other,O=x'})
        self.assertEqual(headers['location'], 'http://127.0.0.1:%d/login?error=not-allowed' % self.port)
        self.assertEqual(self.call('GET', '/api/favourites', headers={'X-Client-Subject': 'CN=Anna'})[0], 200)
        self.assertEqual(self.call('POST', '/api/favourites', {'source': {}}, headers={'X-Client-Subject': 'CN=Anna'})[0], 403)
        self.assertNotEqual(self.call('POST', '/api/favourites', {'source': {}},
                                      headers={'X-Client-Subject': 'CN=Boss'})[0], 403)

    def test_both_wants_the_certificate_and_the_account(self):
        self.auth({'mode': 'both', 'allow': [{'who': 'a@b.it', 'can': 'write'}], 'providers': {'google': {'id': 'g'}}})
        access.envKeep({access.SECRET % 'GOOGLE': 's'}, self.setup)
        self.serve()
        account, cert = self.cookie('a@b.it'), {'X-Client-Subject': 'CN=Anyone'}
        self.assertEqual(self.call('GET', '/api/favourites', headers=account)[0], 403)
        self.assertEqual(self.call('GET', '/api/favourites', headers=cert)[0], 401)
        status, _, raw = self.call('GET', '/api/access', headers=dict(account, **cert))
        self.assertEqual((json.loads(raw)['user'], json.loads(raw)['can']), ('a@b.it', 'write'))
        self.assertEqual(self.call('GET', '/api/favourites', headers=dict(account, **cert))[0], 200)


class SwitchTest(Case):
    """The Access tab changes the mode, and refuses what would shut out the one changing it."""

    def setUp(self):
        Case.setUp(self)
        self.serve()

    def save(self, body, headers=None):
        status, _, raw = self.call('POST', '/api/access/settings', body, headers=headers)
        return status, json.loads(raw)

    def test_from_the_proxys_to_this_pc_only_and_back_to_a_certificate(self):
        self.assertIn('proxy', self.save({'mode': 'none'}, {'X-Forwarded-For': '1.2.3.4'})[1]['error'])
        self.assertEqual(self.save({'mode': 'none'})[1]['mode'], 'none')
        self.assertIn('authority first', self.save({'mode': 'cert'})[1]['error'])
        status, raw = self.call('POST', '/api/access/ca', {'make': True})[::2]
        self.assertEqual(json.loads(raw)['ca'], 'own')
        self.assertIn('no client certificate', self.save({'mode': 'cert'})[1]['error'])
        me = {'X-Client-Subject': 'CN=Claudia,O=parity-deriva'}
        self.assertIn('leaves out', self.save({'mode': 'cert', 'allow': [{'who': 'cert:Other'}]}, me)[1]['error'])
        status, shown = self.save({'mode': 'cert'}, me)
        self.assertEqual((shown['mode'], shown['certificate'], shown['user'], shown['can']),
                         ('cert', 'Claudia', 'Claudia', 'write'))
        # the authority in use stays
        self.assertIn('stop working', json.loads(self.call('POST', '/api/access/ca', {'make': True}, headers=me)[2])['error'])
        # the first certificate named: the one issuing it goes on the list too
        issued = json.loads(self.call('POST', '/api/access/cert', {'name': 'Anna', 'can': 'read'}, headers=me)[2])
        self.assertTrue(issued['p12'] and issued['password'])
        self.assertEqual([c['name'] for c in issued['settings']['issued']], ['Anna'])
        self.assertTrue(issued['settings']['authority']['name'].startswith('parity-deriva CA'))
        self.assertRegex(issued['settings']['authority']['until'], r'^20\d\d-\d\d-\d\d$')
        self.assertEqual(issued['settings']['allow'], [{'who': 'cert:Anna', 'can': 'read'}, {'who': 'cert:Claudia', 'can': 'write'}])
        self.assertEqual(self.call('GET', '/api/favourites', headers=me)[0], 200)

    def test_to_an_account_only_signed_in_with_one_that_writes(self):
        self.assertIn('provider first', self.save({'mode': 'oauth', 'allow': ['a@b.it']})[1]['error'])
        body = {'mode': 'oauth', 'allow': ['a@b.it'], 'public_url': 'https://parity.example.com',
                'providers': {'google': {'id': 'g', 'secret': 's'}, 'github': {'id': ''}}}
        self.assertIn('sign in first', self.save(body)[1]['error'])
        # the sign-in to try works before the switch, the providers kept already
        self.save({'providers': body['providers'], 'public_url': body['public_url']})
        self.assertEqual(access.mode(self.setup), None)
        status, headers, _ = self.call('GET', '/auth/start/google?next=%2Fsettings')
        self.assertTrue(headers['location'].startswith('https://accounts.google.com/'))
        cookie = {'Cookie': 'pd_session=' + access.sign(access.cookieKey(self.setup), {'ids': ['a@b.it'], 'until': time.time() + 60})}
        self.assertEqual(self.save(body, cookie)[1]['mode'], 'oauth')
        self.assertEqual(self.call('GET', '/api/favourites')[0], 401)
        self.assertEqual(self.call('GET', '/api/favourites', headers=cookie)[0], 200)
        # and to both: the certificate as well
        access.makeCA(self.setup)
        self.assertIn('no client certificate', self.save({'mode': 'both'}, cookie)[1]['error'])
        both = dict(cookie, **{'X-Client-Subject': 'CN=Claudia'})
        self.assertEqual(self.save({'mode': 'both'}, both)[1]['mode'], 'both')
        self.assertEqual(self.call('GET', '/api/favourites', headers=cookie)[0], 403)


if __name__ == '__main__':
    unittest.main()
