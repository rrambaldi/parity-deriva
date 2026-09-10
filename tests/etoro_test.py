"""
Tests for the eToro provider: lib/etoro.py, data/etoro.py, execution/etoro.py.

The theme running through these is that eToro is poorer than OANDA in ways
that matter, and that each gap is handled by refusing or by declaring rather
than by filling in a plausible value. So a lot of what is pinned here is a
refusal: no API host means no client, an unmapped instrument means no order,
a granularity eToro does not serve means no data source, a short without a
stop means nothing sent.

The rest is translation, and it is pinned field by field because the failure
mode is not a crash. An order with the trigger in the wrong field, or a fill
whose price came from the wrong key, is a trade that happens at a price
nobody chose.

No network: every handler takes an `api` argument, and the client tests
replace the `requests` module.
"""

import datetime
import types
import unittest
from unittest import mock

from parity_deriva.data import etoro as data_etoro
from parity_deriva.data.etoro import (EToroCandles, EToroRates,
                                      EToroTransactions, closeReason)
from parity_deriva.event.event import (ClientOrderEvent, OrderCancelEvent,
                                       OrderEvent)
from parity_deriva.execution.etoro import EToroExecutionHandler
from parity_deriva.lib import etoro as lib_etoro
from parity_deriva.lib.etoro import (EToroAPI, EToroError, RateLimiter,
                                     SpreadModel, candleTime, instrument,
                                     instrumentId, instrumentName, interval,
                                     pricePrecision, requestId, utcnow)
from parity_deriva.tests.helpers import FakeRequests, FakeResponse, Recorder

T0 = datetime.datetime(2018, 1, 15, 10, 0, 0)


def settings_stub(**over):
    """
    Settings whose unset attributes are genuinely absent.

    Deliberately not a MagicMock: getattr() succeeds for every name on one of
    those, so ETORO_SPREAD would come back as a Mock instead of None and
    every 'unset means off' assertion here would pass for the wrong reason.
    """
    stub = types.SimpleNamespace(
        DOMAIN='practice',
        ETORO_API_DOMAIN='api.etoro.test',
        ETORO_ACCESS_TOKEN='TESTTOKEN',
        ETORO_USER_KEY='',
        ETORO_API_KEY='',
        ETORO_INSTRUMENTS={
            'EUR_USD': {'symbol': 'EURUSD', 'instrumentId': 1001, 'precision': 5},
            'DE30_EUR': {'symbol': 'GER40', 'instrumentId': 1002},
        },
        ETORO_SPREAD=None,
        ETORO_LEVERAGE=1,
        ETORO_SETTLEMENT_TYPE=None,
        ETORO_ENFORCE_EXPIRY=True,
        ETORO_POLL_SECONDS=0,
        ETORO_VERIFY_TLS=False,
        INSTRUMENT_PRECISION={'EUR_USD': 5, 'DE30_EUR': 1},
        DEFAULT_PRICE_PRECISION=5,
    )
    for key, value in over.items():
        setattr(stub, key, value)
    return stub


class FakeAPI(object):
    """
    Stands in for EToroAPI. Answers are queued per route key, and every call
    is recorded so a test can assert on what was asked for as well as on what
    was done with the reply.
    """

    def __init__(self, demo=True):
        self.demo = demo
        self.answers = {}
        self.calls = []

    def queue(self, key, status=200, payload=None):
        self.answers.setdefault(key, []).append((status, payload))
        return self

    def _call(self, method, key, parts, params, body, request_id):
        self.calls.append({'method': method, 'key': key, 'parts': tuple(parts),
                           'params': params, 'body': body,
                           'request_id': request_id})
        queued = self.answers.get(key)
        if not queued:
            return 200, None
        if len(queued) == 1:
            return queued[0]
        return queued.pop(0)

    def get(self, key, parts=(), params=None, request_id=None):
        return self._call('GET', key, parts, params, None, request_id)

    def post(self, key, parts=(), body=None, request_id=None):
        return self._call('POST', key, parts, None, body, request_id)

    def delete(self, key, parts=(), request_id=None):
        return self._call('DELETE', key, parts, None, None, request_id)

    def of(self, key):
        return [c for c in self.calls if c['key'] == key]


def candles_payload(rows, interval_name='OneMinute', instrument_id=1001):
    """
    The nested body the candle route answers with: a list per instrument even
    when one instrument was asked for.
    """
    return {'interval': interval_name,
            'candles': [{'instrumentId': instrument_id, 'candles': rows}]}


def candle_row(when, o=1.2000, h=1.2010, l=1.1990, c=1.2005, volume=7):
    return {'instrumentID': 1001, 'fromDate': when.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'open': o, 'high': h, 'low': l, 'close': c, 'volume': volume}


# ==========================================================================
# lib/etoro.py
# ==========================================================================

class IntervalTest(unittest.TestCase):

    def test_maps_the_projects_vocabulary(self):
        self.assertEqual(interval('M1'), 'OneMinute')
        self.assertEqual(interval('M15'), 'FifteenMinutes')
        self.assertEqual(interval('H1'), 'OneHour')
        self.assertEqual(interval('H4'), 'FourHours')
        self.assertEqual(interval('D'), 'OneDay')

    def test_refuses_what_etoro_does_not_serve(self):
        """
        Rounding 'H3' to 'FourHours' would hand a strategy bars of a period
        it did not ask for, and every level derived from them would be wrong
        by an unknown amount.
        """
        for granularity in ('S5', 'M2', 'H3', 'H12', 'nonsense'):
            with self.assertRaises(EToroError):
                interval(granularity)

    def test_the_error_says_what_is_available(self):
        """
        In the project's own spelling, which is what the operator configures -
        'H1', not 'OneHour'.
        """
        with self.assertRaises(EToroError) as caught:
            interval('S5')
        message = str(caught.exception)
        self.assertIn('H1', message)
        self.assertIn('M1', message)


class RequestIdTest(unittest.TestCase):

    def test_is_a_function_of_its_input(self):
        """
        The whole value of a derived id: the same signal produces the same id
        in a replay as it did live, so the two runs can be compared, and a
        retry cannot place the order twice.
        """
        first = requestId('AG01:EUR_USD:H1:20180115T010000', 'buy', 1.2)
        second = requestId('AG01:EUR_USD:H1:20180115T010000', 'buy', 1.2)
        self.assertEqual(first, second)

    def test_the_two_legs_of_one_signal_differ(self):
        """
        AG01 places two opposite orders from one signal. Sharing an
        idempotency key would have eToro treat the second as a repeat of the
        first.
        """
        key = 'AG01:EUR_USD:H1:20180115T010000'
        self.assertNotEqual(requestId(key, 'buy', 1.2),
                            requestId(key, 'sellShort', 1.1))

    def test_different_signals_differ(self):
        self.assertNotEqual(requestId('AG01:EUR_USD:H1:20180115T010000', 'buy'),
                            requestId('AG01:EUR_USD:H1:20180115T020000', 'buy'))

    def test_is_a_uuid(self):
        import uuid
        uuid.UUID(requestId('anything'))

    def test_no_parts_gives_a_random_one(self):
        """For the reads, where there is nothing to be idempotent about."""
        self.assertNotEqual(requestId(), requestId())


class InstrumentNamingTest(unittest.TestCase):

    def setUp(self):
        self.settings = settings_stub()

    def test_maps_a_configured_instrument(self):
        entry = instrument('EUR_USD', self.settings)
        self.assertEqual(entry['symbol'], 'EURUSD')
        self.assertEqual(instrumentId('EUR_USD', self.settings), 1001)

    def test_unmapped_instrument_raises_and_says_how_to_fix_it(self):
        """
        The alternative - resolving a symbol at runtime - would let a search
        result decide which market the money goes into.
        """
        with self.assertRaises(EToroError) as caught:
            instrumentId('GBP_JPY', self.settings)
        message = str(caught.exception)
        self.assertIn('ETORO_INSTRUMENTS', message)
        self.assertIn('etoro_instruments.py', message)

    def test_entry_without_an_id_raises_on_the_id_routes(self):
        stub = settings_stub(ETORO_INSTRUMENTS={'EUR_USD': {'symbol': 'EURUSD'}})
        with self.assertRaises(EToroError):
            instrumentId('EUR_USD', stub)

    def test_empty_entry_raises(self):
        stub = settings_stub(ETORO_INSTRUMENTS={'EUR_USD': {}})
        with self.assertRaises(EToroError):
            instrument('EUR_USD', stub)

    def test_reverse_lookup(self):
        self.assertEqual(instrumentName(1002, self.settings), 'DE30_EUR')
        self.assertIsNone(instrumentName(9999, self.settings))

    def test_precision_prefers_the_etoro_entry(self):
        stub = settings_stub(
            ETORO_INSTRUMENTS={'EUR_USD': {'instrumentId': 1, 'precision': 3}},
            INSTRUMENT_PRECISION={'EUR_USD': 5})
        self.assertEqual(pricePrecision('EUR_USD', stub), 3)

    def test_precision_falls_back_to_the_shared_table(self):
        """Precision is a property of the market, not of the broker."""
        self.assertEqual(pricePrecision('DE30_EUR', settings_stub()), 1)


