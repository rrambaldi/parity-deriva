"""Tests for parity_deriva.lib (utils, ohlc, candle, oanda)."""

import datetime
import os
import unittest
from unittest import mock

import pandas as pd

from parity_deriva.lib.candle import Candle
from parity_deriva.lib.oanda import (OANDAObject, OANDAOrder, OANDATrade,
                               OANDAPosition, OANDAPositionSize)
from parity_deriva.lib.ohlc import ohlc
from parity_deriva.lib.utils import (datetimeToString, timestampFromString,
                               granularityToTimedelta, dctFromOanda,
                               serieToDict, getLogger, pricePrecision,
                               roundPrice)
from parity_deriva.tests.helpers import T0, oanda_time


class TestTimeHelpers(unittest.TestCase):

    def test_datetime_to_string_is_oanda_shaped(self):
        self.assertEqual(datetimeToString(T0),
                         "2017-02-01T10:00:00.000000000Z")

    def test_round_trip(self):
        self.assertEqual(timestampFromString(datetimeToString(T0)), T0)

    def test_timestamp_keeps_microseconds(self):
        dt = T0.replace(microsecond=123456)
        self.assertEqual(timestampFromString(datetimeToString(dt)), dt)

    def test_timestamp_rejects_anything_not_exactly_30_chars(self):
        """The length check is the only validation, so near-misses give None."""
        self.assertIsNone(timestampFromString("2017-02-01T10:00:00Z"))
        self.assertIsNone(timestampFromString(""))
        self.assertIsNone(timestampFromString(datetimeToString(T0) + "x"))

    def test_timestamp_raises_on_30_chars_of_garbage(self):
        """Right length, wrong content: no try/except, so strptime propagates."""
        with self.assertRaises(ValueError):
            timestampFromString("x" * 30)


class TestGranularityToTimedelta(unittest.TestCase):

    def test_seconds_minutes_hours(self):
        self.assertEqual(granularityToTimedelta("S5"), pd.Timedelta(seconds=5))
        self.assertEqual(granularityToTimedelta("S30"), pd.Timedelta(seconds=30))
        self.assertEqual(granularityToTimedelta("M1"), pd.Timedelta(minutes=1))
        self.assertEqual(granularityToTimedelta("M15"), pd.Timedelta(minutes=15))
        self.assertEqual(granularityToTimedelta("H4"), pd.Timedelta(hours=4))

    def test_day_and_week_ignore_the_multiplier(self):
        """D/W are fixed at 1 day / 7 days whatever number follows."""
        self.assertEqual(granularityToTimedelta("D"), pd.Timedelta(days=1))
        self.assertEqual(granularityToTimedelta("D3"), pd.Timedelta(days=1))
        self.assertEqual(granularityToTimedelta("W"), pd.Timedelta(days=7))

    def test_leading_slash_is_stripped(self):
        """HDFStore keys arrive as '/M1', so the slash form has to work."""
        self.assertEqual(granularityToTimedelta("/M1"), pd.Timedelta(minutes=1))
        self.assertEqual(granularityToTimedelta("/H1"), pd.Timedelta(hours=1))

    def test_unknown_granularity_is_none(self):
        self.assertIsNone(granularityToTimedelta("X5"))

    def test_missing_multiplier_raises(self):
        """'M' with no number reaches int('') and propagates."""
        with self.assertRaises(ValueError):
            granularityToTimedelta("M")


class TestDctFromOanda(unittest.TestCase):

    def setUp(self):
        self.raw = {"time": oanda_time(T0), "volume": 7,
                    "mid": {"o": "1", "h": "2", "l": "0.5", "c": "1.5"},
                    "ask": {"o": "1.2", "h": "2.2", "l": "0.7", "c": "1.7"}}

    def test_only_ohlc_mode_returns_flat_floats(self):
        self.assertEqual(dctFromOanda(self.raw, 'mid', True),
                         {'o': 1.0, 'h': 2.0, 'l': 0.5, 'c': 1.5})

    def test_indexed_mode_keys_on_the_parsed_timestamp(self):
        out = dctFromOanda(self.raw, 'mid')
        self.assertEqual(list(out), [T0])
        self.assertEqual(out[T0]['c'], 1.5)

    def test_price_type_selects_the_triplet(self):
        self.assertEqual(dctFromOanda(self.raw, 'ask', True)['c'], 1.7)

    def test_volume_is_not_carried_over(self):
        """The volume line is commented out in both branches."""
        self.assertNotIn('v', dctFromOanda(self.raw, 'mid', True))
        self.assertNotIn('volume', dctFromOanda(self.raw, 'mid', True))

    def test_default_price_type_is_mid(self):
        self.assertEqual(dctFromOanda(self.raw, onlyohlc=True)['o'], 1.0)


