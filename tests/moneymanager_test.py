"""
Tests for parity_deriva.portfolio.moneymanager.

MoneyManager is the bookkeeper: it turns signals into sized orders, keeps the
signalNumber -> [orders] index, emulates OCO by cancelling the losing leg of a
straddle, and enforces one open trade at a time. That index is the only place
where a real fill can be matched back to the signal that produced it, so it is
the natural anchor for a real-vs-simulated comparison.
"""

import datetime
import logging
import unittest

from parity_deriva.event.event import (SignalEvent, OrderEvent, ClientOrderEvent,
                                 TransactionEvent, StatusEvent, CandleEvent)
from parity_deriva.data import calendar
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


class TestMaxStop(MoneyManagerCase):
    """
    The widest stop the account will take a position on, in pips.

    One rule for every strategy, because it is a rule about the account: every
    strategy that places orders comes through here. The fixture's signal is a
    ten point stop on an instrument whose pip is one point.
    """

    def setUp(self):
        super(TestMaxStop, self).setUp()
        # the temp settings are a mock, so the precision that decides what a
        # pip is has to be said rather than auto-created as another mock
        self.settings.INSTRUMENT_PRECISION = {'DE30_EUR': 1}
        self.settings.DEFAULT_PRICE_PRECISION = 5

    def manager(self, pips):
        mm = MoneyManager(setup=self.settings, units=100, maxStopPips=pips)
        mm.signals, mm.processed = {}, []
        mm.onTrade = mm.orderIssued = False
        mm.set_queue(self.sink)
        return mm

    def test_no_ceiling_is_the_default(self):
        self.assertIsNone(MoneyManager(setup=self.settings).maxStopPips)
        self.mm.handleSignal(self.signal())
        self.assertEqual(len(self.sink.of('ORDER')), 1)

    def test_a_stop_inside_the_ceiling_is_traded(self):
        self.manager(10).handleSignal(self.signal())
        self.assertEqual(len(self.sink.of('ORDER')), 1)

    def test_a_stop_wider_than_the_ceiling_is_refused(self):
        """
        Refused, not pulled in: the stop is where the rule says the setup
        failed, and moving it would be this component choosing a level the
        strategy did not - and dragging the target along with it.
        """
        self.manager(9).handleSignal(self.signal())
        self.assertEqual(self.sink.events, [])

    def test_the_ceiling_is_read_in_pips_and_not_in_price(self):
        """
        Ten points of the DAX is ten pips; ten points of EUR_USD would be a
        hundred thousand. A ceiling that did not divide by the pip would be a
        different rule per instrument under one name.
        """
        mm = self.manager(10)
        self.assertFalse(mm.tooWide({'instrument': 'DE30_EUR',
                                     'price': 11700.0, 'stopLoss': 11690.0}))
        self.assertTrue(mm.tooWide({'instrument': 'DE30_EUR',
                                    'price': 11700.0, 'stopLoss': 11689.0}))

    def test_a_signal_with_no_stop_is_not_too_wide(self):
        """It is unsizable, which size() already refuses and says why."""
        mm = self.manager(10)
        self.assertFalse(mm.tooWide({'instrument': 'DE30_EUR',
                                     'price': 11700.0, 'stopLoss': None}))


