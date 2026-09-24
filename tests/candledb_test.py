"""
data/candledb.py: every provider's candles side by side, and the skew read
off them.
"""

import datetime
import os
import tempfile
import types
import unittest

from parity_deriva.data.candledb import CandleDB, CandleRecorder
from parity_deriva.event.event import CandleEvent

T0 = datetime.datetime(2026, 9, 24, 10, 0)
CFG = types.SimpleNamespace(INSTRUMENT_PRECISION={'EUR_USD': 5}, DEFAULT_PRICE_PRECISION=5)


def bar(when, price, sides=('mid', 'bid', 'ask'), spread=0.0):
    ohlc = {'o': price, 'h': price + 0.0002, 'l': price - 0.0002, 'c': price}
    data = {'time': when, 'volume': 3, 'complete': True}
    for side in sides:
        shift = {'mid': 0.0, 'bid': -spread / 2, 'ask': spread / 2}[side]
        data[side] = dict((k, v + shift) for k, v in ohlc.items())
    event = CandleEvent(data)
    event.instrument, event.granularity = 'EUR_USD', 'M5'
    return event


class CandleDBTest(unittest.TestCase):

    def setUp(self):
        self.db = CandleDB(os.path.join(tempfile.mkdtemp(), 'live', 'candles.db'))

    def fill(self, feeds, bars=4, skip=()):
        for i in range(bars):
            when = T0 + datetime.timedelta(minutes=5 * i)
            for provider, account, price, spread in feeds:
                if (provider, i) in skip:
                    continue
                self.db.write(bar(when, price, spread=spread), provider, account, 's',
                              received=when + datetime.timedelta(minutes=5, seconds=2))

    def test_new_same_revised(self):
        self.assertEqual(self.db.write(bar(T0, 1.1), 'ig', 'Z1'), 'new')
        self.assertEqual(self.db.write(bar(T0, 1.1), 'ig', 'Z1'), 'same')
        self.assertEqual(self.db.write(bar(T0, 1.1005), 'ig', 'Z1'), 'revised')
        rows = self.db.select('EUR_USD', 'M5')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['mid_c'], 1.1005)
        self.assertEqual(rows[0]['volume'], 3)

    def test_a_mid_only_feed_quotes_its_mid_for_bid_and_ask(self):
        self.db.write(bar(T0, 1.1, sides=('mid',)), 'twelvedata', 'paper')
        row = self.db.select('EUR_USD', 'M5')[0]
        self.assertIsNone(row['ask_c'])
        candles = self.db.rows('EUR_USD', 'M5')['feeds'][0]['candles']
        self.assertEqual(len(candles[0]), 9)
        # [t, o, h, l, c, askH, askL, bidH, bidL]: the mid's high for the ask's
        self.assertEqual(candles[0][5], candles[0][2])
        self.assertEqual(candles[0][0], 1790244000000)

    def test_the_paper_feed_is_the_reference_when_there(self):
        self.fill([('ig', 'Z1', 1.1003, 0.0001), ('twelvedata', 'paper', 1.1, 0.0)])
        rows = self.db.rows('EUR_USD', 'M5')
        self.assertEqual(rows['reference'], 'twelvedata:paper')
        self.assertEqual([f['feed'] for f in rows['feeds']], ['twelvedata:paper', 'ig:Z1'])
        self.assertEqual([f['n'] for f in self.db.feeds('EUR_USD', 'M5')], [4, 4])

    def test_skew_in_pips_with_the_missing_bars_counted(self):
        self.fill([('ig', 'Z1', 1.1003, 0.0001), ('twelvedata', 'paper', 1.1, 0.0)],
                  skip=[('ig', 2)])
        skew = self.db.skew('EUR_USD', 'M5', setup=CFG)
        self.assertEqual(skew['reference'], 'twelvedata:paper')
        self.assertEqual(skew['pip'], 0.0001)
        ig = dict((f['feed'], f) for f in skew['feeds'])['ig:Z1']
        self.assertEqual((ig['n'], ig['missing']), (3, 1))
        self.assertEqual((ig['mean'], ig['last'], ig['bias'], ig['max']), (3.0, 3.0, 3.0, 3.0))
        self.assertEqual(ig['spread'], 1.0)
        self.assertEqual(ig['latency'], 2.0)
        self.assertEqual(len(ig['series']), 3)
        self.assertEqual(ig['series'][0][1], 3.0)
        ref = dict((f['feed'], f) for f in skew['feeds'])['twelvedata:paper']
        self.assertIsNone(ref['mean'])
        self.assertEqual(skew['matrix']['feeds'], ['twelvedata:paper', 'ig:Z1'])
        self.assertEqual(skew['matrix']['values'][0][1], 3.0)
        self.assertEqual(skew['matrix']['values'][1][1], 0.0)

    def test_an_empty_window_is_an_empty_answer(self):
        skew = self.db.skew('EUR_USD', 'M5', setup=CFG)
        self.assertEqual((skew['feeds'], skew['reference']), ([], None))
        self.assertEqual(self.db.rows('EUR_USD', 'M5'), {'feeds': [], 'reference': None})

    def test_a_window_is_a_window(self):
        self.fill([('ig', 'Z1', 1.1, 0.0)])
        got = self.db.rows('EUR_USD', 'M5', dtfrom=T0 + datetime.timedelta(minutes=5),
                           dtto=T0 + datetime.timedelta(minutes=10))
        self.assertEqual(len(got['feeds'][0]['candles']), 2)

    def test_api_calls_are_counted_by_utc_day(self):
        self.db.logCall('twelvedata', 'EUR/USD', {'interval': '5min'}, 200, 90)
        self.db.logCall('twelvedata', 'EUR/USD', {'interval': '5min'}, None, 5000)
        self.db.logCall('other', 'X', None, 200, 1)
        self.assertEqual(self.db.spentToday('twelvedata'), 2)
        self.assertEqual(self.db.spentToday('twelvedata',
                                            now=datetime.datetime(2000, 1, 1)), 2)
        self.assertEqual(self.db.spentToday('twelvedata',
                                            now=datetime.datetime(2999, 1, 1)), 0)
        self.assertIsNotNone(self.db.lastCall('twelvedata'))
        self.assertIsNone(self.db.lastCall('nobody'))


