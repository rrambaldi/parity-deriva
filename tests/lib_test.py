"""Tests for parity_deriva.lib (utils, ohlc, candle, oanda)."""

import datetime
import os
import unittest
from unittest import mock

import pandas as pd

from parity_deriva.lib import indicators
from parity_deriva.lib.candle import Candle
from parity_deriva.lib.oanda import (OANDAObject, OANDAOrder, OANDATrade,
                               OANDAPosition, OANDAPositionSize)
from parity_deriva.lib.ohlc import ohlc
from parity_deriva.lib.utils import (datetimeToString, timestampFromString,
                               granularityToTimedelta, dctFromOanda,
                               serieToDict, getLogger, pricePrecision,
                               roundPrice, expiryAt)
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


class ExpiryTest(unittest.TestCase):
    """
    lib/utils.expiryAt: when an order issued on a candle stops being an order.

    It used to be datetime.today(), the machine's clock, which in a replay is
    years away from the candles - so nothing ever expired.
    """

    CLOCK = ('23', '59', '59')

    def test_a_five_minute_bar_expires_that_evening(self):
        self.assertEqual(
            expiryAt(datetime.datetime(2018, 1, 15, 10, 0), 'M5',
                           self.CLOCK),
            datetime.datetime(2018, 1, 15, 23, 59, 59))

    def test_a_daily_bar_expires_at_the_end_of_the_day_it_closes_in(self):
        """
        Not the day it opens in. A daily candle stamped the 3rd closes at
        midnight on the 4th, and the signal is made at that close: measured
        from the open, the order would be issued after its own expiry and
        could never fill at all.
        """
        self.assertEqual(
            expiryAt(datetime.datetime(2022, 7, 3), 'D', self.CLOCK),
            datetime.datetime(2022, 7, 4, 23, 59, 59))

    def test_the_last_bar_of_a_day_belongs_to_the_next_one(self):
        """An H1 bar at 23:00 closes at midnight, so its order rests a day."""
        self.assertEqual(
            expiryAt(datetime.datetime(2018, 1, 15, 23, 0), 'H1',
                           self.CLOCK),
            datetime.datetime(2018, 1, 16, 23, 59, 59))

    def test_a_granularity_nobody_can_read_leaves_the_candle_alone(self):
        """No bar width to add, so the candle's own day is the day."""
        self.assertEqual(
            expiryAt(datetime.datetime(2018, 1, 15, 10, 0), 'X9',
                           self.CLOCK),
            datetime.datetime(2018, 1, 15, 23, 59, 59))

    def test_the_hour_is_the_one_asked_for(self):
        self.assertEqual(
            expiryAt(datetime.datetime(2018, 1, 15, 10, 0), 'M5',
                           ('17', '30', '0')),
            datetime.datetime(2018, 1, 15, 17, 30, 0))


