"""
Tests for data/calendar.py and scripts/import_calendar.py.

No network. The importer is given a page of its own - a small one, with the
same shape as the site's - because what is under test is the parsing and the
merging, and a test that fetched the real calendar would fail on a quiet week
and pass on a busy one.
"""

import datetime
import os
import shutil
import tempfile
import unittest

import pandas as pd

from parity_deriva.data import calendar
from parity_deriva.scripts import import_calendar

T0 = datetime.datetime(2015, 1, 9, 13, 30)

#: a week as the site serves it: the state assignment, the days array, and an
#: event name with a bracket in it - which is the whole reason the parser
#: balances brackets instead of matching a pattern
PAGE = '''<!DOCTYPE html><html><head><script>
if (typeof window.calendarComponentStates === 'undefined') { window.calendarComponentStates = {} }
window.calendarComponentStates[1] = {
days: [{"date":"Fri <span>Jan 9<\\/span>","dateline":1420761600,"events":[
  {"id":1,"name":"Non-Farm Employment Change [NFP]","currency":"USD",
   "dateline":1420810200,"impactName":"high","impactTitle":"High Impact Expected"},
  {"id":2,"name":"Bank Holiday","currency":"JPY",
   "dateline":1420810200,"impactName":"non-economic","impactTitle":"Non-Economic"},
  {"id":3,"name":"Tentative thing","currency":"EUR",
   "dateline":null,"impactName":"low"},
  {"id":4,"name":"A note with no currency","currency":"",
   "dateline":1420810200,"impactName":"low"}]}],
someOtherKey: 1 };
</script></head><body></body></html>'''

FEED = [
    {"title": "Retail Sales m/m", "country": "GBP",
     "date": "2026-09-20T19:01:00-04:00", "impact": "Medium"},
    {"title": "Bank Holiday", "country": "JPY",
     "date": "2026-09-20T19:00:00-04:00", "impact": "Holiday"},
    {"title": "no date", "country": "USD", "date": "", "impact": "High"},
]


class CalendarFileTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='parity-deriva-cal-')
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.path = os.path.join(self.tmpdir, calendar.FILENAME)

    def rows(self):
        return [('2015-01-09 13:30:00', 'USD', 'high', 'NFP'),
                ('2015-01-09 13:35:00', 'USD', 'high', 'Unemployment Rate'),
                ('2015-01-09 20:00:00', 'EUR', 'low', 'Something small')]

    def test_a_missing_file_is_an_empty_calendar(self):
        """
        Not an error. It is a machine that has not imported the calendar yet,
        and every rule that reads it is off by default anyway.
        """
        frame = calendar.load(self.path)
        self.assertEqual(len(frame), 0)
        self.assertFalse(calendar.Calendar(frame, before=30, after=30)
                         .blocked(T0))

    def test_what_is_written_is_what_comes_back(self):
        calendar.save(calendar.merge(calendar.empty(), self.rows()), self.path)
        frame = calendar.load(self.path)
        self.assertEqual(len(frame), 3)
        self.assertEqual(frame['time'].iloc[0], pd.Timestamp('2015-01-09 13:30'))
        self.assertEqual(frame['title'].iloc[0], 'NFP')

    def test_the_same_event_twice_is_one_event(self):
        """Two overlapping weeks import the same Friday; it happened once."""
        frame = calendar.merge(calendar.empty(), self.rows())
        frame = calendar.merge(frame, self.rows())
        self.assertEqual(len(frame), 3)

    def test_a_file_that_is_not_a_calendar_is_refused(self):
        with open(self.path, 'w') as handle:
            handle.write("a,b\n1,2\n")
        with self.assertRaises(ValueError):
            calendar.load(self.path)


