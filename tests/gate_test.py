"""
Tests for performance/gate.py and web/holdout.py: each check of the gate that
passes and that fails; the holdout's cut fixed by the first run, one for every
timeframe of an instrument, a strategy's own for it alone, moved only by hand
and never to less than HOLDOUT_MIN_DAYS, its openings counted.
"""

import os
import shutil
import tempfile
import types
import unittest

import datetime
from decimal import Decimal
from unittest import mock

from parity_deriva.performance import gate
from parity_deriva.tests.web_test import StoreCase
from parity_deriva.web import cards, holdout, journal
from parity_deriva.web.service import Service, ServiceError

DAY = 86400000
FIRST = holdout.millis('2015-01-01')
LAST = holdout.millis('2026-09-18')


def trades(*pls):
    return [{'pl': pl} for pl in pls]


class ChecksTest(unittest.TestCase):

    def dev(self, pls):
        return {'trades': trades(*pls), 'report': {'net': sum(pls)}, 'kpi': {'maxDrawdownPct': 8.0}}

    def test_a_development_that_passes_every_check(self):
        pls = [30.0, -10.0, 25.0, -10.0, 20.0] * 30
        rows = gate.development(self.dev(pls), [{'n': 2, 'pf': 1.4}, {'n': 4, 'pf': 1.2}], {'percentile': 97.0})
        self.assertEqual([r['check'] for r in rows], [
            'trades', 'profit factor, bootstrap 5th percentile', 'plateau: the neighbouring runs of the set',
            'profit factor without the 3 best trades', 'random entries beaten'])
        self.assertTrue(gate.passed(rows), rows)

    def test_each_check_that_fails(self):
        few = gate.development(self.dev([30.0, -10.0] * 20), [{'n': 2, 'pf': 1.4}], {'percentile': 97.0})
        self.assertEqual([r['ok'] for r in few][0], False)
        luck = gate.development(self.dev([-10.0] * 110 + [600.0, 500.0, 400.0]), [{'n': 2, 'pf': 1.4}],
                                {'percentile': 97.0})
        self.assertEqual({r['check']: r['ok'] for r in luck}['profit factor without the 3 best trades'], False)
        self.assertEqual({r['check']: r['ok'] for r in luck}['profit factor, bootstrap 5th percentile'], False)
        good = [30.0, -10.0, 25.0, -10.0, 20.0] * 30
        peak = gate.development(self.dev(good), [{'n': 2, 'pf': 1.4}, {'n': 4, 'pf': 0.8}], {'percentile': 97.0})
        self.assertEqual(peak[2]['value'], '1 of 2 with PF > 1')
        self.assertFalse(peak[2]['ok'])
        self.assertFalse(gate.development(self.dev(good), [], {'percentile': 97.0})[2]['ok'])
        chance = gate.development(self.dev(good), [{'n': 2, 'pf': 1.4}], {'percentile': 60.0})
        self.assertEqual((chance[4]['ok'], chance[4]['value']), (False, '60th percentile'))
        self.assertFalse(gate.development(self.dev(good), [{'n': 2, 'pf': 1.4}], None)[4]['ok'])

    def test_the_holdout_near_the_development(self):
        dev = self.dev([30.0, -10.0] * 60)
        same = {'trades': trades(*[28.0, -10.0] * 20), 'report': {'net': 360.0}, 'kpi': {'maxDrawdownPct': 9.0}}
        self.assertTrue(gate.passed(gate.holdout(dev, same)))
        loss = {'trades': trades(*[10.0, -12.0] * 20), 'report': {'net': -40.0}, 'kpi': {'maxDrawdownPct': 20.0}}
        self.assertEqual([r['ok'] for r in gate.holdout(dev, loss)], [False, False, False])


