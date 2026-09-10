"""
Tests for the AG strategies, the only ones that trade.

Both react to a change of candle direction ("engulfing") by bracketing the
two-bar range with a pair of opposite pending orders. AG01 brackets it with
STOP orders (breakout); AG02 brackets it with LIMIT orders and swaps the roles
of stop loss and take profit (mean reversion). MoneyManager then turns the
pair into an OCO.
"""

import datetime
import unittest

from parity_deriva.event.event import CandleEvent, StatusEvent, TickEvent
from parity_deriva.strategy.AG01 import AG01
from parity_deriva.strategy.AG02 import AG02
from parity_deriva.tests.helpers import (T0, bull_candle, bear_candle, Recorder,
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

    def test_the_signal_number_is_derived_from_the_data(self):
        """
        Was: datetime.today() to the second - so a replay could never be
             joined to the live run it replayed, and signals landing in the
             same second collided outright (116 shared one key in one replay).
        Now: strategy, instrument, granularity and the candle's own time.
        """
        s = self.make()
        first, second = self.reversal(s)
        self.assertEqual(self.sink.events[0].signalNumber,
                         "AG01:DE30_EUR:M5:%s" % second.time.strftime('%Y%m%dT%H%M%S'))

    def test_replaying_the_same_candles_gives_the_same_key(self):
        first = self.make()
        self.reversal(first)
        keys_one = [e.signalNumber for e in self.sink.events]
        second = self.make()
        self.reversal(second)
        keys_two = [e.signalNumber for e in self.sink.events]
        self.assertEqual(keys_one, keys_two)

    def test_two_instruments_do_not_collide(self):
        s = self.make(pairs=["DE30_EUR", "EUR_USD"])
        for instrument in ("DE30_EUR", "EUR_USD"):
            s.execute_event(self.candle(True, T0, instrument=instrument))
            s.execute_event(self.candle(False, T0 + datetime.timedelta(minutes=1),
                                        instrument=instrument))
        keys = {e.signalNumber for e in self.sink.events}
        self.assertEqual(len(keys), 2)
        self.assertTrue(any('EUR_USD' in k for k in keys))

    def test_successive_candles_do_not_collide(self):
        s = self.make()
        s.execute_event(self.candle(True, T0, base=11700.0))
        s.execute_event(self.candle(False, T0 + datetime.timedelta(minutes=1), base=11710.0))
        s.execute_event(self.candle(True, T0 + datetime.timedelta(minutes=2), base=11720.0))
        keys = {e.signalNumber for e in self.sink.events}
        self.assertEqual(len(keys), 2)

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

    def test_the_expiry_matches_the_configured_time(self):
        """
        Was: replace() was handed second=int(gtdTime[0]) - the hour - where
             the seconds belong, so with the default "23:59:59" every order
             expired at 23:59:23.
        Now: the seconds come from gtdTime[2].
        """
        s = self.make()
        self.reversal(s)
        gtd = self.sink.events[0].gtdTime
        self.assertEqual((gtd.hour, gtd.minute, gtd.second), (23, 59, 59))

    def test_a_custom_expiry_is_honoured_to_the_second(self):
        s = self.make(gtdTime="18:30:45")
        self.reversal(s)
        gtd = self.sink.events[0].gtdTime
        self.assertEqual((gtd.hour, gtd.minute, gtd.second), (18, 30, 45))

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


class TestPerInstrumentPrecision(AGCase):
    """
    The levels a strategy derives are rounded to the instrument's precision.

    Was: every level was rounded to one decimal place - the DAX's precision -
         so on EUR_USD the take profit collapsed to 1.2, below the entry of a
         buy. The stop always won and a live order would have been rejected.
    Now: the precision comes from etc/settings.INSTRUMENT_PRECISION.
    """

    strategy = AG01

    def fx_candle(self, up, dt, base):
        """An EUR_USD-scale candle: five decimals, a 1.4 pip spread."""
        o, c = (base, base + 0.0003) if up else (base + 0.0003, base)
        def px(delta):
            return {"o": "%.5f" % (o + delta), "h": "%.5f" % (max(o, c) + 0.0006 + delta),
                    "l": "%.5f" % (min(o, c) - 0.0006 + delta), "c": "%.5f" % (c + delta)}
        ev = CandleEvent({"time": dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z",
                          "volume": 10, "complete": True,
                          "ask": px(0.00007), "bid": px(-0.00007), "mid": px(0.0)})
        ev.instrument, ev.granularity = "EUR_USD", "H1"
        return ev

    def fx_reversal(self, s):
        first = self.fx_candle(True, T0, 1.22000)
        second = self.fx_candle(False, T0 + datetime.timedelta(hours=1), 1.22050)
        s.execute_event(first)
        s.execute_event(second)
        return first, second

    def make_fx(self):
        s = AG01(pairs=["EUR_USD"], granularity="H1")
        self.sink = Recorder()
        s.set_queue(self.sink)
        return s

    def test_the_fx_take_profit_keeps_five_decimals(self):
        s = self.make_fx()
        first, second = self.fx_reversal(s)
        buy = self.sink.events[0]
        spread = second.ask['c'] - second.bid['c']
        expected = round(buy.price + (buy.price - buy.stopLoss) * 1.2 + spread, 5)
        self.assertEqual(buy.takeProfit, expected)

    def test_the_fx_buy_take_profit_is_above_the_entry(self):
        s = self.make_fx()
        self.fx_reversal(s)
        buy = self.sink.events[0]
        self.assertGreater(buy.takeProfit, buy.price)
        self.assertLess(buy.stopLoss, buy.price)

    def test_the_fx_sell_take_profit_is_below_the_entry(self):
        s = self.make_fx()
        self.fx_reversal(s)
        sell = self.sink.events[1]
        self.assertLess(sell.takeProfit, sell.price)
        self.assertGreater(sell.stopLoss, sell.price)

    def test_one_decimal_rounding_would_have_broken_both_legs(self):
        s = self.make_fx()
        self.fx_reversal(s)
        buy, sell = self.sink.events
        self.assertEqual(round(buy.takeProfit, 1), 1.2)
        self.assertLess(round(buy.takeProfit, 1), buy.stopLoss)
        self.assertEqual(round(sell.takeProfit, 1), 1.2)

    def test_the_index_levels_are_unchanged(self):
        """DE30_EUR keeps one decimal, so nothing the strategy ever traded moves."""
        s = self.make()
        first, second = self.reversal(s)
        buy = self.sink.events[0]
        spread = second.ask['c'] - second.bid['c']
        self.assertEqual(buy.takeProfit,
                         round(buy.price + (buy.price - buy.stopLoss) * 1.2 + spread, 1))

    def test_ag02_also_uses_the_instrument_precision(self):
        s = AG02(pairs=["EUR_USD"], granularity="H1")
        self.sink = Recorder()
        s.set_queue(self.sink)
        self.fx_reversal(s)
        fade = self.sink.events[0]
        spread = 0.00014
        self.assertGreater(fade.stopLoss, fade.price)
        self.assertEqual(fade.stopLoss, round(fade.stopLoss, 5))
        self.assertNotEqual(round(fade.stopLoss, 1), fade.stopLoss)


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
