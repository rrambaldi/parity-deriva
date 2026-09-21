"""
Tests for the IG provider: lib/ig.py, data/ig.py, execution/ig.py.

IG is the closest of the three non-OANDA brokers to OANDA, and the theme here
is that "close" is not "the same". What is pinned is where the two genuinely
differ and where the code therefore has to decide something:

* a session rather than a token, opened lazily and re-opened when refused
* a Version header that belongs to the route and not to the API
* two endpoints for what this project calls one kind of order
* a reply that is a reference, not an outcome
* a close whose leg is inferred, and said to be inferred

The rest is translation, pinned field by field because the failure mode is
not a crash: an order with the level in the wrong field, or a candle whose
bid came from the ask, is a trade at a price nobody chose.

No network: every handler takes an `api` argument, and the client tests
replace the `requests` module.
"""

import datetime
import types
import unittest

from parity_deriva.data.ig import (IGCandles, IGRates, IGTransactions,
                                   price, profitAndLoss)
from parity_deriva.event.event import (ClientOrderEvent, OrderCancelEvent,
                                       OrderEvent)
from parity_deriva.execution.ig import IGExecutionHandler
from parity_deriva.lib.closereason import STOP_LOSS, TAKE_PROFIT, UNKNOWN
from parity_deriva.lib.ig import (IGAPI, IGError, currency, dealReference,
                                  dealTime, epic, expiry, goodTillDate,
                                  instrumentName, pricePrecision, resolution,
                                  scale, utcnow)
from parity_deriva.tests.helpers import FakeRequests, FakeResponse, Recorder

T0 = datetime.datetime(2018, 1, 15, 10, 0, 0)


def settings_stub(**over):
    """
    Settings whose unset attributes are genuinely absent.

    Deliberately not a MagicMock: getattr() succeeds for every name on one of
    those, so IG_INSTRUMENTS would come back as a Mock instead of raising and
    every 'unmapped instrument is refused' assertion here would pass for the
    wrong reason.
    """
    stub = types.SimpleNamespace(
        DOMAIN='practice',
        IG_API_DOMAIN='',
        IG_API_KEY='TESTKEY',
        IG_IDENTIFIER='someone',
        IG_PASSWORD='secret',
        IG_ACCOUNT_ID='ABC12',
        IG_SESSION_VERSION=2,
        IG_INSTRUMENTS={
            'EUR_USD': {'epic': 'CS.D.EURUSD.MINI.IP', 'expiry': '-',
                        'currency': 'USD', 'precision': 5},
            'DE30_EUR': {'epic': 'IX.D.DAX.IFMM.IP'},
        },
        IG_CURRENCY=None,
        IG_GUARANTEED_STOP=False,
        IG_ENFORCE_EXPIRY=False,
        IG_POLL_SECONDS=0,
        IG_CONFIRM_ATTEMPTS=3,
        IG_VERIFY_TLS=False,
        BASE_CURRENCY='EUR',
        INSTRUMENT_PRECISION={'EUR_USD': 5, 'DE30_EUR': 1},
        DEFAULT_PRICE_PRECISION=5,
    )
    for key, value in over.items():
        setattr(stub, key, value)
    return stub


class FakeAPI(object):
    """
    Stands in for IGAPI. Answers are queued per route key, and every call is
    recorded so a test can assert on what was asked for as well as on what was
    done with the reply.
    """

    def __init__(self, demo=True, setup=None):
        self.demo = demo
        self.host = 'demo-api.ig.test'
        self.setup = setup
        self.answers = {}
        self.calls = []
        self.noted = []

    def queue(self, key, status=200, payload=None):
        self.answers.setdefault(key, []).append((status, payload))
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

    def delete(self, key, parts=(), body=None):
        return self._call('DELETE', key, parts, None, body)

    def noteAllowance(self, payload):
        self.noted.append(payload)
        return True

    def of(self, key):
        return [c for c in self.calls if c['key'] == key]


