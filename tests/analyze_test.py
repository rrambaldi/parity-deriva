"""
Tests for parity_deriva.performance.analyze.

Analyzer pulls closed trades from the account and reports win/loss statistics
plus three flavours of optimal f. It is the natural place to hang a
real-vs-simulated comparison off, so what it computes - and where it refuses
to compute - is worth pinning.
"""

import datetime
import logging
import unittest
from unittest import mock

from parity_deriva.performance import analyze as analyze_mod
from parity_deriva.performance.analyze import Analyzer
from parity_deriva.tests.helpers import (FakeRequests, FakeResponse, Recorder,
                                   TempDirCase, oanda_time)


DAY = datetime.date(2017, 2, 6)


def trade(minute, pl, financing="-0.04", day=DAY):
    opened = datetime.datetime.combine(day, datetime.time(10, minute, 0))
    closed = opened + datetime.timedelta(minutes=1)
    return {"openTime": oanda_time(opened), "closeTime": oanda_time(closed),
            "financing": financing, "realizedPL": "%.4f" % pl,
            "instrument": "DE30_EUR", "state": "CLOSED",
            "closingTransactionIDs": ["1"]}


class AnalyzerCase(TempDirCase):

    def build(self, trades, **kw):
        self.fake = FakeRequests(FakeResponse({"trades": trades}))
        patcher = mock.patch.object(analyze_mod, 'requests', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        kw.setdefault('setup', self.settings)
        kw.setdefault('pairs', ["DE30_EUR"])
        kw.setdefault('day', DAY)
        return Analyzer(**kw)

    def report(self, trades, **kw):
        analyzer = self.build(trades, **kw)
        with self.assertLogs('parity_deriva.trading.trading', level='INFO') as log:
            analyzer.analyze("DE30_EUR")
        return "\n".join(log.output)

    def field(self, report, label):
        for line in report.split("\n"):
            if label in line:
                return line
        raise AssertionError("%r not in report" % label)


class TestAnalyzerSetUp(AnalyzerCase):

    def test_it_queries_closed_trades_for_the_instrument(self):
        analyzer = self.build([])
        url = analyzer.url["DE30_EUR"]
        self.assertIn("/v3/accounts/001-TEST-000/trades", url)
        self.assertIn("state=CLOSED", url)
        self.assertIn("instrument=DE30_EUR", url)

    def test_the_bearer_token_is_sent(self):
        analyzer = self.build([])
        analyzer.request("DE30_EUR")
        self.assertEqual(self.fake.last['headers'],
                         {'Authorization': 'Bearer TESTTOKEN'})

    def test_a_non_200_reply_yields_none(self):
        analyzer = self.build([])
        self.fake.responses = [FakeResponse(None, status=401)]
        self.assertIsNone(analyzer.request("DE30_EUR"))

    def test_a_transport_error_yields_none(self):
        analyzer = self.build([])
        self.fake.raise_on_send = True
        self.assertIsNone(analyzer.request("DE30_EUR"))

    def test_it_is_an_execution_handler_that_consumes_nothing(self):
        """
        Analyzer plugs into the Engine but its execute_event is a stub, so it
        only ever works from the REST snapshot, never from the live stream.
        """
        analyzer = self.build([])
        self.assertIsNone(analyzer.execute_event(object()))

    def test_a_failed_request_makes_analyze_a_no_op(self):
        analyzer = self.build([])
        self.fake.responses = [FakeResponse(None, status=500)]
        self.assertIsNone(analyzer.analyze("DE30_EUR"))


class TestAggregation(AnalyzerCase):

    def trades(self):
        # three winners, two losers, deliberately interleaved
        return [trade(1, 450.0), trade(2, 120.0), trade(3, -80.0),
                trade(4, -60.0), trade(5, 200.0)]

    def test_the_net_result_is_gross_win_minus_gross_loss(self):
        report = self.report(self.trades())
        self.assertIn("RESULT PL: 630.00", self.field(report, "RESULT PL"))

    def test_the_winning_side_is_summarised(self):
        line = self.field(self.report(self.trades()), "WN TOT")
        self.assertIn("TOT: 770.00", line)
        self.assertIn("NUM:   3.00", line)
        self.assertIn("MAX: 450.00", line)

    def test_the_losing_side_uses_absolute_values(self):
        line = self.field(self.report(self.trades()), "LS TOT")
        self.assertIn("TOT: 140.00", line)
        self.assertIn("NUM:   2.00", line)
        self.assertIn("MAX:  80.00", line)

    def test_the_win_loss_ratio_is_the_gross_win_share(self):
        line = self.field(self.report(self.trades()), "WIN/LOSS RATIO")
        self.assertIn("0.85", line)          # 770 / 910

    def test_optimal_f_is_derived_from_that_share(self):
        line = self.field(self.report(self.trades()), "OPTIMAL F:")
        self.assertIn("0.69", line)          # 2 * 0.846 - 1

    def test_the_financing_costs_are_totalled(self):
        line = self.field(self.report(self.trades()), "TOTAL COSTS")
        self.assertIn("-0.20", line)         # 5 x -0.04

    def test_every_trade_is_listed_with_its_duration(self):
        report = self.report(self.trades())
        rows = [l for l in report.split("\n") if "0:01:00" in l]
        self.assertEqual(len(rows), 5)
        self.assertIn("PL: 450.00", rows[0])

    def test_all_three_optimal_f_variants_are_reported(self):
        report = self.report(self.trades())
        for label in ("OPTIMAL F:", "OPTIMAL F-AVG:", "OPTIMAL F-MAX:"):
            self.field(report, label)


class TestConsecutiveRuns(AnalyzerCase):

    def test_consecutive_winners_are_counted_and_summed(self):
        """
        Was: the counter only incremented when the previous trade was already
             on the same side, so a run of three winners was reported as 2 -
             the streak was always one short.
        Now: the streak length is the number of trades in the run, and the
             amount accumulated over it starts with the trade that opens it.
        """
        report = self.report([trade(1, 100.0), trade(2, 100.0),
                              trade(3, 100.0), trade(4, -50.0)])
        line = self.field(report, "WN MAX.CONS")
        self.assertIn("MAX.CONS: 3", line)
        self.assertIn("MAX.AMOUNT: 300.00", line)

    def test_a_single_winner_is_a_run_of_one(self):
        report = self.report([trade(1, 10.0), trade(2, -5.0)])
        self.assertIn("MAX.CONS: 1", self.field(report, "WN MAX.CONS"))

    def test_two_winners_are_a_run_of_two(self):
        report = self.report([trade(1, 10.0), trade(2, 10.0), trade(3, -5.0)])
        self.assertIn("MAX.CONS: 2", self.field(report, "WN MAX.CONS"))

    def test_consecutive_losers_are_counted_separately(self):
        report = self.report([trade(1, 10.0), trade(2, -10.0),
                              trade(3, -10.0), trade(4, -10.0)])
        self.assertIn("MAX.CONS: 3", self.field(report, "LS MAX.CONS"))

    def test_a_winner_resets_the_losing_run(self):
        report = self.report([trade(1, -10.0), trade(2, -10.0),
                              trade(3, 10.0), trade(4, -10.0)])
        self.assertIn("MAX.CONS: 2", self.field(report, "LS MAX.CONS"))


class TestDayFilter(AnalyzerCase):

    def test_only_trades_opened_on_the_requested_day_are_counted(self):
        other = datetime.date(2017, 2, 7)
        report = self.report([trade(1, 100.0), trade(2, -50.0),
                              trade(3, 999.0, day=other)])
        self.assertIn("RESULT PL:  50.00", self.field(report, "RESULT PL"))

    def test_without_a_day_every_trade_is_counted(self):
        other = datetime.date(2017, 2, 7)
        report = self.report([trade(1, 100.0), trade(2, -50.0),
                              trade(3, 100.0, day=other)], day=None)
        self.assertIn("RESULT PL: 150.00", self.field(report, "RESULT PL"))

    def test_the_filter_looks_at_the_opening_time(self):
        analyzer = self.build([])
        self.assertEqual(analyzer.day, DAY)


class TestDegenerateInputs(AnalyzerCase):
    """
    A one-sided day - all winners, all losers, or no trades at all - is exactly
    what a monitoring job meets on a quiet session, or on a day the safety net
    stopped trading.

    Was: the averages and the win share divided by the trade counts with no
         guard, so precisely those days raised ZeroDivisionError instead of
         producing the report they most needed.
    Now: the ratios are guarded, optimal f reports "n/a (one-sided day)" where
         it is undefined, and a day with no closed trades says so.
    """

    def test_a_day_with_no_losers_reports(self):
        report = self.report([trade(1, 100.0), trade(2, 50.0)])
        self.assertIn("RESULT PL: 150.00", self.field(report, "RESULT PL"))
        self.assertIn("NUM:   0.00", self.field(report, "LS TOT"))
        self.assertIn("n/a (one-sided day)", self.field(report, "OPTIMAL F-AVG"))
        self.assertIn("WIN/LOSS RATIO:   1.00", self.field(report, "WIN/LOSS"))

    def test_a_day_with_no_winners_reports(self):
        report = self.report([trade(1, -100.0), trade(2, -50.0)])
        self.assertIn("RESULT PL: -150.00", self.field(report, "RESULT PL"))
        self.assertIn("WIN/LOSS RATIO:   0.00", self.field(report, "WIN/LOSS"))
        self.assertIn("n/a (one-sided day)", self.field(report, "OPTIMAL F-MAX"))

    def test_a_day_with_no_matching_trades_says_so(self):
        report = self.report([trade(1, 100.0, day=datetime.date(2017, 2, 7))])
        self.assertIn("NO CLOSED TRADES", report)
        self.assertIn("2017-02-06", report)

    def test_an_empty_trade_list_says_so(self):
        self.assertIn("NO CLOSED TRADES", self.report([]))

    def test_no_report_lines_are_emitted_for_an_empty_day(self):
        report = self.report([])
        self.assertNotIn("OPTIMAL F", report)
        self.assertNotIn("RESULT PL", report)

    def test_a_break_even_trade_is_counted_in_neither_column(self):
        """pl > 0 and pl < 0 both miss zero, so a scratch trade only shows
        up in the trade count and in the financing total."""
        analyzer = self.build([trade(1, 100.0), trade(2, -50.0), trade(3, 0.0)])
        with self.assertLogs('parity_deriva.trading.trading', level='INFO') as log:
            analyzer.analyze("DE30_EUR")
        report = "\n".join(log.output)
        self.assertIn("NUM:   1.00", self.field(report, "WN TOT"))
        self.assertIn("NUM:   1.00", self.field(report, "LS TOT"))
        self.assertIn("-0.12", self.field(report, "TOTAL COSTS"))


if __name__ == "__main__":
    unittest.main()
