"""
Tests for web/cards.py: a version's id is the code and the frozen parameters
and nothing else, its label counted by strategy, instrument and timeframe;
the moves the state may make and the ones it may not; DEAD only by a
person; every move in the journal; the card going with a push to a real
money server, which keeps a copy.
"""

import os
import unittest
from unittest import mock

from parity_deriva.data import sources
from parity_deriva.scripts import sync
from parity_deriva.tests.servers_test import FIELDS, saveSet, server, SWEEP
from parity_deriva.web import cards, journal, livesessions, mcp


class VersionTest(unittest.TestCase):

    def setUp(self):
        self.setup = server(self).setup

    def test_the_id_is_the_code_and_the_parameters_not_the_window_nor_the_capital(self):
        base = cards.versionId('abc', FIELDS)
        self.assertEqual(base, cards.versionId('abc', dict(FIELDS, capital='5000', balance='1', confirmed='1',
                                                           **{'from': '2015-01-01', 'to': '2016-01-01'})))
        self.assertNotEqual(base, cards.versionId('abc', dict(FIELDS, slScale='1.5')))
        self.assertNotEqual(base, cards.versionId('abd', FIELDS))

    def test_a_card_in_sim_labelled_by_strategy_instrument_and_timeframe(self):
        first = cards.make(self.setup, FIELDS)
        self.assertEqual((first['label'], first['state'], first['strategy']), ('AG01 v1', 'SIM', 'AG01'))
        self.assertEqual(cards.make(self.setup, dict(FIELDS, capital='9000'))['id'], first['id'])
        self.assertEqual(cards.make(self.setup, dict(FIELDS, slScale='2'))['label'], 'AG01 v2')
        self.assertEqual(cards.make(self.setup, dict(FIELDS, granularity='H4'))['label'], 'AG01 v1')
        self.assertEqual(cards.forFields(self.setup, dict(FIELDS, capital='1'))['id'], first['id'])
        self.assertIsNone(cards.forFields(self.setup, dict(FIELDS, slScale='9')))
        self.assertEqual([e['kind'] for e in journal.read(self.setup, 'AG01')], ['idea', 'version', 'version', 'version'])
        with self.assertRaises(cards.CardError):
            cards.make(self.setup, dict(FIELDS, strategy='NOT-A-STRATEGY'))
        with self.assertRaises(cards.CardError):
            cards.get(self.setup, '../../etc/passwd')


class MoveTest(unittest.TestCase):

    def setUp(self):
        self.setup = server(self).setup
        self.card = cards.make(self.setup, FIELDS)

    def test_the_moves_there_are_and_the_ones_there_are_not(self):
        with self.assertRaises(cards.CardError) as caught:
            cards.move(self.setup, self.card['id'], 'LIVE', 'too soon', 'parity-deriva')
        self.assertIn('it goes to DEMO or DEAD, not to LIVE', str(caught.exception))
        for to in ('DEMO', 'LIVE', 'SUSPENDED', 'SIM'):
            self.assertEqual(cards.move(self.setup, self.card['id'], to, 'a reason', 'parity-deriva')['state'], to)
        with self.assertRaises(cards.CardError):
            cards.move(self.setup, self.card['id'], 'NOWHERE', '', 'rr')
        states = [(e['data']['from'], e['data']['to']) for e in journal.read(self.setup, 'AG01') if e['kind'] == 'state']
        self.assertEqual(states, [('SIM', 'DEMO'), ('DEMO', 'LIVE'), ('LIVE', 'SUSPENDED'), ('SUSPENDED', 'SIM')])

    def test_dead_is_a_persons_call_and_the_end(self):
        for by in ('parity-deriva', None, ''):
            with self.assertRaises(cards.CardError) as caught:
                cards.move(self.setup, self.card['id'], 'DEAD', 'three losses', by)
            self.assertIn("the user's call", str(caught.exception))
        dead = cards.move(self.setup, self.card['id'], 'DEAD', 'no edge left', 'rr')
        self.assertEqual(dead['state'], 'DEAD')
        entry = journal.read(self.setup, 'AG01')[-1]
        self.assertEqual((entry['kind'], entry['by'], entry['data']['why']), ('state', 'rr', 'no edge left'))
        with self.assertRaises(cards.CardError):
            cards.move(self.setup, self.card['id'], 'SIM', 'second thoughts', 'rr')


class PushTest(unittest.TestCase):

    def test_the_card_goes_with_the_record_to_a_real_money_server_which_keeps_a_copy(self):
        archive, real = server(self), server(self)
        saveSet(archive)
        archive.addFavourite({'kind': 'sweep', 'id': SWEEP, 'n': 1})
        card = cards.make(archive.setup, dict(FIELDS, slScale='1'))
        sent = []

        def rpc(upstream, name, args, timeout=180):
            sent.append((name, args))
            if name == 'live_status':
                return {'server': {'accounts': 'real'}, 'sessions': []}
            if name == 'push_record':
                with mock.patch.object(livesessions.settings, 'ACCOUNTS', 'real'):
                    return mcp.call(real, name, args, 'https://real', 'promote: archive')
            return {'saved': True}
        archive.saveTradeServer({'name': 'real', 'url': 'https://real/mcp', 'token': 't'})
        with mock.patch.object(sources, 'rpc', rpc), mock.patch.object(sync, 'rpc', rpc):
            told = archive.pushForm({'server': 'real', 'fields': dict(FIELDS, slScale='1')})
        record = [args for name, args in sent if name == 'push_record'][0]['record']
        self.assertEqual(record['card']['id'], card['id'])
        self.assertTrue(told['gate'])
        kept = cards.get(real.setup, card['id'])
        self.assertEqual((kept['label'], kept['state']), ('AG01 v1', 'SIM'))
        self.assertEqual(real.live.promoted(dict(FIELDS, slScale='1'))['card']['id'], card['id'])
        pushed = journal.read(archive.setup, 'AG01')[-1]
        self.assertEqual((pushed['kind'], pushed['data']['server'], pushed['data']['card'], pushed['version']),
                         ('push', 'real', 'AG01 v1', card['id']))
        # a form with no card still goes to a demo server, and the journal says "no gate" (D6)
        sent.clear()

        def demo(upstream, name, args, timeout=180):
            return {'server': {'accounts': 'demo'}, 'sessions': []} if name == 'live_status' else {'saved': True}
        archive.saveTradeServer({'name': 'demo', 'url': 'https://demo/mcp', 'token': 't'})
        with mock.patch.object(sources, 'rpc', demo), mock.patch.object(sync, 'rpc', demo):
            told = archive.pushForm({'server': 'demo', 'fields': dict(FIELDS, slScale='2')})
        self.assertFalse(told['gate'])
        self.assertFalse(journal.read(archive.setup, 'AG01')[-1]['data']['gate'])


if __name__ == '__main__':
    unittest.main()
