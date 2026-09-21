"""
Tests for the Interactive Brokers provider: lib/ib.py, data/ib.py,
execution/ib.py.

IB is the odd one out in this project and the tests are shaped around the two
things that make it odd.

The first is that there is nothing to log into. The Web API is served by a
gateway the operator runs and authenticates in a browser, so the interesting
behaviour is the refusal: a gateway with no session raises a named exception
rather than producing a run of 401s, and the message says what a person has
to go and do.

The second is that a bracket is three orders. The stop and the target are
separate children with ids of their own, which is more to get wrong on the
way out - and, on the way back, the one thing eToro and IG cannot do: the
child that filled *is* the leg that closed the trade. Several tests here
exist to pin that the close carries a reason the broker stated and does not
claim to have inferred it.

No network: every handler takes an `api` argument, and the client tests
replace the `requests` module.
"""

import datetime
import types
import unittest

from parity_deriva.data.ib import IBCandles, IBRates, IBTransactions
from parity_deriva.event.event import (ClientOrderEvent, OrderCancelEvent,
                                       OrderEvent)
from parity_deriva.execution.ib import IBExecutionHandler
from parity_deriva.lib.closereason import STOP_LOSS, TAKE_PROFIT
from parity_deriva.lib.ib import (IBAPI, IBError, IBNotAuthenticated, bar,
                                  barTime, clientOrderId, conid, duration,
                                  instrumentName, pricePrecision, startTime,
                                  utcnow)
from parity_deriva.tests.helpers import FakeRequests, FakeResponse, Recorder

T0 = datetime.datetime(2018, 1, 15, 10, 0, 0)


def epoch_ms(when):
    """A datetime as the milliseconds since the epoch the history route sends."""
    return int((when - datetime.datetime(1970, 1, 1)).total_seconds() * 1000)


def settings_stub(**over):
    """
    Settings whose unset attributes are genuinely absent.

    Deliberately not a MagicMock: getattr() succeeds for every name on one of
    those, so IB_SPREAD would come back as a Mock instead of None and every
    'unset means off' assertion here would pass for the wrong reason.
    """
    stub = types.SimpleNamespace(
        DOMAIN='practice',
        IB_GATEWAY='localhost:5000',
        IB_ACCOUNT_ID='DU1234567',
        IB_VERIFY_TLS=False,
        IB_INSTRUMENTS={
            'EUR_USD': {'conid': 12345, 'symbol': 'EUR', 'secType': 'CASH',
                        'precision': 5},
            'DE30_EUR': {'conid': 67890, 'symbol': 'DAX', 'secType': 'FUT'},
        },
        IB_SPREAD=None,
        IB_OUTSIDE_RTH=True,
        IB_CONFIRM_ORDER_QUESTIONS=True,
        IB_REFUSE_QUESTIONS=('exceeds',),
        IB_POLL_SECONDS=0,
        IB_TICKLE_SECONDS=60,
        IB_ENFORCE_EXPIRY=True,
        INSTRUMENT_PRECISION={'EUR_USD': 5, 'DE30_EUR': 1},
        DEFAULT_PRICE_PRECISION=5,
    )
    for key, value in over.items():
        setattr(stub, key, value)
    return stub


class FakeAPI(object):
    """
    Stands in for IBAPI. Answers are queued per route key, and every call is
    recorded so a test can assert on what was asked for as well as on what was
    done with the reply.
    """

    def __init__(self, account='DU1234567'):
        self.account = account
        self.gateway = 'localhost:5000'
        self.answers = {}
        self.calls = []
        self.placed = []
        self.place_answer = []

    def queue(self, key, status=200, payload=None):
        self.answers.setdefault(key, []).append((status, payload))
        return self

    def respond(self, key, status=200, payload=None):
        """Replace whatever was queued, for a poll that asks the same route
        again and should see the newer answer rather than the older one."""
        self.answers[key] = [(status, payload)]
        return self

    def _call(self, method, key, parts, params, body):
        self.calls.append({'method': method, 'key': key, 'parts': tuple(parts),
                           'params': params, 'body': body})
        queued = self.answers.get(key)
        if not queued:
            return 200, None
        if len(queued) == 1:
            return queued[0]
        return queued.pop(0)

    def get(self, key, parts=(), params=None):
        return self._call('GET', key, parts, params, None)

    def post(self, key, parts=(), body=None):
        return self._call('POST', key, parts, None, body)

    def delete(self, key, parts=()):
        return self._call('DELETE', key, parts, None, None)

    def place(self, orders, confirm=True, refuse=()):
        self.placed.append({'orders': orders, 'confirm': confirm,
                            'refuse': tuple(refuse)})
        return self.place_answer

    def of(self, key):
        return [c for c in self.calls if c['key'] == key]