class RegistryTest(unittest.TestCase):

    def setUp(self):
        home = tempfile.mkdtemp(prefix='parity-deriva-holdout-')
        self.addCleanup(shutil.rmtree, home, True)
        self.setup = types.SimpleNamespace(DATA_DIR=home, HOLDOUT_SHARE=0.25, HOLDOUT_MIN_DAYS=365)

    def test_the_first_run_fixes_the_cut_for_every_timeframe(self):
        # 11.7 years: the last quarter is the longer
        first = holdout.cut(self.setup, 'EUR_USD', 'M1502-SBR', {'from': FIRST, 'to': LAST})
        self.assertEqual(first, holdout.day(LAST - 0.25 * (LAST - FIRST)))
        # another timeframe, another history, a later bar: the same cut
        self.assertEqual(holdout.cut(self.setup, 'EUR_USD', 'AG01', {'from': FIRST + 400 * DAY, 'to': LAST + 90 * DAY}),
                         first)
        # two years of history: a year aside
        short = holdout.cut(self.setup, 'GBP_USD', 'AG01', {'from': LAST - 800 * DAY, 'to': LAST})
        self.assertEqual(short, holdout.day(LAST - 365 * DAY))
        # too little to keep a year aside and a year to develop on
        self.assertIsNone(holdout.cut(self.setup, 'USD_JPY', 'AG01', {'from': LAST - 500 * DAY, 'to': LAST}))
        self.assertNotIn('USD_JPY', holdout.registry(self.setup))

    def test_a_strategy_of_its_own_and_the_cut_moved_by_hand(self):
        base = holdout.cut(self.setup, 'EUR_USD', 'AG01', {'from': FIRST, 'to': LAST})
        journal.add(self.setup, 'AG01', 'run', 'experiment', {}, {'instrument': 'EUR_USD'})
        own = holdout.move(self.setup, 'EUR_USD', '2024-06-01', LAST, FIRST, strategy='MY-EMA 2', by='rr')
        self.assertEqual((own['cut'], own['own']), ('2024-06-01', True))
        self.assertEqual(holdout.cut(self.setup, 'EUR_USD', 'MY-EMA 3', {'from': FIRST, 'to': LAST}), '2024-06-01')
        self.assertEqual(holdout.cut(self.setup, 'EUR_USD', 'AG01', {'from': FIRST, 'to': LAST}), base)
        self.assertEqual([holdout.opened(self.setup, 'EUR_USD', 'MY-EMA 2') for _ in range(2)], [1, 2])
        self.assertEqual(holdout.status(self.setup, 'EUR_USD', 'AG01')['openings'], 0)
        # back or forward, never less than a year before the last bar
        for when in ('2026-01-01', 'soon', '2014-06-01'):
            with self.assertRaises(holdout.HoldoutError):
                holdout.move(self.setup, 'EUR_USD', when, LAST, FIRST)
        holdout.opened(self.setup, 'EUR_USD', 'AG01')
        moved = holdout.move(self.setup, 'EUR_USD', '2022-01-01', LAST, FIRST, by='rr')
        self.assertEqual((moved['cut'], moved['openings']), ('2022-01-01', 0))
        entry = journal.read(self.setup, 'AG01')[-1]
        self.assertEqual((entry['kind'], entry['data']['cut'], entry['data']['was'], entry['by']),
                         ('holdout', '2022-01-01', base, 'rr'))
        # its own cut dropped: the instrument's again
        holdout.move(self.setup, 'EUR_USD', '', LAST, strategy='MY-EMA 2', by='rr')
        self.assertEqual(holdout.cut(self.setup, 'EUR_USD', 'MY-EMA 2', {'from': FIRST, 'to': LAST}), '2022-01-01')


