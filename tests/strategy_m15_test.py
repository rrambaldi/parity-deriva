"""
Tests for the six M15 strategies of strategy/6-STRATEGIE-M15.md.

The shared part - stop entries, the attempts, the daily stop, the early exit
and the time stop, all of which hang on the fills - is tested once on a stub
whose signal is whatever the test says. Each strategy then gets the smallest
series that makes its setup, with periods cut down the way the H4 tests cut
theirs, and the same series with one condition removed.
"""

import datetime
import unittest

from parity_deriva.event.event import CandleEvent, TransactionEvent
from parity_deriva.lib.streaming import Hourly, Series
from parity_deriva.strategy.M15 import EARLY_EXIT, TIME_STOP, M15, Reading
from parity_deriva.strategy.M1501 import M1501
from parity_deriva.strategy.M1502 import M1502
from parity_deriva.strategy.M1503 import M1503
from parity_deriva.strategy.M1504 import M1504
from parity_deriva.strategy.M1505 import M1505
from parity_deriva.strategy.M1506 import M1506
from parity_deriva.tests.helpers import T0, Recorder, candle_dict

STEP = datetime.timedelta(minutes=15)


class Stub(M15):
    """Signals whatever `plan` holds, and exits when `leave` says so."""

    plan = None
    leave = None

    def series(self):
        return Reading(Series(atr=self.atrPeriod, keep=3))

    def signal(self, state, candle):
        return dict(self.plan) if self.plan else None

    def exit(self, state, candle, held):
        return self.leave


class M15Case(unittest.TestCase):

    strategy = None
    defaults = {}

    def make(self, **kw):
        kw.setdefault('pairs', ['EUR_USD'])
        kw.setdefault('granularity', 'M15')
        for key, value in self.defaults.items():
            kw.setdefault(key, value)
        s = self.strategy(**kw)
        self.sink = Recorder()
        s.set_queue(self.sink)
        self.at = 0
        return s

    def bar(self, s, o, h, l, c, when=None):
        ev = CandleEvent(candle_dict(when or (T0 + self.at * STEP),
                                     o=o, h=h, l=l, c=c))
        ev.instrument, ev.granularity = 'EUR_USD', 'M15'
        self.at += 1
        s.execute_event(ev)
        return ev

    def flat(self, s, count, price, step=0.0, half=0.5):
        for _ in range(count):
            self.bar(s, o=price, h=price + half, l=price - half, c=price)
            price += step

    def hour(self, s, low, high, mid=None):
        """Four M15 bars that make one H1 bar from `low` to `high`."""
        mid = (low + high) / 2.0 if mid is None else mid
        for _ in range(4):
            self.bar(s, o=mid, h=high, l=low, c=mid)

    def signals(self):
        return self.sink.of('SIGNAL')

    def closes(self):
        return self.sink.of('CLOSETRADE')

    def fill(self, s, signal, price=None, closing=False):
        data = {'type': 'ORDER_FILL', 'signalNumber': signal.signalNumber,
                'price': signal.price if price is None else price,
                'units': signal.units * (-1 if closing else 1)}
        if closing:
            data['tradesClosed'] = [{'tradeID': '1'}]
        s.execute_event(TransactionEvent(data))


