"""
Tests for backtest/resolution.py.

The bars are built by hand so every outcome can be read off the numbers: this
is the module the divergence threshold is computed from, and a threshold
derived from a wrong reading would be worse than no threshold.
"""

import datetime
import unittest

import pandas as pd

from parity_deriva.backtest import resolution
from parity_deriva.backtest.resolution import (AMBIGUOUS, OPEN, STOP, TARGET,
                                               exit_side, first_touch, refine,
                                               residual_ambiguity,
                                               resolve_exit, touches)
from parity_deriva.tests.helpers import T0


def bars(rows, start=T0, step=datetime.timedelta(minutes=1)):
    """
    Each row is (low, high) and applies to both sides of the book, so the
    side-selection logic is exercised separately where it matters.
    """
    index = pd.DatetimeIndex([start + i * step for i in range(len(rows))])
    return pd.DataFrame(
        {'bid_l': [r[0] for r in rows], 'bid_h': [r[1] for r in rows],
         'ask_l': [r[0] for r in rows], 'ask_h': [r[1] for r in rows]},
        index=index)


def split_bars(rows, start=T0, step=datetime.timedelta(minutes=1)):
    """Rows of (bid_l, bid_h, ask_l, ask_h) when the two sides must differ."""
    index = pd.DatetimeIndex([start + i * step for i in range(len(rows))])
    return pd.DataFrame(
        {'bid_l': [r[0] for r in rows], 'bid_h': [r[1] for r in rows],
         'ask_l': [r[2] for r in rows], 'ask_h': [r[3] for r in rows]},
        index=index)


class TestExitSide(unittest.TestCase):

    def test_a_long_closes_on_the_bid_and_a_short_on_the_ask(self):
        self.assertEqual(exit_side(1), 'bid')
        self.assertEqual(exit_side(1000), 'bid')
        self.assertEqual(exit_side(-1), 'ask')

    def test_it_matches_the_simulator(self):
        """
        backtest/oanda.py flips the sign of both exit legs, so a long's stop
        and target are sell orders and trade against the bid.
        """
        self.assertEqual(exit_side(1), 'bid')


class TestTouches(unittest.TestCase):

    def setUp(self):
        self.bar = bars([(1.0, 2.0)]).iloc[0]

    def test_a_level_inside_the_range(self):
        self.assertTrue(touches(self.bar, 1.5, 'bid'))

    def test_the_bounds_are_inclusive(self):
        """A real broker fills on touch, and so does the simulator."""
        self.assertTrue(touches(self.bar, 1.0, 'bid'))
        self.assertTrue(touches(self.bar, 2.0, 'bid'))

    def test_a_level_outside(self):
        self.assertFalse(touches(self.bar, 0.99, 'bid'))
        self.assertFalse(touches(self.bar, 2.01, 'bid'))

    def test_the_side_is_honoured(self):
        bar = split_bars([(1.0, 2.0, 3.0, 4.0)]).iloc[0]
        self.assertTrue(touches(bar, 1.5, 'bid'))
        self.assertFalse(touches(bar, 1.5, 'ask'))
        self.assertTrue(touches(bar, 3.5, 'ask'))


class TestFirstTouch(unittest.TestCase):

    def test_it_returns_the_label_of_the_first_bar_reaching_the_level(self):
        frame = bars([(1.0, 1.1), (1.0, 1.5), (1.0, 2.0)])
        self.assertEqual(first_touch(frame, 1.4, 'bid'), frame.index[1])

    def test_none_when_never_reached(self):
        self.assertIsNone(first_touch(bars([(1.0, 1.1)]), 9.0, 'bid'))


