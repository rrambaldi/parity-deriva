"""
Tests for parity_deriva.portfolio.moneymanager.

MoneyManager is the bookkeeper: it turns signals into sized orders, keeps the
signalNumber -> [orders] index, emulates OCO by cancelling the losing leg of a
straddle, and enforces one open trade at a time. That index is the only place
where a real fill can be matched back to the signal that produced it, so it is
the natural anchor for a real-vs-simulated comparison.
"""

import logging
import unittest

from parity_deriva.event.event import (SignalEvent, OrderEvent, ClientOrderEvent,
                                 TransactionEvent, StatusEvent, CandleEvent)
from parity_deriva.portfolio.moneymanager import MoneyManager
from parity_deriva.tests.helpers import Recorder, TempDirCase


class MoneyManagerCase(TempDirCase):

    def setUp(self):
        super(MoneyManagerCase, self).setUp()
        self.mm = MoneyManager(setup=self.settings, units=100)
        # signals/processed are class attributes; isolate each test
        self.mm.signals = {}
        self.mm.processed = []
        self.mm.onTrade = False
        self.mm.orderIssued = False
        self.sink = Recorder()
        self.mm.set_queue(self.sink)

    def signal(self, units=1, price=11700.0, number="S1"):
        return SignalEvent({"instrument": "DE30_EUR", "units": units,
                            "orderType": "STOP", "price": price,
                            "stopLoss": 11690.0, "takeProfit": 11720.0,
                            "signalNumber": number, "gtdTime": None})

    def fill(self, order_id, price=11700.0, **extra):
        payload = {"type": "ORDER_FILL", "orderID": str(order_id),
                   "price": price, "instrument": "DE30_EUR"}
        payload.update(extra)
        return TransactionEvent(payload)

    def acknowledge(self, order_id, price=11700.0, number="S1"):
        """The broker's reply that gives a resting order its real id."""
        return ClientOrderEvent({"id": str(order_id), "batchID": str(order_id),
                                 "price": price, "signalNumber": number})


class TestDefaults(MoneyManagerCase):

    def test_default_size_is_one_unit(self):
        mm = MoneyManager(setup=self.settings)
        self.assertEqual(mm.units, 1)

    def test_signals_and_processed_are_shared_class_attributes(self):
        a = MoneyManager(setup=self.settings)
        b = MoneyManager(setup=self.settings)
        self.assertIs(a.signals, b.signals)


class TestSignalToOrder(MoneyManagerCase):

    def test_a_signal_becomes_an_order(self):
        self.mm.execute_event(self.signal())
        self.assertEqual(self.sink.kinds(), ['ORDER'])

    def test_units_are_scaled_by_the_configured_size(self):
        self.mm.execute_event(self.signal(units=1))
        self.assertEqual(self.sink.events[0].units, 100)

    def test_the_sign_of_the_signal_is_preserved(self):
        self.mm.execute_event(self.signal(units=-1))
        self.assertEqual(self.sink.events[0].units, -100)

    def test_the_rest_of_the_signal_is_carried_through(self):
        self.mm.execute_event(self.signal())
        order = self.sink.events[0]
        self.assertEqual(order.instrument, "DE30_EUR")
        self.assertEqual(order.price, 11700.0)
        self.assertEqual(order.stopLoss, 11690.0)
        self.assertEqual(order.takeProfit, 11720.0)
        self.assertEqual(order.orderType, "STOP")

    def test_the_order_is_indexed_under_its_signal_number(self):
        self.mm.execute_event(self.signal(number="S7"))
        self.assertEqual(list(self.mm.signals), ["S7"])
        self.assertEqual(len(self.mm.signals["S7"]), 1)

    def test_both_legs_of_a_straddle_group_under_one_signal_number(self):
        self.mm.execute_event(self.signal(units=1, price=11710.0))
        self.mm.execute_event(self.signal(units=-1, price=11690.0))
        self.assertEqual(len(self.mm.signals["S1"]), 2)
        self.assertEqual([o.units for o in self.mm.signals["S1"]], [100, -100])

    def test_batch_id_starts_at_zero(self):
        self.mm.execute_event(self.signal())
        self.assertEqual(self.mm.signals["S1"][0].batchID, 0)


