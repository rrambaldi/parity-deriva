"""
Characterisation tests for the AG strategies, the only ones that trade.

Both react to a change of candle direction ("engulfing") by bracketing the
two-bar range with a pair of opposite pending orders. AG01 brackets it with
STOP orders (breakout); AG02 brackets it with LIMIT orders and swaps the roles
of stop loss and take profit (mean reversion). MoneyManager then turns the
pair into an OCO.
"""

import datetime
import unittest

from qsforex.event.event import CandleEvent, StatusEvent, TickEvent
from qsforex.strategy.AG01 import AG01
from qsforex.strategy.AG02 import AG02
from qsforex.tests.helpers import (T0, bull_candle, bear_candle, Recorder,
                                   candle_dict)


class AGCase(unittest.TestCase):

    strategy = None

    def make(self, **kw):
        kw.setdefault('pairs', ["DE30_EUR"])
        s = self.strategy(**kw)
        self.sink = Recorder()
        s.set_queue(self.sink)
        return s

    def candle(self, up, dt=T0, base=11700.0, instrument="DE30_EUR"):
        maker = bull_candle if up else bear_candle
        ev = CandleEvent(maker(dt, base=base))
        ev.instrument = instrument
        ev.granularity = "M1"
        return ev

    def reversal(self, s, base_a=11700.0, base_b=11710.0):
        """Feed a bull then a bear bar: the second one triggers the straddle."""
        first = self.candle(True, T0, base=base_a)
        second = self.candle(False, T0 + datetime.timedelta(minutes=1), base=base_b)
        s.execute_event(first)
        s.execute_event(second)
        return first, second


class TestAG01Breakout(AGCase):

    strategy = AG01

    def test_defaults(self):
        s = self.make()
        self.assertEqual(s.pairs, ["DE30_EUR"])
        self.assertEqual(s.granularity, 'M5')
        self.assertEqual(s.gtdTime, ['23', '59', '59'])
        self.assertFalse(s.invested)

    def test_the_first_candle_only_primes_the_buffer(self):
        s = self.make()
        s.execute_event(self.candle(True))
        self.assertEqual(self.sink.events, [])
        self.assertIsNotNone(s.prev["DE30_EUR"])

    def test_no_signal_while_the_direction_holds(self):
        s = self.make()
        s.execute_event(self.candle(True, T0))
        s.execute_event(self.candle(True, T0 + datetime.timedelta(minutes=1)))
        self.assertEqual(self.sink.events, [])

    def test_a_reversal_emits_exactly_two_signals(self):
        s = self.make()
        self.reversal(s)
        self.assertEqual(self.sink.kinds(), ['SIGNAL', 'SIGNAL'])

    def test_the_two_legs_are_opposite(self):
        s = self.make()
        self.reversal(s)
        buy, sell = self.sink.events
        self.assertEqual(buy.units, 1)
        self.assertEqual(sell.units, -1)

    def test_orders_are_stops(self):
        s = self.make()
        self.reversal(s)
        for leg in self.sink.events:
            self.assertEqual(leg.orderType, "STOP")
            self.assertEqual(leg.type, "STOP")

    def test_the_buy_leg_brackets_the_two_bar_range(self):
        s = self.make()
        first, second = self.reversal(s)
        buy = self.sink.events[0]
        self.assertEqual(buy.price, max(first.ask['h'], second.ask['h']))
        self.assertEqual(buy.stopLoss, min(first.bid['l'], second.bid['l']))
        self.assertGreater(buy.price, buy.stopLoss)

    def test_the_sell_leg_is_the_mirror_image(self):
        s = self.make()
        first, second = self.reversal(s)
        sell = self.sink.events[1]
        self.assertEqual(sell.price, min(first.bid['l'], second.bid['l']))
        self.assertEqual(sell.stopLoss, max(first.ask['h'], second.ask['h']))
        self.assertLess(sell.price, sell.stopLoss)

    def test_take_profit_is_1_2_times_the_risk_plus_the_spread(self):
        s = self.make()
        first, second = self.reversal(s)
        buy = self.sink.events[0]
        spread = second.ask['c'] - second.bid['c']
        expected = round(buy.price + (buy.price - buy.stopLoss) * 1.2 + spread, 1)
        self.assertEqual(buy.takeProfit, expected)
        self.assertGreater(buy.takeProfit, buy.price)

    def test_the_sell_take_profit_sits_below_the_entry(self):
        s = self.make()
        self.reversal(s)
        sell = self.sink.events[1]
        self.assertLess(sell.takeProfit, sell.price)

    def test_both_legs_share_one_signal_number(self):
        """That shared id is what lets MoneyManager treat them as an OCO pair."""
        s = self.make()
        self.reversal(s)
        buy, sell = self.sink.events
        self.assertEqual(buy.signalNumber, sell.signalNumber)
        self.assertEqual(len(buy.signalNumber), 14)   # %Y%m%d%H%M%S

    def test_the_client_extension_tags_the_strategy(self):
        s = self.make()
        self.reversal(s)
        ext = self.sink.events[0].clientExtension
        self.assertEqual(ext['tag'], 'AG01')
        self.assertEqual(ext['comment'], 'M5')
        self.assertEqual(ext['id'], self.sink.events[0].signalNumber)

    def test_signal_type_is_exclusive(self):
        s = self.make()
        self.reversal(s)
        self.assertEqual(self.sink.events[0].signalType, 'EXCLUSIVE')

    def test_both_legs_carry_the_triggering_candle_time(self):
        """
        The second leg is rebuilt from the first one's dict, so its timestamp
        goes back through the Event coercion. Since parse_time now passes
        datetimes through, both legs agree; before the Python 3 migration the
        copied leg silently became 1970-01-01.
        """
        s = self.make()
        first, second = self.reversal(s)
        buy, sell = self.sink.events
        self.assertEqual(buy.time, second.time)
        self.assertEqual(sell.time, second.time)

    def test_gtd_time_uses_the_hour_where_the_seconds_belong(self):
        """
        gtdTime is split into [hh, mm, ss] but the replace() call passes
        second=int(self.gtdTime[0]) - the hour - instead of [2]. With the
        default "23:59:59" the expiry lands at 23:59:23.
        """
        s = self.make()
        self.reversal(s)
        gtd = self.sink.events[0].gtdTime
        self.assertEqual((gtd.hour, gtd.minute, gtd.second), (23, 59, 23))

    def test_a_custom_gtd_time_shows_the_same_substitution(self):
        s = self.make(gtdTime="18:30:45")
        self.reversal(s)
        gtd = self.sink.events[0].gtdTime
        self.assertEqual((gtd.hour, gtd.minute, gtd.second), (18, 30, 18))

    def test_candles_for_other_instruments_are_ignored(self):
        s = self.make()
        s.execute_event(self.candle(True, T0, instrument="EUR_USD"))
        s.execute_event(self.candle(False, T0, instrument="EUR_USD"))
        self.assertEqual(self.sink.events, [])

    def test_non_candle_events_are_ignored(self):
        s = self.make()
        self.assertIsNone(s.execute_event(StatusEvent('DONE')))
        self.assertIsNone(s.execute_event(TickEvent({"instrument": "DE30_EUR"})))
        self.assertEqual(self.sink.events, [])

    def test_without_a_queue_nothing_is_emitted_but_state_still_advances(self):
        s = self.strategy(pairs=["DE30_EUR"])
        first, second = self.reversal(s)
        self.assertIs(s.prev["DE30_EUR"], second)

    def test_the_buffer_advances_to_the_triggering_candle(self):
        s = self.make()
        first, second = self.reversal(s)
        self.assertIs(s.prev["DE30_EUR"], second)

    def test_two_reversals_in_a_row_produce_two_pairs(self):
        s = self.make()
        s.execute_event(self.candle(True, T0, base=11700.0))
        s.execute_event(self.candle(False, T0 + datetime.timedelta(minutes=1), base=11710.0))
        s.execute_event(self.candle(True, T0 + datetime.timedelta(minutes=2), base=11720.0))
        self.assertEqual(len(self.sink.of('SIGNAL')), 4)

    def test_a_flat_candle_counts_as_a_direction_change(self):
        """direction() returns 0 for open == close, which differs from 1."""
        s = self.make()
        s.execute_event(self.candle(True, T0))
        flat = CandleEvent(candle_dict(T0 + datetime.timedelta(minutes=1),
                                       o=11700.0, h=11706.0, l=11694.0, c=11700.0))
        flat.instrument, flat.granularity = "DE30_EUR", "M1"
        s.execute_event(flat)
        self.assertEqual(len(self.sink.of('SIGNAL')), 2)


