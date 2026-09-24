"""
Tests for the backtest viewer: backtest/ledger.py, performance/report.py and
web/service.py.

Three layers, and they are tested as three:

* **the ledger** turns a stream of events into a list of trades. What is
  pinned here is the join - which fill belongs to which signal, and which of
  the simulator's fills is an entry rather than the stop doing its job -
  because that join is the one thing no single event carries and the one
  thing a wrong answer to would produce a plausible table of nonsense.
* **the report** is arithmetic, so it is tested as arithmetic, including the
  one-sided cases. A run of nothing but winners is exactly the run somebody
  wants a report for, and a ratio printed as 0.00 there reads as the worst
  possible result rather than the best.
* **the service** is mostly refusals: an instrument that is not a store, a
  granularity the store does not hold, a window too wide to draw, a path that
  tries to leave the static directory. The instrument name reaches a file
  path, so that one is not a style question.

No network beyond a loopback server the HTTP tests start themselves, and no
HDF5 warehouse: the store these tests read is written into a temporary
directory in setUp.
"""

import datetime
import json
import os
import shutil
import tempfile
import threading
import types
from decimal import Decimal
import unittest
from unittest import mock
import urllib.error
import urllib.parse
import urllib.request

from parity_deriva.backtest import ledger as ledger_module
from parity_deriva.backtest.ledger import Ledger, LedgerError, moneyManager, run
from parity_deriva.backtest.oanda import OANDABacktester
from parity_deriva.backtest.offline import SimulatedBroker
from parity_deriva.backtest.driver import Cancelled, ReplayEngine
from parity_deriva.event.event import (CandleEvent, ClientOrderEvent,
                                       OrderCancelEvent, OrderEvent,
                                       SignalEvent, StopModifyEvent,
                                       TransactionEvent)
from parity_deriva.performance import report as report_module
from parity_deriva.trading.handler import StreamHandler
from parity_deriva.strategy import plugins as plugins_module
from parity_deriva.data import store as store_module
from parity_deriva.web import service as service_module
from parity_deriva.web.service import (Service, ServiceError, millis,
                                        parseAmount, parsePercent,
                                        parsePips)
from parity_deriva.tests.helpers import T0, candle_dict

MINUTE = datetime.timedelta(minutes=1)


class Feed(StreamHandler):
    """A source that replays events already in hand."""

    def __init__(self, events):
        import logging
        self.logger = logging.getLogger('parity_deriva.trading.trading')
        self.events = events

    def stream_to_queue(self):
        for event in self.events:
            self.queue_event(event)


def candle(dt=T0, o=11700.0, h=11706.0, l=11694.0, c=11703.0,
           instrument='DE30_EUR', granularity='M1'):
    event = CandleEvent(candle_dict(dt, o=o, h=h, l=l, c=c))
    event.instrument, event.granularity = instrument, granularity
    return event


class LedgerRecordingTest(unittest.TestCase):
    """The join, fed by hand so that each event's part in it is visible."""

    def setUp(self):
        self.ledger = Ledger(instrument='DE30_EUR', granularity='M1')

    def signal(self, key='K1', when=T0):
        event = SignalEvent({'instrument': 'DE30_EUR', 'time': when})
        event.signalNumber = key
        self.ledger.execute_event(event)

    def order(self, key='K1', units=1, price=11700.0, sl=11690.0, tp=11720.0):
        event = OrderEvent({'instrument': 'DE30_EUR', 'units': units,
                            'orderType': 'STOP', 'price': price,
                            'stopLoss': sl, 'takeProfit': tp,
                            'signalNumber': key})
        self.ledger.execute_event(event)

    def ack(self, key='K1', order_id=1, price=11700.0):
        event = ClientOrderEvent({'id': order_id, 'batchID': order_id,
                                  'price': price, 'instrument': 'DE30_EUR'})
        event.signalNumber = key
        self.ledger.execute_event(event)

    def fill(self, order_id=1, price=11700.0, when=T0 + MINUTE):
        self.ledger.execute_event(TransactionEvent({
            'type': 'ORDER_FILL', 'orderID': order_id, 'price': price,
            'time': when, 'reason': 'ORDER_FILL', 'instrument': 'DE30_EUR'}))

    def close(self, order_id=1, price=11720.0, when=T0 + 2 * MINUTE,
              reason='TAKE_PROFIT_ORDER', pl=20.0):
        self.ledger.execute_event(TransactionEvent({
            'type': 'ORDER_FILL', 'orderID': order_id, 'price': price,
            'time': when, 'reason': reason, 'pl': pl,
            'accountBalance': 100020.0, 'instrument': 'DE30_EUR',
            'tradesClosed': [{'tradeID': order_id, 'realizedPL': pl}]}))

    def move(self, order_id=1, price=11695.0, when=T0 + MINUTE):
        """The trailer walking the stop of an open trade."""
        self.ledger.execute_event(StopModifyEvent({
            'orderID': order_id, 'tradeID': order_id, 'signalNumber': 'K1',
            'instrument': 'DE30_EUR', 'price': price, 'time': when}))

    def opened(self):
        self.signal()
        self.order()
        self.ack()
        self.fill()

    # ------------------------------------------------------------ the basics

    def test_a_closed_trade_carries_its_whole_life(self):
        self.opened()
        self.close()
        trade, = self.ledger.trades()
        self.assertEqual(trade['key'], 'K1')
        self.assertEqual(trade['direction'], 'long')
        self.assertEqual(trade['signalTime'], T0)
        self.assertEqual(trade['entryTime'], T0 + MINUTE)
        self.assertEqual(trade['entryPrice'], 11700.0)
        self.assertEqual(trade['exitTime'], T0 + 2 * MINUTE)
        self.assertEqual(trade['exitPrice'], 11720.0)
        self.assertEqual(trade['outcome'], 'TAKE_PROFIT_ORDER')
        self.assertEqual(trade['pl'], 20.0)

    def test_the_levels_come_from_the_order_not_the_fill(self):
        """
        The stop and the target are the order as the strategy meant it, and
        that is the only place the three levels appear together. Reading them
        off anything later would report what survived rather than what was
        asked for.
        """
        self.opened()
        self.close()
        trade, = self.ledger.trades()
        self.assertEqual(trade['orderPrice'], 11700.0)
        self.assertEqual(trade['stopLoss'], 11690.0)
        self.assertEqual(trade['takeProfit'], 11720.0)

    def test_a_short_is_labelled_short(self):
        self.signal()
        self.order(units=-1, price=11690.0)
        self.ack(price=11690.0)
        self.fill(price=11690.0)
        self.close(price=11680.0, reason='STOP_LOSS_ORDER', pl=-10.0)
        trade, = self.ledger.trades()
        self.assertEqual(trade['direction'], 'short')
        self.assertEqual(trade['outcome'], 'STOP_LOSS_ORDER')

    def test_a_trade_still_open_is_listed_with_no_result(self):
        """
        The data running out is not an outcome the broker reported, so it is
        named differently from the two that are, and its P&L is absent rather
        than zero - a trade that has not closed has not made nothing.
        """
        self.opened()
        trade, = self.ledger.trades()
        self.assertEqual(trade['outcome'], ledger_module.STILL_OPEN)
        self.assertIsNone(trade['pl'])
        self.assertIsNone(trade['exitTime'])

    # ------------------------------------------------------- what is not one

    def test_a_signal_that_never_entered_is_counted_and_not_listed(self):
        """
        Both halves matter. It is not a trade, so it is not in the table; and
        "the strategy signalled twice and entered once" is the first thing
        worth knowing about a run.
        """
        self.signal(key='K1')
        self.order(key='K1')
        self.ack(key='K1')
        self.signal(key='K2', when=T0 + MINUTE)
        self.order(key='K2', price=11800.0)
        self.ack(key='K2', order_id=2, price=11800.0)
        self.fill(order_id=1)

        self.assertEqual([t['key'] for t in self.ledger.trades()], ['K1'])
        counts = self.ledger.counts()
        self.assertEqual(counts['signals'], 2)
        self.assertEqual(counts['entered'], 1)
        self.assertEqual(counts['neverEntered'], 1)

    def test_a_cancelled_leg_is_not_a_trade(self):
        self.signal()
        self.order(units=1, price=11700.0)
        self.order(units=-1, price=11690.0)
        self.ack(order_id=1, price=11700.0)
        self.ack(order_id=2, price=11690.0)
        self.ledger.execute_event(OrderCancelEvent({'orderID': 2,
                                                    'price': 11690.0,
                                                    'instrument': 'DE30_EUR'}))
        self.fill(order_id=1)
        self.assertEqual(len(self.ledger.trades()), 1)

    def test_a_childs_acknowledgement_does_not_steal_a_legs_id(self):
        """
        Was: this is the trap AG01 walks into. Its long leg's stop sits at
             exactly the price of its short leg's entry, so the simulator's
             acknowledgement of that stop child matches the short leg on
             price - and a ledger matching the same way would file the child's
             id against a leg that never filled, then report the trade against
             the wrong one.
        Now: a leg that already has an id is not a candidate, so an
             acknowledgement that finds none is recognised as a child and
             ignored.
        """
        self.signal()
        self.order(units=1, price=11700.0, sl=11690.0, tp=11720.0)
        self.order(units=-1, price=11690.0, sl=11700.0, tp=11680.0)
        self.ack(order_id=1, price=11700.0)
        self.ack(order_id=2, price=11690.0)
        # the long fills, and the simulator acknowledges its stop child, which
        # sits at 11690 - the short leg's entry
        self.fill(order_id=1)
        self.ack(order_id=3, price=11690.0)

        legs = self.ledger.groups['K1']['legs']
        self.assertEqual([leg['orderID'] for leg in legs], [1, 2])
        self.assertNotIn(3, [leg['orderID'] for leg in legs])

    def test_a_fill_of_an_unknown_order_is_not_a_trade(self):
        """
        The simulator reports every order it fills, the stop and the target
        included. Those are the trade ending, not a new one beginning, and
        the same event arrives a moment later as the close.
        """
        self.opened()
        self.fill(order_id=99, price=11720.0)
        self.assertEqual(len(self.ledger.trades()), 1)

    def test_a_rejection_leaves_the_leg_dead(self):
        self.signal()
        self.order()
        self.ledger.execute_event(TransactionEvent({
            'type': 'ORDER_REJECT', 'instrument': 'DE30_EUR',
            'price': 11700.0, 'signalNumber': 'K1',
            'rejectReason': 'MARKET_HALTED'}))
        self.assertEqual(self.ledger.trades(), [])
        self.assertEqual(self.ledger.groups['K1']['legs'][0]['status'],
                         'REJECTED')

    # ------------------------------------------------------ the walking stop

    def test_a_stop_that_never_moved_has_no_final_level(self):
        """
        None, not the level it was ordered with. "It never moved" and "nobody
        wrote down where it went" are different facts, and only one of them
        should make the page draw a second line.
        """
        self.opened()
        self.close()
        trade, = self.ledger.trades()
        self.assertIsNone(trade['stopFinal'])

    def test_the_stop_is_followed_as_it_walks(self):
        self.opened()
        self.move(price=11695.0)
        self.move(price=11702.0)
        self.close(reason='STOP_LOSS_ORDER', price=11702.0, pl=2.0)
        trade, = self.ledger.trades()
        self.assertEqual(trade['stopLoss'], 11690.0, "as ordered, untouched")
        self.assertEqual(trade['stopFinal'], 11702.0, "where it ended up")
        self.assertEqual(trade['exitPrice'], 11702.0)

    def test_a_winning_trade_can_still_exit_on_its_stop(self):
        """
        The case that reads as a bug in the table and is not one: a strategy
        whose only exit is a stop that climbs closes every trade as
        STOP_LOSS_ORDER, including the ones it made money on.
        """
        self.opened()
        self.move(price=11710.0)
        self.close(reason='STOP_LOSS_ORDER', price=11710.0, pl=10.0)
        trade, = self.ledger.trades()
        self.assertEqual(trade['outcome'], 'STOP_LOSS_ORDER')
        self.assertGreater(trade['pl'], 0)
        self.assertGreater(trade['stopFinal'], trade['stopLoss'])

    def test_a_stop_moved_before_the_fill_is_not_recorded(self):
        """A pending order has no trade to walk a stop for."""
        self.signal()
        self.order()
        self.ack()
        self.move(price=11695.0)
        self.fill()
        self.close()
        trade, = self.ledger.trades()
        self.assertIsNone(trade['stopFinal'])

    def test_a_stop_moved_after_the_close_does_not_rewrite_it(self):
        """
        The exit has happened; what the trade exited on is settled. A late
        event overwriting it would make the table disagree with the fill.
        """
        self.opened()
        self.move(price=11695.0)
        self.close(reason='STOP_LOSS_ORDER', price=11695.0, pl=-5.0)
        self.move(price=11800.0)
        trade, = self.ledger.trades()
        self.assertEqual(trade['stopFinal'], 11695.0)

    def test_a_move_for_an_order_nobody_knows_is_dropped(self):
        self.opened()
        self.move(order_id=99, price=11695.0)
        self.close()
        trade, = self.ledger.trades()
        self.assertIsNone(trade['stopFinal'])

    def test_candles_of_another_stream_are_not_collected(self):
        """
        Two granularities of one instrument can be on the bus at once. A chart
        drawn from both would interleave bars of two periods.
        """
        self.ledger.execute_event(candle(granularity='M1'))
        self.ledger.execute_event(candle(T0 + MINUTE, granularity='H1'))
        self.assertEqual(len(self.ledger.candles), 1)