def points(rows, factor=1):
    return {'symbol': 'EUR', 'priceFactor': factor, 'points': rows}


def point(when=T0, o=1.1600, h=1.1610, l=1.1590, c=1.1605, v=7):
    return {'t': epoch_ms(when), 'o': o, 'h': h, 'l': l, 'c': c, 'v': v}


class NamingTest(unittest.TestCase):

    def setUp(self):
        self.setup = settings_stub()

    def test_conid_comes_from_settings(self):
        self.assertEqual(conid('EUR_USD', self.setup), 12345)

    def test_unmapped_instrument_names_the_script(self):
        """
        'EUR' names a cash pair, several futures and a fund or two. Dealing
        whatever a search returned first is unbounded.
        """
        with self.assertRaises(IBError) as caught:
            conid('GBP_JPY', self.setup)
        self.assertIn('ib_instruments.py', str(caught.exception))

    def test_entry_without_a_conid_is_refused(self):
        setup = settings_stub(IB_INSTRUMENTS={'EUR_USD': {'symbol': 'EUR'}})
        with self.assertRaises(IBError):
            conid('EUR_USD', setup)

    def test_instrument_name_reverses_the_mapping(self):
        self.assertEqual(instrumentName(12345, self.setup), 'EUR_USD')
        self.assertIsNone(instrumentName(999, self.setup))

    def test_precision_prefers_the_instrument_entry(self):
        self.assertEqual(pricePrecision('EUR_USD', self.setup), 5)
        self.assertEqual(pricePrecision('DE30_EUR', self.setup), 1)


class BarTest(unittest.TestCase):

    def test_translates(self):
        self.assertEqual(bar('M5'), '5min')
        self.assertEqual(bar('H1'), '1h')
        self.assertEqual(bar('D'), '1d')

    def test_refuses_what_the_route_does_not_serve(self):
        """This route's smallest bar is a minute."""
        with self.assertRaises(IBError):
            bar('S5')

    def test_duration_rounds_up(self):
        """
        Asking for more than the range and filtering the surplus costs one
        request; asking for less silently loses the oldest bars of the window
        somebody asked for.
        """
        self.assertEqual(duration(datetime.timedelta(minutes=90)), '2h')
        self.assertEqual(duration(datetime.timedelta(hours=30)), '2d')
        self.assertEqual(duration(datetime.timedelta(seconds=30)), '1min')

    def test_bar_time_reads_milliseconds(self):
        self.assertEqual(barTime(epoch_ms(T0)), T0)

    def test_bar_time_also_reads_seconds(self):
        """
        Seconds interpreted as milliseconds land in 1970 and would be
        discarded as too old, silently.
        """
        seconds = (T0 - datetime.datetime(1970, 1, 1)).total_seconds()
        self.assertEqual(barTime(seconds), T0)

    def test_start_time_is_ibs_format(self):
        self.assertEqual(startTime(T0), '20180115-10:00:00')

    def test_utcnow_is_naive_utc(self):
        self.assertIsNone(utcnow().tzinfo)


class ClientOrderIdTest(unittest.TestCase):

    def test_is_a_function_of_the_signal(self):
        key = 'AG01:EUR_USD:H1:20180115T010000'
        self.assertEqual(clientOrderId(key, 'BUY', 1.16),
                         clientOrderId(key, 'BUY', 1.16))

    def test_differs_by_direction_and_level(self):
        key = 'AG01:EUR_USD:H1:20180115T010000'
        self.assertNotEqual(clientOrderId(key, 'BUY', 1.16),
                            clientOrderId(key, 'SELL', 1.16))

    def test_refuses_to_invent_one(self):
        with self.assertRaises(IBError):
            clientOrderId()


