"""
Tests for parity_deriva.backtest.oanda.OANDABacktester.

This is the local broker simulator. In scripts/t01.py and t02.py it is
registered on the Engine alongside the real OANDAExecutionHandler, so the
same ORDER events are executed twice - once for real, once in simulation.
Anything this class gets wrong shows up as a false divergence, or hides a
real one, so its fidelity to a live account is what these tests are about.

Was: six of those tests described fidelity gaps that the simulator had, and
     asserted the wrong behaviour on purpose so it could not drift further.
Now: the gaps are fixed and the assertions describe a real account. Each such
     test keeps a Was/Now note, because that history is why some of them look
     oddly specific about touching an extreme or about which bar a child order
     becomes live on.
"""

import datetime
import logging
import unittest

from parity_deriva.backtest.oanda import OANDABacktester
from parity_deriva.event.event import (CandleEvent, OrderEvent, OrderCancelEvent,
                                 SignalEvent, StatusEvent)
from parity_deriva.tests.helpers import T0, candle_dict, Recorder, TempDirCase


class BacktesterCase(TempDirCase):

    def setUp(self):
        super(BacktesterCase, self).setUp()
        self.bt = OANDABacktester(setup=self.settings)
        self.sink = Recorder()
        self.bt.set_queue(self.sink)

    def order(self, units=1, price=11700.0, sl=11690.0, tp=11720.0):
        return OrderEvent({"instrument": "DE30_EUR", "units": units,
                           "orderType": "STOP", "price": price,
                           "stopLoss": sl, "takeProfit": tp,
                           "signalNumber": "S1", "gtdTime": None})

    def candle(self, dt=T0, o=11700.0, h=11706.0, l=11694.0, c=11703.0):
        ev = CandleEvent(candle_dict(dt, o=o, h=h, l=l, c=c))
        ev.instrument = "DE30_EUR"
        ev.granularity = "M1"
        return ev


class TestInstanceState(unittest.TestCase):
    """
    The book is per instance, so one simulator per instrument is safe.
    """

    def test_each_instance_keeps_its_own_book(self):
        a = OANDABacktester()
        b = OANDABacktester()
        self.assertIsNot(a.orders, b.orders)
        self.assertIsNot(a.trades, b.trades)
        self.assertIsNot(a.closed_orders, b.closed_orders)

    def test_an_order_placed_on_one_simulator_is_invisible_to_the_other(self):
        a, b = OANDABacktester(), OANDABacktester()
        a.execute_event(OrderEvent({"instrument": "DE30_EUR", "units": 1,
                                    "orderType": "STOP", "price": 1.0,
                                    "stopLoss": None, "takeProfit": None}))
        self.assertEqual(len(a.orders), 1)
        self.assertEqual(b.orders, [])

    def test_the_order_counter_is_per_instance(self):
        a, b = OANDABacktester(), OANDABacktester()
        self.assertEqual((a.lastOrderID, b.lastOrderID), (0, 0))

    def test_currency_and_balance_defaults(self):
        bt = OANDABacktester()
        self.assertEqual(bt.currency, 'EUR')
        self.assertEqual(bt.balance, 100000.0)


class TestEventRouting(BacktesterCase):

    def test_order_creates_a_pending_order(self):
        self.bt.execute_event(self.order())
        self.assertEqual(len(self.bt.orders), 1)
        self.assertEqual(self.bt.orders[0].state, 'PENDING')

    def test_candle_is_checked_against_the_book(self):
        self.bt.execute_event(self.order(price=11700.0))
        self.bt.execute_event(self.candle())
        self.assertEqual(self.bt.orders[0].state, 'FILLED')

    def test_unrelated_events_are_ignored(self):
        for ev in (SignalEvent({"units": 1}), StatusEvent('DONE')):
            self.assertIsNone(self.bt.execute_event(ev))
        self.assertEqual(self.bt.orders, [])

    def test_the_simulator_reports_what_it_did(self):
        """
        Was: it recorded into its own book and published nothing, so nothing
             downstream could learn that a simulated trade had opened.
        Now: it publishes SIMULATEDORDER and SIMULATEDFILL - its own types,
             not the broker's, so that in the parallel deployment a component
             listening for the real thing does not act on them.

        The events themselves are covered in tests/offline_test.py.
        """
        self.bt.execute_event(self.order())
        self.bt.execute_event(self.candle())
        kinds = set(self.sink.kinds())
        self.assertTrue(kinds)
        self.assertLessEqual(kinds, {'SIMULATEDORDER', 'SIMULATEDFILL'})


class TestOrderCreation(BacktesterCase):

    def test_ids_are_assigned_sequentially_from_one(self):
        self.bt.execute_event(self.order(price=1.0, sl=None, tp=None))
        self.bt.execute_event(self.order(price=2.0, sl=None, tp=None))
        self.assertEqual([o.id for o in self.bt.orders], [1, 2])

    def test_the_order_carries_the_event_fields(self):
        self.bt.execute_event(self.order(units=-2, price=11583.3))
        o = self.bt.orders[0]
        self.assertEqual(o.instrument, "DE30_EUR")
        self.assertEqual(o.units, -2)
        self.assertEqual(o.price, 11583.3)
        self.assertEqual(o.signalNumber, "S1")


