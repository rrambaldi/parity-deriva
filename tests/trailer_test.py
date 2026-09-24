"""
The two stops portfolio/trailer.py walks for the account's rules rather than
for a strategy: one that follows at a distance (trailing) and one that
starts at the target (trailProfit). The ladder itself is a strategy's, and is
tested next to the strategy that carries it.
"""

import datetime
import unittest

from parity_deriva.event.event import CandleEvent, OrderEvent, TransactionEvent
from parity_deriva.portfolio.trailer import Trailer
from parity_deriva.tests.helpers import T0, Recorder


def candle(n, high, low):
    """A daily EUR_USD bar with no spread: bid, ask and mid all the same."""
    px = {'o': low, 'h': high, 'l': low, 'c': high}
    ev = CandleEvent({'time': (T0 + datetime.timedelta(days=n)).strftime(
                          '%Y-%m-%dT%H:%M:%S.%f') + '000Z',
                      'volume': 1, 'complete': True,
                      'ask': dict(px), 'bid': dict(px), 'mid': dict(px)})
    ev.instrument, ev.granularity = 'EUR_USD', 'D'
    return ev


class TrailerCase(unittest.TestCase):

    def walk(self, bars, units=1, **order):
        t = Trailer(granularity='D')
        sink = Recorder()
        t.set_queue(sink)
        t.execute_event(OrderEvent(dict({'signalNumber': 'K', 'instrument': 'EUR_USD',
                                         'price': 1.3000, 'units': units,
                                         'stopLoss': 1.2900 if units > 0 else 1.3100},
                                        **order)))
        t.execute_event(TransactionEvent({'type': 'ORDER_FILL', 'orderID': 7,
                                          'signalNumber': 'K', 'instrument': 'EUR_USD',
                                          'price': 1.3000, 'time': T0}))
        for n, (high, low) in enumerate(bars, start=1):
            t.execute_event(candle(n, high, low))
        return [round(e.price, 5) for e in sink.of('STOPMODIFY')]


class TestFollow(TrailerCase):

    def test_the_stop_follows_the_high_at_its_distance_and_only_up(self):
        stops = self.walk([(1.3050, 1.2990), (1.3020, 1.2980), (1.3120, 1.3000)],
                          trailDistance=0.0100)
        self.assertEqual(stops, [1.2950, 1.3020])

    def test_a_short_follows_the_low(self):
        stops = self.walk([(1.3010, 1.2950)], units=-1, trailDistance=0.0100)
        self.assertEqual(stops, [1.3050])


class TestStraddle(unittest.TestCase):
    """A signal with two legs whose fill gapped past its level."""

    def test_the_gapped_leg_is_found_by_its_side_and_trailed(self):
        t = Trailer(granularity='D')
        sink = Recorder()
        t.set_queue(sink)
        for price, units, stop in ((1.3000, 1, 1.2900), (1.2900, -1, 1.3000)):
            t.execute_event(OrderEvent({'signalNumber': 'K', 'instrument': 'EUR_USD',
                                        'price': price, 'units': units, 'stopLoss': stop,
                                        'trailDistance': 0.0100}))
        # the short fills a pip under its level: the exact join misses
        t.execute_event(TransactionEvent({'type': 'ORDER_FILL', 'orderID': 8,
                                          'signalNumber': 'K', 'instrument': 'EUR_USD',
                                          'price': 1.2899, 'units': -1, 'time': T0}))
        t.execute_event(candle(1, 1.2890, 1.2800))
        self.assertEqual([round(e.price, 5) for e in sink.of('STOPMODIFY')], [1.2900])


class TestFromEntry(TrailerCase):

    def test_the_follower_starts_at_break_even_not_on_the_first_bar(self):
        # entry 1.3000, stop 1.2900, following 40 pips from break even: a high
        # of 1.3030 would put it at 1.2990, under the entry, so nothing moves;
        # 1.3050 puts it at 1.3010, and from there it follows
        stops = self.walk([(1.3030, 1.2990), (1.3050, 1.3020), (1.3100, 1.3060)],
                          trailDistance=0.0040, trailFromEntry=True)
        self.assertEqual(stops, [1.3010, 1.3060])

    def test_a_short_from_break_even(self):
        stops = self.walk([(1.3010, 1.2970), (1.2980, 1.2950)], units=-1,
                          trailDistance=0.0040, trailFromEntry=True)
        self.assertEqual(stops, [1.2990])


class TestFloor(TrailerCase):

    def test_nothing_moves_before_the_target(self):
        self.assertEqual(self.walk([(1.3090, 1.2990)], trailTarget=1.3100), [])

    def test_the_target_reached_puts_the_stop_on_it_then_follows_at_the_risk(self):
        # risk 100 pips: the stop sits on the target until the market is
        # 100 pips past it
        stops = self.walk([(1.3100, 1.3000), (1.3150, 1.3080), (1.3250, 1.3150)],
                          trailTarget=1.3100)
        self.assertEqual(stops, [1.3100, 1.3150])


if __name__ == '__main__':
    unittest.main()
