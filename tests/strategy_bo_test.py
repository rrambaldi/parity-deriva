"""
Characterisation tests for the BO family - the pattern research engines.

These classes emit no orders. They answer one question: after a given run of
consecutive candle directions, how many bars pass before the direction flips?
The answer is accumulated three ways (overall, per hour, per weekday) and
printed as percentage tables. Since those tables are the research output, the
counting semantics and the reporting quirks are both pinned here.
"""

import datetime
import logging
import unittest

from parity_deriva.event.event import CandleEvent, StatusEvent
from parity_deriva.strategy.BO import BO
from parity_deriva.strategy.BO01 import BO01
from parity_deriva.strategy.BO02 import BO02
from parity_deriva.strategy.BO03 import BO03
from parity_deriva.strategy.BO04 import BO04
from parity_deriva.strategy.BO05 import BO05, BO06
from parity_deriva.tests.helpers import T0, bull_candle, bear_candle, Recorder


ALL_DAY = ("00:00:00", "23:00:00")


def feed(strategy, directions, start=T0, instrument="DE30_EUR"):
    """Push a sequence of bull/bear candles one minute apart."""
    for i, up in enumerate(directions):
        maker = bull_candle if up else bear_candle
        ev = CandleEvent(maker(start + datetime.timedelta(minutes=i),
                               base=11700.0 + i))
        ev.instrument = instrument
        ev.granularity = "M1"
        strategy.execute_event(ev)
    return strategy


def make(cls, **kw):
    kw.setdefault('pair', "DE30_EUR")
    kw.setdefault('minTime', ALL_DAY[0])
    kw.setdefault('maxTime', ALL_DAY[1])
    s = cls(**kw)
    s.set_queue(Recorder())
    return s


class TestCommonSetUp(unittest.TestCase):

    def test_depths(self):
        self.assertEqual(make(BO01).depth, 10)
        self.assertEqual(make(BO02).depth, 10)
        self.assertEqual(make(BO03).depth, 14)
        self.assertEqual(make(BO04).depth, 14)
        self.assertEqual(make(BO05).depth, 14)
        self.assertEqual(make(BO).depth, 14)

    def test_the_buffer_is_a_one_based_shift_register(self):
        """prev[1] is the oldest bar in the window, prev[depth] the newest."""
        s = make(BO01, depth=3)
        self.assertEqual(sorted(s.prev), [1, 2, 3])
        feed(s, [True, False, True])
        self.assertEqual(s.prev[1].time, T0)
        self.assertEqual(s.prev[3].time, T0 + datetime.timedelta(minutes=2))

    def test_the_window_slides(self):
        s = make(BO01, depth=3)
        feed(s, [True, False, True, False])
        self.assertEqual(s.prev[1].time, T0 + datetime.timedelta(minutes=1))
        self.assertEqual(s.prev[3].time, T0 + datetime.timedelta(minutes=3))

    def test_counters_start_empty(self):
        s = make(BO01)
        self.assertEqual(set(s.num.values()), {0})
        self.assertEqual(s.received, 0)

    def test_nothing_is_counted_until_the_window_is_full(self):
        s = make(BO01, depth=5)
        feed(s, [True, False, False, True, True])
        self.assertEqual(s.received, 0)
        self.assertEqual(sum(s.num.values()), 0)

    def test_counting_starts_on_the_bar_after_the_window_fills(self):
        s = make(BO01, depth=5)
        feed(s, [True, False, False, True, True, True])
        self.assertEqual(s.received, 1)

    def test_no_strategy_in_the_family_ever_emits_a_signal(self):
        for cls in (BO, BO01, BO02, BO03, BO04, BO05, BO06):
            s = make(cls)
            feed(s, [True, False, False] * 12)
            self.assertEqual(s.event_queue.events, [], cls.__name__)

    def test_non_candle_events_are_ignored(self):
        s = make(BO01)
        self.assertIsNone(s.execute_event(StatusEvent('DONE')))

    def test_candles_for_another_instrument_are_ignored(self):
        s = make(BO01, depth=3)
        feed(s, [True, False, False, True], instrument="EUR_USD")
        self.assertIsNone(s.prev[1])

    def test_the_done_status_never_triggers_the_final_report(self):
        """
        Both the base class and BO01/BO03 gate their report on
        `str(event) == 'DONE'` / `'QUI'`, but a StatusEvent stringifies to
        'STATUS'. The end-of-run tables are therefore never printed - the only
        output comes from the periodic stats_after dump.
        """
        s = make(BO)
        with self.assertRaises(AssertionError):
            with self.assertLogs('parity_deriva.trading.trading', level='INFO'):
                s.execute_event(StatusEvent('DONE'))