class LedgerThroughTheStackTest(unittest.TestCase):
    """
    The same thing again, with the real money manager and the real simulator
    in between, so that the recording is pinned against what those two
    actually publish rather than against this test's idea of it.
    """

    def build(self):
        ledger = Ledger(instrument='DE30_EUR', granularity='M1')
        engine = ReplayEngine()
        for handler in (moneyManager(units=1), OANDABacktester(granularity='M1'),
                        SimulatedBroker(), ledger):
            engine.add_handler(handler)
        return engine, ledger

    def straddle(self, key='K1'):
        out = []
        for units, price, sl, tp in ((1, 11705.0, 11695.0, 11715.0),
                                     (-1, 11695.0, 11705.0, 11685.0)):
            event = SignalEvent({'instrument': 'DE30_EUR', 'units': units,
                                 'orderType': 'STOP', 'price': price,
                                 'stopLoss': sl, 'takeProfit': tp,
                                 'time': T0, 'gtdTime': None})
            event.signalNumber = key
            out.append(event)
        return out

    def test_a_straddle_that_reaches_its_target_is_one_winning_trade(self):
        engine, ledger = self.build()
        engine.run(Feed(self.straddle() + [
            candle(T0 + MINUTE, l=11704.0, h=11706.0),
            candle(T0 + 2 * MINUTE, l=11714.0, h=11716.0)]))

        trade, = ledger.trades()
        self.assertEqual(trade['direction'], 'long')
        self.assertEqual(trade['entryPrice'], 11705.0)
        self.assertEqual(trade['exitPrice'], 11715.0)
        self.assertEqual(trade['outcome'], 'TAKE_PROFIT_ORDER')
        self.assertGreater(trade['pl'], 0)
        self.assertEqual(ledger.counts()['entered'], 1)

    def test_the_losing_leg_does_not_become_a_second_trade(self):
        engine, ledger = self.build()
        engine.run(Feed(self.straddle() + [
            candle(T0 + MINUTE, l=11704.0, h=11706.0),
            candle(T0 + 2 * MINUTE, l=11694.0, h=11716.0)]))
        self.assertEqual(len(ledger.trades()), 1)

    def test_a_trade_is_not_closed_twice(self):
        """
        Was: the loser of a bracket stayed on the simulator's book, so a later
             bar reaching it closed the same trade again - with the opposite
             outcome. A ledger built on that reported a target and then a stop
             for one entry, and the report's net was the sum of both.
        Now: one close. backtest_oanda_test.py pins the simulator's side of
             this; here it is pinned where it was noticed.
        """
        engine, ledger = self.build()
        engine.run(Feed(self.straddle() + [
            candle(T0 + MINUTE, l=11704.0, h=11706.0),
            candle(T0 + 2 * MINUTE, l=11714.0, h=11716.0),
            candle(T0 + 3 * MINUTE, l=11694.0, h=11696.0)]))
        trade, = ledger.trades()
        self.assertEqual(trade['outcome'], 'TAKE_PROFIT_ORDER')
        self.assertEqual(ledger.counts()['closed'], 1)


class MoneyManagerStateTest(unittest.TestCase):

    def test_two_runs_in_one_process_are_independent(self):
        """
        Was: signals, processed, onTrade and orderIssued are class attributes,
             so a second run started believing the first run's trade was still
             open and refused every signal. Live it never showed - a process
             runs one stack and exits - but a service runs one per request,
             and the second backtest would come back empty as though the
             strategy had stopped working.
        Now: moneyManager() shadows them per instance.
        """
        first = moneyManager(units=1)
        first.handleSignal(self._signal())
        first.onTrade = True

        second = moneyManager(units=1)
        self.assertEqual(second.signals, {})
        self.assertEqual(second.processed, [])
        self.assertFalse(second.onTrade)
        self.assertFalse(second.orderIssued)

    def _signal(self):
        event = SignalEvent({'instrument': 'DE30_EUR', 'units': 1,
                             'orderType': 'STOP', 'price': 11700.0,
                             'stopLoss': 11690.0, 'takeProfit': 11720.0})
        event.signalNumber = 'K1'
        return event


class LoadStrategyTest(unittest.TestCase):

    def test_it_loads_the_ones_that_trade(self):
        from parity_deriva.strategy.AG01 import AG01
        self.assertIs(ledger_module.load_strategy('AG01'), AG01)

    def test_a_research_engine_is_refused_with_the_reason(self):
        """
        BO01 places no orders, so a backtest of it has nothing to write down.
        Returning an empty ledger would look like a strategy that never
        triggered.
        """
        with self.assertRaises(LedgerError) as caught:
            ledger_module.load_strategy('BO01')
        self.assertIn('place no orders', str(caught.exception))

    def test_an_unknown_name_is_refused(self):
        with self.assertRaises(LedgerError):
            ledger_module.load_strategy('nope')


class ReportTest(unittest.TestCase):

    def trades(self, *pls):
        out = []
        for i, pl in enumerate(pls):
            out.append({'pl': pl, 'outcome': 'TAKE_PROFIT_ORDER' if pl > 0
                        else 'STOP_LOSS_ORDER'})
        return out

    def test_nothing_at_all(self):
        r = report_module.report([])
        self.assertEqual(r['closedTrades'], 0)
        self.assertIsNone(r['winRate'])
        self.assertIsNone(r['profitFactor'])
        self.assertEqual(r['net'], 0)

    def test_the_ordinary_case(self):
        r = report_module.report(self.trades(2.0, -1.0, 3.0, -4.0))
        self.assertEqual(r['closedTrades'], 4)
        self.assertEqual(r['wins'], 2)
        self.assertEqual(r['losses'], 2)
        self.assertEqual(r['grossProfit'], 5.0)
        self.assertEqual(r['grossLoss'], 5.0)
        self.assertEqual(r['net'], 0.0)
        self.assertEqual(r['profitFactor'], 1.0)
        self.assertEqual(r['averageWin'], 2.5)
        self.assertEqual(r['averageLoss'], 2.5)
        self.assertEqual(r['largestWin'], 3.0)
        self.assertEqual(r['largestLoss'], -4.0)

    def test_a_one_sided_run_says_n_a_rather_than_zero(self):
        """
        A run of nothing but winners is exactly the run somebody wants a
        report for, and a profit factor printed as 0.00 there would read as
        the worst possible result rather than the best.
        """
        r = report_module.report(self.trades(1.0, 2.0))
        self.assertEqual(r['winRate'], 1.0)
        self.assertIsNone(r['profitFactor'])
        self.assertIsNone(r['averageLoss'])
        self.assertIsNone(r['largestLoss'])

    def test_open_trades_are_not_counted_among_the_closed(self):
        trades = self.trades(2.0, -1.0)
        trades.append({'pl': None, 'outcome': 'STILL_OPEN'})
        r = report_module.report(trades)
        self.assertEqual(r['trades'], 3)
        self.assertEqual(r['closedTrades'], 2)
        self.assertEqual(r['openTrades'], 1)
        self.assertEqual(r['outcomes']['STILL_OPEN'], 1)

    def test_runs_of_wins_and_losses(self):
        self.assertEqual(report_module.runs([1, 1, 1, -1, -1, 1]), (3, 2))
        self.assertEqual(report_module.runs([]), (0, 0))
        self.assertEqual(report_module.runs([0, 0]), (0, 0))

    def test_a_lone_winner_is_a_run_of_one(self):
        self.assertEqual(report_module.runs([1]), (1, 0))

    def test_drawdown_is_measured_from_the_peak(self):
        curve = report_module.equity([5.0, -2.0, -1.0, 4.0])
        self.assertEqual(curve, [5.0, 3.0, 2.0, 6.0])
        worst, at = report_module.drawdown(curve)
        self.assertEqual(worst, 3.0)
        self.assertEqual(at, 2)

    def test_drawdown_of_a_run_that_only_rises(self):
        worst, at = report_module.drawdown(report_module.equity([1.0, 1.0]))
        self.assertEqual(worst, 0.0)
        self.assertIsNone(at)

    def test_the_unit_is_stated_in_the_payload(self):
        """
        P&L here is price times units, not money. A consumer that labelled it
        with a currency would be wrong by whatever the contract size is.
        """
        self.assertEqual(report_module.report([])['unit'], 'price x units')


class StoreCase(unittest.TestCase):
    """A temporary warehouse with one small instrument in it."""

    @classmethod
    def store(cls, path, bars=200, granularity='H1', start=T0):
        import pandas as pd
        rows = []
        index = []
        price = 1.2000
        for i in range(bars):
            # a shape that turns over often enough for AG01 to signal
            price += 0.0008 if (i // 3) % 2 == 0 else -0.0008
            o, c = price, price + (0.0004 if i % 2 else -0.0004)
            h, l = max(o, c) + 0.0006, min(o, c) - 0.0006
            row = {'volume': 10}
            for side, shift in (('ask', 0.00005), ('bid', -0.00005),
                                ('mid', 0.0)):
                row['%s_o' % side] = o + shift
                row['%s_h' % side] = h + shift
                row['%s_l' % side] = l + shift
                row['%s_c' % side] = c + shift
            rows.append(row)
            index.append(start + datetime.timedelta(hours=i))
        frame = pd.DataFrame(rows, index=pd.DatetimeIndex(index))
        store = pd.HDFStore(path, mode='a')
        try:
            store.put(granularity, frame, format='table')
        finally:
            store.close()

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='parity-deriva-web-')
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.store(os.path.join(self.tmpdir, 'EUR_USD.hd5'))
        # EQUITY because a backtest now opens the simulated account with it,
        # and the page reads it to fill its capital field
        self.settings = types.SimpleNamespace(DATA_DIR=self.tmpdir,
                                              EQUITY=Decimal("100000.00"))
        self.service = Service(setup=self.settings, max_candles=1000)