def price_row(when=T0, o=1.1600, h=1.1610, l=1.1590, c=1.1605, spread=0.0002,
              volume=7):
    """One row of GET /prices, with the bid/ask split IG actually serves."""
    def pair(mid):
        return {'bid': round(mid - spread / 2, 6),
                'ask': round(mid + spread / 2, 6),
                'lastTraded': None}
    return {
        'snapshotTime': when.strftime('%Y/%m/%d %H:%M:%S'),
        'snapshotTimeUTC': when.strftime('%Y-%m-%dT%H:%M:%S'),
        'openPrice': pair(o), 'highPrice': pair(h),
        'lowPrice': pair(l), 'closePrice': pair(c),
        'lastTradedVolume': volume,
    }


def prices_payload(rows, remaining=9000, total=10000):
    return {'prices': rows, 'instrumentType': 'CURRENCIES',
            'metadata': {'allowance': {'remainingAllowance': remaining,
                                       'totalAllowance': total,
                                       'allowanceExpiry': 604800}}}


class NamingTest(unittest.TestCase):

    def setUp(self):
        self.setup = settings_stub()

    def test_epic_comes_from_settings(self):
        self.assertEqual(epic('EUR_USD', self.setup), 'CS.D.EURUSD.MINI.IP')

    def test_unmapped_instrument_names_the_script(self):
        """
        Dealing on whatever epic a search returned first is unbounded, so an
        instrument nobody mapped is a refusal with the remedy in it.
        """
        with self.assertRaises(IGError) as caught:
            epic('GBP_JPY', self.setup)
        self.assertIn('ig_instruments.py', str(caught.exception))

    def test_entry_without_an_epic_is_refused(self):
        setup = settings_stub(IG_INSTRUMENTS={'EUR_USD': {'currency': 'USD'}})
        with self.assertRaises(IGError):
            epic('EUR_USD', setup)

    def test_instrument_name_reverses_the_mapping(self):
        self.assertEqual(instrumentName('CS.D.EURUSD.MINI.IP', self.setup),
                         'EUR_USD')
        self.assertIsNone(instrumentName('CS.D.NOPE.IP', self.setup))

    def test_expiry_defaults_to_the_non_expiring_contract(self):
        """'-' is IG's own value for a market that does not expire."""
        self.assertEqual(expiry('DE30_EUR', self.setup), '-')
        self.assertEqual(expiry('EUR_USD', self.setup), '-')

    def test_currency_falls_back_rather_than_being_guessed(self):
        """
        'EUR_USD' can be dealt in either of its two currencies and the choice
        is the account's, so nothing reads it off the instrument's name.
        """
        self.assertEqual(currency('EUR_USD', self.setup), 'USD')
        self.assertEqual(currency('DE30_EUR', self.setup), 'EUR')
        setup = settings_stub(IG_CURRENCY='GBP')
        self.assertEqual(currency('DE30_EUR', setup), 'GBP')

    def test_scale_is_one_unless_configured(self):
        self.assertEqual(scale('EUR_USD', self.setup), 1.0)
        setup = settings_stub(IG_INSTRUMENTS={
            'EUR_USD': {'epic': 'X', 'scalingFactor': 10000}})
        self.assertEqual(scale('EUR_USD', setup), 10000.0)

    def test_precision_prefers_the_instrument_entry(self):
        self.assertEqual(pricePrecision('EUR_USD', self.setup), 5)
        self.assertEqual(pricePrecision('DE30_EUR', self.setup), 1)


class ResolutionTest(unittest.TestCase):

    def test_translates(self):
        self.assertEqual(resolution('M5'), 'MINUTE_5')
        self.assertEqual(resolution('H1'), 'HOUR')
        self.assertEqual(resolution('D'), 'DAY')

    def test_refuses_what_ig_does_not_serve(self):
        """
        Rounding 'S5' to SECOND would hand a strategy bars of a period it did
        not ask for, and every level derived from them would be wrong.
        """
        with self.assertRaises(IGError) as caught:
            resolution('S5')
        self.assertIn('S1', str(caught.exception))


