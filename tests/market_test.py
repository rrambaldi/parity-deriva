"""
Tests for data/market.py: one market folder that several servers read and
one of them writes.
"""

import json
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

import pandas as pd

from parity_deriva.data import calendar, market, sources
from parity_deriva.etc import settings
from parity_deriva.scripts import import_csv
from parity_deriva.web import service as service_module


def bars(start, count, price=1.17):
    index = pd.date_range(start, periods=count, freq='5min')
    columns = dict(('%s_%s' % (side, part), price) for side in ('ask', 'bid', 'mid') for part in 'ohlc')
    return pd.DataFrame(dict(columns, volume=5), index=index)


class MarketTest(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='parity_deriva-home-')
        self.shared = tempfile.mkdtemp(prefix='parity_deriva-market-')
        for folder in (self.home, self.shared):
            self.addCleanup(shutil.rmtree, folder, True)
        self.setup = types.SimpleNamespace(DATA_DIR=self.home)
        # the scripts read the process's settings, as they do from a shell
        patch = mock.patch.object(settings, 'DATA_DIR', self.home)
        patch.start()
        self.addCleanup(patch.stop)

    def test_without_a_choice_the_data_is_the_servers_own_and_it_writes_it(self):
        kept = market.config(self.setup)
        self.assertEqual((kept['dir'], kept['writer'], kept['candles']['source']), (self.home, True, 'manual'))
        self.assertEqual(calendar.path(self.setup), os.path.join(self.home, 'calendar.csv'))

    def test_a_reader_reads_the_shared_folder_and_writes_nothing_into_it(self):
        market.save({'dir': self.shared, 'writer': False}, self.setup)
        self.assertEqual(market.store('EUR_USD', self.setup), os.path.join(self.shared, 'EUR_USD.hd5'))
        with self.assertRaises(market.MarketError):
            import_csv.merge(market.store('EUR_USD'), '/M5', bars('2025-09-11', 3))
        with self.assertRaises(market.MarketError):
            calendar.save(calendar.empty(), setup=self.setup)
        with self.assertRaises(market.MarketError), calendar.writing(calendar.path(self.setup), self.setup):
            pass
        self.assertEqual(os.listdir(self.shared), [])
        # its own folder is still its own: a store somewhere else is not the market's
        import_csv.merge(os.path.join(self.home, 'X.hd5'), '/M5', bars('2025-09-11', 3))

    def test_the_writer_merges_into_a_copy_that_replaces_the_store(self):
        market.save({'dir': self.shared, 'writer': True}, self.setup)
        path = market.store('EUR_USD')
        import_csv.merge(path, '/M5', bars('2025-09-11', 3))
        # a reader that opened it before the second merge keeps what it opened
        before = open(path, 'rb')
        self.addCleanup(before.close)
        self.assertEqual(import_csv.merge(path, '/M5', bars('2025-09-11 00:15', 2))[:2], (2, 0))
        self.assertNotEqual(os.fstat(before.fileno()).st_ino, os.stat(path).st_ino)
        self.assertEqual(len(pd.read_hdf(path, 'M5')), 5)
        self.assertEqual(sorted(os.listdir(self.shared)), ['.market.lock', 'EUR_USD.hd5'])

    def test_a_write_that_fails_leaves_the_store_as_it_was(self):
        path = os.path.join(self.shared, 'EUR_USD.hd5')
        import_csv.merge(path, '/M5', bars('2025-09-11', 3))
        size = os.path.getsize(path)
        with self.assertRaises(RuntimeError), market.rewrite(path) as work:
            with open(work, 'ab') as handle:
                handle.write(b'half a write')
            raise RuntimeError('killed')
        self.assertEqual(os.path.getsize(path), size)
        self.assertFalse(os.path.exists(path + '.part'))

    def test_the_settings_page_points_the_service_at_the_folder(self):
        service = service_module.Service(setup=self.setup)
        self.assertEqual(service.marketData()['writer'], True)
        for body in ({'dir': 'relative', 'writer': True}, {'dir': '/no/such/folder', 'writer': True},
                     {'dir': self.shared, 'writer': 'yes'}):
            with self.assertRaises(market.MarketError):
                service.setMarketData(body)
        told = service.setMarketData({'dir': self.shared, 'writer': False})
        self.assertEqual((told['dir'], told['writer'], told['home']), (self.shared, False, self.home))
        with open(os.path.join(self.home, 'market.json')) as handle:
            self.assertEqual(json.load(handle), {'dir': self.shared, 'writer': False})
        with self.assertRaises(market.MarketError):
            service.startImport(['anything'])
        # the stores it lists are the shared folder's
        import_csv.merge(os.path.join(self.home, 'EUR_USD.hd5'), '/M5', bars('2025-09-11', 3))
        self.assertEqual(service.instruments(), [])


