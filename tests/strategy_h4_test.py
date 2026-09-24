"""
Tests for the five 4H strategies of strategy/5-STRATEGIE.md.

Each one gets the smallest series of candles that makes its setup, and the
same series with one condition removed. The periods are tiny here - an EMA of
three rather than two hundred - because they are parameters and a fixture that
needed two hundred bars to test a rule about two would be testing the fixture.

The shared part - market order, one leg, the bracket, the volatility filter -
is tested once, on the simplest of the five.
"""

import datetime
import unittest

from parity_deriva.event.event import CandleEvent
from parity_deriva.strategy.H401 import H401
from parity_deriva.strategy.H402 import H402
from parity_deriva.strategy.H403 import H403
from parity_deriva.strategy.H404 import H404
from parity_deriva.strategy.H405 import H405
from parity_deriva.tests.helpers import T0, Recorder, candle_dict

STEP = datetime.timedelta(hours=4)


class H4Case(unittest.TestCase):

    strategy = None
    defaults = {}

    def make(self, **kw):
        kw.setdefault('pairs', ['EUR_USD'])
        kw.setdefault('granularity', 'H4')
        for key, value in self.defaults.items():
            kw.setdefault(key, value)
        s = self.strategy(**kw)
        self.sink = Recorder()
        s.set_queue(self.sink)
        return s

    def bar(self, s, i, o, h, l, c, volume=10, when=None):
        ev = CandleEvent(candle_dict(when or (T0 + i * STEP),
                                     o=o, h=h, l=l, c=c, volume=volume))
        ev.instrument, ev.granularity = 'EUR_USD', 'H4'
        s.execute_event(ev)
        return ev

    def flat(self, s, count, price, start=0, step=0.0):
        """Bars that go nowhere, to warm the averages up."""
        for i in range(count):
            level = price + i * step
            self.bar(s, start + i, o=level, h=level + 0.5, l=level - 0.5,
                     c=level)
        return start + count

    def signals(self):
        return self.sink.of('SIGNAL')


class TestH401(H4Case):
    """Trend following: a pullback to the fast average inside the trend."""

    strategy = H401
    defaults = {'fast': 3, 'slow': 5, 'atrPeriod': 3}

    def setup_long(self, close=124.0, touches=True, bullish=True):
        s = self.make()
        # a rise, so price is above both averages and the trend is up
        at = self.flat(s, 12, 100.0, step=2.0)
        fast = s.state['EUR_USD'].ema(3)
        self.assertIsNotNone(fast)
        self.assertLess(fast, close - 1, 'the fixture has to leave room')
        # the average moves with this bar, so "above it" is measured against
        # where it lands and not against where it was: on a rising series the
        # new value is a weighted mean of the close and the old one, so a low
        # just under the close is above it whatever it turns out to be
        low = fast - 0.5 if touches else close - 0.5
        opened = close - 1 if bullish else close + 1
        self.bar(s, at, o=opened, h=close + 1, l=low, c=close)
        return s

    def test_a_pullback_to_the_fast_average_in_an_uptrend_buys(self):
        s = self.setup_long()
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].units, 1)

    def test_the_stop_is_two_atr_under_the_close_and_the_target_two_r(self):
        close = 124.0
        s = self.setup_long(close=close)
        signal = self.signals()[0]
        atr = s.state['EUR_USD'].atr()
        self.assertAlmostEqual(signal.stopLoss, round(close - 2 * atr, 1),
                               places=1)
        risk = close - signal.stopLoss
        self.assertAlmostEqual(signal.takeProfit, round(close + 2 * risk, 1),
                               places=1)

    def test_a_bar_that_never_reached_the_average_is_not_a_pullback(self):
        s = self.setup_long(touches=False)
        self.assertLess(s.state['EUR_USD'].ema(3), 123.5,
                        'the fixture has to leave the low above the average')
        self.assertEqual(self.signals(), [])

    def test_a_pullback_that_closed_red_is_not_an_entry(self):
        self.setup_long(bullish=False)
        self.assertEqual(self.signals(), [])

    def test_the_mirror_sells(self):
        s = self.make()
        at = self.flat(s, 12, 130.0, step=-2.0)
        fast = s.state['EUR_USD'].ema(3)
        close = 100.0
        self.bar(s, at, o=close + 1, h=fast + 0.5, l=close - 1, c=close)
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].units, -1)
        self.assertGreater(signals[0].stopLoss, close)
        self.assertLess(signals[0].takeProfit, close)


