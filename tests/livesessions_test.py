"""
web/livesessions.py: a session is a process and a folder, read back as trades.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from parity_deriva.web import livesessions

FAKE = """
import json, sys, time
events = sys.argv[sys.argv.index('--events') + 1]
with open(events + '-20260924.log', 'w') as handle:
    for row in (
        {'_type': 'CANDLE', 'time': '2026-09-24T10:00:00', 'mid': {'c': 1.1}, 'instrument': 'EUR_USD'},
        {'_type': 'SIGNAL'}, {'_type': 'ORDER'},
        {'_type': 'TRANSACTION', 'type': 'ORDER_FILL', 'dealId': 'D1', 'price': 1.1,
         'units': 2.0, 'time': '2026-09-24T11:00:00'},
        {'_type': 'TRANSACTION', 'type': 'ORDER_FILL', 'dealId': 'D1', 'pl': -12.5,
         'tradesClosed': [{'price': 1.098}], 'time': '2026-09-24T12:00:00'},
        {'_type': 'TRANSACTION', 'type': 'ORDER_FILL', 'dealId': 'D2', 'price': 1.2,
         'units': -1.0, 'time': '2026-09-24T13:00:00'}):
        handle.write(json.dumps(row) + '\\n')
print('fake session up', flush=True)
time.sleep(60)
"""

TARGETS = [{'provider': 'ig', 'configured': True, 'error': None, 'capabilities': {},
            'accounts': [{'id': 'Z1', 'name': 'CFD', 'currency': 'EUR',
                          'balance': 1000.0, 'demo': True}]}]


class LiveSessionsTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.script = os.path.join(self.root, 'fake.py')
        with open(self.script, 'w') as handle:
            handle.write(FAKE)
        self.live = livesessions.LiveSessions(os.path.join(self.root, 'live'))
        self.live.targets = lambda fresh=False: TARGETS
        self.live.check = lambda fields, provider: None

    def tearDown(self):
        for session in self.live.sessions():
            if session['running']:
                self.live.stop(session['id'])
        shutil.rmtree(self.root)

    def test_started_read_and_stopped(self):
        with mock.patch.object(livesessions, 'SCRIPT', self.script):
            started = self.live.start({'strategy': 'AB', 'inverse': '1', 'balance': '1000'},
                                      [{'provider': 'ig', 'account': 'Z1'}])
        session = started[0]['id']
        # the backtest's capital, as every account's reference
        self.assertEqual(self.live.meta(session)['fields']['capital'], '1000')
        for _ in range(50):
            if self.live.summary(session)['closed']:
                break
            time.sleep(0.1)
        got = self.live.summary(session, detail=True)
        self.assertTrue(got['running'])
        self.assertEqual((got['signals'], got['orders']), (1, 1))
        self.assertEqual([t['pl'] for t in got['closed']], [-12.5])
        self.assertEqual([t['deal'] for t in got['open']], ['D2'])
        self.assertEqual(got['net'], -12.5)
        self.assertEqual(got['curve'][-1][1], 987.5)
        self.assertIn('fake session up', '\n'.join(got['console']))
        # the menu's light: one process up
        self.assertEqual(self.live.running(), 1)
        stopped = self.live.stop(session)
        for _ in range(50):
            if not self.live.summary(session)['running']:
                break
            time.sleep(0.1)
        self.assertFalse(self.live.summary(session)['running'])
        self.assertEqual(self.live.running(), 0)
        self.assertIsNotNone(stopped['stopped'])
        self.live.delete(session)
        self.assertEqual(self.live.sessions(), [])

    def test_a_session_never_stopped_comes_back_after_a_restart(self):
        with mock.patch.object(livesessions, 'SCRIPT', self.script):
            session = self.live.start({'strategy': 'AB', 'inverse': '1'},
                                      [{'provider': 'ig', 'account': 'Z1'}])[0]['id']
            # no capital and no backtest's: the default equity, on every account
            self.assertEqual(float(self.live.meta(session)['fields']['capital']),
                             float(livesessions.settings.EQUITY))
            first = self.live.meta(session)['pid']
            # what systemctl restart does: the process goes, the service is new
            os.killpg(first, 9)
            self.live.children[session].wait()
            again = livesessions.LiveSessions(self.live.root)
            self.assertFalse(again.summary(session)['running'])
            self.assertEqual(again.resume(), [session])
            self.assertTrue(again.summary(session)['running'])
            self.assertNotEqual(again.meta(session)['pid'], first)
            self.assertEqual(again.resume(), [])
            again.stop(session)
            again.children[session].wait()
            self.assertEqual(livesessions.LiveSessions(self.live.root).resume(), [])
            self.live = again

    def test_an_account_the_credentials_do_not_reach_is_refused(self):
        with self.assertRaises(livesessions.LiveError):
            self.live.start({}, [{'provider': 'ig', 'account': 'nope'}])
        with self.assertRaises(livesessions.LiveError):
            self.live.meta('../../etc')


SIGNAL = 'X:EUR_USD:M5:20260924T100000'


def simulatorLog():
    """
    What the simulator's fills look like once backtest/offline.SimulatedBroker
    has promoted them: orderID on both ends, the level in `price`, the P&L on
    the close, no dealId anywhere.
    """
    return [
        {'_type': 'TRANSACTION', 'type': 'ORDER_FILL', 'orderID': 7, 'price': 1.1,
         'units': 2.0, 'time': '2026-09-24T11:00:00', 'signalNumber': SIGNAL},
        {'_type': 'TRANSACTION', 'type': 'ORDER_FILL', 'orderID': 7, 'pl': -12.5,
         'price': 1.098, 'reason': 'STOP_LOSS_ORDER', 'time': '2026-09-24T12:00:00',
         'tradesClosed': [{'tradeID': 7, 'realizedPL': -12.5}], 'signalNumber': SIGNAL},
    ]


def shadowLog():
    return [
        {'_type': 'SIMULATEDFILL', 'orderID': 1, 'price': 1.1001, 'units': 2.0,
         'time': '2026-09-24T11:00:00', 'signalNumber': SIGNAL},
        {'_type': 'SIMULATEDFILL', 'orderID': 1, 'pl': -12.3, 'price': 1.0981,
         'reason': 'STOP_LOSS_ORDER', 'time': '2026-09-24T12:00:00',
         'tradesClosed': [{'tradeID': 1}], 'signalNumber': SIGNAL},
        {'_type': 'STATUS', 'status': 'PARITY', 'kind': 'slippage', 'key': SIGNAL,
         'detail': '0.0001 > 0.00005', '_created': '2026-09-24T12:00:01'},
        {'_type': 'STATUS', 'status': 'PARITY_ALARM', 'breaches': ['3 > 2'],
         '_created': '2026-09-24T12:00:02'},
    ]


class ReadTest(unittest.TestCase):

    def test_the_simulators_fills_pair_up_on_their_order_id(self):
        """
        Was: the deal key was dealId or id, so the simulator's fills - which
             carry neither - all keyed None, the close popped nothing and
             every trade came out with no entry and no exit.
        Now: orderID is the third key, and the close's own price is the exit
             where tradesClosed states none.
        """
        got = livesessions.read(simulatorLog())
        self.assertEqual(len(got['closed']), 1)
        trade = got['closed'][0]
        self.assertEqual((trade['deal'], trade['entry'], trade['exit'], trade['pl']),
                         (7, 1.1, 1.098, -12.5))
        self.assertEqual(trade['signal'], SIGNAL)
        self.assertEqual(trade['reason'], 'STOP_LOSS_ORDER')
        self.assertEqual(got['open'], [])
        self.assertEqual(got['net'], -12.5)

    def test_the_shadow_and_the_parity_findings_are_read_too(self):
        got = livesessions.read(simulatorLog() + shadowLog())
        self.assertEqual(len(got['simClosed']), 1)
        self.assertEqual(got['simClosed'][0]['entry'], 1.1001)
        self.assertEqual(got['parity']['divergences'], 1)
        self.assertEqual(got['parity']['byKind'], {'slippage': 1})
        self.assertEqual(got['parity']['alarms'], ['3 > 2'])
        self.assertEqual(got['parity']['last']['kind'], 'slippage')
        # the errors count is untouched by the new statuses
        self.assertEqual(got['errors'], 0)

    def test_on_the_paper_account_the_shadow_side_is_left_empty(self):
        """The log holds the simulator's fills twice there: once is enough."""
        got = livesessions.read(simulatorLog() + shadowLog(), paper=True)
        self.assertEqual(got['simClosed'], [])
        self.assertEqual(len(got['closed']), 1)

    def test_is_paper(self):
        self.assertTrue(livesessions.isPaper('twelvedata'))
        self.assertFalse(livesessions.isPaper('ig'))
        self.assertFalse(livesessions.isPaper('nobody'))


