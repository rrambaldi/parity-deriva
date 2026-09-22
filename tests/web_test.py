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
import urllib.error
import urllib.request

from parity_deriva.backtest import ledger as ledger_module
from parity_deriva.backtest.ledger import Ledger, LedgerError, moneyManager, run
from parity_deriva.backtest.oanda import OANDABacktester
from parity_deriva.backtest.offline import SimulatedBroker
from parity_deriva.backtest.driver import ReplayEngine
from parity_deriva.event.event import (CandleEvent, ClientOrderEvent,
                                       OrderCancelEvent, OrderEvent,
                                       SignalEvent, StopModifyEvent,
                                       TransactionEvent)
from parity_deriva.performance import report as report_module
from parity_deriva.trading.handler import StreamHandler
from parity_deriva.strategy import plugins as plugins_module
from parity_deriva.web import service as service_module
from parity_deriva.web.service import (Service, ServiceError, millis,
                                        parseAmount, parsePercent)
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
        self.assertEqual([g['granularity'] for g in rows[0]['granularities']],
                         ['H1'])
        self.assertEqual(rows[0]['granularities'][0]['bars'], 200)

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

    def test_the_shape_is_what_the_page_reads(self):
        for key in ('instrument', 'granularity', 'strategy', 'from', 'to',
                    'candles', 'trades', 'counts', 'report', 'elapsed',
                    'balance', 'risk'):
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


class HTTPTest(StoreCase):
    """The routes, over a loopback socket, because routing is what is tested."""

    def setUp(self):
        super(HTTPTest, self).setUp()
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

    def test_the_page_is_served(self):
        status, body, headers = self.get('/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn(b'<canvas id="chart"', body)

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
        defaults whatever the page shows. An empty mapping is correct on a
        checkout with no plugins installed.
        """
        status, payload = self.json('/api/stores')
        self.assertEqual(sorted(payload['params']),
                         sorted(plugins_module.viewers()))
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
        status, payload = self.json(
            '/api/backtest?instrument=EUR_USD&granularity=H1&risk=2')
        self.assertEqual(status, 200)
        self.assertAlmostEqual(payload['risk'], 0.02)

    def test_a_risk_over_the_whole_account_is_refused(self):
        status, payload = self.json(
            '/api/backtest?instrument=EUR_USD&granularity=H1&risk=150')
        self.assertEqual(status, 400)
        self.assertIn('risk', payload['error'])

    def test_the_backtest_route_takes_a_starting_balance(self):
        status, payload = self.json(
            '/api/backtest?instrument=EUR_USD&granularity=H1&balance=2500')
        self.assertEqual(status, 200)
        self.assertEqual(payload['balance'], 2500.0)

    def test_a_balance_of_nothing_is_refused(self):
        status, payload = self.json(
            '/api/backtest?instrument=EUR_USD&granularity=H1&balance=0')
        self.assertEqual(status, 400)
        self.assertIn('balance', payload['error'])

    def test_the_backtest_route(self):
        status, payload = self.json('/api/backtest?instrument=EUR_USD&granularity=H1')
        self.assertEqual(status, 200)
        self.assertEqual(payload['instrument'], 'EUR_USD')
        self.assertIn('report', payload)

    def test_a_refusal_comes_back_as_json_with_the_reason(self):
        status, payload = self.json('/api/backtest?instrument=NOPE&granularity=H1')
        self.assertEqual(status, 400)
        self.assertIn('no store', payload['error'])

    def test_missing_parameters_are_named(self):
        status, payload = self.json('/api/backtest?granularity=H1')
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
        last = self.service.instruments()[0]['granularities'][0]['to']
        day = service_module.moment(last).strftime('%Y-%m-%d')
        status, payload = self.json(
            '/api/backtest?instrument=EUR_USD&granularity=H1&to=' + day)
        self.assertEqual(status, 200)
        self.assertEqual(payload['candles'][-1][0], last)

    def test_a_time_given_explicitly_is_not_widened(self):
        when = service_module.parseDate('2018-03-02T09:00:00', 'to', end=True)
        self.assertEqual(when, datetime.datetime(2018, 3, 2, 9, 0, 0))

    def test_a_bad_date_is_named(self):
        status, payload = self.json(
            '/api/backtest?instrument=EUR_USD&granularity=H1&from=yesterday')
        self.assertEqual(status, 400)
        self.assertIn('not a date', payload['error'])

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


if __name__ == '__main__':
    unittest.main()