class CandleTimeTest(unittest.TestCase):

    def test_offsets_are_normalised_to_naive_utc(self):
        """
        Every other timestamp in the project is naive. An aware one raises
        TypeError the first time it is compared with datetime.today().
        """
        when = candleTime('2018-01-15T10:00:00Z')
        self.assertIsNone(when.tzinfo)
        self.assertEqual(when, datetime.datetime(2018, 1, 15, 10, 0, 0))

    def test_a_non_utc_offset_is_converted_not_dropped(self):
        self.assertEqual(candleTime('2018-01-15T12:00:00+02:00'),
                         datetime.datetime(2018, 1, 15, 10, 0, 0))

    def test_it_can_be_compared_with_now(self):
        self.assertIsInstance(
            candleTime('2018-01-15T10:00:00Z') < datetime.datetime.today(), bool)

    def test_a_datetime_passes_through(self):
        self.assertEqual(candleTime(T0), T0)

    def test_garbage_falls_back_to_the_epoch(self):
        self.assertEqual(candleTime('not a date'),
                         datetime.datetime(1970, 1, 1, 0, 0, 0))


class UtcNowTest(unittest.TestCase):
    """
    The clock has to be on the same scale as the timestamps it is compared
    with. This is the defect real data found: candle times come through
    candleTime() and are UTC, the completeness check used datetime.today()
    which is local, and on a CEST host that made every candle look two hours
    older than it was - so the bar still forming was published as complete.

    These assertions are written to hold on any machine, including a UTC one
    where the bug would have been invisible.
    """

    def test_it_is_naive(self):
        """Mixing it with an aware datetime would raise, which is the point
        of dropping the offset rather than keeping it."""
        self.assertIsNone(utcnow().tzinfo)

    def test_it_is_utc_not_local(self):
        reference = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self.assertLess(abs((utcnow() - reference).total_seconds()), 5)

    def test_it_agrees_with_candletime_on_the_same_instant(self):
        """
        The two have to be usable in one comparison, which is the only
        property that matters here.
        """
        stamp = datetime.datetime.now(datetime.timezone.utc)
        parsed = candleTime(stamp.isoformat().replace('+00:00', 'Z'))
        self.assertLess(abs((utcnow() - parsed).total_seconds()), 5)


class SpreadModelTest(unittest.TestCase):

    OHLC = {'o': 1.2000, 'h': 1.2010, 'l': 1.1990, 'c': 1.2005}

    def test_off_by_default(self):
        """
        Unset means no bid and no ask, not a spread of zero. A zero would be
        a claim about the market; None is the absence of one.
        """
        model = SpreadModel(None)
        self.assertFalse(model.enabled())
        self.assertEqual(model.apply('EUR_USD', self.OHLC), (None, None))

    def test_a_number_applies_to_every_instrument(self):
        bid, ask = SpreadModel(0.0002).apply('EUR_USD', self.OHLC)
        self.assertAlmostEqual(bid['c'], 1.2004)
        self.assertAlmostEqual(ask['c'], 1.2006)

    def test_half_goes_each_side(self):
        bid, ask = SpreadModel(0.0002).apply('EUR_USD', self.OHLC)
        self.assertAlmostEqual(ask['h'] - bid['h'], 0.0002)

    def test_every_field_moves_by_the_same_amount(self):
        """
        Widening the high and not the low would assert something about where
        in the bar the spread moved, which one price series cannot say.
        """
        bid, ask = SpreadModel(0.0002).apply('EUR_USD', self.OHLC)
        for key in ('o', 'h', 'l', 'c'):
            self.assertAlmostEqual(ask[key] - self.OHLC[key], 0.0001)
            self.assertAlmostEqual(self.OHLC[key] - bid[key], 0.0001)

    def test_per_instrument_dict(self):
        model = SpreadModel({'EUR_USD': 0.0002, 'DE30_EUR': 2.0})
        bid, ask = model.apply('DE30_EUR', self.OHLC)
        self.assertAlmostEqual(ask['c'] - bid['c'], 2.0)

    def test_an_instrument_missing_from_the_dict_is_off(self):
        """
        Not silently given the other instrument's spread: a DAX spread on a
        currency pair would move every level by thousands of pips.
        """
        model = SpreadModel({'DE30_EUR': 2.0})
        self.assertIsNone(model.width('EUR_USD'))
        self.assertEqual(model.apply('EUR_USD', self.OHLC), (None, None))


class RateLimiterTest(unittest.TestCase):

    def setUp(self):
        self.now = 1000.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def limiter(self, limit=3, window=60):
        return RateLimiter(limit, window, sleep=self.sleep, clock=self.clock)

    def test_under_the_limit_never_sleeps(self):
        limiter = self.limiter()
        for _ in range(3):
            limiter.take()
        self.assertEqual(self.slept, [])

    def test_the_call_over_the_limit_waits_for_the_window(self):
        """
        Pacing rather than discovering the limit by being refused: on the
        write pool a 429 is a lost order.
        """
        limiter = self.limiter()
        for _ in range(3):
            limiter.take()
        limiter.take()
        self.assertEqual(len(self.slept), 1)
        self.assertAlmostEqual(self.slept[0], 60.0)

    def test_calls_ageing_out_of_the_window_free_the_budget(self):
        limiter = self.limiter()
        for _ in range(3):
            limiter.take()
        self.now += 61
        limiter.take()
        self.assertEqual(self.slept, [])

    def test_penalise_holds_everything_off(self):
        """A 429's Retry-After, honoured even with no RateLimit headers."""
        limiter = self.limiter()
        limiter.penalise(30)
        limiter.take()
        self.assertAlmostEqual(self.slept[0], 30.0)

    def test_penalise_ignores_nonsense(self):
        limiter = self.limiter()
        limiter.penalise(None)
        limiter.penalise('soon')
        limiter.take()
        self.assertEqual(self.slept, [])