class ServiceTest(unittest.TestCase):
    """The cut on a real store: 40 days of H1 and H4, a holdout of at least 5 days."""

    START = datetime.datetime(2024, 1, 1)

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='parity-deriva-gate-')
        self.addCleanup(shutil.rmtree, self.home, True)
        store = os.path.join(self.home, 'EUR_USD.hd5')
        StoreCase.store(store, bars=40 * 24, granularity='H1', start=self.START)
        # the helper steps an hour a bar whatever it is called: as long a history
        StoreCase.store(store, bars=40 * 24, granularity='H4', start=self.START)
        self.setup = types.SimpleNamespace(DATA_DIR=self.home, EQUITY=Decimal('100000'), HOLDOUT_MIN_DAYS=5)
        self.service = Service(setup=self.setup, max_candles=100000)

    def run_(self, **more):
        return self.service.backtest(**dict({'instrument': 'EUR_USD', 'granularity': 'H1', 'strategy': 'AG01',
                                             'confirmed': True}, **more))

    def test_a_run_past_the_cut_stops_at_it_and_the_timeframes_share_it(self):
        payload = self.run_()
        cut = payload['holdout']['cut']
        self.assertEqual(cut, holdout.registry(self.setup)['EUR_USD']['cut'])
        self.assertLess(payload['to'], holdout.millis(cut))
        self.assertLess(max(c[0] for c in payload['candles']), holdout.millis(cut))
        # 25% of 40 days is 10: more than the 5 kept aside at least
        window = self.service.window(self.service.check('EUR_USD', 'H1', 'AG01'), 'H1')
        self.assertEqual(cut, holdout.day(window['to'] - 0.25 * (window['to'] - window['from'])))
        four = self.run_(granularity='H4')
        self.assertEqual(four['holdout']['cut'], cut)
        # a run that ends before it is left as it is
        early = self.run_(dtto=datetime.datetime(2024, 1, 20, 23, 59))
        self.assertIsNone(early['holdout'])
        with self.assertRaises(ServiceError) as caught:
            self.run_(dtfrom=datetime.datetime(2024, 2, 2))
        self.assertIn('all in the holdout', str(caught.exception))
        # the gate's run reads it
        read = self.run_(dtfrom=datetime.datetime(2024, 1, 31), readHoldout=True)
        self.assertIsNone(read['holdout'])
        self.assertGreater(read['to'], holdout.millis(cut))

    def saveSet(self):
        fields = {'strategy': 'AG01', 'instrument': 'EUR_USD', 'granularity': 'H1', 'from': '2024-01-01',
                  'to': '2024-02-09', 'balance': '100000', 'risk': '1'}
        self.service.saveSweep({'id': '20260926-120000-abcdef', 'name': 'a set', 'fields': fields, 'total': 3,
                                'grid': {'slScale': '1, 1.5, 2'}, 'finished': 1790000000.0, 'done': [
                                    {'n': n, 'params': {'slScale': v}, 'final': 1.0, 'balance': 1.0,
                                     'report': {'profitFactor': pf, 'closedTrades': 10}}
                                    for n, v, pf in ((1, '1', 1.3), (2, '1.5', 1.4), (3, '2', 0.9))]})
        return '20260926-120000-abcdef'

    def test_the_neighbours_are_one_step_of_one_parameter(self):
        job = self.service.savedSweep(self.saveSet())
        self.assertEqual([r['n'] for r in self.service.neighbours(job, 2)], [1, 3])
        self.assertEqual([r['n'] for r in self.service.neighbours(job, 1)], [2])

    def test_the_gate_opens_the_holdout_once_a_version(self):
        sweep = self.saveSet()
        self.run_()
        # the development fails: the holdout stays shut
        failed = self.service.gate(sweep, 2, by='rr')
        self.assertFalse(failed['ok'])
        self.assertIsNone(failed['holdout'])
        card = cards.get(self.setup, failed['card']['id'])
        self.assertEqual((card['state'], card['holdout'], card['reference']), ('SIM', None, None))
        self.assertEqual(holdout.registry(self.setup)['EUR_USD']['openings'], 0)
        # made to pass: the holdout read once, counted, on the card with its reference
        passing = [{'check': 'x', 'ok': True, 'value': 1, 'need': '1'}]
        with mock.patch.object(gate, 'development', lambda *a, **k: passing), \
                mock.patch.object(gate, 'holdout', lambda *a, **k: passing):
            opened = self.service.gate(sweep, 1, by='rr')
            self.assertTrue(opened['ok'])
            self.assertEqual(opened['openings'], 1)
            card = cards.get(self.setup, opened['card']['id'])
            self.assertTrue(card['holdout']['opened'])
            self.assertEqual(card['reference']['sample']['from'], holdout.millis('2024-01-01'))
            self.assertIn('p5', card['reference']['band'])
            with self.assertRaises(ServiceError) as caught:
                self.service.gate(sweep, 1, by='rr')
            self.assertIn('reads it once', str(caught.exception))
            # another version - another step of the grid - opens it again, and the count says so
            self.assertEqual(self.service.gate(sweep, 3, by='rr')['openings'], 2)
        # no DEAD by the gate, and every verdict in the journal
        self.assertTrue(all(c['state'] == 'SIM' for c in cards.cards(self.setup, 'AG01')))
        gates = [e for e in journal.read(self.setup, 'AG01') if e['kind'] == 'gate']
        self.assertEqual([e['data']['ok'] for e in gates], [False, True, True])

    def test_the_sets_that_read_past_the_cut_are_told(self):
        sweep = self.saveSet()
        cut = self.run_()['holdout']['cut']
        told = self.service.holdoutReadBy('EUR_USD', cut)
        # the set asked to 2024-02-09, from before the cut was fixed; the run was stopped at it
        self.assertEqual((told['sets'], told['runs'], told['until']), (1, 0, '2024-02-09'))
        held = self.service.holdouts()['instruments'][0]
        self.assertEqual((held['instrument'], held['cut'], held['readBy']['sets']), ('EUR_USD', cut, 1))
        with self.assertRaises(ServiceError):
            self.service.moveHoldout({'instrument': 'EUR_USD', 'cut': '2024-02-08'})
        moved = self.service.moveHoldout({'instrument': 'EUR_USD', 'cut': '2024-01-25'}, by='rr')
        self.assertEqual(moved['instruments'][0]['cut'], '2024-01-25')
        self.assertEqual(self.run_()['holdout']['cut'], '2024-01-25')
        self.assertIsNotNone(sweep)


if __name__ == '__main__':
    unittest.main()
