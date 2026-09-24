"""
Tests for ending the trading day: portfolio/session.py and the close it asks
the simulator for.

Two halves of one rule, and they are tested as two. Which bar ends the day is
a decision about the clock and is taken in one place; closing a trade at the
market is the broker's job, offline as well as live. The point of the split is
that the simulator and an account act on the same decision instead of on two
implementations of it - the argument portfolio/trailer.py makes about the stop
ladder.
"""

import datetime
import unittest

from parity_deriva.backtest.oanda import OANDABacktester
from parity_deriva.event.event import (CandleEvent, CloseTradeEvent, OrderEvent)
from parity_deriva.portfolio.session import SESSION_CLOSE, SessionCloser
from parity_deriva.tests.helpers import T0, candle_dict, Recorder, TempDirCase

DAY = datetime.datetime(2024, 1, 2)


class CloserCase(unittest.TestCase):

    def closer(self, at='21:00', granularity='H4', instrument='EUR_USD'):
        one = SessionCloser(at=at, granularity=granularity,
                            instrument=instrument)
        self.sink = Recorder()
        one.set_queue(self.sink)
        return one

    def candle(self, hour, granularity='H4', instrument='EUR_USD'):
        ev = CandleEvent(candle_dict(DAY.replace(hour=hour)))
        ev.instrument, ev.granularity = instrument, granularity
        return ev


class TestWhichBarEndsTheDay(CloserCase):
    """
    The bar the cut falls inside, and its close is where the trade comes out:
    the last price of the day the strategy could have traded at.
    """

    def test_the_bar_the_cut_falls_inside(self):
        one = self.closer(at='21:00', granularity='H4')
        for hour in (0, 4, 8, 12, 16):
            self.assertFalse(one.ends(DAY.replace(hour=hour)), hour)
        self.assertTrue(one.ends(DAY.replace(hour=20)))

    def test_a_bar_that_opens_on_the_cut_belongs_to_the_next_day(self):
        """Half open, like every other window in this project."""
        one = self.closer(at='20:00', granularity='H4')
        self.assertTrue(one.ends(DAY.replace(hour=16)))
        self.assertFalse(one.ends(DAY.replace(hour=20)))

    def test_a_daily_bar_contains_every_cut(self):
        self.assertTrue(self.closer(at='21:00', granularity='D')
                        .ends(DAY.replace(hour=0)))


class TestTheCloseIsAsked(CloserCase):

    def test_it_says_what_and_when_and_why(self):
        one = self.closer()
        one.execute_event(self.candle(20))
        asked = self.sink.of('CLOSETRADE')
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0].instrument, 'EUR_USD')
        self.assertEqual(asked[0].time, DAY.replace(hour=20))
        self.assertEqual(asked[0].reason, SESSION_CLOSE)

    def test_a_day_ends_once(self):
        """
        Twice would close the trade the second bar opened as well, which is
        not what "do not hold overnight" asks for.

        Asked without a granularity - so every bar past the cut answers yes -
        because that is the case the guard is for; told the granularity, only
        one bar of the day contains the cut in the first place.
        """
        one = self.closer(at='21:00', granularity=None)
        for hour in (21, 22, 23):
            one.execute_event(self.candle(hour, granularity='H1'))
        self.assertEqual(len(self.sink.of('CLOSETRADE')), 1)

    def test_the_next_day_ends_too(self):
        one = self.closer()
        one.execute_event(self.candle(20))
        later = self.candle(20)
        later.time = later.time + datetime.timedelta(days=1)
        one.execute_event(later)
        self.assertEqual(len(self.sink.of('CLOSETRADE')), 2)

    def test_the_fine_stream_does_not_end_the_day(self):
        """
        The orders rest on M5 bars; the day ends once, not two hundred and
        eighty-eight times.
        """
        one = self.closer(granularity='H4')
        one.execute_event(self.candle(20, granularity='M5'))
        self.assertEqual(self.sink.of('CLOSETRADE'), [])

    def test_another_instrument_is_not_this_one_s_day(self):
        one = self.closer(instrument='EUR_USD')
        one.execute_event(self.candle(20, instrument='GBP_USD'))
        self.assertEqual(self.sink.of('CLOSETRADE'), [])


