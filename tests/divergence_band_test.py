"""
Tests for scripts/divergence_band.py.

The trades are constructed so each lands in a known bucket, because the whole
point of the script is to produce a number an alarm threshold is set from: a
miscount here would be a threshold that either never fires or always does.
"""

import datetime
import os
import sys
import unittest

import pandas as pd

from parity_deriva.event.event import SignalEvent
from parity_deriva.tests.helpers import T0, TempDirCase

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import divergence_band                                          # noqa: E402


HOUR = datetime.timedelta(hours=1)


def frame(rows, start, step):
    """Rows of (low, high), applied to both sides of the book."""
    index = pd.DatetimeIndex([start + i * step for i in range(len(rows))])
    return pd.DataFrame(
        {'bid_l': [r[0] for r in rows], 'bid_h': [r[1] for r in rows],
         'ask_l': [r[0] for r in rows], 'ask_h': [r[1] for r in rows]},
        index=index)


class Candle(object):
    def __init__(self, time):
        self.time = time


def signal(entry=1.5, stop=1.0, target=2.0, units=1, key="K"):
    return SignalEvent({"instrument": "EUR_USD", "units": units,
                        "orderType": "STOP", "price": entry,
                        "stopLoss": stop, "takeProfit": target,
                        "signalNumber": key})


class MeasureCase(unittest.TestCase):

    def measure(self, coarse_rows, fine_rows, sig=None, at=T0):
        coarse = frame(coarse_rows, at, HOUR)
        fine = frame(fine_rows, at + HOUR, datetime.timedelta(minutes=1))
        signals = [(Candle(at - HOUR), sig or signal())]
        return divergence_band.measure(signals, coarse, fine, HOUR)


class TestBuckets(MeasureCase):

    def test_a_trade_the_coarse_bars_decide(self):
        # bar 0 fills the entry at 1.5, bar 1 reaches only the target
        tally, flips, unresolved = self.measure(
            [(1.4, 1.6), (1.9, 2.1)], [(1.9, 2.1)])
        self.assertEqual(tally['decided by the coarse bars'], 1)
        self.assertEqual(flips, [])

    def test_a_trade_that_never_enters(self):
        tally, _, _ = self.measure([(3.0, 4.0), (3.0, 4.0)], [(3.0, 4.0)])
        self.assertEqual(tally['never entered'], 1)

    def test_a_trade_still_open_when_the_data_ends(self):
        tally, _, _ = self.measure([(1.4, 1.6), (1.5, 1.6)], [(1.5, 1.6)])
        self.assertEqual(tally['still open at the end of the range'], 1)

    def test_a_coin_flip_the_reference_settles(self):
        """
        The bar after entry holds both 1.0 and 2.0, so the hour cannot say
        which came first. The minutes inside it can: the target, at once.
        """
        tally, flips, unresolved = self.measure(
            [(1.4, 1.6), (1.0, 2.0)],
            [(1.9, 2.1), (0.9, 1.1)])
        self.assertEqual(tally['coin flip, settled by M1'], 1)
        self.assertEqual(unresolved, 0)
        self.assertEqual(len(flips), 1)
        self.assertEqual(flips[0][2], 'TARGET')

    def test_the_reference_can_settle_it_the_other_way(self):
        tally, flips, _ = self.measure(
            [(1.4, 1.6), (1.0, 2.0)],
            [(0.9, 1.1), (1.9, 2.1)])
        self.assertEqual(tally['coin flip, settled by M1'], 1)
        self.assertEqual(flips[0][2], 'STOP')

    def test_a_coin_flip_the_reference_cannot_settle(self):
        """A minute holding both levels is the reference's own limit."""
        tally, flips, unresolved = self.measure(
            [(1.4, 1.6), (1.0, 2.0)], [(1.0, 2.0)])
        self.assertEqual(tally['coin flip, unresolved at M1'], 1)
        self.assertEqual(unresolved, 1)
        self.assertEqual(flips, [])

    def test_a_coin_flip_where_the_reference_shows_neither(self):
        """Minutes missing from the store, or a gap: reported, not guessed."""
        tally, _, _ = self.measure(
            [(1.4, 1.6), (1.0, 2.0)], [(1.5, 1.6)])
        self.assertEqual(tally['coin flip, M1 says neither'], 1)

    def test_a_signal_without_levels_is_skipped(self):
        tally, _, _ = self.measure([(1.4, 1.6)], [(1.4, 1.6)],
                                   sig=signal(stop=None, target=None))
        self.assertEqual(sum(tally.values()), 0)

    def test_a_short_is_read_off_the_other_side(self):
        sig = signal(entry=1.5, stop=2.0, target=1.0, units=-1)
        tally, flips, _ = self.measure(
            [(1.4, 1.6), (1.0, 2.0)],
            [(0.9, 1.1), (1.9, 2.1)], sig=sig)
        self.assertEqual(tally['coin flip, settled by M1'], 1)
        self.assertEqual(flips[0][2], 'TARGET')


