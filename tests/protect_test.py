"""
Tests for live on real money (docs/PIANO-FASE1.md C6, web/livesessions.py):
the ramp ends at the first of its trades and its days, full size only with no
trade open and before the end only when asked; a session that breaks its card
is stopped and the card SUSPENDED - never DEAD - with an alert and a journal
entry; the panel's numbers against the card.
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
from parity_deriva.tests.servers_test import FIELDS
from parity_deriva.web import cards, journal, livesessions, notify

DAY = 86400000


def session(closed, days=10, ramp='30/60/20000', capital='5000', open_=()):
    now = int(time.time() * 1000)
    return {'id': 'S1', 'provider': 'ig', 'account': 'R1', 'demo': False, 'running': True, 'stopped': None,
            'started': now - days * DAY, 'fields': dict(FIELDS, capital=capital, **({'ramp': ramp} if ramp else {})),
            'closed': [{'time': now - (len(closed) - i) * 1000, 'pl': pl} for i, pl in enumerate(closed)],
            'open': list(open_), 'parity': {'alarms': []}}


class RampTest(unittest.TestCase):

    def test_it_ends_at_the_first_of_its_trades_and_its_days(self):
        state = livesessions.rampState(session([10.0] * 12, days=10))
        self.assertEqual((state['trades'], state['days'], state['full'], state['closed'], state['done']),
                         (30, 60, 20000.0, 12, False))
        self.assertTrue(livesessions.rampState(session([10.0] * 30, days=10))['done'])
        self.assertTrue(livesessions.rampState(session([10.0] * 3, days=61))['done'])
        self.assertIsNone(livesessions.rampState(session([10.0], ramp=None)))
        self.assertIsNone(livesessions.rampState(session([10.0], ramp='soon')))
        # the ramp is no other form: the same record, the same promotion
        self.assertEqual(livesessions.groupKey(session([], ramp='30/60/1')['fields']),
                         livesessions.groupKey(dict(FIELDS, capital='20000')))


class Case(unittest.TestCase):

    def setUp(self):
        home = tempfile.mkdtemp(prefix='parity-deriva-protect-')
        self.addCleanup(shutil.rmtree, home, True)
        self.setup = types.SimpleNamespace(DATA_DIR=home, ACCOUNTS='real')
        self.live = livesessions.LiveSessions(os.path.join(home, 'live'), self.setup)
        self.stopped, self.started = [], []
        self.live.stop = lambda sid: self.stopped.append(sid) or {'id': sid}
        self.live.start = lambda fields, targets, confirm=None: self.started.append((fields, targets, confirm)) or [
            {'id': 'S2', 'fields': fields, 'provider': 'ig'}]
        for patch in (mock.patch.object(settings, 'ACCOUNTS', 'real'), mock.patch.object(notify, 'send', lambda *a: {})):
            patch.start()
            self.addCleanup(patch.stop)


class FullSizeTest(Case):

    def full(self, s, confirm='20000', early=False):
        self.live.summary = lambda sid, detail=False: dict(s, ramp=livesessions.rampState(s))
        return self.live.fullSize('S1', confirm, early)

    def test_full_size_only_with_no_trade_open_and_early_only_when_asked(self):
        with self.assertRaises(livesessions.LiveError) as caught:
            self.full(session([10.0] * 30, open_=[{'deal': 'D1'}]))
        self.assertIn('a trade is open', str(caught.exception))
        with self.assertRaises(livesessions.LiveError) as caught:
            self.full(session([10.0] * 12))
        self.assertIn('is at 12 of 30 trades', str(caught.exception))
        with self.assertRaises(livesessions.LiveError) as caught:
            self.full(session([10.0] * 30), confirm='5000')
        self.assertIn('confirm the full capital, 20000', str(caught.exception))
        with self.assertRaises(livesessions.LiveError):
            self.full(session([10.0] * 30, ramp=None))
        self.assertEqual(self.stopped, [])
        started = self.full(session([10.0] * 12), early=True)
        self.assertEqual(self.stopped, ['S1'])
        fields, targets, confirm = self.started[0]
        self.assertEqual((fields['capital'], 'ramp' in fields, targets, confirm),
                         ('20000', False, [{'provider': 'ig', 'account': 'R1'}], '20000'))
        self.assertEqual(started[0]['id'], 'S2')
        entry = journal.read(self.setup, 'AG01')[-1]
        self.assertEqual((entry['kind'], entry['data']['early'], entry['data']['after']['trades']), ('full-size', True, 12))


class ProtectTest(Case):

    def setUp(self):
        super().setUp()
        band = montecarlo.band([0.01, 0.012, -0.008, -0.008] * 25)
        self.card = cards.save(self.setup, {
            'id': '0123456789abcdef', 'label': 'AG01 v1', 'strategy': 'AG01', 'fields': FIELDS, 'state': 'LIVE',
            'reference': {'maxDDpct': 4.0, 'worstStreak': 4, 'band': band, 'pf': 1.3, 'expectancy': 5.0,
                          'winRate': 0.5, 'tradesPerMonth': 3.0}})
        self.live.promoted = lambda fields: {'ok': True, 'card': self.card}

    def test_a_session_inside_its_card_carries_on(self):
        self.assertIsNone(self.live.protect(self.setup, session([50.0, 60.0, -40.0, -40.0] * 6)))
        self.assertEqual((self.stopped, cards.get(self.setup, self.card['id'])['state']), ([], 'LIVE'))

    def test_a_drawdown_past_the_card_stops_it_and_suspends_the_card(self):
        why = self.live.protect(self.setup, session([60.0] * 5 + [-40.0] * 4 + [-200.0, -200.0]))
        self.assertIn("a drawdown of", why)
        self.assertIn("more than 1.5 x the card's 4.0%", why)
        self.assertEqual(self.stopped, ['S1'])
        card = cards.get(self.setup, self.card['id'])
        self.assertEqual(card['state'], 'SUSPENDED')
        self.assertIn('protection:S1', notify.read(self.setup)['open'])
        kinds = [(e['kind'], e['by']) for e in journal.read(self.setup, 'AG01')][-2:]
        self.assertEqual(kinds, [('state', 'parity-deriva'), ('protection', 'parity-deriva')])
        # suspended, it is no longer judged: nothing moves it to DEAD
        self.assertIsNone(self.live.protect(self.setup, session([-500.0] * 10)))
        self.assertNotEqual(cards.get(self.setup, self.card['id'])['state'], 'DEAD')

    def test_the_band_and_the_streak(self):
        self.assertEqual(self.live.protect(self.setup, session([-45.0] * 3 + [60.0] * 10)),
                         "under the card's band at trade %d" % montecarlo.below(
                             montecarlo.compounded([-0.009] * 3), self.card['reference']['band']))
        cards.save(self.setup, dict(cards.get(self.setup, self.card['id']), state='LIVE',
                                    reference=dict(self.card['reference'], band=None, maxDDpct=50.0)))
        self.assertIn('a losing streak of 7, more than 1.5 x the card\'s 4',
                      self.live.protect(self.setup, session([80.0] * 20 + [-10.0] * 7)))

    def test_the_panel_against_the_card(self):
        v = livesessions.versus(session([50.0, -25.0] * 30), self.card)
        self.assertEqual((v['recent']['trades'], v['recent']['pf'], v['recent']['winRate']), (50, 2.0, 0.5))
        self.assertEqual(v['reference']['pf'], 1.3)
        self.assertEqual(len(v['curve']), 60)
        self.assertEqual(len(v['band']['p5']), 60)
        self.assertEqual(v['card']['label'], 'AG01 v1')


if __name__ == '__main__':
    unittest.main()