class TestTheSimulatorCloses(TempDirCase):
    """
    The other half: a trade that is open comes out at this bar, on the side
    of the spread a market close would pay.
    """

    def setUp(self):
        super(TestTheSimulatorCloses, self).setUp()
        self.bt = OANDABacktester(setup=self.settings, balance=1000.0)
        self.sink = Recorder()
        self.bt.set_queue(self.sink)

    def order(self, units=1, price=11700.0, sl=11690.0, tp=11720.0):
        return OrderEvent({"instrument": "DE30_EUR", "units": units,
                           "orderType": "STOP", "price": price,
                           "stopLoss": sl, "takeProfit": tp,
                           "signalNumber": "S1", "gtdTime": None})

    def candle(self, o=11700.0, h=11706.0, l=11694.0, c=11703.0, dt=T0):
        ev = CandleEvent(candle_dict(dt, o=o, h=h, l=l, c=c))
        ev.instrument, ev.granularity = "DE30_EUR", "M1"
        return ev

    def close(self):
        self.bt.execute_event(CloseTradeEvent({
            'instrument': 'DE30_EUR', 'time': T0, 'reason': SESSION_CLOSE}))

    def open_a_trade(self):
        self.bt.execute_event(self.order())
        # a bar that reaches the entry and neither of the brackets
        self.bt.execute_event(self.candle(o=11700.0, h=11702.0, l=11698.0,
                                          c=11701.0))

    def test_an_open_trade_comes_out_on_the_bid(self):
        """
        A long is sold on the bid, the way every other fill here is priced,
        and at the close of the bar - the account cannot trade at a price the
        bar never showed.
        """
        self.open_a_trade()
        bar = self.candle(c=11710.0)
        self.bt.execute_event(bar)
        self.close()
        fills = [ev for ev in self.sink.of('SIMULATEDFILL')
                 if getattr(ev, 'reason', None) == SESSION_CLOSE]
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].price, bar.bid['c'])
        self.assertEqual(fills[0].reason, SESSION_CLOSE)

    def test_the_account_moves_by_what_the_trade_made(self):
        self.open_a_trade()
        entry = [o for o in self.bt.orders if o.state == 'FILLED'][0].price
        bar = self.candle(c=11710.0)
        self.bt.execute_event(bar)
        self.close()
        gain = bar.bid['c'] - entry
        self.assertAlmostEqual(self.bt.balance, 1000.0 + gain, 6)

    def test_both_legs_come_off_the_book(self):
        """
        Otherwise the stop is still resting, and closes the same trade a
        second time when price reaches it later - the bug retire() exists
        for, found once already.
        """
        self.open_a_trade()
        self.close()
        self.assertEqual([o for o in self.bt.orders if o.state == 'PENDING'], [])
        # a bar right through the old stop now closes nothing
        before = len(self.sink.of('SIMULATEDFILL'))
        self.bt.execute_event(self.candle(o=11690.0, h=11691.0, l=11680.0,
                                          c=11685.0))
        self.assertEqual(len(self.sink.of('SIMULATEDFILL')), before)

    def test_nothing_open_closes_nothing(self):
        self.bt.execute_event(self.candle())
        self.close()
        self.assertEqual(self.sink.of('SIMULATEDFILL'), [])

    def test_a_resting_entry_is_left_alone(self):
        """
        A pending order is not a position. Ending the day closes what is
        held; an order that never filled expires on its own terms.
        """
        self.bt.execute_event(self.order(price=11800.0))
        self.bt.execute_event(self.candle())
        self.close()
        self.assertEqual(len(self.bt.orders), 1)
        self.assertEqual(self.bt.orders[0].state, 'PENDING')

    def test_a_close_before_any_bar_says_so_rather_than_guessing(self):
        self.bt.execute_event(self.order())
        self.close()
        self.assertEqual(self.sink.of('SIMULATEDFILL'), [])


if __name__ == '__main__':
    unittest.main()