class TestOneTradeAtATime(MoneyManagerCase):

    def test_a_signal_is_ignored_while_a_trade_is_open(self):
        self.mm.onTrade = True
        self.mm.execute_event(self.signal())
        self.assertEqual(self.sink.events, [])

    def test_a_new_signal_number_is_ignored_once_an_order_is_outstanding(self):
        self.mm.execute_event(self.signal(number="S1"))
        self.sink.events = []
        self.mm.execute_event(self.signal(number="S2"))
        self.assertEqual(self.sink.events, [])

    def test_the_second_leg_of_the_same_signal_still_gets_through(self):
        """
        orderIssued blocks new signal numbers but not further legs of the one
        already working - that is what lets AG01 send its buy and sell stop.
        """
        self.mm.execute_event(self.signal(units=1, number="S1"))
        self.mm.execute_event(self.signal(units=-1, number="S1"))
        self.assertEqual(len(self.sink.of('ORDER')), 2)


class TestBrokerAcknowledgement(MoneyManagerCase):

    def test_the_order_id_is_matched_back_by_price(self):
        self.mm.execute_event(self.signal(price=11700.0))
        self.mm.execute_event(self.acknowledge(1508, price=11700.0))
        order = self.mm.signals["S1"][0]
        self.assertEqual(order.orderID, 1508)
        self.assertEqual(order.batchID, 1508)

    def test_only_the_leg_at_that_price_is_updated(self):
        self.mm.execute_event(self.signal(units=1, price=11710.0))
        self.mm.execute_event(self.signal(units=-1, price=11690.0))
        self.mm.execute_event(self.acknowledge(1508, price=11690.0))
        legs = self.mm.signals["S1"]
        self.assertFalse(legs[0].has_attr('orderID'))
        self.assertEqual(legs[1].orderID, 1508)

    def test_a_close_without_oandas_own_fields_is_still_handled(self):
        """
        Only OANDA sends financing, and only OANDA's close carries an account
        balance. Reading them directly raised AttributeError on every IG
        close - inside a handler, which trading/engine.py answers with
        os._exit(1), so the process died on the first trade it completed.
        """
        self.mm.execute_event(self.signal(price=11700.0))
        self.mm.execute_event(self.acknowledge('PDabc', price=11700.0))
        self.mm.execute_event(self.fill('PDabc'))
        close = TransactionEvent({'type': 'ORDER_FILL', 'orderID': 'PDabc',
                                  'price': 11720.0, 'pl': 3.5,
                                  'instrument': 'DE30_EUR',
                                  'tradesClosed': [{'tradeID': 'D1'}]})
        self.mm.execute_event(close)
        self.assertFalse(self.mm.onTrade)
        self.assertEqual(self.mm.signals, {})

    def test_an_id_that_is_a_name_rather_than_a_number_is_kept(self):
        """
        IG names a deal - 'PD6e1b03...' - where OANDA and eToro number it.
        Was: every id went through int(), so the first acknowledgement from
        IG raised inside the handler and the order was never registered; the
        fill that followed matched nothing and the losing leg was never
        cancelled. Now the name is kept as a name.
        """
        reference = 'PD6e1b0397ad1c4f5a9c0d2e3f4a5b'
        self.mm.execute_event(self.signal(price=11700.0))
        self.mm.execute_event(self.acknowledge(reference, price=11700.0))
        self.assertEqual(self.mm.signals["S1"][0].orderID, reference)

    def test_a_named_id_still_cancels_the_other_leg(self):
        """The whole point of keeping it: the straddle still closes down."""
        self.mm.execute_event(self.signal(units=1, price=11710.0))
        self.mm.execute_event(self.signal(units=-1, price=11690.0))
        self.mm.execute_event(self.acknowledge('PDaaa', price=11710.0))
        self.mm.execute_event(self.acknowledge('PDbbb', price=11690.0))
        self.sink.events = []

        self.mm.execute_event(self.fill('PDaaa'))

        cancels = self.sink.of('ORDERCANCEL')
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0].orderID, 'PDbbb')
        self.assertEqual(self.mm.signals["S1"][0].orderStatus, 'FILLED')
        self.assertEqual(self.mm.signals["S1"][1].orderStatus, 'CANCELED')

    def test_an_acknowledgement_for_an_unknown_signal_is_dropped(self):
        self.mm.execute_event(self.acknowledge(1508, number="NOPE"))
        self.assertEqual(self.mm.signals, {})

    def test_price_matching_is_exact(self):
        """A rounded confirmation price would leave the leg without an id."""
        self.mm.execute_event(self.signal(price=11700.0))
        self.mm.execute_event(self.acknowledge(1508, price=11700.01))
        self.assertFalse(self.mm.signals["S1"][0].has_attr('orderID'))