class DealReferenceTest(unittest.TestCase):

    def test_is_a_function_of_the_signal(self):
        a = dealReference('AG01:EUR_USD:H1:20180115T010000', 'BUY', 1.16)
        b = dealReference('AG01:EUR_USD:H1:20180115T010000', 'BUY', 1.16)
        self.assertEqual(a, b)

    def test_differs_by_direction_and_level(self):
        """
        One signal produces two opposite orders, so the signal alone does not
        identify one of them.
        """
        key = 'AG01:EUR_USD:H1:20180115T010000'
        self.assertNotEqual(dealReference(key, 'BUY', 1.16),
                            dealReference(key, 'SELL', 1.16))
        self.assertNotEqual(dealReference(key, 'BUY', 1.16),
                            dealReference(key, 'BUY', 1.17))

    def test_fits_what_ig_accepts(self):
        """
        IG's pattern is [A-Za-z0-9_-]{1,30}, and the raw signal key is both
        too long and full of colons.
        """
        ref = dealReference('AG01:EUR_USD:H1:20180115T010000', 'BUY', 1.16)
        self.assertLessEqual(len(ref), 30)
        self.assertTrue(all(c.isalnum() or c in '_-' for c in ref))

    def test_refuses_to_invent_one(self):
        with self.assertRaises(IGError):
            dealReference()


class TimeTest(unittest.TestCase):

    def test_reads_the_three_spellings(self):
        want = datetime.datetime(2020, 9, 1, 10, 0, 0)
        self.assertEqual(dealTime('2020/09/01 10:00:00'), want)
        self.assertEqual(dealTime('2020-09-01T10:00:00'), want)
        self.assertEqual(dealTime('2020-09-01T10:00:00.000'), want)

    def test_offsets_are_honoured_then_dropped(self):
        """
        Everything downstream compares naive UTC datetimes; an aware one
        raises TypeError the first time it meets a naive one.
        """
        when = dealTime('2020-09-01T12:00:00+02:00')
        self.assertEqual(when, datetime.datetime(2020, 9, 1, 10, 0, 0))
        self.assertIsNone(when.tzinfo)

    def test_unreadable_becomes_the_epoch_rather_than_raising(self):
        """
        A timestamp this code cannot read is a row that gets filtered out as
        too old, which is recoverable; an exception in a polling loop is not.
        """
        self.assertEqual(dealTime('not a time'),
                         datetime.datetime(1970, 1, 1, 0, 0, 0))

    def test_utcnow_is_naive_utc(self):
        self.assertIsNone(utcnow().tzinfo)

    def test_good_till_date_is_igs_own_format(self):
        self.assertEqual(goodTillDate(datetime.datetime(2018, 1, 15, 23, 59, 0)),
                         '2018/01/15 23:59:00')