class APITest(unittest.TestCase):

    def test_refuses_to_build_without_an_account(self):
        """
        One login can hold a paper account and a live one. They are not
        interchangeable and picking one is not this code's to do.
        """
        with self.assertRaises(IBError) as caught:
            IBAPI(setup=settings_stub(IB_ACCOUNT_ID=''))
        self.assertIn('IB_ACCOUNT_ID', str(caught.exception))

    def test_urls_are_built_off_the_gateway(self):
        api = IBAPI(setup=settings_stub())
        self.assertEqual(api.url('orders'),
                         'https://localhost:5000/v1/api/iserver/account/orders')

    def test_unknown_route_raises(self):
        with self.assertRaises(IBError):
            IBAPI(setup=settings_stub()).url('teleport')

    def test_an_unauthenticated_gateway_is_its_own_failure(self):
        """
        No amount of polling logs a human into a browser, so this is named,
        raised, and says what to do - not turned into a run of 401s.
        """
        import parity_deriva.lib.ib as lib_ib
        fake = FakeRequests(FakeResponse(payload={'authenticated': False,
                                                  'connected': False}))
        real, lib_ib.requests = lib_ib.requests, fake
        try:
            api = IBAPI(setup=settings_stub())
            with self.assertRaises(IBNotAuthenticated) as caught:
                api.prepare()
            self.assertIn('localhost:5000', str(caught.exception))
        finally:
            lib_ib.requests = real

    def test_an_account_the_session_does_not_hold_is_refused(self):
        """
        Discovering this from a rejection would mean discovering it after an
        order was sent somewhere.
        """
        import parity_deriva.lib.ib as lib_ib
        fake = FakeRequests(
            FakeResponse(payload={'authenticated': True}),
            FakeResponse(payload={'accounts': ['DU9999999']}))
        real, lib_ib.requests = lib_ib.requests, fake
        try:
            api = IBAPI(setup=settings_stub())
            with self.assertRaises(IBError) as caught:
                api.prepare()
            self.assertIn('DU9999999', str(caught.exception))
        finally:
            lib_ib.requests = real

    def test_the_precondition_is_sent_once(self):
        """
        IB documents /iserver/accounts as a precondition; a request per poll
        would be a precondition turned into traffic.
        """
        import parity_deriva.lib.ib as lib_ib
        fake = FakeRequests(
            FakeResponse(payload={'authenticated': True}),
            FakeResponse(payload={'accounts': ['DU1234567']}),
            FakeResponse(payload={}))
        real, lib_ib.requests = lib_ib.requests, fake
        try:
            api = IBAPI(setup=settings_stub())
            self.assertTrue(api.prepare())
            self.assertTrue(api.prepare())
            accounts = [s for s in fake.sent if s['url'].endswith('/iserver/accounts')]
            self.assertEqual(len(accounts), 1)
        finally:
            lib_ib.requests = real


class PlaceTest(unittest.TestCase):
    """
    The question-and-answer exchange, which has no counterpart at the other
    brokers.
    """

    def api(self, *responses):
        import parity_deriva.lib.ib as lib_ib
        fake = FakeRequests(
            FakeResponse(payload={'authenticated': True}),
            FakeResponse(payload={'accounts': ['DU1234567']}),
            FakeResponse(payload={}),            # tickle
            *responses)
        self.real = lib_ib.requests
        lib_ib.requests = fake
        self.addCleanup(self.restore)
        self.fake = fake
        return IBAPI(setup=settings_stub())

    def restore(self):
        import parity_deriva.lib.ib as lib_ib
        lib_ib.requests = self.real

    def test_a_question_is_answered_and_the_order_placed(self):
        api = self.api(
            FakeResponse(payload=[{'id': 'q1', 'message': ['Confirm order?']}]),
            FakeResponse(payload=[{'order_id': '111',
                                   'order_status': 'PreSubmitted'}]))
        answer = api.place([{'conid': 1}])
        self.assertEqual(answer[0]['order_id'], '111')

    def test_a_refused_question_stops_the_submission(self):
        """
        Anything whose answer should be a person's is not answered here, and
        then no order exists.
        """
        api = self.api(
            FakeResponse(payload=[{'id': 'q1',
                                   'message': ['Order size exceeds limit']}]))
        answer = api.place([{'conid': 1}], refuse=('exceeds',))
        self.assertEqual(answer[0]['id'], 'q1')
        self.assertNotIn('order_id', answer[0])

    def test_confirmation_can_be_turned_off_entirely(self):
        api = self.api(
            FakeResponse(payload=[{'id': 'q1', 'message': ['Confirm order?']}]))
        answer = api.place([{'conid': 1}], confirm=False)
        self.assertEqual(answer[0]['id'], 'q1')

    def test_a_question_that_never_ends_is_given_up_on(self):
        """Confirming forever is how an order gets placed by accident."""
        api = self.api(*[FakeResponse(payload=[{'id': 'q%d' % i,
                                                'message': ['again?']}])
                         for i in range(12)])
        answer = api.place([{'conid': 1}])
        self.assertIn('id', answer[0])


