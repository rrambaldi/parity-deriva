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
from parity_deriva.performance import montecarlo
from parity_deriva.web import livesessions, mcp
from parity_deriva.web.service import Service

TARGETS = [{'provider': 'ig', 'configured': True, 'error': None, 'capabilities': {},
            'accounts': [{'id': 'DEMO1', 'balance': 1000.0, 'demo': True},
                         {'id': 'REAL1', 'balance': 1000.0, 'demo': False}]},
           {'provider': 'twelvedata', 'configured': True, 'error': None, 'capabilities': {},
            'accounts': [{'id': 'paper', 'balance': 1000.0, 'demo': True}]}]
FORM = {'strategy': 'AB', 'instrument': 'EUR_USD', 'granularity': 'H1', 'capital': '1000'}
DAY = 86400000


def record(days, trades, alarms=0, demo=True, pl=1.0):
    now = int(time.time() * 1000)
    return {'fields': dict(FORM, **{'from': '2020-01-01'}), 'sessions': [{
        'id': 'S', 'provider': 'ig', 'account': 'DEMO1', 'demo': demo, 'started': now - days * DAY,
        'stopped': None, 'closed': [{'time': now, 'pl': pl}] * trades,
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
        # the record's own checks: the card's are CardPromotionTest's
        patch = mock.patch.object(settings, 'PROMOTE_NEEDS_CARD', False)
        patch.start()
        self.addCleanup(patch.stop)

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

    def test_a_promotion_needs_a_net_not_below_zero(self):
        told = self.live.promote(record(30, 50, pl=-2.5), 'promote: demo')
        self.assertFalse(told['ok'])
        self.assertEqual(told['need'], ['net on demo \u2265 0, it has -125.00'])
        self.assertTrue(self.live.promote(record(30, 50, pl=0.0), 'promote: demo')['ok'])
        self.assertTrue(self.live.promote(record(30, 50, pl=2.5), 'promote: demo')['ok'])

    def test_a_slow_timeframe_passes_with_fewer_trades_after_enough_days(self):
        self.assertTrue(self.live.promote(record(95, 12), 'promote: demo')['ok'])
        for days, trades in ((95, 8), (40, 12)):
            told = self.live.promote(record(days, trades), 'promote: demo')
            self.assertFalse(told['ok'])
            self.assertEqual(told['need'], ['30 closed trades, or 10 after 90 days: it has %d' % trades])

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


class CardPromotionTest(unittest.TestCase):
    """The demo record against its version's card: its band, its losing streak (C1b)."""

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        self.setup = types.SimpleNamespace(DATA_DIR=folder)
        self.live = livesessions.LiveSessions(os.path.join(folder, 'live'), self.setup)
        self.card = {'id': '0123456789abcdef', 'label': 'AB v1', 'state': 'DEMO', 'fields': FORM,
                     'reference': {'worstStreak': 4, 'band': montecarlo.band([0.01, 0.012, -0.008, -0.008] * 25)}}

    def carded(self, pls, card=True, capital=1000.0):
        now = int(time.time() * 1000)
        held = record(30, 0)
        held['sessions'][0].update(capital=capital, closed=[{'time': now + i, 'pl': pl} for i, pl in enumerate(pls)])
        if card:
            held['card'] = self.card if card is True else card
        return self.live.promote(held, 'promote: archive')

    def test_a_curve_inside_the_band_with_a_short_streak_is_promoted(self):
        told = self.carded([10.0, 12.0, -8.0, -8.0] * 8)
        self.assertEqual(told['need'], [])
        self.assertEqual(told['card']['id'], self.card['id'])

    def test_the_first_trade_under_the_band_is_named(self):
        told = self.carded([-8.0] * 3 + [10.0, 12.0] * 15)
        self.assertEqual(told['need'], ["a curve inside the card's band: trade %d fell under its 5th percentile"
                                        % montecarlo.below(montecarlo.compounded([-0.008] * 3), self.card['reference']['band'])])

    def test_a_streak_longer_than_the_cards_is_refused(self):
        told = self.carded([30.0] * 25 + [-8.0] * 5 + [30.0] * 2)
        self.assertIn("a losing streak of at most 4, the card's worst: it had 5", told['need'])

    def test_no_card_or_one_without_a_reference_is_no_promotion(self):
        self.assertEqual(self.carded([10.0] * 32, card=False)['need'],
                         ['a reference card: the version passes the gate first'])
        failed = dict(self.card, reference=None)
        self.assertEqual(self.carded([10.0] * 32, card=failed)['need'],
                         ["a card that passed the gate's holdout: AB v1 has no reference"])
        with mock.patch.object(settings, 'PROMOTE_NEEDS_CARD', False):
            self.assertTrue(self.carded([10.0] * 32, card=False)['ok'])


class RealMcpTest(unittest.TestCase):

    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        self.service = Service(setup=types.SimpleNamespace(DATA_DIR=folder))
        for patch in (mock.patch.object(settings, 'ACCOUNTS', 'real'),
                      mock.patch.object(settings, 'PROMOTE_NEEDS_CARD', False)):
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, name, args, client):
        return mcp.call(self.service, name, args, 'https://real', client)

    def test_a_real_server_takes_promotions_and_nothing_else(self):
        for client in ('token: cli', 'claude.ai', 'pc: my PC'):
            with self.assertRaises(mcp.ToolError) as caught:
                self.call('submit_strategy', {'name': 'X', 'source': 'x'}, client)
            self.assertIn('takes only what the archive promotes', str(caught.exception))
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
        # the archive reads the trades with the same token, an assistant does not
        self.assertEqual(self.call('live_status', {}, 'promote: archive')['server']['accounts'], 'real')
        with self.assertRaises(mcp.ToolError):
            self.call('live_status', {}, 'claude.ai')


class ArchivePromoteTest(unittest.TestCase):

    def test_the_archive_sends_a_forms_record_on_demo_to_the_real_server(self):
        from parity_deriva.data import sources
        from parity_deriva.web import servers
        from parity_deriva.web.service import ServiceError
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        service = Service(setup=types.SimpleNamespace(DATA_DIR=folder))
        session = '20260926-120000-abcdef'
        service.live.writeMeta(session, {'id': session, 'fields': dict(FORM), 'provider': 'ig',
                                         'account': 'DEMO1', 'demo': True, 'balance': 1000.0,
                                         'pid': None, 'started': int(time.time() * 1000) - DAY,
                                         'stopped': None})
        with self.assertRaises(ServiceError) as caught:
            service.pushForm({'server': 'real', 'fields': FORM})
        self.assertIn("no trade server 'real'", str(caught.exception))
        service.saveTradeServer({'name': 'real', 'url': 'https://real/mcp', 'token': 'the promote token'})
        self.assertEqual(service.tradeServers()['servers'][0]['token'], True)
        sent = []

        def rpc(upstream, name, args, timeout=180):
            sent.append((upstream['token'], name, args))
            if name == 'live_status':
                return {'server': {'accounts': 'real'}, 'sessions': []}
            return {'ok': False, 'need': ['20 days on demo, it has 1.0'], 'days': 1.0, 'trades': 0,
                    'alarms': 0, 'net': 0}
        with mock.patch.object(sources, 'rpc', rpc):
            told = service.pushForm({'server': 'real', 'fields': FORM})
        self.assertEqual(told['verdict']['need'], ['20 days on demo, it has 1.0'])
        # the server is asked what it trades first; a built-in strategy goes
        # through git, and no set run was starred: only the record is sent
        self.assertEqual([(token, name) for token, name, _ in sent],
                         [('the promote token', 'live_status'), ('the promote token', 'push_record')])
        shipped = sent[1][2]['record']
        self.assertEqual((shipped['fields'], [x['id'] for x in shipped['sessions']]), (FORM, [session]))
        # an archive without the role does none of it
        servers.saveRoles(['test'], service.setup)
        with self.assertRaises(ServiceError) as caught:
            service.pushForm({'server': 'real', 'fields': FORM})
        self.assertIn('no archive role', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
