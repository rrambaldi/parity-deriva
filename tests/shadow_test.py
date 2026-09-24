"""
Tests for backtest/shadow.py: which candles a run's orders rest on.

The fixture is a minute walk that the hourly series is aggregated from, which
is the one property a real store has and a hand-written pair of series does
not: the hour's high is a minute's high, so a level the hourly bar says was
touched is a level some minute actually reached. Two series invented
separately would let the two disagree, and every test below would then be
measuring the fixture.
"""

import datetime
import random
import unittest

import pandas as pd

from parity_deriva.backtest import ledger, shadow
from parity_deriva.tests.helpers import TempDirCase

T0 = datetime.datetime(2018, 1, 2, 0, 0, 0)
MINUTE = datetime.timedelta(minutes=1)
HOUR = datetime.timedelta(hours=1)
SPREAD = 0.0001


def walk(bars, start=T0, seed=7):
    """A minute series: one flat bar per minute of a seeded random walk."""
    rng = random.Random(seed)
    price, rows, index = 1.2, [], []
    for i in range(bars):
        opened = price
        price = round(price + rng.uniform(-0.0004, 0.0004), 5)
        rows.append((opened, max(opened, price), min(opened, price), price))
        index.append(start + i * MINUTE)
    return pd.DataFrame(rows, columns=['o', 'h', 'l', 'c'],
                        index=pd.DatetimeIndex(index))


def hourly(minutes):
    """The same candles an hour at a time, the way a store holds both."""
    return minutes.resample('1h').agg({'o': 'first', 'h': 'max', 'l': 'min',
                                       'c': 'last'}).dropna()


def store_frame(bars):
    """One series in the layout data/replay.py reads back."""
    out = pd.DataFrame(index=bars.index)
    for side, shift in (('mid', 0.0), ('ask', SPREAD / 2), ('bid', -SPREAD / 2)):
        for field in 'ohlc':
            out['%s_%s' % (side, field)] = bars[field] + shift
    out['volume'] = 100
    return out


class StoreCase(TempDirCase):
    """A store with an hourly series and the minute series it came from."""

    minutes = 60 * 24 * 3

    def setUp(self):
        TempDirCase.setUp(self)
        self.m1 = walk(self.minutes)
        self.h1 = hourly(self.m1)
        self.write({'M1': self.m1, 'H1': self.h1})

    def write(self, series, instrument='EUR_USD'):
        store = pd.HDFStore(self.path('%s.hd5' % instrument), mode='w')
        try:
            for name, bars in series.items():
                store.append('/' + name, store_frame(bars))
        finally:
            store.close()

    def window(self):
        return self.h1.index[0].to_pydatetime(), self.h1.index[-1].to_pydatetime()