class TestBand(unittest.TestCase):

    def tally(self, **counts):
        import collections
        return collections.Counter(counts)

    def test_the_denominator_is_the_trades_that_entered_and_closed(self):
        flips, decided = divergence_band.band(self.tally(**{
            'decided by the coarse bars': 90,
            'coin flip, settled by M1': 8,
            'coin flip, unresolved at M1': 2,
            'never entered': 500,
            'still open at the end of the range': 300,
        }))
        self.assertEqual(flips, 10)
        self.assertEqual(decided, 100)

    def test_all_three_flip_kinds_count(self):
        flips, decided = divergence_band.band(self.tally(**{
            'coin flip, settled by M1': 1,
            'coin flip, unresolved at M1': 1,
            'coin flip, M1 says neither': 1,
        }))
        self.assertEqual((flips, decided), (3, 3))

    def test_an_empty_tally(self):
        self.assertEqual(divergence_band.band(self.tally()), (0, 0))

    def test_a_sample_with_no_flips_gives_a_zero_band(self):
        flips, decided = divergence_band.band(
            self.tally(**{'decided by the coarse bars': 40}))
        self.assertEqual(flips, 0)
        self.assertEqual(decided, 40)


class TestLoadBars(TempDirCase):

    def test_a_missing_granularity_is_refused_rather_than_guessed(self):
        import parity_deriva.etc.settings as cfg
        original = cfg.DATA_DIR
        cfg.DATA_DIR = self.tmpdir
        self.addCleanup(setattr, cfg, 'DATA_DIR', original)
        path = self.path("EUR_USD.hd5")
        store = pd.HDFStore(path, mode='w')
        store.append('/M1', frame([(1.0, 2.0)], T0, HOUR))
        store.close()
        with self.assertRaises(SystemExit):
            divergence_band.load_bars("EUR_USD", "H1")

    def test_it_reads_the_requested_granularity(self):
        import parity_deriva.etc.settings as cfg
        original = cfg.DATA_DIR
        cfg.DATA_DIR = self.tmpdir
        self.addCleanup(setattr, cfg, 'DATA_DIR', original)
        path = self.path("EUR_USD.hd5")
        store = pd.HDFStore(path, mode='w')
        store.append('/H1', frame([(1.0, 2.0), (1.1, 2.1)], T0, HOUR))
        store.close()
        got = divergence_band.load_bars("EUR_USD", "H1")
        self.assertEqual(len(got), 2)

    def test_it_leaves_the_store_closed(self):
        """
        data/replay.py used to leak an open handle in append mode, which then
        refused every read-only open of the same file in the process.
        """
        import parity_deriva.etc.settings as cfg
        original = cfg.DATA_DIR
        cfg.DATA_DIR = self.tmpdir
        self.addCleanup(setattr, cfg, 'DATA_DIR', original)
        path = self.path("EUR_USD.hd5")
        store = pd.HDFStore(path, mode='w')
        store.append('/H1', frame([(1.0, 2.0)], T0, HOUR))
        store.close()
        divergence_band.load_bars("EUR_USD", "H1")
        again = pd.HDFStore(path, mode='r')      # would raise if still open
        again.close()


if __name__ == "__main__":
    unittest.main()