class ServiceRefusalTest(StoreCase):

    def test_it_lists_what_the_directory_holds(self):
        rows = self.service.instruments()
        self.assertEqual([r['instrument'] for r in rows], ['EUR_USD'])
        # H1 is what the store holds; H4 and D are built from it on request
        self.assertEqual([g['granularity'] for g in rows[0]['granularities']],
                         ['D', 'H1', 'H4'])
        held = dict((g['granularity'], g) for g in rows[0]['granularities'])
        self.assertEqual(held['H1']['bars'], 200)
        self.assertNotIn('derivedFrom', held['H1'])
        self.assertEqual(held['D']['derivedFrom'], 'H1')

    def test_an_instrument_that_is_not_a_store_is_refused(self):
        """
        The name reaches a file path, so it is checked against the files that
        exist rather than sanitised: a rule about what a name may contain is a
        rule somebody has to get exactly right.
        """
        with self.assertRaises(ServiceError) as caught:
            self.service.backtest('../../etc/passwd', 'H1')
        self.assertIn('no store', str(caught.exception))
        self.assertIn('EUR_USD', str(caught.exception))

    def test_a_granularity_the_store_lacks_is_refused_by_name(self):
        with self.assertRaises(ServiceError) as caught:
            self.service.backtest('EUR_USD', 'M5')
        self.assertIn('H1', str(caught.exception))

    def test_an_unknown_strategy_is_refused(self):
        with self.assertRaises(ServiceError):
            self.service.backtest('EUR_USD', 'H1', strategy='BO01')

    def test_a_window_too_wide_to_draw_is_refused_with_the_count(self):
        """
        Drawing the first few thousand of a hundred thousand bars would show a
        range nobody asked for, so the count and the limit are both in the
        message.
        """
        service = Service(setup=self.settings, max_candles=10)
        with self.assertRaises(ServiceError) as caught:
            service.backtest('EUR_USD', 'H1')
        self.assertIn('200', str(caught.exception))
        self.assertIn('10', str(caught.exception))

    def test_a_confirmed_run_is_not_refused_for_its_size(self):
        """
        Was: the page warned, somebody said run it anyway, and the service
             refused it for the candle ceiling a moment later.
        Now: the estimate carries the ceiling, the page asks about both, and
             a confirmed run is drawn however wide it is.
        """
        service = Service(setup=self.settings, max_candles=10)
        self.assertEqual(service.estimate('EUR_USD', 'H1')['limit'], 10)
        payload = service.backtest('EUR_USD', 'H1', confirmed=True)
        self.assertEqual(len(payload['candles']), 200)
        # and a reload of it redraws it rather than refusing it
        self.assertIs(service.backtest('EUR_USD', 'H1', cachedOnly=True), payload)

    def test_an_empty_window_is_refused(self):
        with self.assertRaises(ServiceError) as caught:
            self.service.backtest('EUR_USD', 'H1',
                                  dtfrom=datetime.datetime(1999, 1, 1),
                                  dtto=datetime.datetime(1999, 2, 1))
        self.assertIn('no candles', str(caught.exception))

    def test_a_backwards_window_is_refused(self):
        with self.assertRaises(ServiceError):
            self.service.backtest('EUR_USD', 'H1',
                                  dtfrom=datetime.datetime(2018, 2, 1),
                                  dtto=datetime.datetime(2018, 1, 1))


class ServicePayloadTest(StoreCase):

    def setUp(self):
        super(ServicePayloadTest, self).setUp()
        self.payload = self.service.backtest('EUR_USD', 'H1')

    def test_a_store_with_nothing_finer_fills_on_its_own_bars(self):
        """'fine' is None, and None is an answer rather than a gap."""
        self.assertIsNone(self.payload['fine'])

    def test_the_ceiling_on_a_stop_is_in_the_payload(self):
        """None when there is none, and the page says which it was."""
        self.assertIsNone(self.payload['maxStopPips'])
        capped = self.service.backtest('EUR_USD', 'H1', maxStopPips=30)
        self.assertEqual(capped['maxStopPips'], 30)

    def test_no_trade_carries_a_stop_wider_than_the_ceiling(self):
        """
        The invariant, and the only one there is. It is NOT that the capped
        run is the uncapped one with trades removed: this stack takes one
        position at a time, so refusing a wide signal leaves the slot free
        for the next one, and a ceiling can end up with *more* trades than no
        ceiling. On EUR_USD H1 over January 2018 the uncapped run enters 28
        and a 20 pip ceiling enters 41.
        """
        # the fixture's stops are 21 pips apart but for one of 29, so 25 is
        # a ceiling that refuses something and lets something through
        pip = 0.0001
        capped = self.service.backtest('EUR_USD', 'H1', maxStopPips=25)
        self.assertTrue(capped['trades'], 'nothing to check')
        for trade in capped['trades']:
            self.assertLessEqual(
                abs(trade['orderPrice'] - trade['stopLoss']), 25 * pip + 1e-12,
                "a trade got through with a stop wider than the ceiling")

    def test_the_uncapped_run_has_trades_the_ceiling_would_refuse(self):
        """Otherwise the test above is checking an empty rule."""
        loose = self.service.backtest('EUR_USD', 'H1')
        self.assertTrue(any(abs(t['orderPrice'] - t['stopLoss']) > 25 * 0.0001
                            for t in loose['trades']))

    def test_a_run_filled_on_finer_bars_says_which(self):
        """
        The page prints it next to the granularity: a run that resolved its
        exits a minute at a time is not the same measurement as one that
        guessed between a stop and a target inside one daily bar.
        """
        run = types.SimpleNamespace(
            instrument='EUR_USD', granularity='D', strategy='AG01',
            dtfrom=T0, dtto=T0, candles=[], trades=[], counts={},
            balance=None, risk=None, fine='M1', maxStopPips=None)
        self.assertEqual(self.service.payload(run, 0.0)['fine'], 'M1')

    def test_a_fill_between_two_bars_is_placed_on_the_bar_it_fell_in(self):
        """
        Was: a trade's bar was looked up by exact time, and a D run filled on
             M5 fills at 14:35 - no daily bar is stamped that, so the chart
             drew no trade at all.
        Now: the bar covering the fill.
        """
        leg = {'o': 1.2, 'h': 1.2, 'l': 1.2, 'c': 1.2}
        day = datetime.timedelta(days=1)
        candles = [types.SimpleNamespace(time=T0 + i * day, mid=leg, ask=leg,
                                         bid=leg) for i in range(3)]
        trade = {'key': 1, 'direction': 'long', 'units': 1,
                 'signalTime': T0, 'orderPrice': 1.2, 'stopLoss': 1.1,
                 'takeProfit': 1.3, 'entryPrice': 1.2, 'exitPrice': 1.3,
                 'entryTime': T0 + day + datetime.timedelta(hours=14, minutes=35),
                 'exitTime': T0 + 2 * day + datetime.timedelta(minutes=5),
                 'outcome': 'target', 'pl': 1, 'balance': 1}
        run = types.SimpleNamespace(
            instrument='EUR_USD', granularity='D', strategy='AG01',
            dtfrom=T0, dtto=T0 + 2 * day, candles=candles, trades=[trade],
            counts={}, balance=None, risk=None, fine='M5', maxStopPips=None)
        placed = self.service.payload(run, 0.0)['trades'][0]
        self.assertEqual((placed['signalIndex'], placed['entryIndex'],
                          placed['exitIndex']), (0, 1, 2))

    def test_the_shape_is_what_the_page_reads(self):
        for key in ('instrument', 'granularity', 'strategy', 'from', 'to',
                    'candles', 'trades', 'counts', 'report', 'elapsed',
                    'balance', 'risk', 'fine', 'maxStopPips'):
            self.assertIn(key, self.payload)

    def test_a_candle_is_nine_numbers_in_a_fixed_order(self):
        """
        Arrays rather than objects: nine numbers a bar instead of nine names
        repeated a thousand times. The order is agreed here and in app.js and
        nowhere else, which is why it is pinned.
        """
        candle = self.payload['candles'][0]
        self.assertEqual(len(candle), 9)
        when, o, h, l, c, ask_h, ask_l, bid_h, bid_l = candle
        self.assertIsInstance(when, int)
        self.assertLessEqual(l, o)
        self.assertGreaterEqual(h, c)
        self.assertGreater(ask_h, bid_l)

    def test_the_candles_are_the_ones_the_run_saw(self):
        self.assertEqual(len(self.payload['candles']),
                         self.payload['counts']['candles'])

    def test_times_are_epoch_milliseconds(self):
        """
        Not an ISO string: one without an offset is parsed by a browser in the
        viewer's own timezone, which moves every candle by however many hours
        that happens to be.
        """
        self.assertEqual(self.payload['candles'][0][0], millis(T0))

    def test_a_trade_says_which_bars_to_zoom_to(self):
        trades = self.payload['trades']
        self.assertTrue(trades, "the fixture should produce at least one trade")
        trade = trades[0]
        self.assertIsNotNone(trade['entryIndex'])
        self.assertEqual(self.payload['candles'][trade['entryIndex']][0],
                         trade['entryTime'])

    def test_a_trades_entry_lies_inside_the_bar_that_filled_it(self):
        """
        The whole point of the zoom is that the fill can be checked against
        the bar, so a fill outside its bar's range would be the one thing the
        chart cannot show honestly.
        """
        index = dict((c[0], c) for c in self.payload['candles'])
        for trade in self.payload['trades']:
            bar = index[trade['entryTime']]
            low, high = (bar[6], bar[5]) if trade['direction'] == 'long' \
                else (bar[8], bar[7])
            self.assertGreaterEqual(trade['entryPrice'], low)
            self.assertLessEqual(trade['entryPrice'], high)

    def test_the_report_counts_the_same_trades(self):
        self.assertEqual(self.payload['report']['trades'],
                         len(self.payload['trades']))

    def test_the_same_request_is_answered_from_the_cache(self):
        again = self.service.backtest('EUR_USD', 'H1')
        self.assertIs(again, self.payload)

    def test_the_cache_is_bounded(self):
        service = Service(setup=self.settings, max_candles=1000, cache_size=1)
        first = service.backtest('EUR_USD', 'H1', units=1)
        service.backtest('EUR_USD', 'H1', units=2)
        self.assertIsNot(service.backtest('EUR_USD', 'H1', units=1), first)