class CandlesTest(unittest.TestCase):

    def setUp(self):
        self.setup = settings_stub()
        self.api = FakeAPI()
        self.recorder = Recorder()

    def candles(self, **args):
        args.setdefault('setup', self.setup)
        args.setdefault('pairs', ['EUR_USD'])
        args.setdefault('granularity', 'M1')
        args.setdefault('api', self.api)
        handler = IBCandles(**args)
        handler.set_queue(self.recorder)
        return handler

    def test_one_series_means_mid_only(self):
        """
        IB's history route serves a single OHLC. Without IB_SPREAD there is no
        bid and no ask, and the provider declines bid_ask_candles.
        """
        self.api.queue('history', 200, points([point(T0)]))
        handler = self.candles()
        handler.poll(pair='EUR_USD', now=T0 + datetime.timedelta(minutes=2))
        candle = self.recorder.of('CANDLE')[0]
        self.assertEqual(candle.mid['c'], 1.1605)
        self.assertIsNone(candle.bid)

    def test_a_configured_spread_is_applied_half_either_side(self):
        setup = settings_stub(IB_SPREAD=0.0002)
        api = FakeAPI()
        api.queue('history', 200, points([point(T0)]))
        handler = self.candles(setup=setup, api=api)
        handler.poll(pair='EUR_USD', now=T0 + datetime.timedelta(minutes=2))
        candle = self.recorder.of('CANDLE')[0]
        self.assertAlmostEqual(candle.ask['c'] - candle.bid['c'], 0.0002,
                               places=6)

    def test_a_forming_bar_is_not_emitted(self):
        self.api.queue('history', 200, points([point(T0)]))
        handler = self.candles()
        handler.poll(pair='EUR_USD', now=T0 + datetime.timedelta(seconds=30))
        self.assertEqual(self.recorder.of('CANDLE'), [])

    def test_price_factor_is_applied(self):
        """
        A factor silently ignored is every level wrong by a power of ten.
        """
        self.api.queue('history', 200, points([point(T0, o=11600, h=11610,
                                                     l=11590, c=11605)],
                                              factor=10000))
        handler = self.candles()
        handler.poll(pair='EUR_USD', now=T0 + datetime.timedelta(minutes=2))
        candle = self.recorder.of('CANDLE')[0]
        self.assertAlmostEqual(candle.mid['c'], 1.1605, places=6)

    def test_offline_asks_by_start_and_duration(self):
        """IB takes a period rather than an end, so a range becomes 'how much'."""
        handler = self.candles(dtfrom=T0, dtto=T0 + datetime.timedelta(hours=2))
        params = handler.params('EUR_USD')
        self.assertEqual(params['startTime'], '20180115-10:00:00')
        self.assertEqual(params['period'], '2h')

    def test_the_local_window_filter_still_applies(self):
        """
        The filter is what makes the request safe: if IB reads startTime
        differently than this code does, the result is fewer bars, never bars
        from the wrong window.
        """
        self.api.queue('history', 200, points([
            point(T0 - datetime.timedelta(hours=5)), point(T0)]))
        handler = self.candles(dtfrom=T0 - datetime.timedelta(minutes=1),
                               dtto=T0 + datetime.timedelta(minutes=1))
        handler.poll(pair='EUR_USD', now=T0 + datetime.timedelta(minutes=2))
        candles = self.recorder.of('CANDLE')
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].time, T0)


