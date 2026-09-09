"""
Characterisation tests for the tick-CSV backtest path inherited from upstream
parity-deriva: HistoricCSVPriceHandler, the example strategies, Portfolio/Position
accounting, the drawdown maths and the Backtest driver.

This path is separate from the live event bus: it predates the dictionary-based
Event hierarchy and was never migrated to it, so these tests also pin the
adapter layer that now connects the two.
"""

import datetime
import os
import queue
import unittest
from decimal import Decimal

import numpy as np
import pandas as pd

from parity_deriva.backtest.backtest import Backtest
from parity_deriva.data.price import PriceHandler, HistoricCSVPriceHandler
from parity_deriva.event.event import TickEvent, SignalEvent
from parity_deriva.execution.execution import SimulatedExecution
from parity_deriva.performance.performance import create_drawdowns
from parity_deriva.portfolio.portfolio import Portfolio
from parity_deriva.portfolio.position import Position
from parity_deriva.strategy.strategy import (TestStrategy, MovingAverageCrossStrategy,
                                       _signal)
from parity_deriva.tests.helpers import TempDirCase


TICKS = """Time,Ask,Bid,AskVolume,BidVolume
02.01.2014 00:00:01.833,1.50349,1.50328,2.3600,2.1500
02.01.2014 00:00:03.257,1.50352,1.50331,1.5700,2.6900
02.01.2014 00:00:05.100,1.50355,1.50334,1.1000,1.2000
"""

EUR_TICKS = """Time,Ask,Bid,AskVolume,BidVolume
02.01.2014 00:00:02.000,1.07847,1.07832,1.0000,1.0000
02.01.2014 00:00:04.000,1.07850,1.07835,1.0000,1.0000
"""


class TestSignalAdapter(unittest.TestCase):
    """
    The example strategies speak (instrument, order_type, side, time); the
    Event hierarchy is dictionary-driven. _signal() is the bridge.
    """

    def test_it_produces_a_routable_signal(self):
        sig = _signal("GBPUSD", "market", "buy", datetime.datetime(2014, 1, 2))
        self.assertEqual(str(sig), 'SIGNAL')
        self.assertEqual(sig.type, 'SIGNAL')

    def test_the_fields_the_portfolio_reads_are_present(self):
        sig = _signal("GBPUSD", "market", "sell", datetime.datetime(2014, 1, 2))
        self.assertEqual(sig.instrument, "GBPUSD")
        self.assertEqual(sig.side, "sell")
        self.assertEqual(sig.orderType, "market")
        self.assertEqual(sig.time, datetime.datetime(2014, 1, 2))


class TestPriceHandlerHelpers(unittest.TestCase):

    def test_the_price_dictionary_holds_each_pair_and_its_inverse(self):
        handler = PriceHandler()
        handler.pairs = ["GBPUSD", "EURUSD"]
        prices = handler._set_up_prices_dict()
        self.assertEqual(sorted(prices),
                         ["EURUSD", "GBPUSD", "USDEUR", "USDGBP"])
        self.assertEqual(prices["GBPUSD"], {"bid": None, "ask": None, "time": None})

    def test_inverting_a_quote_swaps_the_pair_and_reciprocates(self):
        handler = PriceHandler()
        pair, bid, ask = handler.invert_prices("GBPUSD", Decimal("1.50328"),
                                               Decimal("1.50349"))
        self.assertEqual(pair, "USDGBP")
        self.assertEqual(bid, Decimal("0.66521"))
        self.assertEqual(ask, Decimal("0.66512"))

    def test_the_inverted_bid_ends_up_above_the_inverted_ask(self):
        """Reciprocating without re-ordering leaves the spread inside out."""
        _, bid, ask = handler_invert()
        self.assertGreater(bid, ask)


def handler_invert():
    handler = PriceHandler()
    return handler.invert_prices("GBPUSD", Decimal("1.50328"), Decimal("1.50349"))


class CSVCase(TempDirCase):

    def write(self, name, body):
        with open(self.path(name), "w") as fh:
            fh.write(body)

    def handler(self, pairs=("GBPUSD",)):
        import parity_deriva.data.price as price_mod
        self.patched = price_mod
        old = price_mod.settings.CSV_DATA_DIR
        price_mod.settings.CSV_DATA_DIR = self.tmpdir
        self.addCleanup(setattr, price_mod.settings, 'CSV_DATA_DIR', old)
        self.events = queue.Queue()
        return HistoricCSVPriceHandler(list(pairs), self.events, self.tmpdir)


