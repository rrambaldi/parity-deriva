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
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11719.0, h=11721.0))
        parent, stop, take = self.bt.orders
        self.assertEqual(stop.state, 'CLOSED')
        self.assertEqual(parent.state, 'CLOSED')

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
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11690.0, h=11710.0))
        parent, stop, take = self.bt.orders
        self.assertEqual(parent.state, 'CLOSED')
        self.assertEqual(stop.state, 'CLOSED')

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


class TestDumps(BacktesterCase):

    def test_dump_orders_and_trades_do_not_raise_on_an_empty_book(self):
        self.assertIsNone(self.bt.dumpOrders())
        self.assertIsNone(self.bt.dumpTrades())

    def test_dump_orders_walks_a_populated_book(self):
        self.bt.execute_event(self.order(sl=None, tp=None))
        self.assertIsNone(self.bt.dumpOrders())


if __name__ == "__main__":
    unittest.main()