class EToroAPITest(unittest.TestCase):

    def test_refuses_to_be_built_without_a_host(self):
        """
        Every other host in settings is one OANDA publishes. The eToro public
        API host is whatever the developer account gives you, and this code
        is not going to invent one.
        """
        with self.assertRaises(EToroError) as caught:
            EToroAPI(setup=settings_stub(ETORO_API_DOMAIN=''))
        self.assertIn('ETORO_API_DOMAIN', str(caught.exception))

    def test_refuses_both_authentication_modes(self):
        """eToro answers a request carrying both with a 422 that says little."""
        with self.assertRaises(EToroError) as caught:
            EToroAPI(setup=settings_stub(ETORO_ACCESS_TOKEN='T',
                                          ETORO_USER_KEY='U',
                                          ETORO_API_KEY='K'))
        self.assertIn('422', str(caught.exception))

    def test_no_credentials_at_all_is_left_to_fail_as_a_401(self):
        """
        The same choice the OANDA settings document: an empty token produces
        a 401 the data handlers report as StatusEvent('ERROR'), rather than a
        process that will not boot.
        """
        api = EToroAPI(setup=settings_stub(ETORO_ACCESS_TOKEN=''))
        headers = api.headers('rid')
        self.assertNotIn('Authorization', headers)
        self.assertNotIn('x-user-key', headers)

    def test_bearer_headers(self):
        api = EToroAPI(setup=settings_stub())
        headers = api.headers('rid')
        self.assertEqual(headers['Authorization'], 'Bearer TESTTOKEN')
        self.assertEqual(headers['x-request-id'], 'rid')

    def test_key_pair_headers(self):
        api = EToroAPI(setup=settings_stub(ETORO_ACCESS_TOKEN='',
                                            ETORO_USER_KEY='U',
                                            ETORO_API_KEY='K'))
        headers = api.headers('rid')
        self.assertEqual(headers['x-user-key'], 'U')
        self.assertEqual(headers['x-api-key'], 'K')
        self.assertNotIn('Authorization', headers)

    def test_practice_uses_the_demo_routes(self):
        api = EToroAPI(setup=settings_stub(DOMAIN='practice'))
        self.assertTrue(api.demo)
        self.assertEqual(api.route('create_order'),
                         '/api/v2/trading/execution/demo/orders')
        self.assertEqual(api.route('trade_history'),
                         '/api/v1/trading/info/trade/demo/history')

    def test_real_uses_the_real_routes(self):
        api = EToroAPI(setup=settings_stub(DOMAIN='real'))
        self.assertFalse(api.demo)
        self.assertEqual(api.route('create_order'),
                         '/api/v2/trading/execution/orders')
        self.assertEqual(api.route('trade_history'),
                         '/api/v1/trading/info/trade/history')

    def test_demo_is_not_a_uniform_prefix(self):
        """
        Why ROUTES spells both variants out: /demo goes in a different place
        per route family, so a transform would get one of them wrong.
        """
        api = EToroAPI(setup=settings_stub(DOMAIN='practice'))
        self.assertIn('/execution/demo/orders', api.route('create_order'))
        self.assertIn('/trade/demo/history', api.route('trade_history'))
        self.assertIn('/info/demo/orders:lookup', api.route('order_lookup'))

    def test_market_data_is_the_same_either_way(self):
        practice = EToroAPI(setup=settings_stub(DOMAIN='practice'))
        real = EToroAPI(setup=settings_stub(DOMAIN='real'))
        self.assertEqual(practice.route('rates'), real.route('rates'))

    def test_route_parameters_are_filled_in(self):
        api = EToroAPI(setup=settings_stub())
        self.assertEqual(
            api.route('candles', 1001, 'asc', 'OneHour', 100),
            '/api/v1/market-data/instruments/1001/history/candles/asc/OneHour/100')

    def test_unknown_route_raises(self):
        with self.assertRaises(EToroError):
            EToroAPI(setup=settings_stub()).route('teleport')

    def test_a_call_sends_the_request_id_and_returns_the_payload(self):
        api = EToroAPI(setup=settings_stub())
        fake = FakeRequests(FakeResponse({'ok': True}))
        with mock.patch.object(lib_etoro, 'requests', fake):
            status, payload = api.get('rates', params={'instrumentIds': '1001'},
                                      request_id='fixed-id')
        self.assertEqual((status, payload), (200, {'ok': True}))
        self.assertEqual(fake.last['headers']['x-request-id'], 'fixed-id')
        self.assertEqual(fake.last['url'], 'https://api.etoro.test/api/v2/market-data/rates')

    def test_a_post_carries_its_body(self):
        api = EToroAPI(setup=settings_stub())
        fake = FakeRequests(FakeResponse({'orderId': 7}))
        with mock.patch.object(lib_etoro, 'requests', fake):
            api.post('create_order', body={'action': 'open'})
        self.assertEqual(fake.body(), {'action': 'open'})
        self.assertEqual(fake.last['method'], 'POST')

    def test_an_error_status_comes_back_rather_than_raising(self):
        """A 4xx is a fact about the order, reported the way OANDA's is."""
        api = EToroAPI(setup=settings_stub())
        fake = FakeRequests(FakeResponse({'detail': 'nope'}, status=400))
        with mock.patch.object(lib_etoro, 'requests', fake):
            status, payload = api.get('rates')
        self.assertEqual(status, 400)
        self.assertEqual(payload['detail'], 'nope')

    def test_a_dead_network_returns_nothing_rather_than_raising(self):
        api = EToroAPI(setup=settings_stub())
        fake = FakeRequests(raise_on_send=True)
        with mock.patch.object(lib_etoro, 'requests', fake):
            self.assertEqual(api.get('rates'), (None, None))

    def test_an_unparseable_body_is_reported_not_raised(self):
        api = EToroAPI(setup=settings_stub())
        fake = FakeRequests(FakeResponse(text='<html>gateway</html>'))
        with mock.patch.object(lib_etoro, 'requests', fake):
            status, payload = api.get('rates')
        self.assertEqual(status, 200)
        self.assertIsNone(payload)

    def test_a_429_penalises_the_pool_it_came_from(self):
        api = EToroAPI(setup=settings_stub())
        response = FakeResponse({'errorCode': 'TooMany'}, status=429)
        response.headers = {'Retry-After': '42'}
        fake = FakeRequests(response)
        with mock.patch.object(lib_etoro, 'requests', fake):
            api.get('rates')
        self.assertGreater(api.limiters['market'].until, 0)

    def test_each_pool_is_paced_separately(self):
        """
        The quotas are per group, not per endpoint: 120/60s across the
        market-data routes, 20/60s across the execution ones.
        """
        api = EToroAPI(setup=settings_stub())
        self.assertEqual(api.limiters['market'].limit, 120)
        self.assertEqual(api.limiters['write'].limit, 20)
        self.assertEqual(api.limiters['orderinfo'].limit, 60)
        self.assertIsNot(api.limiters['market'], api.limiters['write'])


if __name__ == '__main__':
    unittest.main()


# ==========================================================================
# data/etoro.py - candles
# ==========================================================================

class EToroCandlesTest(unittest.TestCase):

    def setUp(self):
        self.settings = settings_stub()
        self.api = FakeAPI()
        self.sink = Recorder()

    def candles(self, **over):
        args = dict(setup=self.settings, pairs=['EUR_USD'], granularity='M1',
                    api=self.api)
        args.update(over)
        source = EToroCandles(**args)
        source.set_queue(self.sink)
        return source

    def test_live_asks_for_only_the_newest_bars(self):
        """
        Opening a session with a batch of stale candles would have a strategy
        signal on a reversal that is hours old. data/candles.py asks for two
        live for the same reason.
        """
        self.assertEqual(self.candles().batch_size, 2)

    def test_offline_reaches_back(self):
        source = self.candles(dtfrom=T0 - datetime.timedelta(days=1))
        self.assertEqual(source.batch_size, 500)
        self.assertFalse(source.live)

    def test_more_than_a_thousand_is_refused(self):
        """The route's own ceiling; asking for more silently returns less."""
        with self.assertRaises(EToroError):
            self.candles(batch_size=1001)

    def test_the_request_names_the_instrument_id_and_interval(self):
        self.api.queue('candles', 200, candles_payload([]))
        self.candles().poll('EUR_USD', now=T0)
        call = self.api.of('candles')[0]
        self.assertEqual(call['parts'], (1001, 'asc', 'OneMinute', 2))

    def test_rows_are_pulled_out_of_the_nesting(self):
        """
        The route answers with a list per instrument even for one instrument,
        so the candles are two levels down.
        """
        source = self.candles()
        payload = candles_payload([candle_row(T0), candle_row(T0)])
        self.assertEqual(len(source.rows(payload)), 2)

    def test_rows_of_an_empty_answer(self):
        self.assertEqual(self.candles().rows({}), [])
        self.assertEqual(self.candles().rows({'candles': []}), [])

    def test_completeness_is_computed_from_the_interval(self):
        """
        eToro sends no complete flag, so the only way to know is the candle's
        own start plus the period.
        """
        source = self.candles()
        self.assertTrue(source.complete(T0, now=T0 + datetime.timedelta(minutes=1)))
        self.assertFalse(source.complete(T0, now=T0 + datetime.timedelta(seconds=30)))

    def test_the_forming_candle_is_not_emitted(self):
        """
        The newest candle eToro serves is the one still forming. Emitting it
        would have a strategy signal on a high that is not the high.
        """
        rows = [candle_row(T0), candle_row(T0 + datetime.timedelta(minutes=1))]
        self.api.queue('candles', 200, candles_payload(rows))
        source = self.candles()
        sent = source.poll('EUR_USD', now=T0 + datetime.timedelta(minutes=1, seconds=30))
        self.assertEqual(sent, 1)
        self.assertEqual(len(self.sink.of('CANDLE')), 1)
        self.assertEqual(self.sink.of('CANDLE')[0].time, T0)

    def test_the_forming_candle_is_not_emitted_on_the_real_clock(self):
        """
        The same claim as the test above, but through the default clock rather
        than an injected one - which is where it was actually wrong. A candle
        that began half its period ago has not finished, whatever the
        machine's timezone.
        """
        source = self.candles()
        half = source.period / 2
        self.assertFalse(source.complete(utcnow() - half))
        self.assertTrue(source.complete(utcnow() - source.period * 2))

    def test_a_local_clock_would_have_called_a_forming_candle_complete(self):
        """
        Pins the shape of the defect rather than the fix: on a host with a
        positive UTC offset, comparing against local time accepts a candle
        that has not finished. Skipped where the machine is on UTC, since
        there the two clocks agree and there is nothing to catch.
        """
        offset = datetime.datetime.today() - utcnow()
        if abs(offset.total_seconds()) < 60:
            self.skipTest("machine is on UTC; the two clocks cannot disagree")
        source = self.candles()
        forming = utcnow() - source.period / 2
        self.assertFalse(source.complete(forming))
        self.assertTrue(source.complete(forming, now=datetime.datetime.today()))

    def test_the_offline_window_is_on_the_candles_own_scale(self):
        source = self.candles(dtfrom=T0)
        self.assertLess(abs((source.dtto - utcnow()).total_seconds()), 5)

    def test_nothing_is_complete_without_a_known_period(self):
        """
        Refusing to emit is recoverable; emitting a partial bar is not. This
        cannot be reached through the constructor, which refuses an unknown
        granularity outright, so it is pinned directly.
        """
        source = self.candles()
        source.period = None
        self.assertFalse(source.complete(T0, now=T0 + datetime.timedelta(days=1)))

    def test_a_candle_already_seen_is_not_repeated(self):
        rows = [candle_row(T0)]
        self.api.queue('candles', 200, candles_payload(rows))
        source = self.candles()
        now = T0 + datetime.timedelta(minutes=5)
        self.assertEqual(source.poll('EUR_USD', now=now), 1)
        self.assertEqual(source.poll('EUR_USD', now=now), 0)
        self.assertEqual(len(self.sink.of('CANDLE')), 1)

    def test_the_event_is_tagged_the_way_a_source_tags_it(self):
        """Strategies filter on instrument and the simulator reads granularity."""
        self.api.queue('candles', 200, candles_payload([candle_row(T0)]))
        self.candles().poll('EUR_USD', now=T0 + datetime.timedelta(minutes=5))
        event = self.sink.of('CANDLE')[0]
        self.assertEqual(event.instrument, 'EUR_USD')
        self.assertEqual(event.granularity, 'M1')
        self.assertTrue(event.complete)
        self.assertEqual(event.volume, 7)

    def test_without_a_spread_the_candle_carries_mid_only(self):
        """
        Not a spread of zero: bid and ask are absent, and the provider
        declines bid_ask_candles so a strategy needing them never starts.
        """
        self.api.queue('candles', 200, candles_payload([candle_row(T0)]))
        self.candles().poll('EUR_USD', now=T0 + datetime.timedelta(minutes=5))
        event = self.sink.of('CANDLE')[0]
        self.assertEqual(event.mid['c'], 1.2005)
        self.assertIsNone(event.bid)
        self.assertIsNone(event.ask)

    def test_with_a_spread_the_candle_carries_all_three(self):
        self.settings.ETORO_SPREAD = 0.0002
        self.api.queue('candles', 200, candles_payload([candle_row(T0)]))
        self.candles().poll('EUR_USD', now=T0 + datetime.timedelta(minutes=5))
        event = self.sink.of('CANDLE')[0]
        self.assertAlmostEqual(event.mid['c'], 1.2005)
        self.assertAlmostEqual(event.ask['c'], 1.2006)
        self.assertAlmostEqual(event.bid['c'], 1.2004)

    def test_a_missing_price_field_becomes_zero_not_an_exception(self):
        row = candle_row(T0)
        del row['high']
        self.api.queue('candles', 200, candles_payload([row]))
        self.candles().poll('EUR_USD', now=T0 + datetime.timedelta(minutes=5))
        self.assertEqual(self.sink.of('CANDLE')[0].mid['h'], 0.0)

    def test_an_error_status_reports_through_the_event_stream(self):
        self.api.queue('candles', 401, None)
        self.assertEqual(self.candles().poll('EUR_USD', now=T0), 0)
        self.assertEqual(self.sink.statuses(), ['ERROR'])

    def test_offline_discards_what_falls_outside_the_window(self):
        rows = [candle_row(T0 - datetime.timedelta(minutes=5)),
                candle_row(T0),
                candle_row(T0 + datetime.timedelta(minutes=5))]
        self.api.queue('candles', 200, candles_payload(rows))
        source = self.candles(dtfrom=T0 - datetime.timedelta(minutes=1),
                              dtto=T0 + datetime.timedelta(minutes=1))
        source.poll('EUR_USD', now=T0 + datetime.timedelta(days=1))
        times = [e.time for e in self.sink.of('CANDLE')]
        self.assertEqual(times, [T0])

    def test_offline_runs_one_pass_and_reports_done(self):
        """
        The route has no date range, so asking again returns the same window;
        looping would spin for ever on the same candles.
        """
        self.api.queue('candles', 200, candles_payload([candle_row(T0)]))
        source = self.candles(dtfrom=T0 - datetime.timedelta(minutes=1),
                              dtto=T0 + datetime.timedelta(days=1))
        source.stream_to_queue()
        self.assertEqual(self.sink.statuses(), ['STARTED', 'DONE'])
        self.assertEqual(len(self.api.of('candles')), 1)

    def test_an_unmapped_instrument_refuses_at_construction(self):
        with self.assertRaises(EToroError):
            self.candles(pairs=['GBP_JPY'])

    def test_an_unserved_granularity_refuses_at_construction(self):
        with self.assertRaises(EToroError):
            self.candles(granularity='S5')