class TestLevelScale(MoneyManagerCase):
    """
    slScale and tpScale move the initial stop and target, as a multiple of
    their distance from the entry. The fixture is a 10 point stop and a 20
    point target on a 11700 entry.
    """

    def setUp(self):
        super(TestLevelScale, self).setUp()
        # a pip of the DAX is one point, for the ceiling test
        self.settings.INSTRUMENT_PRECISION = {'DE30_EUR': 1}
        self.settings.DEFAULT_PRICE_PRECISION = 5

    def scaled(self, sl=None, tp=None, pips=None):
        mm = MoneyManager(setup=self.settings, units=100, slScale=sl,
                          tpScale=tp, maxStopPips=pips)
        mm.signals, mm.processed = {}, []
        mm.onTrade = mm.orderIssued = False
        mm.set_queue(self.sink)
        mm.handleSignal(self.signal())
        orders = self.sink.of('ORDER')
        return orders[0] if orders else None

    def test_none_leaves_them_where_they_were(self):
        order = self.scaled()
        self.assertEqual((order.stopLoss, order.takeProfit), (11690.0, 11720.0))

    def test_a_multiple_of_the_distance(self):
        order = self.scaled(sl=1.5, tp=0.5)
        self.assertAlmostEqual(order.stopLoss, 11685.0)
        self.assertAlmostEqual(order.takeProfit, 11710.0)

    def test_a_short_scales_the_other_way(self):
        mm = MoneyManager(setup=self.settings, units=100, slScale=2)
        mm.signals, mm.processed = {}, []
        mm.onTrade = mm.orderIssued = False
        mm.set_queue(self.sink)
        mm.handleSignal(SignalEvent({"instrument": "DE30_EUR", "units": -1,
                                     "orderType": "STOP", "price": 11700.0,
                                     "stopLoss": 11710.0, "takeProfit": 11680.0,
                                     "signalNumber": "S1", "gtdTime": None}))
        self.assertAlmostEqual(self.sink.of('ORDER')[0].stopLoss, 11720.0)

    def test_the_ceiling_reads_the_stop_that_is_traded(self):
        """A 10 point stop fits a 12 pip ceiling; the same stop x1.5 does not."""
        self.assertIsNotNone(self.scaled(pips=12))
        self.sink.events[:] = []
        self.assertIsNone(self.scaled(sl=1.5, pips=12))


class TestOrderShape(TestLevelScale):
    """
    inverse, trailing and trailProfit: rules about every order, on the same
    fixture - a buy stop at 11700, stop 11690, target 11720.
    """

    def shaped(self, signal=None, **rules):
        mm = MoneyManager(setup=self.settings, units=100, **rules)
        mm.signals, mm.processed = {}, []
        mm.onTrade = mm.orderIssued = False
        mm.set_queue(self.sink)
        mm.handleSignal(signal or self.signal())
        return self.sink.of('ORDER')[0]

    def test_inverse_sells_on_a_limit_with_stop_and_target_swapped(self):
        order = self.shaped(inverse=True)
        self.assertEqual(order.units, -100)
        self.assertEqual(order.orderType, 'LIMIT')
        self.assertEqual((order.stopLoss, order.takeProfit), (11720.0, 11690.0))

    def test_inverse_mirrors_a_missing_target(self):
        signal = self.signal()
        signal.takeProfit = None
        order = self.shaped(signal, inverse=True)
        self.assertEqual((order.stopLoss, order.takeProfit), (11710.0, 11690.0))

    def test_the_scales_stretch_the_inverse_bracket(self):
        order = self.shaped(inverse=True, slScale=2)
        self.assertAlmostEqual(order.stopLoss, 11740.0)

    def test_trailing_0_takes_the_ladder_off(self):
        signal = self.signal()
        signal.trailStep, signal.timeStopBars = 5.0, 2
        order = self.shaped(signal, trailing=0)
        self.assertIsNone(getattr(order, 'trailStep', None))
        self.assertIsNone(getattr(order, 'timeStopBars', None))

    def test_trailing_1_follows_at_the_initial_distance_unless_there_is_a_ladder(self):
        self.assertAlmostEqual(self.shaped(trailing=1).trailDistance, 10.0)
        self.sink.events[:] = []
        self.assertAlmostEqual(self.shaped(trailing=1, slScale=1.5).trailDistance, 15.0)
        self.sink.events[:] = []
        signal = self.signal()
        signal.trailStep = 5.0
        self.assertIsNone(getattr(self.shaped(signal, trailing=1), 'trailDistance', None))

    def test_trail_pips_is_a_distance_of_its_own_off_only_with_trailing_0(self):
        from parity_deriva.lib.utils import pipSize
        pip = pipSize('DE30_EUR', self.settings)
        # the initial stop is 10 away; the stop follows 4 pips behind instead
        order = self.shaped(trailPips=4)
        self.assertAlmostEqual(order.trailDistance, 4 * pip)
        self.assertTrue(order.trailFromEntry)
        self.sink.events[:] = []
        self.assertAlmostEqual(self.shaped(trailPips=4, trailing=1, slScale=2).trailDistance, 4 * pip)
        self.sink.events[:] = []
        signal = self.signal()
        signal.trailStep = 5.0
        self.assertAlmostEqual(self.shaped(signal, trailPips=4).trailDistance, 4 * pip)
        self.sink.events[:] = []
        self.assertIsNone(getattr(self.shaped(trailPips=4, trailing=0), 'trailDistance', None))

    def test_trail_profit_sends_no_target_and_carries_it(self):
        order = self.shaped(trailProfit=True, tpScale=0.5)
        self.assertIsNone(order.takeProfit)
        self.assertAlmostEqual(order.trailTarget, 11710.0)

    def test_none_of_them_is_the_order_as_it_was(self):
        order = self.shaped()
        self.assertEqual((order.units, order.orderType, order.takeProfit),
                         (100, 'STOP', 11720.0))
        self.assertIsNone(getattr(order, 'trailDistance', None))