class TestHistoricCSVPriceHandler(CSVCase):

    def test_it_discovers_the_dated_files(self):
        self.write("GBPUSD_20140102.csv", TICKS)
        self.write("GBPUSD_20140103.csv", TICKS)
        handler = self.handler()
        self.assertEqual(handler.file_dates, ["20140102", "20140103"])

    def test_files_that_do_not_match_the_pattern_are_ignored(self):
        self.write("GBPUSD_20140102.csv", TICKS)
        self.write("notes.txt", "junk")
        self.write("GBPUSD.csv", TICKS)
        handler = self.handler()
        self.assertEqual(handler.file_dates, ["20140102"])

    def test_a_tick_is_published_per_row(self):
        self.write("GBPUSD_20140102.csv", TICKS)
        handler = self.handler()
        handler.stream_next_tick()
        tick = self.events.get_nowait()
        self.assertEqual(str(tick), 'TICK')
        self.assertEqual(tick.instrument, "GBPUSD")

    def test_prices_are_decimals_quantised_to_five_places(self):
        self.write("GBPUSD_20140102.csv", TICKS)
        handler = self.handler()
        handler.stream_next_tick()
        self.assertEqual(handler.prices["GBPUSD"]["bid"], Decimal("1.50328"))
        self.assertIsInstance(handler.prices["GBPUSD"]["ask"], Decimal)

    def test_the_inverse_pair_is_priced_at_the_same_time(self):
        self.write("GBPUSD_20140102.csv", TICKS)
        handler = self.handler()
        handler.stream_next_tick()
        self.assertIsNotNone(handler.prices["USDGBP"]["bid"])
        self.assertEqual(handler.prices["USDGBP"]["time"],
                         handler.prices["GBPUSD"]["time"])

    def test_the_timestamp_is_parsed_day_first(self):
        self.write("GBPUSD_20140102.csv", TICKS)
        handler = self.handler()
        handler.stream_next_tick()
        tick = self.events.get_nowait()
        self.assertEqual(tick.time, pd.Timestamp("2014-01-02 00:00:01.833"))

    def test_the_tick_timestamp_is_not_reset_to_the_epoch(self):
        """
        The tick carries a pandas Timestamp, which parse_time passes through.
        A plain datetime coercion here would silently date every tick 1970.
        """
        self.write("GBPUSD_20140102.csv", TICKS)
        handler = self.handler()
        handler.stream_next_tick()
        self.assertEqual(self.events.get_nowait().time.year, 2014)

    def test_multiple_pairs_are_merged_in_time_order(self):
        self.write("GBPUSD_20140102.csv", TICKS)
        self.write("EURUSD_20140102.csv", EUR_TICKS)
        handler = self.handler(pairs=("GBPUSD", "EURUSD"))
        seen = []
        for _ in range(5):
            handler.stream_next_tick()
            seen.append(self.events.get_nowait())
        self.assertEqual([t.instrument for t in seen],
                         ["GBPUSD", "EURUSD", "GBPUSD", "EURUSD", "GBPUSD"])
        self.assertEqual(seen, sorted(seen, key=lambda t: t.time))

    def test_it_rolls_over_to_the_next_day(self):
        self.write("GBPUSD_20140102.csv", TICKS)
        self.write("GBPUSD_20140103.csv", TICKS.replace("02.01.2014", "03.01.2014"))
        handler = self.handler()
        for _ in range(4):
            handler.stream_next_tick()
        self.assertTrue(handler.continue_backtest)
        self.assertEqual(handler.cur_date_idx, 1)

    def test_the_backtest_flag_clears_at_the_end_of_the_data(self):
        self.write("GBPUSD_20140102.csv", TICKS)
        handler = self.handler()
        for _ in range(4):
            handler.stream_next_tick()
        self.assertFalse(handler.continue_backtest)

    def test_no_extra_tick_is_published_after_the_data_runs_out(self):
        self.write("GBPUSD_20140102.csv", TICKS)
        handler = self.handler()
        for _ in range(6):
            handler.stream_next_tick()
        self.assertEqual(self.events.qsize(), 3)


