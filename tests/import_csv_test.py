"""
Tests for scripts/import_csv.py.

The fixture writes the two CSV files by hand with values whose mid is
checkable in the head, which is what the import has to get right: the pairing
of the two sides, and that a second run changes nothing.
"""

import os
import sys
import unittest
from unittest import mock

import pandas as pd

from parity_deriva.tests.helpers import TempDirCase

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import import_csv                                               # noqa: E402


DAY = 86400000
HEADER = "timestamp,open,high,low,close,volume\n"


class ImportCase(TempDirCase):

    def csv(self, side, rows, name="eurusd_d1_20160121_20160123", label=None):
        """
        One side's file. ask is bid + 0.001, so mid is bid + 0.0005. label
        writes one side's prices into the other side's filename, which is what
        an export that has them backwards looks like.
        """
        off = 0.001 if side == 'ASK' else 0.0
        path = self.path("%s-%s.csv" % (name, label or side))
        with open(path, 'w') as handle:
            handle.write(HEADER)
            for i in rows:
                base = 1.1 + i / 1000.0 + off
                handle.write("%d,%.5f,%.5f,%.5f,%.5f,%.1f\n"
                             % (i * DAY, base, base + 0.01, base - 0.01,
                                base + 0.002, 100.4 + i))
        return path

    def store_key(self, path, key='/D'):
        store = pd.HDFStore(path, mode='r')
        try:
            return store[key]
        finally:
            store.close()


class TestNames(unittest.TestCase):

    def test_an_exporters_name_for_the_dax_is_the_stacks(self):
        self.assertEqual(import_csv.instrument_name('deuidxeur'), 'DE30_EUR')

    def test_instrument_gets_its_separator(self):
        self.assertEqual(import_csv.instrument_name('eurusd'), 'EUR_USD')
        self.assertEqual(import_csv.instrument_name('DE30_EUR'), 'DE30_EUR')

    def test_daily_and_weekly_lose_the_count(self):
        self.assertEqual(import_csv.granularity('d1'), 'D')
        self.assertEqual(import_csv.granularity('w1'), 'W')
        self.assertEqual(import_csv.granularity('m5'), 'M5')

    def test_filename_is_parsed(self):
        self.assertEqual(
            import_csv.parse_name("/tmp/eurusd_d1_20160121_20260920-ASK.csv"),
            ('EUR_USD', 'D', 'ASK'))

    def test_unrecognised_name_is_not_parsed(self):
        self.assertIsNone(import_csv.parse_name("prices.csv"))