class TestSessionHours(MoneyManagerCase):
    """
    When this account decides, in UTC.

    One rule for every strategy, like the stop ceiling and for the same
    reason. It closes the signal and not the order: an order placed inside
    the window rests until it fills or expires, because "this account decides
    between eight and four" is a different rule from "its pending orders
    vanish at four".
    """

    def manager(self, session):
        mm = MoneyManager(setup=self.settings, units=100, session=session)
        mm.signals, mm.processed = {}, []
        mm.onTrade = mm.orderIssued = False
        mm.set_queue(self.sink)
        return mm

    def at(self, hour, minute=0):
        se = self.signal()
        se.time = datetime.datetime(2024, 1, 2, hour, minute)
        return se

    def test_no_window_is_all_day(self):
        self.assertIsNone(MoneyManager(setup=self.settings).session)
        self.manager(None).handleSignal(self.at(3))
        self.assertEqual(len(self.sink.of('ORDER')), 1)

    def test_a_signal_inside_the_window_is_taken(self):
        self.manager(('07:00', '16:00')).handleSignal(self.at(7))
        self.assertEqual(len(self.sink.of('ORDER')), 1)

    def test_a_signal_outside_it_is_refused(self):
        self.manager(('07:00', '16:00')).handleSignal(self.at(6, 59))
        self.assertEqual(self.sink.of('ORDER'), [])

    def test_the_end_is_outside(self):
        """
        Half open, like every other window here: 16:00 is the first instant
        after the session and not the last of it.
        """
        mm = self.manager(('07:00', '16:00'))
        mm.handleSignal(self.at(15, 59))
        self.assertEqual(len(self.sink.of('ORDER')), 1)
        mm.onTrade = mm.orderIssued = False
        mm.handleSignal(self.at(16))
        self.assertEqual(len(self.sink.of('ORDER')), 1, "16:00 is the next day's")

    def test_a_window_that_wraps_midnight(self):
        """('22:00', '06:00') is the Asian session, not an empty one."""
        mm = self.manager(('22:00', '06:00'))
        self.assertFalse(mm.outsideSession(datetime.datetime(2024, 1, 2, 23)))
        self.assertFalse(mm.outsideSession(datetime.datetime(2024, 1, 2, 3)))
        self.assertTrue(mm.outsideSession(datetime.datetime(2024, 1, 2, 12)))

    def test_a_signal_with_no_time_is_not_refused(self):
        """A hand-made signal in a test has no clock to be outside of."""
        self.manager(('07:00', '16:00')).handleSignal(self.signal())
        self.assertEqual(len(self.sink.of('ORDER')), 1)


class TestNewsWindows(MoneyManagerCase):
    """
    Standing aside for the calendar. The widths and which events matter
    belong to the Calendar handed in; this only asks it about an instant.
    """

    def manager(self, before=10, after=10, impacts=(calendar.HIGH,)):
        frame = calendar.merge(calendar.empty(), [
            ('2024-01-02 13:30:00', 'EUR', 'high', 'Rate decision'),
            ('2024-01-02 15:00:00', 'EUR', 'low', 'Something small'),
            ('2024-01-02 17:00:00', 'JPY', 'high', 'Elsewhere'),
        ])
        mm = MoneyManager(setup=self.settings, units=100,
                          calendar=calendar.Calendar(
                              frame, currencies=('DE30', 'EUR'),
                              impacts=impacts, before=before, after=after))
        mm.signals, mm.processed = {}, []
        mm.onTrade = mm.orderIssued = False
        mm.set_queue(self.sink)
        return mm

    def at(self, hour, minute=0):
        se = self.signal()
        se.time = datetime.datetime(2024, 1, 2, hour, minute)
        return se

    def test_no_calendar_is_no_rule(self):
        self.assertIsNone(MoneyManager(setup=self.settings).calendar)
        self.mm.handleSignal(self.at(13, 30))
        self.assertEqual(len(self.sink.of('ORDER')), 1)

    def test_a_signal_on_the_news_is_refused(self):
        self.manager().handleSignal(self.at(13, 25))
        self.assertEqual(self.sink.of('ORDER'), [])

    def test_a_signal_clear_of_it_is_taken(self):
        self.manager().handleSignal(self.at(13, 19))
        self.assertEqual(len(self.sink.of('ORDER')), 1)

    def test_an_event_of_another_currency_is_not_this_pair_s_news(self):
        self.manager().handleSignal(self.at(17))
        self.assertEqual(len(self.sink.of('ORDER')), 1)

    def test_an_impact_not_asked_for_is_not_news(self):
        self.manager().handleSignal(self.at(15))
        self.assertEqual(len(self.sink.of('ORDER')), 1)
        wider = self.manager(impacts=(calendar.HIGH, calendar.LOW))
        wider.handleSignal(self.at(15))
        self.assertEqual(len(self.sink.of('ORDER')), 1,
                         "the wider calendar refused it, so nothing was added")