class TestExampleStrategies(unittest.TestCase):

    def tick(self, instrument="GBPUSD", bid=1.5):
        return TickEvent({"type": "TICK", "instrument": instrument,
                          "time": datetime.datetime(2014, 1, 2),
                          "bid": bid, "ask": bid + 0.0002})

    def test_the_test_strategy_alternates_every_fifth_tick(self):
        events = queue.Queue()
        strategy = TestStrategy(["GBPUSD"], events)
        for _ in range(11):
            strategy.calculate_signals(self.tick())
        sides = []
        while not events.empty():
            sides.append(events.get_nowait().side)
        self.assertEqual(sides, ["buy", "sell", "buy"])

    def test_the_test_strategy_only_watches_the_first_pair(self):
        events = queue.Queue()
        strategy = TestStrategy(["GBPUSD", "EURUSD"], events)
        for _ in range(10):
            strategy.calculate_signals(self.tick(instrument="EURUSD"))
        self.assertTrue(events.empty())

    def test_the_moving_average_state_is_per_pair(self):
        strategy = MovingAverageCrossStrategy(["GBPUSD", "EURUSD"], queue.Queue())
        self.assertEqual(sorted(strategy.pairs_dict), ["EURUSD", "GBPUSD"])
        self.assertIsNot(strategy.pairs_dict["GBPUSD"],
                         strategy.pairs_dict["EURUSD"])

    def test_the_rolling_average_seeds_from_the_first_tick(self):
        strategy = MovingAverageCrossStrategy(["GBPUSD"], queue.Queue(),
                                              short_window=3, long_window=5)
        strategy.calculate_signals(self.tick(bid=1.5))
        state = strategy.pairs_dict["GBPUSD"]
        self.assertEqual(state["short_sma"], 1.5)
        self.assertEqual(state["long_sma"], 1.5)

    def test_the_rolling_average_formula(self):
        strategy = MovingAverageCrossStrategy(["GBPUSD"], queue.Queue(),
                                              short_window=3, long_window=5)
        self.assertAlmostEqual(strategy.calc_rolling_sma(1.5, 3, 1.8), 1.6)

    def test_no_signal_before_the_short_window_is_filled(self):
        events = queue.Queue()
        strategy = MovingAverageCrossStrategy(["GBPUSD"], events,
                                              short_window=5, long_window=10)
        for i in range(5):
            strategy.calculate_signals(self.tick(bid=1.5 + i * 0.01))
        self.assertTrue(events.empty())

    def test_a_rising_market_eventually_buys_once(self):
        events = queue.Queue()
        strategy = MovingAverageCrossStrategy(["GBPUSD"], events,
                                              short_window=3, long_window=8)
        for i in range(20):
            strategy.calculate_signals(self.tick(bid=1.5 + i * 0.01))
        sides = []
        while not events.empty():
            sides.append(events.get_nowait().side)
        self.assertEqual(sides, ["buy"])
        self.assertTrue(strategy.pairs_dict["GBPUSD"]["invested"])

    def test_a_reversal_closes_the_position(self):
        events = queue.Queue()
        strategy = MovingAverageCrossStrategy(["GBPUSD"], events,
                                              short_window=3, long_window=8)
        for i in range(20):
            strategy.calculate_signals(self.tick(bid=1.5 + i * 0.01))
        for i in range(40):
            strategy.calculate_signals(self.tick(bid=1.7 - i * 0.01))
        sides = []
        while not events.empty():
            sides.append(events.get_nowait().side)
        self.assertEqual(sides, ["buy", "sell"])
        self.assertFalse(strategy.pairs_dict["GBPUSD"]["invested"])

    def test_it_is_long_only(self):
        """It never sells without an open long, so it cannot go short."""
        events = queue.Queue()
        strategy = MovingAverageCrossStrategy(["GBPUSD"], events,
                                              short_window=3, long_window=8)
        for i in range(30):
            strategy.calculate_signals(self.tick(bid=1.7 - i * 0.01))
        self.assertTrue(events.empty())


class TickerMock(object):

    def __init__(self):
        self.pairs = ["GBPUSD", "EURUSD"]
        self.prices = {
            "GBPUSD": {"bid": Decimal("1.50328"), "ask": Decimal("1.50349")},
            "USDGBP": {"bid": Decimal("0.66521"), "ask": Decimal("0.66512")},
            "EURUSD": {"bid": Decimal("1.07832"), "ask": Decimal("1.07847")},
            "USDEUR": {"bid": Decimal("0.92736"), "ask": Decimal("0.92724")},
        }