class TestOnlyTheRightCandlesDriveFills(BacktesterCase):
    """
    Was: checkOrder matched every resting order against every candle on the
         bus, whatever instrument or granularity it belonged to. Masked only
         because the runners drive one instrument at a time - two on the same
         bus and EUR_USD orders would fill on DAX candles.
    Now: an order is matched only by its own instrument, which is intrinsic
         and needs no configuration, and the simulator can be pinned to one
         granularity when the strategy watches a coarser one.
    """

    def eur_candle(self, dt=T0, low=1.2, high=1.3):
        ev = CandleEvent({"time": dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z",
                          "volume": 1, "complete": True,
                          "ask": {"o": "1.25", "h": str(high), "l": str(low), "c": "1.25"},
                          "bid": {"o": "1.25", "h": str(high), "l": str(low), "c": "1.25"},
                          "mid": {"o": "1.25", "h": str(high), "l": str(low), "c": "1.25"}})
        ev.instrument, ev.granularity = "EUR_USD", "M1"
        return ev

    def test_a_candle_for_another_instrument_does_not_fill_the_order(self):
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        self.bt.execute_event(self.eur_candle())
        self.assertEqual(self.bt.orders[0].state, 'PENDING')

    def test_each_instrument_is_filled_by_its_own_candles(self):
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        self.bt.execute_event(OrderEvent(
            {"instrument": "EUR_USD", "units": 1, "orderType": "STOP",
             "price": 1.25, "stopLoss": None, "takeProfit": None}))
        self.bt.execute_event(self.eur_candle())
        states = {o.instrument: o.state for o in self.bt.orders}
        self.assertEqual(states, {"DE30_EUR": 'PENDING', "EUR_USD": 'FILLED'})
        self.bt.execute_event(self.candle())
        states = {o.instrument: o.state for o in self.bt.orders}
        self.assertEqual(states["DE30_EUR"], 'FILLED')

    def test_by_default_any_granularity_drives_the_fills(self):
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        candle = self.candle()
        candle.granularity = "H1"
        self.bt.execute_event(candle)
        self.assertEqual(self.bt.orders[0].state, 'FILLED')

    def test_a_pinned_simulator_ignores_the_other_stream(self):
        """
        The strategy watches H1 while the simulator shadows it on M1, so that
        the ambiguity inside a bar - stop and target both touched, order
        unknown - shrinks by a factor of sixty.
        """
        bt = OANDABacktester(setup=self.settings, granularity="M1")
        bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        coarse = self.candle()
        coarse.granularity = "H1"
        bt.execute_event(coarse)
        self.assertEqual(bt.orders[0].state, 'PENDING')
        fine = self.candle()
        fine.granularity = "M1"
        bt.execute_event(fine)
        self.assertEqual(bt.orders[0].state, 'FILLED')

    def test_the_default_granularity_is_unset(self):
        self.assertIsNone(OANDABacktester(setup=self.settings).granularity)


class TestFillRules(BacktesterCase):

    def test_a_buy_fills_inside_the_ask_range(self):
        self.bt.execute_event(self.order(units=1, price=11700.0, sl=None, tp=None))
        self.bt.execute_event(self.candle(l=11694.0, h=11706.0))
        self.assertEqual(self.bt.orders[0].state, 'FILLED')

    def test_a_buy_uses_ask_not_mid(self):
        """ask is mid + 0.2 here, so a price just above the mid high still fills."""
        self.bt.execute_event(self.order(units=1, price=11706.1, sl=None, tp=None))
        self.bt.execute_event(self.candle(h=11706.0))
        self.assertEqual(self.bt.orders[0].state, 'FILLED')

    def test_a_sell_fills_inside_the_bid_range(self):
        self.bt.execute_event(self.order(units=-1, price=11700.0, sl=None, tp=None))
        self.bt.execute_event(self.candle())
        self.assertEqual(self.bt.orders[0].state, 'FILLED')

    def test_a_sell_uses_bid_not_mid(self):
        """bid is mid - 0.2, so a price just under the mid low still fills."""
        self.bt.execute_event(self.order(units=-1, price=11693.9, sl=None, tp=None))
        self.bt.execute_event(self.candle(l=11694.0))
        self.assertEqual(self.bt.orders[0].state, 'FILLED')

    def test_price_outside_the_range_does_not_fill(self):
        self.bt.execute_event(self.order(units=1, price=11800.0, sl=None, tp=None))
        self.bt.execute_event(self.candle())
        self.assertEqual(self.bt.orders[0].state, 'PENDING')

    def test_an_order_resting_on_the_high_fills_on_touch(self):
        """
        The bounds are inclusive, so a buy sitting exactly on the bar's ask
        high is filled - which is what a real broker does, and what AG01
        depends on, since it places its straddle on the previous extreme.
        """
        self.bt.execute_event(self.order(units=1, price=11706.2, sl=None, tp=None))
        self.bt.execute_event(self.candle(h=11706.0))  # ask high == 11706.2
        self.assertEqual(self.bt.orders[0].state, 'FILLED')

    def test_an_order_resting_on_the_low_fills_on_touch(self):
        self.bt.execute_event(self.order(units=-1, price=11693.8, sl=None, tp=None))
        self.bt.execute_event(self.candle(l=11694.0))  # bid low == 11693.8
        self.assertEqual(self.bt.orders[0].state, 'FILLED')

    def test_a_price_just_beyond_the_extreme_still_does_not_fill(self):
        self.bt.execute_event(self.order(units=1, price=11706.3, sl=None, tp=None))
        self.bt.execute_event(self.candle(h=11706.0))
        self.assertEqual(self.bt.orders[0].state, 'PENDING')

    def test_a_filled_order_is_not_refilled_by_later_candles(self):
        self.bt.execute_event(self.order(units=1, price=11700.0, sl=None, tp=None))
        self.bt.execute_event(self.candle())
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1)))
        self.assertEqual([o.state for o in self.bt.orders], ['FILLED'])