class RecorderTest(unittest.TestCase):

    def test_it_records_candles_and_survives_a_broken_database(self):
        folder = tempfile.mkdtemp()
        recorder = CandleRecorder(setup=types.SimpleNamespace(DATA_DIR=folder),
                                  path=os.path.join(folder, 'c.db'),
                                  provider='ig', account='Z1', session='s1')
        recorder.execute_event(bar(T0, 1.1))
        recorder.execute_event(bar(T0, 1.1))
        self.assertEqual(recorder.written, 2)
        rows = recorder.db.select('EUR_USD', 'M5')
        self.assertEqual((rows[0]['provider'], rows[0]['account']), ('ig', 'Z1'))
        # a path nothing can write: logged, counted, and the bus goes on
        recorder.db = CandleDB(os.path.join(folder, 'c.db', 'not', 'a', 'dir.db'))
        with self.assertLogs('parity_deriva.trading.trading', level='ERROR'):
            recorder.execute_event(bar(T0, 1.1))
        self.assertEqual(recorder.failed, 1)

    def test_a_revision_is_said_out_loud(self):
        folder = tempfile.mkdtemp()
        recorder = CandleRecorder(setup=types.SimpleNamespace(DATA_DIR=folder),
                                  path=os.path.join(folder, 'c.db'),
                                  provider='ig', account='Z1')
        recorder.execute_event(bar(T0, 1.1))
        with self.assertLogs('parity_deriva.trading.trading', level='WARNING') as logs:
            recorder.execute_event(bar(T0, 1.2))
        self.assertTrue(any('REVISED' in line for line in logs.output))


if __name__ == '__main__':
    unittest.main()
