"""
Tests for parity_deriva.event.event.

This module is the data contract shared by the live and the simulated side,
so its coercion rules matter more than anywhere else: if a field silently
changes type or a timestamp silently becomes 1970, a real-vs-simulated
comparison is measuring the wrong thing.
"""

import datetime
import json
import unittest

import pandas as pd

from parity_deriva.event.event import (Event, CandleEvent, TickEvent, SignalEvent,
                                 OrderEvent, OrderCancelEvent, OrderFillEvent,
                                 ClientOrderEvent, StatusEvent,
                                 TransactionEvent, parse_time)
from parity_deriva.tests.helpers import T0, candle_dict, oanda_time


class TestParseTime(unittest.TestCase):

    def test_oanda_format(self):
        self.assertEqual(parse_time(oanda_time(T0)), T0)

    def test_datetime_passes_through_unchanged(self):
        self.assertIs(parse_time(T0), T0)

    def test_pandas_timestamp_passes_through(self):
        """Timestamp subclasses datetime, so the CSV backtest path keeps it."""
        ts = pd.Timestamp(T0)
        self.assertIs(parse_time(ts), ts)

    def test_isoformat_is_accepted(self):
        """EventSaver writes isoformat(); EventReplay has to read it back."""
        self.assertEqual(parse_time(T0.isoformat()), T0)

    def test_isoformat_with_microseconds(self):
        dt = T0.replace(microsecond=123456)
        self.assertEqual(parse_time(dt.isoformat()), dt)

    def test_trailing_z_is_accepted(self):
        parsed = parse_time("2017-02-01T10:00:00Z")
        self.assertEqual(parsed.replace(tzinfo=None), T0)

    def test_unparseable_falls_back_to_epoch(self):
        epoch = datetime.datetime(1970, 1, 1)
        self.assertEqual(parse_time("not a date"), epoch)
        self.assertEqual(parse_time(None), epoch)
        self.assertEqual(parse_time(12345), epoch)