class StartingBalanceTest(StoreCase):
    """
    What the account opens with, and the curve the page draws from it.

    The curve itself is the trades' own 'balance' read in exit order, so what
    is pinned here is the two things it stands on: that the opening figure is
    the one asked for, and that the closing one is that figure plus what the
    run made.
    """

    def closed(self, payload):
        """The trades with a realised balance, in the order they closed."""
        done = [t for t in payload['trades']
                if t['exitTime'] is not None and t['balance'] is not None]
        return sorted(done, key=lambda t: t['exitTime'])

    def test_the_account_opens_with_the_setting(self):
        payload = self.service.backtest('EUR_USD', 'H1')
        self.assertEqual(payload['balance'], float(self.settings.EQUITY))

    def test_the_account_opens_with_what_was_asked_for(self):
        payload = self.service.backtest('EUR_USD', 'H1', balance=5000)
        self.assertEqual(payload['balance'], 5000.0)
        self.assertAlmostEqual(self.closed(payload)[0]['balance'],
                               5000.0 + self.closed(payload)[0]['pl'], places=9)

    def test_the_last_balance_is_the_opening_one_plus_the_net(self):
        payload = self.service.backtest('EUR_USD', 'H1', balance=5000)
        done = self.closed(payload)
        self.assertTrue(done, "the fixture is meant to produce closed trades")
        self.assertAlmostEqual(done[-1]['balance'],
                               5000.0 + payload['report']['net'], places=9)

    def test_the_trades_are_the_same_whatever_the_account_holds(self):
        """
        Position size is `units`, not a fraction of equity, so a different
        opening balance must move the curve and nothing else. A run that
        traded differently would mean sizing had quietly started to depend on
        it, and every backtest ever compared across two balances would be
        comparing two strategies.
        """
        poor = self.service.backtest('EUR_USD', 'H1', balance=1000)
        rich = self.service.backtest('EUR_USD', 'H1', balance=999999)
        self.assertEqual([t['entryTime'] for t in poor['trades']],
                         [t['entryTime'] for t in rich['trades']])
        self.assertAlmostEqual(poor['report']['net'], rich['report']['net'],
                               places=9)

    def test_two_balances_are_two_answers(self):
        """The opening balance is part of the question, so part of the key."""
        first = self.service.backtest('EUR_USD', 'H1', balance=1000)
        self.assertIsNot(self.service.backtest('EUR_USD', 'H1', balance=2000),
                         first)
        self.assertIs(self.service.backtest('EUR_USD', 'H1', balance=1000),
                      first)


class IndicatorPayloadTest(StoreCase):
    """The service's end: the strategy declares, the payload carries."""

    def test_a_strategy_that_declares_nothing_sends_nothing(self):
        payload = self.service.backtest('EUR_USD', 'H1', strategy='AG01')
        self.assertEqual(payload['indicators'], [])

    def test_the_declaration_is_read_off_the_strategy(self):
        self.assertEqual(self.service.indicatorSpecs('AG01'), [])

    def test_a_declared_curve_is_computed_over_the_run(self):
        """
        Patched onto the class rather than asserted against whichever
        strategy happens to declare one today: what is under test is the
        wiring, and a test naming a strategy's periods would fail the day
        somebody tuned them.
        """
        handler = ledger_module.load_strategy('AG01')
        with mock.patch.object(handler, 'INDICATORS',
                               ({'kind': 'sma', 'period': 3},), create=True):
            payload = self.service.backtest('EUR_USD', 'H1', strategy='AG01')
        curve, = payload['indicators']
        self.assertEqual(curve['label'], 'SMA 3')
        self.assertEqual(len(curve['values']), len(payload['candles']))
        self.assertEqual(curve['values'][:2], [None, None])
        self.assertAlmostEqual(
            curve['values'][2],
            sum(c[4] for c in payload['candles'][:3]) / 3.0)

    def test_an_unknown_strategy_declares_nothing_rather_than_raising(self):
        """check() refuses it first; this is only about not raising here."""
        self.assertEqual(self.service.indicatorSpecs('NOSUCH'), [])


class ProgressHandlerTest(unittest.TestCase):
    """
    ledger.Progress: where the replay is, while it is still running.

    A run over eleven years fills on a million M5 bars and takes minutes, and
    the page has nothing to show for them unless somebody on the bus says so.
    """

    def watcher(self, every=0):
        self.seen = []
        return ledger_module.Progress(
            self.seen.append, account=types.SimpleNamespace(balance=1234.5),
            instrument='DE30_EUR', granularity='M1', every=every)

    def test_it_reports_the_bar_it_is_on_and_what_the_account_holds(self):
        watcher = self.watcher()
        watcher.execute_event(candle(T0))
        self.assertEqual(self.seen, [{'bars': 1, 'at': T0, 'balance': 1234.5}])

    def test_it_counts_the_trades_closed_so_far(self):
        """
        The same rule the report at the end uses: above zero is a win, below
        it a loss, and a trade still open is neither. Two counts that
        disagree about the same run are worse than one count.
        """
        watcher = self.watcher()
        watcher.ledger = types.SimpleNamespace(trades=lambda: [
            {'exitTime': T0, 'pl': 2.0},
            {'exitTime': T0, 'pl': -1.0},
            {'exitTime': T0, 'pl': 0.0},
            {'exitTime': None, 'pl': None},
        ])
        watcher.execute_event(candle(T0))
        self.assertEqual(self.seen[-1]['trades'], 3, "the open one is not a trade yet")
        self.assertEqual(self.seen[-1]['won'], 1)
        self.assertEqual(self.seen[-1]['lost'], 1)

    def test_it_counts_the_strategy_s_bars_and_not_the_fine_ones(self):
        """
        The other stream is the one the orders rest on - a million M5 bars
        under 18 000 H4 ones. Counting those would report a share of a series
        nobody asked about, and a progress line that runs to 5000%.
        """
        watcher = self.watcher()
        watcher.execute_event(candle(T0))
        watcher.execute_event(candle(T0, granularity='M5'))
        watcher.execute_event(candle(T0, instrument='EUR_USD'))
        self.assertEqual([one['bars'] for one in self.seen], [1])

    def test_it_speaks_on_a_clock_and_not_on_every_bar(self):
        """A million calls to report a line nobody can read that fast."""
        watcher = self.watcher(every=3600)
        for _ in range(50):
            watcher.execute_event(candle(T0))
        self.assertEqual(len(self.seen), 1)
        watcher.say()
        self.assertEqual(self.seen[-1]['bars'], 50,
                         "the end of the run is reported whatever the clock says")