class EToroRatesTest(unittest.TestCase):

    def setUp(self):
        self.settings = settings_stub()
        self.api = FakeAPI()
        self.sink = Recorder()
        self.rates = EToroRates(setup=self.settings, pairs=['EUR_USD', 'DE30_EUR'],
                                api=self.api)
        self.rates.set_queue(self.sink)

    def test_asks_for_every_instrument_in_one_call(self):
        """The quota is shared across eleven endpoints; batching is free."""
        self.api.queue('rates', 200, {'results': []})
        self.rates.poll()
        self.assertEqual(self.api.of('rates')[0]['params'],
                         {'instrumentIds': '1001,1002'})

    def test_a_tick_per_instrument(self):
        self.api.queue('rates', 200, {'results': [
            {'instrumentId': 1001, 'bid': 1.2000, 'ask': 1.2002,
             'date': '2018-01-15T10:00:00Z', 'quoteType': 'realtime'},
            {'instrumentId': 1002, 'bid': 12000.0, 'ask': 12002.0,
             'date': '2018-01-15T10:00:00Z', 'quoteType': 'realtime'}]})
        self.assertEqual(self.rates.poll(), 2)
        ticks = self.sink.of('TICK')
        self.assertEqual([t.instrument for t in ticks], ['EUR_USD', 'DE30_EUR'])
        self.assertEqual(ticks[0].bid, 1.2000)
        self.assertEqual(ticks[0].ask, 1.2002)
        self.assertIsNone(ticks[0].time.tzinfo)

    def test_an_unmapped_id_is_skipped(self):
        self.api.queue('rates', 200, {'results': [
            {'instrumentId': 4242, 'bid': 1.0, 'ask': 1.1}]})
        self.assertEqual(self.rates.poll(), 0)

    def test_a_rate_without_both_sides_is_skipped(self):
        """Half a quote is not a spread."""
        self.api.queue('rates', 200, {'results': [
            {'instrumentId': 1001, 'bid': 1.2, 'ask': None}]})
        self.assertEqual(self.rates.poll(), 0)

    def test_a_partial_answer_is_still_used(self):
        """206 means a subset was found, which is data, not an error."""
        self.api.queue('rates', 206, {'results': [
            {'instrumentId': 1001, 'bid': 1.2, 'ask': 1.3}]})
        self.assertEqual(self.rates.poll(), 1)

    def test_an_error_reports_through_the_event_stream(self):
        self.api.queue('rates', 500, None)
        self.assertEqual(self.rates.poll(), 0)
        self.assertEqual(self.sink.statuses(), ['ERROR'])


# ==========================================================================
# data/etoro.py - reading the closing leg off the rate
# ==========================================================================

class CloseReasonTest(unittest.TestCase):
    """
    eToro reports the rate a trade closed at, never which leg took it. The
    two levels bound the interval the trade lived in, and that is the rule:
    a close at or beyond a level reached it, a close strictly between them
    reached neither.
    """

    def test_a_close_at_the_target(self):
        self.assertEqual(closeReason(1.2100, 1.1900, 1.2100),
                         data_etoro.TAKE_PROFIT)

    def test_a_close_at_the_stop(self):
        self.assertEqual(closeReason(1.1900, 1.1900, 1.2100),
                         data_etoro.STOP_LOSS)

    def test_a_close_a_tick_past_the_stop(self):
        """Slippage through the level still belongs to the level."""
        self.assertEqual(closeReason(1.1898, 1.1900, 1.2100),
                         data_etoro.STOP_LOSS)

    def test_a_short_reads_the_same_way(self):
        """The rule is about distance, so the levels' order does not matter."""
        self.assertEqual(closeReason(1.1900, 1.2100, 1.1900),
                         data_etoro.TAKE_PROFIT)

    def test_a_close_between_the_levels_is_undecidable(self):
        """
        A trade closed by hand, by a margin call or at a gap reached neither
        level. Naming the nearer one would report an outcome the account
        never had.
        """
        self.assertEqual(closeReason(1.2000, 1.1900, 1.2100), data_etoro.UNKNOWN)

    def test_a_close_well_inside_the_target_is_undecidable(self):
        """
        90 pips short of the target is not the target, however much nearer it
        is to that end of the interval than the other.
        """
        self.assertEqual(closeReason(1.2010, 1.1900, 1.2100), data_etoro.UNKNOWN)

    def test_a_close_one_tick_inside_a_level_is_undecidable(self):
        """
        Where the rule errs: a target that slipped and filled just inside its
        level is not claimed as a target. Declining to judge costs the parity
        monitor a comparison; claiming it would cost it a wrong one.
        """
        self.assertEqual(closeReason(1.2099, 1.1900, 1.2100), data_etoro.UNKNOWN)

    def test_a_close_beyond_the_target_is_the_target(self):
        """A gap through the level, which is still the level being reached."""
        self.assertEqual(closeReason(1.2150, 1.1900, 1.2100),
                         data_etoro.TAKE_PROFIT)

    def test_no_gap_between_the_levels_is_undecidable(self):
        self.assertEqual(closeReason(1.2000, 1.2000, 1.2000), data_etoro.UNKNOWN)

    def test_missing_levels_are_undecidable(self):
        self.assertEqual(closeReason(1.2000, None, 1.2100), data_etoro.UNKNOWN)
        self.assertEqual(closeReason(None, 1.19, 1.21), data_etoro.UNKNOWN)
        self.assertEqual(closeReason('', 1.19, 1.21), data_etoro.UNKNOWN)

    def test_the_vocabulary_matches_oandas(self):
        """
        Because the parity monitor compares one side's reason against the
        other's, and the simulator speaks OANDA's.
        """
        self.assertEqual(data_etoro.TAKE_PROFIT, 'TAKE_PROFIT_ORDER')
        self.assertEqual(data_etoro.STOP_LOSS, 'STOP_LOSS_ORDER')


