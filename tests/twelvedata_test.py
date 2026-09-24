"""
The Twelve Data provider: lib/twelvedata.py, data/twelvedata.py and the paper
account in trading/providers.py.

Nothing here touches the network. The client is given the fake `requests`
module the other broker tests use, and the poller is given a fake client, so
the two questions - "does the client count and refuse" and "does the poller
ask once per bar" - are asked separately.
"""

import datetime
import os
import tempfile
import types
import unittest
from unittest import mock

from parity_deriva.data import twelvedata as poller
from parity_deriva.data.candledb import CandleDB
from parity_deriva.lib import twelvedata
from parity_deriva.tests.helpers import FakeRequests, FakeResponse, Recorder
from parity_deriva.trading import providers


def setup_stub(**over):
    base = dict(
        # a minute limit no test reaches: the ninth call of a real limiter
        # would sleep the suite for a minute
        TWELVEDATA_API_KEY='k', TWELVEDATA_MINUTE_LIMIT=1000,
        TWELVEDATA_DAILY_LIMIT=10, TWELVEDATA_RESERVE=2,
        TWELVEDATA_POLL_DELAY=20, TWELVEDATA_SPREAD=None,
        TWELVEDATA_INSTRUMENTS={'EUR_USD': 'EUR/USD'},
        INSTRUMENT_PRECISION={'EUR_USD': 5}, DEFAULT_PRICE_PRECISION=5,
        EQUITY=1000,
        CANDLE_DB=os.path.join(tempfile.mkdtemp(), 'candles.db'))
    base.update(over)
    return types.SimpleNamespace(**base)


def series(*bars):
    """A time_series payload, newest first as the vendor sends it."""
    values = [{'datetime': when, 'open': str(o), 'high': str(o + 0.0002),
               'low': str(o - 0.0002), 'close': str(c)}
              for when, o, c in bars]
    return {'meta': {'symbol': 'EUR/USD', 'interval': '5min'},
            'values': list(reversed(values)), 'status': 'ok'}


class VocabularyTest(unittest.TestCase):

    def test_interval_refuses_what_the_vendor_has_not(self):
        self.assertEqual(twelvedata.interval('M5'), '5min')
        self.assertEqual(twelvedata.interval('H1'), '1h')
        with self.assertRaises(twelvedata.TwelveDataError):
            twelvedata.interval('H3')

    def test_symbol_is_configuration_not_derivation(self):
        self.assertEqual(twelvedata.symbol('EUR_USD', setup_stub()), 'EUR/USD')
        with self.assertRaises(twelvedata.TwelveDataError):
            twelvedata.symbol('DE30_EUR', setup_stub())

    def test_candle_time_is_naive_utc(self):
        self.assertEqual(twelvedata.candleTime('2026-09-24 10:25:00'),
                         datetime.datetime(2026, 9, 24, 10, 25))


class ClientTest(unittest.TestCase):

    def setUp(self):
        self.setup = setup_stub()
        self.db = CandleDB(self.setup.CANDLE_DB)

    def client(self, *responses):
        fake = FakeRequests(*responses)
        patch = mock.patch.object(twelvedata, 'requests', fake)
        patch.start()
        self.addCleanup(patch.stop)
        return twelvedata.TwelveDataAPI(setup=self.setup, db=self.db), fake

    def test_a_missing_key_refuses_to_be_built(self):
        with self.assertRaises(twelvedata.TwelveDataError):
            twelvedata.TwelveDataAPI(setup=setup_stub(TWELVEDATA_API_KEY=''), db=self.db)

    def test_every_call_is_counted_and_carries_the_key_and_utc(self):
        api, fake = self.client(FakeResponse(series(('2026-09-24 10:25:00', 1.1, 1.1))))
        status, payload, rows = api.timeSeries('EUR_USD', 'M5', outputsize=2)
        self.assertEqual(status, 200)
        self.assertEqual([r['datetime'] for r in rows], ['2026-09-24 10:25:00'])
        sent = fake.last['params']
        self.assertEqual((sent['symbol'], sent['interval'], sent['outputsize'],
                          sent['timezone'], sent['apikey']),
                         ('EUR/USD', '5min', 2, 'UTC', 'k'))
        self.assertEqual(api.spent(), 1)
        self.assertEqual(api.budget()['calls_today'], 1)
        self.assertIsNotNone(api.budget()['last_call'])

    def test_rows_come_oldest_first(self):
        api, fake = self.client(FakeResponse(series(
            ('2026-09-24 10:20:00', 1.1, 1.1), ('2026-09-24 10:25:00', 1.2, 1.2))))
        rows = api.timeSeries('EUR_USD', 'M5')[2]
        self.assertEqual([r['datetime'] for r in rows],
                         ['2026-09-24 10:20:00', '2026-09-24 10:25:00'])

    def test_the_daily_budget_stops_the_poller_and_keeps_the_reserve(self):
        """
        Limit 10, reserve 2: the poller may spend 8. The ninth call is not
        made at all - nothing is sent - and says why; a history call may
        still take the reserve.
        """
        api, fake = self.client(FakeResponse(series(('2026-09-24 10:25:00', 1.1, 1.1))))
        for _ in range(8):
            self.assertEqual(api.get('time_series', symbol='EUR/USD')[0], 200)
        status, payload = api.get('time_series', symbol='EUR/USD')
        self.assertIsNone(status)
        self.assertEqual(payload['code'], 'budget')
        self.assertEqual(len(fake.sent), 8)
        self.assertEqual(api.get('time_series', reserve=False, symbol='EUR/USD')[0], 200)
        self.assertEqual(len(fake.sent), 9)

    def test_a_vendor_error_is_returned_not_raised(self):
        api, fake = self.client(FakeResponse(
            {'code': 404, 'message': 'symbol invalid', 'status': 'error'}, status=404))
        status, payload, rows = api.timeSeries('EUR_USD', 'M5')
        self.assertEqual((status, rows), (404, []))
        self.assertEqual(payload['status'], 'error')

    def test_a_network_failure_is_counted_too(self):
        """
        The vendor may well have counted a request whose answer never came
        back, so the local count errs on the side of having spent it.
        """
        api, fake = self.client(FakeResponse(series()))
        fake.raise_on_send = True
        self.assertEqual(api.get('time_series', symbol='EUR/USD'), (None, None))
        self.assertEqual(api.spent(), 1)

    def test_a_429_penalises_the_minute(self):
        api, fake = self.client(FakeResponse({'code': 429, 'status': 'error',
                                              'message': 'slow down'}, status=429))
        api.get('time_series', symbol='EUR/USD')
        self.assertGreater(api.minute.until, 0)


