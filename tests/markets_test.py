"""
Tests for lib/markets.py: the exchanges' opens in UTC across the clock
changes, and the count of bars after one.
"""

import datetime
import unittest

import pandas as pd

from parity_deriva.lib import markets

D = datetime.date
T = datetime.datetime


class TestOpening(unittest.TestCase):

	def test_new_york_moves_an_hour_in_utc_with_its_clock(self):
		self.assertEqual(markets.opening(markets.NEW_YORK, D(2026, 7, 1)), T(2026, 7, 1, 13, 30))
		self.assertEqual(markets.opening(markets.NEW_YORK, D(2026, 1, 15)), T(2026, 1, 15, 14, 30))

	def test_the_weeks_the_two_sides_of_the_atlantic_disagree(self):
		"""America moved on 8 March 2026 and Europe on the 29th: in between,
		New York opens at 13:30 UTC and London still at 08:00."""
		day = D(2026, 3, 16)
		self.assertEqual(markets.opening(markets.NEW_YORK, day), T(2026, 3, 16, 13, 30))
		self.assertEqual(markets.opening(markets.LONDON, day), T(2026, 3, 16, 8, 0))
		self.assertEqual(markets.opening(markets.LONDON, D(2026, 3, 30)), T(2026, 3, 30, 7, 0))

	def test_sydney_opens_on_the_evening_before_in_utc(self):
		self.assertEqual(markets.opening(markets.SYDNEY, D(2026, 1, 12)), T(2026, 1, 11, 23, 0))
		self.assertEqual(markets.opening(markets.TOKYO, D(2026, 1, 12)), T(2026, 1, 12, 0, 0))

	def test_no_open_at_the_weekend(self):
		self.assertIsNone(markets.opening(markets.NEW_YORK, D(2026, 7, 4)))   # a Saturday
		self.assertIsNone(markets.opening(markets.FRANKFURT, D(2026, 7, 5)))


class TestBarAfterOpen(unittest.TestCase):

	def test_the_first_two_m15_candles_of_new_york(self):
		bars = [T(2026, 7, 1, 13, 0) + datetime.timedelta(minutes=15 * i) for i in range(5)]
		self.assertEqual([markets.barAfterOpen(markets.NEW_YORK, b, 'M15') for b in bars],
						 [95, 96, 1, 2, 3])   # a day of M15 bars since yesterday's open, then today's

	def test_an_h1_bar_holding_the_open_is_the_first(self):
		self.assertEqual(markets.barAfterOpen(markets.NEW_YORK, T(2026, 7, 1, 13), 'H1'), 1)
		self.assertEqual(markets.barAfterOpen(markets.NEW_YORK, T(2026, 7, 1, 14), 'H1'), 2)

	def test_monday_before_the_open_is_still_fridays_session(self):
		friday_open = T(2026, 7, 3, 13, 30)
		monday = T(2026, 7, 6, 9, 0)
		expected = (monday + datetime.timedelta(hours=1) - friday_open) / datetime.timedelta(hours=1)
		self.assertEqual(markets.barAfterOpen(markets.NEW_YORK, monday, 'H1'), int(expected + 0.5))

	def test_a_pandas_timestamp_and_a_timedelta_work_too(self):
		self.assertEqual(markets.barAfterOpen(markets.NEW_YORK, pd.Timestamp('2026-01-15 14:45'),
											  datetime.timedelta(minutes=15)), 2)


if __name__ == '__main__':
	unittest.main()