class TestExclusiveLegs(BacktesterCase):
    """
    A straddle is one trade or none, never both.

    AG01 brackets a reversal with a buy above the pair and a sell below it and
    says signalType EXCLUSIVE. On a bar wide enough to reach both, this used
    to open both - each one's stop being the other's entry - and the money
    manager, which never saw that group finish, refused every signal after it.
    On EUR_USD daily the run's last trade was in September 2016 with ten years
    of data behind it.
    """

    def straddle(self, buy=11720.0, sell=11680.0, key="S1"):
        for units, price in ((1, buy), (-1, sell)):
            event = self.order(units=units, price=price, sl=None, tp=None)
            event.signalNumber = key
            self.bt.execute_event(event)

    def wide(self, dt=T0):
        """A bar that reaches both ends of the straddle above."""
        return self.candle(dt=dt, o=11700.0, h=11730.0, l=11670.0, c=11700.0)

    def test_only_one_end_of_a_straddle_fills(self):
        self.straddle()
        self.bt.execute_event(self.wide())
        filled = [o for o in self.bt.closed_orders + self.bt.orders
                  if o.state == 'FILLED']
        self.assertEqual(len(filled), 1)

    def test_the_other_end_is_off_the_book(self):
        self.straddle()
        self.bt.execute_event(self.wide())
        self.assertEqual([o.state for o in self.bt.orders
                          if o.state == 'PENDING'], [])

    def test_the_end_nearer_the_open_is_the_one_that_filled(self):
        """
        Which one a bar reached first is not in the bar - the same blindness
        backtest/resolution.py measures - and coming out of the open the
        nearer level is the one price met first unless it doubled back.
        """
        self.straddle(buy=11705.0, sell=11650.0)
        self.bt.execute_event(self.wide())
        filled = [o for o in self.bt.closed_orders + self.bt.orders
                  if o.state == 'FILLED']
        self.assertEqual(len(filled), 1)
        self.assertAlmostEqual(filled[0].price, 11705.0)

    def test_two_signals_are_not_each_other_s_siblings(self):
        """Only the legs of one signal cancel each other."""
        self.straddle(buy=11720.0, sell=11680.0, key="S1")
        self.straddle(buy=11725.0, sell=11675.0, key="S2")
        self.bt.execute_event(self.wide())
        filled = [o for o in self.bt.closed_orders + self.bt.orders
                  if o.state == 'FILLED']
        self.assertEqual(len(filled), 2)
        self.assertEqual(sorted(getattr(o, 'signalNumber') for o in filled),
                         ['S1', 'S2'])


class TestCancelNeverTakesAStop(BacktesterCase):
    """
    A cancel matched on price must not find a live trade's stop.

    In AG01 the long's stop sits at exactly the short leg's entry, by
    construction: the pair straddles two candles and each leg's stop is the
    other leg's price. So when one leg fills and the money manager cancels the
    other by price - the only field both sides agree on - a match that does
    not exclude children can take the stop of the trade that just opened. The
    position then runs with nothing under it until it reaches its target,
    which on EUR_USD daily was months, and reads as a strategy with a
    remarkable win rate.
    """

    def bracket(self):
        """AG01's shape: a long whose stop is where the short would enter."""
        buy = self.order(units=1, price=11720.0, sl=11680.0, tp=11760.0)
        buy.signalNumber = "S1"
        self.bt.execute_event(buy)
        sell = self.order(units=-1, price=11680.0, sl=11720.0, tp=11640.0)
        sell.signalNumber = "S1"
        self.bt.execute_event(sell)

    def test_cancelling_the_losing_leg_leaves_the_stop_standing(self):
        self.bracket()
        # a bar that reaches the buy and not the sell
        self.bt.execute_event(self.candle(o=11700.0, h=11730.0, l=11695.0,
                                          c=11725.0))
        self.bt.execute_event(OrderCancelEvent({'orderID': 99,
                                                'price': 11680.0,
                                                'instrument': 'DE30_EUR'}))
        stops = [o for o in self.bt.orders
                 if getattr(o, 'orig', None) is not None and o.price == 11680.0]
        self.assertEqual([o.state for o in stops], ['PENDING'])

    def test_and_the_trade_still_exits_on_it(self):
        self.bracket()
        self.bt.execute_event(self.candle(o=11700.0, h=11730.0, l=11695.0,
                                          c=11725.0))
        self.bt.execute_event(OrderCancelEvent({'orderID': 99,
                                                'price': 11680.0,
                                                'instrument': 'DE30_EUR'}))
        self.bt.execute_event(self.candle(dt=T0 + datetime.timedelta(minutes=1),
                                          o=11700.0, h=11705.0, l=11670.0,
                                          c=11675.0))
        closed = [e for e in self.sink.of('SIMULATEDFILL')
                  if e.has_attr('tradesClosed')]
        self.assertEqual([e.reason for e in closed], ['STOP_LOSS_ORDER'])

    def test_a_leg_that_is_still_resting_is_what_a_cancel_finds(self):
        """The ordinary case: the cancel does its job."""
        self.bracket()
        self.bt.execute_event(OrderCancelEvent({'orderID': 99,
                                                'price': 11680.0,
                                                'instrument': 'DE30_EUR'}))
        self.assertEqual([o.price for o in self.bt.orders], [11720.0])


