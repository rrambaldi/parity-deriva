"""
Tests for lib/streaming.py: the indicators a strategy reads one bar at a time.

The one that matters most is the agreement with lib/indicators.py. That module
computes a whole series for the chart and this one carries a value forward bar
by bar; if they disagreed, the line drawn over a backtest would not be the line
the rule read, and every argument about a trade would be an argument about
which of the two was being looked at.
"""

import datetime
import unittest

from parity_deriva.event.event import CandleEvent
from parity_deriva.lib import indicators
from parity_deriva.lib.streaming import Daily, Series, Swings
from parity_deriva.tests.helpers import T0, candle_dict


def bar(i, o=None, h=None, l=None, c=None, volume=10, step=None):
    """One candle, i steps after T0."""
    close = 100.0 + i if c is None else c
    opened = close - 1 if o is None else o
    step = step or datetime.timedelta(hours=4)
    ev = CandleEvent(candle_dict(
        T0 + i * step,
        o=opened, c=close,
        h=max(opened, close) + 1 if h is None else h,
        l=min(opened, close) - 1 if l is None else l,
        volume=volume))
    ev.instrument, ev.granularity = 'EUR_USD', 'H4'
    return ev


def walk(closes, highs=None, lows=None):
    """A series from a list of closes, with optional extremes."""
    out = []
    for i, close in enumerate(closes):
        out.append(bar(i, o=close, c=close,
                       h=close if highs is None else highs[i],
                       l=close if lows is None else lows[i]))
    return out


class TestAgreement(unittest.TestCase):
    """The streaming values are the array ones, bar for bar."""

    CLOSES = [1.0, 3.0, 2.0, 5.0, 4.0, 6.0, 8.0, 7.0, 9.0, 11.0,
              10.0, 12.0, 14.0, 13.0, 15.0, 17.0, 16.0, 18.0, 20.0, 19.0]

    def feed(self, **kw):
        series = Series(**kw)
        for candle in walk(self.CLOSES, highs=[c + 1 for c in self.CLOSES],
                           lows=[c - 1 for c in self.CLOSES]):
            series.add(candle)
        return series

    def test_the_ema_is_the_one_the_chart_draws(self):
        series = self.feed(ema=(5,))
        self.assertAlmostEqual(series.ema(5),
                               indicators.ema(self.CLOSES, 5)[-1])

    def test_and_is_absent_until_it_is_seeded(self):
        series = Series(ema=(5,))
        for candle in walk(self.CLOSES[:4]):
            series.add(candle)
        self.assertIsNone(series.ema(5))
        series.add(walk(self.CLOSES[:5])[-1])
        self.assertIsNotNone(series.ema(5))

    def test_the_atr_is_wilder_s_and_the_chart_s(self):
        series = self.feed(atr=5)
        highs = [c + 1 for c in self.CLOSES]
        lows = [c - 1 for c in self.CLOSES]
        self.assertAlmostEqual(series.atr(),
                               indicators.atr(highs, lows, self.CLOSES, 5)[-1])

    def test_the_sma_is_the_mean_of_its_window(self):
        series = self.feed(sma=(4,))
        self.assertAlmostEqual(series.sma(4),
                               sum(self.CLOSES[-4:]) / 4.0)

    def test_the_bands_sit_either_side_of_the_middle(self):
        series = self.feed(sma=(5,), stdev=(5,))
        lower, middle, upper = series.bands(5, 2.0)
        spread = indicators.stdev(self.CLOSES, 5)[-1]
        self.assertAlmostEqual(middle, indicators.sma(self.CLOSES, 5)[-1])
        self.assertAlmostEqual(upper, middle + 2 * spread)
        self.assertAlmostEqual(lower, middle - 2 * spread)


class TestRsi(unittest.TestCase):

    def test_a_series_that_only_rises_is_a_hundred(self):
        series = Series(rsi=5)
        for candle in walk([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]):
            series.add(candle)
        self.assertEqual(series.rsi(), 100.0)

    def test_a_series_that_only_falls_is_zero(self):
        series = Series(rsi=5)
        for candle in walk([7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0]):
            series.add(candle)
        self.assertAlmostEqual(series.rsi(), 0.0)

    def test_a_series_that_alternates_sits_near_fifty(self):
        """
        Near it and not on it: Wilder's averages weight the newest move most,
        so the side of the last one tips the reading. The series below ends
        on a fall, and the mirror of it ends on a rise.
        """
        falling, rising = Series(rsi=4), Series(rsi=4)
        for candle in walk([1.0, 2.0, 1.0, 2.0, 1.0, 2.0, 1.0, 2.0, 1.0]):
            falling.add(candle)
        for candle in walk([2.0, 1.0, 2.0, 1.0, 2.0, 1.0, 2.0, 1.0, 2.0]):
            rising.add(candle)
        self.assertTrue(40 < falling.rsi() < 50, falling.rsi())
        self.assertTrue(50 < rising.rsi() < 60, rising.rsi())

    def test_it_is_absent_until_it_is_warm(self):
        series = Series(rsi=5)
        for candle in walk([1.0, 2.0, 3.0]):
            series.add(candle)
        self.assertIsNone(series.rsi())