class PortfolioCase(TempDirCase):
    """Complements the upstream portfolio_test.py rather than repeating it."""

    def setUp(self):
        super(PortfolioCase, self).setUp()
        import parity_deriva.portfolio.portfolio as pf
        self.pf = pf
        self.old_dir = pf.OUTPUT_RESULTS_DIR
        pf.OUTPUT_RESULTS_DIR = self.tmpdir
        self.addCleanup(setattr, pf, 'OUTPUT_RESULTS_DIR', self.old_dir)
        self.events = queue.Queue()
        self.ticker = TickerMock()

    def portfolio(self, **kw):
        kw.setdefault('home_currency', "GBP")
        port = Portfolio(self.ticker, self.events, **kw)
        if getattr(port, 'backtest_file', None):
            self.addCleanup(port.backtest_file.close)
        return port


class TestPortfolioBookkeeping(PortfolioCase):

    def test_trade_size_is_equity_times_risk(self):
        port = self.portfolio(equity=Decimal("100000.00"),
                              risk_per_trade=Decimal("0.02"))
        self.assertEqual(port.trade_units, Decimal("2000.0000"))

    def test_the_balance_starts_at_the_equity(self):
        port = self.portfolio(equity=Decimal("50000.00"))
        self.assertEqual(port.balance, Decimal("50000.00"))

    def test_a_backtest_writes_a_header_row(self):
        port = self.portfolio()
        port.backtest_file.flush()
        with open(self.path("backtest.csv")) as fh:
            self.assertEqual(fh.readline().strip(), "Timestamp,Balance,GBPUSD,EURUSD")

    def test_the_header_is_not_flushed_until_the_first_tick(self):
        """
        create_equity_file() writes and returns without flushing, so a crash
        before the first tick leaves an empty backtest.csv behind.
        """
        self.portfolio()
        with open(self.path("backtest.csv")) as fh:
            self.assertEqual(fh.read(), "")

    def test_no_equity_file_outside_a_backtest(self):
        port = self.portfolio(backtest=False)
        self.assertFalse(hasattr(port, 'backtest_file'))
        self.assertFalse(os.path.exists(self.path("backtest.csv")))

    def test_a_tick_appends_a_row_per_pair(self):
        port = self.portfolio()
        port.update_portfolio(TickEvent({"instrument": "GBPUSD",
                                         "time": datetime.datetime(2014, 1, 2)}))
        port.backtest_file.flush()
        with open(self.path("backtest.csv")) as fh:
            rows = fh.read().strip().split("\n")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].split(",")[2:], ["0.00", "0.00"])

    def test_an_open_position_shows_its_unrealised_pnl(self):
        port = self.portfolio()
        port.add_new_position("long", "GBPUSD", Decimal("2000"), self.ticker)
        port.update_portfolio(TickEvent({"instrument": "GBPUSD",
                                         "time": datetime.datetime(2014, 1, 2)}))
        port.backtest_file.flush()
        with open(self.path("backtest.csv")) as fh:
            last = fh.read().strip().split("\n")[-1]
        self.assertNotEqual(last.split(",")[2], "0.00")

    def test_a_signal_opens_a_position_and_emits_an_order(self):
        port = self.portfolio()
        port.execute_event(_signal("GBPUSD", "market", "buy",
                                   datetime.datetime(2014, 1, 2)))
        self.assertIn("GBPUSD", port.positions)
        self.assertEqual(port.positions["GBPUSD"].position_type, "long")
        order = self.events.get_nowait()
        self.assertEqual(str(order), 'ORDER')
        self.assertEqual(order.side, "buy")
        self.assertEqual(order.units, int(port.trade_units))

    def test_a_sell_signal_opens_a_short(self):
        port = self.portfolio()
        port.execute_event(_signal("GBPUSD", "market", "sell",
                                   datetime.datetime(2014, 1, 2)))
        self.assertEqual(port.positions["GBPUSD"].position_type, "short")

    def test_an_opposite_signal_of_equal_size_closes_the_position(self):
        port = self.portfolio()
        port.execute_event(_signal("GBPUSD", "market", "buy",
                                   datetime.datetime(2014, 1, 2)))
        port.execute_event(_signal("GBPUSD", "market", "sell",
                                   datetime.datetime(2014, 1, 2)))
        self.assertNotIn("GBPUSD", port.positions)

    def test_nothing_is_executed_while_a_pair_is_unpriced(self):
        self.ticker.prices["EURUSD"]["bid"] = None
        port = self.portfolio()
        port.execute_event(_signal("GBPUSD", "market", "buy",
                                   datetime.datetime(2014, 1, 2)))
        self.assertEqual(port.positions, {})
        self.assertTrue(self.events.empty())

    def test_output_results_derives_the_equity_curve(self):
        port = self.portfolio()
        for i in range(4):
            port.add_new_position("long", "GBPUSD", Decimal("2000"), self.ticker)
            port.update_portfolio(TickEvent({
                "instrument": "GBPUSD",
                "time": datetime.datetime(2014, 1, 2, 0, 0, i)}))
        port.output_results()
        curve = pd.read_csv(self.path("equity.csv"), index_col=0)
        self.assertEqual(sorted(curve.columns)[:2], ['Balance', 'Drawdown'])
        for column in ('Total', 'Returns', 'Equity', 'Drawdown'):
            self.assertIn(column, curve.columns)