class TestPricePrecision(unittest.TestCase):
    """
    How many decimals an order price may carry, per instrument.

    Was: the AG strategies rounded every derived level to one decimal place,
         which is the DAX's precision. On EUR_USD a take profit of 1.22380
         became 1.2 - two figures below the entry of a buy - so the stop
         always triggered first and OANDA would have rejected the order.
    Now: the precision comes from the instrument.
    """

    def test_the_configured_instruments(self):
        self.assertEqual(pricePrecision('EUR_USD'), 5)
        self.assertEqual(pricePrecision('DE30_EUR'), 1)

    def test_an_unknown_instrument_falls_back_to_the_default(self):
        """
        Erring towards too many decimals is deliberate: the broker rejects
        the order loudly instead of silently accepting a moved level.
        """
        with self.assertLogs('parity_deriva.trading.trading', level='WARNING') as log:
            got = pricePrecision('NOT_AN_INSTRUMENT')
        self.assertEqual(got, 5)
        self.assertTrue(any('no precision configured' in line for line in log.output))

    def test_a_setup_can_override_the_table(self):
        setup = mock.MagicMock()
        setup.INSTRUMENT_PRECISION = {'XAU_USD': 2}
        setup.DEFAULT_PRICE_PRECISION = 3
        self.assertEqual(pricePrecision('XAU_USD', setup), 2)
        with self.assertLogs('parity_deriva.trading.trading', level='WARNING'):
            self.assertEqual(pricePrecision('EUR_USD', setup), 3)

    def test_rounding_an_fx_level_keeps_five_decimals(self):
        self.assertEqual(roundPrice('EUR_USD', 1.2238012345), 1.2238)
        self.assertEqual(roundPrice('EUR_USD', 1.223867), 1.22387)

    def test_rounding_an_index_level_keeps_one(self):
        self.assertEqual(roundPrice('DE30_EUR', 11714.04), 11714.0)
        self.assertEqual(roundPrice('DE30_EUR', 11714.06), 11714.1)

    def test_an_fx_take_profit_stays_on_the_right_side_of_the_entry(self):
        """The regression this exists for, with the real numbers that found it."""
        entry, stop, spread = 1.22096, 1.21871, 0.00014
        take = roundPrice('EUR_USD', entry + (entry - stop) * 1.2 + spread)
        self.assertGreater(take, entry)
        self.assertEqual(round(take, 1), 1.2)      # what the old code produced

    def test_the_old_one_decimal_rounding_would_have_destroyed_it(self):
        entry, stop, spread = 1.22096, 1.21871, 0.00014
        raw = entry + (entry - stop) * 1.2 + spread
        self.assertLess(round(raw, 1), stop)       # below the stop, let alone the entry


class TestSerieToDict(unittest.TestCase):

    def test_is_an_unimplemented_stub(self):
        self.assertIsNone(serieToDict(pd.Series([1, 2, 3])))


class TestGetLogger(unittest.TestCase):

    def test_resolves_config_from_parity_deriva_home(self):
        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with mock.patch.dict(os.environ, {'PARITY_DERIVA_HOME': pkg}, clear=False):
            with mock.patch('os.path.exists', side_effect=lambda p: p != 'logging.conf'):
                logger = getLogger()
        self.assertEqual(logger.name, 'parity_deriva.trading.trading')

    def test_explicit_config_path_is_honoured(self):
        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        conf = os.path.join(pkg, 'etc', 'logging.conf')
        logger = getLogger(conf, 'parity_deriva.trading.trading')
        self.assertEqual(logger.name, 'parity_deriva.trading.trading')