class TestImport(ImportCase):

    def run_import(self, *extra):
        return import_csv.main([self.tmpdir, '--data-dir', self.tmpdir] + list(extra))

    def test_a_pair_becomes_one_store_key(self):
        self.csv('ASK', range(3))
        self.csv('BID', range(3))
        self.run_import()

        frame = self.store_key(self.path("EUR_USD.hd5"))
        self.assertEqual(len(frame.index), 3)
        self.assertEqual(sorted(frame.columns), sorted(
            ['%s_%s' % (s, l) for s in ('ask', 'bid', 'mid') for l in 'ohlc']
            + ['volume']))
        self.assertAlmostEqual(frame['ask_o'].iloc[1], 1.102)
        self.assertAlmostEqual(frame['bid_o'].iloc[1], 1.101)
        self.assertAlmostEqual(frame['mid_o'].iloc[1], 1.1015)
        self.assertEqual(frame['volume'].iloc[1], 101)
        self.assertEqual(frame.index[0], pd.Timestamp('1970-01-01'))

    def test_running_twice_changes_nothing(self):
        self.csv('ASK', range(3))
        self.csv('BID', range(3))
        self.run_import()
        first = self.store_key(self.path("EUR_USD.hd5"))
        size = os.path.getsize(self.path("EUR_USD.hd5"))
        self.run_import()
        pd.testing.assert_frame_equal(first, self.store_key(self.path("EUR_USD.hd5")))
        # not rewritten either: HDF5 keeps a rewritten key's old blocks, and
        # the page's import button would grow the store on every press
        self.assertEqual(os.path.getsize(self.path("EUR_USD.hd5")), size)

    def test_rows_after_the_series_are_appended_not_rewritten(self):
        self.csv('ASK', range(3))
        self.csv('BID', range(3))
        self.run_import()
        self.csv('ASK', range(3, 6), name="eurusd_d1_20160124_20160126")
        self.csv('BID', range(3, 6), name="eurusd_d1_20160124_20160126")
        self.run_import()
        frame = self.store_key(self.path("EUR_USD.hd5"))
        self.assertEqual(len(frame.index), 6)
        self.assertTrue(frame.index.is_unique and frame.index.is_monotonic_increasing)

    def test_a_later_file_extends_the_key(self):
        self.csv('ASK', range(3))
        self.csv('BID', range(3))
        self.run_import()
        os.remove(self.path("eurusd_d1_20160121_20160123-ASK.csv"))
        os.remove(self.path("eurusd_d1_20160121_20160123-BID.csv"))
        self.csv('ASK', range(2, 5), name="eurusd_d1_20160123_20160125")
        self.csv('BID', range(2, 5), name="eurusd_d1_20160123_20160125")
        self.run_import()

        frame = self.store_key(self.path("EUR_USD.hd5"))
        self.assertEqual(len(frame.index), 5)
        self.assertTrue(frame.index.is_monotonic_increasing)

    def test_two_sets_of_one_series_are_both_imported(self):
        """
        Was: files were paired by instrument and timeframe, so a second export
             of the same series replaced the first in the pairing unseen.
        Now: a set is the -ASK/-BID pair sharing a name, and each is merged.
        """
        self.csv('ASK', range(3))
        self.csv('BID', range(3))
        self.csv('ASK', range(3, 6), name="eurusd_d1_20160124_20160126")
        self.csv('BID', range(3, 6), name="eurusd_d1_20160124_20160126")
        self.run_import()
        self.assertEqual(len(self.store_key(self.path("EUR_USD.hd5")).index), 6)

    def test_a_set_already_imported_is_not_read_again(self):
        self.csv('ASK', range(3))
        self.csv('BID', range(3))
        self.run_import()
        lines = []
        with mock.patch.object(import_csv, 'read_side',
                               side_effect=AssertionError("read again")):
            self.assertEqual(import_csv.main(
                [self.tmpdir, '--data-dir', self.tmpdir], report=lines.append), 0)
        self.assertEqual(len(lines), 1)
        self.assertIn('already imported', lines[0])

    def test_a_set_whose_files_changed_is_imported_again(self):
        self.csv('ASK', range(3))
        self.csv('BID', range(3))
        self.run_import()
        self.csv('ASK', range(4))
        self.csv('BID', range(4))
        self.run_import()
        self.assertEqual(len(self.store_key(self.path("EUR_USD.hd5")).index), 4)

    def test_progress_runs_to_the_bytes_of_the_sets_read(self):
        self.csv('ASK', range(3))
        self.csv('BID', range(3))
        seen = []
        import_csv.main([self.tmpdir, '--data-dir', self.tmpdir], report=lambda line: None,
                        progress=lambda done, total, text: seen.append((done, total)))
        total = sum(os.path.getsize(self.path(n)) for n in os.listdir(self.tmpdir)
                    if n.endswith('.csv'))
        self.assertEqual(seen[-1], (total, total))
        self.assertEqual([d for d, _ in seen], sorted(d for d, _ in seen))

    def test_a_lone_side_is_refused(self):
        self.csv('ASK', range(3))
        self.assertEqual(self.run_import(), 1)
        self.assertFalse(os.path.exists(self.path("EUR_USD.hd5")))

    def test_rows_without_a_counterpart_are_dropped(self):
        self.csv('ASK', range(5))
        self.csv('BID', range(3))
        self.run_import()
        self.assertEqual(len(self.store_key(self.path("EUR_USD.hd5")).index), 3)

    def test_a_backwards_labelled_pair_is_refused(self):
        """The -ASK file holding the lower side is a mislabelled export."""
        self.csv('BID', range(3), label='ASK')
        self.csv('ASK', range(3), label='BID')
        self.assertEqual(self.run_import(), 1)
        self.assertFalse(os.path.exists(self.path("EUR_USD.hd5")))

        self.run_import('--swap-sides')
        frame = self.store_key(self.path("EUR_USD.hd5"))
        self.assertTrue((frame['ask_c'] > frame['bid_c']).all())
        self.assertAlmostEqual(frame['mid_o'].iloc[1], 1.1015)

    def test_dry_run_writes_nothing(self):
        self.csv('ASK', range(3))
        self.csv('BID', range(3))
        self.run_import('--dry-run')
        self.assertFalse(os.path.exists(self.path("EUR_USD.hd5")))

    def test_the_store_stays_appendable(self):
        """bulksaver.py appends to the same key after an import."""
        self.csv('ASK', range(3))
        self.csv('BID', range(3))
        self.run_import()
        frame = self.store_key(self.path("EUR_USD.hd5"))
        later = frame.copy()
        later.index = later.index + pd.Timedelta(days=10)
        store = pd.HDFStore(self.path("EUR_USD.hd5"), mode='a')
        try:
            store.append('/D', later)
        finally:
            store.close()
        self.assertEqual(len(self.store_key(self.path("EUR_USD.hd5")).index), 6)


if __name__ == '__main__':
    unittest.main()
