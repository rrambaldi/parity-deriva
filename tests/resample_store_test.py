"""
Tests for scripts/resample_store.py.

The fixture builds a minute store with known, hand-checkable values so the
aggregation can be verified by arithmetic rather than by comparing against
another pandas call.
"""

import datetime
import os
import sys
import unittest

import numpy as np
import pandas as pd

from parity_deriva.tests.helpers import T0, TempDirCase

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import resample_store                                           # noqa: E402


SIDES = ('ask', 'bid', 'mid')
LEGS = ('o', 'h', 'l', 'c')


class ResampleCase(TempDirCase):

    def minutes(self, rows, start=None):
        """
        One bar per minute. The nth bar has mid o=n, h=n+10, l=n-10, c=n+1,
        ask is mid+1 and bid is mid-1, volume is n. Every aggregate is then a
        number that can be checked in the head.
        """
        start = start or T0
        index = pd.DatetimeIndex(
            [start + datetime.timedelta(minutes=i) for i in range(rows)])
        data = {}
        offsets = {'mid': 0.0, 'ask': 1.0, 'bid': -1.0}
        base = np.arange(rows, dtype='float64')
        for side in SIDES:
            off = offsets[side]
            data['%s_o' % side] = base + off
            data['%s_h' % side] = base + 10 + off
            data['%s_l' % side] = base - 10 + off
            data['%s_c' % side] = base + 1 + off
        data['volume'] = np.arange(rows, dtype='int64')
        return pd.DataFrame(data, index=index)

    def store_with(self, frame, name="EUR_USD.hd5", key='/M1'):
        path = self.path(name)
        store = pd.HDFStore(path, mode='w')
        store.append(key, frame)
        store.close()
        return path


class TestAggregationMap(unittest.TestCase):

    def test_each_leg_gets_its_own_aggregation(self):
        columns = ['%s_%s' % (s, l) for s in SIDES for l in LEGS] + ['volume']
        how = resample_store.aggregation_map(columns)
        self.assertEqual(how['ask_o'], 'first')
        self.assertEqual(how['bid_h'], 'max')
        self.assertEqual(how['mid_l'], 'min')
        self.assertEqual(how['ask_c'], 'last')
        self.assertEqual(how['volume'], 'sum')

    def test_an_unexpected_column_is_refused(self):
        with self.assertRaises(ValueError):
            resample_store.aggregation_map(['ask_o', 'surprise'])


class TestResample(ResampleCase):

    def test_sixty_minutes_become_one_hour(self):
        out = resample_store.resample(self.minutes(60), 'H1')
        self.assertEqual(len(out), 1)
        self.assertEqual(out.index[0], pd.Timestamp(T0))

    def test_open_is_the_first_and_close_is_the_last(self):
        out = resample_store.resample(self.minutes(60), 'H1').iloc[0]
        self.assertEqual(out['mid_o'], 0.0)     # first bar's open
        self.assertEqual(out['mid_c'], 60.0)    # last bar's close, 59 + 1

    def test_high_is_the_max_and_low_is_the_min(self):
        out = resample_store.resample(self.minutes(60), 'H1').iloc[0]
        self.assertEqual(out['mid_h'], 69.0)    # 59 + 10
        self.assertEqual(out['mid_l'], -10.0)   # 0 - 10

    def test_each_price_side_keeps_its_own_spread(self):
        out = resample_store.resample(self.minutes(60), 'H1').iloc[0]
        self.assertEqual(out['ask_o'], 1.0)
        self.assertEqual(out['bid_o'], -1.0)
        self.assertEqual(out['ask_h'] - out['mid_h'], 1.0)
        self.assertEqual(out['mid_l'] - out['bid_l'], 1.0)

    def test_volume_is_summed(self):
        out = resample_store.resample(self.minutes(60), 'H1')
        self.assertEqual(out['volume'].iloc[0], sum(range(60)))

    def test_volume_stays_an_integer(self):
        out = resample_store.resample(self.minutes(60), 'H1')
        self.assertEqual(out['volume'].dtype, np.dtype('int64'))

    def test_two_hours_give_two_bars(self):
        out = resample_store.resample(self.minutes(120), 'H1')
        self.assertEqual(len(out), 2)
        self.assertEqual(out['mid_o'].tolist(), [0.0, 60.0])
        self.assertEqual(out['volume'].tolist(), [sum(range(60)), sum(range(60, 120))])

    def test_a_partial_hour_is_kept(self):
        """
        A session that opens at 22:04 leaves four minutes in that hour. That
        bar is still the truth about the hour and is not dropped.
        """
        out = resample_store.resample(self.minutes(4), 'H1')
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]['volume'], sum(range(4)))
        self.assertEqual(out.iloc[0]['mid_c'], 4.0)

    def test_closed_periods_produce_no_bar(self):
        """A weekend inside the range must not become a row of NaN."""
        first = self.minutes(60, start=T0)
        later = self.minutes(60, start=T0 + datetime.timedelta(days=3))
        out = resample_store.resample(pd.concat([first, later]), 'H1')
        self.assertEqual(len(out), 2)
        self.assertFalse(out.isna().any().any())

    def test_h4_and_d_also_work(self):
        """
        The bins are anchored to midnight, not to the first bar: eight hours
        from 10:00 fall into the 08:00, 12:00 and 16:00 four-hour buckets.
        """
        frame = self.minutes(60 * 8)
        self.assertEqual(len(resample_store.resample(frame, 'H4')), 3)
        self.assertEqual(len(resample_store.resample(frame, 'D')), 1)

    def test_an_unknown_granularity_is_refused(self):
        with self.assertRaises(ValueError):
            resample_store.resample(self.minutes(10), 'X9')

    def test_the_result_has_the_same_columns_as_the_source(self):
        frame = self.minutes(60)
        out = resample_store.resample(frame, 'H1')
        self.assertEqual(sorted(out.columns), sorted(frame.columns))