class TestOhlc(unittest.TestCase):

    def test_from_positional_values(self):
        bar = ohlc(T0, 1, 3, 0.5, 2, 7)
        self.assertEqual((bar.o, bar.h, bar.l, bar.c, bar.v), (1.0, 3.0, 0.5, 2.0, 7))
        self.assertEqual(bar.t, T0)

    def test_strings_are_coerced_to_float(self):
        bar = ohlc(T0, "1", "3", "0.5", "2", "7")
        self.assertEqual(bar.o, 1.0)
        self.assertIsInstance(bar.o, float)
        self.assertEqual(bar.v, 7)

    def test_oanda_timestamp_string_is_parsed(self):
        bar = ohlc(t=oanda_time(T0), o=1, h=2, l=0, c=1.5)
        self.assertEqual(bar.t, T0)

    def test_timestamp_is_none_when_not_given(self):
        self.assertIsNone(ohlc(o=1, h=2, l=0, c=1.5).t)

    def test_dict_as_first_argument_is_unpacked(self):
        bar = ohlc({"time": oanda_time(T0), "o": 2, "h": 3, "l": 1,
                    "c": 1.5, "volume": 4})
        self.assertEqual((bar.o, bar.h, bar.l, bar.c, bar.v), (2.0, 3.0, 1.0, 1.5, 4))
        self.assertEqual(bar.t, T0)

    def test_from_dict_without_volume_defaults_to_zero(self):
        bar = ohlc({"time": oanda_time(T0), "o": 2, "h": 3, "l": 1, "c": 1.5})
        self.assertEqual(bar.v, 0)

    def test_from_dict_explicit_time_argument_wins(self):
        bar = ohlc()
        bar.from_dict({"o": 1, "h": 2, "l": 0, "c": 1.5}, T0)
        self.assertEqual(bar.t, T0)

    def test_direction_and_shape(self):
        up = ohlc(T0, 1, 3, 0.5, 2)
        down = ohlc(T0, 2, 3, 0.5, 1)
        flat = ohlc(T0, 2, 3, 0.5, 2)
        self.assertEqual((up.direction(), down.direction(), flat.direction()), (1, -1, 0))
        self.assertTrue(up.isBull())
        self.assertFalse(up.isBear())
        self.assertTrue(down.isBear())
        self.assertFalse(flat.isBull())
        self.assertFalse(flat.isBear())

    def test_vola_is_the_full_range(self):
        self.assertEqual(ohlc(T0, 1, 3, 0.5, 2).vola(), 2.5)

    def test_ratio_is_body_over_range(self):
        self.assertAlmostEqual(ohlc(T0, 1, 3, 1, 2).ratio(), 0.5)

    def test_ratio_divides_by_zero_on_a_flat_bar(self):
        """A bar with h == l has no range; there is no guard."""
        with self.assertRaises(ZeroDivisionError):
            ohlc(T0, 1, 1, 1, 1).ratio()

    def test_to_dict_only_price(self):
        self.assertEqual(ohlc(T0, 1, 3, 0.5, 2).to_dict(True),
                         {'o': 1.0, 'h': 3.0, 'l': 0.5, 'c': 2.0})

    def test_to_dict_is_keyed_on_the_timestamp(self):
        out = ohlc(T0, 1, 3, 0.5, 2).to_dict()
        self.assertEqual(list(out), [T0])
        self.assertEqual(out[T0]['h'], 3.0)

    def test_to_dict_drops_the_volume(self):
        self.assertNotIn('v', ohlc(T0, 1, 3, 0.5, 2, 9).to_dict()[T0])

    def test_str(self):
        self.assertEqual(str(ohlc(T0, 1, 3, 0.5, 2, 9)),
                         "2017-02-01 10:00:00 O:1.0 H:3.0 L:0.5 C:2.0 V:9")

    def test_from_oanda_reads_the_requested_price_type(self):
        """
        Was: the method indexed the literal key 'type' instead of the price
             type it was passed, so it raised KeyError however it was called.
             Nothing in the codebase reached it, which is why that went
             unnoticed.
        Now: it reads dct[typ].
        """
        raw = {"time": oanda_time(T0), "volume": 3,
               "mid": {"o": 1, "h": 2, "l": 0, "c": 1.5},
               "ask": {"o": 1.2, "h": 2.2, "l": 0.2, "c": 1.7}}
        bar = ohlc()
        bar.from_oanda(raw, 'mid')
        self.assertEqual((bar.o, bar.c, bar.v), (1.0, 1.5, 3))
        self.assertEqual(bar.t, T0)
        bar.from_oanda(raw, 'ask')
        self.assertEqual(bar.c, 1.7)