class TestWindow(unittest.TestCase):

    def test_the_window_is_the_last_bars_and_nothing_older(self):
        series = Series(keep=3)
        for candle in walk([1.0, 2.0, 3.0, 4.0, 5.0]):
            series.add(candle)
        self.assertEqual([c.mid['c'] for c in series.window(3)],
                         [3.0, 4.0, 5.0])
        self.assertIsNone(series.window(4), 'it does not invent bars')

    def test_the_keep_is_never_shorter_than_what_was_asked_for(self):
        """A window of 3 cannot answer an SMA(20), so it is not a window of 3."""
        series = Series(sma=(20,), keep=3)
        self.assertGreater(series.keep, 20)

    def test_the_average_volume_is_over_the_window(self):
        series = Series(volume=3)
        for i, volume in enumerate((10, 20, 30, 40)):
            series.add(bar(i, volume=volume))
        self.assertAlmostEqual(series.averageVolume(), 30.0)


class TestSwings(unittest.TestCase):
    """
    Moved here from strategy/AG01MOD.py, which was the only reader until the
    4H strategies wanted the same thing.
    """

    def feed(self, highs, lows, bars=2):
        swings = Swings(bars=bars)
        for i, (high, low) in enumerate(zip(highs, lows)):
            swings.add(bar(i, o=(high + low) / 2, c=(high + low) / 2,
                           h=high, l=low))
        return swings

    def test_a_peak_is_a_resistance_once_the_bars_after_it_have_printed(self):
        highs = [1, 1, 5, 1, 1]
        swings = self.feed(highs, [0] * 5)
        self.assertEqual([(l['kind'], l['price']) for l in swings.levels()],
                         [('resistance', 5)])

    def test_and_not_before(self):
        swings = self.feed([1, 1, 5, 1], [0] * 4)
        self.assertEqual(swings.levels(), [])

    def test_a_trough_is_a_support(self):
        swings = self.feed([9] * 5, [5, 5, 1, 5, 5])
        self.assertEqual([l['kind'] for l in swings.levels()], ['support'])

    def test_a_flat_run_has_no_swings(self):
        """The test of an extreme is strict."""
        self.assertEqual(self.feed([1] * 7, [0] * 7).levels(), [])

    def test_the_most_recent_of_a_kind_is_the_one_asked_for(self):
        swings = self.feed([1, 1, 5, 1, 1, 1, 9, 1, 1],
                           [0] * 9)
        self.assertEqual(swings.last('resistance')['price'], 9)
        self.assertIsNone(swings.last('support'))


class TestDaily(unittest.TestCase):
    """A day, folded out of the bars inside it."""

    def day(self, i, hour):
        when = T0.replace(hour=0) + datetime.timedelta(days=i, hours=hour)
        ev = CandleEvent(candle_dict(when, o=10.0 + hour, h=20.0 + hour,
                                     l=5.0 + hour, c=15.0 + hour))
        ev.instrument = 'EUR_USD'
        return ev

    def test_a_day_is_not_complete_until_the_next_one_starts(self):
        daily = Daily()
        for hour in (0, 4, 8):
            self.assertIsNone(daily.add(self.day(0, hour)))

    def test_and_then_it_carries_the_whole_day(self):
        daily = Daily()
        for hour in (0, 4, 8):
            daily.add(self.day(0, hour))
        done = daily.add(self.day(1, 0))
        self.assertIsNotNone(done)
        self.assertEqual(done.mid['o'], 10.0)        # the first bar's open
        self.assertEqual(done.mid['c'], 23.0)        # the last bar's close
        self.assertEqual(done.mid['h'], 28.0)        # the highest high
        self.assertEqual(done.mid['l'], 5.0)         # the lowest low

    def test_the_day_being_built_is_not_in_the_list(self):
        daily = Daily()
        daily.add(self.day(0, 0))
        daily.add(self.day(1, 0))
        daily.add(self.day(1, 4))
        self.assertEqual(len(daily.bars), 1)


if __name__ == '__main__':
    unittest.main()
