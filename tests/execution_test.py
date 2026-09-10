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

from parity_deriva.event.event import OrderEvent, OrderCancelEvent, SignalEvent
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

    def test_rejected_order_emits_nothing(self):
        FakeHTTPSConnection.reset(json.dumps(ORDER_REJECTED).encode("utf-8"))
        self.handler.execute_event(self.order())
        self.assertEqual(self.sink.events, [])


class TestEventRouting(ExecutionCase):

    def test_only_order_and_ordercancel_are_acted_on(self):
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
