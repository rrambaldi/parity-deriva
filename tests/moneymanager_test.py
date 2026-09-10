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