class TestProcess(ResampleCase):

    def test_it_writes_the_target_key(self):
        path = self.store_with(self.minutes(180))
        rows, note = resample_store.process(path, 'M1', 'H1')
        self.assertEqual(rows, 3)
        store = pd.HDFStore(path, mode='r')
        self.assertEqual(sorted(store.keys()), ['/H1', '/M1'])
        store.close()

    def test_the_source_key_is_left_untouched(self):
        path = self.store_with(self.minutes(180))
        before = pd.HDFStore(path, mode='r')['/M1'] if False else None
        store = pd.HDFStore(path, mode='r'); before = store['/M1']; store.close()
        resample_store.process(path, 'M1', 'H1')
        store = pd.HDFStore(path, mode='r'); after = store['/M1']; store.close()
        pd.testing.assert_frame_equal(before, after)

    def test_the_target_is_appendable_like_any_store_key(self):
        """data/bulksaver.py has to be able to keep appending to it."""
        path = self.store_with(self.minutes(180))
        resample_store.process(path, 'M1', 'H1')
        extra = resample_store.resample(
            self.minutes(60, start=T0 + datetime.timedelta(days=1)), 'H1')
        store = pd.HDFStore(path, mode='a')
        store.append('/H1', extra)
        store.close()
        store = pd.HDFStore(path, mode='r')
        self.assertEqual(len(store['/H1']), 4)
        store.close()

    def test_an_existing_target_is_not_overwritten(self):
        path = self.store_with(self.minutes(180))
        resample_store.process(path, 'M1', 'H1')
        rows, note = resample_store.process(path, 'M1', 'H1')
        self.assertIsNone(rows)
        self.assertIn("already present", note)

    def test_force_rebuilds_it(self):
        path = self.store_with(self.minutes(180))
        resample_store.process(path, 'M1', 'H1')
        rows, _ = resample_store.process(path, 'M1', 'H1', force=True)
        self.assertEqual(rows, 3)
        store = pd.HDFStore(path, mode='r')
        self.assertEqual(len(store['/H1']), 3)      # not doubled
        store.close()

    def test_a_missing_source_is_reported(self):
        path = self.store_with(self.minutes(60), key='/M5')
        rows, note = resample_store.process(path, 'M1', 'H1')
        self.assertIsNone(rows)
        self.assertIn("no /M1 key", note)

    def test_dry_run_writes_nothing(self):
        path = self.store_with(self.minutes(180))
        rows, note = resample_store.process(path, 'M1', 'H1', dry_run=True)
        self.assertIsNone(rows)
        self.assertIn("would build", note)
        store = pd.HDFStore(path, mode='r')
        self.assertEqual(list(store.keys()), ['/M1'])
        store.close()

    def test_the_replay_source_can_read_the_built_key(self):
        """
        The point of writing into the same store: data/replay.py streams a
        granularity key straight back out.
        """
        from parity_deriva.data import replay as replay_mod
        for name in ('last', 'url', 'store', 'curr', 'candles', 'samples', 'cent'):
            if hasattr(replay_mod.ForexCandles, name):
                setattr(replay_mod.ForexCandles, name, {})
        path = self.store_with(self.minutes(180), name="EUR_USD.hd5")
        resample_store.process(path, 'M1', 'H1')

        class Sink(object):
            def __init__(self): self.candles = []
            def put(self, event):
                if str(event) == 'CANDLE':
                    self.candles.append(event)

        src = replay_mod.ForexCandles(
            setup=self.settings, pairs=["EUR_USD"], granularity="H1",
            dtfrom=T0, dtto=T0 + datetime.timedelta(hours=2))
        sink = Sink()
        src.set_queue(sink)
        src.stream_to_queue()
        self.assertEqual(len(sink.candles), 3)
        self.assertEqual(sink.candles[0].mid['o'], 0.0)
        self.assertEqual(sink.candles[0].mid['c'], 60.0)


class TestMain(ResampleCase):

    def test_named_store(self):
        path = self.store_with(self.minutes(180))
        self.assertEqual(resample_store.main([path, '--source', 'M1',
                                              '--target', 'H1']), 0)
        store = pd.HDFStore(path, mode='r')
        self.assertIn('/H1', store.keys())
        store.close()

    def test_no_stores_found(self):
        import parity_deriva.etc.settings as cfg
        original = cfg.DATA_DIR
        cfg.DATA_DIR = self.path("nowhere")
        self.addCleanup(setattr, cfg, 'DATA_DIR', original)
        self.assertEqual(resample_store.main([]), 1)


if __name__ == "__main__":
    unittest.main()
