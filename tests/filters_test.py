"""
Tests for portfolio/filters.py and portfolio/features.py: the conditions
written one way and refused with a clear message when they are not one; the
filter read off the signal's own bar, never the next one; the same filter in
the backtest and in the live wiring.
"""

import datetime
import logging
import os
import shutil
import tempfile
import types
import unittest
from decimal import Decimal
from unittest import mock

from parity_deriva.portfolio import filters
from parity_deriva.portfolio.features import Features
from parity_deriva.tests.web_test import StoreCase
from parity_deriva.web import service as service_module
from parity_deriva.web.service import Service, ServiceError


def bar(when, close, spread=0.001):
    return types.SimpleNamespace(time=when, mid={'o': close, 'h': close + spread, 'l': close - spread, 'c': close})


class ConditionsTest(unittest.TestCase):

    def test_written_one_way_and_refused_clearly(self):
        self.assertEqual(filters.parse(' rsi14 < 55 & hour>=7'), [('rsi14', '<', 55.0), ('hour', '>=', 7.0)])
        self.assertEqual(filters.text(filters.parse('rsi14<55.0&hour>=7')), 'rsi14<55&hour>=7')
        self.assertEqual(filters.parse('none'), [])
        self.assertEqual(filters.parse(''), [])
        with self.assertRaises(filters.FilterError) as caught:
            filters.parse('rsi<55')
        self.assertIn('no feature rsi - there are rsi14, atrpct14', str(caught.exception))
        with self.assertRaises(filters.FilterError) as caught:
            filters.parse('rsi14=55')
        self.assertIn('a condition is a feature', str(caught.exception))
        self.assertIsNone(service_module.parseFilters('none'))
        self.assertEqual(service_module.parseFilters('hour >= 12'), 'hour>=12')
        with self.assertRaises(ServiceError):
            service_module.parseFilters('hour ~ 12')

    def test_the_features_of_the_last_closed_bar(self):
        features = Features()
        start = datetime.datetime(2024, 1, 1)
        for i in range(120):
            features.add(bar(start + datetime.timedelta(hours=i), 1.1 + 0.0001 * i))
        values = features.values()
        self.assertEqual((values['hour'], values['weekday']), (23, 4))
        self.assertGreater(values['rsi14'], 90)
        self.assertGreater(values['slope100'], 0)
        self.assertGreater(values['dist_sma100_atr'], 0)
        self.assertIsNone(Features().values()['rsi14'])
        entry = filters.EntryFilter('rsi14<55', granularity='H1', instrument='EUR_USD')
        self.assertEqual(entry.blocked(), 'rsi14<55 (warming up)')
        entry.features = features
        self.assertTrue(entry.blocked().startswith('rsi14<55 ('))
        passing = filters.EntryFilter('rsi14>55&hour>=23')
        passing.features = features
        self.assertIsNone(passing.blocked())


class BacktestTest(unittest.TestCase):

    def setUp(self):
        home = tempfile.mkdtemp(prefix='parity-deriva-filters-')
        self.addCleanup(shutil.rmtree, home, True)
        StoreCase.store(os.path.join(home, 'EUR_USD.hd5'), bars=600)
        self.service = Service(setup=types.SimpleNamespace(DATA_DIR=home, EQUITY=Decimal('100000')),
                               max_candles=100000)

    def run_(self, **more):
        return self.service.backtest(**dict({'instrument': 'EUR_USD', 'granularity': 'H1', 'strategy': 'AG01',
                                             'confirmed': True}, **more))

    def test_the_signal_bar_decides_never_the_next(self):
        every = self.run_()['trades']
        afternoon = self.run_(filters='hour>=12')['trades']
        hours = [datetime.datetime.fromtimestamp(t['signalTime'] / 1000, datetime.timezone.utc).hour for t in afternoon]
        self.assertTrue(afternoon)
        self.assertLess(len(afternoon), len(every))
        # the 11:00 bar would pass reading the bar after it: none does
        self.assertTrue(all(h >= 12 for h in hours), hours)
        self.assertEqual(self.run_(filters='hour<0')['trades'], [])


class WeekdaysTest(BacktestTest):
    """The days a signal is taken on (C7a): a Saturday's refused, offline and in the live form alike."""

    def test_the_days_written_one_way(self):
        self.assertEqual(service_module.parseWeekdays(' 5 4 3 2 1 '), '12345')
        self.assertIsNone(service_module.parseWeekdays('7654321'))
        self.assertIsNone(service_module.parseWeekdays('none'))
        with self.assertRaises(ServiceError):
            service_module.parseWeekdays('1-5')

    def test_a_signal_on_a_day_not_traded_is_refused(self):
        every = self.run_()['trades']
        days = lambda trades: set(datetime.datetime.fromtimestamp(t['signalTime'] / 1000, datetime.timezone.utc)
                                  .isoweekday() for t in trades)
        # the store's 600 hours are every day of the week, weekends too
        self.assertEqual(len(days(every)), 7)
        mondays = self.run_(weekdays='1')['trades']
        self.assertTrue(mondays)
        self.assertEqual(days(mondays), {1})
        self.assertEqual(days(self.run_(weekdays='67')['trades']), {6, 7})
        self.assertNotIn(6, days(self.run_(weekdays='12345')['trades']))
        from parity_deriva.scripts import live
        spec = live.fromForm('{"strategy": "AG01", "instrument": "EUR_USD", "granularity": "H1", "weekdays": "12345"}')
        self.assertEqual(spec['weekdays'], '12345')


class LiveTest(unittest.TestCase):

    def test_the_live_form_carries_the_same_filter(self):
        from parity_deriva.scripts import live
        spec = live.fromForm('{"strategy": "AG01", "instrument": "EUR_USD", "granularity": "H1", '
                             '"filters": "rsi14 < 55"}')
        self.assertEqual(spec['filters'], 'rsi14<55')
        registered = []
        engine = types.SimpleNamespace(handlers=registered, add_handler=registered.append)
        provider = types.SimpleNamespace(execution=lambda sized=False: 'execution', name='paper')
        args = types.SimpleNamespace(units=1, account='A')

        class Strategy(object):
            def __init__(self, **kw):
                pass
        with mock.patch.object(live, 'getLogger', lambda: logging.getLogger('test')):
            live.wire(engine, provider, dict(spec, risk=None), args, Strategy, 'pairs', ['EUR_USD'], 'H1')
        kinds = [type(h).__name__ for h in registered]
        self.assertEqual(kinds[:3], ['EntryFilter', 'Strategy', 'MoneyManager'])
        self.assertIs(registered[2].filters, registered[0])


if __name__ == '__main__':
    unittest.main()