class TestFillAndOCO(MoneyManagerCase):

    def open_a_straddle(self):
        self.mm.execute_event(self.signal(units=1, price=11710.0))
        self.mm.execute_event(self.signal(units=-1, price=11690.0))
        self.mm.execute_event(self.acknowledge(11, price=11710.0))
        self.mm.execute_event(self.acknowledge(22, price=11690.0))
        self.sink.events = []

    def test_a_fill_marks_the_trade_open(self):
        self.open_a_straddle()
        self.mm.execute_event(self.fill(11))
        self.assertTrue(self.mm.onTrade)

    def test_the_filled_leg_is_marked_filled(self):
        self.open_a_straddle()
        self.mm.execute_event(self.fill(11))
        self.assertEqual(self.mm.signals["S1"][0].orderStatus, 'FILLED')

    def test_the_other_leg_is_cancelled(self):
        self.open_a_straddle()
        self.mm.execute_event(self.fill(11))
        self.assertEqual(self.mm.signals["S1"][1].orderStatus, 'CANCELED')

    def test_a_cancel_event_is_emitted_for_the_losing_leg(self):
        self.open_a_straddle()
        self.mm.execute_event(self.fill(11))
        cancels = self.sink.of('ORDERCANCEL')
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0].orderID, 22)
        self.assertEqual(cancels[0].price, 11690.0)

    def test_a_fill_for_an_unknown_order_changes_no_leg(self):
        self.open_a_straddle()
        self.mm.execute_event(self.fill(999))
        self.assertEqual(self.sink.of('ORDERCANCEL'), [])
        for leg in self.mm.signals["S1"]:
            self.assertFalse(leg.has_attr('orderStatus'))

    def test_a_leg_without_an_order_id_cannot_be_cancelled(self):
        """If the broker never acknowledged a leg, it is left dangling."""
        self.mm.execute_event(self.signal(units=1, price=11710.0))
        self.mm.execute_event(self.signal(units=-1, price=11690.0))
        self.mm.execute_event(self.acknowledge(11, price=11710.0))
        self.sink.events = []
        self.mm.execute_event(self.fill(11))
        self.assertEqual(self.sink.of('ORDERCANCEL'), [])
        self.assertEqual(self.mm.signals["S1"][1].orderStatus, 'CANCELED')


class TestTradeClose(MoneyManagerCase):

    def open_and_close(self):
        self.mm.execute_event(self.signal(units=1, price=11710.0))
        self.mm.execute_event(self.acknowledge(11, price=11710.0))
        self.mm.execute_event(self.fill(11))
        self.mm.execute_event(self.fill(
            11, price=11720.0, tradesClosed=[{"tradeID": "11"}],
            pl="10.0", financing="-0.1", accountBalance="100010"))

    def test_a_close_frees_the_manager_for_the_next_signal(self):
        self.open_and_close()
        self.assertFalse(self.mm.onTrade)
        self.assertFalse(self.mm.orderIssued)

    def test_the_signal_group_moves_to_processed(self):
        self.open_and_close()
        self.assertEqual(self.mm.signals, {})
        self.assertEqual(len(self.mm.processed), 1)

    def test_the_closing_transaction_is_kept_on_the_order(self):
        """This is the record a reconciliation step would read the real fill from."""
        self.open_and_close()
        order = self.mm.processed[0][0]
        self.assertEqual(order.orderStatus, 'CLOSED')
        self.assertEqual(order.closeEvent.pl, "10.0")
        self.assertEqual(order.closeEvent.price, 11720.0)

    def test_a_new_signal_is_accepted_after_the_close(self):
        self.open_and_close()
        self.sink.events = []
        self.mm.execute_event(self.signal(number="S2"))
        self.assertEqual(self.sink.kinds(), ['ORDER'])

    def test_a_close_leaves_other_signal_groups_alone(self):
        """
        Only the group holding the closed order is retired.

        Was: `found` was initialised outside the loop over signal groups and
             never reset, so once one group matched, every group visited after
             it was moved to processed and deleted with its orders untouched.
        Now: the flag is reset per group.
        """
        self.mm.execute_event(self.signal(units=1, price=11710.0, number="S1"))
        self.mm.execute_event(self.acknowledge(11, price=11710.0, number="S1"))
        self.mm.signals["S2"] = [OrderEvent({"instrument": "DE30_EUR",
                                             "units": 1, "price": 1.0})]
        self.mm.execute_event(self.fill(
            11, tradesClosed=[{"tradeID": "11"}], pl="1", financing="0",
            accountBalance="1"))
        self.assertEqual(list(self.mm.signals), ["S2"])
        self.assertEqual(len(self.mm.processed), 1)