def lookup_payload(status_id=data_etoro.STATUS_FILLED, position_id=9001,
                   avg_price=1.2005, units=1.0, side='long', order_type='mit',
                   state='open', sl=1.1900, tp=1.2100, error_code=0,
                   error_message=None, executions=True):
    """The body GET /api/v2/trading/info/orders:lookup answers with."""
    payload = {
        'orderId': 555,
        'action': 'open',
        'transaction': 'buy' if side == 'long' else 'sellShort',
        'type': order_type,
        'status': {'id': status_id, 'name': 'x', 'errorCode': error_code,
                   'errorMessage': error_message},
        'asset': {'symbol': 'EURUSD', 'instrumentId': 1001, 'side': side},
        'referenceId': 'ref-1',
        'requestTime': '2018-01-15T10:00:00Z',
        'lastUpdate': '2018-01-15T10:00:05Z',
        'positionExecutions': [],
    }
    if executions:
        payload['positionExecutions'] = [{
            'positionId': position_id,
            'state': state,
            'stopLossRate': sl,
            'takeProfitRate': tp,
            'remainingUnits': units,
            'openingData': {
                'openTime': '2018-01-15T10:00:01Z',
                'executionTime': '2018-01-15T10:00:02Z',
                'units': units,
                'avgPrice': avg_price,
                'avgConversionRate': 1.0,
                'marketSpread': 0.0002,
                'markup': 0.0,
                'fees': 0.5,
                'taxes': 0.0,
            },
        }]
    return payload


def history_row(position_id=9001, close_rate=1.2100, sl=1.1900, tp=1.2100,
                net=12.0, fees=0.5, units=1.0, open_rate=1.2005):
    """One closed trade from GET /api/v1/trading/info/trade/history."""
    return {'positionId': position_id, 'closeRate': close_rate,
            'closeTimestamp': '2018-01-15T11:00:00Z', 'instrumentId': 1001,
            'isBuy': True, 'leverage': 1, 'openRate': open_rate,
            'openTimestamp': '2018-01-15T10:00:02Z', 'stopLossRate': sl,
            'takeProfitRate': tp, 'trailingStopLoss': False, 'orderId': 555,
            'netProfit': net, 'fees': fees, 'units': units,
            'investment': 100.0, 'initialInvestment': 100.0}


SIGNAL = 'AG01:EUR_USD:H1:20180115T100000'


def acknowledgement(order_id=555, price=1.2010, gtd=None, **over):
    """The ClientOrderEvent the execution handler publishes."""
    payload = {'id': order_id, 'batchID': 0, 'instrument': 'EUR_USD',
               'price': price, 'units': 1, 'orderType': 'STOP',
               'stopLoss': 1.1900, 'takeProfit': 1.2100, 'gtdTime': gtd,
               'referenceId': 'ref-1'}
    payload.update(over)
    event = ClientOrderEvent(payload)
    event.signalNumber = SIGNAL
    return event


# ==========================================================================
# data/etoro.py - the fills nobody pushes
# ==========================================================================