class RatesTest(unittest.TestCase):

    def setUp(self):
        self.setup = settings_stub()
        self.api = FakeAPI()
        self.recorder = Recorder()

    def rates(self):
        handler = IBRates(setup=self.setup, pairs=['EUR_USD'], api=self.api)
        handler.set_queue(self.recorder)
        return handler

    def test_publishes_both_sides(self):
        """This is where IB does quote a bid and an ask."""
        self.api.queue('snapshot', 200, [{'conid': 12345, '84': '1.1599',
                                          '86': '1.1601'}])
        self.rates().poll()
        tick = self.recorder.of('TICK')[0]
        self.assertAlmostEqual(tick.ask - tick.bid, 0.0002, places=6)

    def test_the_subscription_warm_up_is_not_an_error(self):
        """
        The first request for a contract only starts the subscription and
        often answers without the fields asked for.
        """
        self.api.queue('snapshot', 200, [{'conid': 12345}])
        handler = self.rates()
        handler.poll()
        self.assertEqual(self.recorder.of('TICK'), [])
        self.assertEqual(self.recorder.statuses(), [])


class ExecutionTest(unittest.TestCase):

    def setUp(self):
        self.setup = settings_stub()
        self.api = FakeAPI()
        self.recorder = Recorder()
        self.handler = IBExecutionHandler(setup=self.setup, api=self.api)
        self.handler.set_queue(self.recorder)

    def order(self, **over):
        data = {'instrument': 'EUR_USD', 'units': 1, 'price': 1.1650,
                'orderType': 'STOP', 'stopLoss': 1.1600, 'takeProfit': 1.1700,
                'gtdTime': datetime.datetime(2018, 1, 15, 23, 59, 0)}
        data.update(over)
        event = OrderEvent(data)
        event.signalNumber = 'AG01:EUR_USD:H1:20180115T010000'
        return event

    def test_a_bracket_is_three_orders(self):
        """
        IB has no stop-and-limit attached to an entry: they are children of
        their own, which is what later lets the broker name the leg.
        """
        orders = self.handler.orders(self.order())
        self.assertEqual(len(orders), 3)
        self.assertEqual(orders[1]['parentId'], orders[0]['cOID'])
        self.assertEqual(orders[2]['parentId'], orders[0]['cOID'])

    def test_the_children_are_one_oca_group(self):
        """
        Without it a closed trade leaves its other leg resting, and the next
        fill opens a position nobody signalled.
        """
        orders = self.handler.orders(self.order())
        self.assertTrue(orders[1]['isSingleGroup'])
        self.assertTrue(orders[2]['isSingleGroup'])

    def test_the_children_face_the_other_way(self):
        orders = self.handler.orders(self.order(units=1))
        self.assertEqual(orders[0]['side'], 'BUY')
        self.assertEqual(orders[1]['side'], 'SELL')
        self.assertEqual(orders[2]['side'], 'SELL')

    def test_a_stops_level_is_auxprice(self):
        """
        IB reads 'price' on a stop order as the limit of a stop-limit, which
        is a different order.
        """
        orders = self.handler.orders(self.order(orderType='STOP'))
        self.assertEqual(orders[0]['orderType'], 'STP')
        self.assertEqual(orders[0]['auxPrice'], 1.1650)
        self.assertNotIn('price', orders[0])

    def test_a_limits_level_is_price(self):
        orders = self.handler.orders(self.order(orderType='LIMIT'))
        self.assertEqual(orders[0]['orderType'], 'LMT')
        self.assertEqual(orders[0]['price'], 1.1650)
        self.assertNotIn('auxPrice', orders[0])

    def test_the_stop_child_is_a_stop_and_the_target_a_limit(self):
        orders = self.handler.orders(self.order())
        self.assertEqual(orders[1]['orderType'], 'STP')
        self.assertEqual(orders[1]['auxPrice'], 1.1600)
        self.assertEqual(orders[2]['orderType'], 'LMT')
        self.assertEqual(orders[2]['price'], 1.1700)

    def test_a_market_order_carries_no_level(self):
        orders = self.handler.orders(self.order(orderType='MARKET'))
        self.assertEqual(orders[0]['orderType'], 'MKT')
        self.assertNotIn('price', orders[0])
        self.assertNotIn('auxPrice', orders[0])

    def test_an_unknown_order_type_is_refused(self):
        with self.assertRaises(IBError):
            self.handler.orders(self.order(orderType='TRAILING'))

    def test_zero_units_is_refused(self):
        with self.assertRaises(IBError):
            self.handler.orders(self.order(units=0))

    def test_the_child_ids_travel_with_the_acknowledgement(self):
        """
        They are what data/ib.py needs to say which leg closed a trade, and
        they are derived rather than IB's, so they survive a restart.
        """
        self.api.place_answer = [{'order_id': '111',
                                  'order_status': 'PreSubmitted'}]
        coe = self.handler.placeOrder(self.order())
        self.assertIsNotNone(coe)
        self.assertIsNotNone(coe.stopChildId)
        self.assertIsNotNone(coe.targetChildId)
        self.assertNotEqual(coe.stopChildId, coe.targetChildId)

    def test_a_refusal_is_published_not_only_logged(self):
        """
        The money manager refuses every new signal while it believes an order
        is outstanding, so a rejection that never reached it would stop the
        strategy for good.
        """
        self.api.place_answer = [{'error': 'no market data permission'}]
        self.assertIsNone(self.handler.placeOrder(self.order()))
        rejects = [e for e in self.recorder.events
                   if getattr(e, 'type', None) == 'ORDER_REJECT']
        self.assertEqual(len(rejects), 1)
        self.assertIn('permission', rejects[0].rejectReason)

    def test_an_unanswered_question_is_a_rejection_too(self):
        self.api.place_answer = [{'id': 'q1', 'message': ['size exceeds cap']}]
        self.assertIsNone(self.handler.placeOrder(self.order()))
        rejects = [e for e in self.recorder.events
                   if getattr(e, 'type', None) == 'ORDER_REJECT']
        self.assertEqual(len(rejects), 1)

    def test_the_refusal_list_reaches_the_client(self):
        self.api.place_answer = [{'order_id': '1'}]
        self.handler.placeOrder(self.order())
        self.assertEqual(self.api.placed[0]['refuse'], ('exceeds',))

    def test_cancel_without_an_order_id_is_not_sent(self):
        """
        IB deletes by its own order id on a route that also takes an account,
        so a delete named by the wrong identifier would be somebody else's
        order.
        """
        event = OrderCancelEvent({'clientOrderId': 'PDabc'})
        self.assertIsNone(self.handler.cancelOrder(event))
        self.assertEqual(self.api.of('cancel_order'), [])

    def test_cancel_with_an_order_id_is_sent(self):
        self.handler.cancelOrder(OrderCancelEvent({'orderID': 111}))
        self.assertEqual(self.api.of('cancel_order')[0]['parts'],
                         ('DU1234567', 111))