class TestEventRouting(MoneyManagerCase):

    def test_unrelated_events_are_ignored(self):
        for ev in (StatusEvent('DONE'), CandleEvent({})):
            self.assertIsNone(self.mm.execute_event(ev))
        self.assertEqual(self.sink.events, [])

    def test_only_order_fill_transactions_are_acted_on(self):
        self.mm.execute_event(self.signal())
        self.sink.events = []
        self.mm.execute_event(TransactionEvent({"type": "STOP_ORDER", "id": "9"}))
        self.assertEqual(self.sink.events, [])
        self.assertFalse(self.mm.onTrade)


if __name__ == "__main__":
    unittest.main()


class TestOrdersThatNeverFill(MoneyManagerCase):
    """
    A signal whose orders all die has to stop blocking the next one.

    Was: nothing cleared orderIssued except a trade closing. An order that
         expired or that the broker refused left the manager believing orders
         were outstanding, and handleSignal refuses every new signal number
         while it believes that - so a bracket that simply expired unfilled
         stopped the strategy for the rest of the process's life. OANDA's
         orders are GTD and expire nightly, so it was a matter of time rather
         than of bad luck.
    Now: an order reported cancelled, expired or rejected is marked, and a
         group with no surviving order is released.
    """

    def straddle(self, low=11690.0, high=11710.0, number="S1"):
        """AG01's pair of opposite orders, both acknowledged."""
        self.mm.execute_event(self.signal(units=1, price=high, number=number))
        self.mm.execute_event(self.signal(units=-1, price=low, number=number))
        self.mm.execute_event(self.acknowledge(1, price=high, number=number))
        self.mm.execute_event(self.acknowledge(2, price=low, number=number))
        self.sink.events = []

    def cancel(self, order_id=None, price=None, reason=None):
        from parity_deriva.event.event import OrderCancelEvent
        payload = {"instrument": "DE30_EUR"}
        if order_id is not None:
            payload["orderID"] = order_id
        if price is not None:
            payload["price"] = price
        if reason is not None:
            payload["reason"] = reason
        return OrderCancelEvent(payload)

    def reject(self, price=11710.0, number="S1"):
        return TransactionEvent({"type": "ORDER_REJECT", "price": price,
                                 "instrument": "DE30_EUR",
                                 "rejectReason": "MARKET_HALTED",
                                 "signalNumber": number})

    # ------------------------------------------------------------- releasing

    def test_a_straddle_that_expires_unfilled_is_released(self):
        self.straddle()
        self.mm.execute_event(self.cancel(order_id=1, price=11710.0))
        self.mm.execute_event(self.cancel(order_id=2, price=11690.0))
        self.assertEqual(self.mm.signals, {})
        self.assertFalse(self.mm.orderIssued)

    def test_the_next_signal_gets_through_afterwards(self):
        """The point of the fix: the strategy keeps working."""
        self.straddle()
        self.mm.execute_event(self.cancel(order_id=1, price=11710.0))
        self.mm.execute_event(self.cancel(order_id=2, price=11690.0))
        self.mm.execute_event(self.signal(number="S2"))
        self.assertEqual(len(self.sink.of('ORDER')), 1)

    def test_one_leg_dying_releases_nothing(self):
        """The other is still resting and may still fill."""
        self.straddle()
        self.mm.execute_event(self.cancel(order_id=1, price=11710.0))
        self.assertIn("S1", self.mm.signals)
        self.assertTrue(self.mm.orderIssued)

    def test_a_released_group_is_kept_in_processed(self):
        """The audit trail should not lose a signal just because it went nowhere."""
        self.straddle()
        self.mm.execute_event(self.cancel(order_id=1, price=11710.0))
        self.mm.execute_event(self.cancel(order_id=2, price=11690.0))
        self.assertEqual(len(self.mm.processed), 1)
        self.assertEqual(len(self.mm.processed[0]), 2)

    # ------------------------------------------------------------ rejections

    def test_a_rejected_order_is_matched_by_price(self):
        """
        A rejected order never got an id - the broker refused it before
        issuing one - so instrument and price are all there is to match on.
        """
        self.mm.execute_event(self.signal(units=1, price=11710.0))
        self.mm.execute_event(self.reject(price=11710.0))
        self.assertEqual(self.mm.signals, {})
        self.assertFalse(self.mm.orderIssued)

    def test_both_legs_rejected_releases_the_signal(self):
        self.mm.execute_event(self.signal(units=1, price=11710.0))
        self.mm.execute_event(self.signal(units=-1, price=11690.0))
        self.mm.execute_event(self.reject(price=11710.0))
        self.assertTrue(self.mm.orderIssued)
        self.mm.execute_event(self.reject(price=11690.0))
        self.assertFalse(self.mm.orderIssued)

    def test_oandas_per_type_reject_names_are_understood(self):
        """
        The transaction stream spells it per order type, where both execution
        handlers publish the normalised 'ORDER_REJECT'.
        """
        self.mm.execute_event(self.signal(units=1, price=11710.0))
        self.mm.execute_event(TransactionEvent(
            {"type": "STOP_ORDER_REJECT", "price": 11710.0,
             "instrument": "DE30_EUR"}))
        self.assertEqual(self.mm.signals, {})

    def test_a_transaction_cancel_from_the_stream_counts(self):
        self.straddle()
        for order_id, price in ((1, 11710.0), (2, 11690.0)):
            self.mm.execute_event(TransactionEvent(
                {"type": "ORDER_CANCEL", "orderID": str(order_id),
                 "price": price, "instrument": "DE30_EUR"}))
        self.assertEqual(self.mm.signals, {})

    # -------------------------------------------------- not releasing a trade

    def test_a_filled_leg_keeps_its_group_alive(self):
        """
        OANDA's stream also reports the cancel of the losing leg after a fill,
        and eToro's expiry pass can fire on the same tick. The group has to
        survive until closeTrade, or the manager would take new signals while
        a trade is open.
        """
        self.straddle()
        self.mm.execute_event(self.fill(1, price=11710.0))
        self.mm.execute_event(self.cancel(order_id=2, price=11690.0))
        self.assertIn("S1", self.mm.signals)
        self.assertTrue(self.mm.onTrade)
        self.assertTrue(self.mm.orderIssued)

    def test_a_fill_status_is_never_overwritten_by_a_late_cancel(self):
        self.straddle()
        self.mm.execute_event(self.fill(1, price=11710.0))
        self.mm.execute_event(self.cancel(order_id=1, price=11710.0))
        order = [o for o in self.mm.signals["S1"] if o.orderID == 1][0]
        self.assertEqual(order.orderStatus, 'FILLED')

    def test_a_signal_is_still_refused_while_a_trade_is_open(self):
        self.straddle()
        self.mm.execute_event(self.fill(1, price=11710.0))
        self.sink.events = []
        self.mm.execute_event(self.signal(number="S2"))
        self.assertEqual(self.sink.of('ORDER'), [])

    # ------------------------------------------------------------- stragglers

    def test_a_cancel_for_an_unknown_order_is_harmless(self):
        """Another strategy's order, or one from before this process started."""
        self.straddle()
        self.mm.execute_event(self.cancel(order_id=999, price=12345.0))
        self.assertIn("S1", self.mm.signals)
        self.assertTrue(self.mm.orderIssued)

    def test_a_cancel_with_nothing_to_match_on_is_harmless(self):
        self.straddle()
        self.mm.execute_event(self.cancel())
        self.assertIn("S1", self.mm.signals)

    def test_an_empty_group_is_not_a_finished_one(self):
        """all() of an empty sequence is True, which would release it."""
        self.mm.signals["S9"] = []
        self.mm.orderIssued = True
        self.mm.release()
        self.assertIn("S9", self.mm.signals)
        self.assertTrue(self.mm.orderIssued)

    def test_release_does_not_unblock_while_a_trade_is_open(self):
        self.mm.onTrade = True
        self.mm.orderIssued = True
        self.mm.release()
        self.assertTrue(self.mm.orderIssued)