class TestTheSharedPart(M15Case):

    strategy = Stub
    defaults = {'atrPeriod': 3}

    def ready(self, **kw):
        s = self.make(**kw)
        self.flat(s, 4, 100.0)
        return s

    def ask(self, s, **plan):
        s.plan = dict({'side': 1, 'stop': 98.0, 'target': 104.0}, **plan)
        self.bar(s, o=100.0, h=100.5, l=99.5, c=100.0)
        s.plan = None
        return self.signals()[-1] if self.signals() else None

    def test_a_market_entry_is_a_market_order_on_the_close(self):
        s = self.ready()
        signal = self.ask(s)
        self.assertEqual(signal.orderType, 'MARKET')
        self.assertAlmostEqual(signal.price, 100.2)     # the ask's close

    def test_a_stop_entry_rests_at_the_ask_s_high_for_one_bar(self):
        s = self.ready()
        s.STOP_ENTRY = True
        signal = self.ask(s)
        self.assertEqual(signal.orderType, 'STOP')
        self.assertAlmostEqual(signal.price, 100.7)
        self.assertEqual(signal.gtdTime,
                         signal.time + 2 * STEP - datetime.timedelta(seconds=1))

    def test_a_target_under_the_minimum_reward_is_not_taken(self):
        s = self.ready(minReward=1.5)
        self.assertIsNone(self.ask(s, stop=99.0, target=101.0))
        self.assertIsNotNone(self.ask(s, stop=99.0, target=102.5))

    def test_a_stop_on_the_wrong_side_is_not_taken(self):
        s = self.ready()
        self.assertIsNone(self.ask(s, stop=101.0))

    def test_a_bar_already_too_wide_is_not_taken(self):
        s = self.ready(maxBarAtr=0.5)
        self.assertIsNone(self.ask(s))

    def test_the_attempts_on_one_move_are_counted_on_the_fills(self):
        s = self.ready(maxAttempts=1)
        first = self.ask(s, key='A')
        # sent but never filled: not an attempt yet
        self.assertIsNotNone(self.ask(s, key='A'))
        self.fill(s, first)
        self.fill(s, first, closing=True)
        count = len(self.signals())
        self.ask(s, key='A')
        self.assertEqual(len(self.signals()), count)
        self.assertIsNotNone(self.ask(s, key='B'))

    def test_nothing_new_while_a_trade_is_open(self):
        s = self.ready()
        self.fill(s, self.ask(s))
        count = len(self.signals())
        self.ask(s)
        self.assertEqual(len(self.signals()), count)

    def test_a_fill_of_someone_else_s_signal_is_not_this_trade(self):
        s = self.ready()
        signal = self.ask(s)
        signal.signalNumber = 'OTHER'
        self.fill(s, signal)
        self.assertEqual(s.held, {})

    def test_the_early_exit_closes_the_trade_once(self):
        s = self.ready()
        self.fill(s, self.ask(s))
        s.leave = EARLY_EXIT
        self.flat(s, 2, 100.0)
        closes = self.closes()
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0].reason, EARLY_EXIT)
        self.assertEqual(closes[0].instrument, 'EUR_USD')

    def test_the_time_stop_closes_a_trade_that_went_nowhere(self):
        s = self.ready(timeStopBars=3)
        self.fill(s, self.ask(s))
        self.flat(s, 2, 100.0)
        self.assertEqual(self.closes(), [])
        self.flat(s, 1, 100.0)
        self.assertEqual(self.closes()[0].reason, TIME_STOP)

    def test_and_leaves_one_that_went_its_way(self):
        s = self.ready(timeStopBars=3)
        self.fill(s, self.ask(s))
        # risk 2.2 from the ask's 100.2: 0.5R is 1.1, and 101.5 is past it
        self.bar(s, o=100.0, h=101.5, l=99.9, c=101.0)
        self.flat(s, 3, 100.0)
        self.assertEqual(self.closes(), [])

    def test_the_daily_stop_counts_r_and_ends_with_the_day(self):
        s = self.ready(dailyStopR=2.0)
        for _ in range(2):
            signal = self.ask(s)
            self.fill(s, signal)
            self.fill(s, signal, price=signal.stopLoss, closing=True)
        self.assertAlmostEqual(s.dayR, -2.0)
        count = len(self.signals())
        self.ask(s)
        self.assertEqual(len(self.signals()), count)
        self.bar(s, o=100.0, h=100.5, l=99.5, c=100.0,
                 when=T0 + datetime.timedelta(days=1))
        self.assertIsNotNone(self.ask(s))


    def test_an_instrument_it_does_not_follow_is_ignored(self):
        s = self.ready()
        s.plan = {'side': 1, 'stop': 98.0, 'target': 104.0}
        ev = CandleEvent(candle_dict(T0 + 10 * STEP, o=100.0, h=100.5,
                                     l=99.5, c=100.0))
        ev.instrument, ev.granularity = 'GBP_USD', 'M15'
        s.execute_event(ev)
        self.assertEqual(self.signals(), [])

    def test_bars_of_another_granularity_are_not_its_bars(self):
        s = self.ready()
        s.plan = {'side': 1, 'stop': 98.0, 'target': 104.0}
        ev = CandleEvent(candle_dict(T0 + 10 * STEP, o=100.0, h=100.5,
                                     l=99.5, c=100.0))
        ev.instrument, ev.granularity = 'EUR_USD', 'M5'
        s.execute_event(ev)
        self.assertEqual(self.signals(), [])


class TestHourly(unittest.TestCase):

    def bar(self, minute):
        ev = CandleEvent(candle_dict(T0 + datetime.timedelta(minutes=minute),
                                     o=100, h=100 + minute, l=90, c=100))
        return ev

    def test_an_hour_is_sealed_by_the_first_bar_of_the_next(self):
        hourly = Hourly()
        for minute in (0, 15, 30, 45):
            self.assertIsNone(hourly.add(self.bar(minute)))
        done = hourly.add(self.bar(60))
        self.assertEqual(done.time, T0)
        self.assertEqual(done.mid['h'], 145)

    def test_and_only_the_last_few_are_kept(self):
        hourly = Hourly()
        for minute in range(0, 600, 15):
            hourly.add(self.bar(minute))
        self.assertEqual(len(hourly.bars), 2)