class TestResolveExit(unittest.TestCase):
    """A long with stop 1.0 and target 2.0."""

    def resolve(self, rows, units=1, stop=1.0, target=2.0):
        return resolve_exit(bars(rows), stop, target, units)

    def test_the_target_alone(self):
        outcome, label = self.resolve([(1.4, 1.5), (1.5, 2.0)])
        self.assertEqual(outcome, TARGET)
        self.assertEqual(label, T0 + datetime.timedelta(minutes=1))

    def test_the_stop_alone(self):
        outcome, label = self.resolve([(1.4, 1.5), (1.0, 1.4)])
        self.assertEqual(outcome, STOP)

    def test_neither(self):
        outcome, label = self.resolve([(1.3, 1.5), (1.4, 1.6)])
        self.assertEqual(outcome, OPEN)
        self.assertIsNone(label)

    def test_both_in_one_bar_is_ambiguous(self):
        """
        The reading this whole module exists for: the bar reached the stop and
        the target, and does not say in which order.
        """
        outcome, label = self.resolve([(1.0, 2.0)])
        self.assertEqual(outcome, AMBIGUOUS)
        self.assertEqual(label, T0)

    def test_an_earlier_clean_bar_wins_over_a_later_ambiguous_one(self):
        outcome, _ = self.resolve([(1.5, 2.0), (1.0, 2.0)])
        self.assertEqual(outcome, TARGET)

    def test_the_first_bar_decides_even_if_later_bars_would_differ(self):
        outcome, _ = self.resolve([(1.0, 1.2), (1.9, 2.0)])
        self.assertEqual(outcome, STOP)

    def test_a_short_reads_the_ask(self):
        frame = split_bars([(1.0, 2.0, 5.0, 6.0)])
        outcome, _ = resolve_exit(frame, stop=5.5, target=5.2, units=-1)
        self.assertEqual(outcome, AMBIGUOUS)
        outcome, _ = resolve_exit(frame, stop=1.5, target=1.2, units=-1)
        self.assertEqual(outcome, OPEN)

    def test_a_short_target_below_and_stop_above(self):
        frame = split_bars([(0.0, 0.0, 5.0, 5.4)])
        outcome, _ = resolve_exit(frame, stop=5.5, target=5.2, units=-1)
        self.assertEqual(outcome, TARGET)

    def test_empty_bars_leave_it_open(self):
        outcome, label = resolve_exit(bars([]), 1.0, 2.0, 1)
        self.assertEqual(outcome, OPEN)


class TestRefine(unittest.TestCase):
    """Resolving the same trade over the minutes inside one hour."""

    def test_the_finer_bars_break_the_tie(self):
        hour = T0.replace(minute=0)
        minutes = bars([(1.4, 1.5), (1.5, 2.0), (1.0, 1.5)], start=hour)
        outcome, label = refine(minutes, 1.0, 2.0, 1, hour,
                                datetime.timedelta(hours=1))
        self.assertEqual(outcome, TARGET)
        self.assertEqual(label, hour + datetime.timedelta(minutes=1))

    def test_the_other_order_gives_the_other_answer(self):
        hour = T0.replace(minute=0)
        minutes = bars([(1.4, 1.5), (1.0, 1.5), (1.5, 2.0)], start=hour)
        outcome, _ = refine(minutes, 1.0, 2.0, 1, hour,
                            datetime.timedelta(hours=1))
        self.assertEqual(outcome, STOP)

    def test_it_looks_only_inside_the_coarse_bar(self):
        """A minute belonging to the next hour must not be consulted."""
        hour = T0.replace(minute=0)
        minutes = bars([(1.4, 1.5)] * 60 + [(1.5, 2.0)], start=hour)
        outcome, _ = refine(minutes, 1.0, 2.0, 1, hour,
                            datetime.timedelta(hours=1))
        self.assertEqual(outcome, OPEN)

    def test_the_minute_after_the_hour_is_excluded_at_the_boundary(self):
        hour = T0.replace(minute=0)
        minutes = bars([(1.4, 1.5)] * 59 + [(1.5, 2.0)], start=hour)
        outcome, label = refine(minutes, 1.0, 2.0, 1, hour,
                                datetime.timedelta(hours=1))
        self.assertEqual(outcome, TARGET)
        self.assertEqual(label, hour + datetime.timedelta(minutes=59))


class TestResidualAmbiguity(unittest.TestCase):
    """
    How much the reference itself cannot say. M1 is finer than H1, not
    infinitely fine: a minute holding both levels is still a coin flip, and a
    threshold has to sit above that too.
    """

    def test_it_counts_the_bars_holding_both_levels(self):
        frame = bars([(1.0, 2.0), (1.4, 1.5), (1.0, 2.0)])
        self.assertEqual(residual_ambiguity(frame, 1.0, 2.0, 1), 2)

    def test_none_when_every_bar_is_narrow(self):
        frame = bars([(1.4, 1.5), (1.5, 1.6)])
        self.assertEqual(residual_ambiguity(frame, 1.0, 2.0, 1), 0)

    def test_the_side_is_honoured(self):
        frame = split_bars([(1.0, 2.0, 5.0, 5.1)])
        self.assertEqual(residual_ambiguity(frame, 1.0, 2.0, 1), 1)
        self.assertEqual(residual_ambiguity(frame, 5.0, 5.1, -1), 1)
        self.assertEqual(residual_ambiguity(frame, 1.0, 2.0, -1), 0)


if __name__ == "__main__":
    unittest.main()
