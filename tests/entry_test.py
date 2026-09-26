"""
Tests for performance/entry.py: trades that win when the signal came in the
morning give a morning filter as a candidate; trades with random results give
none; the count of bands looked at and the chance they carry are said.
"""

import random
import unittest

from parity_deriva.performance import entry

HOUR = 3600000
START = 1704067200000  # 2024-01-01 00:00 UTC


def candles(n):
    """Hourly rows as the payload has them: time, mid o h l c, ask h l, bid h l."""
    rng, price, out = random.Random(3), 1.1, []
    for i in range(n):
        o, c = price, price + rng.gauss(0, 0.0005)
        h, l = max(o, c) + 0.0002, min(o, c) - 0.0002
        out.append([START + i * HOUR, o, h, l, c, h, l, h, l])
        price = c
    return out


def trades(n, won, seed=1):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        i = rng.randrange(150, 2000)
        r = 1.5 if won(i) else -1.0
        out.append({'signalIndex': i, 'r': r, 'pl': r * 10.0})
    return sorted(out, key=lambda t: t['signalIndex'])


class AnalysisTest(unittest.TestCase):

    def test_a_morning_edge_is_a_candidate(self):
        rows = candles(2100)
        # the morning wins, the rest of the day loses more often than not
        found = entry.analysis({'trades': trades(400, lambda i: i % 24 < 9 or random.Random(i).random() < 0.3),
                                'candles': rows})
        self.assertIsNotNone(found['overall'])
        hour = next(f for f in found['features'] if f['name'] == 'hour')
        self.assertEqual(len(hour['bands']), 5)
        self.assertEqual(sum(b['n'] for b in hour['bands']), 400)
        self.assertIn('hour', [c['name'] for c in found['candidates']])
        best = next(c for c in found['candidates'] if c['name'] == 'hour')
        self.assertTrue(best['filter'].startswith('hour<'), best)
        self.assertGreater(best['ci'][0], found['overall']['expectancyR'])
        self.assertEqual(found['chance'], round(found['tried'] * 0.05, 1))

    def test_random_results_give_no_more_candidates_than_chance_says(self):
        # about N x 5% of the bands come out good by chance, and the answer
        # says so: over twenty runs of trades that win at random, no more
        rows = candles(2100)
        counts, chance = [], None
        for seed in range(20):
            rng = random.Random(100 + seed)
            found = entry.analysis({'trades': trades(400, lambda i: rng.random() < 0.4, seed=seed), 'candles': rows})
            counts.append(len(found['candidates']))
            chance = found['chance']
        self.assertLessEqual(sum(counts) / len(counts), chance)

    def test_a_trade_without_a_stop_or_a_result_is_left_out(self):
        rows = candles(300)
        found = entry.analysis({'trades': [{'signalIndex': 200, 'r': None, 'pl': 5.0},
                                           {'signalIndex': 201, 'r': 1.0, 'pl': None}], 'candles': rows})
        self.assertIsNone(found['overall'])
        self.assertEqual(found['candidates'], [])
        low, high = entry.block_ci([random.Random(i).gauss(1.5, 1.0) for i in range(60)])
        self.assertLess(low, 1.5)
        self.assertGreater(high, 1.5)


if __name__ == '__main__':
    unittest.main()