class EToroTransactionsTest(unittest.TestCase):

    def setUp(self):
        self.settings = settings_stub()
        self.api = FakeAPI()
        self.sink = Recorder()
        self.poller = EToroTransactions(setup=self.settings, pairs=['EUR_USD'],
                                        api=self.api)
        self.poller.set_queue(self.sink)

    def fills(self):
        return [e for e in self.sink.of('TRANSACTION')
                if e.type == 'ORDER_FILL' and not e.has_attr('tradesClosed')]

    def closes(self):
        return [e for e in self.sink.of('TRANSACTION')
                if e.has_attr('tradesClosed')]

    # ------------------------------------------------------------ listening

    def test_it_learns_of_an_order_from_the_bus(self):
        """
        Nothing tells this handler which orders exist; it hears the execution
        handler's acknowledgements, which is what keeps the two decoupled.
        """
        self.poller.execute_event(acknowledgement())
        self.assertIn(555, self.poller.orders)
        self.assertEqual(self.poller.orders[555]['signalNumber'], SIGNAL)

    def test_an_acknowledgement_without_an_id_is_refused_not_crashed_on(self):
        event = ClientOrderEvent({'batchID': 0, 'price': 1.2})
        self.poller.execute_event(event)
        self.assertEqual(self.poller.orders, {})

    def test_a_cancel_stops_the_polling(self):
        self.poller.execute_event(acknowledgement())
        self.poller.execute_event(OrderCancelEvent(
            {'orderID': 555, 'price': 1.2010, 'instrument': 'EUR_USD'}))
        self.assertEqual(self.poller.orders, {})

    def test_other_events_are_ignored(self):
        from parity_deriva.event.event import CandleEvent
        self.poller.execute_event(CandleEvent({'time': T0}))
        self.assertEqual(self.poller.orders, {})

    # ---------------------------------------------------------------- fills

    def test_a_fill_is_reported_in_oandas_shape(self):
        """
        The money manager and the parity monitor read these field names, so
        the translation happens here rather than in them.
        """
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)

        fill = self.fills()[0]
        self.assertEqual(fill.type, 'ORDER_FILL')
        self.assertEqual(fill.orderID, 555)
        self.assertEqual(fill.price, 1.2005)
        self.assertEqual(fill.units, 1.0)
        self.assertEqual(fill.instrument, 'EUR_USD')
        self.assertEqual(fill.signalNumber, SIGNAL)
        self.assertEqual(fill.positionId, 9001)
        self.assertIsNone(fill.time.tzinfo)

    def test_the_fill_price_is_the_average_execution_price(self):
        """
        Not the trigger the order carried: what the parity monitor compares
        against the simulator's fill is where the account actually filled.
        """
        self.poller.execute_event(acknowledgement(price=1.2010))
        self.api.queue('order_lookup', 200, lookup_payload(avg_price=1.2017))
        self.poller.pollOrders(now=T0)
        self.assertEqual(self.fills()[0].price, 1.2017)

    def test_a_filled_order_stops_being_polled(self):
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        self.assertEqual(self.poller.orders, {})
        self.assertIn(9001, self.poller.positions)

    def test_a_short_fill_carries_negative_units(self):
        """
        eToro states the direction as a word; the rest of this project reads
        it off the sign of units.
        """
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload(side='short'))
        self.poller.pollOrders(now=T0)
        self.assertEqual(self.fills()[0].units, -1.0)

    def test_an_order_still_in_flight_keeps_being_polled(self):
        """
        200 on the create call means accepted, not executed, so an order
        waiting for its trigger is the normal case rather than a problem.
        """
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200,
                       lookup_payload(status_id=data_etoro.STATUS_WAITING_FOR_MARKET,
                                      executions=False))
        self.poller.pollOrders(now=T0)
        self.assertIn(555, self.poller.orders)
        self.assertEqual(self.fills(), [])

    def test_a_rejection_is_published_and_logged(self):
        """
        The rejection arrives asynchronously here, long after the call that
        placed the order returned a 200.
        """
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200,
                       lookup_payload(status_id=data_etoro.STATUS_REJECTED,
                                      executions=False, error_code=42,
                                      error_message='insufficient funds'))
        self.poller.pollOrders(now=T0)
        rejects = [e for e in self.sink.of('TRANSACTION')
                   if e.type == 'ORDER_REJECT']
        self.assertEqual(len(rejects), 1)
        self.assertEqual(rejects[0].rejectReason, 42)
        self.assertEqual(rejects[0].signalNumber, SIGNAL)
        self.assertEqual(self.poller.orders, {})

    def test_a_cancelled_order_is_dropped_without_an_event(self):
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200,
                       lookup_payload(status_id=data_etoro.STATUS_CANCELED,
                                      executions=False))
        self.poller.pollOrders(now=T0)
        self.assertEqual(self.poller.orders, {})
        self.assertEqual(self.sink.of('TRANSACTION'), [])

    def test_a_lookup_that_fails_leaves_the_order_alone(self):
        """A dead read is not evidence about the order."""
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 500, None)
        self.poller.pollOrders(now=T0)
        self.assertIn(555, self.poller.orders)

    # --------------------------------------------------------------- closes

    def test_no_open_positions_means_no_history_read(self):
        """The quota is shared; a read with nothing to match is wasted."""
        self.assertEqual(self.poller.pollCloses(), 0)
        self.assertEqual(self.api.of('trade_history'), [])

    def test_a_close_is_reported_with_the_leg_inferred(self):
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        self.api.queue('trade_history', 200, [history_row()])
        self.poller.pollCloses()

        close = self.closes()[0]
        self.assertEqual(close.orderID, 555)
        self.assertEqual(close.price, 1.2100)
        self.assertEqual(close.pl, 12.0)
        self.assertEqual(close.financing, 0.5)
        self.assertEqual(close.reason, data_etoro.TAKE_PROFIT)
        self.assertTrue(close.reasonInferred)
        self.assertEqual(close.signalNumber, SIGNAL)

    def test_a_close_says_that_its_reason_is_inferred(self):
        """
        So that nothing downstream mistakes an inference for something the
        broker reported. OANDA states the leg; eToro never does.
        """
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        self.api.queue('trade_history', 200, [history_row()])
        self.poller.pollCloses()
        self.assertTrue(self.closes()[0].has_attr('reasonInferred'))

    def test_the_balance_is_absent_rather_than_invented(self):
        """
        The trade history does not carry it. A plausible number here would be
        a figure in the ledger that no read ever returned.
        """
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        self.api.queue('trade_history', 200, [history_row()])
        self.poller.pollCloses()
        self.assertIsNone(self.closes()[0].accountBalance)

    def test_a_close_between_the_levels_is_reported_unknown(self):
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        self.api.queue('trade_history', 200, [history_row(close_rate=1.2000)])
        self.poller.pollCloses()
        self.assertEqual(self.closes()[0].reason, data_etoro.UNKNOWN)

    def test_a_close_is_reported_once(self):
        """The history keeps answering with it on every later read."""
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        self.api.queue('trade_history', 200, [history_row()])
        self.assertEqual(self.poller.pollCloses(), 1)
        self.assertEqual(self.poller.pollCloses(), 0)
        self.assertEqual(len(self.closes()), 1)

    def test_a_trade_this_session_never_opened_is_ignored(self):
        """
        The history is account-wide. A position with no signal behind it has
        nothing to be joined to, and reporting it would invent a close for a
        trade the bus never saw open.
        """
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        self.api.queue('trade_history', 200, [history_row(position_id=1)])
        self.assertEqual(self.poller.pollCloses(), 0)

    def test_the_history_window_starts_at_the_oldest_open_position(self):
        self.poller.positions[1] = {'opened': datetime.datetime(2018, 1, 10, 9, 0),
                                     'positionId': 1, 'orderID': 1,
                                     'signalNumber': SIGNAL,
                                     'instrument': 'EUR_USD', 'units': 1,
                                     'openRate': 1.0, 'stopLoss': 0.9,
                                     'takeProfit': 1.1}
        self.assertEqual(self.poller.since(), '2018-01-10')

    def test_a_history_read_wrapped_in_an_envelope_is_still_read(self):
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        self.api.queue('trade_history', 200, {'trades': [history_row()]})
        self.assertEqual(self.poller.pollCloses(), 1)

    # --------------------------------------------------------------- expiry

    def test_a_resting_order_past_its_expiry_is_cancelled_here(self):
        """
        eToro has no expiry on an order. AG01 brackets one reversal, and a
        bracket still resting a week later is not that trade any more, so the
        gtdTime the order was issued with is honoured on our side.
        """
        self.poller.execute_event(acknowledgement(gtd=T0))
        self.api.queue('order_lookup', 200,
                       lookup_payload(status_id=data_etoro.STATUS_WAITING_FOR_MARKET,
                                      executions=False))
        self.poller.pollOrders(now=T0 + datetime.timedelta(minutes=1))

        cancels = self.sink.of('ORDERCANCEL')
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0].orderID, 555)
        self.assertEqual(cancels[0].reason, 'GTD_EXPIRY_ENFORCED')
        self.assertEqual(self.poller.orders, {})

    def test_the_cancel_carries_the_price_the_simulator_matches_on(self):
        """
        The simulator numbers its own orders, so instrument and price are the
        only fields both sides agree on - both books have to drop the order.
        """
        self.poller.execute_event(acknowledgement(gtd=T0, price=1.2010))
        self.api.queue('order_lookup', 200,
                       lookup_payload(status_id=data_etoro.STATUS_PLACED,
                                      executions=False))
        self.poller.pollOrders(now=T0 + datetime.timedelta(minutes=1))
        cancel = self.sink.of('ORDERCANCEL')[0]
        self.assertEqual(cancel.price, 1.2010)
        self.assertEqual(cancel.instrument, 'EUR_USD')

    def test_an_order_still_within_its_expiry_is_left_alone(self):
        self.poller.execute_event(acknowledgement(gtd=T0 + datetime.timedelta(hours=1)))
        self.api.queue('order_lookup', 200,
                       lookup_payload(status_id=data_etoro.STATUS_PLACED,
                                      executions=False))
        self.poller.pollOrders(now=T0)
        self.assertEqual(self.sink.of('ORDERCANCEL'), [])
        self.assertIn(555, self.poller.orders)

    def test_an_order_without_an_expiry_is_left_alone(self):
        self.poller.execute_event(acknowledgement(gtd=None))
        self.api.queue('order_lookup', 200,
                       lookup_payload(status_id=data_etoro.STATUS_PLACED,
                                      executions=False))
        self.poller.pollOrders(now=T0 + datetime.timedelta(days=7))
        self.assertEqual(self.sink.of('ORDERCANCEL'), [])

    def test_enforcement_can_be_turned_off(self):
        """Then a resting order rests until it triggers, which is eToro's own
        behaviour."""
        poller = EToroTransactions(setup=settings_stub(ETORO_ENFORCE_EXPIRY=False),
                                   pairs=['EUR_USD'], api=self.api)
        poller.set_queue(self.sink)
        poller.execute_event(acknowledgement(gtd=T0))
        self.api.queue('order_lookup', 200,
                       lookup_payload(status_id=data_etoro.STATUS_PLACED,
                                      executions=False))
        poller.pollOrders(now=T0 + datetime.timedelta(days=7))
        self.assertEqual(self.sink.of('ORDERCANCEL'), [])

    def test_a_filled_order_is_not_expired(self):
        """Expiry applies to what is resting, not to what already traded."""
        self.poller.execute_event(acknowledgement(gtd=T0))
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0 + datetime.timedelta(days=1))
        self.assertEqual(self.sink.of('ORDERCANCEL'), [])
        self.assertEqual(len(self.fills()), 1)


class MoneyManagerCompatibilityTest(unittest.TestCase):
    """
    The synthesised events have to satisfy the money manager field for field.

    This is not a stylistic point. Engine.run() calls os._exit(1) when a
    handler raises, so a close event missing a field MoneyManager.closeTrade
    reads would take the process down with the trade still open.
    """

    def setUp(self):
        from parity_deriva.portfolio.moneymanager import MoneyManager
        MoneyManager.signals = {}
        MoneyManager.processed = []
        self.settings = settings_stub()
        self.api = FakeAPI()
        self.sink = Recorder()
        self.poller = EToroTransactions(setup=self.settings, pairs=['EUR_USD'],
                                        api=self.api)
        self.poller.set_queue(self.sink)
        self.manager = MoneyManager(setup=self.settings, units=1)
        self.manager.set_queue(self.sink)

    def synthesise(self):
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        self.api.queue('trade_history', 200, [history_row()])
        self.poller.pollCloses()
        return [e for e in self.sink.of('TRANSACTION')
                if getattr(e, 'type', None) == 'ORDER_FILL']

    def test_the_manager_reads_a_synthesised_fill_without_raising(self):
        fill = self.synthesise()[0]
        self.manager.execute_event(fill)
        self.assertTrue(self.manager.onTrade)

    def test_the_manager_reads_a_synthesised_close_without_raising(self):
        events = self.synthesise()
        close = [e for e in events if e.has_attr('tradesClosed')][0]
        self.manager.execute_event(close)
        self.assertFalse(self.manager.onTrade)
        self.assertFalse(self.manager.orderIssued)

    def test_the_manager_matches_the_acknowledgement_by_price(self):
        """
        handleClientOrder pairs an acknowledgement to its order on price, and
        keeps id and batchID off it, so both have to be present and integral.
        """
        from parity_deriva.event.event import OrderEvent
        order = OrderEvent({'instrument': 'EUR_USD', 'units': 1,
                            'orderType': 'STOP', 'price': 1.2010,
                            'stopLoss': 1.1900, 'takeProfit': 1.2100})
        order.signalNumber = SIGNAL
        self.manager.addOrder(order)
        self.manager.execute_event(acknowledgement(order_id=555, price=1.2010))
        self.assertEqual(self.manager.signals[SIGNAL][0].orderID, 555)
        self.assertEqual(self.manager.signals[SIGNAL][0].batchID, 0)


# ==========================================================================
# execution/etoro.py
# ==========================================================================

def order_event(**over):
    payload = {'instrument': 'EUR_USD', 'units': 1, 'orderType': 'STOP',
               'price': 1.2010, 'stopLoss': 1.1900, 'takeProfit': 1.2100,
               'gtdTime': None}
    payload.update(over)
    event = OrderEvent(payload)
    event.signalNumber = SIGNAL
    return event