class TestM1501(M15Case):
    """MTP: a pullback to the EMA 20 inside an M15 and H1 uptrend."""

    strategy = M1501
    defaults = {'fast': 3, 'slow': 5, 'atrPeriod': 3, 'structureBars': 2,
                'pullbackBars': 2, 'maxBarAtr': 0}

    def rise(self, s, bars=40):
        # every close equals the previous high, so no bar of the rise is a
        # confirmation, and every low stays above the lagging average
        # candle_dict prints one decimal, so the steps are whole units
        level = 100.0
        for _ in range(bars):
            self.bar(s, o=level - 0.4, h=level + 1.0, l=level - 0.5, c=level)
            level += 1.0
        return level - 1.0

    def test_the_rise_itself_signals_nothing(self):
        s = self.make()
        self.rise(s)
        self.assertEqual(self.signals(), [])

    def pullback(self, s, touch=True):
        top = self.rise(s)
        low = s.state['EUR_USD'].m15.ema(3) - 2.0 if touch else top - 0.3
        self.bar(s, o=top, h=top + 0.5, l=low, c=top - 0.2)
        # the confirmation; an average of three moves a long way on one bar,
        # so without a pullback its low has to stay clear of where it lands
        low = top - 0.3 if touch else top + 0.5
        self.bar(s, o=max(low, top - 0.2), h=top + 2.0, l=low, c=top + 1.5)
        return top

    def test_a_pullback_and_a_close_over_the_previous_high_buy(self):
        s = self.make()
        top = self.pullback(s)
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.units, 1)
        self.assertEqual(signal.orderType, 'MARKET')
        self.assertLess(signal.stopLoss, top - 2.0)
        risk = signal.price - signal.stopLoss
        self.assertAlmostEqual(signal.takeProfit, signal.price + 2 * risk,
                               places=4)

    def test_the_stop_is_at_least_one_atr_away(self):
        s = self.make(minStopAtr=5.0)
        self.pullback(s)
        signal = self.signals()[0]
        atr = s.state['EUR_USD'].atr()
        self.assertGreaterEqual(signal.price - signal.stopLoss, 5 * atr - 1e-3)

    def test_no_pullback_no_trade(self):
        s = self.make()
        self.pullback(s, touch=False)
        self.assertEqual(self.signals(), [])

    def test_a_close_under_the_ema_50_ends_the_trade(self):
        s = self.make()
        top = self.pullback(s)
        self.fill(s, self.signals()[0])
        slow = s.state['EUR_USD'].m15.ema(5)
        self.bar(s, o=top, h=top + 0.5, l=slow - 2.0, c=slow - 1.0)
        self.assertEqual(self.closes()[0].reason, EARLY_EXIT)


class TestM1502(M15Case):
    """SBR: the session's first bars make a range, a close outside it trades."""

    strategy = M1502
    defaults = {'sessionHour': T0.hour, 'rangeBars': 2, 'atrPeriod': 3}

    def session(self, s, high=102.0, low=98.0):
        # quiet bars before the session, then the range itself
        start = T0 - 8 * STEP
        for i in range(8):
            self.bar(s, o=100.0, h=100.5, l=99.5, c=100.0, when=start + i * STEP)
        self.at = 0
        self.bar(s, o=100.0, h=high, l=low, c=100.0)
        self.bar(s, o=100.0, h=high, l=low, c=100.0)

    def test_a_close_above_the_range_buys_with_the_range_s_height_as_target(self):
        s = self.make()
        self.session(s)
        self.bar(s, o=101.6, h=102.9, l=101.5, c=102.8)
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].units, 1)
        self.assertAlmostEqual(signals[0].takeProfit, 106.0, places=4)
        # back inside the range, by at most half an ATR
        self.assertLess(signals[0].stopLoss, 102.0)
        self.assertGreater(signals[0].stopLoss, 98.0)

    def test_and_below_it_sells(self):
        s = self.make()
        self.session(s)
        self.bar(s, o=98.4, h=98.5, l=97.1, c=97.2)
        self.assertEqual(self.signals()[0].units, -1)

    def test_a_range_too_wide_for_the_atr_is_not_traded(self):
        s = self.make(maxRangeAtr=1.0)
        self.session(s)
        self.bar(s, o=101.6, h=102.9, l=101.5, c=102.8)
        self.assertEqual(self.signals(), [])

    def test_a_spike_with_no_body_is_not_a_breakout(self):
        s = self.make()
        self.session(s)
        self.bar(s, o=102.1, h=103.5, l=101.9, c=102.3)
        self.assertEqual(self.signals(), [])

    def test_a_close_inside_the_range_is_nothing(self):
        s = self.make()
        self.session(s)
        self.bar(s, o=100.0, h=100.9, l=99.9, c=100.8)
        self.assertEqual(self.signals(), [])