class TradeSkewTest(unittest.TestCase):

    FIELDS = {'strategy': 'X', 'instrument': 'EUR_USD', 'granularity': 'M5',
              'risk': '1', 'from': '2020-01-01', 'confirmed': '1'}

    def session(self, id, provider, account, closed, fields=None):
        return {'id': id, 'provider': provider, 'account': account, 'running': True,
                'fields': dict(self.FIELDS, **(fields or {})), 'closed': closed,
                'parity': {'divergences': 0, 'byKind': {}, 'alarms': []}}

    def trade(self, signal, entry, exit, pl, reason='STOP_LOSS_ORDER', opened=1000):
        return {'signal': signal, 'entry': entry, 'exit': exit, 'pl': pl,
                'reason': reason, 'opened': opened, 'time': opened + 60000, 'units': 1}

    def test_the_form_is_the_group_and_the_window_is_not(self):
        a = livesessions.groupKey(self.FIELDS)
        b = livesessions.groupKey(dict(self.FIELDS, **{'from': '2021-01-01', 'balance': '5'}))
        c = livesessions.groupKey(dict(self.FIELDS, risk='2'))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(a, 'X|EUR_USD|M5|risk=1')

    def test_pairs_on_the_signal_and_measures_in_pips(self):
        sim = self.session('p', 'twelvedata', 'paper', [
            self.trade('S1', 1.1000, 1.1020, 20.0, 'TAKE_PROFIT_ORDER'),
            self.trade('S2', 1.2000, 1.1990, -10.0),
            self.trade('S3', 1.3000, 1.3010, 10.0, 'TAKE_PROFIT_ORDER')])
        ig = self.session('i', 'ig', 'Z1', [
            self.trade('S1', 1.1001, 1.1020, 19.0, 'TAKE_PROFIT_ORDER', opened=1500),
            self.trade('S2', 1.2000, 1.1985, -15.0, 'STOP_LOSS_ORDER'),
            self.trade('S4', 1.4000, 1.4010, 10.0)])
        got = livesessions.tradeSkew([ig, sim], setup=None)
        self.assertEqual(len(got['groups']), 1)
        group = got['groups'][0]
        self.assertEqual(group['reference']['id'], 'p')
        self.assertEqual(group['pip'], 0.0001)
        self.assertEqual([s['id'] for s in group['sessions']], ['i'])
        rows = dict((r['signal'], r) for r in group['sessions'][0]['rows'])
        self.assertEqual(rows['S1']['entryDiff'], 1.0)
        self.assertEqual(rows['S1']['exitDiff'], 0.0)
        self.assertEqual(rows['S1']['plDiff'], -1.0)
        self.assertTrue(rows['S1']['outcomeMatch'])
        self.assertEqual(rows['S1']['entryLagMs'], 500)
        self.assertEqual(rows['S2']['exitDiff'], -5.0)
        self.assertEqual(rows['S3']['unpaired'], 'broker')
        self.assertIsNone(rows['S3']['broker'])
        self.assertEqual(rows['S4']['unpaired'], 'sim')
        summary = group['sessions'][0]['summary']
        self.assertEqual((summary['paired'], summary['unpairedBroker'],
                          summary['unpairedSim']), (2, 1, 1))
        self.assertEqual(summary['meanEntryDiff'], 0.5)
        self.assertEqual(summary['maxEntryDiff'], 1.0)
        self.assertEqual(summary['outcomeMismatch'], 0)
        self.assertEqual(summary['plDiff'], -6.0)

    def test_the_p_and_l_difference_in_money_pips_and_percent(self):
        # the same capital on both: a long filled 1 pip worse and closed
        # 2 pips worse is 3 pips less, 30 of money on 10 units a pip
        sim = self.session('p', 'twelvedata', 'paper',
                           [dict(self.trade('S1', 1.1000, 1.1020, 200.0), side=1)],
                           fields={'capital': '10000'})
        ig = self.session('i', 'ig', 'Z1',
                          [dict(self.trade('S1', 1.1001, 1.1018, 170.0), side=1)],
                          fields={'capital': '10000'})
        # a short: the broker's worse exit is a higher price
        sim['closed'].append(dict(self.trade('S2', 1.2000, 1.1980, 200.0, opened=2000), side=-1))
        ig['closed'].append(dict(self.trade('S2', 1.2000, 1.1985, 150.0, opened=2000), side=-1))
        session = livesessions.tradeSkew([ig, sim])['groups'][0]['sessions'][0]
        rows = dict((r['signal'], r) for r in session['rows'])
        self.assertEqual(rows['S1']['plDiff'], -30.0)
        self.assertEqual(rows['S1']['plDiffPips'], -3.0)
        self.assertEqual(rows['S1']['plDiffPct'], -0.3)
        self.assertEqual(rows['S2']['plDiffPips'], -5.0)
        self.assertEqual(session['summary']['plDiffPips'], -8.0)
        self.assertEqual(session['summary']['plDiff'], -80.0)
        self.assertEqual(session['summary']['plDiffPct'], -0.8)

    def test_a_trade_s_side_is_its_opening_fill_s(self):
        events = [
            {'_type': 'TRANSACTION', 'type': 'ORDER_FILL', 'id': 'D1', 'price': 1.1,
             'units': -2.0, 'time': '2026-09-24T11:00:00'},
            {'_type': 'TRANSACTION', 'type': 'ORDER_FILL', 'id': 'D1', 'price': 1.09,
             'units': 2.0, 'pl': 2.0, 'tradesClosed': [{'price': 1.09}],
             'time': '2026-09-24T12:00:00'}]
        _open, closed = livesessions.fills(events, 'TRANSACTION')
        self.assertEqual(closed[0]['side'], -1)

    def test_without_a_paper_session_there_is_no_reference(self):
        ig = self.session('i', 'ig', 'Z1', [self.trade('S1', 1.1, 1.2, 1.0)])
        group = livesessions.tradeSkew([ig])['groups'][0]
        self.assertIsNone(group['reference'])
        self.assertEqual(group['sessions'][0]['rows'], [])
        self.assertIsNone(group['sessions'][0]['summary'])

    def test_two_forms_are_two_groups(self):
        a = self.session('a', 'ig', 'Z1', [])
        b = self.session('b', 'ig', 'Z1', [], fields={'strategy': 'Y'})
        self.assertEqual(len(livesessions.tradeSkew([a, b])['groups']), 2)


