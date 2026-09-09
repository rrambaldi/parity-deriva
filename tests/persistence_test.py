"""
Characterisation tests for the persistence layer: the HDF5 candle warehouse
(BulkSaver, CandleSaver), the offline replay that reads it back, and the JSONL
event log (EventSaver / EventReplay).

The event log is the audit trail a real-vs-simulated comparison would be run
against, and the HDF5 store is what an offline re-run would replay, so the
round-trip fidelity of both is pinned here.
"""

import datetime
import json
import os
import unittest
from unittest import mock

import pandas as pd

from parity_deriva.data import bulksaver as bulk_mod
from parity_deriva.data.bulksaver import BulkSaver
from parity_deriva.data.datasaver import CandleSaver
from parity_deriva.data import replay as replay_mod
from parity_deriva.event.event import CandleEvent, StatusEvent, TickEvent, Event
from parity_deriva.event.replay import EventReplay
from parity_deriva.event.saver import EventSaver
from parity_deriva.tests.helpers import (T0, FakeRequests, FakeResponse, Recorder,
                                   TempDirCase, candle_dict, candles_response)


class BulkSaverCase(TempDirCase):

    def setUp(self):
        super(BulkSaverCase, self).setUp()
        BulkSaver.curr = {}
        BulkSaver.url = {}

    def build(self, response=None, **kw):
        self.fake = FakeRequests(response or FakeResponse(candles_response(3)))
        patcher = mock.patch.object(bulk_mod, 'requests', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        kw.setdefault('setup', self.settings)
        kw.setdefault('pairs', ["DE30_EUR"])
        kw.setdefault('granularity', "M1")
        kw.setdefault('dtfrom', T0)
        kw.setdefault('dtto', T0 + datetime.timedelta(hours=1))
        return BulkSaver(**kw)

    def store(self, pair="DE30_EUR"):
        return pd.HDFStore(self.path("%s.hd5" % pair))


class TestBulkSaverSetUp(BulkSaverCase):

    def test_one_store_file_per_instrument(self):
        bs = self.build(pairs=["DE30_EUR", "EUR_USD"])
        self.assertEqual(bs.store_name["EUR_USD"], "EUR_USD.hd5")

    def test_the_granularity_becomes_the_bar_width(self):
        self.assertEqual(self.build(granularity="M5").timedelta,
                         pd.Timedelta(minutes=5))

    def test_the_cursor_starts_at_dtfrom_for_an_empty_store(self):
        self.assertEqual(self.build().curr["DE30_EUR"], T0)

    def test_query_parameters(self):
        bs = self.build()
        self.assertEqual(bs.params['price'], 'ABM')
        self.assertEqual(bs.params['count'], 2000)
        self.assertEqual(bs.params['alignmentTimezone'], 'Europe/Rome')

    def test_an_inverted_date_range_aborts_the_process(self):
        with mock.patch('os._exit') as halt:
            self.build(dtfrom=T0, dtto=T0 - datetime.timedelta(days=1))
        halt.assert_called_once_with(-1)


class TestBulkSaverConversion(BulkSaverCase):

    def test_the_row_schema_is_flat(self):
        bs = self.build()
        block = bs.create_dict(candles_response(2))
        row = block[T0]
        self.assertEqual(sorted(row), [
            'ask_c', 'ask_h', 'ask_l', 'ask_o',
            'bid_c', 'bid_h', 'bid_l', 'bid_o',
            'mid_c', 'mid_h', 'mid_l', 'mid_o', 'volume'])

    def test_the_block_is_keyed_on_parsed_timestamps(self):
        block = self.build().create_dict(candles_response(3))
        self.assertEqual(sorted(block),
                         [T0 + datetime.timedelta(minutes=i) for i in range(3)])

    def test_prices_are_floats_and_volume_an_int(self):
        row = self.build().create_dict(candles_response(1))[T0]
        self.assertIsInstance(row['mid_o'], float)
        self.assertIsInstance(row['volume'], int)

    def test_incomplete_candles_are_kept_by_create_dict(self):
        """
        create_dict does not look at `complete`, unlike the streaming sources.
        A block whose last bar is still forming is written to the store as if
        it were final.
        """
        body = candles_response(2)
        body['candles'][-1]['complete'] = False
        self.assertEqual(len(self.build().create_dict(body)), 2)

    def test_an_empty_candle_list_yields_an_empty_block(self):
        self.assertEqual(self.build().create_dict({"candles": []}), {})


class TestBulkSaverPersistence(BulkSaverCase):

    def test_save_dict_appends_to_the_store(self):
        bs = self.build()
        bs.save_dict("DE30_EUR", bs.create_dict(candles_response(5)))
        store = self.store()
        saved = store['/M1']
        store.close()
        self.assertEqual(len(saved), 5)
        self.assertEqual(saved.index.min(), pd.Timestamp(T0))

    def test_the_cursor_advances_past_the_last_saved_bar(self):
        bs = self.build()
        bs.save_dict("DE30_EUR", bs.create_dict(candles_response(5)))
        self.assertEqual(bs.curr["DE30_EUR"],
                         pd.Timestamp(T0 + datetime.timedelta(minutes=5)))

    def test_the_stored_columns_match_the_candle_event_layout(self):
        """
        The store schema has to line up with CandleEvent.to_dict(), because
        CandleSaver appends events straight into the same table.
        """
        bs = self.build()
        bs.save_dict("DE30_EUR", bs.create_dict(candles_response(2)))
        store = self.store()
        columns = sorted(store['/M1'].columns)
        store.close()
        self.assertEqual(columns, sorted(CandleEvent(candle_dict()).to_dict()))

    def test_a_second_run_resumes_from_the_end_of_the_store(self):
        bs = self.build()
        bs.save_dict("DE30_EUR", bs.create_dict(candles_response(5)))
        again = self.build(dtfrom=T0, dtto=T0 + datetime.timedelta(hours=1))
        self.assertEqual(again.curr["DE30_EUR"],
                         pd.Timestamp(T0 + datetime.timedelta(minutes=5)))

    def test_a_run_entirely_before_the_store_clamps_dtto_to_its_start(self):
        """
        Back-filling: if the requested window ends before anything already
        stored, dtto is pulled back to the store's first bar so the gap is
        filled and nothing is duplicated.
        """
        bs = self.build()
        bs.save_dict("DE30_EUR", bs.create_dict(candles_response(5)))
        earlier = T0 - datetime.timedelta(days=10)
        back = self.build(dtfrom=earlier, dtto=earlier + datetime.timedelta(days=1))
        self.assertEqual(back.curr["DE30_EUR"], earlier)
        self.assertEqual(back.dtto, pd.Timestamp(T0))


class TestBulkSaverStreaming(BulkSaverCase):

    def test_a_full_run_writes_and_reports_done(self):
        bs = self.build(dtto=T0 + datetime.timedelta(minutes=2))
        sink = Recorder()
        bs.set_queue(sink)
        bs.stream_to_queue()
        self.assertEqual(sink.statuses(), ['STARTED', 'DONE'])
        store = self.store()
        self.assertEqual(len(store['/M1']), 3)
        store.close()

    def test_an_empty_response_skips_the_cursor_forward_by_half_a_batch(self):
        """Weekends and holidays are jumped rather than retried bar by bar."""
        bs = self.build(FakeResponse({"candles": []}),
                        dtto=T0 + datetime.timedelta(minutes=2))
        sink = Recorder()
        bs.set_queue(sink)
        bs.stream_to_queue()
        self.assertEqual(sink.statuses(), ['STARTED', 'DONE'])
        self.assertGreater(bs.curr["DE30_EUR"], bs.dtto)

    def test_a_failed_request_returns_none_and_is_logged(self):
        bs = self.build(FakeResponse(None, status=401))
        self.assertIsNone(bs.request("DE30_EUR"))

    def test_the_from_parameter_tracks_the_cursor(self):
        bs = self.build()
        bs.request("DE30_EUR")
        self.assertTrue(self.fake.last['params']['from'].startswith("2017-02-01T10:00:00"))


class TestCandleSaver(TempDirCase):

    def setUp(self):
        super(TestCandleSaver, self).setUp()
        CandleSaver.store = {}

    def build(self, pairs=None):
        saver = CandleSaver(setup=self.settings,
                            pairs=pairs if pairs is not None else ["DE30_EUR"])
        # store is a class attribute that setUp replaces, so hold on to the
        # handles this instance actually opened.
        opened = list(saver.store.values())
        self.addCleanup(lambda: [h.close() for h in opened])
        return saver

    def candle(self, dt=T0):
        ev = CandleEvent(candle_dict(dt))
        ev.instrument, ev.granularity = "DE30_EUR", "M1"
        return ev

    def test_a_candle_is_appended_under_the_granularity_key(self):
        saver = self.build()
        saver.execute_event(self.candle())
        saver.store["DE30_EUR"].flush()
        self.assertEqual(len(saver.store["DE30_EUR"]['/M1']), 1)

    def test_successive_candles_accumulate(self):
        saver = self.build()
        for i in range(3):
            saver.execute_event(self.candle(T0 + datetime.timedelta(minutes=i)))
        saver.store["DE30_EUR"].flush()
        self.assertEqual(len(saver.store["DE30_EUR"]['/M1']), 3)

    def test_non_candle_events_are_ignored(self):
        saver = self.build()
        saver.execute_event(StatusEvent('DONE'))
        saver.execute_event(TickEvent({"instrument": "DE30_EUR"}))
        self.assertNotIn('/M1', saver.store["DE30_EUR"])

    def test_candles_for_other_instruments_are_ignored(self):
        saver = self.build(pairs=["EUR_USD"])
        saver.execute_event(self.candle())
        self.assertNotIn('/M1', saver.store["EUR_USD"])

    def test_no_store_is_opened_without_pairs(self):
        saver = CandleSaver(setup=self.settings)
        self.assertIsNone(saver.pairs)
        self.assertEqual(saver.store, {})

    def test_re_saving_an_existing_bar_is_silently_dropped(self):
        """
        The intended update path is `if t in store[i][g].index: ... .loc[t] =`,
        but HDFStore.__getitem__ hands back a fresh DataFrame, so the write
        lands on a throwaway copy and the method returns as if it had saved.
        The bar is neither updated nor appended: a re-delivered candle - which
        is exactly what a reconnecting live feed produces - vanishes.
        """
        saver = self.build()
        saver.execute_event(self.candle())
        saver.execute_event(self.candle())
        saver.store["DE30_EUR"].flush()
        self.assertEqual(len(saver.store["DE30_EUR"]['/M1']), 1)

    def test_a_corrected_bar_does_not_overwrite_the_stored_one(self):
        saver = self.build()
        saver.execute_event(self.candle())
        revised = self.candle()
        revised.mid = dict(revised.mid, c=99999.0)
        saver.execute_event(revised)
        saver.store["DE30_EUR"].flush()
        table = saver.store["DE30_EUR"]['/M1']
        self.assertEqual(len(table), 1)
        self.assertNotEqual(table['mid_c'].iloc[0], 99999.0)


class TestOfflineReplay(TempDirCase):
    """data/replay.py streams a stored instrument back through the event bus."""

    def setUp(self):
        super(TestOfflineReplay, self).setUp()
        for name in ('last', 'url', 'store', 'curr', 'candles', 'samples', 'cent'):
            if hasattr(replay_mod.ForexCandles, name):
                setattr(replay_mod.ForexCandles, name, {})
        BulkSaver.curr = {}
        BulkSaver.url = {}
        with mock.patch.object(bulk_mod, 'requests',
                               FakeRequests(FakeResponse(candles_response(1)))):
            bs = BulkSaver(setup=self.settings, pairs=["DE30_EUR"],
                           granularity="M1", dtfrom=T0,
                           dtto=T0 + datetime.timedelta(hours=1))
        bs.save_dict("DE30_EUR", bs.create_dict(candles_response(30)))

    def build(self, **kw):
        kw.setdefault('setup', self.settings)
        kw.setdefault('pairs', ["DE30_EUR"])
        kw.setdefault('granularity', "M1")
        kw.setdefault('dtfrom', T0)
        kw.setdefault('dtto', T0 + datetime.timedelta(minutes=9))
        src = replay_mod.ForexCandles(**kw)
        self.sink = Recorder()
        src.set_queue(self.sink)
        return src

    def test_the_store_is_loaded_into_memory(self):
        src = self.build()
        self.assertEqual(len(src.store["DE30_EUR"]), 30)

    def test_the_bar_interval_comes_from_the_granularity(self):
        self.assertEqual(self.build().interval, pd.Timedelta(minutes=1))

    def test_it_replays_the_requested_window_inclusive_of_both_ends(self):
        src = self.build()
        src.stream_to_queue()
        replayed = self.sink.of('CANDLE')
        self.assertEqual(len(replayed), 10)
        self.assertEqual(replayed[0].time, T0)
        self.assertEqual(replayed[-1].time, T0 + datetime.timedelta(minutes=9))

    def test_the_run_is_bracketed_by_status_events(self):
        src = self.build()
        src.stream_to_queue()
        self.assertEqual(self.sink.statuses(), ['STARTED', 'DONE'])

    def test_each_replayed_bar_is_a_full_candle_event(self):
        """
        The store rows are flat; to_candle() rebuilds the nested ask/bid/mid
        layout plus the timestamp, so a replayed bar is indistinguishable from
        a live one as far as a strategy is concerned.
        """
        src = self.build()
        src.stream_to_queue()
        bar = self.sink.of('CANDLE')[0]
        self.assertEqual(sorted(bar.mid), ['c', 'h', 'l', 'o'])
        self.assertEqual(sorted(bar.ask), ['c', 'h', 'l', 'o'])
        self.assertEqual(bar.instrument, "DE30_EUR")
        self.assertEqual(bar.granularity, "M1")
        self.assertTrue(bar.complete)
        self.assertIn(bar.direction(), (-1, 0, 1))

    def test_the_replayed_values_match_the_store(self):
        src = self.build()
        src.stream_to_queue()
        store = pd.HDFStore(self.path("DE30_EUR.hd5"))
        table = store['/M1']
        store.close()
        for bar in self.sink.of('CANDLE'):
            row = table.loc[pd.Timestamp(bar.time)]
            self.assertEqual(bar.mid['o'], row['mid_o'])
            self.assertEqual(bar.ask['c'], row['ask_c'])
            self.assertEqual(bar.volume, row['volume'])

    def test_every_bar_is_distinct(self):
        src = self.build()
        src.stream_to_queue()
        opens = [b.mid['o'] for b in self.sink.of('CANDLE')]
        self.assertEqual(len(set(opens)), len(opens))

    def test_gaps_in_the_store_are_skipped_silently(self):
        """A missing timestamp is a KeyError, swallowed so the clock keeps going."""
        src = self.build(dtfrom=T0 + datetime.timedelta(minutes=25),
                         dtto=T0 + datetime.timedelta(minutes=40))
        src.stream_to_queue()
        self.assertEqual(len(self.sink.of('CANDLE')), 5)
        self.assertEqual(self.sink.statuses()[-1], 'DONE')


class TestEventLogRoundTrip(TempDirCase):
    """EventSaver writes one JSON object per line; EventReplay reads it back."""

    def saver(self, **kw):
        kw.setdefault('setup', self.settings)
        kw.setdefault('logname', 'RT')
        kw.setdefault('overwrite', True)
        saver = EventSaver(**kw)
        self.addCleanup(saver.quit)
        return saver

    def candle(self, dt=T0):
        ev = CandleEvent(candle_dict(dt))
        ev.instrument, ev.granularity = "DE30_EUR", "M1"
        return ev

    def test_the_filename_carries_the_start_date(self):
        saver = self.saver()
        today = datetime.date.today().strftime("%Y%m%d")
        self.assertEqual(os.path.basename(saver.getFileName()), "RT-%s.log" % today)

    def test_the_log_lands_in_the_log_dir(self):
        self.assertTrue(self.saver().getFileName().startswith(self.tmpdir))

    def test_append_is_the_default_mode(self):
        saver = EventSaver(setup=self.settings, logname='A')
        self.addCleanup(saver.quit)
        self.assertEqual(saver.openmode, 'a')

    def test_overwrite_mode_truncates(self):
        self.assertEqual(self.saver().openmode, 'w')

    def test_one_line_per_event(self):
        saver = self.saver()
        for i in range(3):
            saver.execute_event(self.candle(T0 + datetime.timedelta(minutes=i)))
        with open(saver.getFileName()) as fh:
            lines = fh.read().strip().split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(json.loads(lines[0])['_type'], 'CANDLE')

    def test_the_private_fields_are_written_too(self):
        """_type is what lets the replayed event route to the right handler."""
        saver = self.saver()
        saver.execute_event(self.candle())
        with open(saver.getFileName()) as fh:
            record = json.loads(fh.readline())
        self.assertIn('_type', record)
        self.assertIn('_created', record)

    def test_every_event_kind_survives_serialisation(self):
        saver = self.saver()
        for ev in (self.candle(), StatusEvent('DONE'),
                   TickEvent({"instrument": "X", "time": T0, "bid": 1, "ask": 2})):
            saver.execute_event(ev)
        with open(saver.getFileName()) as fh:
            kinds = [json.loads(l)['_type'] for l in fh if l.strip()]
        self.assertEqual(kinds, ['CANDLE', 'STATUS', 'TICK'])

    def test_replay_publishes_every_line_then_done(self):
        saver = self.saver()
        for i in range(4):
            saver.execute_event(self.candle(T0 + datetime.timedelta(minutes=i)))
        name = os.path.basename(saver.getFileName())
        replay = EventReplay(setup=self.settings, logname=name)
        sink = Recorder()
        replay.set_queue(sink)
        replay.stream_to_queue()
        self.assertEqual(sink.kinds(), ['CANDLE'] * 4 + ['STATUS'])
        self.assertEqual(sink.statuses(), ['DONE'])

    def test_timestamps_survive_the_round_trip(self):
        """
        The log stores isoformat(); parse_time accepts it, so a replayed event
        carries its real timestamp. Before the Python 3 migration the reader
        only understood the OANDA format and every replayed event came back
        dated 1970-01-01, which would make any replay-based comparison useless.
        """
        saver = self.saver()
        saver.execute_event(self.candle(T0))
        name = os.path.basename(saver.getFileName())
        replay = EventReplay(setup=self.settings, logname=name)
        sink = Recorder()
        replay.set_queue(sink)
        replay.stream_to_queue()
        self.assertEqual(sink.events[0].time, T0)

    def test_the_replayed_event_is_a_plain_event_carrying_its_type(self):
        saver = self.saver()
        saver.execute_event(self.candle(T0))
        name = os.path.basename(saver.getFileName())
        replay = EventReplay(setup=self.settings, logname=name)
        sink = Recorder()
        replay.set_queue(sink)
        replay.stream_to_queue()
        first = sink.events[0]
        self.assertIsInstance(first, Event)
        self.assertNotIsInstance(first, CandleEvent)
        self.assertEqual(str(first), 'CANDLE')

    def test_replayed_candles_lose_their_helper_methods(self):
        """
        Because replay rebuilds a bare Event, direction()/isBull() and the
        nested price books are gone - the flat fields are all that come back.
        A replay-driven strategy run is therefore not equivalent to a live one.
        """
        saver = self.saver()
        saver.execute_event(self.candle(T0))
        name = os.path.basename(saver.getFileName())
        replay = EventReplay(setup=self.settings, logname=name)
        sink = Recorder()
        replay.set_queue(sink)
        replay.stream_to_queue()
        first = sink.events[0]
        self.assertFalse(hasattr(first, 'direction'))
        self.assertIsInstance(first.mid, dict)

    def test_a_missing_log_raises_on_construction(self):
        with self.assertRaises(IOError):
            EventReplay(setup=self.settings, logname='does-not-exist.log')

    def test_a_write_failure_reopens_the_file_in_append_mode(self):
        saver = self.saver()
        saver.f.close()
        saver.execute_event(self.candle())
        self.assertFalse(saver.f.closed)


if __name__ == "__main__":
    unittest.main()
