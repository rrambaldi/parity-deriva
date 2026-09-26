"""
Tests for data/archive.py: the bars the brokers served, recorded, archived
as stores a provider, and set against the downloaded ones.
"""

import datetime
import os
import random
import shutil
import tempfile
import types
import unittest
from decimal import Decimal
from unittest import mock

import pandas as pd

from parity_deriva.data import archive, market
from parity_deriva.event.event import CandleEvent
from parity_deriva.tests.web_test import StoreCase
from parity_deriva.web.service import Service, ServiceError

T0 = datetime.datetime(2026, 9, 21, 0, 0)


def bar(when, price, sides=True, complete=True):
    legs = {'o': price, 'h': price + 0.0005, 'l': price - 0.0005, 'c': price}
    data = {'time': when.strftime('%Y-%m-%dT%H:%M:%S.000000000Z'), 'volume': 3,
            'complete': complete, 'mid': legs}
    if sides:
        data['ask'] = dict((k, v + 0.00005) for k, v in legs.items())
        data['bid'] = dict((k, v - 0.00005) for k, v in legs.items())
    event = CandleEvent(data)
    event.instrument, event.granularity = 'EUR_USD', 'M5'
    return event


class ArchiveTest(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='parity-deriva-archive-')
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.service = Service(setup=types.SimpleNamespace(DATA_DIR=self.folder,
                                                           EQUITY=Decimal('100000.00')), max_candles=5000)

    def path(self, provider):
        return os.path.join(self.folder, 'archive', provider, 'EUR_USD.hd5')

    def test_the_bars_served_go_into_the_archive_a_provider_once(self):
        db = self.service.candles
        for i in range(4):
            db.write(bar(T0 + datetime.timedelta(minutes=5 * i), 1.17 + i / 10000.0), 'oanda', 'record')
        # a feed of one series: its mid is its ask and its bid, as in a store
        db.write(bar(T0, 1.2, sides=False), 'twelvedata', 'paper')
        lines = []
        archive.compact(self.service, lines.append)
        self.assertEqual(sorted(lines), ['archive oanda EUR_USD M5: 4 bars',
                                         'archive twelvedata EUR_USD M5: 1 bars'])
        held = pd.read_hdf(self.path('oanda'), 'M5')
        self.assertEqual(len(held), 4)
        self.assertAlmostEqual(held['ask_c'].iloc[-1] - held['bid_c'].iloc[-1], 0.0001)
        paper = pd.read_hdf(self.path('twelvedata'), 'M5')
        self.assertEqual(paper['ask_c'].iloc[0], paper['mid_c'].iloc[0])
        self.assertEqual(market.archives(self.service.setup), ['oanda', 'twelvedata'])
        # once is once: the next compaction takes only what came after
        db.write(bar(T0 + datetime.timedelta(minutes=20), 1.18), 'oanda', 'record')
        lines = []
        archive.compact(self.service, lines.append)
        self.assertEqual(lines, ['archive oanda EUR_USD M5: 1 bars'])

    def test_a_feed_is_recorded_with_no_session_and_no_bar_still_forming(self):
        from parity_deriva.trading import providers
        asked = []

        def history(provider, instrument, granularity, since=None, bars=500):
            asked.append(since)
            return [bar(T0, 1.17), bar(T0 + datetime.timedelta(minutes=5), 1.171, complete=False)]
        kept = market.save({'record': {'feeds': ['oanda  eur_usd m5'], 'every': 5}}, self.service.setup)
        self.assertEqual(kept['record'], {'feeds': ['oanda eur_usd m5'], 'every': 5})
        with mock.patch.object(providers, 'get_provider', lambda name, setup: object()), \
                mock.patch.object(providers, 'history', history):
            lines = []
            archive.record(self.service, kept, lines.append)
            archive.record(self.service, kept, lines.append)
        self.assertEqual(lines[0], 'oanda EUR_USD M5: 1 bars recorded')
        self.assertEqual(asked[1], datetime.datetime(2026, 9, 21, 0, 0))
        self.assertEqual([f['feed'] for f in self.service.candles.feeds('EUR_USD', 'M5')], ['oanda:record'])
        for bad in ({'feeds': ['nobody EUR_USD M5']}, {'feeds': ['oanda EUR_USD']}, {'feeds': [], 'every': 0}):
            with self.assertRaises(market.MarketError):
                market.save({'record': bad}, self.service.setup)
        # nothing to record, nothing to run
        market.save({'record': {'feeds': []}}, self.service.setup)
        with self.assertRaises(market.MarketError):
            self.service.sources.trigger('record')