class RiskSizingCase(MoneyManagerCase):
    """
    Sizing a trade off the account instead of off a fixed number of units.

    The rule: a trade that exits on its stop loses `risk` of the capital,
    whatever the distance to that stop is, and the capital is re-read from
    the account at the start of each calendar month. Every number below is
    chosen so the arithmetic can be checked in the head - a stop ten points
    away and a thousand at risk is a hundred units.
    """

    def manager(self, risk=0.01, balance=100000.0, **extra):
        mm = MoneyManager(setup=self.settings, units=100, risk=risk,
                          balance=balance, **extra)
        mm.signals = {}
        mm.processed = []
        mm.onTrade = False
        mm.orderIssued = False
        mm.set_queue(self.sink)
        return mm

    def at(self, when, units=1, price=11700.0, stop=11690.0, number="S1"):
        """A signal that happened on a given day."""
        event = self.signal(units=units, price=price, number=number)
        event.stopLoss = stop
        event.time = when
        return event

    def sizes(self):
        return [o.units for o in self.sink.events if str(o) == 'ORDER']

    def close(self, mm, balance, pl=None):
        """What the broker says the account holds after a trade closed."""
        mm.closeTrade(TransactionEvent({"type": "ORDER_FILL",
                                        "orderID": "1",
                                        "tradesClosed": [{}],
                                        "pl": pl,
                                        "accountBalance": balance}))


class TestRiskSizing(RiskSizingCase):

    def test_the_stop_distance_decides_the_size(self):
        mm = self.manager()
        # 1% of 100000 is 1000; a stop 10 points away is 100 units
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5)))
        self.assertEqual(self.sizes(), [100.0])

    def test_a_wider_stop_buys_fewer_units(self):
        """The loss at the stop is the constant, not the size."""
        mm = self.manager()
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5), stop=11600.0))
        self.assertEqual(self.sizes(), [10.0])

    def test_the_side_of_the_signal_survives_the_sizing(self):
        mm = self.manager()
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5), units=-1))
        self.assertEqual(self.sizes(), [-100.0])

    def test_units_is_ignored_when_a_risk_is_given(self):
        """
        Two ways to size would be two answers to one question. The fixture
        asks for units=100 as well, and a size of 100 here would be that
        number by coincidence, so the stop is moved to tell them apart.
        """
        mm = self.manager()
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5), stop=11695.0))
        self.assertEqual(self.sizes(), [200.0])

    def test_without_a_risk_nothing_changes(self):
        mm = self.manager(risk=None)
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5)))
        self.assertEqual(self.sizes(), [100.0])   # units=100 x the signal's 1


class TestRiskRefusals(RiskSizingCase):
    """
    A signal that cannot be sized is dropped, not guessed at and not raised.

    Dropped because any size invented here is a position whose loss nobody
    chose; not raised because trading/engine.py answers an exception in a
    handler with os._exit(1), and one bad signal is not a reason to take a
    live session down.
    """

    def test_a_signal_with_no_stop_is_not_sent(self):
        mm = self.manager()
        event = self.at(datetime.datetime(2018, 1, 5))
        event.stopLoss = None
        mm.handleSignal(event)
        self.assertEqual(self.sizes(), [])
        self.assertFalse(mm.orderIssued, "and the manager is not left blocked")

    def test_a_stop_at_the_entry_price_is_not_sent(self):
        mm = self.manager()
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5), stop=11700.0))
        self.assertEqual(self.sizes(), [])

    def test_an_account_at_nothing_sends_nothing(self):
        mm = self.manager(balance=0.0)
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5)))
        self.assertEqual(self.sizes(), [])

    def test_a_size_that_rounds_to_nothing_is_not_sent(self):
        """Not a small trade: no trade. 0 units is an order for nothing."""
        mm = self.manager(balance=0.01, risk=0.0001)
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5)))
        self.assertEqual(self.sizes(), [])