class IndicatorCase(unittest.TestCase):
    """
    lib/indicators.py. A ramp is the fixture: on 1, 2, 3, ... every average is
    a number that can be checked without a second implementation to check it
    against.
    """

    RAMP = [float(i) for i in range(1, 21)]

    def test_an_average_is_absent_until_it_is_warm(self):
        """
        None, not a mean of whatever bars exist so far. A chart that drew one
        would show an SMA(100) that is really an SMA(3) for its first
        ninety-seven bars, over exactly the trades a backtest starts with.
        """
        values = indicators.sma(self.RAMP, 5)
        self.assertEqual(values[:4], [None] * 4)
        self.assertEqual(values[4], 3.0)

    def test_the_simple_average_is_the_mean_of_its_window(self):
        self.assertEqual(indicators.sma(self.RAMP, 3)[2:5], [2.0, 3.0, 4.0])

    #: a straight line of 0.1 a bar, drawn with a 0.05 wick either side. Every
    #: true range on it is 0.15 - the high against the bar before's close -
    #: so the slope measure is 0.1 / 0.15 whatever window it is read over
    SLOPE_STEP = 0.1
    SLOPE_TR = 0.15

    def ramp(self, n=160):
        closes = [100.0 + self.SLOPE_STEP * i for i in range(n)]
        return (closes, [c + 0.05 for c in closes], [c - 0.05 for c in closes])

    def test_the_slope_is_the_move_per_bar_in_atr(self):
        closes, highs, lows = self.ramp()
        values = indicators.slope(closes, highs, lows, period=20, window=10,
                                  atrPeriod=14)
        self.assertAlmostEqual(values[-1], self.SLOPE_STEP / self.SLOPE_TR, 6)

    def test_the_window_divides_out_of_the_reading(self):
        """
        The whole point of dividing by N: the same market has to read the same
        over ten bars and over twenty, or a threshold on it means a different
        thing for every window and cannot be carried between them.
        """
        closes, highs, lows = self.ramp()
        short = indicators.slope(closes, highs, lows, period=20, window=10)
        long_ = indicators.slope(closes, highs, lows, period=20, window=20)
        self.assertAlmostEqual(short[-1], long_[-1], 6)

    def test_the_slope_is_absent_until_both_averages_are_warm(self):
        closes, highs, lows = self.ramp(n=40)
        values = indicators.slope(closes, highs, lows, period=30, window=10)
        self.assertEqual(values[:38], [None] * 38)
        self.assertIsNotNone(values[-1])

    def test_a_falling_line_reads_negative(self):
        closes, highs, lows = self.ramp()
        closes = list(reversed(closes))
        highs = [c + 0.05 for c in closes]
        lows = [c - 0.05 for c in closes]
        values = indicators.slope(closes, highs, lows, period=20, window=10)
        self.assertLess(values[-1], 0)

    def test_a_percentile_lands_between_its_two_neighbours(self):
        self.assertEqual(indicators.percentile([0, 1, 2, 3, 4], 50), 2)
        self.assertEqual(indicators.percentile([1.0, 2.0], 75), 1.75)
        self.assertEqual(indicators.percentile([5.0], 90), 5.0)
        self.assertIsNone(indicators.percentile([], 90))

    def test_a_period_longer_than_the_data_is_all_absent(self):
        self.assertEqual(indicators.sma([1.0, 2.0], 5), [None, None])

    def test_the_exponential_average_is_seeded_with_a_simple_one(self):
        """
        Not started from the first close. This is what the strategies here do,
        so the line on the chart is the line that was traded; the two
        disagree for dozens of bars otherwise, which is where the early trades
        of every backtest are.
        """
        values = indicators.ema(self.RAMP, 4)
        self.assertEqual(values[:3], [None] * 3)
        self.assertAlmostEqual(values[3], 2.5)          # (1+2+3+4)/4
        self.assertAlmostEqual(values[4], 5 * 0.4 + 2.5 * 0.6)

    def test_the_bands_sit_either_side_of_their_middle(self):
        middle, upper, lower = indicators.bollinger(self.RAMP, 5, 2.0)
        self.assertEqual(middle[4], 3.0)
        # population deviation of 1..5 is sqrt(2)
        self.assertAlmostEqual(upper[4], 3.0 + 2 * (2 ** 0.5))
        self.assertAlmostEqual(lower[4], 3.0 - 2 * (2 ** 0.5))
        self.assertEqual(middle[:4], [None] * 4)

    def test_a_flat_series_has_no_width(self):
        _, upper, lower = indicators.bollinger([5.0] * 10, 4, 2.0)
        self.assertEqual(upper[5], 5.0)
        self.assertEqual(lower[5], 5.0)

    def test_nothing_reads_a_later_bar(self):
        """
        The rule the whole module rests on: a curve computed over the run
        agrees at every point with one computed over the run cut there. A
        chart that broke it would draw a line the strategy could not have
        seen.
        """
        for kind in ('sma', 'ema'):
            whole = getattr(indicators, kind)(self.RAMP, 4)
            for cut in range(4, len(self.RAMP) + 1):
                part = getattr(indicators, kind)(self.RAMP[:cut], 4)
                self.assertAlmostEqual(part[cut - 1], whole[cut - 1],
                                       msg="%s at %d" % (kind, cut - 1))

    def test_the_true_range_of_the_first_bar_is_its_own_range(self):
        """No previous close to reach for, which is Wilder's own handling."""
        ranges = indicators.true_range([3.0, 4.0], [1.0, 2.0], [2.0, 3.0])
        self.assertEqual(ranges[0], 2.0)

    def test_the_true_range_reaches_across_a_gap(self):
        """
        A bar that opened above the last close is a 3-wide move even if its
        own high minus its own low is 1. Reading the bar alone would call a
        gap quiet.
        """
        ranges = indicators.true_range([10.0, 14.0], [9.0, 13.0], [9.5, 13.5])
        self.assertEqual(ranges[1], 4.5)          # 14 - 9.5

    def test_the_atr_is_seeded_with_the_mean_of_its_first_ranges(self):
        highs = [2.0, 3.0, 4.0, 5.0]
        lows = [1.0, 2.0, 3.0, 4.0]
        closes = [1.5, 2.5, 3.5, 4.5]
        # ranges: 1.0, then 3 - 1.5 = 1.5 each
        values = indicators.atr(highs, lows, closes, 3)
        self.assertEqual(values[:2], [None, None])
        self.assertAlmostEqual(values[2], (1.0 + 1.5 + 1.5) / 3)
        # Wilder's step: previous + (range - previous) / period
        self.assertAlmostEqual(values[3],
                               values[2] + (1.5 - values[2]) / 3)

    def test_the_atr_is_drawn_on_its_own_axis(self):
        """
        An ATR of 0.004 on the price axis of a chart at 1.2 does not read as a
        low line: it drags the scale to zero and flattens every candle. The
        page is told which axis a curve is on rather than guessing from the
        size of the numbers.
        """
        highs = [float(i) + 1 for i in range(1, 21)]
        curve = indicators.curve({'kind': 'atr', 'period': 3}, self.RAMP,
                                 highs, self.RAMP)
        self.assertTrue(curve['panel'])
        self.assertEqual(curve['label'], 'ATR 3')
        self.assertFalse(indicators.curve({'kind': 'sma', 'period': 3},
                                          self.RAMP)['panel'])

    def test_an_atr_of_the_closes_is_refused_rather_than_computed(self):
        """A different number with the same name, which is the worst kind."""
        with self.assertRaises(ValueError) as caught:
            indicators.curve({'kind': 'atr', 'period': 3}, self.RAMP)
        self.assertIn('highs', str(caught.exception))

    def test_a_declaration_becomes_a_drawable_curve(self):
        curve = indicators.curve({'kind': 'sma', 'period': 3}, self.RAMP)
        self.assertEqual(curve['label'], 'SMA 3')
        self.assertEqual(curve['values'][2], 2.0)

    def test_a_band_declares_its_three_series(self):
        curve = indicators.curve(
            {'kind': 'bollinger', 'period': 5, 'deviations': 1.5}, self.RAMP)
        self.assertEqual(curve['label'], 'Bollinger 5 / 1.5')
        for name in ('middle', 'upper', 'lower'):
            self.assertEqual(len(curve[name]), len(self.RAMP))

    def test_an_indicator_nobody_wrote_is_refused_by_name(self):
        """
        Rather than quietly missing from the chart: a strategy declaring one
        has made a mistake, and a curve that is absent looks exactly like a
        curve that was never asked for.
        """
        with self.assertRaises(ValueError) as caught:
            indicators.curve({'kind': 'kagi', 'period': 3}, self.RAMP)
        self.assertIn('kagi', str(caught.exception))

    def test_declaring_nothing_draws_nothing(self):
        self.assertEqual(indicators.curves(None, self.RAMP), [])
        self.assertEqual(indicators.curves((), self.RAMP), [])
