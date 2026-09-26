"""
Tests for lib/news.py and the pushed calendar of data/calendar.py: an event's
outcome for a strategy, and never before its minute.
"""

import datetime
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

from parity_deriva.data import calendar
from parity_deriva.lib import news
from parity_deriva.strategy.H4 import H4

HIGHER_IS_GOOD = {'Usual Effect': "'Actual' greater than 'Forecast' is good for currency;"}
LOWER_IS_GOOD = {'Usual Effect': "'Actual' less than 'Forecast' is good for currency;"}


def ff(id, at, event, currency='EUR', impact='high', time='2:15pm', detail=None, **values):
    """One of ForexFactory's events as the scraper keeps it."""
    return dict({'id': id, 'datetime_utc': at, 'time': time, 'currency': currency,
                 'impact': impact, 'event': event, 'detail': detail or {},
                 'actual': '', 'forecast': '', 'previous': '', 'revision': ''}, **values)


# the ECB of 11 September 2025: the number at 12:15, the words at 12:45
RATE = ff(1, '2025-09-11T12:15:00+00:00', 'Main Refinancing Rate', detail=HIGHER_IS_GOOD,
          actual='2.40%', forecast='2.15%', previous='2.15%')
PRESS = ff(2, '2025-09-11T12:45:00+00:00', 'ECB Press Conference', time='2:45pm')
CLAIMS = ff(3, '2025-09-11T12:30:00+00:00', 'Unemployment Claims', currency='USD',
            time='2:30pm', detail=LOWER_IS_GOOD, actual='263K', forecast='235K')
POUND = ff(4, '2025-09-11T12:20:00+00:00', 'Retail Sales m/m', currency='GBP')
HOLIDAY = ff(5, '2025-09-11T12:00:00+00:00', 'Bank Holiday', impact='holiday', time='All Day')


class PushedCalendarTest(unittest.TestCase):

    def test_an_event_is_its_id_and_its_newest_push_wins(self):
        frame = calendar.merge(calendar.empty(), [calendar.fromForexFactory(dict(RATE, actual=''))])
        frame = calendar.merge(frame, [calendar.fromForexFactory(RATE)])
        self.assertEqual(list(frame['actual']), ['2.40%'])
        # the site fixed the minute later: the placeholder goes
        moved = dict(RATE, datetime_utc='2025-09-11T12:16:00+00:00')
        frame = calendar.merge(frame, [calendar.fromForexFactory(moved)])
        self.assertEqual([str(t) for t in frame['time']], ['2025-09-11 12:16:00'])

    def test_a_row_of_four_does_not_replace_a_pushed_one(self):
        frame = calendar.merge(calendar.empty(), [calendar.fromForexFactory(RATE)])
        frame = calendar.merge(frame, [('2025-09-11 12:15:00', 'EUR', 'high', 'Main Refinancing Rate')])
        self.assertEqual((len(frame), frame.iloc[0]['actual']), (1, '2.40%'))

    def test_what_the_site_gives_is_what_this_file_says(self):
        row = calendar.fromForexFactory(HOLIDAY)
        self.assertEqual((row['impact'], row['timed']), (calendar.NON_ECONOMIC, 0))
        row = calendar.fromForexFactory(dict(RATE, datetime_utc='2025-09-11T14:15:00+02:00'))
        self.assertEqual((row['time'], row['timed'], row['effect']),
                         (datetime.datetime(2025, 9, 11, 12, 15), 1, 1))
        self.assertEqual(calendar.fromForexFactory(CLAIMS)['effect'], -1)
        with self.assertRaises(ValueError):
            calendar.fromForexFactory(dict(RATE, id=''))

    def test_a_calendar_saved_through_a_link_stays_a_link(self):
        folder = tempfile.mkdtemp(prefix='parity-deriva-news-')
        self.addCleanup(shutil.rmtree, folder, True)
        real, link = os.path.join(folder, 'calendar.csv'), os.path.join(folder, 'dev.csv')
        calendar.save(calendar.empty(), real)
        os.symlink(real, link)
        calendar.save(calendar.merge(calendar.empty(), [calendar.fromForexFactory(RATE)]), link)
        self.assertTrue(os.path.islink(link))
        self.assertEqual(len(calendar.load(real)), 1)

    def test_a_placeholder_minute_closes_no_window(self):
        frame = calendar.merge(calendar.empty(), [calendar.fromForexFactory(dict(
            RATE, time='Tentative'))])
        self.assertEqual(len(calendar.Calendar(frame, ['EUR'], before=5, after=5).windows), 0)