class TestExpiry(BacktesterCase):
    """
    An order dies when the expiry it was issued with passes.

    Nothing offline read gtdTime at all. AG01 issues a bracket meant to last
    the day; here it rested for as long as the data did, and on EUR_USD daily
    one from 3 July 2022 filled on 15 November - four months and a different
    market. Taking one position at a time, it held the strategy shut for all
    of it, which is why a single zombie order cost more than one trade.
    """

    def order(self, units=1, price=11700.0, sl=11690.0, tp=11720.0, gtd=None):
        event = BacktesterCase.order(self, units=units, price=price, sl=sl,
                                     tp=tp)
        event.gtdTime = gtd
        return event

    def test_an_order_past_its_expiry_is_cancelled(self):
        self.bt.execute_event(self.order(price=11800.0, sl=None, tp=None,
                                         gtd=T0))
        self.bt.execute_event(self.candle(dt=T0 + datetime.timedelta(minutes=1)))
        self.assertEqual(self.bt.orders, [])
        self.assertEqual(self.bt.closed_orders[0].state, 'CANCELED')

    def test_it_says_so_where_the_money_manager_can_hear(self):
        """
        A group the money manager still believes is outstanding stops every
        later signal, so an order that dies quietly costs more than itself.

        Was: a plain ORDERCANCEL straight off the simulator. Live, the real
             execution handler and the money manager - which matches on price
             when it does not know the id - both heard it and acted on the
             real order.
        Now: the simulator's own SIMULATEDORDERCANCEL, which offline
             SimulatedBroker promotes to ORDERCANCEL for the money manager.
        """
        self.bt.execute_event(self.order(price=11800.0, sl=None, tp=None,
                                         gtd=T0))
        self.bt.execute_event(self.candle(dt=T0 + datetime.timedelta(minutes=1)))
        self.assertEqual(self.sink.of('ORDERCANCEL'), [])
        cancels = self.sink.of('SIMULATEDORDERCANCEL')
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0].reason, 'GTD_EXPIRY')

    def test_a_bar_at_the_expiry_itself_still_trades(self):
        """Good *till* that instant: the bar stamped on it is not past it."""
        self.bt.execute_event(self.order(sl=None, tp=None, gtd=T0))
        self.bt.execute_event(self.candle(dt=T0))
        self.assertEqual(self.bt.orders[0].state, 'FILLED')

    def test_an_expired_order_does_not_fill_on_the_bar_that_killed_it(self):
        """It was off the book before that bar traded, so it cannot be both."""
        self.bt.execute_event(self.order(sl=None, tp=None, gtd=T0))
        self.bt.execute_event(self.candle(dt=T0 + datetime.timedelta(minutes=1)))
        self.assertEqual(self.sink.of('SIMULATEDFILL'), [])
        self.assertEqual(self.bt.closed_orders[0].state, 'CANCELED')

    def test_an_order_with_no_expiry_rests_as_long_as_the_data(self):
        self.bt.execute_event(self.order(price=11800.0, sl=None, tp=None,
                                         gtd=None))
        for i in range(5):
            self.bt.execute_event(
                self.candle(dt=T0 + datetime.timedelta(minutes=i)))
        self.assertEqual(self.bt.orders[0].state, 'PENDING')

    def test_the_stop_and_target_of_an_open_trade_do_not_expire(self):
        """
        They carry their parent's fields, expiry included. Cancelling them at
        midnight would leave a position standing with neither a stop nor a
        target, which is not something any account does to you.
        """
        self.bt.execute_event(self.order(gtd=T0))
        self.bt.execute_event(self.candle(dt=T0))          # the entry fills
        self.bt.execute_event(self.candle(dt=T0 + datetime.timedelta(minutes=1),
                                          o=11700.0, h=11701.0, l=11699.0,
                                          c=11700.0))
        children = [o for o in self.bt.orders if getattr(o, 'orig', None)]
        self.assertEqual(len(children), 2)
        self.assertEqual(sorted(o.state for o in children),
                         ['PENDING', 'PENDING'])