class CheckTest(unittest.TestCase):

    def test_the_live_script_refusals_are_given_before_a_start(self):
        live = livesessions.LiveSessions(tempfile.mkdtemp())
        base = {'strategy': 'AB', 'inverse': '1', 'instrument': 'EUR_USD',
                'granularity': 'H1'}
        self.assertEqual(live.check(base, 'ig')['strategy'], 'AB')
        # the clock rules AB's engine applies go live, the hours it has not
        self.assertEqual(live.check(dict(base, maxBars='5', intraday='1'), 'ig')['maxBars'], 5)
        with self.assertRaises(livesessions.LiveError):
            live.check(dict(base, session='07:00-16:00'), 'ig')
        # eToro expires orders itself (data/etoro.py), which is what AB needs
        self.assertEqual(live.check(base, 'etoro')['strategy'], 'AB')
        # AB goes live turned round only; the old name is AB turned round
        with self.assertRaises(livesessions.LiveError):
            live.check(dict(base, inverse=''), 'ig')
        self.assertTrue(live.check(dict(base, strategy='AB-INVERSA', inverse=''),
                                   'ig')['inverse'])
        # its engine's stop moves are not sent to the account
        with self.assertRaises(livesessions.LiveError):
            live.check(dict(base, trailing='1'), 'ig')
        # a stop that follows needs a provider that can move one: IG cannot
        plain = {'strategy': 'AG01', 'instrument': 'EUR_USD', 'granularity': 'H1'}
        live.check(plain, 'ig')
        with self.assertRaises(livesessions.LiveError):
            live.check(dict(plain, trailing='1'), 'ig')
        with self.assertRaises(livesessions.LiveError):
            live.check(dict(plain, trailProfit='1'), 'ig')
        live.check(dict(plain, trailing='1'), 'oanda')


if __name__ == '__main__':
    unittest.main()