class TestPositionUpdates(unittest.TestCase):
    """Position arithmetic not already covered by the upstream tests."""

    def position(self, kind="long"):
        return Position("GBP", kind, "GBPUSD", Decimal("2000"), TickerMock())

    def test_a_long_is_opened_at_the_ask_and_marked_at_the_bid(self):
        pos = self.position()
        self.assertEqual(pos.avg_price, Decimal("1.50349"))
        self.assertEqual(pos.cur_price, Decimal("1.50328"))

    def test_a_short_is_opened_at_the_bid_and_marked_at_the_ask(self):
        pos = self.position("short")
        self.assertEqual(pos.avg_price, Decimal("1.50328"))
        self.assertEqual(pos.cur_price, Decimal("1.50349"))

    def test_the_quote_home_pair_is_derived_from_the_traded_pair(self):
        self.assertEqual(self.position().quote_home_currency_pair, "USDGBP")

    def test_a_fresh_long_starts_down_by_the_spread(self):
        self.assertEqual(self.position().calculate_pips(), Decimal("-0.00021"))

    def test_adding_units_averages_the_entry(self):
        pos = self.position()
        pos.add_units(Decimal("2000"))
        self.assertEqual(pos.units, Decimal("4000"))
        self.assertEqual(pos.avg_price, Decimal("1.50349"))

    def test_removing_units_realises_a_decimal_pnl(self):
        pos = self.position()
        pnl = pos.remove_units(Decimal("500"))
        self.assertEqual(pos.units, Decimal("1500"))
        self.assertIsInstance(pnl, Decimal)
        self.assertEqual(pnl.as_tuple().exponent, -2)

    def test_closing_realises_the_pnl_over_the_whole_size(self):
        pos = self.position()
        self.assertEqual(pos.close_position(), Decimal("-0.28"))

    def test_updating_the_mark_moves_the_unrealised_pnl(self):
        pos = self.position()
        before = pos.profit_base
        pos.ticker.prices["GBPUSD"]["bid"] = Decimal("1.51000")
        pos.update_position_price()
        self.assertGreater(pos.profit_base, before)