class TestBO01Pattern(unittest.TestCase):
    """BO01 looks for up, down, down at the oldest end of the window."""

    def test_the_pattern_is_counted(self):
        s = make(BO01, depth=5)
        # window = [U, D, D, U, U]; the trailing bar drives it in
        feed(s, [True, False, False, True, True, True])
        self.assertEqual(sum(s.num.values()), 1)

    def test_a_non_matching_window_counts_nothing(self):
        s = make(BO01, depth=5)
        feed(s, [False, False, False, True, True, True])
        self.assertEqual(sum(s.num.values()), 0)

    def test_the_bucket_is_the_distance_to_the_next_down_bar(self):
        """
        After the U,D,D at positions 1..3, the scan walks positions 4..depth
        and stops at the first bearish bar; `out` counts the steps taken.
        Here position 4 is already bearish, so out == 1.
        """
        s = make(BO01, depth=5)
        feed(s, [True, False, False, False, True, True])
        self.assertEqual(s.num[1], 1)

    def test_a_later_flip_lands_in_a_higher_bucket(self):
        s = make(BO01, depth=5)
        feed(s, [True, False, False, True, False, True])
        self.assertEqual(s.num[2], 1)

    def test_a_window_that_never_flips_saturates_at_the_last_bucket(self):
        """
        With no bearish bar in positions 4..depth the loop runs to the end, so
        `out` saturates at depth-3 instead of signalling "did not resolve".
        A saturated bucket and a genuine depth-3 resolution are indistinguishable.
        """
        s = make(BO01, depth=5)
        feed(s, [True, False, False, True, True, True])
        self.assertEqual(s.num[2], 1)      # depth-3 == 2

    def test_per_hour_and_per_weekday_accumulate_together(self):
        s = make(BO01, depth=5)
        feed(s, [True, False, False, False, True, True])
        hour = T0.hour
        weekday = T0.weekday()
        self.assertEqual(s.numh[hour][1], 1)
        self.assertEqual(s.week[weekday][1], 1)
        self.assertEqual(sum(s.num.values()), 1)

    def test_the_hour_filter_excludes_bars_outside_the_session(self):
        s = make(BO01, depth=5, minTime="14:00:00", maxTime="18:00:00")
        feed(s, [True, False, False, False, True, True], start=T0)  # 10:00
        self.assertEqual(sum(s.num.values()), 0)

    def test_the_hour_filter_includes_bars_inside_the_session(self):
        s = make(BO01, depth=5, minTime="09:00:00", maxTime="18:00:00")
        feed(s, [True, False, False, False, True, True], start=T0)
        self.assertEqual(sum(s.num.values()), 1)

    def test_the_filter_looks_at_the_oldest_bar_of_the_window(self):
        """
        It is p[1].time.hour that is tested, i.e. the first bar of the pattern,
        not the bar being processed - so a pattern that starts just before the
        session close is still counted.
        """
        s = make(BO01, depth=5, minTime="09:00:00", maxTime="10:00:00")
        feed(s, [True, False, False, False, True, True],
             start=T0.replace(hour=10, minute=58))
        self.assertEqual(sum(s.num.values()), 1)

    def test_a_sunday_candle_crashes_the_weekday_accumulator(self):
        """
        week is initialised for range(0, 6) - Monday to Saturday. OANDA does
        publish Sunday-evening bars, and weekday() == 6 has no bucket, so a
        research run over raw history dies with KeyError. Under the Engine
        that becomes os._exit(1), losing the run.
        """
        sunday = datetime.datetime(2017, 2, 5, 22, 0, 0)
        self.assertEqual(sunday.weekday(), 6)
        s = make(BO01, depth=5)
        with self.assertRaises(KeyError):
            feed(s, [True, False, False, False, True, True], start=sunday)


class TestMirrorPatterns(unittest.TestCase):
    """The family is built in mirrored pairs so both directions can be compared."""

    def test_bo02_is_the_mirror_of_bo01(self):
        up = make(BO01, depth=5)
        down = make(BO02, depth=5)
        feed(up, [True, False, False, False, True, True])
        feed(down, [False, True, True, True, False, False])
        self.assertEqual(sum(up.num.values()), 1)
        self.assertEqual(sum(down.num.values()), 1)
        self.assertEqual(up.num, down.num)

    def test_bo01_does_not_fire_on_the_bo02_pattern(self):
        s = make(BO01, depth=5)
        feed(s, [False, True, True, True, False, False])
        self.assertEqual(sum(s.num.values()), 0)

    def test_bo03_needs_a_four_bar_run(self):
        """up, up, down, down - so the scan starts at position 5."""
        s = make(BO03, depth=6)
        feed(s, [True, True, False, False, False, True, True])
        self.assertEqual(s.num[1], 1)

    def test_bo04_is_the_mirror_of_bo03(self):
        s = make(BO04, depth=6)
        feed(s, [False, False, True, True, True, False, False])
        self.assertEqual(s.num[1], 1)

    def test_bo05_counts_three_bears_in_a_row(self):
        s = make(BO05, depth=5)
        feed(s, [False, False, False, False, True, True])
        self.assertEqual(s.num[1], 1)

    def test_bo06_counts_three_bulls_in_a_row(self):
        s = make(BO06, depth=5)
        feed(s, [True, True, True, False, True, True])
        self.assertEqual(s.num[1], 1)

    def test_bo06_still_scans_forward_for_a_bear_bar(self):
        """
        BO06 matches on three bulls but its resolution loop breaks on
        `direction() < 0`, the same test BO05 uses - so for BO06 the bucket
        means "bars until the up-run ends", which is the intended reading.
        """
        s = make(BO06, depth=6)
        feed(s, [True, True, True, True, False, True, True])
        self.assertEqual(s.num[2], 1)