class APITest(unittest.TestCase):

    def test_refuses_to_build_without_a_key(self):
        with self.assertRaises(IGError) as caught:
            IGAPI(setup=settings_stub(IG_API_KEY=''))
        self.assertIn('IG_API_KEY', str(caught.exception))

    def test_refuses_to_build_without_credentials(self):
        """IG issues session tokens from a login; there is no token to paste."""
        with self.assertRaises(IGError):
            IGAPI(setup=settings_stub(IG_PASSWORD=''))

    def test_host_follows_the_account_type(self):
        self.assertEqual(IGAPI(setup=settings_stub()).host, 'demo-api.ig.com')
        self.assertEqual(IGAPI(setup=settings_stub(DOMAIN='real')).host,
                         'api.ig.com')

    def test_explicit_host_wins(self):
        api = IGAPI(setup=settings_stub(IG_API_DOMAIN='ig.example'))
        self.assertEqual(api.host, 'ig.example')

    def test_version_belongs_to_the_route(self):
        """
        /prices is version 3 and /confirms is version 1. Sending the wrong
        number gets a schema from another era.
        """
        api = IGAPI(setup=settings_stub())
        self.assertEqual(api.headers('prices')['Version'], '3')
        self.assertEqual(api.headers('confirm')['Version'], '1')
        self.assertEqual(api.headers('create_order')['Version'], '2')

    def test_session_version_is_two_or_three(self):
        with self.assertRaises(IGError):
            IGAPI(setup=settings_stub(IG_SESSION_VERSION=4))

    def test_login_keeps_the_header_tokens(self):
        """
        CST and X-SECURITY-TOKEN arrive as headers, not in the body, and a 200
        without them is not a session.
        """
        import parity_deriva.lib.ig as lib_ig
        resp = FakeResponse(payload={'currentAccountId': 'ABC12'})
        resp.headers = {'CST': 'cst-token', 'X-SECURITY-TOKEN': 'sec-token'}
        fake = FakeRequests(resp)
        real, lib_ig.requests = lib_ig.requests, fake
        try:
            api = IGAPI(setup=settings_stub())
            self.assertTrue(api.login())
            self.assertTrue(api.loggedIn())
            self.assertEqual(api.headers('prices')['CST'], 'cst-token')
            self.assertEqual(api.headers('prices')['X-SECURITY-TOKEN'],
                             'sec-token')
        finally:
            lib_ig.requests = real

    def test_login_without_tokens_is_a_failed_login(self):
        import parity_deriva.lib.ig as lib_ig
        fake = FakeRequests(FakeResponse(payload={'currentAccountId': 'ABC12'}))
        real, lib_ig.requests = lib_ig.requests, fake
        try:
            api = IGAPI(setup=settings_stub())
            self.assertFalse(api.login())
            self.assertFalse(api.loggedIn())
        finally:
            lib_ig.requests = real

    def test_oauth_expiry_is_read_not_assumed(self):
        """
        Version 3's access token is measured in seconds and the reply says how
        many. Assuming a figure would send a request with a dead token.
        """
        import parity_deriva.lib.ig as lib_ig
        fake = FakeRequests(FakeResponse(payload={
            'oauthToken': {'access_token': 'at', 'refresh_token': 'rt',
                           'expires_in': '60'},
            'accountId': 'ABC12'}))
        real, lib_ig.requests = lib_ig.requests, fake
        try:
            api = IGAPI(setup=settings_stub(IG_SESSION_VERSION=3))
            self.assertTrue(api.login())
            self.assertTrue(api.loggedIn())
            headers = api.headers('prices')
            self.assertEqual(headers['Authorization'], 'Bearer at')
            self.assertEqual(headers['IG-ACCOUNT-ID'], 'ABC12')
        finally:
            lib_ig.requests = real

    def test_allowance_is_recorded_from_what_ig_reports(self):
        """
        The weekly history budget is shared with everything using the key, so
        no local counter can track it.
        """
        api = IGAPI(setup=settings_stub())
        self.assertTrue(api.noteAllowance(prices_payload([], remaining=4200)))
        self.assertEqual(api.allowance.remaining, 4200)
        self.assertFalse(api.allowance.low())
        api.noteAllowance(prices_payload([], remaining=100))
        self.assertTrue(api.allowance.low())