class TestGaps(BacktesterCase):
    """
    An order the market jumped over is filled at the open, not left resting.

    The case that found this: EUR_USD daily, a long entered 18 April 2017
    with its target at 1.07851. Friday 21 April closed at 1.07268 and Monday
    23 April opened at 1.09197 - clean over it. Reading only [low, high], the
    target was never touched again until February 2020, so the run carried
    one position for three years and, taking one trade at a time, refused
    every signal in between.
    """

    def gap(self, first, second, units=1, price=11750.0):
        """A bar, then a bar that opens somewhere else entirely."""
        self.bt.execute_event(self.order(units=units, price=price,
                                         sl=None, tp=None))
        self.bt.execute_event(self.candle(**first))
        self.bt.execute_event(
            self.candle(dt=T0 + datetime.timedelta(minutes=1), **second))
        return self.bt.orders[0]

    def test_a_buy_the_market_gapped_over_fills_at_the_open(self):
        order = self.gap({'o': 11700.0, 'h': 11706.0, 'l': 11694.0,
                          'c': 11703.0},
                         {'o': 11800.0, 'h': 11810.0, 'l': 11795.0,
                          'c': 11805.0})
        self.assertEqual(order.state, 'FILLED')
        # the ask open, because a buy trades against the ask
        self.assertAlmostEqual(order.price, 11800.2)

    def test_the_fill_it_reports_is_the_price_it_got(self):
        """
        Not the level it asked for. handleSLTP books P&L off these prices, so
        a fill recorded at the level is money the account never saw.
        """
        self.gap({'o': 11700.0, 'h': 11706.0, 'l': 11694.0, 'c': 11703.0},
                 {'o': 11800.0, 'h': 11810.0, 'l': 11795.0, 'c': 11805.0})
        fill = self.sink.of('SIMULATEDFILL')[-1]
        self.assertAlmostEqual(float(fill.price), 11800.2)

    def test_a_sell_the_market_gapped_under_fills_at_the_bid_open(self):
        order = self.gap({'o': 11700.0, 'h': 11706.0, 'l': 11694.0,
                          'c': 11697.0},
                         {'o': 11600.0, 'h': 11605.0, 'l': 11590.0,
                          'c': 11595.0},
                         units=-1, price=11650.0)
        self.assertEqual(order.state, 'FILLED')
        self.assertAlmostEqual(order.price, 11599.8)

    def test_a_bar_that_gapped_over_and_came_back_still_fills_at_the_open(self):
        """
        The level is inside this bar's range, so the range alone would fill it
        at the level - but the market had already jumped it before the bar
        opened, and the open is the first price there was.
        """
        order = self.gap({'o': 11700.0, 'h': 11706.0, 'l': 11694.0,
                          'c': 11703.0},
                         {'o': 11800.0, 'h': 11810.0, 'l': 11740.0,
                          'c': 11760.0})
        self.assertEqual(order.state, 'FILLED')
        self.assertAlmostEqual(order.price, 11800.2)

    def test_a_gap_that_stops_short_of_the_level_does_not_fill(self):
        """The whole rule is that the market crossed it, not that it moved."""
        order = self.gap({'o': 11700.0, 'h': 11706.0, 'l': 11694.0,
                          'c': 11703.0},
                         {'o': 11740.0, 'h': 11745.0, 'l': 11735.0,
                          'c': 11742.0})
        self.assertEqual(order.state, 'PENDING')

    def test_the_first_candle_of_a_run_has_nothing_to_have_gapped_from(self):
        self.bt.execute_event(self.order(units=1, price=11750.0,
                                         sl=None, tp=None))
        self.bt.execute_event(self.candle(o=11800.0, h=11810.0, l=11795.0,
                                          c=11805.0))
        self.assertEqual(self.bt.orders[0].state, 'PENDING')

    def test_a_target_gapped_over_closes_the_trade_at_the_open(self):
        """The case above, end to end: the trade closes, and for more."""
        self.bt.execute_event(self.order(units=1, price=11700.0,
                                         sl=11690.0, tp=11720.0))
        self.bt.execute_event(self.candle(o=11700.0, h=11706.0, l=11694.0,
                                          c=11703.0))
        self.bt.execute_event(self.candle(dt=T0 + datetime.timedelta(minutes=1),
                                          o=11800.0, h=11810.0, l=11795.0,
                                          c=11805.0))
        closed = [e for e in self.sink.of('SIMULATEDFILL')
                  if e.has_attr('tradesClosed')]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].reason, 'TAKE_PROFIT_ORDER')
        # the bid open, and the trade made 99.8 rather than the 20 its target
        # asked for
        self.assertAlmostEqual(float(closed[0].price), 11799.8)
        self.assertAlmostEqual(float(closed[0].pl), 99.8)

    def test_the_other_leg_of_the_bracket_is_still_retired(self):
        """
        A gap fill is still one-cancels-the-other. Leaving the stop resting
        would close the same trade a second time when price came back.
        """
        self.bt.execute_event(self.order(units=1, price=11700.0,
                                         sl=11690.0, tp=11720.0))
        self.bt.execute_event(self.candle(o=11700.0, h=11706.0, l=11694.0,
                                          c=11703.0))
        self.bt.execute_event(self.candle(dt=T0 + datetime.timedelta(minutes=1),
                                          o=11800.0, h=11810.0, l=11795.0,
                                          c=11805.0))
        self.assertEqual([o.state for o in self.bt.orders
                          if o.state == 'PENDING'], [])


