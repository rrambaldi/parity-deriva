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

    def respond(self, key, status=200, payload=None):
        """
        The answer to every later call on this route, replacing what was
        queued. queue() appends, so a second queue() on the same route is
        answered only after the first has been used up - which is the wrong
        shape for a test that changes what the account holds.
        """
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

    def delete(self, key, parts=(), body=None):
        return self._call('DELETE', key, parts, None, body)

    def noteAllowance(self, payload):
        self.noted.append(payload)
        return True

    def of(self, key):
        return [c for c in self.calls if c['key'] == key]


def confirmation(status='OPEN', deal='OPENED', dealId='D1', level=1.16512,
                 stop=1.1600, limit=1.1700, distances=None, direction='BUY',
                 size=1):
    """
    GET /confirms, in the shape the demo account answered with.

    Both status vocabularies are here because both are real: the top-level
    one is the position's - OPEN, CLOSED - and the one in affectedDeals is the
    deal's - OPENED, FULLY_CLOSED. A working order reports the same pair as a
    filled market deal, and carries distances where a fill carries levels.
    """
    return {
        'date': '2018-01-15T10:00:01.000',
        'status': status,
        'reason': 'SUCCESS',
        'dealStatus': 'ACCEPTED',
        'epic': 'CS.D.EURUSD.MINI.IP',
        'expiry': '-',
        'dealReference': 'REF1',
        'dealId': dealId,
        'affectedDeals': [{'dealId': dealId, 'status': deal}],
        'level': level,
        'size': size,
        'direction': direction,
        'stopLevel': stop,
        'limitLevel': limit,
        'stopDistance': distances,
        'limitDistance': distances,
        'guaranteedStop': False,
        'trailingStop': False,
        'profit': None,
        'profitCurrency': None,
    }


def close_activity(dealId='D1', level=1.17000, when='2018-01-15T11:00:00'):
    """
    One row of GET /history/activity, as the demo account serves it for a
    position that has just closed. This is the prompt record; the transaction
    history carries the profit and can arrive minutes later.
    """
    return {
        'date': when,
        'epic': 'CS.D.EURUSD.MINI.IP',
        'dealId': 'DCLOSE1',
        'type': 'POSITION',
        'status': 'ACCEPTED',
        'description': 'Posizioni chiuse: %s' % dealId,
        'details': {
            'dealReference': 'CLOSEREF',
            'actions': [{'actionType': 'POSITION_CLOSED',
                         'affectedDealId': dealId}],
            'marketName': 'EUR/USD Mini',
            'size': 1, 'direction': 'SELL', 'level': level,
        },
    }


