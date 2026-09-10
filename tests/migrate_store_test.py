"""
Tests for scripts/migrate_store.py.

The fixture builds a current store and then downgrades it the way Python 2
left the real one: descriptors re-encoded as bytes and the index put back to
nanosecond resolution. That reproduces both failures the script exists to
repair, so the tests exercise the real code paths rather than a mock.
"""

import datetime
import os
import sys
import unittest

import numpy as np
import pandas as pd
import tables

from parity_deriva.tests.helpers import T0, TempDirCase

# scripts/ is not a package, so it is imported by path
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import migrate_store                                            # noqa: E402


COLUMNS = ['ask_o', 'ask_h', 'ask_l', 'ask_c',
           'bid_o', 'bid_h', 'bid_l', 'bid_c',
           'mid_o', 'mid_h', 'mid_l', 'mid_c', 'volume']


class MigrateCase(TempDirCase):

    def frame(self, rows=10, start=T0):
        index = pd.DatetimeIndex(
            [start + datetime.timedelta(minutes=i) for i in range(rows)])
        data = {}
        for i, col in enumerate(COLUMNS):
            if col == 'volume':
                data[col] = np.arange(rows, dtype='int64')
            else:
                data[col] = np.arange(rows, dtype='float64') + i
        return pd.DataFrame(data, index=index)

    def current_store(self, name="EUR_USD.hd5", rows=10, key='/M1'):
        """A store as today's BulkSaver would write it."""
        path = self.path(name)
        store = pd.HDFStore(path, mode='w')
        store.append(key, self.frame(rows))
        store.close()
        return path

    def legacy_store(self, name="EUR_USD.hd5", rows=10, key='/M1'):
        """
        The same store, degraded the way Python 2 wrote it: descriptors as
        bytes and a nanosecond index.
        """
        path = self.path(name)
        frame = self.frame(rows)
        frame.index = frame.index.as_unit('ns')
        store = pd.HDFStore(path, mode='w')
        store.append(key, frame)
        store.close()

        handle = tables.open_file(path, mode='a')
        try:
            for node in handle.walk_nodes("/"):
                attrs = node._v_attrs
                for attr in attrs._f_list('all'):
                    value = attrs[attr]
                    if isinstance(value, str) and len(value) < 64:
                        attrs[attr] = value.encode('ascii')
        finally:
            handle.close()
        return path


class TestFixtureReproducesTheFailures(MigrateCase):
    """Guard the fixture itself: if it stops failing, the tests prove nothing."""

    def test_a_legacy_store_cannot_be_opened(self):
        path = self.legacy_store()
        store = pd.HDFStore(path, mode='r')
        try:
            with self.assertRaises(TypeError):
                store.get_storer('/M1')
        finally:
            store.close()

    def test_a_nanosecond_store_cannot_be_appended_to(self):
        path = self.path("ns.hd5")
        frame = self.frame(4)
        frame.index = frame.index.as_unit('ns')
        store = pd.HDFStore(path, mode='w')
        store.append('/M1', frame)
        store.close()

        extra = self.frame(1, start=T0 + datetime.timedelta(hours=1))
        store = pd.HDFStore(path, mode='a')
        try:
            with self.assertRaises(TypeError):
                store.append('/M1', extra)
        finally:
            store.close()


class TestInspect(MigrateCase):

    def test_a_legacy_store_needs_migration(self):
        needed, note = migrate_store.inspect(self.legacy_store())
        self.assertTrue(needed)
        self.assertIn("byte-valued descriptors", note)

    def test_a_nanosecond_store_needs_migration(self):
        path = self.path("ns.hd5")
        frame = self.frame(4)
        frame.index = frame.index.as_unit('ns')
        store = pd.HDFStore(path, mode='w')
        store.append('/M1', frame)
        store.close()
        needed, note = migrate_store.inspect(path)
        self.assertTrue(needed)
        self.assertIn("index resolution", note)

    def test_a_current_store_does_not(self):
        needed, note = migrate_store.inspect(self.current_store())
        self.assertFalse(needed)
        self.assertIn("already current", note)

    def test_an_empty_store_does_not(self):
        path = self.path("empty.hd5")
        pd.HDFStore(path, mode='w').close()
        needed, note = migrate_store.inspect(path)
        self.assertFalse(needed)
        self.assertIn("empty", note)

    def test_inspect_does_not_modify_the_store(self):
        path = self.legacy_store()
        before = open(path, 'rb').read()
        migrate_store.inspect(path)
        self.assertEqual(open(path, 'rb').read(), before)