class TestStopLossTakeProfitChildren(BacktesterCase):

    def fill_a_long(self, sl=11690.0, tp=11720.0):
        self.bt.execute_event(self.order(units=1, price=11700.0, sl=sl, tp=tp))
        self.bt.execute_event(self.candle(l=11699.0, h=11701.0))
        return self.bt.orders

    def test_a_fill_creates_a_stop_loss_and_a_take_profit_order(self):
        orders = self.fill_a_long()
        self.assertEqual(len(orders), 3)
        self.assertEqual(orders[0].state, 'FILLED')
        self.assertEqual([o.type for o in orders[1:]],
                         ['STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER'])

    def test_the_children_sit_at_the_requested_levels(self):
        orders = self.fill_a_long()
        self.assertEqual(orders[1].price, 11690.0)
        self.assertEqual(orders[2].price, 11720.0)

    def test_the_children_point_back_at_the_parent(self):
        orders = self.fill_a_long()
        self.assertIs(orders[1].orig, orders[0])
        self.assertIs(orders[2].orig, orders[0])

    def test_the_parent_type_is_cleared_and_batched(self):
        orders = self.fill_a_long()
        self.assertIsNone(orders[0].type)
        self.assertEqual(orders[0].batchID, orders[0].id)

    def test_no_children_when_no_levels_were_requested(self):
        self.bt.execute_event(self.order(units=1, price=11700.0, sl=None, tp=None))
        self.bt.execute_event(self.candle())
        self.assertEqual(len(self.bt.orders), 1)

    def test_a_missing_stoploss_attribute_raises(self):
        """
        OANDAOrder has no stopLoss class default, so an ORDER event that never
        carried one blows up when the fill tries to build the children.
        """
        self.bt.execute_event(OrderEvent({"instrument": "DE30_EUR", "units": 1,
                                          "orderType": "STOP", "price": 11700.0}))
        with self.assertRaises(AttributeError):
            self.bt.execute_event(self.candle())

    def test_long_stop_loss_is_flipped_to_a_sell(self):
        orders = self.fill_a_long()
        self.assertEqual(orders[0].units, 1)
        self.assertEqual(orders[1].units, -1)   # stop loss sells
        self.assertEqual(orders[2].units, -1)   # take profit sells

    def test_short_stop_loss_is_flipped_to_a_buy(self):
        """Both legs of a short close by buying back."""
        self.bt.execute_event(self.order(units=-1, price=11700.0,
                                         sl=11720.0, tp=11680.0))
        self.bt.execute_event(self.candle(l=11699.0, h=11701.0))
        parent, stop, take = self.bt.orders
        self.assertEqual(parent.units, -1)
        self.assertEqual(stop.units, 1)
        self.assertEqual(take.units, 1)

    def test_a_short_stop_actually_closes_the_trade(self):
        self.bt.execute_event(self.order(units=-1, price=11700.0,
                                         sl=11720.0, tp=11680.0))
        self.bt.execute_event(self.candle(l=11699.0, h=11701.0))
        parent, stop, take = self.bt.orders
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11719.0, h=11721.0))
        self.assertEqual(stop.state, 'CLOSED')
        self.assertEqual(parent.state, 'CLOSED')
        self.assertEqual(take.state, 'CANCELED')

    def test_children_are_not_matched_against_the_opening_bar(self):
        """
        checkOrder walks a snapshot of the book, so the stop and target created
        by a fill are only live from the next bar on. A wide bar opens the
        trade and leaves it open, the way a live account would.
        """
        self.bt.execute_event(self.order(units=1, price=11700.0,
                                         sl=11695.0, tp=11705.0))
        self.bt.execute_event(self.candle(l=11690.0, h=11710.0))
        parent, stop, take = self.bt.orders
        self.assertEqual(parent.state, 'FILLED')
        self.assertEqual(stop.state, 'PENDING')
        self.assertEqual(take.state, 'PENDING')

    def test_the_children_are_live_from_the_next_bar(self):
        self.bt.execute_event(self.order(units=1, price=11700.0,
                                         sl=11695.0, tp=11705.0))
        self.bt.execute_event(self.candle(l=11690.0, h=11710.0))
        parent, stop, take = self.bt.orders
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11690.0, h=11710.0))
        self.assertEqual(parent.state, 'CLOSED')
        self.assertEqual(stop.state, 'CLOSED')

    def test_hitting_the_take_profit_closes_both_legs(self):
        parent, stop, take = self.fill_a_long()
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11719.0, h=11721.0))
        self.assertEqual(take.state, 'CLOSED')
        self.assertEqual(parent.state, 'CLOSED')

    def test_hitting_the_take_profit_cancels_the_stop(self):
        """
        Was: the parent's SLOrder attribute was overwritten with the string
             'CANCELED', and this test pinned that. The attribute held the
             Event the child was built from, not the OANDAOrder the book
             holds, so the order itself stayed PENDING and nothing about the
             book changed.
        Now: the stop is cancelled - the order the book holds - which is what
             one-cancels-the-other means.
        """
        parent, stop, take = self.fill_a_long()
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11719.0, h=11721.0))
        self.assertEqual(stop.state, 'CANCELED')
        self.assertIs(parent.SLOrder, stop)

    def test_hitting_the_stop_loss_closes_and_cancels_the_target(self):
        parent, stop, take = self.fill_a_long()
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11689.0, h=11691.0))
        self.assertEqual(stop.state, 'CLOSED')
        self.assertEqual(take.state, 'CANCELED')

    def test_the_trade_is_not_closed_a_second_time(self):
        """
        Was: the loser of a bracket stayed PENDING for the rest of the run, so
             when price later reached it, it filled and published a second
             close for a trade that had already closed - with the opposite
             outcome, and the balance moved again. Over two months of EUR_USD
             H1, 73 of 103 trades closed twice.
        Now: one close, and the balance moves once.
        """
        self.fill_a_long()
        self.sink.events = []
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11719.0, h=11721.0))
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=2),
                                          l=11689.0, h=11691.0))
        closes = [e for e in self.sink.of('SIMULATEDFILL')
                  if e.has_attr('tradesClosed')]
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0].reason, 'TAKE_PROFIT_ORDER')
        self.assertAlmostEqual(self.bt.balance, 100020.0)

    def test_the_cancelled_leg_leaves_the_book_and_the_closed_ones_stay(self):
        """
        Was: closing removed nothing, because nothing was cancelled.
        Now: the loser is retired the way cancelOrder retires an order - off
             self.orders and onto closed_orders - while the parent and the leg
             that closed the trade stay on the book with state CLOSED. Any
             reconciliation still has to filter on state rather than on list
             membership.
        """
        self.fill_a_long()
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11719.0, h=11721.0))
        self.assertEqual([o.state for o in self.bt.orders],
                         ['CLOSED', 'CLOSED'])
        self.assertEqual([o.state for o in self.bt.closed_orders], ['CANCELED'])
        self.assertEqual(self.bt.closed_trades, [])

    def test_a_winning_trade_is_realised_into_the_balance(self):
        self.fill_a_long()
        self.assertEqual(self.bt.balance, 100000.0)
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11719.0, h=11721.0))
        # entry 11700, target 11720, one unit
        self.assertAlmostEqual(self.bt.balance, 100020.0)

    def test_a_losing_trade_reduces_the_balance(self):
        self.fill_a_long()
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11689.0, h=11691.0))
        # entry 11700, stop 11690, one unit
        self.assertAlmostEqual(self.bt.balance, 99990.0)