class CalendarWindowTest(unittest.TestCase):
    """The reading a rule does: which events, and how wide a hole each one is."""

    def frame(self):
        return calendar.merge(calendar.empty(), [
            ('2015-01-09 13:30:00', 'USD', 'high', 'NFP'),
            ('2015-01-09 13:35:00', 'USD', 'high', 'Unemployment Rate'),
            ('2015-01-09 15:00:00', 'USD', 'low', 'Small one'),
            ('2015-01-09 16:00:00', 'JPY', 'high', 'Elsewhere'),
        ])

    def test_two_events_close_together_are_one_hole(self):
        """
        Not two. A rule that counted the overlap twice would stand aside for
        longer than it was told to, which is a different rule.
        """
        book = calendar.Calendar(self.frame(), currencies=('EUR', 'USD'),
                                 before=10, after=10)
        # the two high impact releases are five minutes apart, so ten minutes
        # either side of each is one hole from 13:20 to 13:45
        self.assertEqual(len(book.windows), 1)
        self.assertEqual(book.windows[0],
                         (datetime.datetime(2015, 1, 9, 13, 20),
                          datetime.datetime(2015, 1, 9, 13, 45)))
        # and the small one at 15:00 is a second hole once it is asked for
        wider = calendar.Calendar(self.frame(), currencies=('EUR', 'USD'),
                                  impacts=(calendar.HIGH, calendar.LOW),
                                  before=10, after=10)
        self.assertEqual(len(wider.windows), 2)

    def test_the_edges_are_inside(self):
        book = calendar.Calendar(self.frame(), currencies=('USD',),
                                 before=10, after=10)
        self.assertTrue(book.blocked(datetime.datetime(2015, 1, 9, 13, 20)))
        self.assertTrue(book.blocked(datetime.datetime(2015, 1, 9, 13, 45)))
        self.assertFalse(book.blocked(datetime.datetime(2015, 1, 9, 13, 19)))
        self.assertFalse(book.blocked(datetime.datetime(2015, 1, 9, 13, 46)))

    def test_a_currency_the_pair_does_not_trade_is_not_an_event(self):
        book = calendar.Calendar(self.frame(), currencies=('EUR', 'USD'),
                                 before=30, after=30)
        self.assertFalse(book.blocked(datetime.datetime(2015, 1, 9, 16, 0)),
                         "a Japanese release is not news for EUR/USD")

    def test_an_impact_not_asked_for_is_not_an_event(self):
        book = calendar.Calendar(self.frame(), currencies=('USD',),
                                 impacts=(calendar.HIGH,), before=30, after=30)
        self.assertFalse(book.blocked(datetime.datetime(2015, 1, 9, 15, 0)))
        wider = calendar.Calendar(self.frame(), currencies=('USD',),
                                  impacts=(calendar.HIGH, calendar.LOW),
                                  before=30, after=30)
        self.assertTrue(wider.blocked(datetime.datetime(2015, 1, 9, 15, 0)))

    def test_no_width_is_no_rule(self):
        """
        Zero and zero blocks nothing, however many events there are. There is
        no default width here: how long before the payrolls a strategy should
        stand aside is a decision about the strategy.
        """
        book = calendar.Calendar(self.frame(), currencies=('USD',))
        self.assertEqual(book.windows, [])
        self.assertFalse(book.blocked(T0))

    def test_it_can_say_what_is_next(self):
        book = calendar.Calendar(self.frame(), currencies=('USD',),
                                 impacts=(calendar.HIGH,))
        self.assertEqual(book.next(datetime.datetime(2015, 1, 9, 13, 0))['title'],
                         'NFP')
        self.assertIsNone(book.next(datetime.datetime(2015, 1, 10, 0, 0)))


