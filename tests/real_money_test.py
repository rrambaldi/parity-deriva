"""
A server for demo accounts or for real money (etc/settings.py ACCOUNTS):
which accounts it trades, what a real one asks before a session starts, the
promotion from a demo server, the loss limit and the kill switch.
"""

import os
import shutil
import tempfile
import time
import types
import unittest
from unittest import mock

from parity_deriva.etc import settings
from parity_deriva.web import livesessions, mcp
from parity_deriva.web.service import Service

TARGETS = [{'provider': 'ig', 'configured': True, 'error': None, 'capabilities': {},
            'accounts': [{'id': 'DEMO1', 'balance': 1000.0, 'demo': True},
                         {'id': 'REAL1', 'balance': 1000.0, 'demo': False}]},
           {'provider': 'twelvedata', 'configured': True, 'error': None, 'capabilities': {},
            'accounts': [{'id': 'paper', 'balance': 1000.0, 'demo': True}]}]
FORM = {'strategy': 'AB', 'instrument': 'EUR_USD', 'granularity': 'H1', 'capital': '1000'}
DAY = 86400000


def record(days, trades, alarms=0, demo=True):
    now = int(time.time() * 1000)
    return {'fields': dict(FORM, **{'from': '2020-01-01'}), 'sessions': [{
        'id': 'S', 'provider': 'ig', 'account': 'DEMO1', 'demo': demo, 'started': now - days * DAY,
        'stopped': None, 'closed': [{'time': now, 'pl': 1.0}] * trades,
        'parity': {'divergences': 0, 'alarms': ['a breach'] * alarms}}]}


class AccountsSettingTest(unittest.TestCase):

    def load(self, value):
        with mock.patch.dict(os.environ, {'PARITY_DERIVA_ACCOUNTS': value}):
            return livesessions._load(settings.__file__, 'parity_deriva_accounts_test')

    def test_the_server_is_demo_unless_it_says_real(self):
        real = self.load('real')
        self.assertEqual((real.ACCOUNTS, real.DOMAIN, real.MT5_ALLOW_REAL), ('real', 'real', True))
        for value in ('demo', 'reale', ''):
            held = self.load(value)
            self.assertEqual((held.ACCOUNTS, held.DOMAIN, held.MT5_ALLOW_REAL), ('demo', 'practice', False))


class DemoRealTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.live = livesessions.LiveSessions(os.path.join(self.root, 'live'))
        self.live.targets = lambda fresh=False: TARGETS
        self.live.check = lambda fields, provider: None
        self.spawned = []
        self.live.spawn = lambda fields, provider, account: self.spawned.append((provider, account['id']))

    def server(self, kind):
        patch = mock.patch.object(settings, 'ACCOUNTS', kind)
        patch.start()
        self.addCleanup(patch.stop)

    def start(self, account, provider='ig', confirm=None):
        return self.live.start(FORM, [{'provider': provider, 'account': account}], confirm)

    def test_a_demo_server_trades_demo_accounts_only(self):
        self.server('demo')
        with self.assertRaises(livesessions.LiveError) as caught:
            self.start('REAL1')
        self.assertIn('demo accounts only', str(caught.exception))
        self.start('DEMO1')
        self.start('paper', 'twelvedata')
        self.assertEqual(self.spawned, [('ig', 'DEMO1'), ('twelvedata', 'paper')])

    def test_a_real_server_trades_a_promoted_form_on_real_money_confirmed(self):
        self.server('real')
        with self.assertRaises(livesessions.LiveError) as caught:
            self.start('DEMO1')
        self.assertIn('real money only', str(caught.exception))
        with self.assertRaises(livesessions.LiveError) as caught:
            self.start('REAL1', confirm='1000')
        self.assertIn('not promoted', str(caught.exception))
        # judged here, by this server's minimums, and kept either way
        told = self.live.promote(record(3, 5, alarms=1), 'promote: demo')
        self.assertFalse(told['ok'])
        self.assertEqual(len(told['need']), 3, told['need'])
        self.assertFalse(self.live.promote(record(30, 50, demo=False), 'promote: demo')['ok'])
        with self.assertRaises(livesessions.LiveError):
            self.start('REAL1', confirm='1000')
        self.assertTrue(self.live.promote(record(30, 50), 'promote: demo')['ok'])
        # the form is the same one without its window or its capital
        self.assertTrue(self.live.promoted(dict(FORM, capital='5000'))['ok'])
        for confirm in (None, '999'):
            with self.assertRaises(livesessions.LiveError) as caught:
                self.start('REAL1', confirm=confirm)
            self.assertIn('confirm the capital at risk', str(caught.exception))
        self.start('REAL1', confirm='1000')
        self.assertEqual(self.spawned, [('ig', 'REAL1')])

    def test_the_loss_limit_stops_every_session_until_tomorrow(self):
        self.server('real')
        self.live.promote(record(30, 50), 'promote: demo')
        stopped = []
        self.live.stopAll = lambda: stopped.append(1) or {'stopped': ['S']}
        self.live.lossToday = lambda: (-20.0, 1000.0)
        self.assertIsNone(self.live.guard(3))
        self.live.lossToday = lambda: (-30.0, 1000.0)
        held = self.live.guard(3)
        self.assertEqual((held['stopped'], stopped), (['S'], [1]))
        self.assertIsNone(self.live.guard(3))
        with self.assertRaises(livesessions.LiveError) as caught:
            self.start('REAL1', confirm='1000')
        self.assertIn('loss limit', str(caught.exception))
        # a halt of another day is no halt
        with open(self.live.haltPath(), 'w') as handle:
            handle.write('{"day": 0}')
        self.assertIsNone(self.live.halted())