def position_row(dealId='D1', level=1.16680, stop=1.1600, limit=1.1700,
                 size=1, direction='BUY', reference='REF1'):
    """
    One row of GET /positions, as the demo account serves it.

    The dealId is the working order's own - that is what makes the join
    possible - and createdDate is local while createdDateUTC is not, which is
    why only the second is read.
    """
    return {
        'position': {
            'contractSize': 10000.0,
            'createdDate': '2018/01/15 11:00:02:000',
            'createdDateUTC': '2018-01-15T10:00:02',
            'dealId': dealId,
            'dealReference': reference,
            'size': size,
            'direction': direction,
            'limitLevel': limit,
            'level': level,
            'currency': 'USD',
            'controlledRisk': False,
            'stopLevel': stop,
        },
        'market': {'instrumentName': 'EUR/USD Mini', 'epic':
                   'CS.D.EURUSD.MINI.IP', 'scalingFactor': 10000},
    }


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
            'EUR_USD': {'epic': 'X', 'priceDivisor': 10000}})
        self.assertEqual(scale('EUR_USD', setup), 10000.0)

    def test_igs_own_scaling_factor_is_not_a_price_divisor(self):
        """
        Measured on the demo account: EUR/USD reports scalingFactor 10000 and
        quotes 1.14625. The field relates distances in points to price units -
        the same market's minimum stop is 2.0 points, which is 0.0002 - and
        using it as a divisor would put every level four decimal places from
        the market. So the override is keyed 'priceDivisor', and pasting IG's
        field into an entry does nothing at all.
        """
        setup = settings_stub(IG_INSTRUMENTS={
            'EUR_USD': {'epic': 'X', 'scalingFactor': 10000}})
        self.assertEqual(scale('EUR_USD', setup), 1.0)

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
        """
        Was: the wall clock was copied out verbatim, on the belief that IG
        read it in the account's timezone. Now: it is converted to UTC, which
        is the clock the demo account was measured on - an expiry ten minutes
        behind UTC was refused as being in the past, while one thirty minutes
        ahead of UTC but behind both London and the account's own clock was
        accepted. A naive datetime is the machine's local time, so the
        expected value is computed the same way rather than written out: the
        test would otherwise pass only on a machine set to UTC.
        """
        when = datetime.datetime(2018, 1, 15, 23, 59, 0)
        expected = when.astimezone(datetime.timezone.utc)
        self.assertEqual(goodTillDate(when),
                         expected.strftime('%Y/%m/%d %H:%M:%S'))

    def test_an_expiry_with_an_offset_is_converted_not_copied(self):
        """
        Rome in winter is an hour ahead: an order meant to rest until 23:59
        there has to reach IG as 22:59, or it dies an hour early.
        """
        rome = datetime.timezone(datetime.timedelta(hours=1))
        when = datetime.datetime(2018, 1, 15, 23, 59, 0, tzinfo=rome)
        self.assertEqual(goodTillDate(when), '2018/01/15 22:59:00')


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

    def test_a_configured_divisor_is_applied(self):
        setup = settings_stub(IG_INSTRUMENTS={
            'EUR_USD': {'epic': 'X', 'priceDivisor': 10000}})
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

        Was: the expiry was expected verbatim, '2018/01/15 23:59:00', which
        pinned the wall clock being copied out. IG reads the field in UTC -
        measured, see lib/ig.goodTillDate - and a strategy's gtdTime is the
        machine's local time, so the two differ by the machine's offset on
        every machine that is not on UTC. The expected value is computed the
        same way the code converts it, which is the only form of this
        assertion that is true anywhere.
        """
        _route, body = self.handler.body(self.order())
        expected = datetime.datetime(2018, 1, 15, 23, 59, 0).astimezone(
            datetime.timezone.utc).strftime('%Y/%m/%d %H:%M:%S')
        self.assertEqual(body['timeInForce'], 'GOOD_TILL_DATE')
        self.assertEqual(body['goodTillDate'], expected)

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

    def test_cancel_with_nothing_to_go_on_is_not_sent(self):
        """
        IG deletes a working order by dealId, and the acknowledgement carried
        only the reference. With no instrument and no level there is nothing
        to look the order up by either, so the delete is refused rather than
        aimed at a resource named by the wrong identifier.
        """
        event = OrderCancelEvent({'dealReference': 'REF123'})
        self.assertIsNone(self.handler.cancelOrder(event))
        self.assertEqual(self.api.of('cancel_order'), [])

    def working_order(self, dealId='DEAL1', level=11710.0,
                      ig_epic='IX.D.DAX.IFMM.IP'):
        return {'workingOrderData': {'dealId': dealId, 'epic': ig_epic,
                                     'orderLevel': level, 'orderType': 'STOP',
                                     'direction': 'BUY', 'orderSize': 1}}

    def test_a_cancel_that_knows_only_the_reference_finds_the_order(self):
        """
        This is how the money manager cancels the losing leg: it names the
        order by the id it was given, which for IG is the deal reference, and
        GET /workingorders does not carry that. So the order is found on the
        epic and the level - the fields both sides have - and the delete goes
        out with the dealId IG does publish.

        Until this existed the cancel was logged and dropped, which on a
        straddle leaves the abandoned leg live on the account.
        """
        self.api.queue('workingorders', 200,
                       {'workingOrders': [self.working_order()]})
        self.api.queue('cancel_order', 200, {'dealReference': 'REF999'})
        status = self.handler.cancelOrder(OrderCancelEvent(
            {'orderID': 'PDabc', 'instrument': 'DE30_EUR', 'price': 11710.0}))
        self.assertEqual(status, 200)
        self.assertEqual(self.api.of('cancel_order')[0]['parts'], ('DEAL1',))

    def test_two_orders_at_one_level_cancel_neither(self):
        """
        Cancelling the wrong leg leaves the account holding the position the
        strategy meant to abandon.
        """
        self.api.queue('workingorders', 200, {'workingOrders': [
            self.working_order(dealId='DEAL1'),
            self.working_order(dealId='DEAL2')]})
        self.assertIsNone(self.handler.cancelOrder(OrderCancelEvent(
            {'orderID': 'PDabc', 'instrument': 'DE30_EUR', 'price': 11710.0})))
        self.assertEqual(self.api.of('cancel_order'), [])

    def test_an_order_on_another_market_is_not_the_one_to_cancel(self):
        self.api.queue('workingorders', 200, {'workingOrders': [
            self.working_order(ig_epic='CS.D.EURUSD.CEBM.IP')]})
        self.assertIsNone(self.handler.cancelOrder(OrderCancelEvent(
            {'orderID': 'PDabc', 'instrument': 'DE30_EUR', 'price': 11710.0})))
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
        """
        Was: the confirmation was written with status 'OPENED', which IG never
        sends at that level, and the test passed on a payload the broker does
        not produce. Now: the payload is the one a live deal on the demo
        account came back with - 'OPEN' at the top, 'OPENED' only inside
        affectedDeals.
        """
        self.acknowledge()
        self.api.queue('confirm', 200, confirmation(status='OPEN', deal='OPENED'))
        self.handler.pollDeals()
        fills = self.fills()
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].price, 1.16512)
        self.assertEqual(fills[0].reason, 'MARKET_ORDER')
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

        Was: the confirmation was given a status of None, so any reading of
        that field kept the two apart. Now: it carries exactly what a live
        working order came back with - the same 'OPEN' / 'OPENED' a filled
        market deal reports - because that is the trap. Only the endpoint the
        order went to tells them apart.
        """
        self.acknowledge(orderType='STOP')
        self.api.queue('confirm', 200,
                       confirmation(dealId='D2', level=1.16651, stop=None,
                                    limit=None, distances=30.0))
        self.handler.pollDeals()
        self.assertEqual(self.fills(), [])
        self.assertIn('D2', self.handler.positions)
        self.assertTrue(self.handler.positions['D2']['resting'])

    def test_a_working_order_that_has_triggered_becomes_a_fill(self):
        """
        Nothing published a fill for a triggered order before this existed:
        the position stayed marked resting for good, so the entry was never
        reported and pollCloses - which skips a resting position - would have
        dropped the close as well. Every strategy here enters on a STOP.

        The join is on the dealId, which the position IG opens keeps from the
        working order, as does the dealReference this project chose.
        """
        self.acknowledge(orderType='STOP', price=1.16651)
        self.api.queue('confirm', 200,
                       confirmation(dealId='D2', level=1.16651, stop=None,
                                    limit=None, distances=30.0))
        self.handler.pollDeals()

        self.api.queue('positions', 200, {'positions': [
            position_row(dealId='D2', level=1.16680, stop=1.16351,
                         limit=1.16951)]})
        sent = self.handler.pollFills()

        self.assertEqual(sent, 1)
        fill = self.fills()[0]
        # the level the market reached, not the level that was asked for: a
        # stop that gapped fills worse than its own price
        self.assertEqual(fill.price, 1.16680)
        self.assertEqual(fill.reason, 'STOP_ORDER')
        self.assertEqual(fill.dealId, 'D2')
        self.assertEqual(fill.signalNumber,
                         'AG01:EUR_USD:H1:20180115T010000')
        self.assertFalse(self.handler.positions['D2']['resting'])

    def test_an_order_still_resting_publishes_nothing(self):
        self.acknowledge(orderType='STOP')
        self.api.queue('confirm', 200,
                       confirmation(dealId='D2', level=1.16651, stop=None,
                                    limit=None, distances=30.0))
        self.handler.pollDeals()
        self.api.queue('positions', 200, {'positions': []})
        self.assertEqual(self.handler.pollFills(), 0)
        self.assertEqual(self.fills(), [])
        self.assertTrue(self.handler.positions['D2']['resting'])

    def test_a_triggered_order_is_then_followed_to_its_close(self):
        """
        The whole path an entry takes at IG: accepted, resting, triggered,
        gone. The close was unreachable while the position stayed resting.
        """
        self.acknowledge(orderType='STOP', price=1.16651)
        self.api.queue('confirm', 200,
                       confirmation(dealId='D2', level=1.16651, stop=None,
                                    limit=None, distances=30.0))
        self.handler.pollDeals()
        self.api.queue('positions', 200, {'positions': [
            position_row(dealId='D2', level=1.16680, stop=1.16351,
                         limit=1.16951)]})
        self.handler.pollFills()

        self.api.respond('positions', 200, {'positions': []})
        self.api.queue('activity', 200, {'activities': [
            close_activity(dealId='D2', level=1.16951)]})
        self.api.queue('transactions', 200, {'transactions': [
            {'openLevel': 1.16680, 'closeLevel': 1.16951,
             'profitAndLoss': 'E27.10', 'dateUtc': '2018-01-15T12:00:00'}]})
        sent = self.handler.pollCloses()

        self.assertEqual(sent, 1)
        close = self.fills()[-1]
        self.assertEqual(close.price, 1.16951)
        self.assertEqual(close.reason, TAKE_PROFIT)
        self.assertEqual(close.pl, 27.10)

    def test_a_confirmation_that_closed_something_is_not_an_entry(self):
        """
        Nothing here sends a closing deal, so a confirmation that says one
        happened belongs to something else - the platform, a margin call -
        and is not a position this stack opened. The word that says so is in
        affectedDeals; the top level says CLOSED.
        """
        self.acknowledge()
        self.api.queue('confirm', 200,
                       confirmation(status='CLOSED', deal='FULLY_CLOSED'))
        self.handler.pollDeals()
        self.assertEqual(self.fills(), [])
        self.assertEqual(self.handler.positions, {})

    def test_one_read_of_the_positions_serves_both_polls(self):
        """
        A cycle runs every few seconds against a 30-a-minute allowance, and
        the open positions cannot change between two reads in the same cycle.
        """
        self.acknowledge(orderType='STOP')
        self.api.queue('confirm', 200,
                       confirmation(dealId='D2', level=1.16651, stop=None,
                                    limit=None, distances=30.0))
        self.handler.pollDeals()
        self.api.queue('positions', 200, {'positions': [
            position_row(dealId='D2', level=1.16680)]})
        before = len(self.api.of('positions'))
        self.handler.poll()
        self.assertEqual(len(self.api.of('positions')) - before, 1)

    def test_a_cancelled_working_order_is_not_watched_for_a_fill(self):
        """
        A deleted order will never trigger, so looking for it among the open
        positions for the rest of the session is work with one answer.
        """
        self.acknowledge(orderType='STOP')
        self.api.queue('confirm', 200,
                       confirmation(dealId='D2', level=1.16651, stop=None,
                                    limit=None, distances=30.0))
        self.handler.pollDeals()
        self.handler.execute_event(OrderCancelEvent({'orderID': 'REF1',
                                                     'price': 1.16651}))
        self.assertEqual(self.handler.positions, {})

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

    def open_position(self, **over):
        """A position this handler is already following, as a fill left it."""
        data = {'dealId': 'D1', 'dealReference': 'REF1', 'signalNumber': 'sig',
                'instrument': 'EUR_USD', 'units': 1, 'openLevel': 1.1650,
                'stopLoss': 1.1600, 'takeProfit': 1.1700, 'opened': T0,
                'orderType': 'STOP', 'resting': False}
        data.update(over)
        self.handler.positions[data['dealId']] = data
        return data

    def test_the_close_is_reported_before_the_profit_is_known(self):
        """
        The transaction history is the only route with a profit on it and it
        lags - measured at over a minute on the demo account, where an
        earlier close had landed within five seconds. The activity is
        current, so the close is published as soon as it is recorded, with
        the level that decides the leg and without a profit figure. A missing
        P&L is not a flat trade, and the event says None rather than zero.
        """
        self.open_position()
        self.api.queue('positions', 200, {'positions': []})
        self.api.queue('activity', 200, {'activities': [close_activity()]})
        self.api.queue('transactions', 200, {'transactions': []})

        self.assertEqual(self.handler.pollCloses(), 1)
        close = self.fills()[0]
        self.assertEqual(close.price, 1.17)
        self.assertEqual(close.reason, TAKE_PROFIT)
        self.assertIsNone(close.pl)

    def test_a_position_gone_with_no_closing_deal_yet_is_asked_after_again(self):
        """
        Missing from the open positions is not a level. Publishing here would
        put a close in the ledger with no price - and an absent price becomes
        0.0 on the way into an event.
        """
        self.open_position()
        self.api.queue('positions', 200, {'positions': []})
        self.api.queue('activity', 200, {'activities': []})
        self.api.queue('transactions', 200, {'transactions': []})

        self.assertEqual(self.handler.pollCloses(), 0)
        self.assertEqual(self.fills(), [])
        self.assertIn('D1', self.handler.positions)

    def test_a_close_that_is_never_recorded_is_published_without_a_price(self):
        """
        Giving up eventually, loudly, and without inventing a level: the
        trade did close, and the money manager is waiting to hear so.
        """
        self.open_position()
        self.api.respond('positions', 200, {'positions': []})
        self.api.respond('activity', 200, {'activities': []})
        self.api.respond('transactions', 200, {'transactions': []})

        for _ in range(self.handler.max_attempts):
            self.handler.pollCloses()

        closes = self.fills()
        self.assertEqual(len(closes), 1)
        self.assertFalse(closes[0].has_attr('price'))
        self.assertEqual(closes[0].reason, UNKNOWN)
        self.assertEqual(self.handler.positions, {})

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

    def test_a_level_the_history_sent_as_text_is_published_as_a_number(self):
        """
        IG's transaction history quotes openLevel and closeLevel as strings
        while every other route sends numbers. A price that reached the
        ledger as text is one nothing downstream can subtract.
        """
        position = {'dealId': 'D1', 'dealReference': 'REF1',
                    'signalNumber': 'sig', 'instrument': 'EUR_USD',
                    'units': 1, 'openLevel': 1.1650, 'stopLoss': 1.1600,
                    'takeProfit': 1.1700, 'opened': T0, 'resting': False}
        self.handler.reportClose(position, {'closeLevel': '1.17000',
                                            'openLevel': '1.16500',
                                            'profitAndLoss': 'E5.00'})
        close = self.fills()[0]
        self.assertEqual(close.price, 1.17)
        self.assertEqual(close.openLevel, 1.165)
        self.assertEqual(close.reason, TAKE_PROFIT)

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