class TestMigrate(MigrateCase):

    def test_the_rows_survive_unchanged(self):
        path = self.legacy_store(rows=20)
        expected = self.frame(20)
        migrate_store.migrate(path)
        store = pd.HDFStore(path, mode='r')
        got = store['/M1']
        store.close()
        pd.testing.assert_frame_equal(
            got.sort_index(axis=1), expected.sort_index(axis=1),
            check_index_type=False)

    def test_the_store_becomes_readable(self):
        path = self.legacy_store()
        migrate_store.migrate(path)
        store = pd.HDFStore(path, mode='r')
        try:
            self.assertEqual(store.get_storer('/M1').nrows, 10)
        finally:
            store.close()

    def test_the_store_becomes_appendable(self):
        """This is the one that matters: BulkSaver appends on every run."""
        path = self.legacy_store()
        migrate_store.migrate(path)
        extra = self.frame(1, start=T0 + datetime.timedelta(hours=1))
        store = pd.HDFStore(path, mode='a')
        store.append('/M1', extra)
        store.close()
        store = pd.HDFStore(path, mode='r')
        self.assertEqual(len(store['/M1']), 11)
        store.close()

    def test_the_where_query_used_by_check_still_works(self):
        path = self.legacy_store(rows=120)
        migrate_store.migrate(path)
        day = T0.strftime("%Y%m%d")
        got = pd.read_hdf(path, '/M1',
                          where='index >= %s000000 and index < %s235959' % (day, day))
        self.assertEqual(len(got), 120)

    def test_the_index_ends_up_at_the_resolution_of_new_data(self):
        path = self.legacy_store()
        migrate_store.migrate(path)
        store = pd.HDFStore(path, mode='r')
        unit = np.datetime_data(store['/M1'].index.dtype)[0]
        store.close()
        self.assertEqual(unit, migrate_store.target_unit())

    def test_a_backup_is_left_behind(self):
        path = self.legacy_store()
        migrate_store.migrate(path)
        self.assertTrue(os.path.exists(path + ".bak"))

    def test_the_backup_is_the_original_bytes(self):
        path = self.legacy_store()
        before = open(path, 'rb').read()
        migrate_store.migrate(path)
        self.assertEqual(open(path + ".bak", 'rb').read(), before)

    def test_the_backup_can_be_suppressed(self):
        path = self.legacy_store()
        migrate_store.migrate(path, backup=False)
        self.assertFalse(os.path.exists(path + ".bak"))

    def test_every_key_is_carried_over(self):
        path = self.path("multi.hd5")
        store = pd.HDFStore(path, mode='w')
        for key, rows in (('/M1', 10), ('/M5', 4), ('/H1', 2)):
            frame = self.frame(rows)
            frame.index = frame.index.as_unit('ns')
            store.append(key, frame)
        store.close()
        handle = tables.open_file(path, mode='a')
        for node in handle.walk_nodes("/"):
            attrs = node._v_attrs
            for attr in attrs._f_list('all'):
                value = attrs[attr]
                if isinstance(value, str) and len(value) < 64:
                    attrs[attr] = value.encode('ascii')
        handle.close()

        counts = migrate_store.migrate(path)
        self.assertEqual(counts, {'/M1': 10, '/M5': 4, '/H1': 2})
        store = pd.HDFStore(path, mode='r')
        self.assertEqual(sorted(store.keys()), ['/H1', '/M1', '/M5'])
        store.close()

    def test_no_temporary_files_are_left_behind(self):
        path = self.legacy_store()
        migrate_store.migrate(path)
        leftovers = [f for f in os.listdir(self.tmpdir) if f.startswith('.migrate-')]
        self.assertEqual(leftovers, [])

    def test_running_it_twice_is_harmless(self):
        path = self.legacy_store(rows=15)
        migrate_store.migrate(path)
        needed, _ = migrate_store.inspect(path)
        self.assertFalse(needed)
        store = pd.HDFStore(path, mode='r')
        self.assertEqual(len(store['/M1']), 15)
        store.close()


class TestDecodeAttrs(MigrateCase):

    def test_it_reports_what_it_decoded(self):
        decoded = migrate_store.decode_attrs(self.legacy_store())
        self.assertTrue(decoded)
        self.assertTrue(any('pandas_type' in name for name in decoded))

    def test_a_current_store_has_nothing_to_decode(self):
        self.assertEqual(migrate_store.decode_attrs(self.current_store()), [])

    def test_pickled_attributes_are_left_alone(self):
        """
        Pickled attributes are bytes as well and must not be turned into text.
        """
        path = self.current_store()
        handle = tables.open_file(path, mode='a')
        handle.root.M1._v_attrs['pickled_marker'] = b'\x80\x04\x95\x00\xff'
        handle.close()
        migrate_store.decode_attrs(path)
        handle = tables.open_file(path, mode='r')
        value = handle.root.M1._v_attrs['pickled_marker']
        handle.close()
        self.assertIsInstance(value, bytes)


class TestMain(MigrateCase):

    def test_dry_run_changes_nothing(self):
        path = self.legacy_store()
        before = open(path, 'rb').read()
        self.assertEqual(migrate_store.main([path, '--dry-run']), 0)
        self.assertEqual(open(path, 'rb').read(), before)

    def test_named_stores_are_migrated(self):
        path = self.legacy_store()
        self.assertEqual(migrate_store.main([path]), 0)
        needed, _ = migrate_store.inspect(path)
        self.assertFalse(needed)

    def test_a_current_store_is_skipped(self):
        path = self.current_store()
        before = open(path, 'rb').read()
        self.assertEqual(migrate_store.main([path]), 0)
        self.assertEqual(open(path, 'rb').read(), before)

    def test_no_stores_found_is_reported(self):
        import parity_deriva.etc.settings as cfg
        original = cfg.DATA_DIR
        cfg.DATA_DIR = self.path("nowhere")
        self.addCleanup(setattr, cfg, 'DATA_DIR', original)
        self.assertEqual(migrate_store.main([]), 1)


if __name__ == "__main__":
    unittest.main()
