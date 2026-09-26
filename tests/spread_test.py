"""
The shared spread set: lib/spread.py reading spread.json by the time of the
bar, and scripts/spread_profile.py building it.
"""

import datetime
import json
import os
import tempfile
import types
import unittest

import pandas as pd

from parity_deriva.lib import spread
from parity_deriva.lib.spread import SLOTS, SpreadModel, slot
from parity_deriva.scripts import spread_profile

NY = 'America/New_York'
# Friday 17:00 in New York, the rollover, in summer and in winter
ROLLOVER = 4 * 288 + 17 * 12


def entry(values=None):
    slots = [0.00006] * SLOTS
    for i, v in (values or {}).items():
        slots[i] = v
    return {'zone': NY, 'widest': 0.0005, 'slots': slots}


class SlotTest(unittest.TestCase):

    def test_the_rollover_is_one_slot_all_year(self):
        self.assertEqual(slot(datetime.datetime(2026, 9, 25, 21, 0), NY), ROLLOVER)
        self.assertEqual(slot(datetime.datetime(2026, 1, 9, 22, 0), NY), ROLLOVER)

    def test_the_builder_keys_samples_the_same_way(self):
        times = [datetime.datetime(2026, 9, 25, 21, 0), datetime.datetime(2026, 1, 5, 3, 35)]
        got = spread_profile.slots(pd.Series([0.0003, 0.0001], index=pd.DatetimeIndex(times)), NY)
        for when, value in zip(times, (0.0003, 0.0001)):
            self.assertEqual(got[slot(when, NY)], value)
        self.assertEqual(sum(v is not None for v in got), 2)


class ProfileModelTest(unittest.TestCase):

    OHLC = {'o': 1.2000, 'h': 1.2010, 'l': 1.1990, 'c': 1.2005}
    M5 = datetime.timedelta(minutes=5)

    def model(self, spread_setting=None):
        return SpreadModel(spread_setting, {'EUR_USD': entry({ROLLOVER: 0.0003})})

    def test_an_m5_bar_takes_its_slot(self):
        bid, ask = self.model().apply('EUR_USD', self.OHLC, datetime.datetime(2026, 9, 25, 21, 0), self.M5)
        self.assertAlmostEqual(ask['c'] - bid['c'], 0.0003)
        bid, ask = self.model().apply('EUR_USD', self.OHLC, datetime.datetime(2026, 9, 25, 21, 5), self.M5)
        self.assertAlmostEqual(ask['c'] - bid['c'], 0.00006)

    def test_a_longer_bar_takes_the_widest_slot_it_covers(self):
        width = self.model().width('EUR_USD', datetime.datetime(2026, 9, 25, 20, 0),
                                   datetime.timedelta(hours=4))
        self.assertAlmostEqual(width, 0.0003)

    def test_an_empty_slot_or_no_time_takes_the_widest(self):
        model = SpreadModel(None, {'EUR_USD': dict(entry(), slots=[None] * SLOTS)})
        self.assertAlmostEqual(model.width('EUR_USD', datetime.datetime(2026, 9, 26, 12), self.M5), 0.0005)
        self.assertAlmostEqual(self.model().width('EUR_USD'), 0.0005)

    def test_a_stated_number_wins_and_other_instruments_stay_off(self):
        self.assertAlmostEqual(self.model(0.0001).width('EUR_USD', datetime.datetime(2026, 9, 25, 21)), 0.0001)
        self.assertEqual(self.model().apply('GBP_USD', self.OHLC, datetime.datetime(2026, 9, 25, 21)),
                         (None, None))
        self.assertTrue(self.model().enabled())
        self.assertFalse(SpreadModel(None, {}).enabled())


class BuildTest(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.setup = types.SimpleNamespace(DATA_DIR=self.home, INSTRUMENT_PRECISION={'EUR_USD': 5},
                                           DEFAULT_PRICE_PRECISION=5)

    def test_the_widest_source_wins_and_old_sources_are_kept(self):
        broker = [None] * SLOTS
        broker[ROLLOVER] = 0.0009
        kept = {'sources': {'ig': {'measured': '2026-09-01', 'samples': 1, 'slots': broker}}}
        when = pd.DatetimeIndex([datetime.datetime(2026, 9, 25, 21, 0), datetime.datetime(2026, 9, 25, 21, 5)])
        built = spread_profile.build('EUR_USD', {'store': pd.Series([0.0002, 0.00007], index=when)},
                                     kept, '2026-09-26')
        self.assertEqual(sorted(built['sources']), ['ig', 'store'])
        self.assertEqual(built['slots'][ROLLOVER], 0.0009)
        self.assertEqual(built['slots'][ROLLOVER + 1], 0.00007)
        self.assertEqual(built['widest'], 0.0009)

    def test_main_writes_what_the_providers_then_read(self):
        index = pd.DatetimeIndex([datetime.datetime(2026, 9, 25, 21, 0)]).as_unit('us')
        frame = pd.DataFrame({'ask_o': [1.1004], 'ask_h': [1.1010], 'ask_l': [1.1000], 'ask_c': [1.1006],
                              'bid_o': [1.1000], 'bid_h': [1.1006], 'bid_l': [1.0996], 'bid_c': [1.1002],
                              'mid_o': [1.1002], 'mid_h': [1.1008], 'mid_l': [1.0998], 'mid_c': [1.1004],
                              'volume': [1]}, index=index)
        frame.to_hdf(os.path.join(self.home, 'EUR_USD.hd5'), key='M5', format='table')
        out = []
        self.assertEqual(spread_profile.main(['--years', '5', '--write'], setup=self.setup,
                                             out=out.append), 0)
        with open(os.path.join(self.home, spread.FILENAME)) as handle:
            written = json.load(handle)
        self.assertIn('EUR_USD', written['instruments'])
        model = spread.spreadModel(self.setup, 'TWELVEDATA_SPREAD')
        self.assertAlmostEqual(model.width('EUR_USD', datetime.datetime(2026, 9, 25, 21, 0),
                                           datetime.timedelta(minutes=5)), 0.0004)

        from parity_deriva.trading.providers import TwelveDataProvider
        self.assertTrue(TwelveDataProvider(setup=self.setup).capabilities.bid_ask_candles)

    def test_a_reader_does_not_write_without_force(self):
        from parity_deriva.data import market
        with open(os.path.join(self.home, market.FILENAME), 'w') as handle:
            json.dump({'dir': self.home, 'writer': False}, handle)
        with self.assertRaises(market.MarketError):
            spread_profile.main(['--instrument', 'NONE_XXX', '--write'], setup=self.setup, out=lambda s: None)


if __name__ == '__main__':
    unittest.main()
