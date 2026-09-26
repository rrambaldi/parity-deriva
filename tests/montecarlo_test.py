"""
Tests for performance/montecarlo.py and performance/baseline.py: the same seed
the same band, a band that widens as the trades go on, the losing streak, the
first trade under the band; random entries land around the middle of the
baseline and entries that know the future at its top.
"""

import math
import random
import unittest

from parity_deriva.performance import baseline, montecarlo

HOUR = 3600000


def trade(pl, balance):
    return {'pl': pl, 'balance': balance}


class BandTest(unittest.TestCase):

    def setUp(self):
        rng = random.Random(7)
        self.values = [rng.choice((0.02, 0.015, -0.01, -0.01)) for _ in range(120)]

    def test_returns_are_over_the_balance_before_each_trade(self):
        self.assertEqual(montecarlo.returns([trade(10.0, 1010.0), trade(-101.0, 909.0), trade(None, None)]),
                         [10 / 1000.0, -101 / 1010.0])
        # no balance kept: the start, compounded
        self.assertEqual(montecarlo.returns([{'pl': 50.0}, {'pl': -105.0}], start=1000.0), [0.05, -0.1])

    def test_the_same_seed_the_same_band_and_it_widens(self):
        a, b = montecarlo.band(self.values, seed=3), montecarlo.band(self.values, seed=3)
        self.assertEqual(a, b)
        self.assertNotEqual(a, montecarlo.band(self.values, seed=4))
        width = [high - low for low, high in zip(a['p5'], a['p95'])]
        self.assertLess(width[0], width[30])
        self.assertLess(width[30], width[-1])
        self.assertTrue(all(low <= mid <= high for low, mid, high in zip(a['p5'], a['p50'], a['p95'])))
        self.assertEqual((len(a['p5']), a['trades'], a['n']), (120, 120, 1000))
        self.assertEqual(len(montecarlo.band(self.values, length=300)['p50']), 300)
        self.assertLessEqual(a['streak']['p50'], a['streak']['p95'])
        self.assertIsNone(montecarlo.band([]))

    def test_the_first_trade_under_the_band(self):
        drawn = montecarlo.band(self.values)
        good = montecarlo.compounded([0.02] * 60)
        self.assertIsNone(montecarlo.below(good, drawn))
        bad = montecarlo.compounded([-0.01] * 30)
        self.assertEqual(montecarlo.below(bad, drawn), next(
            i + 1 for i, v in enumerate(bad) if v < drawn['p5'][i]))
        self.assertEqual(montecarlo.streak([0.1, -0.1, -0.2, 0.1, -0.1]), 2)


def candles(n=4000, seed=1):
    """A random walk, hourly, with a spread: the payload's rows."""
    rng = random.Random(seed)
    rows, price, t = [], 1.1, 1577836800000
    for _ in range(n):
        o = price
        c = o + rng.gauss(0, 0.0008)
        h, l = max(o, c) + abs(rng.gauss(0, 0.0004)), min(o, c) - abs(rng.gauss(0, 0.0004))
        rows.append([t, o, h, l, c, h + 0.00005, l + 0.00005, h - 0.00005, l - 0.00005])
        price, t = c, t + HOUR
    return rows


def trades(rows, n, seed, know=False):
    """Trades on the rows: random ones, or ones that take the side the next bars go."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        i = rng.randrange(len(rows) - 30)
        side = rng.choice((1, -1))
        if know:
            side = 1 if rows[i + 10][4] > rows[i][1] else -1
        entry, d = rows[i][1], 0.002
        stop, target = entry - side * d, entry + side * d
        exit_, j = None, i
        for j in range(i, i + 30):
            low, high = (rows[j][8], rows[j][7]) if side > 0 else (rows[j][6], rows[j][5])
            if low <= stop <= high:
                exit_ = stop
                break
            if low <= target <= high:
                exit_ = target
                break
        exit_ = rows[j][4] if exit_ is None else exit_
        out.append({'entryTime': rows[i][0], 'entryPrice': entry, 'stopLoss': stop, 'takeProfit': target,
                    'exitPrice': exit_, 'units': side, 'pl': (exit_ - entry) * side,
                    'entryIndex': i, 'exitIndex': j})
    return out


class BaselineTest(unittest.TestCase):

    def test_random_entries_land_in_the_middle_and_foresight_at_the_top(self):
        rows = candles()
        lucky = baseline.baseline(trades(rows, 150, seed=5), rows, reps=100, seed=2)
        self.assertEqual((lucky['reps'], lucky['trades']), (100, 150))
        self.assertTrue(10 <= lucky['percentile'] <= 90, lucky['percentile'])
        seer = baseline.baseline(trades(rows, 150, seed=5, know=True), rows, reps=100, seed=2)
        self.assertGreaterEqual(seer['percentile'], 95)
        self.assertEqual(baseline.baseline(trades(rows, 150, seed=5), rows, reps=100, seed=2), lucky)

    def test_too_few_trades_say_nothing(self):
        rows = candles(200)
        self.assertIsNone(baseline.baseline(trades(rows, 5, seed=1), rows))
        self.assertTrue(math.isclose(baseline.profitFactor([2.0, -1.0, -1.0]), 1.0))
        self.assertIsNone(baseline.profitFactor([1.0]))


if __name__ == '__main__':
    unittest.main()
