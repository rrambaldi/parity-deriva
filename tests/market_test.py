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

from parity_deriva.data import calendar, market
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
        self.assertEqual(market.config(self.setup), {'dir': self.home, 'writer': True})
        self.assertEqual(calendar.path(self.setup), os.path.join(self.home, 'calendar.csv'))

    def test_a_reader_reads_the_shared_folder_and_writes_nothing_into_it(self):
        market.save(self.shared, False, self.setup)
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
        market.save(self.shared, True, self.setup)
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


if __name__ == '__main__':
    unittest.main()