class TestCancel(BacktesterCase):

    def test_a_pending_order_at_that_price_is_cancelled_and_removed(self):
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        self.bt.execute_event(OrderCancelEvent({"orderID": 1, "price": 11700.0,
                                                "instrument": "DE30_EUR"}))
        self.assertEqual(self.bt.orders, [])
        self.assertEqual(len(self.bt.closed_orders), 1)
        self.assertEqual(self.bt.closed_orders[0].state, 'CANCELED')

    def test_cancel_matches_on_price_and_ignores_the_order_id(self):
        """
        Two orders resting at the same price on the same instrument still
        cannot be told apart: the first in the book is cancelled whatever
        orderID the event names.

        Was: price was the only field compared, so a cancel could hit an order
             on a different instrument.
        Now: instrument and price are both compared. The broker's orderID
             remains unusable - the simulator numbers its own orders - so this
             residual ambiguity is by construction, not an oversight.
        """
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        self.bt.execute_event(OrderCancelEvent({"orderID": 2, "price": 11700.0}))
        self.assertEqual([o.id for o in self.bt.orders], [2])

    def test_cancel_does_not_touch_another_instrument(self):
        """The event carries the instrument, so two pairs resting at the same
        price are told apart even though the broker orderID cannot be used."""
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        other = OrderEvent({"instrument": "EUR_USD", "units": 1,
                            "orderType": "STOP", "price": 11700.0,
                            "stopLoss": None, "takeProfit": None})
        self.bt.execute_event(other)
        self.bt.execute_event(OrderCancelEvent({"orderID": 1, "price": 11700.0,
                                                "instrument": "EUR_USD"}))
        self.assertEqual([o.instrument for o in self.bt.orders], ["DE30_EUR"])

    def test_cancelling_an_unknown_price_is_a_no_op(self):
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        self.bt.execute_event(OrderCancelEvent({"orderID": 1, "price": 999.0}))
        self.assertEqual(len(self.bt.orders), 1)

    def test_cancelling_a_filled_order_leaves_it_in_place(self):
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        self.bt.execute_event(self.candle())
        self.bt.execute_event(OrderCancelEvent({"orderID": 1, "price": 11700.0}))
        self.assertEqual([o.state for o in self.bt.orders], ['FILLED'])