class TestCreateDrawdowns(unittest.TestCase):

    def curve(self, values):
        idx = pd.date_range("2014-01-02", periods=len(values), freq="s")
        return pd.Series(values, index=idx)

    def test_a_monotonic_rise_has_no_drawdown(self):
        drawdown, max_dd, _ = create_drawdowns(self.curve([1.0, 1.1, 1.2, 1.3]))
        self.assertEqual(max_dd, 0.0)
        self.assertEqual(list(drawdown.iloc[1:]), [0.0, 0.0, 0.0])

    def test_a_dip_is_measured_from_the_high_water_mark(self):
        _, max_dd, _ = create_drawdowns(self.curve([1.0, 1.2, 1.1, 1.3]))
        self.assertAlmostEqual(max_dd, 0.1)

    def test_the_series_keeps_the_input_index(self):
        curve = self.curve([1.0, 1.2, 1.1])
        drawdown, _, _ = create_drawdowns(curve)
        self.assertTrue(drawdown.index.equals(curve.index))

    def test_the_first_point_is_never_filled_in(self):
        """The loop starts at index 1, so position 0 of both series stays NaN."""
        drawdown, max_dd, duration = create_drawdowns(self.curve([1.0, 1.2, 1.1]))
        self.assertTrue(np.isnan(drawdown.iloc[0]))
        self.assertFalse(np.isnan(max_dd))
        self.assertFalse(np.isnan(duration))

    def test_the_second_point_always_reports_a_zero_drawdown(self):
        """
        The high water mark is seeded with 0 rather than with the first value,
        so hwm[1] is always pnl[1] and drawdown[1] is always exactly 0 - a
        first bar that drops is invisible to the drawdown series, and the
        duration counter is reset by it.
        """
        drawdown, _, duration = create_drawdowns(self.curve([1.0, 0.5, 0.4]))
        self.assertEqual(drawdown.iloc[1], 0.0)
        self.assertEqual(duration, 1.0)

    def test_the_duration_counts_consecutive_bars_below_the_peak(self):
        _, _, duration = create_drawdowns(self.curve([1.0, 1.5, 1.4, 1.3, 1.2]))
        self.assertEqual(duration, 3.0)


class TestBacktestDriver(CSVCase):

    def test_it_wires_the_components_and_runs_to_completion(self):
        self.write("GBPUSD_20140102.csv", TICKS)
        import parity_deriva.backtest.backtest as bt
        import parity_deriva.portfolio.portfolio as pf
        old_csv, old_out = bt.settings.CSV_DATA_DIR, pf.OUTPUT_RESULTS_DIR
        bt.settings.CSV_DATA_DIR = self.tmpdir
        pf.OUTPUT_RESULTS_DIR = self.tmpdir
        self.addCleanup(setattr, bt.settings, 'CSV_DATA_DIR', old_csv)
        self.addCleanup(setattr, pf, 'OUTPUT_RESULTS_DIR', old_out)

        import parity_deriva.data.price as price_mod
        old_price = price_mod.settings.CSV_DATA_DIR
        price_mod.settings.CSV_DATA_DIR = self.tmpdir
        self.addCleanup(setattr, price_mod.settings, 'CSV_DATA_DIR', old_price)

        backtest = Backtest(
            ["GBPUSD"], HistoricCSVPriceHandler,
            MovingAverageCrossStrategy, {"short_window": 2, "long_window": 3},
            Portfolio, SimulatedExecution,
            equity=Decimal("100000.00"), max_iters=50)
        backtest.simulate_trading()

        self.assertTrue(os.path.exists(self.path("backtest.csv")))
        self.assertTrue(os.path.exists(self.path("equity.csv")))

    def test_the_driver_owns_its_own_queue(self):
        self.write("GBPUSD_20140102.csv", TICKS)
        import parity_deriva.data.price as price_mod
        old = price_mod.settings.CSV_DATA_DIR
        price_mod.settings.CSV_DATA_DIR = self.tmpdir
        self.addCleanup(setattr, price_mod.settings, 'CSV_DATA_DIR', old)
        import parity_deriva.backtest.backtest as bt
        old_csv = bt.settings.CSV_DATA_DIR
        bt.settings.CSV_DATA_DIR = self.tmpdir
        self.addCleanup(setattr, bt.settings, 'CSV_DATA_DIR', old_csv)

        import parity_deriva.portfolio.portfolio as pf
        old_out = pf.OUTPUT_RESULTS_DIR
        pf.OUTPUT_RESULTS_DIR = self.tmpdir
        self.addCleanup(setattr, pf, 'OUTPUT_RESULTS_DIR', old_out)

        backtest = Backtest(
            ["GBPUSD"], HistoricCSVPriceHandler,
            MovingAverageCrossStrategy, {}, Portfolio, SimulatedExecution,
            equity=Decimal("100000.00"), max_iters=1)
        self.addCleanup(backtest.portfolio.backtest_file.close)
        self.assertIsInstance(backtest.events, queue.Queue)
        self.assertIs(backtest.strategy.events, backtest.events)
        self.assertIs(backtest.ticker.events_queue, backtest.events)


if __name__ == "__main__":
    unittest.main()