class TestMonthlyReview(RiskSizingCase):

    def test_the_size_does_not_move_inside_a_month(self):
        """
        Re-reading the balance after every close would make each trade's size
        depend on the one before it, which is compounding by the hour rather
        than a monthly review.
        """
        mm = self.manager()
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5)))
        self.close(mm, 200000.0)
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 25), number="S2"))
        self.assertEqual(self.sizes(), [100.0, 100.0])

    def test_the_next_month_sizes_off_what_the_account_now_holds(self):
        mm = self.manager()
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5)))
        self.close(mm, 200000.0)
        mm.handleSignal(self.at(datetime.datetime(2018, 2, 1), number="S2"))
        self.assertEqual(self.sizes(), [100.0, 200.0])

    def test_a_reference_capital_moves_with_its_own_closes_not_the_account(self):
        # the account holds 5000000 - other money - and this session made
        # 100000: the next month risks on 200000, not on 5000000
        mm = self.manager(reference=True)
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5)))
        self.close(mm, 5000000.0, pl="100000")
        mm.handleSignal(self.at(datetime.datetime(2018, 2, 1), number="S2"))
        self.assertEqual(self.sizes(), [100.0, 200.0])

    def test_a_reference_capital_s_pl_is_converted_at_the_rate_it_was(self):
        # 100000 USD of capital from a EUR account at 1.25: a 40000 EUR win
        # is 50000 USD
        mm = self.manager(reference=True, plRate=1.25)
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5)))
        self.close(mm, None, pl="40000")
        mm.handleSignal(self.at(datetime.datetime(2018, 2, 1), number="S2"))
        self.assertEqual(self.sizes(), [100.0, 150.0])

    def test_a_reference_capital_does_not_move_inside_the_month(self):
        mm = self.manager(reference=True)
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5)))
        self.close(mm, None, pl="100000")
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 20), number="S2"))
        self.assertEqual(self.sizes(), [100.0, 100.0])

    def test_a_losing_month_sizes_down(self):
        mm = self.manager()
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5)))
        self.close(mm, 50000.0)
        mm.handleSignal(self.at(datetime.datetime(2018, 2, 1), number="S2"))
        self.assertEqual(self.sizes(), [100.0, 50.0])

    def test_january_of_the_next_year_is_a_new_month(self):
        """(year, month), not the month number, which repeats every twelve."""
        mm = self.manager()
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5)))
        self.close(mm, 200000.0)
        mm.handleSignal(self.at(datetime.datetime(2019, 1, 5), number="S2"))
        self.assertEqual(self.sizes(), [100.0, 200.0])

    def test_a_month_that_ended_where_it_began_sizes_the_same(self):
        """
        The review re-reads the account; it does not move the size by itself.
        A month that gave back exactly what it made sizes the next one
        identically. (A month with no close at all cannot be asked here: the
        manager allows one trade at a time, so the second signal would be
        refused for that reason and the test would pass without touching the
        review.)
        """
        mm = self.manager()
        mm.handleSignal(self.at(datetime.datetime(2018, 1, 5)))
        self.close(mm, 100000.0)
        mm.handleSignal(self.at(datetime.datetime(2018, 3, 5), number="S2"))
        self.assertEqual(self.sizes(), [100.0, 100.0])

    def test_a_signal_with_no_time_sizes_off_the_opening_capital(self):
        """
        Replays and tests build signals without one. Such a run must not
        re-read the balance on every signal - that would be the compounding
        the monthly review exists to avoid - so it sizes off the opening
        figure throughout.
        """
        mm = self.manager()
        first = self.at(datetime.datetime(2018, 1, 5))
        del first.time
        mm.handleSignal(first)
        self.close(mm, 200000.0)
        second = self.at(datetime.datetime(2018, 1, 5), number="S2")
        del second.time
        mm.handleSignal(second)
        self.assertEqual(self.sizes(), [100.0, 100.0])