class TestEventBase(unittest.TestCase):

    def test_type_is_derived_from_the_class_name(self):
        self.assertEqual(TickEvent()._type, 'TICK')
        self.assertEqual(CandleEvent()._type, 'CANDLE')
        self.assertEqual(OrderCancelEvent()._type, 'ORDERCANCEL')
        self.assertEqual(ClientOrderEvent()._type, 'CLIENTORDER')

    def test_base_event_has_an_empty_type(self):
        """
        _type is the class name with the literal substring "Event" removed, so
        the base class is left with ''. A bare Event therefore dispatches to
        nothing: every handler compares str(event) against a real name. This is
        why EventReplay works only because the payload carries its own _type
        key, which overwrites the empty one (see the round-trip test below).
        """
        self.assertEqual(Event()._type, '')
        self.assertEqual(str(Event()), '')

    def test_str_and_repr_are_the_type(self):
        """Every handler dispatches on str(event), so this is load-bearing."""
        self.assertEqual(str(TickEvent()), 'TICK')
        self.assertEqual(repr(TickEvent()), 'TICK')

    def test_created_is_stamped_at_construction(self):
        before = datetime.datetime.today()
        ev = Event()
        self.assertGreaterEqual(ev._created, before)

    def test_dict_becomes_attributes(self):
        ev = Event({"instrument": "DE30_EUR", "units": -1})
        self.assertEqual(ev.instrument, "DE30_EUR")
        self.assertEqual(ev.units, -1)

    def test_scalar_payload_lands_on_a_field_named_after_the_type(self):
        """StatusEvent('DONE') is the whole reason this branch exists."""
        self.assertEqual(StatusEvent('DONE').status, 'DONE')
        self.assertEqual(TickEvent('x').tick, 'x')

    def test_scalar_payload_on_the_base_class_creates_an_unnamed_field(self):
        """
        With _type == '' the scalar branch does setattr(self, '', data), which
        succeeds and produces an attribute reachable only through __dict__.
        """
        ev = Event('x')
        self.assertEqual(ev.__dict__[''], 'x')
        self.assertEqual(ev.to_dict(), {'': 'x'})

    def test_no_payload_sets_nothing(self):
        ev = Event()
        self.assertEqual(ev.to_dict(), {})

    def test_price_fields_are_coerced_to_float(self):
        ev = Event({"price": "11700.5", "o": "1", "h": "2", "l": "3", "c": "4"})
        self.assertEqual(ev.price, 11700.5)
        self.assertIsInstance(ev.price, float)
        self.assertEqual((ev.o, ev.h, ev.l, ev.c), (1.0, 2.0, 3.0, 4.0))

    def test_uncoercible_price_becomes_the_string_zero_not_a_float(self):
        """
        The except branch assigns the *string* "0.0", so a bad price yields a
        str where every caller expects a number. Formatting it with %f raises.
        """
        ev = Event({"price": "abc"})
        self.assertEqual(ev.price, "0.0")
        self.assertIsInstance(ev.price, str)

    def test_units_is_not_coerced(self):
        """
        Only price/o/h/l/c are coerced. units stays whatever it was, and the
        info() helpers compare it with 0 - so a string unit raises TypeError
        on Python 3 (it silently compared as greater on Python 2).
        """
        ev = OrderEvent({"units": "-1", "instrument": "X", "orderType": "STOP",
                         "price": 1.0, "stopLoss": None, "takeProfit": None})
        self.assertEqual(ev.units, "-1")
        with self.assertRaises(TypeError):
            ev.info()

    def test_time_is_parsed(self):
        self.assertEqual(Event({"time": oanda_time(T0)}).time, T0)

    def test_has_attr(self):
        ev = Event({"units": 1})
        self.assertTrue(ev.has_attr('units'))
        self.assertFalse(ev.has_attr('price'))

    def test_has_attr_sees_the_private_fields_too(self):
        self.assertTrue(Event().has_attr('_type'))
        self.assertTrue(Event().has_attr('_created'))

    def test_get_returns_none_for_unknown_keys(self):
        ev = Event({"units": 1})
        self.assertEqual(ev.__get__('units'), 1)
        self.assertIsNone(ev.__get__('nope'))

    def test_to_dict_hides_the_private_fields(self):
        d = Event({"units": 1}).to_dict()
        self.assertEqual(d, {"units": 1})
        self.assertNotIn('_type', d)
        self.assertNotIn('_created', d)

    def test_to_json_default_hides_the_private_fields(self):
        d = json.loads(Event({"units": 1}).to_json())
        self.assertEqual(d, {"units": 1})

    def test_to_json_all_includes_them_and_isoformats_datetimes(self):
        d = json.loads(TickEvent({"units": 1, "time": oanda_time(T0)}).to_json(True))
        self.assertEqual(d['_type'], 'TICK')
        self.assertEqual(d['time'], T0.isoformat())
        self.assertIn('_created', d)

    def test_saved_and_replayed_event_keeps_its_type_and_time(self):
        """
        This is the EventSaver -> EventReplay round trip. The replayed object is
        a plain Event, but _type comes back from the payload, so str(event)
        still routes it to the right handler.
        """
        original = CandleEvent(candle_dict(T0))
        original.instrument = "DE30_EUR"
        replayed = Event(json.loads(original.to_json(True)))
        self.assertEqual(str(replayed), 'CANDLE')
        self.assertEqual(replayed.time, T0)
        self.assertEqual(replayed.instrument, "DE30_EUR")
        self.assertEqual(replayed.volume, 10)

    def test_dump_lists_the_attributes(self):
        text = TickEvent({"units": 1}).dump()
        self.assertIn("TICK @", text)
        self.assertIn("units: 1", text)