class CandlesTest(unittest.TestCase):

    def setUp(self):
        self.setup = settings_stub()
        self.api = FakeAPI(setup=self.setup)
        self.recorder = Recorder()

    def candles(self, **args):
        args.setdefault('setup', self.setup)
        args.setdefault('pairs', ['EUR_USD'])
        args.setdefault('granularity', 'M1')
        args.setdefault('api', self.api)
        handler = IGCandles(**args)
        handler.set_queue(self.recorder)
        return handler

    def test_serves_bid_and_ask_without_a_spread_model(self):
        """
        This is the whole reason IG is different from eToro: the two sides are
        real numbers, not a constant applied to a mid.
        """
        self.api.queue('prices', 200, prices_payload([price_row(T0)]))
        handler = self.candles()
        handler.poll(pair='EUR_USD', now=T0 + datetime.timedelta(minutes=2))
        candles = self.recorder.of('CANDLE')
        self.assertEqual(len(candles), 1)
        self.assertAlmostEqual(candles[0].bid['o'], 1.1599, places=6)
        self.assertAlmostEqual(candles[0].ask['o'], 1.1601, places=6)

    def test_mid_is_the_average_of_the_two_sides(self):
        """IG serves no mid, so it is computed and that is stated."""
        self.api.queue('prices', 200, prices_payload([price_row(T0)]))
        handler = self.candles()
        handler.poll(pair='EUR_USD', now=T0 + datetime.timedelta(minutes=2))
        candle = self.recorder.of('CANDLE')[0]
        self.assertAlmostEqual(candle.mid['c'], 1.1605, places=6)

    def test_a_forming_bar_is_not_emitted(self):
        """
        IG sends no completeness flag and its newest row is the bar still
        forming. Emitting it would have a strategy signal on a high that is
        not yet the high.
        """
        self.api.queue('prices', 200, prices_payload([price_row(T0)]))
        handler = self.candles()
        handler.poll(pair='EUR_USD', now=T0 + datetime.timedelta(seconds=30))
        self.assertEqual(self.recorder.of('CANDLE'), [])

    def test_a_row_missing_a_side_is_skipped_not_half_filled(self):
        """
        A candle whose bid is a copy of its ask would have AG01 place a stop
        where no price was ever quoted.
        """
        row = price_row(T0)
        row['lowPrice']['bid'] = None
        self.api.queue('prices', 200, prices_payload([row]))
        handler = self.candles()
        handler.poll(pair='EUR_USD', now=T0 + datetime.timedelta(minutes=2))
        self.assertEqual(self.recorder.of('CANDLE'), [])

    def test_the_same_bar_is_not_sent_twice(self):
        self.api.queue('prices', 200, prices_payload([price_row(T0)]))
        handler = self.candles()
        later = T0 + datetime.timedelta(minutes=2)
        handler.poll(pair='EUR_USD', now=later)
        handler.poll(pair='EUR_USD', now=later)
        self.assertEqual(len(self.recorder.of('CANDLE')), 1)

    def test_live_asks_for_the_newest_bars_only(self):
        """
        Opening a session by replaying a batch of stale candles would have a
        strategy signal on a reversal from last Tuesday.
        """
        handler = self.candles()
        params = handler.params('EUR_USD')
        self.assertNotIn('from', params)
        self.assertEqual(params['max'], 2)

    def test_offline_asks_by_date(self):
        handler = self.candles(dtfrom=T0, dtto=T0 + datetime.timedelta(hours=1))
        params = handler.params('EUR_USD')
        self.assertEqual(params['from'], '2018-01-15T10:00:00')
        self.assertEqual(params['to'], '2018-01-15T11:00:00')

    def test_a_missing_epic_history_says_so_rather_than_retrying(self):
        self.api.queue('prices', 404, None)
        handler = self.candles()
        handler.poll(pair='EUR_USD')
        self.assertIn('ERROR', self.recorder.statuses())

    def test_the_allowance_is_read_off_every_reply(self):
        self.api.queue('prices', 200, prices_payload([price_row(T0)]))
        handler = self.candles()
        handler.poll(pair='EUR_USD', now=T0 + datetime.timedelta(minutes=2))
        self.assertEqual(len(self.api.noted), 1)

    def test_scaled_market_is_divided_back(self):
        setup = settings_stub(IG_INSTRUMENTS={
            'EUR_USD': {'epic': 'X', 'scalingFactor': 10000}})
        api = FakeAPI(setup=setup)
        api.queue('prices', 200, prices_payload([
            price_row(T0, o=11600, h=11610, l=11590, c=11605, spread=2)]))
        handler = self.candles(setup=setup, api=api)
        handler.poll(pair='EUR_USD', now=T0 + datetime.timedelta(minutes=2))
        candle = self.recorder.of('CANDLE')[0]
        self.assertAlmostEqual(candle.bid['o'], 1.1599, places=6)


class RatesTest(unittest.TestCase):

    def setUp(self):
        self.setup = settings_stub()
        self.api = FakeAPI(setup=self.setup)
        self.recorder = Recorder()

    def rates(self):
        handler = IGRates(setup=self.setup, pairs=['EUR_USD'], api=self.api)
        handler.set_queue(self.recorder)
        return handler

    def test_publishes_the_snapshot(self):
        self.api.queue('market', 200, {'snapshot': {
            'bid': 1.1599, 'offer': 1.1601, 'updateTime': '10:00:00',
            'marketStatus': 'TRADEABLE'}})
        self.rates().poll()
        ticks = self.recorder.of('TICK')
        self.assertEqual(len(ticks), 1)
        self.assertAlmostEqual(ticks[0].ask - ticks[0].bid, 0.0002, places=6)

    def test_a_shut_market_is_not_an_error(self):
        """IG nulls both sides outside market hours rather than holding the
        last quote, and that is the market being shut."""
        self.api.queue('market', 200, {'snapshot': {
            'bid': None, 'offer': None, 'marketStatus': 'CLOSED'}})
        self.rates().poll()
        self.assertEqual(self.recorder.of('TICK'), [])
        self.assertEqual(self.recorder.statuses(), [])


