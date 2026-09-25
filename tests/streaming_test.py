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
from parity_deriva.lib.streaming import Cross, Daily, Series, Swings, Values, Weekly
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


class TestReadings(unittest.TestCase):
    """What the assistants' weekly strategies wrote out by hand, read off a Series."""

    CLOSES = TestAgreement.CLOSES

    def feed(self, **kw):
        series = Series(**kw)
        for i, close in enumerate(self.CLOSES):
            series.add(bar(i, o=close, c=close, h=close + 1, l=close - 1, volume=i + 1))
        return series

    def test_the_sma_some_bars_ago_is_the_chart_s_there(self):
        series = self.feed()
        self.assertAlmostEqual(series.sma(5, back=4), indicators.sma(self.CLOSES, 5)[-5])
        self.assertAlmostEqual(series.sma(5, back=0), series.sma(5))
        self.assertIsNone(series.sma(5, back=16))

    def test_the_rate_of_change_is_the_close_over_the_one_before_less_one(self):
        series = self.feed()
        self.assertAlmostEqual(series.change(3), self.CLOSES[-1] / self.CLOSES[-4] - 1)
        self.assertIsNone(series.change(len(self.CLOSES)))

    def test_the_bandwidth_is_the_width_of_the_bands_over_the_middle(self):
        series = self.feed()
        lower, middle, upper = series.bands(5, 2.0)
        self.assertAlmostEqual(series.bandwidth(5, 2.0), (upper - lower) / middle)

    def test_the_channel_leaves_out_the_bar_it_is_measured_for(self):
        series = self.feed()
        before = self.CLOSES[-4:-1]
        self.assertEqual(series.channel(3), (min(before) - 1, max(before) + 1))
        self.assertEqual(series.high(3), max(self.CLOSES[-3:]) + 1)
        self.assertEqual(series.high(3, skip=1), max(before) + 1)
        self.assertIsNone(series.channel(len(self.CLOSES)))

    def test_the_distance_from_the_high_and_the_low_is_a_share_of_it(self):
        series = self.feed()
        close = self.CLOSES[-1]
        self.assertAlmostEqual(series.offHigh(5), close / (max(self.CLOSES[-5:]) + 1) - 1)
        self.assertAlmostEqual(series.offLow(5), close / (min(self.CLOSES[-5:]) - 1) - 1)
        # WK09's filter, "within maxDrop percent of the highBars high", read off it
        high = max(c + 1 for c in self.CLOSES[-5:])
        self.assertEqual(close >= (1 - 0.05) * high, series.offHigh(5) >= -0.05)
        self.assertIsNone(series.offHigh(len(self.CLOSES) + 1))

    def test_the_average_volume_before_this_bar_leaves_it_out(self):
        series = self.feed()
        n = len(self.CLOSES)
        self.assertAlmostEqual(series.averageVolume(3, skip=1), (n - 1 + n - 2 + n - 3) / 3.0)
        self.assertAlmostEqual(series.averageVolume(3), (n + n - 1 + n - 2) / 3.0)


class TestValues(unittest.TestCase):
    """A series the strategy derives, and where a new value stands in it."""

    def test_a_value_is_ranked_against_the_ones_before_it(self):
        past = Values(4)
        for value in (1, 2, 3, 4):
            self.assertFalse(past.full())
            past.add(value)
        self.assertTrue(past.full())
        self.assertEqual(past.rank(3.5), 0.75)
        self.assertEqual((past.rank(0), past.rank(10)), (0.0, 1.0))
        past.add(5)
        self.assertEqual(past.values, [2, 3, 4, 5])

    def test_the_median_is_the_middle_or_the_mean_of_the_two(self):
        # WK08's own: (ordered[(n - 1) // 2] + ordered[n // 2]) / 2
        for values in ([1, 2, 3, 10], [1, 5, 2], [0.3, -0.1, 0.7, 0.2, 0.9, 0.4]):
            past = Values(len(values))
            for value in values:
                past.add(value)
            ordered, n = sorted(values), len(values)
            self.assertAlmostEqual(past.median(), (ordered[(n - 1) // 2] + ordered[n // 2]) / 2.0)

    def test_nothing_is_said_about_an_empty_one(self):
        past = Values(3)
        self.assertIsNone(past.rank(1))
        self.assertIsNone(past.median())


class TestCross(unittest.TestCase):
    """The bar a value goes over a level or under it."""

    def test_it_is_the_bar_the_side_changes_and_no_other(self):
        cross = Cross()
        self.assertEqual([cross.add(v) for v in (-2, -1, 0, 1, 2, 0.5, -0.5, -1)],
                         [0, 0, 0, 1, 0, 0, -1, 0])

    def test_an_rsi_back_over_thirty(self):
        cross = Cross(30)
        self.assertEqual([cross.add(v) for v in (35, 28, 25, 31, 29)], [0, -1, 0, 1, -1])

    def test_a_value_still_warming_has_nothing_to_cross_from(self):
        cross = Cross()
        self.assertEqual([cross.add(v) for v in (None, 1, None, -1, 1)], [0, 0, 0, 0, 1])


class TestWeekly(unittest.TestCase):
    """Weeks out of daily bars, sealed by their Friday."""

    SUNDAY = datetime.date(2017, 2, 5)

    def day(self, days, hour=21, price=10.0):
        when = datetime.datetime.combine(self.SUNDAY + datetime.timedelta(days=days),
                                         datetime.time(hour))
        ev = CandleEvent(candle_dict(when, o=price, h=price + 5, l=price - 5, c=price + 1))
        ev.instrument = 'EUR_USD'
        return ev

    def test_the_friday_bar_seals_the_week_it_ends(self):
        weekly = Weekly()
        # stamped Sunday 21:00 to Thursday 21:00: Monday's trading to Friday's
        done = [weekly.add(self.day(i, price=10.0 + i)) for i in range(5)]
        self.assertEqual(done[:4], [[], [], [], []])
        week = done[4][0]
        self.assertEqual((week.mid['o'], week.mid['h'], week.mid['l'], week.mid['c']),
                         (10.0, 19.0, 5.0, 15.0))
        self.assertEqual(len(weekly.bars), 1)

    def test_a_bar_stamped_at_midnight_is_its_own_day(self):
        weekly = Weekly()
        done = [weekly.add(self.day(i, hour=0)) for i in range(1, 6)]   # Monday to Friday
        self.assertEqual([len(d) for d in done], [0, 0, 0, 0, 1])

    def test_a_week_without_its_friday_is_sealed_by_the_next_one(self):
        weekly = Weekly()
        for i in range(4):                       # Monday to Thursday, no Friday
            self.assertEqual(weekly.add(self.day(i)), [])
        self.assertEqual(len(weekly.add(self.day(7))), 1)       # next Monday
        # and a week of one Friday after another with none: both at once
        for i in range(8, 11):
            weekly.add(self.day(i))
        self.assertEqual(len(weekly.add(self.day(18))), 2)      # the Friday after


if __name__ == '__main__':
    unittest.main()
