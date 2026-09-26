"""
Tests for the simulator's costs (backtest/oanda.py costs, PIANO-FASE1 C7b):
a commission a side, per lot or per trade; the financing of each night a
trade is held, long and short; both in the trade's P&L and the report's total.
"""

import datetime
import os
import shutil
import tempfile
import types
import unittest
from decimal import Decimal

from parity_deriva.backtest import oanda
from parity_deriva.tests.web_test import StoreCase
from parity_deriva.web.service import Service


class CostsTest(unittest.TestCase):

    def simulator(self, commission=None, financing=None):
        return oanda.OANDABacktester(setup=types.SimpleNamespace(COMMISSION=commission, FINANCING=financing))

    def trade(self, units, opened):
        return types.SimpleNamespace(units=units, instrument='EUR_USD', filledAt=opened)

    def test_a_trade_held_three_nights_pays_three_of_them(self):
        sim = self.simulator(financing='EUR_USD:-3.65/0.73')
        monday, thursday = datetime.datetime(2026, 9, 21, 10), datetime.datetime(2026, 9, 24, 10)
        commission, financing = sim.costs(self.trade(100000, monday), 1.1, thursday)
        self.assertEqual(commission, 0.0)
        self.assertAlmostEqual(financing, 3 * -0.0365 / 365 * 100000 * 1.1)
        # a short earns this rate; in and out on one day pays none
        self.assertAlmostEqual(sim.costs(self.trade(-100000, monday), 1.1, thursday)[1],
                               3 * 0.0073 / 365 * 100000 * 1.1)
        self.assertEqual(sim.costs(self.trade(100000, monday), 1.1, datetime.datetime(2026, 9, 21, 20))[1], 0.0)
        self.assertEqual(oanda.nights(datetime.datetime(2026, 9, 21, 20), datetime.datetime(2026, 9, 21, 22)), 1)

    def test_a_commission_a_side_per_lot_or_per_trade(self):
        monday = datetime.datetime(2026, 9, 21, 10)
        self.assertEqual(self.simulator('3.5/lot').costs(self.trade(200000, monday), 1.1, monday)[0], -14.0)
        self.assertEqual(self.simulator('2/trade').costs(self.trade(50000, monday), 1.1, monday)[0], -4.0)
        self.assertEqual(self.simulator('nonsense').costs(self.trade(50000, monday), 1.1, monday)[0], 0.0)


class BacktestTest(unittest.TestCase):

    def test_the_costs_are_in_the_pl_and_the_report(self):
        home = tempfile.mkdtemp(prefix='parity-deriva-costs-')
        self.addCleanup(shutil.rmtree, home, True)
        StoreCase.store(os.path.join(home, 'EUR_USD.hd5'), bars=300)
        run = lambda **cfg: Service(setup=types.SimpleNamespace(DATA_DIR=home, EQUITY=Decimal('100000'), **cfg),
                                    max_candles=100000).backtest(instrument='EUR_USD', granularity='H1',
                                                                 strategy='AG01', confirmed=True)
        free, paid = run(), run(COMMISSION='1/trade')
        self.assertEqual(free['report']['costs'], 0.0)
        closed = [t for t in paid['trades'] if t['pl'] is not None]
        self.assertTrue(closed)
        self.assertAlmostEqual(paid['report']['costs'], -2.0 * len(closed))
        self.assertAlmostEqual(paid['report']['net'], free['report']['net'] - 2.0 * len(closed))
        self.assertEqual(closed[0]['costs'], -2.0)


if __name__ == '__main__':
    unittest.main()