class TestM1503(M15Case):
    """S/RP: a rejection off an H1 support, entered on a stop."""

    strategy = M1503
    defaults = {'swingBars': 1, 'atrPeriod': 3}

    def support(self, s):
        # an H1 swing low at 99.0, confirmed once the hour after it is sealed
        for low in (99.5, 99.0, 99.5, 99.5):
            self.hour(s, low, 100.5, mid=100.0)
        levels = s.state['EUR_USD'].levels.levels()
        self.assertIn(99.0, [l['price'] for l in levels])

    def test_a_pin_bar_on_the_support_buys_on_a_stop_at_its_high(self):
        s = self.make()
        self.support(s)
        self.bar(s, o=99.6, h=99.7, l=99.1, c=99.7)
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].units, 1)
        self.assertEqual(signals[0].orderType, 'STOP')
        self.assertAlmostEqual(signals[0].price, 99.9)     # the ask's high
        self.assertLess(signals[0].stopLoss, 99.0)

    def test_a_bar_that_closes_through_the_zone_is_not_a_rebound(self):
        s = self.make()
        self.support(s)
        self.bar(s, o=99.6, h=99.7, l=97.0, c=97.1)
        self.assertEqual(self.signals(), [])

    def test_a_plain_bar_on_the_support_is_not_a_rejection(self):
        s = self.make()
        self.support(s)
        self.bar(s, o=99.9, h=100.0, l=99.1, c=99.2)
        self.assertEqual(self.signals(), [])

    def test_a_close_under_the_zone_ends_the_trade(self):
        s = self.make()
        self.support(s)
        self.bar(s, o=99.6, h=99.7, l=99.1, c=99.7)
        self.fill(s, self.signals()[0])
        self.bar(s, o=99.6, h=99.6, l=98.2, c=98.3)
        self.assertEqual(self.closes()[0].reason, EARLY_EXIT)


class TestM1504(M15Case):
    """BMR: a false break of the lower band in a flat market."""

    strategy = M1504
    defaults = {'period': 5, 'atrPeriod': 3, 'rsiPeriod': 3, 'h1Slow': 3,
                'flatBars': 2, 'structureBars': 2, 'expansion': 100.0}

    def sideways(self, s, hours=10):
        for i in range(hours * 4):
            c = 100.5 if i % 2 else 99.5
            self.bar(s, o=100.0, h=100.6, l=99.4, c=c)

    def test_a_flat_market_is_one_to_revert_in(self):
        s = self.make()
        self.sideways(s)
        self.assertTrue(s.ranging(s.state['EUR_USD']))

    def test_a_trending_one_is_not(self):
        s = self.make()
        self.flat(s, 40, 100.0, step=0.5)
        self.assertFalse(s.ranging(s.state['EUR_USD']))

    def drop(self, s, low, close):
        """Two bars down, then one through the lower band."""
        self.sideways(s)
        self.bar(s, o=100.0, h=100.0, l=98.9, c=99.0)
        self.bar(s, o=99.0, h=99.0, l=98.1, c=98.2)
        self.bar(s, o=98.2, h=98.3, l=low, c=close)
        state = s.state['EUR_USD']
        self.assertLess(min(m['rsi'] for m in state.marks), 30)
        return state.m15.bands(s.period, s.deviations)

    def test_a_close_back_inside_the_lower_band_buys_for_the_middle(self):
        s = self.make(minReward=0)
        lower, middle, upper = self.drop(s, low=97.2, close=98.3)
        self.assertLess(97.2, lower)
        self.assertLess(lower, 98.3)
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].units, 1)
        self.assertEqual(signals[0].orderType, 'STOP')
        self.assertAlmostEqual(signals[0].takeProfit, round(middle, 1),
                               places=4)
        self.assertLess(signals[0].stopLoss, 97.2)

    def test_a_bar_still_closing_outside_is_not_bought(self):
        """
        One deviation, because with five bars and two the last close can
        never be outside: its distance from the mean is at most two of them.
        """
        s = self.make(minReward=0, deviations=1.0)
        lower, middle, upper = self.drop(s, low=95.0, close=94.9)
        self.assertLess(94.9, lower)
        self.assertEqual(self.signals(), [])

    def test_without_the_rsi_stretch_it_does_not_buy(self):
        s = self.make(minReward=0, rsiLow=5.0)
        self.drop(s, low=97.2, close=98.3)
        self.assertEqual(self.signals(), [])


