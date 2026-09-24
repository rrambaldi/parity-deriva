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
from parity_deriva.strategy.AG01MOD import AG01MOD
from parity_deriva.tests.helpers import (T0, bull_candle, bear_candle, Recorder,
                                   candle_dict)


class AGCase(unittest.TestCase):

    strategy = None

    def make(self, **kw):
        kw.setdefault('pairs', ["DE30_EUR"])
        # the candles below are stamped M1, and a strategy reads one stream:
        # a backtest can now put two granularities of one instrument on the
        # bus, so a handler whose granularity does not match the candle's is
        # a handler watching the other one.
        kw.setdefault('granularity', "M1")
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
        s = self.strategy(pairs=["DE30_EUR"])
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
                         "AG01:DE30_EUR:M1:%s" % second.time.strftime('%Y%m%dT%H%M%S'))

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
        self.assertEqual(ext['comment'], 'M1')
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
        s = self.strategy(pairs=["DE30_EUR"], granularity="M1")
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


class TestExpiryComesFromTheCandle(AGCase):
    """
    The expiry is a function of the data, like the signal's own key is.

    Was: datetime.today().replace(hour=23, ...), the machine's clock. Live
         that is roughly the candle's day; replaying 2022 in 2026 it is four
         years in the future, so no order could ever expire and the simulator
         filled one four months after it was placed.
    """

    strategy = AG01

    def test_the_expiry_is_the_candle_s_day_and_not_the_clock_s(self):
        s = self.make()
        _first, second = self.reversal(s)
        expiry = self.sink.events[0].gtdTime
        # T0 is 2017, the machine's clock is not
        self.assertEqual(expiry.date(), second.time.date())
        self.assertEqual((expiry.hour, expiry.minute, expiry.second),
                         (23, 59, 59))

    def test_replaying_the_same_candles_gives_the_same_expiry(self):
        # make() hands out a fresh sink, so each run is read before the next
        self.reversal(self.make())
        first = [e.gtdTime for e in self.sink.events]
        self.reversal(self.make())
        self.assertEqual([e.gtdTime for e in self.sink.events], first)

    def test_a_daily_bar_expires_at_the_end_of_the_day_it_closes_in(self):
        """
        A daily candle's signal is made at its close, which is midnight of the
        next day. Measured from the open the order would be born expired, and
        a daily backtest would enter nothing at all.
        """
        s = self.make(granularity="D")
        first = self.candle(True, T0)
        second = self.candle(False, T0 + datetime.timedelta(days=1))
        for candle in (first, second):
            candle.granularity = "D"
            s.execute_event(candle)
        self.assertEqual(self.sink.events[0].gtdTime.date(),
                         (second.time + datetime.timedelta(days=1)).date())

    def test_both_legs_carry_the_same_expiry(self):
        s = self.make()
        self.reversal(s)
        buy, sell = self.sink.events
        self.assertEqual(buy.gtdTime, sell.gtdTime)


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


class AG01MODCase(AGCase):
    """
    AG01 with the leg that would trade into a level dropped.

    The fixture is eleven bars that go nowhere but for one spike, so exactly
    one swing is confirmed and the test is about the filter rather than about
    which levels the finder found. All of them rise, so no colour change fires
    while the level is being built: the first one is the pair at the end, and
    it is the pair the filter is asked about.
    """

    strategy = AG01MOD

    def bar(self, i, o, h, l, c):
        ev = CandleEvent(candle_dict(T0 + datetime.timedelta(minutes=i),
                                     o=o, h=h, l=l, c=c))
        ev.instrument, ev.granularity = "DE30_EUR", "M1"
        return ev

    def build(self, s, dip=None, spike=None):
        """Eleven rising bars, one of them reaching further than the rest."""
        for i in range(11):
            low, high = 11694.0, 11706.0
            if dip is not None and i == 5:
                low = dip
            if spike is not None and i == 5:
                high = spike
            s.execute_event(self.bar(i, o=11698.0, h=high, l=low, c=11702.0))

    def pair(self, s, low, high, close, first=11):
        """A rise then a fall: AG01's colour change, with these extremes."""
        s.execute_event(self.bar(first, o=11698.0, h=high, l=low, c=11702.0))
        s.execute_event(self.bar(first + 1, o=11702.0, h=high, l=low,
                                 c=close))

    def levels(self, s):
        return [(l['kind'], l['price'])
                for l in s.swings["DE30_EUR"].levels()]