class TestCandleEvent(unittest.TestCase):

    def setUp(self):
        self.ev = CandleEvent(candle_dict(T0, o=11700.0, h=11706.0,
                                          l=11694.0, c=11703.0, volume=42))
        self.ev.instrument = "DE30_EUR"
        self.ev.granularity = "M1"

    def test_class_defaults(self):
        blank = CandleEvent()
        self.assertIsNone(blank.bid)
        self.assertIsNone(blank.ask)
        self.assertIsNone(blank.mid)
        self.assertIsNone(blank.time)
        self.assertEqual(blank.volume, 0)
        self.assertFalse(blank.complete)

    def test_price_triplets_are_coerced_to_float(self):
        for side in ('ask', 'bid', 'mid'):
            book = getattr(self.ev, side)
            self.assertEqual(sorted(book), ['c', 'h', 'l', 'o'])
            for v in book.values():
                self.assertIsInstance(v, float)

    def test_triplets_keep_the_spread_apart(self):
        self.assertEqual(self.ev.mid['c'], 11703.0)
        self.assertEqual(self.ev.ask['c'], 11703.2)
        self.assertEqual(self.ev.bid['c'], 11702.8)

    def test_only_ohlc_keys_survive_the_triplet_rebuild(self):
        """__set__ rebuilds ask/bid/mid from scratch, dropping anything else."""
        ev = CandleEvent({"mid": {"o": 1, "h": 2, "l": 0, "c": 1.5, "junk": 9}})
        self.assertNotIn('junk', ev.mid)

    def test_missing_leg_in_a_triplet_becomes_the_string_zero(self):
        ev = CandleEvent({"mid": {"o": 1, "h": 2, "c": 1.5}})
        self.assertEqual(ev.mid['l'], "0.0")

    def test_price_selector(self):
        self.assertEqual(self.ev.price('M'), self.ev.mid)
        self.assertEqual(self.ev.price('A'), self.ev.ask)
        self.assertEqual(self.ev.price('B'), self.ev.bid)
        self.assertEqual(self.ev.price(), self.ev.mid)
        self.assertEqual(self.ev.price('junk'), self.ev.mid)

    def test_direction_and_shape_use_mid(self):
        self.assertEqual(self.ev.direction(), 1)
        self.assertTrue(self.ev.isBull())
        self.assertFalse(self.ev.isBear())

    def test_flat_candle_has_no_direction(self):
        ev = CandleEvent({"mid": {"o": 1, "h": 2, "l": 0, "c": 1}})
        self.assertEqual(ev.direction(), 0)
        self.assertFalse(ev.isBull())
        self.assertFalse(ev.isBear())

    def test_shape_helpers_ignore_their_price_argument(self):
        """
        direction/isBear/isBull/vola/ratio all call self.price() with no
        argument, so passing 'A' or 'B' still measures mid.

        Was and still is: unchanged, because every caller in the codebase uses
        the bare form and the fix would silently change what existing research
        measured. It is pinned so the limit is visible: a real-vs-simulated
        check that reads ask or bid through these helpers would be wrong.
        """
        ev = CandleEvent({"mid": {"o": 2, "h": 3, "l": 0, "c": 1},
                          "ask": {"o": 1, "h": 3, "l": 0, "c": 2},
                          "bid": {"o": 1, "h": 3, "l": 0, "c": 2}})
        self.assertEqual(ev.direction(), -1)      # mid is bearish
        self.assertEqual(ev.direction('A'), -1)   # ask is bullish, still -1
        self.assertEqual(ev.vola('A'), ev.vola('M'))

    def test_vola_is_the_mid_range(self):
        self.assertAlmostEqual(self.ev.vola(), 12.0)

    def test_ratio_is_body_over_range(self):
        self.assertAlmostEqual(self.ev.ratio(), 3.0 / 12.0)

    def test_price_str(self):
        self.assertIn("T:2017-02-01 10:00:00", self.ev.price_str())

    def test_dump_lists_the_three_books(self):
        text = self.ev.dump()
        self.assertIn("CANDLE T: 2017-02-01 10:00:00 V: 42", text)
        self.assertIn("ASK:", text)
        self.assertIn("BID:", text)
        self.assertIn("MID:", text)

    def test_to_dict_flattens_to_the_hdf5_schema(self):
        """This is exactly the row layout BulkSaver writes to the store."""
        d = self.ev.to_dict()
        self.assertEqual(d['volume'], 42)
        self.assertEqual(sorted(d), [
            'ask_c', 'ask_h', 'ask_l', 'ask_o',
            'bid_c', 'bid_h', 'bid_l', 'bid_o',
            'mid_c', 'mid_h', 'mid_l', 'mid_o', 'volume'])
        self.assertEqual(d['mid_o'], 11700.0)
        self.assertEqual(d['ask_c'], 11703.2)

    def test_to_dict_drops_time_instrument_and_granularity(self):
        """The timestamp becomes the store index, the rest is not persisted."""
        d = self.ev.to_dict()
        self.assertNotIn('time', d)
        self.assertNotIn('instrument', d)
        self.assertNotIn('granularity', d)

    def test_dataframe_is_a_single_row_indexed_on_the_timestamp(self):
        df = self.ev.dataframe()
        self.assertEqual(len(df), 1)
        self.assertEqual(df.index[0], pd.Timestamp(T0))
        self.assertEqual(df['mid_c'].iloc[0], 11703.0)

    def test_incomplete_candle_is_flagged(self):
        self.assertFalse(CandleEvent(candle_dict(complete=False)).complete)
        self.assertTrue(self.ev.complete)