class ProgressTest(StoreCase):
    """The service's end of it: what /api/progress answers."""

    def test_before_anything_has_run(self):
        self.assertEqual(self.service.progress(), {'running': False})

    def test_the_reading_reports_before_any_bar_is_simulated(self):
        """
        Rows read out of rows to read, which is not the run's own bar count:
        the fine series the orders rest on is where the minute goes.
        """
        seen = []
        ledger_module.run('EUR_USD', 'H1', 'AG01', setup=self.settings,
                          progress=seen.append)
        reading = [one for one in seen if one.get('loading')]
        self.assertTrue(reading, "nothing was said while the store was read")
        self.assertEqual(reading[0]['read'], 0)
        self.assertTrue(reading[0]['toRead'])
        self.assertIn('H1', reading[0]['stage'])
        self.assertFalse(seen[-1].get('loading'),
                         "and the last word is a bar, not a row")

    def test_a_finished_run_says_where_it_got_to(self):
        self.service.backtest('EUR_USD', 'H1')
        where = self.service.progress()
        self.assertFalse(where['running'])
        self.assertEqual(where['bars'], where['total'],
                         "a run that ended at 97% reads as one that stopped")
        self.assertEqual(where['instrument'], 'EUR_USD')
        self.assertIsNotNone(where['balance'])

    def test_a_run_that_raised_is_not_left_running(self):
        """A status line stuck at 43% after a failure is worse than none."""
        with mock.patch.object(ledger_module, 'run',
                               side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.service.backtest('EUR_USD', 'H1',
                                      dtfrom=T0 + datetime.timedelta(hours=1))
        self.assertFalse(self.service.progress()['running'])


class StopTest(StoreCase):
    """
    Stopping a run that is already going.

    Cooperative, and it has to be: the replay is a loop over bars in the
    thread serving its own request, and killing that thread mid-bar would
    leave the simulator's book in a state nothing here reasons about.
    """

    def test_a_report_that_says_no_stops_the_replay(self):
        """The whole mechanism: the listener answers, and False means stop."""
        with self.assertRaises(Cancelled) as caught:
            # through the reading, and no once the bars start
            ledger_module.run('EUR_USD', 'H1', 'AG01', setup=self.settings,
                              progress=lambda where: bool(where.get('loading')))
        self.assertIn('stopped after', str(caught.exception))

    def test_a_report_that_says_nothing_lets_it_run(self):
        """Anything but False carries on - a listener that only looks at the
        line it is drawing returns None, and None is not a refusal."""
        result = ledger_module.run('EUR_USD', 'H1', 'AG01',
                                   setup=self.settings,
                                   progress=lambda where: None)
        self.assertTrue(result.candles)

    def test_it_can_be_stopped_while_the_candles_are_still_being_read(self):
        """
        The reading is a minute of an eleven year run, before a single bar
        has been simulated. A stop that only took effect after it would be a
        button that does nothing for the first minute.
        """
        seen = []

        def refuse(where):
            seen.append(where)
            return False

        with self.assertRaises(Cancelled) as caught:
            ledger_module.run('EUR_USD', 'H1', 'AG01', setup=self.settings,
                              progress=refuse)
        self.assertIn('stopped while reading', str(caught.exception))
        self.assertTrue(seen[0]['loading'], "it stopped before the first bar")
        self.assertIn('read', seen[0])

    def test_stopping_when_nothing_is_running(self):
        with self.assertRaises(ServiceError) as caught:
            self.service.stop()
        self.assertIn('no backtest is running', str(caught.exception))

    def test_a_stopped_run_is_a_refusal_and_nothing_is_kept(self):
        """
        Not a payload with a flag on it: a backtest over part of a window,
        drawn and reported as that backtest, is the one outcome worse than no
        backtest at all.
        """
        service = self.service

        def stopping(*args, **kwargs):
            report = kwargs['progress']
            self.assertNotEqual(
                report({'bars': 3, 'at': T0, 'balance': 1.0}), False,
                "it should be running before anybody asks it to stop")
            service.stop()
            self.assertIs(
                report({'bars': 4, 'at': T0, 'balance': 1.0}), False,
                "and told to stop at the next bar after that")
            raise Cancelled('stopped after 4 bars, at %s' % T0)

        with mock.patch.object(ledger_module, 'run', side_effect=stopping):
            with self.assertRaises(ServiceError) as caught:
                service.backtest('EUR_USD', 'H1')
        self.assertIn('stopped after 4 bars', str(caught.exception))
        self.assertFalse(service.progress()['running'])
        self.assertEqual(service._cache, {}, "a stopped run is not cached")

    def test_the_next_run_is_not_stopped_too(self):
        """The flag belongs to the run that was stopped and to no other."""
        service = self.service
        with mock.patch.object(ledger_module, 'run',
                               side_effect=Cancelled('stopped after 1 bars')):
            with self.assertRaises(ServiceError):
                service.backtest('EUR_USD', 'H1')
        self.assertTrue(service.backtest('EUR_USD', 'H1')['candles'])


class CalendarServiceTest(StoreCase):
    """
    The service's end of the calendar: say what the file holds, and take the
    one the browser collected. The collecting itself is not here and cannot
    be - it happens in a browser on the site's own page, because the site
    refuses this address.
    """

    CSV = ("time,currency,impact,title\n"
           "2015-01-09 13:30:00,USD,high,Non-Farm Employment Change\n"
           "2015-01-09 15:00:00,EUR,low,Something small\n")

    def test_no_file_is_not_an_error(self):
        state = self.service.calendar()
        self.assertEqual(state['events'], 0)
        self.assertIsNone(state['from'])
        self.assertEqual(state['start'], T0.strftime('%Y-%m-%d'),
                         "with no calendar at all, start where the candles do")

    def test_the_history_is_collected_before_the_weeks_since(self):
        """
        A calendar holding only last week is a calendar with the past
        missing, and the past is what a backtest reads. So the suggestion is
        the day the stores start, and it becomes 'the day after the last
        event' only once the beginning is covered.
        """
        self.service.importCalendar(
            "time,currency,impact,title\n"
            "2026-09-21 13:30:00,USD,high,Something recent\n")
        self.assertEqual(self.service.calendar()['start'],
                         T0.strftime('%Y-%m-%d'))

        self.service.importCalendar(
            "time,currency,impact,title\n"
            "%s,USD,high,At the very beginning\n"
            % T0.strftime('%Y-%m-%d %H:%M:%S'))
        self.assertEqual(self.service.calendar()['start'], '2026-09-22',
                         "the beginning is covered now, so carry on from the end")

    def test_what_is_imported_is_what_it_reports(self):
        done = self.service.importCalendar(self.CSV)
        self.assertEqual((done['read'], done['added'], done['events']), (2, 2, 2))
        self.assertEqual(done['impacts'], {'high': 1, 'low': 1})
        self.assertEqual(done['from'], millis(datetime.datetime(2015, 1, 9, 13, 30)))

    def test_a_second_stretch_is_merged_and_not_dropped(self):
        """
        The collector is pointed at a stretch of weeks at a time, so the
        second import must keep the first: merged, and the same event twice
        is one event.
        """
        self.service.importCalendar(self.CSV)
        again = self.service.importCalendar(
            self.CSV + "2016-02-05 13:30:00,USD,high,Non-Farm Employment Change\n")
        self.assertEqual(again['read'], 3)
        self.assertEqual(again['added'], 1)
        self.assertEqual(again['events'], 3)

    def test_a_file_that_is_not_a_calendar_says_what_one_is(self):
        with self.assertRaises(ServiceError) as caught:
            self.service.importCalendar("a,b\n1,2\n")
        self.assertIn('time, currency, impact, title', str(caught.exception))

    def test_an_empty_upload(self):
        with self.assertRaises(ServiceError):
            self.service.importCalendar("   ")


class EstimateTest(StoreCase):
    """
    How big a run is, before anybody waits for it. The page warns on this and
    asks; the service only says how many bars and how long they took last
    time.
    """

    def test_it_counts_the_bars_the_simulator_walks(self):
        ahead = self.service.estimate('EUR_USD', 'H1')
        self.assertEqual(ahead['bars'], 200)
        self.assertEqual(ahead['ticks'], 200,
                         "nothing finer in this store, so the run is its own bars")
        self.assertIsNone(ahead['fine'])

    def test_the_fine_bars_are_in_the_count(self):
        """
        The whole point of the warning: a year of H4 is 1 616 candles and 75
        000 M5 bars under them, and it is the second number that is the wait.
        """
        path = os.path.join(self.tmpdir, 'EUR_USD.hd5')
        fine = store_module.load(path, 'H1')
        ahead = self.service.estimate('EUR_USD', 'H4')
        self.assertEqual(ahead['fine'], 'H1')
        self.assertGreater(ahead['ticks'], ahead['bars'])
        # the window is the H4 bars' own span, and an H4 bar is stamped at its
        # open: the fine bars inside the last one are past the last stamp and
        # are not walked, which is why this is the series less the tail of it
        counted = ahead['ticks'] - ahead['bars']
        self.assertLessEqual(counted, len(fine))
        self.assertGreaterEqual(counted, len(fine) - 4,
                                "at most one coarse bar's worth is outside")

    def test_the_rate_is_the_last_run_s(self):
        """Seeded with a measured figure and replaced by this machine's."""
        self.assertEqual(self.service.estimate('EUR_USD', 'H1')['rate'],
                         service_module.TICKS_A_SECOND)
        self.service.backtest('EUR_USD', 'H1')
        self.assertNotEqual(self.service.estimate('EUR_USD', 'H1')['rate'],
                            service_module.TICKS_A_SECOND)

    def test_the_seconds_follow_the_bars(self):
        ahead = self.service.estimate('EUR_USD', 'H1')
        self.assertAlmostEqual(ahead['seconds'],
                               round(ahead['ticks'] / ahead['rate'], 1), 1)

    def test_an_instrument_the_store_does_not_hold(self):
        with self.assertRaises(ServiceError):
            self.service.estimate('NOSUCH', 'H1')


class WhenToTradeTest(StoreCase):
    """
    The hours, the overnight rule and the news windows, as the page sends
    them: parsed here, applied in portfolio/, and part of what makes one run
    a different run from another.
    """

    def test_a_session_is_a_pair_of_times(self):
        self.assertEqual(service_module.parseSession('07:00-16:00'),
                         ('07:00', '16:00'))
        self.assertIsNone(service_module.parseSession(''))
        self.assertEqual(service_module.parseSession('22:00-06:00'),
                         ('22:00', '06:00'),
                         "an end before the start wraps midnight")

    def test_a_session_that_is_not_one(self):
        for bad in ('07:00', '07:00-', 'seven-four', '07:00-xx'):
            with self.assertRaises(ServiceError, msg=bad):
                service_module.parseSession(bad)

    def test_news_minutes(self):
        self.assertEqual(service_module.parseNews('15', '30'), (15, 30))
        self.assertIsNone(service_module.parseNews('0', '0'),
                          "no minutes is no rule")
        self.assertIsNone(service_module.parseNews('', ''))
        with self.assertRaises(ServiceError):
            service_module.parseNews('-5', '0')

    def test_an_impact_the_calendar_does_not_have(self):
        self.assertEqual(service_module.parseImpacts('high,medium'),
                         ('high', 'medium'))
        with self.assertRaises(ServiceError) as caught:
            service_module.parseImpacts('huge')
        self.assertIn('high', str(caught.exception))

    def test_each_of_them_is_a_different_run(self):
        """
        In the cache key, or the first answer would be served to every later
        question - a run with hours would come back as the run without them.
        """
        plain = self.service.key('EUR_USD', 'H1', 'AG01', T0, T0, 1)
        for extra in ({'session': ('07:00', '16:00')}, {'intraday': True},
                      {'news': (15, 15)}, {'newsImpacts': ('high', 'medium')}):
            self.assertNotEqual(
                plain, self.service.key('EUR_USD', 'H1', 'AG01', T0, T0, 1,
                                        **extra), extra)

    def test_the_hours_reach_the_run(self):
        """End to end: every trade taken was decided inside the window."""
        loose = self.service.backtest('EUR_USD', 'H1')
        self.assertTrue(loose['trades'])

        morning = self.service.backtest('EUR_USD', 'H1',
                                        session=('00:00', '06:00'))
        self.assertTrue(morning['trades'])
        self.assertLess(len(morning['trades']), len(loose['trades']))
        for trade in morning['trades']:
            self.assertLess(service_module.moment(trade['signalTime']).hour, 6,
                            "a signal outside the window was traded")

        # ten minutes between two whole hours, on an hourly store: nothing
        # can be decided in there
        never = self.service.backtest('EUR_USD', 'H1',
                                      session=('03:10', '03:20'))
        self.assertEqual(never['trades'], [])


class LiveCandlesTest(StoreCase):
    """
    /api/live/candles and /api/live/skew: what the live page draws, read off
    the candle database every session writes to (data/candledb.py).
    """

    def fill(self):
        from parity_deriva.data.candledb import CandleDB
        db = CandleDB(os.path.join(self.tmpdir, 'live', 'candles.db'))
        now = datetime.datetime.now(datetime.timezone.utc).replace(
            tzinfo=None, second=0, microsecond=0)
        for i in range(3):
            when = now - datetime.timedelta(minutes=5 * (4 - i))
            for provider, account, price in (('twelvedata', 'paper', 1.1),
                                             ('ig', 'Z1', 1.1002)):
                ohlc = {'o': price, 'h': price + 0.0001, 'l': price - 0.0001, 'c': price}
                event = CandleEvent({'time': when, 'mid': ohlc, 'bid': ohlc, 'ask': ohlc,
                                     'volume': 0, 'complete': True})
                event.instrument, event.granularity = 'EUR_USD', 'M5'
                db.write(event, provider, account, 's')
        return db

    def test_the_feeds_come_back_in_the_nine_numbers_with_the_paper_first(self):
        self.fill()
        got = self.service.liveCandles('EUR_USD', 'M5')
        self.assertEqual(got['reference'], 'twelvedata:paper')
        self.assertEqual([(f['feed'], len(f['candles'])) for f in got['feeds']],
                         [('twelvedata:paper', 3), ('ig:Z1', 3)])
        self.assertEqual(len(got['feeds'][0]['candles'][0]), 9)
        self.assertEqual((got['instrument'], got['granularity']), ('EUR_USD', 'M5'))

    def test_the_skew_is_in_pips_and_carries_the_budget(self):
        db = self.fill()
        db.logCall('twelvedata', 'EUR/USD', {}, 200, 50)
        got = self.service.liveSkew('EUR_USD', 'M5')
        ig = dict((f['feed'], f) for f in got['feeds'])['ig:Z1']
        self.assertEqual((ig['mean'], ig['missing']), (2.0, 0))
        self.assertEqual(got['twelvedata']['calls_today'], 1)
        self.assertEqual(got['twelvedata']['limit'], 800)

    def test_an_empty_database_is_an_empty_answer_not_an_error(self):
        got = self.service.liveCandles('EUR_USD', 'M5')
        self.assertEqual(got['feeds'], [])
        self.assertEqual(self.service.liveSkew('EUR_USD', 'M5')['feeds'], [])
        self.assertEqual(self.service.liveTradeSkew(), {'groups': []})

    def test_a_window_too_wide_or_backwards_is_refused(self):
        with self.assertRaises(ServiceError):
            self.service.liveCandles('EUR_USD', 'M1', T0, T0 + datetime.timedelta(days=30))
        with self.assertRaises(ServiceError):
            self.service.liveCandles('EUR_USD', 'M5', T0, T0 - datetime.timedelta(hours=1))
        with self.assertRaises(ServiceError):
            self.service.liveCandles('EUR_USD', 'X9')


class FavouritesTest(StoreCase):
    """
    api/favourites: a simulated form starred for the live page, with a
    snapshot of what the simulation made, read off the run's own files.
    """

    FIELDS = {'strategy': 'AG01', 'instrument': 'EUR_USD', 'granularity': 'H1',
              'from': '2018-01-01', 'to': '2018-01-31', 'risk': '1'}

    def saveARun(self):
        trades = [{'exitTime': millis(T0 + datetime.timedelta(days=d)), 'balance': b,
                   'pl': p} for d, b, p in ((1, 1010.0, 10.0), (2, 1005.0, -5.0))]
        payload = {'strategy': 'AG01', 'instrument': 'EUR_USD', 'granularity': 'H1',
                   'from': millis(T0), 'to': millis(T0 + datetime.timedelta(days=30)),
                   'balance': 1000.0, 'trades': trades,
                   'report': report_module.report([
                       {'outcome': 'TAKE_PROFIT', 'pl': 10.0, 'balance': 1010.0},
                       {'outcome': 'STOP_LOSS', 'pl': -5.0, 'balance': 1005.0}])}
        self.service.saveRun(self.FIELDS, payload)
        return self.service.runId(self.FIELDS)

    def saveASweep(self):
        job = {'id': '20260924-120000-abcdef', 'name': 'griglia', 'fields': self.FIELDS,
               'total': 2, 'finished': 1790000000.0, 'done': [
                   {'n': 1, 'params': {'slScale': '1.5'}, 'final': 1020.0, 'balance': 1000.0,
                    'report': {'closedTrades': 4, 'net': 20.0, 'winRate': 0.5,
                               'profitFactor': 2.0, 'maxDrawdown': 3.0},
                    'kpi': {'roi': 2.0, 'car': 24.0, 'maxDrawdownPct': 0.3, 'sharpe': 1.1},
                    'curve': []},
                   {'n': 2, 'params': {'slScale': '2'}, 'error': 'boom'}]}
        self.service.saveSweep(job)
        return job['id']

    def test_a_saved_run_becomes_a_favourite_with_its_summary(self):
        run = self.saveARun()
        entry = self.service.addFavourite({'kind': 'run', 'id': run}, note='la buona')
        self.assertEqual(entry['id'], run)
        self.assertEqual(entry['fields'], self.FIELDS)
        self.assertEqual(entry['source'], {'kind': 'run', 'id': run, 'n': None, 'name': None})
        self.assertEqual(entry['note'], 'la buona')
        summary = entry['summary']
        self.assertEqual((summary['strategy'], summary['instrument'], summary['granularity']),
                         ('AG01', 'EUR_USD', 'H1'))
        self.assertEqual((summary['trades'], summary['net'], summary['winRate']),
                         (2, 5.0, 0.5))
        self.assertEqual((summary['start'], summary['final']), (1000.0, 1005.0))
        self.assertAlmostEqual(summary['roi'], 0.5)
        self.assertIsNotNone(summary['maxDrawdownPct'])
        self.assertEqual([f['id'] for f in self.service.favourites()], [run])

    def test_a_sweep_run_becomes_a_favourite_with_the_rows_own_numbers(self):
        sweep = self.saveASweep()
        entry = self.service.addFavourite({'kind': 'sweep', 'id': sweep, 'n': 1})
        # the form is the sweep's with the row's parameters over it
        self.assertEqual(entry['fields']['slScale'], '1.5')
        self.assertEqual(entry['fields']['strategy'], 'AG01')
        self.assertEqual(entry['source']['name'], 'griglia')
        self.assertEqual((entry['summary']['trades'], entry['summary']['final'],
                          entry['summary']['roi'], entry['summary']['sharpe']),
                         (4, 1020.0, 2.0, 1.1))
        self.assertEqual(entry['summary']['from'],
                         millis(service_module.parseDate('2018-01-01', 'from')))
        with self.assertRaises(ServiceError):
            self.service.addFavourite({'kind': 'sweep', 'id': sweep, 'n': 2})
        with self.assertRaises(ServiceError):
            self.service.addFavourite({'kind': 'sweep', 'id': sweep})

    def test_starring_twice_is_one_favourite_and_keeps_the_note(self):
        run = self.saveARun()
        self.service.addFavourite({'kind': 'run', 'id': run}, note='n1')
        again = self.service.addFavourite({'kind': 'run', 'id': run})
        self.assertEqual(again['note'], 'n1')
        self.assertEqual(len(self.service.favourites()), 1)
        self.service.noteFavourite(run, 'n2')
        self.assertEqual(self.service.favourites()[0]['note'], 'n2')
        self.service.dropFavourite(run)
        self.assertEqual(self.service.favourites(), [])
        with self.assertRaises(ServiceError):
            self.service.noteFavourite(run, 'gone')

    def test_what_is_not_on_disk_is_refused(self):
        with self.assertRaises(ServiceError):
            self.service.addFavourite({'kind': 'run', 'id': '0123456789abcdef'})
        with self.assertRaises(ServiceError):
            self.service.addFavourite({'kind': 'form', 'id': 'x'})
        with self.assertRaises(ServiceError):
            self.service.addFavourite({'kind': 'run', 'id': '../etc'})

    def test_the_routes(self):
        run = self.saveARun()
        server = service_module.serve(host='127.0.0.1', port=0, setup=self.settings)
        server.RequestHandlerClass.service = self.service
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:%d' % server.server_address[1]
        try:
            def call(path, body=None):
                request = urllib.request.Request(
                    base + path, data=None if body is None else json.dumps(body).encode(),
                    headers={'X-Parity-Deriva': '1'} if body is not None else {})
                with urllib.request.urlopen(request) as answer:
                    return json.loads(answer.read())
            self.assertEqual(call('/api/favourites'), {'favourites': []})
            added = call('/api/favourites', {'source': {'kind': 'run', 'id': run}})
            self.assertEqual(added['added']['id'], run)
            self.assertEqual(len(added['favourites']), 1)
            self.assertEqual(call('/api/favourites/%s' % run, {'note': 'x'})
                             ['favourites'][0]['note'], 'x')
            self.assertEqual(call('/api/favourites/%s/delete' % run, {}), {'favourites': []})
            with self.assertRaises(urllib.error.HTTPError):
                call('/api/favourites', {'nope': 1})
        finally:
            server.shutdown()
            server.server_close()


class SeriesTest(StoreCase):
    """
    /api/candles: the bars on their own, at whatever granularity the chart
    asks for. This is what the zoom reads when it opens a coarse candle up.
    """

    def test_the_bars_come_back_in_the_window_asked_for(self):
        rows = self.service.series('EUR_USD', 'H1', T0,
                                   T0 + datetime.timedelta(hours=5))
        self.assertEqual(len(rows['candles']), 6)
        self.assertEqual(rows['candles'][0][0], millis(T0))
        self.assertEqual(rows['granularity'], 'H1')

    def test_a_bar_is_the_same_nine_numbers_a_backtest_sends(self):
        """
        One shape, so the page draws both with one function. A second order
        for the same four prices is a bug nobody sees until the chart is
        upside down.
        """
        drawn = self.service.series('EUR_USD', 'H1')['candles'][0]
        ran = self.service.backtest('EUR_USD', 'H1')['candles'][0]
        self.assertEqual(len(drawn), 9)
        self.assertEqual(drawn, ran)

    def test_a_window_on_a_derived_series_is_the_whole_one_cut(self):
        """
        The one that matters. A derived bar is built from the finer rows
        underneath it, so a window read too narrowly builds its first and
        last bars out of part of themselves - a candle that never traded,
        drawn at the edge of every zoom.
        """
        import pandas as pd
        path = os.path.join(self.tmpdir, 'EUR_USD.hd5')
        whole = store_module.load(path, 'H4')
        for start, end in ((T0, T0 + datetime.timedelta(hours=20)),
                           (T0 + datetime.timedelta(hours=2, minutes=7),
                            T0 + datetime.timedelta(hours=19, minutes=53))):
            window = store_module.load(path, 'H4', start, end)
            cut = whole[(whole.index >= pd.Timestamp(start))
                        & (whole.index <= pd.Timestamp(end))]
            self.assertTrue(cut.equals(window),
                            "the %s window is not the whole series cut" % (start,))

    def test_a_granularity_the_store_cannot_build_is_refused(self):
        with self.assertRaises(ServiceError) as caught:
            self.service.series('EUR_USD', 'M5', T0, T0 + datetime.timedelta(hours=5))
        self.assertIn('M5', str(caught.exception))

    def test_a_window_too_wide_to_draw_is_refused_here_too(self):
        """The chart's zoom cannot ask for what a run cannot ask for."""
        service = Service(setup=self.settings, max_candles=10)
        with self.assertRaises(ServiceError) as caught:
            service.series('EUR_USD', 'H1')
        self.assertIn('limit is 10', str(caught.exception))


class SlopeOverlayTest(unittest.TestCase):
    """
    RG2 - what the chart shades with. The measure is lib/indicators.py's; what
    is pinned here is where the thresholds are read.
    """

    def line(self, n, step):
        closes = [100.0 + step * i for i in range(n)]
        return closes, [c + 0.05 for c in closes], [c - 0.05 for c in closes]

    def test_the_thresholds_are_read_on_the_front_of_the_run_only(self):
        """
        A percentile over the whole run is a number that knows how the run
        ends. Shading with it would colour the early bars with what the late
        ones did, which is the look-ahead this package spends its life on -
        wearing a statistician's hat, but the same one.
        """
        head = self.line(service_module.SLOPE_TRAINING, 0.01)
        tail = self.line(400, 0.5)
        whole = tuple([a + b for a, b in zip(head, tail)])
        self.assertEqual(
            service_module.slopeOverlay(*whole)['thresholds'],
            service_module.slopeOverlay(*head)['thresholds'])

    def test_a_run_too_short_to_warm_up_gets_nothing(self):
        """None rather than a threshold read off four bars."""
        self.assertIsNone(service_module.slopeOverlay(*self.line(60, 0.1)))

    def test_there_is_one_reading_per_bar(self):
        overlay = service_module.slopeOverlay(*self.line(300, 0.1))
        self.assertEqual(len(overlay['values']), 300)
        self.assertEqual(overlay['values'][:100], [None] * 100)
        self.assertEqual([one['percentile'] for one in overlay['thresholds']],
                         list(service_module.SLOPE_PERCENTILES))

    def test_the_payload_carries_it(self):
        settings_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, settings_dir)
        run_ = types.SimpleNamespace(
            instrument='EUR_USD', granularity='H1', strategy='AG01',
            dtfrom=T0, dtto=T0, candles=[], trades=[], counts={},
            balance=None, risk=None, fine=None, maxStopPips=None)
        service = Service(setup=types.SimpleNamespace(
            STORE_DIR=settings_dir, IMPORT_DIR=settings_dir, EQUITY=10000))
        payload = service.payload(run_, 0.0)
        self.assertIn('slope', payload)
        self.assertIsNone(payload['slope'], "no candles, no reading")