class TestM1505(M15Case):
    """BBO: a squeeze of the bands, then a close out of the congestion."""

    strategy = M1505
    defaults = {'period': 5, 'atrPeriod': 3, 'lookback': 10, 'squeezeBars': 2,
                'maxBarAtr': 0}

    def squeeze(self, s):
        for i in range(15):
            c = 102.0 if i % 2 else 100.0
            self.bar(s, o=101.0, h=102.5, l=99.5, c=c)
        for _ in range(8):
            self.bar(s, o=101.0, h=101.2, l=100.8, c=101.0)
        box = s.state['EUR_USD'].box
        self.assertIsNotNone(box)
        return box

    def test_a_close_above_the_congestion_buys_with_its_middle_as_stop(self):
        s = self.make()
        box = self.squeeze(s)
        self.bar(s, o=101.0, h=102.5, l=100.9, c=102.4)
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].units, 1)
        self.assertEqual(signals[0].orderType, 'MARKET')
        self.assertAlmostEqual(signals[0].stopLoss,
                               (box['high'] + box['low']) / 2, places=4)

    def test_a_small_body_is_not_a_breakout(self):
        s = self.make(bodyAtr=10.0)
        self.squeeze(s)
        self.bar(s, o=101.0, h=102.5, l=100.9, c=102.4)
        self.assertEqual(self.signals(), [])

    def test_a_close_back_inside_ends_the_trade(self):
        s = self.make()
        self.squeeze(s)
        self.bar(s, o=101.0, h=102.5, l=100.9, c=102.4)
        self.fill(s, self.signals()[0])
        self.bar(s, o=102.0, h=102.1, l=101.1, c=101.1)
        self.assertEqual(self.closes()[0].reason, EARLY_EXIT)

    def test_two_trades_per_squeeze_and_no_third(self):
        s = self.make()
        self.squeeze(s)
        seen = 0
        for _ in range(3):
            self.bar(s, o=101.0, h=102.5, l=100.9, c=102.4)
            if len(self.signals()) > seen:
                seen = len(self.signals())
                signal = self.signals()[-1]
                self.fill(s, signal)
                self.fill(s, signal, price=signal.stopLoss, closing=True)
            # back into the congestion, ready to break it again
            self.bar(s, o=101.0, h=101.2, l=100.8, c=101.0)
        self.assertEqual(len(self.signals()), 2)


class TestM1506(M15Case):
    """BRT: a resistance broken, then retested from above."""

    strategy = M1506
    defaults = {'swingBars': 1, 'atrPeriod': 3}

    def breakout(self, s):
        # an H1 swing high at 101.0
        for high in (100.5, 101.0, 100.5, 100.5):
            self.hour(s, 99.5, high, mid=100.0)
        self.assertIn(101.0, [l['price']
                              for l in s.state['EUR_USD'].levels.levels()])
        self.bar(s, o=100.0, h=101.4, l=99.9, c=101.3)

    def test_the_breakout_bar_itself_is_not_the_entry(self):
        s = self.make()
        self.breakout(s)
        self.assertEqual(self.signals(), [])

    def test_a_retest_that_holds_buys_on_a_stop_at_its_high(self):
        s = self.make()
        self.breakout(s)
        self.bar(s, o=101.2, h=101.4, l=101.0, c=101.3)
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].orderType, 'STOP')
        self.assertAlmostEqual(signals[0].price, 101.6)
        self.assertLess(signals[0].stopLoss, 101.0)

    def test_a_decisive_close_back_under_cancels_the_breakout(self):
        s = self.make()
        self.breakout(s)
        self.bar(s, o=101.0, h=101.0, l=99.4, c=99.5)
        self.bar(s, o=101.2, h=101.4, l=101.0, c=101.3)
        self.assertEqual(self.signals(), [])

    def test_no_retest_in_time_no_trade(self):
        s = self.make(retestBars=2)
        self.breakout(s)
        self.flat(s, 3, 101.8, half=0.2)
        self.bar(s, o=101.2, h=101.4, l=101.0, c=101.3)
        self.assertEqual(self.signals(), [])


if __name__ == '__main__':
    unittest.main()