class TestFiner(StoreCase):
    """Which series a run is allowed to fill against."""

    def test_the_minute_series_shadows_the_hourly_one(self):
        dtfrom, dtto = self.window()
        self.assertEqual(
            shadow.finer('EUR_USD', 'H1', dtfrom, dtto, self.settings), 'M1')

    def test_the_finest_series_has_nothing_below_it(self):
        dtfrom, dtto = self.window()
        self.assertIsNone(
            shadow.finer('EUR_USD', 'M1', dtfrom, dtto, self.settings))

    def test_a_series_that_stops_short_is_refused(self):
        """
        Half the minutes would fill half the trades, and the ones it dropped
        would look exactly like trades the strategy never took.
        """
        self.write({'M1': self.m1.iloc[:len(self.m1) // 2], 'H1': self.h1})
        dtfrom, dtto = self.window()
        self.assertIsNone(
            shadow.finer('EUR_USD', 'H1', dtfrom, dtto, self.settings))

    def test_a_series_that_starts_late_is_refused(self):
        self.write({'M1': self.m1.iloc[60:], 'H1': self.h1})
        dtfrom, dtto = self.window()
        self.assertIsNone(
            shadow.finer('EUR_USD', 'H1', dtfrom, dtto, self.settings))

    def test_too_many_bars_falls_back_rather_than_running_for_an_hour(self):
        dtfrom, dtto = self.window()
        self.assertIsNone(shadow.finer('EUR_USD', 'H1', dtfrom, dtto,
                                       self.settings, max_bars=10))

    def test_a_granularity_the_store_does_not_hold_is_built_and_shadowed(self):
        """
        Was: a D run on a store holding only M1 and H1 had no shadow - and no
             D bars either, so it could not run at all.
        Now: D is built from the finest series held, and the orders rest on
             that series, the way a live account sees the day move.
        """
        dtfrom, dtto = self.window()
        self.assertEqual(
            shadow.finer('EUR_USD', 'D', dtfrom, dtto, self.settings), 'M1')

    def test_a_series_opening_late_in_the_day_still_shadows_that_day(self):
        """
        Was: an export opening on Sunday 22:00 builds a D bar stamped 00:00,
             the series 'started after' that bar, and the run fell back to
             filling on days.
        Now: a fine series only has to reach into the first coarse bar.
        """
        self.write({'M1': walk(60 * 26, start=T0 + 22 * HOUR)})
        self.assertEqual(shadow.finer('EUR_USD', 'D', T0, T0 + 2 * HOUR * 24,
                                      self.settings), 'M1')

    def test_a_granularity_nothing_can_build_has_no_shadow(self):
        dtfrom, dtto = self.window()
        self.assertIsNone(
            shadow.finer('EUR_USD', 'W', dtfrom, dtto, self.settings))

    def test_a_window_outside_the_data_has_no_shadow(self):
        far = datetime.datetime(2030, 1, 1)
        self.assertIsNone(shadow.finer('EUR_USD', 'H1', far,
                                       far + HOUR, self.settings))

    def test_no_store_at_all_is_not_an_error(self):
        self.assertEqual(shadow.held('NO_SUCH', self.settings), {})


class Bar(object):
    """A candle as far as the merge is concerned: a time and a label."""

    def __init__(self, when, label):
        self.time, self.label = when, label

    def __str__(self):
        return 'CANDLE'


class Source(object):
    """A drained source, which is all Shadowed asks of one."""

    def __init__(self, granularity, events):
        self.granularity, self.events = granularity, events

    def set_queue(self, queue):
        self.queue = queue

    def stream_to_queue(self):
        for event in self.events:
            self.queue.put(event)


class TestOrder(unittest.TestCase):
    """
    Bars reach the bus in the order they closed, not the order they opened.

    That is the whole of the look-ahead argument: an hourly bar stamped 10:00
    is knowledge of 11:00, so it must arrive after the minute that ends at
    11:00 and before the one that ends at 11:01. Sorted on the open instead,
    an order derived from it would be resting through the very hour it was
    derived from.
    """

    def merged(self):
        coarse = Source('H1', [Bar(T0 + i * HOUR, 'H1 %d' % i)
                               for i in range(3)])
        fine = Source('M1', [Bar(T0 + i * MINUTE, 'M1 %d' % i)
                             for i in range(180)])
        out = []

        class Queue(object):
            def put(self, event):
                out.append(event)

        merge = shadow.Shadowed(coarse, fine)
        merge.set_queue(Queue())
        merge.stream_to_queue()
        return [getattr(e, 'label', str(e)) for e in out]

    def test_an_hour_arrives_after_every_minute_it_contains(self):
        labels = self.merged()
        self.assertEqual(labels[labels.index('H1 0') - 1], 'M1 59')

    def test_and_before_the_first_minute_of_the_next_one(self):
        labels = self.merged()
        self.assertEqual(labels[labels.index('H1 0') + 1], 'M1 60')

    def test_every_bar_of_both_series_is_there(self):
        labels = self.merged()
        self.assertEqual(len([l for l in labels if l.startswith('M1')]), 180)
        self.assertEqual(len([l for l in labels if l.startswith('H1')]), 3)

    def test_the_run_is_still_announced(self):
        """The driver's handlers see a STATUS before any candle, as ever."""
        self.assertEqual(self.merged()[0], 'STATUS')


class TestShadowedRun(StoreCase):
    """A whole backtest, with and without the minute series under it."""

    def run_it(self, fine):
        dtfrom, dtto = self.window()
        return ledger.run('EUR_USD', 'H1', 'AG01', dtfrom, dtto,
                          setup=self.settings, fine=fine)

    def setUp(self):
        StoreCase.setUp(self)
        self.plain = self.run_it(False)
        self.shadowed = self.run_it(True)

    def test_the_run_says_which_series_filled_it(self):
        self.assertIsNone(self.plain.fine)
        self.assertEqual(self.shadowed.fine, 'M1')

    def test_the_strategy_still_reads_the_hourly_bars(self):
        """
        The minute series is on the bus and the strategy must not have
        counted it: a candle it signals on is an hour of the run, not a
        minute of it.
        """
        self.assertEqual(self.shadowed.counts['candles'], len(self.h1))
        self.assertEqual(self.shadowed.counts['signals'],
                         self.plain.counts['signals'])

    def test_every_trade_still_comes_from_an_hourly_signal(self):
        """
        Which signals become trades is allowed to differ - that is the point
        of filling on minutes, and on real data it more than halves them -
        but a signal is still a thing an hourly bar said.
        """
        hours = set(self.h1.index.to_pydatetime())
        self.assertTrue(self.shadowed.trades)
        for trade in self.shadowed.trades:
            self.assertIn(trade['signalTime'], hours)

    def test_an_entry_lands_on_a_minute_rather_than_on_the_hour(self):
        minutes = [t for t in self.shadowed.trades if t['entryTime'].minute]
        self.assertTrue(minutes, "every entry fell on the hour")

    def test_nothing_fills_inside_the_bar_that_signalled_it(self):
        """The look-ahead this whole ordering exists to prevent."""
        for trade in self.shadowed.trades:
            self.assertGreaterEqual(trade['entryTime'],
                                    trade['signalTime'] + HOUR)

    def test_an_exit_never_precedes_its_entry(self):
        for trade in self.shadowed.trades:
            if trade['exitTime'] is not None:
                self.assertGreaterEqual(trade['exitTime'], trade['entryTime'])

    def test_a_named_granularity_is_taken_as_given(self):
        self.assertEqual(self.run_it('M1').fine, 'M1')


if __name__ == '__main__':
    unittest.main()