class TestCandle(unittest.TestCase):

    def test_class_defaults_are_zero(self):
        c = Candle()
        self.assertEqual((c.o, c.h, c.l, c.c), (0, 0, 0, 0))

    def test_set_coerces_to_float(self):
        c = Candle()
        c.set({"o": "1", "h": "2", "l": "0", "c": "1.5"})
        self.assertEqual((c.o, c.h, c.l, c.c), (1.0, 2.0, 0.0, 1.5))

    def test_set_ignores_missing_and_extra_keys(self):
        c = Candle()
        c.set({"o": 1, "junk": 99})
        self.assertEqual(c.o, 1.0)
        self.assertEqual(c.c, 0)
        self.assertFalse(hasattr(c, 'junk'))

    def test_str(self):
        c = Candle()
        c.set({"o": 1, "h": 2, "l": 0, "c": 1.5})
        self.assertEqual(str(c), "O: 1.000000 H: 2.000000 L: 0.000000 C: 1.500000")

    def test_the_constructor_accepts_ohlc(self):
        """
        Was: the initialiser was spelled `__init`, which Python mangles to
             _Candle__init, leaving the class with no __init__ at all -
             Candle(1,2,3,4) raised TypeError and only the no-argument form
             worked.
        Now: the constructor takes o/h/l/c, or a dict.
        """
        c = Candle(1, 2, 0, 1.5)
        self.assertEqual((c.o, c.h, c.l, c.c), (1.0, 2.0, 0, 1.5))

    def test_the_constructor_accepts_a_dict(self):
        c = Candle({"o": 1, "h": 2, "l": 0, "c": 1.5})
        self.assertEqual((c.o, c.h, c.l, c.c), (1.0, 2.0, 0.0, 1.5))

    def test_the_no_argument_form_still_works(self):
        c = Candle()
        self.assertEqual((c.o, c.h, c.l, c.c), (0, 0, 0, 0))


class TestOANDAObjects(unittest.TestCase):

    def test_attributes_come_from_the_dict(self):
        o = OANDAObject(5, {"instrument": "DE30_EUR", "price": 11700.0})
        self.assertEqual(o.instrument, "DE30_EUR")
        self.assertEqual(o.price, 11700.0)

    def test_explicit_id_overrides_an_id_in_the_dict(self):
        """add() runs before self.id is assigned, so the argument wins."""
        self.assertEqual(OANDAObject(5, {"id": 99}).id, 5)

    def test_to_dict_exposes_the_instance_dict_including_the_logger(self):
        d = OANDAObject(5, {"price": 1.0}).to_dict()
        self.assertEqual(d['price'], 1.0)
        self.assertEqual(d['id'], 5)
        self.assertIn('logger', d)

    def test_dump_renders_every_attribute(self):
        text = OANDAObject(5, {"price": 1.0}).dump()
        self.assertIn("price: 1.0", text)
        self.assertIn("id: 5", text)

    def test_order_defaults(self):
        o = OANDAOrder(1, {})
        self.assertEqual(o.state, '')
        self.assertEqual(o.type, 'STOP_LOSS')
        self.assertEqual(o.units, 0)
        self.assertIsNone(o.SLOrder)
        self.assertIsNone(o.TPOrder)

    def test_order_has_no_stoploss_attribute_by_default(self):
        """OANDABacktester.handleSLTP relies on the order dict carrying it."""
        self.assertFalse(hasattr(OANDAOrder(1, {}), 'stopLoss'))

    def test_trade_and_position_defaults(self):
        self.assertIsNone(OANDATrade(1, {}).state)
        self.assertEqual(OANDATrade(1, {}).realizedPL, 0.0)
        self.assertEqual(OANDAPosition(1, {}).instrument, "")
        self.assertEqual(OANDAPositionSize(1, {}).units, 0.0)


if __name__ == "__main__":
    unittest.main()