class TestTheSharedOrder(H4Case):
    """What every one of the five sends, tested on the first of them."""

    strategy = H401
    defaults = {'fast': 3, 'slow': 5, 'atrPeriod': 3}

    def fire(self, **kw):
        s = self.make(**kw)
        at = self.flat(s, 12, 100.0, step=2.0)
        fast = s.state['EUR_USD'].ema(3)
        self.bar(s, at, o=123.0, h=125.0, l=fast - 0.5, c=124.0)
        return s

    def test_it_is_a_market_order_and_one_leg(self):
        """
        The document says "apri al prezzo di chiusura". A resting order at
        that price would be a different instruction: it would wait for price
        to come back to it.
        """
        self.fire()
        signal = self.signals()[0]
        self.assertEqual(signal.orderType, 'MARKET')
        self.assertEqual(signal.signalType, 'SINGLE')

    def test_the_price_carried_is_the_close_on_the_side_it_opens_against(self):
        self.fire()
        signal = self.signals()[0]
        # candle_dict puts the ask half a spread above the mid
        self.assertGreater(signal.price, 124.0)

    def test_it_expires_if_it_has_not_filled_by_the_next_bar(self):
        """A market order that missed its close is not this trade any more."""
        self.fire()
        signal = self.signals()[0]
        self.assertEqual(signal.gtdTime, signal.time + 2 * STEP)

    def test_the_volatility_floor_refuses_a_quiet_market(self):
        """The fixture's ATR is about two, and a pip here is one."""
        self.fire(atrMinPips=10, pipSize=1.0)
        self.assertEqual(self.signals(), [])

    def test_the_ceiling_refuses_a_wild_one(self):
        self.fire(atrMaxPips=0.1, pipSize=1.0)
        self.assertEqual(self.signals(), [])

    def test_and_with_neither_it_does_not_ask(self):
        self.fire()
        self.assertEqual(len(self.signals()), 1)


class TestH402(H4Case):
    """Breakout: a narrow window, then a close outside it."""

    strategy = H402
    defaults = {'windowBars': 4, 'volumeBars': 4, 'atrPeriod': 3}

    def consolidation(self, s, count=6):
        """Bars in a tight range, which is what makes a pattern."""
        at = 0
        for i in range(count):
            self.bar(s, i, o=100.0, h=100.4, l=99.6, c=100.0)
            at = i + 1
        return at

    def test_a_close_above_a_narrow_range_buys(self):
        s = self.make()
        at = self.consolidation(s)
        self.bar(s, at, o=100.0, h=101.0, l=99.9, c=100.9)
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].units, 1)

    def test_the_stop_is_the_pattern_s_own_support(self):
        s = self.make()
        at = self.consolidation(s)
        self.bar(s, at, o=100.0, h=101.0, l=99.9, c=100.9)
        signal = self.signals()[0]
        self.assertAlmostEqual(signal.stopLoss, 99.6, places=4)
        # and the target is twice that risk from the close
        self.assertAlmostEqual(signal.takeProfit,
                               round(100.9 + 2 * (100.9 - 99.6), 1), places=1)

    def test_the_breaking_bar_is_not_part_of_the_range_it_breaks(self):
        """
        Otherwise no close could ever be above the window's high, because it
        would be in it.
        """
        s = self.make()
        at = self.consolidation(s)
        self.bar(s, at, o=100.0, h=100.95, l=99.9, c=100.9)
        self.assertEqual(len(self.signals()), 1)

    def test_a_range_as_wide_as_the_market_is_not_a_consolidation(self):
        """
        A steady trend: every bar is a small move, so the ATR stays small
        while the window's range keeps growing past it.
        """
        s = self.make(windowBars=10)
        for i in range(12):
            level = 100.0 + i
            self.bar(s, i, o=level, h=level + 0.05, l=level - 0.05, c=level)
        self.bar(s, 12, o=112.0, h=114.0, l=111.9, c=113.5)
        self.assertEqual(self.signals(), [])

    def test_the_rsi_confirmation_reads_the_document_s_two_levels(self):
        """
        Over 60 confirms a breakout up and under 40 one down, so a rising
        market confirms the long and refuses the short of the same bar.
        """
        s = self.make(useRsi=True)
        for i in range(8):
            level = 100.0 + i
            self.bar(s, i, o=level, h=level + 0.1, l=level - 0.1, c=level)
        state = s.state['EUR_USD']
        candle = CandleEvent(candle_dict(T0, o=108.0, h=108.1, l=107.9,
                                         c=108.0))
        self.assertGreater(state.rsi(), 60)
        self.assertTrue(s.confirmed(state, candle, 1))
        self.assertFalse(s.confirmed(state, candle, -1))

    def test_a_breakout_with_the_confirmation_off_does_not_ask(self):
        s = self.make()
        at = self.consolidation(s)
        self.bar(s, at, o=100.0, h=101.0, l=99.9, c=100.9, volume=1)
        self.assertEqual(len(self.signals()), 1)

    def test_the_volume_confirmation_wants_more_than_the_average(self):
        s = self.make(useVolume=True)
        at = self.consolidation(s)
        self.bar(s, at, o=100.0, h=101.0, l=99.9, c=100.9, volume=1)
        self.assertEqual(self.signals(), [])

    def test_and_lets_a_loud_bar_through(self):
        s = self.make(useVolume=True)
        at = self.consolidation(s)
        self.bar(s, at, o=100.0, h=101.0, l=99.9, c=100.9, volume=1000)
        self.assertEqual(len(self.signals()), 1)


