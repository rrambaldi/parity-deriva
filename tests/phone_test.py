"""
Tests for web/phone.py: the pairing code good once and for ten minutes, the
token kept only as its hash, a phone revoked, the phone's routes closed to
anyone without the token, the VAPID signature (openssl, for real) and the
push, and a trade server without its public address flagged.

A loopback server the tests start themselves; no push service is called.
"""

import base64
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import types
import unittest
from unittest import mock

from parity_deriva.web import notify, phone, servers
from parity_deriva.web import service as service_module


def unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def der(signature):
    """JOSE's r || s back to the DER openssl verifies."""
    out = b''
    for half in (signature[:32], signature[32:]):
        half = half.lstrip(b'\0')
        if not half or half[0] & 0x80:
            half = b'\0' + half
        out += bytes([2, len(half)]) + half
    return bytes([0x30, len(out)]) + out


class Case(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='parity-deriva-phone-')
        self.addCleanup(shutil.rmtree, self.home, True)
        self.setup = types.SimpleNamespace(DATA_DIR=self.home, ACCOUNTS='demo')
        for module in (notify, phone):
            patch = mock.patch.object(module, 'logger', mock.Mock())
            patch.start()
            self.addCleanup(patch.stop)

    def serve(self):
        self.server = service_module.serve(host='127.0.0.1', port=0, setup=self.setup)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        return self.server.RequestHandlerClass.service

    def call(self, method, path, body=None, cookie=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=20)
        headers = {'Cookie': cookie} if cookie else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers.update({'X-Parity-Deriva': '1', 'Content-Type': 'application/json'})
        connection.request(method, path, body=data, headers=headers)
        answer = connection.getresponse()
        raw = answer.read()
        connection.close()
        return answer.status, answer.getheader('Set-Cookie'), raw


class PairTest(Case):

    def test_a_code_is_good_once_and_for_ten_minutes(self):
        code = phone.newCode(self.setup)['code']
        self.assertIsNone(phone.pair(self.setup, 'WRONG123', 'iPhone'))
        token = phone.pair(self.setup, code.lower(), 'iPhone')
        self.assertTrue(token)
        self.assertIsNone(phone.pair(self.setup, code, 'iPhone'))
        late = phone.newCode(self.setup)['code']
        with mock.patch.object(phone, 'now', lambda: phone.load(self.setup)['pairing']['until'] + 1):
            self.assertIsNone(phone.pair(self.setup, late, 'Android'))
        # a new code replaces the last
        first, second = phone.newCode(self.setup)['code'], phone.newCode(self.setup)['code']
        self.assertIsNone(phone.pair(self.setup, first, 'x'))
        self.assertTrue(phone.pair(self.setup, second, 'x'))

    def test_the_token_is_kept_only_as_its_hash_and_a_phone_is_revoked(self):
        token = phone.pair(self.setup, phone.newCode(self.setup)['code'], 'iPhone')
        with open(os.path.join(self.home, 'phones.json')) as handle:
            kept = handle.read()
        self.assertNotIn(token, kept)
        self.assertIn(phone.digest(token), kept)
        self.assertEqual(oct(os.stat(os.path.join(self.home, 'phones.json')).st_mode & 0o777), '0o600')
        listed, = phone.listed(self.setup)
        self.assertEqual(sorted(listed), ['id', 'name', 'notifications', 'paired', 'seen'])
        self.assertTrue(phone.revoke(self.setup, listed['id']))
        self.assertEqual(phone.listed(self.setup), [])