class TestBOBaseClass(unittest.TestCase):

    def test_the_base_class_calculate_is_a_no_op(self):
        s = make(BO, depth=5)
        feed(s, [True, False, False, False, True, True])
        self.assertEqual(sum(s.num.values()), 0)
        self.assertEqual(s.received, 0)

    def test_subclasses_drive_received_from_calculate(self):
        s = make(BO05, depth=5)
        feed(s, [False, False, False, False, True, True])
        self.assertEqual(s.received, 1)

    def test_the_base_class_never_reaches_its_periodic_report(self):
        """
        BO.execute_event triggers printStats on `received % stats_after`, but
        only the subclasses' calculate() increments received - so the base
        class alone would report on every bar were it not for the received > 0
        guard.
        """
        s = make(BO, depth=3)
        with self.assertRaises(AssertionError):
            with self.assertLogs('parity_deriva.trading.trading', level='DEBUG'):
                feed(s, [True, False, False, True])


class TestPrintStats(unittest.TestCase):

    def test_it_survives_an_empty_run(self):
        s = make(BO01)
        with self.assertLogs('parity_deriva.trading.trading', level='DEBUG') as log:
            s.printStats()
        self.assertTrue(any("ALL TOT 0" in line for line in log.output))

    def test_the_base_class_report_takes_a_level(self):
        s = make(BO05)
        with self.assertLogs('parity_deriva.trading.trading', level='INFO') as log:
            s.printStats(logging.INFO)
        self.assertTrue(any("BO05 DE30_EUR ALL TOT" in line for line in log.output))

    def test_percentages_are_computed_over_the_total(self):
        s = make(BO01, depth=5)
        feed(s, [True, False, False, False, True, True])
        with self.assertLogs('parity_deriva.trading.trading', level='DEBUG') as log:
            s.printStats()
        self.assertTrue(any("ALL 1 NUM: 1 PERC: 100.00" in l for l in log.output))

    def test_hourly_percentages_are_suppressed_below_ten_samples(self):
        """`if toth[h] > 9` - thinner hours are reported as 0.00, not omitted."""
        s = make(BO01, depth=5)
        feed(s, [True, False, False, False, True, True])
        with self.assertLogs('parity_deriva.trading.trading', level='DEBUG') as log:
            s.printStats()
        line = [l for l in log.output if "HOUR 10-1" in l][0]
        self.assertIn("NUM: 1", line)
        self.assertIn("PERC:   0.00", line)

    def test_the_weekday_section_repeats_every_day_once_per_day(self):
        """
        The reporting loop reuses `w` for both the outer accumulation and the
        inner print, so once any weekday has data the full table is printed
        again for each such day, and every percentage is divided by the last
        totw computed rather than that day's own total. The DAY block of the
        research output is duplicated and mis-normalised.
        """
        s = make(BO01, depth=5)
        feed(s, [True, False, False, False, True, True])
        with self.assertLogs('parity_deriva.trading.trading', level='DEBUG') as log:
            s.printStats()
        day_lines = [l for l in log.output if " DAY " in l]
        # 6 weekdays x depth buckets, emitted once for each day that has data
        self.assertEqual(len(day_lines), 6 * s.depth)
        wednesday = T0.weekday()
        hits = [l for l in day_lines if "DAY %d-1 NUM: 1" % wednesday in l]
        self.assertEqual(len(hits), 1)

    def test_the_periodic_report_fires_every_stats_after_matches(self):
        s = make(BO01, depth=5)
        s.stats_after = 2
        with self.assertLogs('parity_deriva.trading.trading', level='DEBUG') as log:
            feed(s, [True, False, False, False, True, True])
        self.assertFalse(any("ALL TOT" in l for l in log.output))
        with self.assertLogs('parity_deriva.trading.trading', level='DEBUG') as log:
            feed(s, [True], start=T0 + datetime.timedelta(minutes=6))
        self.assertTrue(any("ALL TOT" in l for l in log.output))


if __name__ == "__main__":
    unittest.main()
