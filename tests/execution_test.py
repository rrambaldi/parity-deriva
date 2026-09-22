"""
Tests for parity_deriva.execution.execution.

Everything here runs against a fake HTTPSConnection: no request ever leaves
the machine. The point is to pin the exact wire format of an OANDA v3 order,
because that payload is the thing the simulated side has to mirror.
"""

import datetime
import json
import unittest
from unittest import mock

from parity_deriva.event.event import (OrderEvent, OrderCancelEvent, SignalEvent,
                                       StopModifyEvent)
from parity_deriva.execution import execution as ex
from parity_deriva.execution.execution import OANDAExecutionHandler, SimulatedExecution
from parity_deriva.tests.helpers import FakeHTTPSConnection, Recorder, TempDirCase


ORDER_ACCEPTED = {
    "orderCreateTransaction": {
        "type": "STOP_ORDER", "instrument": "DE30_EUR", "units": "-1",
        "price": "11583.3", "id": "1508", "batchID": "1508",
        "userID": 0, "accountID": "101-000-0000000-000",
        "time": "2017-01-31T15:59:02.470947825Z"},
    "relatedTransactionIDs": ["1508"], "lastTransactionID": "1508"}

ORDER_REJECTED = {
    "orderRejectTransaction": {"type": "STOP_ORDER_REJECT", "id": "1481"},
    "errorMessage": "precision exceeded",
    "errorCode": "TAKE_PROFIT_ON_FILL_PRICE_PRECISION_EXCEEDED"}


class TestSimulatedExecution(unittest.TestCase):

    def test_execute_order_swallows_everything(self):
        """The Portfolio does the fill accounting; this class is a sink."""
        self.assertIsNone(SimulatedExecution().execute_order(object()))


class ExecutionCase(TempDirCase):

    def setUp(self):
        super(ExecutionCase, self).setUp()
        FakeHTTPSConnection.reset(json.dumps(ORDER_ACCEPTED).encode("utf-8"))
        patcher = mock.patch.object(ex.httplib, 'HTTPSConnection',
                                    FakeHTTPSConnection)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.handler = OANDAExecutionHandler(setup=self.settings)
        self.sink = Recorder()
        self.handler.set_queue(self.sink)

    def order(self, **over):
        payload = {"instrument": "DE30_EUR", "units": -1, "orderType": "STOP",
                   "price": 11583.3, "stopLoss": 11591.3, "takeProfit": 11575.2,
                   "gtdTime": None, "signalNumber": "S1"}
        payload.update(over)
        return OrderEvent(payload)


class TestOANDAExecutionHandlerSetup(ExecutionCase):

    def test_api_path_is_scoped_to_the_account(self):
        self.assertEqual(self.handler.api, "/v3/accounts/001-TEST-000/orders")

    def test_headers_carry_the_bearer_token_and_json_content_type(self):
        self.assertEqual(self.handler.headers, {
            "Content-Type": "application/json",
            "Authorization": "Bearer TESTTOKEN"})