class DescriptionTest(unittest.TestCase):
    """
    What each strategy says it does, for the line the page prints over the
    chart. Read off the strategy, like INDICATORS is, so that the page holds
    no copy that could stop being true.
    """

    def test_a_strategy_that_declares_one_is_listed(self):
        handler = ledger_module.load_strategy('AG01')
        with mock.patch.object(handler, 'DESCRIPTION', 'what it does',
                               create=True):
            self.assertEqual(service_module.descriptions()['AG01'], 'what it does')

    def test_one_that_declares_nothing_is_absent_rather_than_blank(self):
        handler = ledger_module.load_strategy('AG01')
        with mock.patch.object(handler, 'DESCRIPTION', None, create=True):
            self.assertNotIn('AG01', service_module.descriptions())

    def test_the_wrapping_of_the_source_is_not_the_wrapping_on_the_page(self):
        """A string joined over several source lines arrives as one line."""
        handler = ledger_module.load_strategy('AG01')
        with mock.patch.object(handler, 'DESCRIPTION',
                               'two   lines\n  of it', create=True):
            self.assertEqual(service_module.descriptions()['AG01'], 'two lines of it')


class SetupBarsTest(StoreCase):
    """
    The box the chart draws around the candles an entry rule read: how wide
    it is comes from the strategy, and where it ends comes from the signal.
    """

    def test_the_width_is_read_off_the_strategy(self):
        handler = ledger_module.load_strategy('AG01')
        with mock.patch.object(handler, 'SETUP_BARS', 7, create=True):
            self.assertEqual(self.service.setupBars('AG01'), 7)

    def test_a_strategy_that_says_nothing_gets_no_box(self):
        handler = ledger_module.load_strategy('AG01')
        with mock.patch.object(handler, 'SETUP_BARS', None, create=True):
            self.assertIsNone(self.service.setupBars('AG01'))

    def test_an_unknown_strategy_says_nothing_rather_than_raising(self):
        """check() refuses it first; this is only about not raising here."""
        self.assertIsNone(self.service.setupBars('NOSUCH'))

    def test_the_payload_carries_it(self):
        payload = self.service.backtest('EUR_USD', 'H1', strategy='AG01')
        self.assertEqual(payload['setupBars'],
                         ledger_module.load_strategy('AG01').SETUP_BARS)

    def test_every_trade_says_which_bar_its_signal_fired_on(self):
        """
        The box ends there and not at the entry: a pending order can be
        filled days after the decision, and the candles worth boxing are the
        ones up to the decision.
        """
        payload = self.service.backtest('EUR_USD', 'H1', strategy='AG01')
        self.assertTrue(payload['trades'], "the fixture entered no trades")
        times = dict((c[0], i) for i, c in enumerate(payload['candles']))
        for trade in payload['trades']:
            self.assertEqual(trade['signalIndex'], times[trade['signalTime']])
            self.assertLessEqual(trade['signalIndex'], trade['entryIndex'])