class ExecutionTest(unittest.TestCase):

    def setUp(self):
        self.setup = settings_stub()
        self.api = FakeAPI(setup=self.setup)
        self.recorder = Recorder()
        self.handler = IGExecutionHandler(setup=self.setup, api=self.api)
        self.handler.set_queue(self.recorder)

    def order(self, **over):
        data = {'instrument': 'EUR_USD', 'units': 1, 'price': 1.1650,
                'orderType': 'STOP', 'stopLoss': 1.1600, 'takeProfit': 1.1700,
                'gtdTime': datetime.datetime(2018, 1, 15, 23, 59, 0)}
        data.update(over)
        event = OrderEvent(data)
        event.signalNumber = 'AG01:EUR_USD:H1:20180115T010000'
        return event

    def test_a_market_order_goes_to_the_position_endpoint(self):
        route, body = self.handler.body(self.order(orderType='MARKET'))
        self.assertEqual(route, 'open_position')
        self.assertEqual(body['orderType'], 'MARKET')
        self.assertNotIn('level', body)

    def test_a_resting_order_is_a_working_order(self):
        route, body = self.handler.body(self.order(orderType='STOP'))
        self.assertEqual(route, 'create_order')
        self.assertEqual(body['type'], 'STOP')
        self.assertEqual(body['level'], 1.1650)

    def test_stop_and_limit_stay_apart(self):
        """
        Unlike eToro, where both collapse onto mit, IG really does tell the
        two orders apart - and so must this.
        """
        _route, stop = self.handler.body(self.order(orderType='STOP'))
        _route, limit = self.handler.body(self.order(orderType='LIMIT'))
        self.assertEqual(stop['type'], 'STOP')
        self.assertEqual(limit['type'], 'LIMIT')

    def test_direction_is_a_word_and_size_is_unsigned(self):
        _route, body = self.handler.body(self.order(units=-2))
        self.assertEqual(body['direction'], 'SELL')
        self.assertEqual(body['size'], 2)

    def test_force_open_is_stated(self):
        """IG attaches a stop only to a deal that opens its own position."""
        _route, body = self.handler.body(self.order())
        self.assertTrue(body['forceOpen'])

    def test_expiry_is_sent_rather_than_enforced(self):
        """
        This is what IG has that eToro does not, so nothing has to be
        cancelled on our side.
        """
        _route, body = self.handler.body(self.order())
        self.assertEqual(body['timeInForce'], 'GOOD_TILL_DATE')
        self.assertEqual(body['goodTillDate'], '2018/01/15 23:59:00')

    def test_an_order_without_an_expiry_rests_until_cancelled(self):
        _route, body = self.handler.body(self.order(gtdTime=None))
        self.assertEqual(body['timeInForce'], 'GOOD_TILL_CANCELLED')

    def test_an_unknown_order_type_is_refused(self):
        with self.assertRaises(IGError):
            self.handler.body(self.order(orderType='TRAILING'))

    def test_zero_units_is_refused(self):
        with self.assertRaises(IGError):
            self.handler.body(self.order(units=0))

    def test_a_guaranteed_stop_needs_a_stop(self):
        handler = IGExecutionHandler(setup=settings_stub(IG_GUARANTEED_STOP=True),
                                     api=self.api)
        with self.assertRaises(IGError):
            handler.body(self.order(stopLoss=None))

    def test_the_reference_travels_with_the_order(self):
        self.api.queue('create_order', 200, {'dealReference': 'REF123'})
        event = self.order()
        coe = self.handler.placeOrder(event)
        self.assertIsNotNone(coe)
        self.assertEqual(coe.dealReference, 'REF123')
        self.assertEqual(coe.signalNumber, event.signalNumber)

    def test_a_refusal_is_published_not_only_logged(self):
        """
        Was: a rejection that never reached the money manager left it
             believing an order was outstanding, and it refuses every new
             signal while it believes that - so one refusal stopped the
             strategy for good.
        Now: published as ORDER_REJECT, the same type the other brokers use.
        """
        self.api.queue('create_order', 400,
                       {'errorCode': 'error.invalid.dealReference'})
        self.assertIsNone(self.handler.placeOrder(self.order()))
        rejects = [e for e in self.recorder.events
                   if getattr(e, 'type', None) == 'ORDER_REJECT']
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0].rejectReason, 'error.invalid.dealReference')

    def test_cancel_without_a_deal_id_is_not_sent(self):
        """
        IG deletes a working order by dealId, and the acknowledgement carried
        only the reference. Sending the delete anyway would name the wrong
        resource.
        """
        event = OrderCancelEvent({'dealReference': 'REF123'})
        self.assertIsNone(self.handler.cancelOrder(event))
        self.assertEqual(self.api.of('cancel_order'), [])

    def test_cancel_with_a_deal_id_is_sent(self):
        self.api.queue('cancel_order', 200, {'dealReference': 'REF999'})
        self.handler.cancelOrder(OrderCancelEvent({'dealId': 'DEAL1'}))
        self.assertEqual(self.api.of('cancel_order')[0]['parts'], ('DEAL1',))