class TestStopModify(BacktesterCase):
    """
    Moving the stop of a trade that is already open.

    The fill logic is untouched by this: only the child's price changes, so a
    moved stop is taken exactly as a stop placed there in the first place.
    """

    def setUp(self):
        super(TestStopModify, self).setUp()
        from parity_deriva.event.event import StopModifyEvent
        self.StopModifyEvent = StopModifyEvent
        self.bt.execute_event(self.order(price=11700.0, sl=11690.0, tp=None))
        self.bt.execute_event(self.candle())          # fills, creating the stop

    def stop(self):
        return self.bt.orders[0].SLOrder.price

    def test_the_stop_of_the_named_order_moves(self):
        self.assertTrue(self.bt.execute_event(self.StopModifyEvent(
            {"orderID": 1, "price": 11695.0})))
        self.assertEqual(self.stop(), 11695.0)

    def test_an_unknown_order_moves_nothing(self):
        self.assertFalse(self.bt.execute_event(self.StopModifyEvent(
            {"orderID": 99, "price": 11695.0})))
        self.assertEqual(self.stop(), 11690.0)

    def test_the_signal_names_the_trade_when_the_id_is_the_brokers(self):
        """
        Shadowing a live account, the id on the event is OANDA's and this book
        has never heard of it - the simulator numbers its own orders. The
        signal is on both sides' orders, so it is the name that crosses.
        """
        self.assertTrue(self.bt.execute_event(self.StopModifyEvent(
            {"orderID": "7291", "price": 11695.0, "signalNumber": "S1"})))
        self.assertEqual(self.stop(), 11695.0)

    def test_an_unknown_signal_moves_nothing(self):
        self.assertFalse(self.bt.execute_event(self.StopModifyEvent(
            {"orderID": "7291", "price": 11695.0, "signalNumber": "OTHER"})))
        self.assertEqual(self.stop(), 11690.0)

    def test_a_moved_stop_is_taken_like_any_other(self):
        self.bt.execute_event(self.StopModifyEvent(
            {"orderID": 1, "price": 11702.0}))
        self.bt.execute_event(self.candle(dt=T0 + datetime.timedelta(minutes=1),
                                          o=11705.0, h=11706.0, l=11701.0,
                                          c=11703.0))
        closed = [e for e in self.sink.events if e.has_attr('tradesClosed')]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].reason, 'STOP_LOSS_ORDER')

    def test_a_stop_above_a_long_entry_is_booked_as_a_gain(self):
        """
        The whole point of moving it. A stop taken at 11702 off an entry of
        11700 is two points made, not two lost - which is what the sign of
        (exit - entry) * units says and what abs() used to get backwards.
        """
        self.bt.execute_event(self.StopModifyEvent(
            {"orderID": 1, "price": 11702.0}))
        self.bt.execute_event(self.candle(dt=T0 + datetime.timedelta(minutes=1),
                                          o=11705.0, h=11706.0, l=11701.0,
                                          c=11703.0))
        closed = [e for e in self.sink.events if e.has_attr('tradesClosed')][0]
        self.assertEqual(closed.pl, 2.0)

    def test_a_stop_moved_past_the_market_is_taken_at_the_next_open(self):
        """
        Was: the trailer moved a long's stop above where the last bar had
             closed, and no later bar came back up to it, so the trade never
             closed - EUR_USD FTWP M15 held one from 2015 to the end.
        Now: a sell stop at or above the open is taken at the open.
        """
        bt = OANDABacktester(setup=self.settings)
        bt.set_queue(self.sink)
        bt.execute_event(self.order(price=11700.0, sl=11690.0, tp=None))
        bt.execute_event(self.candle(o=11700.0, h=11706.0, l=11699.0, c=11701.0))
        bt.execute_event(self.StopModifyEvent({"orderID": 1, "price": 11702.0}))
        bt.execute_event(self.candle(dt=T0 + datetime.timedelta(minutes=1),
                                     o=11700.0, h=11701.0, l=11695.0, c=11700.0))
        closed = [e for e in self.sink.events if e.has_attr('tradesClosed')]
        self.assertEqual([(e.reason, e.price) for e in closed],
                         [('STOP_LOSS_ORDER', 11699.8)])  # the bid's open

    def test_a_price_less_event_is_refused(self):
        self.assertFalse(self.bt.execute_event(self.StopModifyEvent(
            {"orderID": 1})))

    def test_a_level_of_zero_is_not_a_level(self):
        """Event.__set__ renders an unreadable price as "0.0"; it is refused."""
        self.assertFalse(self.bt.execute_event(self.StopModifyEvent(
            {"orderID": 1, "price": None})))
        self.assertEqual(self.stop(), 11690.0)

    def test_a_trade_without_a_stop_has_nothing_to_move(self):
        bt = OANDABacktester(setup=self.settings)
        bt.set_queue(Recorder())
        bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        bt.execute_event(self.candle())
        self.assertFalse(bt.execute_event(self.StopModifyEvent(
            {"orderID": 1, "price": 11695.0})))


class TestDumps(BacktesterCase):

    def test_dump_orders_and_trades_do_not_raise_on_an_empty_book(self):
        self.assertIsNone(self.bt.dumpOrders())
        self.assertIsNone(self.bt.dumpTrades())

    def test_dump_orders_walks_a_populated_book(self):
        self.bt.execute_event(self.order(sl=None, tp=None))
        self.assertIsNone(self.bt.dumpOrders())


if __name__ == "__main__":
    unittest.main()