class EToroExecutionTest(unittest.TestCase):

    def setUp(self):
        self.settings = settings_stub()
        self.api = FakeAPI()
        self.sink = Recorder()
        self.handler = EToroExecutionHandler(setup=self.settings, api=self.api)
        self.handler.set_queue(self.sink)

    def send(self, **over):
        self.api.queue('create_order', 200,
                       {'orderId': 555, 'referenceId': 'ref-1', 'token': 't'})
        self.handler.execute_event(order_event(**over))
        calls = self.api.of('create_order')
        return calls[0]['body'] if calls else None

    # ----------------------------------------------------------- order types

    def test_a_stop_becomes_market_if_touched(self):
        body = self.send(orderType='STOP')
        self.assertEqual(body['orderType'], 'mit')
        self.assertEqual(body['triggerRate'], 1.2010)

    def test_a_limit_becomes_the_same_order(self):
        """
        eToro has one resting type. AG01's breakout STOP and AG02's fade
        LIMIT differ only in where the trigger sits, and the broker cannot
        tell them apart - which is why the provider declares
        distinct_stop_limit False rather than pretending otherwise.
        """
        body = self.send(orderType='LIMIT')
        self.assertEqual(body['orderType'], 'mit')
        self.assertEqual(body['triggerRate'], 1.2010)

    def test_a_market_order_carries_no_trigger(self):
        """Supplying one on a mkt order is rejected outright."""
        body = self.send(orderType='MARKET')
        self.assertEqual(body['orderType'], 'mkt')
        self.assertNotIn('triggerRate', body)

    def test_an_order_type_etoro_lacks_is_not_sent(self):
        self.handler.execute_event(order_event(orderType='TRAILING_STOP'))
        self.assertEqual(self.api.of('create_order'), [])
        self.assertEqual(self.sink.events, [])

    # ------------------------------------------------------------ directions

    def test_a_long_is_a_buy(self):
        body = self.send(units=1)
        self.assertEqual(body['transaction'], 'buy')
        self.assertEqual(body['units'], 1.0)

    def test_a_short_is_a_sell_short(self):
        """
        Not 'sell': that closes a long, and eToro rejects it today. The
        project's signed units have to be translated, not passed through.
        """
        body = self.send(units=-1)
        self.assertEqual(body['transaction'], 'sellShort')
        self.assertEqual(body['units'], 1.0)

    def test_the_action_is_always_open(self):
        """Closing goes through a different route; action 'close' is rejected."""
        self.assertEqual(self.send()['action'], 'open')

    def test_zero_units_is_not_sent(self):
        self.handler.execute_event(order_event(units=0))
        self.assertEqual(self.api.of('create_order'), [])

    # ------------------------------------------------- mandatory stop losses

    def test_a_short_without_a_stop_is_refused_here(self):
        """
        eToro requires stopLossRate on a short. Sending it anyway would be
        accepted with an order id and then refused during execution, where
        the reason surfaces somewhere much less obvious.
        """
        self.handler.execute_event(order_event(units=-1, stopLoss=None))
        self.assertEqual(self.api.of('create_order'), [])
        self.assertEqual(self.sink.events, [])

    def test_a_leveraged_order_without_a_stop_is_refused_here(self):
        handler = EToroExecutionHandler(setup=settings_stub(ETORO_LEVERAGE=5),
                                        api=self.api)
        handler.set_queue(self.sink)
        handler.execute_event(order_event(stopLoss=None))
        self.assertEqual(self.api.of('create_order'), [])

    def test_an_unleveraged_long_without_a_stop_is_allowed(self):
        """eToro only insists for a short or for leverage."""
        body = self.send(units=1, stopLoss=None)
        self.assertNotIn('stopLossRate', body)

    def test_the_levels_are_named_as_etoro_names_them(self):
        body = self.send()
        self.assertEqual(body['stopLossRate'], 1.1900)
        self.assertEqual(body['takeProfitRate'], 1.2100)

    # -------------------------------------------------------------- the body

    def test_the_instrument_is_sent_as_an_id(self):
        """
        Exactly one of symbol or instrumentId; providing both is rejected.
        """
        body = self.send()
        self.assertEqual(body['instrumentId'], 1001)
        self.assertNotIn('symbol', body)

    def test_an_unmapped_instrument_is_not_sent(self):
        self.handler.execute_event(order_event(instrument='GBP_JPY'))
        self.assertEqual(self.api.of('create_order'), [])

    def test_leverage_comes_from_settings(self):
        self.assertEqual(self.send()['leverage'], 1)

    def test_settlement_type_is_omitted_unless_configured(self):
        """
        Optional on the v2 create route, and the eligible values differ per
        instrument, direction and leverage, so there is nothing to default to.
        """
        self.assertNotIn('settlementType', self.send())

    def test_settlement_type_is_sent_when_configured(self):
        handler = EToroExecutionHandler(
            setup=settings_stub(ETORO_SETTLEMENT_TYPE='cfd'), api=self.api)
        handler.set_queue(self.sink)
        self.api.queue('create_order', 200, {'orderId': 555})
        handler.execute_event(order_event())
        self.assertEqual(self.api.of('create_order')[0]['body']['settlementType'],
                         'cfd')

    # ------------------------------------------------------- idempotency key

    def test_the_request_id_is_derived_from_the_signal(self):
        self.send()
        sent = self.api.of('create_order')[0]['request_id']
        self.assertEqual(sent, requestId(SIGNAL, 'buy', 1.2010))

    def test_the_two_legs_of_a_signal_get_different_keys(self):
        """
        Sharing one would have eToro treat the second leg as a repeat of the
        first and place a single order where the strategy wanted a bracket.
        """
        self.assertNotEqual(self.handler.key(order_event(units=1, price=1.2010)),
                            self.handler.key(order_event(units=-1, price=1.1990)))

    def test_the_same_order_twice_gets_the_same_key(self):
        """Which is what makes a retry safe."""
        self.assertEqual(self.handler.key(order_event()),
                         self.handler.key(order_event()))

    # ----------------------------------------------------- acknowledgement

    def test_a_successful_order_is_acknowledged_on_the_bus(self):
        self.send()
        acks = self.sink.of('CLIENTORDER')
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0].id, 555)
        self.assertEqual(acks[0].signalNumber, SIGNAL)

    def test_the_acknowledgement_carries_what_the_money_manager_reads(self):
        """It pairs on price and keeps id and batchID off the event."""
        self.send()
        ack = self.sink.of('CLIENTORDER')[0]
        self.assertEqual(ack.price, 1.2010)
        self.assertEqual(ack.batchID, 0)
        self.assertEqual(int(ack.id), 555)

    def test_the_acknowledgement_carries_what_the_poller_reads(self):
        """
        The fill poller learns of the order from this event, and needs the
        expiry and the levels to enforce the one and infer the other.
        """
        gtd = T0 + datetime.timedelta(hours=5)
        self.send(gtdTime=gtd)
        ack = self.sink.of('CLIENTORDER')[0]
        self.assertEqual(ack.gtdTime, gtd)
        self.assertEqual(ack.stopLoss, 1.1900)
        self.assertEqual(ack.takeProfit, 1.2100)
        self.assertEqual(ack.instrument, 'EUR_USD')

    def test_a_rejection_is_not_acknowledged(self):
        self.api.queue('create_order', 400, {'detail': 'no'})
        self.handler.execute_event(order_event())
        self.assertEqual(self.sink.events, [])

    def test_a_200_without_an_order_id_is_treated_as_a_rejection(self):
        """
        Acknowledging it would leave the poller watching an order id of None
        for ever.
        """
        self.api.queue('create_order', 200, {'referenceId': 'ref-1'})
        self.handler.execute_event(order_event())
        self.assertEqual(self.sink.events, [])

    def test_a_dead_network_is_not_acknowledged(self):
        self.api.queue('create_order', None, None)
        self.handler.execute_event(order_event())
        self.assertEqual(self.sink.events, [])

    # --------------------------------------------------------------- cancels

    def test_a_cancel_deletes_the_order(self):
        self.handler.execute_event(OrderCancelEvent(
            {'orderID': 555, 'price': 1.2010, 'instrument': 'EUR_USD'}))
        call = self.api.of('cancel_order')[0]
        self.assertEqual(call['method'], 'DELETE')
        self.assertEqual(call['parts'], (555,))

    def test_a_cancel_is_idempotent(self):
        self.handler.execute_event(OrderCancelEvent({'orderID': 555}))
        self.handler.execute_event(OrderCancelEvent({'orderID': 555}))
        keys = [c['request_id'] for c in self.api.of('cancel_order')]
        self.assertEqual(keys[0], keys[1])

    def test_a_cancel_without_an_order_id_sends_nothing(self):
        self.handler.execute_event(OrderCancelEvent({'price': 1.2010}))
        self.assertEqual(self.api.of('cancel_order'), [])

    def test_events_it_does_not_own_are_ignored(self):
        from parity_deriva.event.event import CandleEvent, SignalEvent
        self.handler.execute_event(CandleEvent({'time': T0}))
        self.handler.execute_event(SignalEvent({'instrument': 'EUR_USD'}))
        self.assertEqual(self.api.calls, [])