class FakeAPI(object):
    """The client as the poller sees it: canned rows, calls counted."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def timeSeries(self, instrument, granularity, outputsize=2, start=None, end=None,
                   reserve=True):
        self.calls.append({'instrument': instrument, 'granularity': granularity,
                           'outputsize': outputsize, 'start': start, 'end': end,
                           'reserve': reserve})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if answer is None:
            return None, None, []
        return 200, answer, sorted(answer.get('values') or [], key=lambda r: r['datetime'])


class PollerTest(unittest.TestCase):

    T = datetime.datetime(2026, 9, 24, 10, 30, 25)

    def poller(self, api, **over):
        source = poller.TwelveDataCandles(setup=setup_stub(**over), pairs=['EUR_USD'],
                                          granularity='M5', api=api)
        self.sink = Recorder()
        source.set_queue(self.sink)
        return source

    def test_only_closed_bars_are_emitted_and_only_once(self):
        api = FakeAPI(series(('2026-09-24 10:25:00', 1.1, 1.1),
                             ('2026-09-24 10:30:00', 1.2, 1.2)))
        source = self.poller(api)
        # at 10:30:25 the 10:30 bar is still forming
        self.assertEqual(source.poll('EUR_USD', now=self.T), 1)
        candles = self.sink.of('CANDLE')
        self.assertEqual([c.time for c in candles], [datetime.datetime(2026, 9, 24, 10, 25)])
        self.assertEqual((candles[0].instrument, candles[0].granularity), ('EUR_USD', 'M5'))
        self.assertEqual(candles[0].mid['c'], 1.1)
        self.assertIsNone(candles[0].bid)
        # asked again with the same answer, the same bar is not sent twice
        self.assertEqual(source.poll('EUR_USD', now=self.T), 0)
        self.assertEqual(api.calls[-1]['outputsize'], 2)

    def test_a_bar_not_there_yet_is_picked_up_next_time_for_nothing_extra(self):
        """
        The vendor shows a new bar a minute or two after it closes. The
        poller does not ask again for it - that is what would spend the
        budget - but the next boundary's two-bar answer carries it.
        """
        late = series(('2026-09-24 10:20:00', 1.0, 1.0), ('2026-09-24 10:25:00', 1.1, 1.1))
        then = series(('2026-09-24 10:25:00', 1.1, 1.1), ('2026-09-24 10:30:00', 1.2, 1.2))
        api = FakeAPI(late, then, then)
        source = self.poller(api)
        source.poll('EUR_USD', now=datetime.datetime(2026, 9, 24, 10, 30, 25))
        # 10:30 closed at 10:35 but the vendor still answers up to 10:25
        source.poll('EUR_USD', now=datetime.datetime(2026, 9, 24, 10, 35, 25))
        source.poll('EUR_USD', now=datetime.datetime(2026, 9, 24, 10, 40, 25))
        self.assertEqual([c.time.minute for c in self.sink.of('CANDLE')], [20, 25, 30])
        self.assertEqual(len(api.calls), 3)

    def test_the_schedule_is_the_next_close_plus_the_delay(self):
        source = self.poller(FakeAPI(series()))
        self.assertTrue(source.due('EUR_USD', now=self.T))
        self.assertEqual(source.schedule('EUR_USD', now=self.T),
                         datetime.datetime(2026, 9, 24, 10, 35, 20))
        self.assertFalse(source.due('EUR_USD', now=datetime.datetime(2026, 9, 24, 10, 35, 19)))
        self.assertTrue(source.due('EUR_USD', now=datetime.datetime(2026, 9, 24, 10, 35, 20)))

    def test_a_revised_bar_is_logged_and_not_re_sent(self):
        first = series(('2026-09-24 10:25:00', 1.1, 1.1))
        again = series(('2026-09-24 10:25:00', 1.1, 1.1009))
        source = self.poller(FakeAPI(first, again, again))
        source.poll('EUR_USD', now=self.T)
        with self.assertLogs('parity_deriva.trading.trading', level='WARNING') as logs:
            self.assertEqual(source.poll('EUR_USD', now=self.T), 0)
        self.assertTrue(any('REVISED' in line for line in logs.output))
        self.assertEqual(len(self.sink.of('CANDLE')), 1)

    def test_a_spread_gives_bid_and_ask_either_side_of_the_served_price(self):
        source = self.poller(FakeAPI(series(('2026-09-24 10:25:00', 1.1, 1.1))),
                             TWELVEDATA_SPREAD=0.0002)
        source.poll('EUR_USD', now=self.T)
        candle = self.sink.of('CANDLE')[0]
        self.assertAlmostEqual(candle.ask['c'], 1.1001)
        self.assertAlmostEqual(candle.bid['c'], 1.0999)

    def test_history_is_one_call_that_may_take_the_reserve(self):
        api = FakeAPI(series(('2026-09-24 10:20:00', 1.0, 1.0),
                             ('2026-09-24 10:25:00', 1.1, 1.1)))
        source = poller.TwelveDataCandles(
            setup=setup_stub(), pairs=['EUR_USD'], granularity='M5', api=api,
            dtfrom=datetime.datetime(2026, 9, 24, 9, 0),
            dtto=datetime.datetime(2026, 9, 24, 10, 30))
        sink = Recorder()
        source.set_queue(sink)
        source.stream_to_queue()
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0]['outputsize'], poller.MAX_BARS)
        self.assertFalse(api.calls[0]['reserve'])
        self.assertEqual(api.calls[0]['start'], datetime.datetime(2026, 9, 24, 9, 0))
        self.assertEqual(sink.statuses(), ['STARTED', 'DONE'])
        self.assertEqual(len(sink.of('CANDLE')), 2)

    def test_a_failed_request_is_an_error_status_and_a_budget_refusal_is_not(self):
        source = self.poller(FakeAPI(None))
        source.poll('EUR_USD', now=self.T)
        self.assertEqual(self.sink.statuses(), ['ERROR'])
        source = self.poller(FakeAPI({'status': 'error', 'code': 'budget'}))
        source.poll('EUR_USD', now=self.T)
        self.assertEqual(self.sink.statuses(), [])


class ProviderTest(unittest.TestCase):

    def test_it_is_the_paper_account_and_says_so(self):
        p = providers.get_provider('twelvedata', setup=setup_stub())
        self.assertTrue(p.capabilities.paper)
        self.assertFalse(p.capabilities.bid_ask_candles)
        self.assertTrue(p.configured())
        self.assertFalse(providers.get_provider(
            'twelvedata', setup=setup_stub(TWELVEDATA_API_KEY='')).configured())
        account = p.accounts()[0]
        self.assertEqual((account['id'], account['demo'], account['currency'],
                          account['balance']), ('paper', True, None, 1000.0))

    def test_a_spread_grants_bid_and_ask_to_this_instance_only(self):
        p = providers.get_provider('twelvedata', setup=setup_stub(TWELVEDATA_SPREAD=0.0001))
        self.assertTrue(p.capabilities.bid_ask_candles)
        self.assertFalse(providers.TwelveDataProvider.capabilities.bid_ask_candles)

    def test_the_simulator_is_the_broker(self):
        from parity_deriva.backtest.oanda import OANDABacktester
        from parity_deriva.backtest.offline import SimulatedBroker
        p = providers.get_provider('twelvedata', setup=setup_stub())
        execution = p.execution(granularity='M5', sized=True)
        self.assertIsInstance(execution, OANDABacktester)
        # a float: EQUITY may be a Decimal and the simulator adds floats
        self.assertEqual(execution.balance, 1000.0)
        self.assertIsInstance(execution.balance, float)
        self.assertIsInstance(p.transactions(), SimulatedBroker)
        with self.assertRaises(providers.CapabilityError):
            p.simulator(granularity='M5')

    def test_a_strategy_reading_bid_and_ask_is_refused_without_a_spread(self):
        with self.assertRaises(providers.CapabilityError):
            providers.require(providers.get_provider('twelvedata', setup=setup_stub()),
                              'bid_ask_candles')


if __name__ == '__main__':
    unittest.main()
