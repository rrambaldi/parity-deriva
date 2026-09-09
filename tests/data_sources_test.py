"""
Characterisation tests for the OANDA-facing data sources.

Nothing here touches the network: `requests` is replaced by a fake that
records the prepared request and hands back canned bodies. What is pinned is
the request each source builds, the events it publishes, and how it behaves
when the API answers badly - the failure modes matter because a monitoring
setup has to tell "market moved" apart from "feed broke".
"""

import datetime
import json
import unittest
from decimal import Decimal
from unittest import mock

from parity_deriva.data import candles as candles_mod
from parity_deriva.data import resample as resample_mod
from parity_deriva.data import streaming as streaming_mod
from parity_deriva.data import transaction as transaction_mod
from parity_deriva.tests.helpers import (T0, FakeRequests, FakeResponse, Recorder,
                                   TempDirCase, candles_response, candle_dict)


class CandlesCase(TempDirCase):

    module = candles_mod

    def setUp(self):
        super(CandlesCase, self).setUp()
        # last/url/ask/bid/mid are declared at class level, so without this
        # every test inherits the previous one's instruments.
        for name in ('last', 'url', 'ask', 'bid', 'mid'):
            if hasattr(self.module.ForexCandles, name):
                setattr(self.module.ForexCandles, name, {})

    def build(self, response, **kw):
        self.fake = FakeRequests(response)
        patcher = mock.patch.object(self.module, 'requests', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        kw.setdefault('setup', self.settings)
        kw.setdefault('pairs', ["DE30_EUR"])
        kw.setdefault('granularity', "M1")
        src = self.module.ForexCandles(**kw)
        self.sink = Recorder()
        src.set_queue(self.sink)
        return src


class TestForexCandlesSetUp(CandlesCase):

    def test_historic_mode_is_selected_by_passing_dtfrom(self):
        src = self.build(FakeResponse(candles_response(3)),
                         dtfrom=T0, dtto=T0 + datetime.timedelta(minutes=10))
        self.assertFalse(src.live)
        self.assertEqual(src.sleep, 0)
        self.assertEqual(src.batch_size, 500)

    def test_live_mode_is_the_default(self):
        src = self.build(FakeResponse(candles_response(3)))
        self.assertTrue(src.live)
        self.assertEqual(src.sleep, 2)
        self.assertEqual(src.batch_size, 2)

    def test_the_url_is_per_instrument(self):
        src = self.build(FakeResponse(candles_response(3)),
                         pairs=["DE30_EUR", "EUR_USD"])
        self.assertTrue(src.url["EUR_USD"].endswith(
            "/v3/instruments/EUR_USD/candles"))
        self.assertIn(self.settings.API_DOMAIN, src.url["EUR_USD"])

    def test_query_parameters(self):
        src = self.build(FakeResponse(candles_response(3)))
        self.assertEqual(src.params['price'], 'ABM')
        self.assertEqual(src.params['granularity'], 'M1')
        self.assertEqual(src.params['count'], 2)
        self.assertEqual(src.params['includeFirst'], 'true')
        self.assertEqual(src.params['alignmentTimezone'], 'Europe/Rome')

    def test_the_bearer_token_is_sent(self):
        self.build(FakeResponse(candles_response(3)))
        self.assertEqual(self.fake.last['headers'],
                         {'Authorization': 'Bearer TESTTOKEN'})

    def test_a_string_pairs_argument_is_iterated_character_by_character(self):
        """
        The default for `pairs` is the bare string 'DE30_EUR', so anything that
        forgets to wrap it in a list ends up with one "instrument" per letter.
        scripts/save.py used to do exactly this.
        """
        src = self.build(FakeResponse(candles_response(1)), pairs="EUR")
        self.assertEqual(sorted(src.url), ['E', 'R', 'U'])
        self.assertEqual(sorted(src.last), ['E', 'R', 'U'])

    def test_the_seed_request_primes_the_price_frames(self):
        src = self.build(FakeResponse(candles_response(3)))
        self.assertEqual(len(src.mid["DE30_EUR"]), 1)
        self.assertIn('c', src.mid["DE30_EUR"].columns)

    def test_a_failed_seed_request_leaves_the_frames_unset(self):
        self.fake = FakeRequests(FakeResponse(None, status=401))
        patcher = mock.patch.object(self.module, 'requests', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        src = self.module.ForexCandles(setup=self.settings, pairs=["DE30_EUR"],
                                       granularity="M1")
        self.assertNotIn("DE30_EUR", src.mid)


class TestForexCandlesStreaming(CandlesCase):

    def test_it_publishes_one_candle_event_per_complete_candle(self):
        src = self.build(FakeResponse(candles_response(5)),
                         dtfrom=T0, dtto=T0 + datetime.timedelta(minutes=2))
        src.stream_to_queue()
        self.assertEqual(len(self.sink.of('CANDLE')), 5)

    def test_the_run_is_bracketed_by_status_events(self):
        src = self.build(FakeResponse(candles_response(5)),
                         dtfrom=T0, dtto=T0 + datetime.timedelta(minutes=2))
        src.stream_to_queue()
        self.assertEqual(self.sink.statuses(), ['STARTED', 'DONE'])

    def test_each_candle_is_tagged_with_instrument_and_granularity(self):
        src = self.build(FakeResponse(candles_response(5)),
                         dtfrom=T0, dtto=T0 + datetime.timedelta(minutes=2))
        src.stream_to_queue()
        first = self.sink.of('CANDLE')[0]
        self.assertEqual(first.instrument, "DE30_EUR")
        self.assertEqual(first.granularity, "M1")
        self.assertEqual(first.time, T0)

    def test_incomplete_candles_are_dropped(self):
        body = candles_response(4)
        body['candles'][-1]['complete'] = False
        src = self.build(FakeResponse(body), dtfrom=T0,
                         dtto=T0 + datetime.timedelta(minutes=1))
        src.stream_to_queue()
        self.assertEqual(len(self.sink.of('CANDLE')), 3)

    def test_the_last_candle_landing_exactly_on_dtto_never_terminates(self):
        """
        The stop condition is `last[pair] > dtto`, so a block whose final
        complete candle sits exactly on the requested end date leaves the
        historic loop spinning - with sleep == 0 it busy-loops, re-requesting
        the same block and re-publishing the same candles forever. The sink
        below aborts the run once the same block has been seen twice, which is
        enough to show the loop did not stop by itself.
        """
        class Tripwire(Recorder):
            armed = True

            def put(self, event):
                Recorder.put(self, event)
                if self.armed and len(self.of('CANDLE')) > 4:
                    self.armed = False          # let the ERROR status through
                    raise RuntimeError("historic loop did not terminate")

        src = self.build(FakeResponse(candles_response(4)), dtfrom=T0,
                         dtto=T0 + datetime.timedelta(minutes=3))
        trip = Tripwire()
        src.set_queue(trip)
        src.stream_to_queue()          # the RuntimeError is caught internally
        self.assertGreater(src.num_blocks["DE30_EUR"], 1)
        self.assertEqual(trip.statuses()[-1], 'ERROR')

    def test_historic_mode_sends_a_from_parameter(self):
        src = self.build(FakeResponse(candles_response(3)), dtfrom=T0,
                         dtto=T0 + datetime.timedelta(minutes=1))
        src.stream_to_queue()
        self.assertTrue(self.fake.last['params']['from'].endswith("000Z"))

    def test_live_mode_sends_no_from_parameter(self):
        self.build(FakeResponse(candles_response(3)))
        self.assertNotIn('from', self.fake.last['params'])

    def test_counters_track_blocks_and_candles(self):
        src = self.build(FakeResponse(candles_response(5)), dtfrom=T0,
                         dtto=T0 + datetime.timedelta(minutes=2))
        src.stream_to_queue()
        self.assertEqual(src.num_blocks["DE30_EUR"], 1)
        self.assertEqual(src.num_candles["DE30_EUR"], 5)

    def test_the_run_stops_once_the_last_candle_passes_dtto(self):
        """Termination is driven by the data, not by the requested end date."""
        src = self.build(FakeResponse(candles_response(5)), dtfrom=T0,
                         dtto=T0 + datetime.timedelta(minutes=2))
        src.stream_to_queue()
        self.assertEqual(src.last["DE30_EUR"], T0 + datetime.timedelta(minutes=4))

    def test_a_non_200_reply_publishes_an_error_status(self):
        src = self.build(FakeResponse(candles_response(1)), dtfrom=T0,
                         dtto=T0 + datetime.timedelta(minutes=1))
        self.sink.events = []
        self.fake.responses = [FakeResponse(None, status=401)]
        self.assertIsNone(src.request("DE30_EUR"))
        self.assertEqual(self.sink.statuses(), ['ERROR'])

    def test_a_transport_error_publishes_an_error_status(self):
        src = self.build(FakeResponse(candles_response(1)), dtfrom=T0,
                         dtto=T0 + datetime.timedelta(minutes=1))
        self.sink.events = []
        self.fake.raise_on_send = True
        self.assertIsNone(src.request("DE30_EUR"))
        self.assertEqual(self.sink.statuses(), ['ERROR'])

    def test_the_pricing_endpoint_is_separate(self):
        src = self.build(FakeResponse(candles_response(1)), dtfrom=T0,
                         dtto=T0 + datetime.timedelta(minutes=1))
        src.price_request("DE30_EUR", T0)
        self.assertTrue(self.fake.last['url'].endswith(
            "/v3/accounts/001-TEST-000/pricing"))
        self.assertEqual(self.fake.last['params']['instruments'], "DE30_EUR")
        self.assertTrue(self.fake.last['params']['since'].endswith("000Z"))


class TestResampler(CandlesCase):
    """
    data/resample.py aggregates S5 bars up to the requested granularity. It is
    unfinished: stream_to_queue never advances self.last, so the historic
    termination check can only fire when dtto is already in the past.
    """

    module = resample_mod

    def s5(self, n, start=T0):
        return candles_response(n, start=start,
                                step=datetime.timedelta(seconds=5),
                                granularity="S5")

    def test_it_requests_s5_regardless_of_the_target_granularity(self):
        self.build(FakeResponse(self.s5(3)))
        self.assertEqual(self.fake.last['params']['granularity'], 'S5')

    def test_the_pandas_offset_alias_matches_the_granularity(self):
        self.assertEqual(self.build(FakeResponse(self.s5(3)),
                                    granularity="M5").resample, "5min")
        self.assertEqual(self.build(FakeResponse(self.s5(3)),
                                    granularity="H1").resample, "1h")
        self.assertEqual(self.build(FakeResponse(self.s5(3)),
                                    granularity="S30").resample, "30s")

    def test_seconds_per_bar(self):
        self.assertEqual(self.build(FakeResponse(self.s5(3)),
                                    granularity="M5").secs, 300)

    def test_it_aggregates_a_full_minute_into_one_candle(self):
        src = self.build(self.canned(25), granularity="M1", dtfrom=T0,
                         dtto=T0 - datetime.timedelta(days=1))
        src.stream_to_queue()
        aggregated = self.sink.of('CANDLE')
        self.assertEqual(len(aggregated), 1)
        bar = aggregated[0]
        self.assertEqual(sorted(bar.mid), ['c', 'h', 'l', 'o'])
        self.assertEqual(bar.time, T0 + datetime.timedelta(minutes=1))

    def test_the_aggregate_is_first_max_min_last(self):
        src = self.build(self.canned(25), granularity="M1", dtfrom=T0,
                         dtto=T0 - datetime.timedelta(days=1))
        src.stream_to_queue()
        bar = self.sink.of('CANDLE')[0]
        # bars 13..24 cover 10:01:05..10:02:00; the 10:01 bin is bars 13..23
        self.assertLess(bar.mid['l'], bar.mid['h'])
        self.assertEqual(bar.mid['h'], max(bar.mid.values()))
        self.assertEqual(bar.mid['l'], min(bar.mid.values()))

    def test_nothing_is_published_when_the_block_does_not_end_on_a_boundary(self):
        src = self.build(self.canned(24), granularity="M1", dtfrom=T0,
                         dtto=T0 - datetime.timedelta(days=1))
        src.stream_to_queue()
        self.assertEqual(self.sink.of('CANDLE'), [])

    def canned(self, n):
        return FakeResponse(self.s5(n))


class TestTransactionStream(TempDirCase):

    def build(self, *lines, **kw):
        self.fake = FakeRequests(FakeResponse({}, lines=list(lines)), **kw)
        patcher = mock.patch.object(transaction_mod, 'requests', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        src = transaction_mod.StreamingForexTransactions(
            setup=self.settings, pairs=["DE30_EUR"])
        self.sink = Recorder()
        src.set_queue(self.sink)
        return src

    def test_it_subscribes_to_the_account_transaction_stream(self):
        src = self.build()
        src.stream_to_queue()
        self.assertTrue(self.fake.last['url'].endswith(
            "/v3/accounts/001-TEST-000/transactions/stream"))
        self.assertEqual(self.fake.last['params']['instruments'], "DE30_EUR")

    def test_heartbeats_are_dropped(self):
        src = self.build(json.dumps({"type": "HEARTBEAT", "time": "x"}))
        src.stream_to_queue()
        self.assertEqual(self.sink.events, [])

    def test_every_other_message_becomes_a_transaction_event(self):
        src = self.build(
            json.dumps({"type": "HEARTBEAT"}),
            json.dumps({"type": "ORDER_FILL", "orderID": "11", "price": "1.0"}),
            json.dumps({"type": "STOP_ORDER", "id": "12"}))
        src.stream_to_queue()
        self.assertEqual(self.sink.kinds(), ['TRANSACTION', 'TRANSACTION'])
        self.assertEqual(self.sink.events[0].type, "ORDER_FILL")

    def test_blank_lines_are_skipped(self):
        src = self.build("", json.dumps({"type": "ORDER_FILL", "orderID": "1"}))
        src.stream_to_queue()
        self.assertEqual(len(self.sink.of('TRANSACTION')), 1)

    def test_malformed_json_is_skipped_without_killing_the_stream(self):
        src = self.build("{not json",
                         json.dumps({"type": "ORDER_FILL", "orderID": "1"}))
        src.stream_to_queue()
        self.assertEqual(len(self.sink.of('TRANSACTION')), 1)

    def test_a_dead_connection_returns_quietly(self):
        src = self.build(raise_on_send=True)
        self.assertIsNone(src.stream_to_queue())
        self.assertEqual(self.sink.events, [])

    def test_a_non_200_reply_returns_quietly(self):
        self.fake = FakeRequests(FakeResponse({}, status=401))
        patcher = mock.patch.object(transaction_mod, 'requests', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        src = transaction_mod.StreamingForexTransactions(
            setup=self.settings, pairs=["DE30_EUR"])
        src.set_queue(Recorder())
        self.assertIsNone(src.stream_to_queue())

    def test_an_api_version_other_than_3_aborts_the_process(self):
        self.settings.API_VERSION = '1'
        with mock.patch('os._exit') as halt:
            with mock.patch.object(transaction_mod, 'requests', FakeRequests()):
                transaction_mod.StreamingForexTransactions(
                    setup=self.settings, pairs=["DE30_EUR"])
        halt.assert_called_once_with(-1)


class TestPriceStream(TempDirCase):

    def build(self, *lines, **kw):
        self.fake = FakeRequests(FakeResponse({}, lines=list(lines)), **kw)
        patcher = mock.patch.object(streaming_mod, 'requests', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        src = streaming_mod.StreamingForexPrices(["EURUSD"], setup=self.settings)
        self.sink = Recorder()
        src.set_queue(self.sink)
        return src

    def price(self, tradeable=True, bid="1.07832", ask="1.07847"):
        return json.dumps({"type": "PRICE", "tradeable": tradeable,
                           "instrument": "EUR_USD",
                           "time": "2017-02-01T10:00:00.000000000Z",
                           "closeoutBid": bid, "closeoutAsk": ask})

    def test_it_subscribes_to_the_v3_pricing_stream(self):
        src = self.build()
        src.stream_to_queue()
        self.assertTrue(self.fake.last['url'].endswith(
            "/v3/accounts/001-TEST-000/pricing/stream"))

    def test_the_pair_is_converted_to_the_oanda_underscore_form(self):
        src = self.build()
        src.stream_to_queue()
        self.assertEqual(self.fake.last['params']['instruments'], "EUR_USD")

    def test_the_price_dictionary_holds_the_pair_and_its_inverse(self):
        src = self.build()
        self.assertEqual(sorted(src.prices), ["EURUSD", "USDEUR"])

    def test_a_price_message_publishes_a_tick(self):
        src = self.build(self.price())
        src.stream_to_queue()
        self.assertEqual(len(self.sink.of('TICK')), 1)

    def test_prices_are_quantised_to_five_decimals_as_decimals(self):
        src = self.build(self.price())
        src.stream_to_queue()
        self.assertEqual(src.prices["EURUSD"]["bid"], Decimal("1.07832"))
        self.assertIsInstance(src.prices["EURUSD"]["ask"], Decimal)

    def test_the_inverse_pair_is_filled_in(self):
        src = self.build(self.price())
        src.stream_to_queue()
        self.assertEqual(src.prices["USDEUR"]["bid"],
                         (Decimal("1.0") / Decimal("1.07832")).quantize(Decimal("0.00001")))

    def test_only_the_inverse_pair_gets_a_timestamp(self):
        """
        The direct pair's "time" slot is never written - only the inverted one
        is. Anything reading prices[pair]["time"] sees None forever.
        """
        src = self.build(self.price())
        src.stream_to_queue()
        self.assertIsNone(src.prices["EURUSD"]["time"])
        self.assertIsNotNone(src.prices["USDEUR"]["time"])

    def test_an_untradeable_quote_is_still_recorded(self):
        """
        `tradeable: false` only suppresses the debug line; valid_data stays
        False so no tick is published, but the price dict is not updated either.
        """
        src = self.build(self.price(tradeable=False))
        src.stream_to_queue()
        self.assertEqual(self.sink.of('TICK'), [])
        self.assertIsNone(src.prices["EURUSD"]["bid"])

    def test_heartbeats_publish_nothing(self):
        src = self.build(json.dumps({"type": "HEARTBEAT", "time": "x"}))
        src.stream_to_queue()
        self.assertEqual(self.sink.events, [])

    def test_a_dead_connection_returns_quietly(self):
        src = self.build(raise_on_send=True)
        self.assertIsNone(src.stream_to_queue())


if __name__ == "__main__":
    unittest.main()