# ==========================================================================
# the two sides meeting
# ==========================================================================

class ParityAcrossEToroTest(unittest.TestCase):
    """
    The eToro side has to be joinable to the simulated side.

    This is the claim the whole parallel design rests on, and on eToro it is
    less obvious than on OANDA: there the fill arrives carrying the
    clientExtensions the order was tagged with, while here the fill is
    assembled by a poller from two separate reads. If the signal key does not
    survive that, the monitor sees two unpaired trades instead of one
    comparison, and the alarm it raises says nothing about the market.
    """

    def setUp(self):
        from parity_deriva.backtest.oanda import OANDABacktester
        from parity_deriva.trading.parity import ParityMonitor
        self.settings = settings_stub()
        self.api = FakeAPI()
        self.sink = Recorder()

        self.poller = EToroTransactions(setup=self.settings, pairs=['EUR_USD'],
                                        api=self.api)
        self.poller.set_queue(self.sink)
        self.simulator = OANDABacktester(setup=self.settings)
        self.simulator.set_queue(self.sink)
        self.monitor = ParityMonitor(
            setup=self.settings, instrument='EUR_USD',
            policy={'window': 10, 'min_sample': 1, 'max_outcome_mismatch': 0.5,
                    'max_slippage': None, 'max_unpaired': None,
                    'max_undecided': None, 'action': 'warn'})
        self.monitor.set_queue(self.sink)

    def test_a_synthesised_fill_carries_the_key_the_simulator_uses(self):
        """
        Both sides file a trade under the signal that produced it, so the two
        can be found. The key is a function of the candle, so it is the same
        in a replay as it was live.
        """
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        fill = [e for e in self.sink.of('TRANSACTION')
                if e.type == 'ORDER_FILL'][0]
        self.assertEqual(fill.signalNumber, SIGNAL)

    def test_the_monitor_pairs_the_two_sides(self):
        from parity_deriva.event.event import SimulatedFillEvent

        # the eToro side: an acknowledgement, then a fill and a close built
        # out of the two reads
        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        self.api.queue('trade_history', 200, [history_row()])
        self.poller.pollCloses()

        for event in list(self.sink.of('TRANSACTION')):
            self.monitor.execute_event(event)

        # the simulated side, agreeing
        self.monitor.execute_event(SimulatedFillEvent(
            {'signalNumber': SIGNAL, 'orderID': 1, 'price': 1.2005}))
        self.monitor.execute_event(SimulatedFillEvent(
            {'signalNumber': SIGNAL, 'orderID': 1, 'price': 1.2100,
             'reason': data_etoro.TAKE_PROFIT,
             'tradesClosed': [{'tradeID': 1}]}))

        self.assertEqual(self.monitor.reconciled, 1)
        self.assertEqual(self.monitor.divergences, [])
        self.assertEqual(self.monitor.undecided, 0)
        self.assertEqual(self.monitor.unpaired(), [])

    def test_a_real_disagreement_is_still_caught(self):
        """
        The inference is not so loose that it agrees with everything: a
        simulator that read the stop where the account reached the target
        still shows up.
        """
        from parity_deriva.event.event import SimulatedFillEvent

        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        self.api.queue('trade_history', 200, [history_row(close_rate=1.2100)])
        self.poller.pollCloses()
        for event in list(self.sink.of('TRANSACTION')):
            self.monitor.execute_event(event)

        self.monitor.execute_event(SimulatedFillEvent(
            {'signalNumber': SIGNAL, 'orderID': 1, 'price': 1.2005}))
        self.monitor.execute_event(SimulatedFillEvent(
            {'signalNumber': SIGNAL, 'orderID': 1, 'price': 1.1900,
             'reason': data_etoro.STOP_LOSS,
             'tradesClosed': [{'tradeID': 1}]}))

        self.assertEqual([d.kind for d in self.monitor.divergences],
                         ['outcome'])

    def test_an_undecidable_close_is_neither(self):
        from parity_deriva.event.event import SimulatedFillEvent

        self.poller.execute_event(acknowledgement())
        self.api.queue('order_lookup', 200, lookup_payload())
        self.poller.pollOrders(now=T0)
        # closed between the levels: the account reached neither
        self.api.queue('trade_history', 200, [history_row(close_rate=1.2000)])
        self.poller.pollCloses()
        for event in list(self.sink.of('TRANSACTION')):
            self.monitor.execute_event(event)

        self.monitor.execute_event(SimulatedFillEvent(
            {'signalNumber': SIGNAL, 'orderID': 1, 'price': 1.2005}))
        self.monitor.execute_event(SimulatedFillEvent(
            {'signalNumber': SIGNAL, 'orderID': 1, 'price': 1.2100,
             'reason': data_etoro.TAKE_PROFIT,
             'tradesClosed': [{'tradeID': 1}]}))

        self.assertEqual(self.monitor.divergences, [])
        self.assertEqual(self.monitor.undecided, 1)
        self.assertEqual(self.monitor.reconciled, 1)


class ExpiryReleasesTheSignalTest(unittest.TestCase):
    """
    The eToro case that made MoneyManager's stuck state unavoidable.

    On OANDA an unfilled bracket expires broker-side and the transaction
    stream says so. On eToro nothing expires and nothing is pushed, so the
    poller enforces the expiry itself - and if that cancel did not reach the
    money manager, a bracket that never triggered would stop the strategy for
    the rest of the session. The two halves have to meet.
    """

    def setUp(self):
        from parity_deriva.portfolio.moneymanager import MoneyManager
        MoneyManager.signals = {}
        MoneyManager.processed = []
        self.settings = settings_stub()
        self.api = FakeAPI()
        self.sink = Recorder()

        self.manager = MoneyManager(setup=self.settings, units=1)
        self.manager.onTrade = False
        self.manager.orderIssued = False
        self.manager.set_queue(self.sink)
        self.poller = EToroTransactions(setup=self.settings, pairs=['EUR_USD'],
                                        api=self.api)
        self.poller.set_queue(self.sink)

    def leg(self, order_id, price, units):
        from parity_deriva.event.event import SignalEvent
        signal = SignalEvent({'instrument': 'EUR_USD', 'units': units,
                              'orderType': 'STOP', 'price': price,
                              'stopLoss': 1.1900, 'takeProfit': 1.2100,
                              'signalNumber': SIGNAL, 'gtdTime': T0})
        self.manager.execute_event(signal)
        ack = acknowledgement(order_id=order_id, price=price, gtd=T0,
                              units=units)
        self.manager.execute_event(ack)
        self.poller.execute_event(ack)

    def test_a_bracket_that_never_triggered_stops_blocking(self):
        self.leg(555, 1.2010, 1)
        self.leg(556, 1.1990, -1)
        self.assertTrue(self.manager.orderIssued)

        # both legs still resting, both past their gtdTime
        self.api.queue('order_lookup', 200,
                       lookup_payload(status_id=data_etoro.STATUS_WAITING_FOR_MARKET,
                                      executions=False))
        self.poller.pollOrders(now=T0 + datetime.timedelta(minutes=1))

        cancels = self.sink.of('ORDERCANCEL')
        self.assertEqual(len(cancels), 2)
        for cancel in cancels:
            self.manager.execute_event(cancel)

        self.assertEqual(self.manager.signals, {})
        self.assertFalse(self.manager.orderIssued)

    def test_and_the_next_signal_is_accepted(self):
        self.leg(555, 1.2010, 1)
        self.leg(556, 1.1990, -1)
        self.api.queue('order_lookup', 200,
                       lookup_payload(status_id=data_etoro.STATUS_PLACED,
                                      executions=False))
        self.poller.pollOrders(now=T0 + datetime.timedelta(minutes=1))
        for cancel in self.sink.of('ORDERCANCEL'):
            self.manager.execute_event(cancel)

        from parity_deriva.event.event import SignalEvent
        self.sink.events = []
        self.manager.execute_event(SignalEvent(
            {'instrument': 'EUR_USD', 'units': 1, 'orderType': 'STOP',
             'price': 1.2050, 'stopLoss': 1.1950, 'takeProfit': 1.2150,
             'signalNumber': 'AG01:EUR_USD:H1:20180115T110000',
             'gtdTime': None}))
        self.assertEqual(len(self.sink.of('ORDER')), 1)

    def test_a_rejection_found_by_polling_releases_the_signal_too(self):
        """
        eToro accepts an order with a 200 and refuses it later, so the
        rejection arrives here rather than in the reply to the order.
        """
        self.leg(555, 1.2010, 1)
        self.api.queue('order_lookup', 200,
                       lookup_payload(status_id=data_etoro.STATUS_REJECTED,
                                      executions=False, error_code=7,
                                      error_message='market closed'))
        self.poller.pollOrders(now=T0)

        rejects = [e for e in self.sink.of('TRANSACTION')
                   if e.type == 'ORDER_REJECT']
        self.assertEqual(len(rejects), 1)
        self.manager.execute_event(rejects[0])
        self.assertEqual(self.manager.signals, {})
        self.assertFalse(self.manager.orderIssued)