class TestH403(H4Case):
    """Mean reversion: out of the band and back inside on the close."""

    strategy = H403
    defaults = {'period': 5, 'atrPeriod': 3}

    def band(self, s):
        return s.state['EUR_USD'].bands(5, 2.0)

    def test_a_pierce_that_closed_back_inside_buys(self):
        s = self.make()
        for i in range(8):
            level = 100.0 + (0.5 if i % 2 else -0.5)
            self.bar(s, i, o=level, h=level + 0.2, l=level - 0.2, c=level)
        lower, _middle, _upper = self.band(s)
        self.bar(s, 8, o=100.0, h=100.2, l=lower - 0.5, c=100.0)
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].units, 1)
        # the target is the middle band as it stands on the signal bar - the
        # bar itself moves it, and the rule reads it after
        after = self.band(s)
        self.assertAlmostEqual(signals[0].takeProfit, round(after[1], 1),
                               places=1)
        self.assertLess(signals[0].stopLoss, after[0])

    def test_a_close_left_outside_the_band_is_not_a_return(self):
        s = self.make()
        for i in range(8):
            level = 100.0 + (0.5 if i % 2 else -0.5)
            self.bar(s, i, o=level, h=level + 0.2, l=level - 0.2, c=level)
        lower, _middle, _upper = self.band(s)
        self.bar(s, 8, o=100.0, h=100.2, l=lower - 0.5, c=lower - 0.4)
        self.assertEqual(self.signals(), [])

    def test_the_upper_band_sells(self):
        s = self.make()
        for i in range(8):
            level = 100.0 + (0.5 if i % 2 else -0.5)
            self.bar(s, i, o=level, h=level + 0.2, l=level - 0.2, c=level)
        _lower, middle, upper = self.band(s)
        self.bar(s, 8, o=100.0, h=upper + 0.5, l=99.8, c=100.0)
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].units, -1)
        self.assertGreater(signals[0].stopLoss, upper)