class RiskSizingTest(StoreCase):
    """
    The service's end of the risk rule. What the sizing itself does is pinned
    in moneymanager_test.py; what is asked here is that the page's percentage
    arrives as the fraction the engine works in, and that it reaches the run.
    """

    def test_a_run_without_a_risk_says_so(self):
        payload = self.service.backtest('EUR_USD', 'H1')
        self.assertIsNone(payload['risk'])

    def test_the_risk_is_reported_back(self):
        payload = self.service.backtest('EUR_USD', 'H1', risk=0.01)
        self.assertEqual(payload['risk'], 0.01)

    def test_a_risk_sizes_the_trades_off_the_capital(self):
        """
        Under a risk rule the size is the capital times the risk over the
        distance to the stop, so doubling the capital doubles every size and
        leaves the entries where they were.
        """
        poor = self.service.backtest('EUR_USD', 'H1', balance=50000, risk=0.01)
        rich = self.service.backtest('EUR_USD', 'H1', balance=100000, risk=0.01)
        self.assertTrue(poor['trades'], "the fixture is meant to trade")
        self.assertEqual([t['entryTime'] for t in poor['trades']],
                         [t['entryTime'] for t in rich['trades']])
        for small, large in zip(poor['trades'], rich['trades']):
            self.assertAlmostEqual(large['units'], small['units'] * 2, places=1)

    def test_a_risk_and_a_fixed_size_are_two_different_runs(self):
        fixed = self.service.backtest('EUR_USD', 'H1', units=1)
        risked = self.service.backtest('EUR_USD', 'H1', risk=0.01)
        self.assertIsNot(fixed, risked)
        self.assertNotEqual([t['units'] for t in fixed['trades']],
                            [t['units'] for t in risked['trades']])


class PercentTest(unittest.TestCase):

    def test_an_absent_percentage_is_the_default(self):
        self.assertIsNone(parsePercent(None, 'risk', None))
        self.assertIsNone(parsePercent('', 'risk', None))

    def test_a_percentage_arrives_as_a_fraction(self):
        self.assertAlmostEqual(parsePercent('1', 'risk', None), 0.01)
        self.assertAlmostEqual(parsePercent('2.5', 'risk', None), 0.025)
        self.assertAlmostEqual(parsePercent('100', 'risk', None), 1.0)

    def test_nothing_outside_zero_to_a_hundred_is_taken(self):
        for text in ('0', '-1', '101', 'some'):
            with self.assertRaises(ServiceError) as caught:
                parsePercent(text, 'risk', None)
            self.assertIn('risk', str(caught.exception))


class PipsTest(unittest.TestCase):

    def test_an_absent_distance_is_the_default(self):
        self.assertIsNone(parsePips(None, 'maxStop'))
        self.assertIsNone(parsePips('', 'maxStop'))

    def test_a_distance_is_taken_as_it_was_typed(self):
        """Pips, not a fraction: the money manager divides by the pip."""
        self.assertEqual(parsePips('30', 'maxStop'), 30.0)
        self.assertEqual(parsePips('12.5', 'maxStop'), 12.5)

    def test_zero_is_a_rule_that_refuses_everything(self):
        """So it is refused here, rather than served as an empty backtest."""
        for text in ('0', '-5', 'wide'):
            with self.assertRaises(ServiceError) as caught:
                parsePips(text, 'maxStop')
            self.assertIn('maxStop', str(caught.exception))


class AmountTest(unittest.TestCase):

    def test_an_absent_amount_is_the_default(self):
        self.assertIsNone(parseAmount(None, 'balance', None))
        self.assertIsNone(parseAmount('', 'balance', None))

    def test_a_number_is_taken(self):
        self.assertEqual(parseAmount('2500.5', 'balance', None), 2500.5)

    def test_nothing_is_refused_by_name(self):
        for text in ('0', '-1', 'lots'):
            with self.assertRaises(ServiceError) as caught:
                parseAmount(text, 'balance', None)
            self.assertIn('balance', str(caught.exception))


class HTTPCase(StoreCase):
    """A server on a loopback socket, because routing is what is tested."""

    def setUp(self):
        super(HTTPCase, self).setUp()
        handler = type('TestHandler', (service_module.Handler,),
                       {'service': self.service})
        from http.server import ThreadingHTTPServer
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self, path):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def json(self, path):
        status, body, _headers = self.get(path)
        return status, json.loads(body.decode('utf-8'))

    def post(self, path, data=b'', header=True):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), data=data, method='POST',
            headers={'X-Parity-Deriva': '1'} if header else {})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode())

    def backtest(self, query):
        """POST /api/backtest with the fields of a query string."""
        fields = dict(urllib.parse.parse_qsl(query))
        return self.post('/api/backtest', json.dumps(fields).encode())



class CollectorTest(HTTPCase):
    """
    The one door this service opens to another site.

    The calendar collector runs on forexfactory's own page - it has to, since
    a page may not read another site's pages - so its POST is cross origin.
    What is pinned here is how narrow the opening is: one origin, one route,
    one token, and nothing else on the port answers a browser at all.
    """

    CSV = ("time,currency,impact,title\n"
           "2015-01-09 13:30:00,USD,high,Non-Farm Employment Change\n")

    def options(self, path, origin):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), method='OPTIONS',
            headers={'Origin': origin, 'Access-Control-Request-Method': 'POST'})
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                return answer.status, answer.headers
        except urllib.error.HTTPError as error:
            return error.code, error.headers

    def push(self, token, data=None, origin=service_module.COLLECTOR_ORIGIN,
             path='/api/calendar'):
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s?token=%s" % (self.port, path, token),
            data=(self.CSV if data is None else data).encode('utf-8'),
            method='POST',
            headers={'Origin': origin, 'Content-Type': 'text/plain'})
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                return answer.status, json.loads(answer.read().decode()), answer.headers
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode()), error.headers

    def test_the_collector_carries_this_service_s_address_and_token(self):
        status, body, headers = self.get('/api/collector?origin=http%3A%2F%2Fhere%3A1')
        self.assertEqual(status, 200)
        self.assertIn('javascript', headers['Content-Type'])
        text = body.decode('utf-8')
        self.assertIn("FF_PUSH_TO = 'http://here:1'", text)
        self.assertIn("FF_TOKEN = '%s'" % self.service.token, text)
        self.assertNotIn('__TOKEN__', text)

    def test_the_collector_starts_where_the_candles_do(self):
        """
        The history first. A collector that started today would collect the
        week nobody is backtesting.
        """
        _status, body, _headers = self.get('/api/collector?origin=x')
        self.assertIn("FF_FROM = '%s'" % T0.strftime('%Y-%m-%d'),
                      body.decode('utf-8'))

    def test_a_start_can_be_asked_for(self):
        _status, body, _headers = self.get('/api/collector?origin=x&from=2008-01-01')
        self.assertIn("FF_FROM = '2008-01-01'", body.decode('utf-8'))

    def test_the_token_is_not_in_the_file_on_disk(self):
        """The blanks are filled when it is handed out, so the file is not a
        copy of the key."""
        status, body, _headers = self.get('/static/ff-calendar.js')
        self.assertEqual(status, 200)
        self.assertIn(b'__TOKEN__', body)

    def test_the_preflight_is_answered_for_the_collector(self):
        status, headers = self.options('/api/calendar',
                                       service_module.COLLECTOR_ORIGIN)
        self.assertEqual(status, 204)
        self.assertEqual(headers['Access-Control-Allow-Origin'],
                         service_module.COLLECTOR_ORIGIN)
        # a public page reaching an address on this machine is a private
        # network request, which Chrome asks about separately
        self.assertEqual(headers['Access-Control-Allow-Private-Network'], 'true')

    def test_no_other_origin_gets_a_preflight(self):
        status, headers = self.options('/api/calendar', 'https://evil.example')
        self.assertEqual(status, 403)
        self.assertIsNone(headers.get('Access-Control-Allow-Origin'))

    def test_no_other_route_gets_a_preflight(self):
        """The opening is the calendar and not the port."""
        for route in ('/api/imports/run', '/api/backtest', '/api/imports/upload'):
            status, _headers = self.options(route, service_module.COLLECTOR_ORIGIN)
            self.assertEqual(status, 403, route)

    def test_the_token_stands_in_for_the_header(self):
        """
        A script on another site cannot send a custom header to this port, so
        the calendar route takes a token instead - one made when the service
        started, handed out only with the collector.
        """
        status, payload, headers = self.push(self.service.token)
        self.assertEqual(status, 200)
        self.assertEqual(payload['added'], 1)
        self.assertEqual(headers['Access-Control-Allow-Origin'],
                         service_module.COLLECTOR_ORIGIN)

    def test_a_wrong_token_is_refused(self):
        status, payload, _headers = self.push('nope')
        self.assertEqual(status, 403)
        self.assertIn('X-Parity-Deriva', payload['error'])

    def test_the_token_opens_nothing_else(self):
        status, payload, _headers = self.push(self.service.token,
                                              path='/api/imports/run')
        self.assertEqual(status, 403, payload)

    def test_the_page_s_own_upload_still_uses_the_header(self):
        status, payload = self.post('/api/calendar', self.CSV.encode('utf-8'))
        self.assertEqual(status, 200)
        self.assertEqual(payload['events'], 1)

    def test_a_reply_to_anybody_else_carries_no_allowance(self):
        _status, _body, headers = self.get('/api/stores')
        self.assertIsNone(headers.get('Access-Control-Allow-Origin'))