class TestAG01MODLevels(AG01MODCase):
    """What it takes to be a level here."""

    def test_a_swing_is_not_confirmed_until_the_bars_after_it_have_printed(self):
        """
        Five bars either side. Before the fifth one after it, the strategy
        does not know the dip was a low - and must not, or it would be reading
        a bar it had not seen.
        """
        s = self.make()
        for i in range(10):
            low = 11650.0 if i == 5 else 11694.0
            s.execute_event(self.bar(i, o=11698.0, h=11706.0, l=low, c=11702.0))
        self.assertEqual(self.levels(s), [])
        s.execute_event(self.bar(10, o=11698.0, h=11706.0, l=11694.0, c=11702.0))
        self.assertEqual(self.levels(s), [('support', 11650.0)])

    def test_a_flat_run_has_no_swings_at_all(self):
        """The test of an extreme is strict: equal bars are not levels."""
        s = self.make()
        self.build(s)
        self.assertEqual(self.levels(s), [])

    def test_the_band_is_a_share_of_the_window(self):
        s = self.make()
        self.build(s, dip=11650.0)
        # the window runs 11650 to 11706, and one per cent of that is 0.56
        self.assertAlmostEqual(s.swings["DE30_EUR"].levels()[0]['near'], 0.56)

    def test_a_level_is_forgotten_once_its_bars_have_gone(self):
        s = self.make(keepBars=20)
        self.build(s, dip=11650.0)
        self.assertEqual(len(self.levels(s)), 1)
        for i in range(30):
            s.execute_event(self.bar(20 + i, o=11698.0, h=11706.0,
                                     l=11694.0, c=11702.0))
        self.assertEqual(self.levels(s), [])


class TestAG01MODFilter(AG01MODCase):
    """Which leg survives."""

    def sides(self):
        return sorted(e.units for e in self.sink.of('SIGNAL'))

    def test_without_a_level_both_legs_go_out_as_in_ag01(self):
        s = self.make()
        self.build(s)
        self.pair(s, low=11650.4, high=11706.0, close=11695.0)
        self.assertEqual(self.sides(), [-1, 1])

    def test_a_sell_into_a_support_is_dropped_and_the_buy_is_not(self):
        """
        The pair's low is 0.4 from a support 0.56 wide and both bars closed
        above it: the sell stop would be a short into the level.
        """
        s = self.make()
        self.build(s, dip=11650.0)
        self.pair(s, low=11650.4, high=11706.0, close=11700.0)
        self.assertEqual(self.sides(), [1])

    def test_a_pair_that_closed_through_the_support_still_sells(self):
        """Below it, the support is broken and this is not a short into one."""
        s = self.make()
        self.build(s, dip=11650.0)
        self.pair(s, low=11650.4, high=11706.0, close=11649.0)
        self.assertEqual(self.sides(), [-1, 1])

    def test_a_buy_under_a_resistance_is_dropped_and_the_sell_is_not(self):
        s = self.make()
        self.build(s, spike=11750.0)
        self.pair(s, low=11694.0, high=11749.6, close=11700.0)
        self.assertEqual(self.sides(), [-1])

    def test_a_low_outside_the_band_is_not_at_the_level(self):
        s = self.make()
        self.build(s, dip=11650.0)
        self.pair(s, low=11651.0, high=11706.0, close=11700.0)
        self.assertEqual(self.sides(), [-1, 1])

    def test_the_signal_says_which_strategy_it_came_from(self):
        """
        AG01-MOD and not AG01MOD: the key is what a live run and its replay
        are joined on, and it is the name the menu offers.
        """
        s = self.make()
        self.build(s)
        self.pair(s, low=11694.0, high=11706.0, close=11695.0)
        signal = self.sink.of('SIGNAL')[0]
        self.assertTrue(signal.signalNumber.startswith('AG01-MOD:DE30_EUR:M1:'))
        self.assertEqual(signal.clientExtension['tag'], 'AG01-MOD')

    def test_the_prices_are_still_ag01s(self):
        """Only the filter is new: a leg that goes out is AG01's leg."""
        mod, plain = self.make(), AG01(pairs=["DE30_EUR"], granularity="M1")
        plain_sink = Recorder()
        plain.set_queue(plain_sink)
        for s in (mod, plain):
            self.build(s)
            self.pair(s, low=11694.0, high=11706.0, close=11695.0)
        for one, two in zip(self.sink.of('SIGNAL'), plain_sink.of('SIGNAL')):
            self.assertEqual((one.units, one.price, one.stopLoss,
                              one.takeProfit),
                             (two.units, two.price, two.stopLoss,
                              two.takeProfit))
