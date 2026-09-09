"""
Characterisation tests for qsforex.backtest.oanda.OANDABacktester.

This is the local broker simulator. In scripts/t01.py and t02.py it is
registered on the Engine alongside the real OANDAExecutionHandler, so the
same ORDER events are executed twice - once for real, once in simulation.
Anything this class gets wrong shows up as a false divergence (or hides a
real one), which is why the fidelity gaps below are pinned explicitly.
"""

import datetime
import logging
import unittest

from qsforex.backtest.oanda import OANDABacktester
from qsforex.event.event import (CandleEvent, OrderEvent, OrderCancelEvent,
                                 SignalEvent, StatusEvent)
from qsforex.tests.helpers import T0, candle_dict, Recorder, TempDirCase


class BacktesterCase(TempDirCase):

    def setUp(self):
        super(BacktesterCase, self).setUp()
        self.bt = OANDABacktester(setup=self.settings)
        # trades/orders/closed_* and the id counters are class attributes;
        # give every test its own instance-level copies.
        self.bt.orders = []
        self.bt.trades = []
        self.bt.closed_orders = []
        self.bt.closed_trades = []
        self.bt.lastOrderID = 0
        self.bt.lastTradeID = 0
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


class TestSharedClassState(unittest.TestCase):
    """
    trades / orders / closed_orders / closed_trades are declared at class
    level, so two simulators in one process write into the same lists. A
    monitoring setup that runs one simulator per instrument would silently
    merge their books.
    """

    def test_two_instances_share_the_order_book(self):
        a = OANDABacktester()
        b = OANDABacktester()
        self.assertIs(a.orders, b.orders)
        self.assertIs(a.trades, b.trades)

    def test_the_currency_attribute_is_misspelled_on_the_class(self):
        """`currenct = 'EUR'` is the class default; `currency` comes from _set."""
        self.assertEqual(OANDABacktester.currenct, 'EUR')
        self.assertFalse(hasattr(OANDABacktester, 'currency'))
        self.assertEqual(OANDABacktester().currency, 'EUR')


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

    def test_the_simulator_never_emits_events(self):
        """
        It records into its own book and logs; it publishes nothing back to
        the Engine. Comparing it with reality therefore means reading its
        state, not listening for events - that is the shape any reconciliation
        component has to take.
        """
        self.bt.execute_event(self.order())
        self.bt.execute_event(self.candle())
        self.assertEqual(self.sink.events, [])


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

    def test_touching_the_extreme_does_not_fill(self):
        """
        The test is strictly `price > low and price < high`, so an order
        resting exactly on the candle high or low is not filled. A real broker
        fills on touch, so a straddle placed exactly at the previous extreme -
        which is what AG01 does - can fill live and not in simulation.
        """
        self.bt.execute_event(self.order(units=1, price=11706.2, sl=None, tp=None))
        self.bt.execute_event(self.candle(h=11706.0))  # ask high == 11706.2
        self.assertEqual(self.bt.orders[0].state, 'PENDING')

    def test_a_filled_order_is_not_refilled_by_later_candles(self):
        self.bt.execute_event(self.order(units=1, price=11700.0, sl=None, tp=None))
        self.bt.execute_event(self.candle())
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1)))
        self.assertEqual([o.state for o in self.bt.orders], ['FILLED'])


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

    def test_short_stop_loss_keeps_the_sell_side(self):
        """
        The stop-loss branch flips the sign only `if o.units > 0`, while the
        take-profit branch flips unconditionally. So for a short position the
        stop loss stays a sell and can never close the trade: checkOrder will
        only ever match it against the bid range as another sell. Shorts are
        simulated without a working stop.
        """
        self.bt.execute_event(self.order(units=-1, price=11700.0,
                                         sl=11720.0, tp=11680.0))
        self.bt.execute_event(self.candle(l=11699.0, h=11701.0))
        parent, stop, take = self.bt.orders
        self.assertEqual(parent.units, -1)
        self.assertEqual(stop.units, -1)        # not flipped: still a sell
        self.assertEqual(take.units, 1)         # flipped correctly

    def test_children_can_fill_on_the_very_candle_that_opened_the_trade(self):
        """
        createOrder appends to self.orders while checkOrder is iterating it, so
        the freshly created stop/target are tested against the same bar. A wide
        bar therefore opens and closes a trade instantly, which a live account
        would not do at the same prices.
        """
        self.bt.execute_event(self.order(units=1, price=11700.0,
                                         sl=11695.0, tp=11705.0))
        self.bt.execute_event(self.candle(l=11690.0, h=11710.0))
        parent, stop, take = self.bt.orders
        # opened and closed inside a single bar: the parent never even rests
        # in the FILLED state that a live account would report.
        self.assertEqual(parent.state, 'CLOSED')
        self.assertEqual(stop.state, 'CLOSED')
        self.assertEqual(parent.TPOrder, 'CANCELED')

    def test_hitting_the_take_profit_closes_both_legs(self):
        self.fill_a_long()
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11719.0, h=11721.0))
        parent, stop, take = self.bt.orders
        self.assertEqual(take.state, 'CLOSED')
        self.assertEqual(parent.state, 'CLOSED')

    def test_hitting_the_take_profit_cancels_the_stop(self):
        self.fill_a_long()
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11719.0, h=11721.0))
        parent = self.bt.orders[0]
        self.assertEqual(parent.SLOrder, 'CANCELED')

    def test_hitting_the_stop_loss_closes_and_cancels_the_target(self):
        self.fill_a_long()
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11689.0, h=11691.0))
        parent, stop, take = self.bt.orders
        self.assertEqual(stop.state, 'CLOSED')
        self.assertEqual(parent.TPOrder, 'CANCELED')

    def test_closing_does_not_remove_the_orders_from_the_book(self):
        """
        Closed legs stay in self.orders with state CLOSED; closed_orders is
        only ever appended to by cancelOrder. Any reconciliation has to filter
        on state, not on list membership.
        """
        self.fill_a_long()
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11719.0, h=11721.0))
        self.assertEqual(len(self.bt.orders), 3)
        self.assertEqual(self.bt.closed_orders, [])
        self.assertEqual(self.bt.closed_trades, [])

    def test_no_profit_and_loss_is_accumulated_anywhere(self):
        """
        handleSLTP computes `gain` and logs it, but nothing adds it to
        self.balance. The simulator tracks order state, not equity.
        """
        self.fill_a_long()
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11719.0, h=11721.0))
        self.assertEqual(self.bt.balance, '100000')


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
        Two resting orders at the same price cannot be told apart: the first
        one in the book is cancelled whatever orderID the event names.
        """
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        self.bt.execute_event(OrderCancelEvent({"orderID": 2, "price": 11700.0}))
        self.assertEqual([o.id for o in self.bt.orders], [2])

    def test_cancelling_an_unknown_price_is_a_no_op(self):
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        self.bt.execute_event(OrderCancelEvent({"orderID": 1, "price": 999.0}))
        self.assertEqual(len(self.bt.orders), 1)

    def test_cancelling_a_filled_order_leaves_it_in_place(self):
        self.bt.execute_event(self.order(price=11700.0, sl=None, tp=None))
        self.bt.execute_event(self.candle())
        self.bt.execute_event(OrderCancelEvent({"orderID": 1, "price": 11700.0}))
        self.assertEqual([o.state for o in self.bt.orders], ['FILLED'])


class TestDumps(BacktesterCase):

    def test_dump_orders_and_trades_do_not_raise_on_an_empty_book(self):
        self.assertIsNone(self.bt.dumpOrders())
        self.assertIsNone(self.bt.dumpTrades())

    def test_dump_orders_walks_a_populated_book(self):
        self.bt.execute_event(self.order(sl=None, tp=None))
        self.assertIsNone(self.bt.dumpOrders())


if __name__ == "__main__":
    unittest.main()