class TransactionsTest(unittest.TestCase):

    def setUp(self):
        self.setup = settings_stub()
        self.api = FakeAPI(setup=self.setup)
        self.recorder = Recorder()
        self.handler = IGTransactions(setup=self.setup, pairs=['EUR_USD'],
                                      api=self.api)
        self.handler.set_queue(self.recorder)

    def acknowledge(self, **over):
        data = {'id': 'REF1', 'dealReference': 'REF1', 'instrument': 'EUR_USD',
                'price': 1.1650, 'units': 1, 'orderType': 'MARKET',
                'stopLoss': 1.1600, 'takeProfit': 1.1700}
        data.update(over)
        event = ClientOrderEvent(data)
        event.signalNumber = 'AG01:EUR_USD:H1:20180115T010000'
        self.handler.execute_event(event)
        return event

    def fills(self):
        return [e for e in self.recorder.events
                if getattr(e, 'type', None) == 'ORDER_FILL']

    def test_it_learns_of_deals_from_the_bus(self):
        """
        Nothing tells this handler which deals exist; it listens for the
        acknowledgements the execution handler publishes.
        """
        self.acknowledge()
        self.assertIn('REF1', self.handler.deals)

    def test_an_accepted_market_deal_becomes_a_fill(self):
        self.acknowledge()
        self.api.queue('confirm', 200, {
            'dealStatus': 'ACCEPTED', 'status': 'OPENED', 'dealId': 'D1',
            'epic': 'CS.D.EURUSD.MINI.IP', 'direction': 'BUY', 'size': 1,
            'level': 1.16512, 'date': '2018-01-15T10:00:01.000',
            'stopLevel': 1.1600, 'limitLevel': 1.1700})
        self.handler.pollDeals()
        fills = self.fills()
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].price, 1.16512)
        self.assertEqual(fills[0].signalNumber,
                         'AG01:EUR_USD:H1:20180115T010000')

    def test_a_rejection_is_published(self):
        self.acknowledge()
        self.api.queue('confirm', 200, {
            'dealStatus': 'REJECTED', 'reason': 'ATTACHED_ORDER_LEVEL_ERROR',
            'date': '2018-01-15T10:00:01.000'})
        self.handler.pollDeals()
        rejects = [e for e in self.recorder.events
                   if getattr(e, 'type', None) == 'ORDER_REJECT']
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0].rejectReason, 'ATTACHED_ORDER_LEVEL_ERROR')
        self.assertNotIn('REF1', self.handler.deals)

    def test_a_resting_order_is_not_reported_as_a_fill(self):
        """
        An accepted working order exists; it has not filled. Publishing it as
        a fill would have the money manager believe it holds a position.
        """
        self.acknowledge(orderType='STOP')
        self.api.queue('confirm', 200, {
            'dealStatus': 'ACCEPTED', 'status': None, 'dealId': 'D2',
            'direction': 'BUY', 'size': 1, 'date': '2018-01-15T10:00:01.000'})
        self.handler.pollDeals()
        self.assertEqual(self.fills(), [])
        self.assertIn('D2', self.handler.positions)

    def test_a_confirmation_that_never_arrives_is_given_up_on_loudly(self):
        """
        IG keeps a confirmation available only briefly. A deal whose
        confirmation was never read may still be live on the account, so
        giving up is an error, not a shrug - the parity monitor will then
        count it unpaired, which is the correct reading.
        """
        self.acknowledge()
        self.api.queue('confirm', 404, None)
        for _ in range(self.handler.max_attempts):
            self.handler.pollDeals()
        self.assertNotIn('REF1', self.handler.deals)

    def test_a_close_names_the_leg_and_says_it_was_inferred(self):
        position = {'dealId': 'D1', 'dealReference': 'REF1',
                    'signalNumber': 'sig', 'instrument': 'EUR_USD',
                    'units': 1, 'openLevel': 1.1650, 'stopLoss': 1.1600,
                    'takeProfit': 1.1700, 'opened': T0, 'resting': False}
        self.handler.reportClose(position, {'closeLevel': 1.1700,
                                            'openLevel': 1.1650,
                                            'profitAndLoss': 'E5.00',
                                            'dateUtc': '2018-01-15T11:00:00'})
        close = self.fills()[0]
        self.assertEqual(close.reason, TAKE_PROFIT)
        self.assertTrue(close.reasonInferred)
        self.assertEqual(close.pl, 5.0)

    def test_a_close_between_the_levels_is_undecidable(self):
        """
        Neither leg was reached, so this was a manual close, a margin call or
        a gap - and saying so costs a comparison, where guessing would report
        an outcome the account did not have.
        """
        position = {'dealId': 'D1', 'dealReference': 'REF1',
                    'signalNumber': 'sig', 'instrument': 'EUR_USD',
                    'units': 1, 'openLevel': 1.1650, 'stopLoss': 1.1600,
                    'takeProfit': 1.1700, 'opened': T0, 'resting': False}
        self.handler.reportClose(position, {'closeLevel': 1.1660})
        self.assertEqual(self.fills()[0].reason, UNKNOWN)

    def test_the_balance_is_absent_rather_than_invented(self):
        position = {'dealId': 'D1', 'dealReference': 'REF1',
                    'signalNumber': 'sig', 'instrument': 'EUR_USD',
                    'units': 1, 'openLevel': 1.1650, 'stopLoss': 1.1600,
                    'takeProfit': 1.1700, 'opened': T0, 'resting': False}
        self.handler.reportClose(position, {'closeLevel': 1.1600})
        close = self.fills()[0]
        self.assertEqual(close.reason, STOP_LOSS)
        self.assertIsNone(close.accountBalance)


class HelpersTest(unittest.TestCase):

    def test_price_reads_one_side(self):
        row = price_row(T0)
        self.assertAlmostEqual(price(row, 'highPrice', 'ask'), 1.1611, places=6)
        self.assertIsNone(price(row, 'highPrice', 'lastTraded'))
        self.assertIsNone(price(row, 'nosuch', 'bid'))

    def test_profit_and_loss_strips_the_currency(self):
        """IG sends 'E-4.20' for a loss of 4.20 euro."""
        self.assertEqual(profitAndLoss('E-4.20'), -4.20)
        self.assertEqual(profitAndLoss('£12.50'), 12.50)
        self.assertEqual(profitAndLoss(3), 3.0)

    def test_an_unreadable_profit_is_none_not_zero(self):
        """A missing P&L is not a flat trade."""
        self.assertIsNone(profitAndLoss(None))
        self.assertIsNone(profitAndLoss('n/a'))


if __name__ == '__main__':
    unittest.main()