class ImportTest(unittest.TestCase):
    """The parsing, fed a page rather than the site."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='parity-deriva-cal-')
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.path = os.path.join(self.tmpdir, calendar.FILENAME)

    def test_the_week_parameter_is_the_site_s_spelling(self):
        self.assertEqual(import_calendar.week_name(datetime.date(2015, 1, 5)),
                         'jan5.2015')

    def test_the_days_are_read_out_of_the_page_s_own_state(self):
        """
        And the brackets are balanced: an event called "Non-Farm Employment
        Change [NFP]" closes a bracket the array has not opened, and a parser
        that stopped there would import Monday and call it a week.
        """
        days = import_calendar.days_of(PAGE)
        self.assertEqual(len(days), 1)
        self.assertEqual(len(days[0]['events']), 4)

    def test_an_event_is_four_columns_in_utc(self):
        rows = import_calendar.rows_of(import_calendar.days_of(PAGE))
        self.assertEqual(rows[0], ('2015-01-09 13:30:00', 'USD', 'high',
                                   'Non-Farm Employment Change [NFP]'))

    def test_an_event_with_no_time_or_no_currency_is_not_imported(self):
        """
        Neither can be the middle of a window: a tentative release has no
        instant to stand aside from, and a site-wide note is not an event.
        """
        rows = import_calendar.rows_of(import_calendar.days_of(PAGE))
        self.assertEqual(len(rows), 2)
        self.assertEqual([one[3] for one in rows],
                         ['Non-Farm Employment Change [NFP]', 'Bank Holiday'])

    def test_a_page_without_the_state_is_an_error_and_not_an_empty_week(self):
        """
        The difference matters: an empty week written to the file is a week
        that will be skipped forever afterwards.
        """
        with self.assertRaises(ValueError):
            import_calendar.days_of('<html>nothing here</html>')

    def test_the_feed_and_the_page_spell_impact_the_same_way(self):
        rows = import_calendar.rows_of_feed(FEED)
        self.assertEqual(len(rows), 2, "the one with no date is not an event")
        self.assertEqual(rows[0][1:], ('GBP', 'medium', 'Retail Sales m/m'))
        self.assertEqual(rows[1][2], calendar.NON_ECONOMIC,
                         "the feed's Holiday is the page's non-economic")

    def test_the_feed_s_offsets_are_read_and_written_as_utc(self):
        rows = import_calendar.rows_of_feed(FEED)
        self.assertEqual(rows[0][0], '2026-09-20 23:01:00')

    def test_a_week_already_in_the_file_is_not_fetched_again(self):
        asked = []

        def reader(day):
            asked.append(day)
            return PAGE

        argv = ['--from', '2015-01-05', '--to', '2015-01-05',
                '--out', self.path, '--delay', '0']
        self.assertEqual(import_calendar.main(argv, report=lambda line: None,
                                              reader=reader), 0)
        self.assertEqual(len(asked), 1)
        self.assertEqual(len(calendar.load(self.path)), 2)

        self.assertEqual(import_calendar.main(argv, report=lambda line: None,
                                              reader=reader), 0)
        self.assertEqual(len(asked), 1, "the second run asked for nothing")

        self.assertEqual(import_calendar.main(argv + ['--force'],
                                              report=lambda line: None,
                                              reader=reader), 0)
        self.assertEqual(len(asked), 2, "--force asks anyway")
        self.assertEqual(len(calendar.load(self.path)), 2,
                         "and the same events are still one each")

    def test_a_week_that_fails_does_not_stop_the_import(self):
        """
        Six hundred requests over a morning: one refused week is a line in
        the report and a week to fetch next time, not an import abandoned at
        2017 with no file written.
        """
        lines = []

        def reader(day):
            if day.year == 2015:
                raise IOError("403")
            return PAGE

        code = import_calendar.main(
            ['--from', '2015-01-05', '--to', '2015-01-12', '--out', self.path,
             '--delay', '0'], report=lines.append, reader=reader)
        self.assertEqual(code, 0)
        self.assertTrue(any('403' in line for line in lines))

    def test_dry_run_writes_nothing(self):
        import_calendar.main(['--from', '2015-01-05', '--to', '2015-01-05',
                              '--out', self.path, '--delay', '0', '--dry-run'],
                             report=lambda line: None, reader=lambda day: PAGE)
        self.assertFalse(os.path.exists(self.path))


if __name__ == '__main__':
    unittest.main()