class TestH404(H4Case):
    """Daily levels, confirmed by a 4H reversal."""

    strategy = H404
    defaults = {'swingDays': 1, 'dailySma': 3, 'atrPeriod': 3,
                'toleranceAtr': 3.0}

    def day(self, s, day, prices):
        """One day of 4H bars, six of them, from a list of (o,h,l,c)."""
        when = T0.replace(hour=0) + datetime.timedelta(days=day)
        for i, (o, h, l, c) in enumerate(prices):
            self.bar(s, 0, o=o, h=h, l=l, c=c,
                     when=when + i * STEP)

    def flatDay(self, s, day, level):
        self.day(s, day, [(level, level + 0.3, level - 0.3, level)] * 6)

    def test_a_pin_bar_on_a_daily_support_in_an_uptrend_buys(self):
        s = self.make()
        # a dip on day 1 makes a daily support once day 2 has printed, and
        # the rise afterwards puts the daily trend up
        self.flatDay(s, 0, 100.0)
        self.flatDay(s, 1, 98.0)
        self.flatDay(s, 2, 101.0)
        self.flatDay(s, 3, 102.0)
        self.flatDay(s, 4, 103.0)
        levels = s.state['EUR_USD'].swings.levels()
        self.assertTrue(any(l['kind'] == 'support' for l in levels))
        support = [l for l in levels if l['kind'] == 'support'][0]['price']
        # a pin bar whose close comes back to the level
        when = T0.replace(hour=0) + datetime.timedelta(days=5)
        self.bar(s, 0, o=support + 0.2, h=support + 0.4,
                 l=support - 2.0, c=support + 0.3, when=when)
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].units, 1)
        self.assertLess(signals[0].stopLoss, support - 2.0)

    def test_a_bar_that_is_no_pattern_is_no_entry(self):
        s = self.make()
        self.flatDay(s, 0, 100.0)
        self.flatDay(s, 1, 98.0)
        self.flatDay(s, 2, 101.0)
        self.flatDay(s, 3, 102.0)
        self.flatDay(s, 4, 103.0)
        levels = s.state['EUR_USD'].swings.levels()
        support = [l for l in levels if l['kind'] == 'support'][0]['price']
        when = T0.replace(hour=0) + datetime.timedelta(days=5)
        # all body and no tail: neither a pin bar nor an engulfing
        self.bar(s, 0, o=support - 0.3, h=support + 0.35, l=support - 0.35,
                 c=support + 0.3, when=when)
        self.assertEqual(self.signals(), [])

    def test_the_daily_bias_has_to_agree(self):
        """A support in a falling market is not a long."""
        s = self.make()
        self.flatDay(s, 0, 103.0)
        self.flatDay(s, 1, 102.0)
        self.flatDay(s, 2, 101.0)
        self.flatDay(s, 3, 100.0)
        self.flatDay(s, 4, 99.0)
        when = T0.replace(hour=0) + datetime.timedelta(days=5)
        self.bar(s, 0, o=99.2, h=99.4, l=97.0, c=99.3, when=when)
        self.assertEqual([e.units for e in self.signals()], [])


class TestH405(H4Case):
    """Momentum: RSI, and a close through the last confirmed swing."""

    strategy = H405
    defaults = {'swingBars': 1, 'smaPeriod': 3, 'atrPeriod': 3}

    def test_a_break_of_the_last_swing_high_with_momentum_buys(self):
        s = self.make()
        # a low, a high, and a rise that takes the RSI over 60
        for i, (o, h, l, c) in enumerate([
                (100.0, 100.2, 99.8, 100.0),
                (100.0, 100.2, 98.0, 100.0),      # the swing low
                (100.0, 100.2, 99.8, 100.0),
                (100.0, 102.0, 99.9, 100.5),      # the swing high
                (100.5, 100.8, 100.3, 100.6),
                (100.6, 101.0, 100.5, 100.9)]):
            self.bar(s, i, o=o, h=h, l=l, c=c)
        swings = s.state['EUR_USD'].swings
        self.assertIsNotNone(swings.last('resistance'))
        self.bar(s, 6, o=101.0, h=103.0, l=100.9, c=102.5)
        signals = self.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].units, 1)
        self.assertAlmostEqual(signals[0].stopLoss, 98.0, places=4)

    def test_without_a_swing_low_there_is_no_stop_and_so_no_trade(self):
        """A stop is not invented: the document makes it mandatory."""
        s = self.make()
        for i, (o, h, l, c) in enumerate([
                (100.0, 100.2, 99.8, 100.0),
                (100.0, 102.0, 99.9, 100.5),
                (100.5, 100.8, 100.3, 100.6),
                (100.6, 101.0, 100.5, 100.9)]):
            self.bar(s, i, o=o, h=h, l=l, c=c)
        self.bar(s, 4, o=101.0, h=103.0, l=100.9, c=102.5)
        self.assertEqual(self.signals(), [])

    def test_a_break_without_momentum_is_not_an_entry(self):
        s = self.make()
        for i, (o, h, l, c) in enumerate([
                (100.0, 100.2, 98.0, 100.0),
                (100.0, 102.0, 99.9, 100.0),
                (100.0, 100.2, 99.8, 99.0),
                (99.0, 99.2, 98.8, 98.5),
                (98.5, 98.8, 98.2, 98.0)]):
            self.bar(s, i, o=o, h=h, l=l, c=c)
        # a break of the old high, but the RSI is on the floor after that fall
        self.bar(s, 5, o=98.0, h=103.0, l=97.9, c=102.5)
        self.assertEqual(self.signals(), [])


if __name__ == '__main__':
    unittest.main()