class CompareTest(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='parity-deriva-compare-')
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.setup = types.SimpleNamespace(DATA_DIR=self.folder, EQUITY=Decimal('100000.00'))
        os.makedirs(os.path.join(self.folder, 'archive', 'oanda'))

    def test_two_sources_bar_by_bar_with_their_clock_and_their_days(self):
        index = pd.date_range(T0, periods=3 * 288, freq='5min')
        # a walk of its own, seeded: moves no shift of it can repeat
        rng = random.Random(7)
        walk = [1.17]
        for _ in range(len(index) - 1):
            walk.append(walk[-1] + rng.choice((-1, 1)) * rng.randint(1, 5) / 10000.0)
        prices = pd.Series(walk, index=index)

        def frame(series, spread):
            data = {'volume': 5}
            for side, shift in (('ask', spread / 2), ('bid', -spread / 2), ('mid', 0.0)):
                for leg in 'ohlc':
                    data['%s_%s' % (side, leg)] = series + shift
            return pd.DataFrame(data, index=series.index)
        frame(prices, 0.0001).to_hdf(os.path.join(self.folder, 'EUR_USD.hd5'), key='M5', format='table')
        # the broker's clock a bar late, half a pip high, a bar short, a wild second day
        served = prices.copy()
        served.index = served.index + pd.Timedelta('5min')
        served = served + 0.00005
        served[(served.index >= T0 + datetime.timedelta(days=1)) &
               (served.index < T0 + datetime.timedelta(days=2))] += 0.003
        served = served.drop(served.index[10])
        frame(served, 0.0002).to_hdf(os.path.join(self.folder, 'archive', 'oanda', 'EUR_USD.hd5'),
                                     key='M5', format='table')
        got = archive.compare(self.setup, 'EUR_USD', 'M5', '', 'oanda')
        self.assertEqual(got['shift'], -1)
        # set against each other a bar back: only the bar dropped is A's alone
        self.assertEqual((got['bars']['onlyA'], got['bars']['onlyB']), (1, 0))
        self.assertEqual(got['dClose']['median'], 0.5)
        self.assertEqual((got['spread']['a'], got['spread']['b']), (1.0, 2.0))
        self.assertEqual(len(got['days']), 3)
        self.assertEqual([d['day'] for d in got['standout']],
                         [int(pd.Timestamp(T0 + datetime.timedelta(days=1)).value // 10 ** 6)])
        with self.assertRaises(market.MarketError):
            archive.compare(self.setup, 'EUR_USD', 'M5', '', 'ig')
        # a level off by a few pips for a day is a day that stands out, not a clock
        offset = prices + 0.00005
        offset[(offset.index >= T0 + datetime.timedelta(days=1)) &
               (offset.index < T0 + datetime.timedelta(days=2))] += 0.0004
        frame(offset, 0.0002).to_hdf(os.path.join(self.folder, 'archive', 'oanda', 'EUR_USD.hd5'),
                                     key='M5', format='table')
        got = archive.compare(self.setup, 'EUR_USD', 'M5', '', 'oanda')
        self.assertEqual((got['shift'], got['bars']['onlyA'], len(got['standout'])), (0, 0, 1))


class ArchiveRunTest(unittest.TestCase):
    """The same form on the stores and on an archive: what the candles do to the trades."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='parity-deriva-impact-')
        self.addCleanup(shutil.rmtree, self.folder, True)
        StoreCase.store(os.path.join(self.folder, 'EUR_USD.hd5'))
        os.makedirs(os.path.join(self.folder, 'archive', 'oanda'))
        StoreCase.store(os.path.join(self.folder, 'archive', 'oanda', 'EUR_USD.hd5'))
        self.service = Service(setup=types.SimpleNamespace(DATA_DIR=self.folder,
                                                           EQUITY=Decimal('100000.00')), max_candles=1000)

    def test_a_run_reads_the_archive_it_is_asked_for_and_the_impact_compares_them(self):
        fields = {'instrument': 'EUR_USD', 'granularity': 'H1', 'strategy': 'AG01'}
        here = self.service.backtest(confirmed=True, instrument='EUR_USD', granularity='H1')
        there = self.service.backtest(confirmed=True, instrument='EUR_USD', granularity='H1', data='oanda')
        self.assertEqual(len(here['trades']), len(there['trades']))
        with self.assertRaises(ServiceError):
            self.service.backtest(confirmed=True, instrument='EUR_USD', granularity='H1', data='ig')
        # the thread's setup is the service's again after it
        self.assertIsInstance(self.service.setup, types.SimpleNamespace)
        # a different archive, different trades: a run on it is not the cached one
        path = os.path.join(self.folder, 'archive', 'oanda', 'EUR_USD.hd5')
        held = pd.read_hdf(path, 'H1')
        held[[c for c in held.columns if c != 'volume']] += 0.003 * (pd.Series(range(len(held)), index=held.index) % 2).values[:, None]
        held.to_hdf(path, key='H1', format='table')
        self.service.addFavourite({'kind': 'run', 'id': self.saved(fields)})
        favourite = self.service.favourites()[0]['id']
        got = self.service.impact({'favourite': favourite, 'a': '', 'b': 'oanda'})
        self.assertEqual((got['a']['source'], got['b']['source']), ('', 'oanda'))
        self.assertFalse(got['same'], got)
        self.assertEqual(self.service.impact({'favourite': favourite, 'a': '', 'b': ''})['same'], True)

    def saved(self, fields):
        payload = self.service.backtest(confirmed=True, instrument='EUR_USD', granularity='H1')
        self.service.saveRun(fields, payload)
        return self.service.runId(fields)


if __name__ == '__main__':
    unittest.main()