class RealMcpTest(unittest.TestCase):

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        self.service = Service(setup=types.SimpleNamespace(DATA_DIR=folder))
        patch = mock.patch.object(settings, 'ACCOUNTS', 'real')
        patch.start()
        self.addCleanup(patch.stop)

    def call(self, name, args, client):
        return mcp.call(self.service, name, args, 'https://real', client)

    def test_a_real_server_takes_promotions_and_nothing_else(self):
        for client in ('token: cli', 'claude.ai', 'pc: my PC'):
            with self.assertRaises(mcp.ToolError) as caught:
                self.call('submit_strategy', {'name': 'X', 'source': 'x'}, client)
            self.assertIn('takes only what a demo server promotes', str(caught.exception))
        with self.assertRaises(mcp.ToolError):
            self.call('push_record', {'record': record(30, 50)}, 'pc: my PC')
        told = self.call('push_record', {'record': record(30, 50)}, 'promote: demo')
        self.assertTrue(told['ok'], told)
        self.assertEqual(self.service.live.promoted(FORM)['origin'], 'promote: demo')
        # reading is still reading
        self.call('list_strategies', {}, 'claude.ai')
        # and a demo server takes no promotion at all
        with mock.patch.object(settings, 'ACCOUNTS', 'demo'), self.assertRaises(mcp.ToolError) as caught:
            self.call('push_record', {'record': record(30, 50)}, 'promote: demo')
        self.assertIn('goes to a real money one', str(caught.exception))


class DemoPromoteTest(unittest.TestCase):

    def test_a_demo_server_sends_a_forms_record_to_the_real_one(self):
        from parity_deriva.data import sources
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        service = Service(setup=types.SimpleNamespace(DATA_DIR=folder))
        session = '20260926-120000-abcdef'
        service.live.writeMeta(session, {'id': session, 'fields': dict(FORM), 'provider': 'ig',
                                         'account': 'DEMO1', 'demo': True, 'balance': 1000.0,
                                         'pid': None, 'started': int(time.time() * 1000) - DAY,
                                         'stopped': None})
        from parity_deriva.web.service import ServiceError
        with self.assertRaises(ServiceError) as caught:
            service.promote(session)
        self.assertIn('set it on the settings page', str(caught.exception))
        service.setPromoteTarget({'url': 'https://real/mcp', 'token': 'the promote token'})
        self.assertEqual(service.serverData()['promoteTo'], {'url': 'https://real/mcp', 'token': True})
        sent = []

        def rpc(upstream, name, args, timeout=180):
            sent.append((upstream['token'], name, args))
            return {'ok': False, 'need': ['20 days on demo, it has 1.0'], 'days': 1.0, 'trades': 0,
                    'alarms': 0, 'net': 0}
        with mock.patch.object(sources, 'rpc', rpc):
            told = service.promote(session)
        self.assertEqual(told['need'], ['20 days on demo, it has 1.0'])
        # a built-in strategy goes through git: only the record is sent
        self.assertEqual([(token, name) for token, name, _ in sent], [('the promote token', 'push_record')])
        shipped = sent[0][2]['record']
        self.assertEqual((shipped['fields'], [x['id'] for x in shipped['sessions']]), (FORM, [session]))
        with mock.patch.object(settings, 'ACCOUNTS', 'real'), self.assertRaises(ServiceError):
            service.promote(session)


if __name__ == '__main__':
    unittest.main()