class HTTPTest(HTTPCase):
    """The routes."""

    def test_the_pages_are_served(self):
        # the simulation is the home page; one run has a page of its own
        for path, mark in (('/', b'<canvas id="sim-equity"'),
                           ('/sim', b'<canvas id="sim-equity"'),
                           ('/run', b'<canvas id="chart"'),
                           ('/settings', b'<table id="data-files"'),
                           ('/live', b'<table id="live-table"')):
            status, body, headers = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn('text/html', headers['Content-Type'])
            self.assertIn(mark, body, path)

    def test_the_assets_are_served(self):
        for path, kind in (('/static/app.js', 'javascript'),
                           ('/static/app.css', 'css')):
            status, _body, headers = self.get(path)
            self.assertEqual(status, 200)
            self.assertIn(kind, headers['Content-Type'])

    def test_the_stores_route(self):
        status, payload = self.json('/api/stores')
        self.assertEqual(status, 200)
        self.assertEqual(payload['instruments'][0]['instrument'], 'EUR_USD')

    def test_the_stores_route_carries_each_strategys_own_defaults(self):
        """The page selects these on picking a strategy; read off the class."""
        status, payload = self.json('/api/stores')
        self.assertEqual(payload['defaults']['AG01'],
                         {'instrument': 'EUR_USD', 'granularity': 'M5'})
        self.assertEqual(payload['defaults']['H401-PULLBACK-EMA']['granularity'], 'H4')
        self.assertEqual(payload['defaults']['AG01']['granularity'],
                         ledger_module.load_strategy('AG01')().granularity)

    def test_the_stores_route_names_every_strategy_it_will_run(self):
        """
        Was: the page listed AG01 and AG02 in its own markup, so the other
        strategies the ledger runs could not be chosen at all.
        Now: the list comes from the same place check() refuses against, which
        is service.strategies() - the ledger's handlers plus whatever viewer
        plugins strategy/plugins.py found installed, each bringing its own
        engine.
        """
        status, payload = self.json('/api/stores')
        self.assertEqual(sorted(payload['strategies']),
                         sorted(service_module.strategies()))
        for name in ledger_module.STRATEGIES:
            self.assertIn(name, payload['strategies'])

    def test_the_stores_route_carries_the_form_of_a_strategy_with_parameters(self):
        """
        The page builds its parameter controls from this, so a plugin that is
        installed and sends no form gets no controls and is then run with its
        defaults whatever the page shows. A handler strategy is here too when
        its constructor takes a number (service.handlerFields).
        """
        status, payload = self.json('/api/stores')
        self.assertEqual(sorted(payload['params']),
                         sorted(set(plugins_module.viewers())
                                | set(name for name in ledger_module.STRATEGIES
                                      if service_module.handlerFields(name))))
        for name, form in payload['params'].items():
            self.assertTrue(form, name)
            for field in form:
                self.assertIn('name', field)
                self.assertIn('value', field)

    def test_the_capital_field_is_filled_from_the_setting(self):
        """
        The page's capital box is not a figure typed into the markup: it is
        the setting the service would use anyway, so the two cannot drift.
        """
        status, payload = self.json('/api/stores')
        self.assertEqual(status, 200)
        self.assertEqual(payload['equity'], float(self.settings.EQUITY))

    def test_the_backtest_route_takes_a_risk(self):
        status, payload = self.backtest('instrument=EUR_USD&granularity=H1&risk=2')
        self.assertEqual(status, 200)
        self.assertAlmostEqual(payload['risk'], 0.02)

    def test_a_risk_over_the_whole_account_is_refused(self):
        status, payload = self.backtest('instrument=EUR_USD&granularity=H1&risk=150')
        self.assertEqual(status, 400)
        self.assertIn('risk', payload['error'])

    def test_the_backtest_route_takes_a_starting_balance(self):
        status, payload = self.backtest('instrument=EUR_USD&granularity=H1&balance=2500')
        self.assertEqual(status, 200)
        self.assertEqual(payload['balance'], 2500.0)

    def test_a_balance_of_nothing_is_refused(self):
        status, payload = self.backtest('instrument=EUR_USD&granularity=H1&balance=0')
        self.assertEqual(status, 400)
        self.assertIn('balance', payload['error'])

    def test_the_backtest_route(self):
        status, payload = self.backtest('instrument=EUR_USD&granularity=H1')
        self.assertEqual(status, 200)
        self.assertEqual(payload['instrument'], 'EUR_USD')
        self.assertIn('report', payload)

    def test_a_refusal_comes_back_as_json_with_the_reason(self):
        status, payload = self.backtest('instrument=NOPE&granularity=H1')
        self.assertEqual(status, 400)
        self.assertIn('no store', payload['error'])

    def test_missing_parameters_are_named(self):
        status, payload = self.backtest('granularity=H1')
        self.assertEqual(status, 400)
        self.assertIn('required', payload['error'])

    def test_a_bare_to_date_includes_that_whole_day(self):
        """
        Was: 'to=<date>' was read as midnight, so the last day's bars were
             dropped - twenty-one of them on an H1 store, a day of trades
             missing from a range that appears to include it.
        Now: a bare date ends the window at the end of that day, which is
             what somebody typing it means. A time given explicitly is taken
             as given.
        """
        last = [g for g in self.service.instruments()[0]['granularities']
                if g['granularity'] == 'H1'][0]['to']
        day = service_module.moment(last).strftime('%Y-%m-%d')
        status, payload = self.backtest('instrument=EUR_USD&granularity=H1&to=' + day)
        self.assertEqual(status, 200)
        self.assertEqual(payload['candles'][-1][0], last)

    def test_a_time_given_explicitly_is_not_widened(self):
        when = service_module.parseDate('2018-03-02T09:00:00', 'to', end=True)
        self.assertEqual(when, datetime.datetime(2018, 3, 2, 9, 0, 0))

    def test_a_bad_date_is_named(self):
        status, payload = self.backtest('instrument=EUR_USD&granularity=H1&from=yesterday')
        self.assertEqual(status, 400)
        self.assertIn('not a date', payload['error'])

    def test_a_reload_redraws_the_last_run_without_running_it(self):
        query = 'instrument=EUR_USD&granularity=H1'
        fields = {'instrument': 'EUR_USD', 'granularity': 'H1', 'cachedOnly': True}
        status, payload = self.post('/api/backtest', json.dumps(fields).encode())
        self.assertEqual((status, payload), (200, {'cached': False}))
        self.backtest(query)
        status, payload = self.post('/api/backtest', json.dumps(fields).encode())
        self.assertEqual(payload['instrument'], 'EUR_USD')

    def test_a_run_is_kept_and_can_be_reopened(self):
        self.backtest('instrument=EUR_USD&granularity=H1&risk=1')
        status, listing = self.json('/api/runs')
        self.assertEqual(status, 200)
        [run] = listing['runs']
        self.assertEqual((run['strategy'], run['granularity']), ('AG01', 'H1'))
        status, saved = self.json('/api/runs/' + run['id'])
        self.assertEqual(saved['fields'], {'instrument': 'EUR_USD',
                                           'granularity': 'H1', 'risk': '1'})
        self.assertEqual(saved['payload']['instrument'], 'EUR_USD')

    def test_a_reload_after_a_restart_reads_the_run_off_disk(self):
        self.backtest('instrument=EUR_USD&granularity=H1')
        self.service._cache.clear()            # what a restart leaves
        status, payload = self.post('/api/backtest', json.dumps(
            {'instrument': 'EUR_USD', 'granularity': 'H1',
             'cachedOnly': True}).encode())
        self.assertEqual(payload['instrument'], 'EUR_USD')

    def test_the_same_form_is_one_run_not_two(self):
        self.backtest('instrument=EUR_USD&granularity=H1')
        self.backtest('instrument=EUR_USD&granularity=H1')
        self.assertEqual(len(self.json('/api/runs')[1]['runs']), 1)

    def test_a_run_id_that_is_not_one_is_refused(self):
        status, _ = self.json('/api/runs/..%2F..%2Fetc')
        self.assertEqual(status, 400)
        status, _ = self.json('/api/runs/0123456789abcdef')
        self.assertEqual(status, 404)

    def test_a_backtest_is_not_a_get(self):
        """A GET is what a reload repeats; running is a POST."""
        status, _payload = self.json('/api/backtest?instrument=EUR_USD&granularity=H1')
        self.assertEqual(status, 404)

    def test_an_unknown_route_is_a_404(self):
        status, payload = self.json('/api/nothing')
        self.assertEqual(status, 404)
        self.assertIn('no route', payload['error'])

    def test_the_static_handler_does_not_leave_its_directory(self):
        """
        The one that is not a style question: the service reads a local
        warehouse and its own source sits next to the directory it serves.
        """
        for path in ('/static/../service.py', '/static/..%2Fservice.py',
                     '/static/%2e%2e/service.py'):
            status, _body, _headers = self.get(path)
            self.assertIn(status, (400, 404), path)

    def test_only_the_three_asset_types_are_served(self):
        status, _body, _headers = self.get('/static/__init__.py')
        self.assertEqual(status, 404)


class ImportTest(HTTPCase):
    """The data dialog: upload into IMPORT_DIR, list it, import it."""

    NAME = 'eurusd_m5_20180301_20180302-%s.csv'
    SETS = b'{"sets": ["eurusd_m5_20180301_20180302"]}'

    def body(self, side):
        # three M5 bars after the fixture's H1 range; ask is bid + 0.0002
        rows = ["timestamp,open,high,low,close,volume"]
        start = int(datetime.datetime(2018, 4, 2).timestamp()) * 1000
        for i in range(3):
            p = 1.2 + i / 1000.0 + (0.0002 if side == 'ASK' else 0)
            rows.append("%d,%s,%s,%s,%s,10" % (start + i * 300000, p, p, p, p))
        return ("\n".join(rows) + "\n").encode()

    def upload(self, name, data):
        return self.post('/api/imports/upload?name=' + name, data)

    def test_an_upload_lands_in_the_import_dir_and_is_listed(self):
        status, payload = self.upload(self.NAME % 'ASK', self.body('ASK'))
        self.assertEqual(status, 200, payload)
        status, listing = self.json('/api/imports')
        self.assertEqual(listing['directory'], os.path.join(self.tmpdir, 'import'))
        # one side is a set that cannot be imported yet, and says so
        self.assertEqual([(s['set'], s['granularity'], sorted(s['sides']), s['complete'])
                          for s in listing['sets']],
                         [('eurusd_m5_20180301_20180302', 'M5', ['ASK'], False)])
        self.upload(self.NAME % 'BID', self.body('BID'))
        status, listing = self.json('/api/imports')
        self.assertEqual(len(listing['sets']), 1)
        self.assertTrue(listing['sets'][0]['complete'])

    def test_a_name_the_import_cannot_read_is_refused_and_nothing_is_left(self):
        for name in ('prices.csv', '..%2F..%2Feurusd_m5_1_2-ASK.csv'):
            status, payload = self.upload(name, self.body('ASK'))
            self.assertEqual(status, 400, name)
        self.assertEqual(os.listdir(os.path.join(self.tmpdir, 'import')), [])

    def test_a_file_that_is_not_a_candle_export_is_refused(self):
        status, payload = self.upload(self.NAME % 'ASK', b'hello\n')
        self.assertEqual(status, 400)
        self.assertIn('timestamp,open', payload['error'])
        self.assertEqual(os.listdir(os.path.join(self.tmpdir, 'import')), [])

    def test_a_write_without_the_header_is_refused(self):
        """A page on another site cannot send it without a preflight."""
        status, _ = self.post('/api/imports/run', header=False)
        self.assertEqual(status, 403)
        status, _ = self.post('/api/imports/upload?name=' + self.NAME % 'ASK',
                              self.body('ASK'), header=False)
        self.assertEqual(status, 403)
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, 'import')))

    def test_the_import_merges_the_pair_into_the_store(self):
        for side in ('ASK', 'BID'):
            self.assertEqual(self.upload(self.NAME % side, self.body(side))[0], 200)
        status, payload = self.post('/api/imports/run', self.SETS)
        self.assertEqual(status, 200, payload)
        payload = self.finished()
        self.assertTrue(payload['ok'], payload['lines'])
        self.assertEqual(payload['done'], payload['total'])
        held = dict((g['granularity'], g)
                    for g in self.service.instruments()[0]['granularities'])
        self.assertEqual(held['M5']['bars'], 3)
        self.assertNotIn('derivedFrom', held['M5'])

        # the same set again is skipped, not read
        self.post('/api/imports/run', self.SETS)
        payload = self.finished()
        self.assertTrue(payload['ok'])
        self.assertIn('already imported', payload['lines'][0])

    def finished(self):
        """Poll the status route until the background import is done."""
        import time
        for _ in range(200):
            status, payload = self.json('/api/imports/status')
            self.assertEqual(status, 200)
            if not payload['running']:
                return payload
            time.sleep(0.05)
        self.fail("import still running")

    def test_only_a_complete_set_that_is_there_can_be_imported(self):
        self.upload(self.NAME % 'ASK', self.body('ASK'))
        for body, reason in ((b'{"sets": []}', 'at least one'),
                             (b'{"sets": ["../../etc/passwd"]}', 'no import set'),
                             (self.SETS, 'needs both'),
                             (b'nonsense', 'the body is')):
            status, payload = self.post('/api/imports/run', body)
            self.assertEqual(status, 400, body)
            self.assertIn(reason, payload['error'])
        self.assertFalse(self.service.importStatus()['running'])

    def test_an_import_with_no_directory_says_so(self):
        status, payload = self.post('/api/imports/run', self.SETS)
        self.assertEqual(status, 400)
        self.assertIn('does not exist', payload['error'])


if __name__ == '__main__':
    unittest.main()