class SourcesTest(unittest.TestCase):
    """A server that takes its market data from another one's MCP endpoint."""

    def setUp(self):
        self.folders = [tempfile.mkdtemp(prefix='parity_deriva-src-') for _ in range(2)]
        for folder in self.folders:
            self.addCleanup(shutil.rmtree, folder, True)
        self.upstream = service_module.Service(setup=types.SimpleNamespace(DATA_DIR=self.folders[0]))
        self.local = service_module.Service(setup=types.SimpleNamespace(DATA_DIR=self.folders[1]))
        # the upstream answers as its endpoint would, JSON and all, to its mirror token
        from parity_deriva.web import mcp
        def rpc(upstream, name, args, timeout=180):
            self.assertEqual((upstream['url'], upstream['token']), ('https://up/mcp', 'the mirror'))
            return json.loads(json.dumps(mcp.call(self.upstream, name, args, 'https://up', 'mirror: test')))
        patch = mock.patch.object(sources, 'rpc', rpc)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(mcp, 'PUSH_BARS', 4)
        patch.start()
        self.addCleanup(patch.stop)
        market.save({'candles': {'source': 'upstream', 'every': 5},
                     'calendar': {'source': 'upstream', 'every': 5},
                     'upstream': {'url': 'https://up/mcp', 'token': 'the mirror'}}, self.local.setup)

    def run_source(self, kind):
        kept = market.config(self.local.setup)
        state = {'lines': []}
        self.local.sources.run(kind, kept, state)
        self.assertTrue(state['ok'], state['lines'])
        return state['lines']

    def test_the_candles_and_the_calendar_come_from_upstream_and_only_what_is_missing(self):
        path = market.store('EUR_USD', self.upstream.setup)
        import_csv.merge(path, '/M5', bars('2025-09-11', 10))
        calendar.save(calendar.merge(calendar.empty(), [
            ('2025-09-11 12:15:00', 'EUR', 'high', 'Main Refinancing Rate')]), setup=self.upstream.setup)
        # ten bars four at a time, and the calendar
        self.assertEqual(self.run_source('candles'), ['EUR_USD M5: 10 bars from upstream'])
        self.assertEqual(self.run_source('calendar'), ['calendar: 1 events from upstream, 1 new'])
        mine = market.store('EUR_USD', self.local.setup)
        pd.testing.assert_frame_equal(pd.read_hdf(mine, 'M5'), pd.read_hdf(path, 'M5'), check_like=True)
        # the next run takes the bars after the last one held here, and nothing else
        import_csv.merge(path, '/M5', bars('2025-09-11 00:50', 3, price=1.2))
        self.assertEqual(self.run_source('candles'), ['EUR_USD M5: 3 bars from upstream'])
        self.assertEqual(len(pd.read_hdf(mine, 'M5')), 13)
        self.assertEqual(self.run_source('calendar'), ['calendar: 1 events from upstream, 0 new'])

    def test_the_spread_set_comes_with_the_candles_and_only_to_a_server(self):
        from parity_deriva.lib import spread
        from parity_deriva.web import mcp
        path = market.store('EUR_USD', self.upstream.setup)
        import_csv.merge(path, '/M5', bars('2025-09-11', 2))
        held = {'built': '2026-09-26', 'instruments': {'EUR_USD': {'etoro': 0.0001}, 'GBP_USD': {'etoro': 0.0002}}}
        with open(spread.path(self.upstream.setup), 'w') as handle:
            json.dump(held, handle)
        self.assertEqual(self.run_source('candles'),
                         ['EUR_USD M5: 2 bars from upstream', 'spread set: 2 instruments from upstream'])
        with open(spread.path(self.local.setup)) as handle:
            self.assertEqual(json.load(handle), held)
        self.assertEqual(spread.profile(self.local.setup)['GBP_USD'], {'etoro': 0.0002})
        with self.assertRaises(mcp.ToolError):
            mcp.call(self.upstream, 'pull_spread', {}, 'https://up', 'claude.ai')

    def test_the_candles_come_from_a_brokers_api_after_the_last_bar(self):
        from parity_deriva.event.event import CandleEvent
        from parity_deriva.trading import providers
        path = market.store('EUR_USD', self.local.setup)
        import_csv.merge(path, '/M5', bars('2025-09-11', 3))
        asked = []
        def history(provider, instrument, granularity, since=None, bars=500):
            asked.append((instrument, granularity, since))
            price = {'o': 1.2, 'h': 1.21, 'l': 1.19, 'c': 1.2}
            return [CandleEvent({'time': '2025-09-11T00:%02d:00.000000000Z' % minute, 'volume': 7,
                                 'complete': minute < 25, 'ask': price, 'bid': price, 'mid': price})
                    for minute in (15, 20, 25)]
        broker = types.SimpleNamespace(name='oanda', capabilities=types.SimpleNamespace(bid_ask_candles=True))
        with mock.patch.object(providers, 'get_provider', lambda name, setup: broker), \
                mock.patch.object(providers, 'history', history):
            market.save({'candles': {'source': 'providers', 'every': 5}, 'provider': 'oanda'},
                        self.local.setup)
            # the bar still forming is not a bar yet
            self.assertEqual(self.run_source('candles'), ['EUR_USD M5: 2 bars from oanda'])
            broker.capabilities.bid_ask_candles = False
            state = {'lines': []}
            self.local.sources.run('candles', market.config(self.local.setup), state)
        self.assertEqual(asked, [('EUR_USD', 'M5', pd.Timestamp('2025-09-11 00:10').to_pydatetime())])
        self.assertIn('one price a bar', state['lines'][0])
        held = pd.read_hdf(path, 'M5')
        self.assertEqual((len(held), held['ask_h'].iloc[-1], held['volume'].iloc[-1]), (5, 1.21, 7))

    def test_a_source_runs_on_a_writer_only_and_its_settings_are_checked(self):
        self.assertEqual(market.config(self.local.setup)['upstream']['token'], 'the mirror')
        # a new address keeps the token when none is given, and the page never sees it
        market.save({'upstream': {'url': 'https://other/mcp'}}, self.local.setup)
        self.assertEqual(market.config(self.local.setup)['upstream']['token'], 'the mirror')
        self.assertIs(self.local.marketData()['upstream']['token'], True)
        self.assertEqual(os.stat(os.path.join(self.folders[1], 'market.json')).st_mode & 0o777, 0o600)
        for bad in ({'candles': {'source': 'scraper'}}, {'calendar': {'source': 'providers'}},
                    {'candles': {'source': 'upstream', 'every': 0}}, {'upstream': {'url': 'ftp://x'}},
                    {'provider': 'nobody'}, {'what': 1}):
            with self.assertRaises(market.MarketError):
                market.save(bad, self.local.setup)
        market.save({'writer': False}, self.local.setup)
        with self.assertRaises(market.MarketError):
            self.local.sources.trigger('candles')
        self.assertFalse(self.local.sources.due('candles', market.config(self.local.setup)))


if __name__ == '__main__':
    unittest.main()