class TestAG02MeanReversion(AGCase):

    strategy = AG02

    def test_orders_are_limits(self):
        s = self.make()
        self.reversal(s)
        for leg in self.sink.events:
            self.assertEqual(leg.orderType, "LIMIT")
            self.assertEqual(leg.type, "LIMIT")

    def test_the_first_leg_sells_the_high_instead_of_buying_it(self):
        """AG01 buys the breakout of the high; AG02 fades it."""
        s = self.make()
        first, second = self.reversal(s)
        fade = self.sink.events[0]
        self.assertEqual(fade.units, -1)
        self.assertEqual(fade.price, max(first.ask['h'], second.ask['h']))

    def test_stop_and_target_are_swapped_relative_to_ag01(self):
        s = self.make()
        first, second = self.reversal(s)
        fade = self.sink.events[0]
        self.assertEqual(fade.takeProfit, min(first.bid['l'], second.bid['l']))
        self.assertGreater(fade.stopLoss, fade.price)
        self.assertLess(fade.takeProfit, fade.price)

    def test_the_second_leg_buys_the_low(self):
        s = self.make()
        first, second = self.reversal(s)
        bounce = self.sink.events[1]
        self.assertEqual(bounce.units, 1)
        self.assertEqual(bounce.price, min(first.bid['l'], second.bid['l']))
        self.assertGreater(bounce.takeProfit, bounce.price)

    def test_the_stop_is_1_2_times_the_target_distance(self):
        """
        Note the asymmetry against AG01: here the 1.2 multiplier is applied to
        the *stop*, so AG02 risks 1.2 to make 1 while AG01 risks 1 to make 1.2.
        """
        s = self.make()
        first, second = self.reversal(s)
        fade = self.sink.events[0]
        spread = second.ask['c'] - second.bid['c']
        expected = round(fade.price + (fade.price - fade.takeProfit) * 1.2 + spread, 1)
        self.assertEqual(fade.stopLoss, expected)

    def test_the_client_extension_tags_ag02(self):
        s = self.make()
        self.reversal(s)
        self.assertEqual(self.sink.events[0].clientExtension['tag'], 'AG02')


if __name__ == "__main__":
    unittest.main()