class TestOrderSubmission(ExecutionCase):

    def test_posts_to_the_orders_endpoint_on_the_api_domain(self):
        self.handler.execute_event(self.order())
        call = FakeHTTPSConnection.last()
        self.assertEqual(call['host'], self.settings.API_DOMAIN)
        self.assertEqual(call['method'], "POST")
        self.assertEqual(call['url'], "/v3/accounts/001-TEST-000/orders")

    def test_body_shape(self):
        self.handler.execute_event(self.order())
        body = json.loads(FakeHTTPSConnection.last()['body'])
        self.assertEqual(list(body), ['order'])
        self.assertEqual(body['order']['instrument'], "DE30_EUR")
        self.assertEqual(body['order']['type'], "STOP")
        self.assertEqual(body['order']['timeInForce'], "GTD")

    def test_units_and_price_are_stringified(self):
        """OANDA v3 wants decimal numbers as strings."""
        body = json.loads(self._submit()['body'])
        self.assertEqual(body['order']['units'], "-1")
        self.assertEqual(body['order']['price'], "11583.3")

    def _submit(self, **over):
        self.handler.execute_event(self.order(**over))
        return FakeHTTPSConnection.last()

    def test_stop_loss_and_take_profit_are_nested_on_fill(self):
        body = json.loads(self._submit()['body'])
        self.assertEqual(body['order']['stopLossOnFill'], {"price": "11591.3"})
        self.assertEqual(body['order']['takeProfitOnFill'], {"price": "11575.2"})

    def test_absent_stop_loss_and_take_profit_are_omitted(self):
        body = json.loads(self._submit(stopLoss=None, takeProfit=None)['body'])
        self.assertNotIn('stopLossOnFill', body['order'])
        self.assertNotIn('takeProfitOnFill', body['order'])

    def test_gtd_defaults_to_23_00_today_when_the_event_has_none(self):
        body = json.loads(self._submit()['body'])
        expected = datetime.datetime.today().replace(
            hour=23, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.assertEqual(body['order']['gtdTime'], expected)

    def test_explicit_gtd_time_is_used(self):
        when = datetime.datetime(2017, 1, 31, 20, 0, 0)
        body = json.loads(self._submit(gtdTime=when)['body'])
        self.assertEqual(body['order']['gtdTime'], "2017-01-31T20:00:00.000Z")

    def test_the_signal_key_travels_with_the_order(self):
        """
        OANDA echoes clientExtensions back on every transaction the order
        generates, so a live fill arrives already carrying the key its
        simulated counterpart is filed under. The strategies have always built
        the field; the handler simply never sent it.
        """
        extension = {'id': 'AG01:DE30_EUR:M5:20170131T155900',
                     'tag': 'AG01', 'comment': 'M5'}
        self.handler.execute_event(self.order(clientExtension=extension))
        body = json.loads(FakeHTTPSConnection.last()['body'])
        self.assertEqual(body['order']['clientExtensions'], extension)

    def test_an_order_without_extensions_sends_none(self):
        self.handler.execute_event(self.order(clientExtension=None))
        body = json.loads(FakeHTTPSConnection.last()['body'])
        self.assertNotIn('clientExtensions', body['order'])

    def test_an_order_that_never_had_the_field_sends_none(self):
        self.handler.execute_event(OrderEvent(
            {"instrument": "DE30_EUR", "units": -1, "orderType": "STOP",
             "price": 11583.3, "stopLoss": None, "takeProfit": None,
             "gtdTime": None, "signalNumber": "S1"}))
        body = json.loads(FakeHTTPSConnection.last()['body'])
        self.assertNotIn('clientExtensions', body['order'])

    def test_accepted_order_emits_a_client_order_event(self):
        self.handler.execute_event(self.order())
        self.assertEqual(self.sink.kinds(), ['CLIENTORDER'])
        coe = self.sink.events[0]
        self.assertEqual(coe.id, "1508")
        self.assertEqual(coe.batchID, "1508")

    def test_the_signal_number_is_carried_onto_the_client_order(self):
        """This is the only link back to the signal that produced the order."""
        self.handler.execute_event(self.order(signalNumber="S42"))
        self.assertEqual(self.sink.events[0].signalNumber, "S42")

    def test_rejected_order_is_published(self):
        """
        Was: the rejection was logged and dropped. The money manager was then
             left believing an order was outstanding that the broker had
             refused, and it refuses every new signal number while it
             believes that - so a rejected bracket stopped the strategy for
             the rest of the process's life.
        Now: it reaches the bus, and MoneyManager.orderDied releases the
             signal.
        """
        FakeHTTPSConnection.reset(json.dumps(ORDER_REJECTED).encode("utf-8"))
        self.handler.execute_event(self.order())
        self.assertEqual(self.sink.kinds(), ['TRANSACTION'])
        rejection = self.sink.events[0]
        self.assertEqual(rejection.type, 'ORDER_REJECT')
        self.assertEqual(rejection.rejectReason,
                         ORDER_REJECTED['errorCode'])

    def test_a_rejection_is_not_a_client_order(self):
        """
        Publishing it as one would have the money manager record an orderID
        for an order that does not exist, and the fill poller wait on it.
        """
        FakeHTTPSConnection.reset(json.dumps(ORDER_REJECTED).encode("utf-8"))
        self.handler.execute_event(self.order())
        self.assertEqual(self.sink.of('CLIENTORDER'), [])

    def test_the_signal_number_is_carried_onto_the_rejection(self):
        """Without it the money manager cannot tell which signal to release."""
        FakeHTTPSConnection.reset(json.dumps(ORDER_REJECTED).encode("utf-8"))
        self.handler.execute_event(self.order(signalNumber="S42"))
        self.assertEqual(self.sink.events[0].signalNumber, "S42")

    def test_the_rejection_is_normalised_across_brokers(self):
        """
        data/etoro.py publishes the same type when it finds a rejection by
        polling, so the money manager has one branch rather than one per
        broker.
        """
        from parity_deriva.data.etoro import STATUS_REJECTED
        self.assertEqual(STATUS_REJECTED, 4)
        FakeHTTPSConnection.reset(json.dumps(ORDER_REJECTED).encode("utf-8"))
        self.handler.execute_event(self.order())
        self.assertEqual(self.sink.events[0].type, 'ORDER_REJECT')


class TestStopModify(ExecutionCase):
    """
    Moving the stop of a trade that is already open.

    OANDA has no amend call, and does not need one: PUT on the trade's orders
    collection cancels the stop the trade carries and attaches the new one, as
    a single transaction batch. These tests pin that wire format, because the
    simulator moves its own stop unconditionally and a request the account
    refuses is the two sides parting company.
    """

    def modify(self, **over):
        payload = {"orderID": 1508, "tradeID": "7291", "price": 1.3005,
                   "instrument": "EUR_USD", "signalNumber": "S1"}
        payload.update(over)
        self.handler.execute_event(StopModifyEvent(payload))

    def test_puts_to_the_trades_orders_collection(self):
        self.modify()
        call = FakeHTTPSConnection.last()
        self.assertEqual(call['host'], self.settings.API_DOMAIN)
        self.assertEqual(call['method'], "PUT")
        self.assertEqual(call['url'],
                         "/v3/accounts/001-TEST-000/trades/7291/orders")

    def test_the_body_is_the_new_stop_alone(self):
        """
        Only stopLoss. A body naming the take profit as well would cancel a
        target that was never mentioned - and the strategies that move a
        stop have none, so the omission would be silent until a strategy that
        does have one runs here.
        """
        self.modify()
        body = json.loads(FakeHTTPSConnection.last()['body'])
        self.assertEqual(body, {"stopLoss": {"price": "1.3005",
                                             "timeInForce": "GTC"}})

    def test_the_stop_is_good_till_cancelled(self):
        """A stop expiring with the session leaves the trade naked overnight."""
        self.modify()
        body = json.loads(FakeHTTPSConnection.last()['body'])
        self.assertEqual(body['stopLoss']['timeInForce'], "GTC")

    def test_the_trade_is_named_not_the_entry_order(self):
        """
        At OANDA the two are different numbers and the stop hangs off the
        trade. Naming the order id would address someone else's trade.
        """
        self.modify(orderID=1508, tradeID="7291")
        self.assertIn("/trades/7291/orders", FakeHTTPSConnection.last()['url'])
        self.assertNotIn("1508", FakeHTTPSConnection.last()['url'])

    def test_without_a_trade_id_nothing_is_sent(self):
        """
        The id comes off the opening fill's tradeOpened. Guessing one would
        move the stop of a trade that is not this one.
        """
        self.modify(tradeID=None)
        self.assertEqual(FakeHTTPSConnection.calls, [])

    def test_without_a_price_nothing_is_sent(self):
        self.modify(price=None)
        self.assertEqual(FakeHTTPSConnection.calls, [])

    def test_a_level_of_zero_is_not_a_level(self):
        """
        Event.__set__ renders a price it cannot read as the string "0.0". The
        guard is on the value, not on None, or the account would be asked to
        put the stop at zero.
        """
        self.modify(price=None)
        self.assertEqual(FakeHTTPSConnection.calls, [])

    def test_a_refusal_publishes_nothing(self):
        """
        Unlike a rejected order, which strands the money manager: nothing is
        waiting on a stop modification, so a refusal is logged loudly and the
        bus is left alone.
        """
        FakeHTTPSConnection.reset(json.dumps(
            {"errorCode": "TRADE_DOESNT_EXIST"}).encode("utf-8"))
        self.modify()
        self.assertEqual(self.sink.events, [])

    def test_a_success_publishes_nothing_either(self):
        self.modify()
        self.assertEqual(self.sink.events, [])


class TestEventRouting(ExecutionCase):

    def test_only_orders_cancels_and_stop_moves_are_acted_on(self):
        for ignored in (SignalEvent({"units": 1}),):
            self.handler.execute_event(ignored)
        self.assertEqual(FakeHTTPSConnection.calls, [])
        self.assertEqual(self.sink.events, [])

    def test_cancel_issues_a_put_to_the_cancel_endpoint(self):
        self.handler.execute_event(OrderCancelEvent(
            {"orderID": 1508, "price": 11583.3, "instrument": "DE30_EUR"}))
        call = FakeHTTPSConnection.last()
        self.assertEqual(call['method'], "PUT")
        self.assertEqual(call["url"], "/v3/accounts/001-TEST-000/orders/1508/cancel")

    def test_cancel_url_is_missing_the_orders_segment(self):
        """
        The path is built as "%s/%d/cancel" % (self.api, id) where self.api
        already ends in /orders, so this yields .../orders/1508/cancel - which
        is right. Pinned so the string arithmetic is not broken by accident.
        """
        self.handler.execute_event(OrderCancelEvent({"orderID": 7}))
        self.assertTrue(FakeHTTPSConnection.last()['url'].endswith("/orders/7/cancel"))

    def test_cancel_needs_a_numeric_order_id(self):
        with self.assertRaises(TypeError):
            self.handler.execute_event(OrderCancelEvent({"orderID": "7"}))


if __name__ == "__main__":
    unittest.main()