class AroundTest(unittest.TestCase):

    def setUp(self):
        folder = tempfile.mkdtemp(prefix='parity-deriva-news-')
        self.addCleanup(shutil.rmtree, folder, True)
        self.path = os.path.join(folder, calendar.FILENAME)
        calendar.save(calendar.merge(calendar.empty(), [
            calendar.fromForexFactory(e) for e in (RATE, PRESS, CLAIMS, POUND, HOLIDAY)]), self.path)

    def at(self, hour, minute, **kw):
        return news.around('EUR_USD', datetime.datetime(2025, 9, 11, hour, minute),
                           where=self.path, **kw)

    def test_figures_are_numbers_with_their_unit(self):
        self.assertEqual(news.number('22K'), (22.0, 'K'))
        self.assertEqual(news.number('<0.50%'), (0.5, '%'))
        self.assertEqual(news.number('-0.1%'), (-0.1, '%'))
        self.assertIsNone(news.number('0-2-7'))
        self.assertIsNone(news.number('1.61|3.9'))

    def test_an_event_not_out_by_the_close_has_no_outcome(self):
        rate = self.at(12, 15)[0]
        self.assertEqual((rate['title'], rate['known'], rate['actual'], rate['forecast']),
                         ('Main Refinancing Rate', False, None, 2.15))
        self.assertIsNone(rate['surprise'])

    def test_one_out_has_its_outcome_and_the_conference_is_words(self):
        found = {e['title']: e for e in self.at(12, 30)}
        rate = found['Main Refinancing Rate']
        self.assertTrue(rate['known'])
        self.assertAlmostEqual(rate['surprise'], 0.25)
        self.assertEqual((rate['kind'], rate['unit'], rate['good']), ('release', '%', 1))
        self.assertEqual(found['ECB Press Conference']['kind'], 'talk')

    def test_a_higher_figure_can_be_bad_for_the_currency(self):
        claims = [e for e in self.at(12, 45) if e['currency'] == 'USD'][0]
        self.assertEqual((claims['surprise'], claims['good']), (28.0, -1))

    def test_only_the_instrument_s_currencies_and_the_impacts_asked(self):
        self.assertNotIn('GBP', [e['currency'] for e in self.at(12, 30, impacts=())])
        self.assertEqual([e['kind'] for e in self.at(12, 30, impacts=(calendar.NON_ECONOMIC,))],
                         ['holiday'])
        self.assertEqual(len(self.at(9, 0)), 0)

    def test_a_strategy_asks_at_its_bar_s_close(self):
        strategy = H4(pairs=['EUR_USD'], granularity='M15')
        candle = types.SimpleNamespace(instrument='EUR_USD', time=datetime.datetime(2025, 9, 11, 12, 0))
        with mock.patch.object(calendar, 'path', return_value=self.path):
            rate = [e for e in strategy.news(candle) if e['title'] == 'Main Refinancing Rate'][0]
            self.assertFalse(rate['known'])   # the bar closes at 12:15, the rate is 12:15
            candle.time = datetime.datetime(2025, 9, 11, 12, 15)
            rate = [e for e in strategy.news(candle) if e['title'] == 'Main Refinancing Rate'][0]
            self.assertTrue(rate['known'])


if __name__ == '__main__':
    unittest.main()