class RoutesTest(Case):

    def test_without_the_token_the_phone_routes_answer_401(self):
        self.serve()
        for path in ('/api/phone/state', '/api/phone/alerts'):
            self.assertEqual(self.call('GET', path)[0], 401, path)
        self.assertEqual(self.call('GET', '/api/phone/state', cookie='pd_phone=forged')[0], 401)
        self.assertEqual(self.call('POST', '/api/phone/subscribe', {'subscription': {}})[0], 401)
        self.assertEqual(self.call('POST', '/api/phone/pair', {'code': 'NOPE'})[0], 403)
        # the page, its worker and its manifest are open: they hold no data
        for path in ('/phone', '/phone-sw.js', '/phone-manifest.json'):
            self.assertEqual(self.call('GET', path)[0], 200, path)

    def test_a_paired_phone_reads_the_state_and_its_alerts_once(self):
        self.serve()
        code = phone.newCode(self.setup)['code']
        status, cookie, _ = self.call('POST', '/api/phone/pair', {'code': code, 'name': 'Android'})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        jar = cookie.split(';')[0]
        status, _, raw = self.call('GET', '/api/phone/state', cookie=jar)
        self.assertEqual(status, 200)
        state = json.loads(raw)
        self.assertEqual((state['title'], state['sessions'], state['notifications']), ('parity demo', [], False))
        self.assertEqual(len(unb64(state['vapid'])), 65)
        notify.notify(self.setup, 'info', 'state', 'x', 'an informative one')
        notify.notify(self.setup, 'urgent', 'exited', 'S1', 'AB · died')
        _, _, raw = self.call('GET', '/api/phone/alerts', cookie=jar)
        self.assertEqual([a['text'] for a in json.loads(raw)['alerts']], ['AB · died'])
        _, _, raw = self.call('GET', '/api/phone/alerts', cookie=jar)
        self.assertEqual(json.loads(raw)['alerts'], [])
        self.assertEqual(self.call('POST', '/api/phone/subscribe',
                                   {'subscription': {'endpoint': 'http://inside/'}}, cookie=jar)[0], 400)
        self.assertEqual(self.call('POST', '/api/phone/subscribe',
                                   {'subscription': {'endpoint': 'https://push.example/abc'}}, cookie=jar)[0], 200)
        self.assertTrue(phone.listed(self.setup)[0]['notifications'])


class PushTest(Case):

    def test_the_vapid_signature_verifies_with_openssl(self):
        header = phone.authorization(self.setup, 'https://fcm.googleapis.com/fcm/send/abc')
        token, key = header[len('vapid t='):].split(', k=')
        head, claims, signature = token.split('.')
        self.assertEqual(json.loads(unb64(head)), {'typ': 'JWT', 'alg': 'ES256'})
        self.assertEqual(json.loads(unb64(claims))['aud'], 'https://fcm.googleapis.com')
        name, public = phone.vapid(self.setup)
        self.assertEqual(unb64(key), public)
        with open(os.path.join(self.home, 'sig.der'), 'wb') as handle:
            handle.write(der(unb64(signature)))
        subprocess.run(['openssl', 'ec', '-in', name, '-pubout', '-out', os.path.join(self.home, 'pub.pem')],
                       check=True, capture_output=True)
        checked = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', os.path.join(self.home, 'pub.pem'),
                                  '-signature', os.path.join(self.home, 'sig.der')],
                                 input=('%s.%s' % (head, claims)).encode(), capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertEqual(oct(os.stat(name).st_mode & 0o777), '0o600')

    def test_a_push_is_empty_and_a_dropped_subscription_is_forgotten(self):
        self.assertEqual(phone.push(self.setup, {}), 'off')
        for name in ('a', 'b'):
            token = phone.pair(self.setup, phone.newCode(self.setup)['code'], name)
            ident = [p for p in phone.load(self.setup)['phones'] if p['hash'] == phone.digest(token)][0]['id']
            phone.subscribe(self.setup, ident, {'endpoint': 'https://push.example/' + name})
        sent = []

        def urlopen(request, timeout=None):
            sent.append(request)
            if request.full_url.endswith('/b'):
                raise phone.urllib.error.HTTPError(request.full_url, 410, 'Gone', {}, None)
            return types.SimpleNamespace(read=lambda: b'')
        with mock.patch.object(phone.urllib.request, 'urlopen', urlopen):
            self.assertEqual(phone.push(self.setup, {}), 'sent to 1 of 2')
        self.assertEqual([r.data for r in sent], [b'', b''])
        self.assertTrue(sent[0].get_header('Authorization').startswith('vapid t='))
        self.assertEqual([p['notifications'] for p in phone.listed(self.setup)], [True, False])


class PublicAddressTest(Case):

    def test_a_trade_server_without_its_public_address_is_flagged(self):
        handler = types.SimpleNamespace(service=types.SimpleNamespace(setup=self.setup), headers={})
        service = handler.service
        self.assertTrue(phone.settings(service, handler)['tradeWithoutUrl'])
        servers.saveRoles(['archive', 'test'], self.setup)
        self.assertFalse(phone.settings(service, handler)['tradeWithoutUrl'])
        servers.saveRoles(['trade'], self.setup)
        self.setup.PUBLIC_URL = 'https://real.example/parity'
        self.assertFalse(phone.settings(service, handler)['tradeWithoutUrl'])
        self.assertEqual(phone.settings(service, handler)['pairUrl'], 'https://real.example/parity/phone')


if __name__ == '__main__':
    unittest.main()