class TestSignalAndOrderEvents(unittest.TestCase):

    def payload(self, units):
        return {"instrument": "DE30_EUR", "units": units, "orderType": "STOP",
                "price": 11700.0, "stopLoss": 11690.0, "takeProfit": 11720.0}

    def test_signal_info_renders_buy_for_positive_units(self):
        info = SignalEvent(self.payload(1)).info()
        self.assertEqual(info,
                         "SIGNAL DE30_EUR BUY STOP 1 @11700.000000 SL@11690.0 TP@11720.0")

    def test_signal_info_renders_sell_and_absolute_units(self):
        self.assertIn("SELL", SignalEvent(self.payload(-3)).info())
        self.assertIn(" 3 ", SignalEvent(self.payload(-3)).info())

    def test_a_fractional_size_is_not_rounded_away(self):
        """
        Was: the size went through %d, so an order for half a contract - a
        normal size on IG and on eToro - was logged as an order for 0.
        """
        self.assertIn(" 0.5 ", SignalEvent(self.payload(0.5)).info())
        self.assertIn(" 0.5 ", OrderEvent(self.payload(-0.5)).info())

    def test_zero_units_reads_as_sell(self):
        """`units > 0` means the flat case falls to SELL."""
        self.assertIn("SELL", SignalEvent(self.payload(0)).info())

    def test_absent_stoploss_and_takeprofit_render_empty(self):
        p = self.payload(1)
        p['stopLoss'] = None
        p['takeProfit'] = None
        info = SignalEvent(p).info()
        self.assertNotIn("SL@", info)
        self.assertNotIn("TP@", info)

    def test_order_info_has_the_same_shape(self):
        self.assertTrue(OrderEvent(self.payload(1)).info().startswith("ORDER DE30_EUR BUY"))

    def test_signal_can_be_copied_through_to_dict(self):
        """AG01/AG02 build the opposite leg of the straddle exactly this way."""
        first = SignalEvent(self.payload(1))
        second = SignalEvent(first.to_dict())
        second.units = -1
        self.assertEqual(second.instrument, "DE30_EUR")
        self.assertEqual(second.price, 11700.0)
        self.assertEqual(first.units, 1)

    def test_order_cancel_info(self):
        self.assertEqual(OrderCancelEvent({"orderID": 1508}).info(),
                         "ORDER CANCEL orderID: 1508")

    def test_order_cancel_info_renders_a_named_id(self):
        """
        Was: this pinned a TypeError, because the id went through %d. IG
        names an order - 'PDed17dc...' - so rendering a cancel raised, inside
        a handler, while the losing leg of a straddle was being cancelled.
        """
        self.assertEqual(OrderCancelEvent({"orderID": "PDed17dc"}).info(),
                         "ORDER CANCEL orderID: PDed17dc")

    def test_tick_info(self):
        ev = TickEvent({"type": "TICK", "instrument": "EURUSD", "time": T0,
                        "bid": 1.1, "ask": 1.2})
        self.assertIn("TICK Type: TICK, Instrument: EURUSD", ev.info())

    def test_transaction_info_lists_every_field(self):
        ev = TransactionEvent({"type": "ORDER_FILL", "orderID": "11"})
        info = ev.info()
        self.assertIn("TRANSACTION", info)
        self.assertIn("orderID: 11", info)

    def test_status_info(self):
        self.assertEqual(StatusEvent('ERROR').info(), "STATUS msg: ERROR")

    def test_order_fill_and_client_order_are_plain_carriers(self):
        fill = OrderFillEvent({"orderID": "11", "price": "11700.0"})
        self.assertEqual(str(fill), 'ORDERFILL')
        self.assertEqual(fill.orderID, "11")
        coe = ClientOrderEvent({"id": "1508", "batchID": "1508"})
        self.assertEqual(str(coe), 'CLIENTORDER')


class TestStatusEventDispatchTrap(unittest.TestCase):
    """
    str() of a StatusEvent is 'STATUS' whatever it carries; the payload lives
    in .status. That is correct and deliberate - handlers dispatch on the
    event kind - but it is a trap for anyone comparing str(event) against a
    payload value.

    Was: every strategy gated its end-of-run report on `str(event) == 'DONE'`,
         which is never true, so the research tables never printed at all.
    Now: the strategies test event.status. This test guards the Event side of
         that contract, so the trap cannot be reintroduced silently.
    """

    def test_status_event_stringifies_to_status_not_to_its_payload(self):
        done = StatusEvent('DONE')
        self.assertEqual(str(done), 'STATUS')
        self.assertNotEqual(str(done), 'DONE')
        self.assertEqual(done.status, 'DONE')


if __name__ == "__main__":
    unittest.main()