class TransactionsTest(unittest.TestCase):

    def setUp(self):
        self.setup = settings_stub()
        self.api = FakeAPI()
        self.recorder = Recorder()
        self.handler = IBTransactions(setup=self.setup, pairs=['EUR_USD'],
                                      api=self.api)
        self.handler.set_queue(self.recorder)

    def acknowledge(self, **over):
        data = {'id': 111, 'clientOrderId': 'PDparent', 'instrument': 'EUR_USD',
                'price': 1.1650, 'units': 1, 'orderType': 'STOP',
                'stopLoss': 1.1600, 'takeProfit': 1.1700,
                'stopChildId': 'PDstop', 'targetChildId': 'PDtarget'}
        data.update(over)
        event = ClientOrderEvent(data)
        event.signalNumber = 'AG01:EUR_USD:H1:20180115T010000'
        self.handler.execute_event(event)
        return event

    def book(self, **states):
        """The order list, keyed by our own reference the way IB returns it."""
        return {'orders': [
            {'order_ref': ref, 'status': state, 'orderId': 900 + i,
             'conid': 12345, 'origOrderType': 'STP', 'price': 1.1650}
            for i, (ref, state) in enumerate(sorted(states.items()))]}

    def fills(self):
        return [e for e in self.recorder.events
                if getattr(e, 'type', None) == 'ORDER_FILL']

    def test_it_learns_of_orders_from_the_bus(self):
        self.acknowledge()
        self.assertIn('PDparent', self.handler.groups)
        self.assertIn('PDstop', self.handler.children)
        self.assertIn('PDtarget', self.handler.children)

    def test_a_filled_entry_becomes_a_fill(self):
        self.acknowledge()
        self.api.queue('orders', 200, self.book(PDparent='Filled'))
        self.api.queue('order_status', 200, {'average_price': '1.16512'})
        self.handler.poll()
        fills = self.fills()
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].price, 1.16512)

    def test_the_average_price_is_read_rather_than_the_placed_level(self):
        """
        The order list's own price is where the order was placed. Reporting
        that as the fill would hide every bit of slippage - which is one of
        the two things the parity monitor exists to measure.
        """
        self.acknowledge()
        self.api.queue('orders', 200, self.book(PDparent='Filled'))
        self.api.queue('order_status', 200, {'average_price': '1.16600'})
        self.handler.poll()
        self.assertEqual(self.fills()[0].price, 1.166)

    def test_a_filled_stop_child_names_the_leg(self):
        """
        This is what IB has that eToro and IG do not: the broker filled this
        particular order and this project knows which leg it placed it as.
        """
        self.acknowledge()
        self.api.queue('orders', 200, self.book(PDparent='Filled'))
        self.api.queue('order_status', 200, {'average_price': '1.16512'})
        self.handler.poll()
        self.api.respond('orders', 200, self.book(PDparent='Filled',
                                                  PDstop='Filled'))
        self.api.respond('order_status', 200, {'average_price': '1.1600'})
        self.handler.poll()
        closes = [f for f in self.fills() if getattr(f, 'reasonInferred', None)
                  is not None]
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0].reason, STOP_LOSS)

    def test_a_close_does_not_claim_to_have_been_inferred(self):
        """
        Nothing here goes near lib/closereason.py, so the event must not say
        it did - the parity monitor reads that flag.
        """
        self.acknowledge()
        self.api.queue('orders', 200, self.book(PDparent='Filled'))
        self.api.queue('order_status', 200, {'average_price': '1.16512'})
        self.handler.poll()
        self.api.respond('orders', 200, self.book(PDparent='Filled',
                                                  PDtarget='Filled'))
        self.api.respond('order_status', 200, {'average_price': '1.1700'})
        self.handler.poll()
        close = [f for f in self.fills()
                 if getattr(f, 'reasonInferred', None) is False][0]
        self.assertEqual(close.reason, TAKE_PROFIT)
        self.assertFalse(close.reasonInferred)

    def test_a_rejection_is_published(self):
        self.acknowledge()
        book = self.book(PDparent='Rejected')
        book['orders'][0]['order_cancellation_by_system_reason'] = 'no margin'
        self.api.queue('orders', 200, book)
        self.handler.poll()
        rejects = [e for e in self.recorder.events
                   if getattr(e, 'type', None) == 'ORDER_REJECT']
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0].rejectReason, 'no margin')
        self.assertNotIn('PDparent', self.handler.groups)

    def test_a_resting_order_past_its_expiry_is_cancelled_here(self):
        """
        The Web API has DAY and GTC and no expiry instant, so a bracket meant
        to die at the end of the day is ours to cancel.
        """
        self.acknowledge(gtdTime=datetime.datetime(2018, 1, 15, 23, 59, 0))
        self.api.queue('orders', 200, self.book(PDparent='Submitted'))
        self.handler.poll(now=datetime.datetime(2018, 1, 16, 0, 1, 0))
        self.assertEqual(len(self.recorder.of('ORDERCANCEL')), 1)
        self.assertNotIn('PDparent', self.handler.groups)

    def test_an_order_before_its_expiry_is_left_alone(self):
        self.acknowledge(gtdTime=datetime.datetime(2018, 1, 15, 23, 59, 0))
        self.api.queue('orders', 200, self.book(PDparent='Submitted'))
        self.handler.poll(now=datetime.datetime(2018, 1, 15, 12, 0, 0))
        self.assertEqual(self.recorder.of('ORDERCANCEL'), [])

    def test_orders_nobody_acknowledged_are_ignored(self):
        """
        Something dealt from TWS is not a signal this stack produced. The
        parity monitor counts it unpaired, which is the correct reading.
        """
        self.acknowledge()
        book = self.book(PDparent='Submitted')
        book['orders'].append({'order_ref': 'SOMETHINGELSE', 'status': 'Filled',
                               'orderId': 555})
        self.api.queue('orders', 200, book)
        self.handler.poll()
        self.assertEqual(self.fills(), [])


if __name__ == '__main__':
    unittest.main()
