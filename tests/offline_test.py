"""
Tests for the offline half of the parallel design: the events the simulator
publishes, the adapter that promotes them where there is no broker, and the
causally ordered driver a replay needs.
"""

import datetime
import unittest

from parity_deriva.backtest.driver import Collector, ReplayEngine
from parity_deriva.backtest.oanda import OANDABacktester
from parity_deriva.backtest.offline import SimulatedBroker
from parity_deriva.event.event import (CandleEvent, OrderEvent, StatusEvent,
                                       SimulatedFillEvent, SimulatedOrderEvent)
from parity_deriva.portfolio.moneymanager import MoneyManager
from parity_deriva.trading.handler import ExecutionHandler, StreamHandler
from parity_deriva.tests.helpers import T0, Recorder, TempDirCase, candle_dict


class OfflineCase(TempDirCase):

    def order(self, units=1, price=11700.0, sl=11690.0, tp=11720.0, key="K1"):
        return OrderEvent({"instrument": "DE30_EUR", "units": units,
                           "orderType": "STOP", "price": price,
                           "stopLoss": sl, "takeProfit": tp,
                           "signalNumber": key, "gtdTime": None})

    def candle(self, dt=T0, o=11700.0, h=11706.0, l=11694.0, c=11703.0,
               granularity="M1"):
        ev = CandleEvent(candle_dict(dt, o=o, h=h, l=l, c=c))
        ev.instrument, ev.granularity = "DE30_EUR", granularity
        return ev


class TestTheSimulatorReportsWhatItDid(OfflineCase):
    """
    Was: the simulator recorded fills in its own book and published nothing,
         so the money manager never learned a trade had opened - onTrade
         stayed False and the losing leg of a straddle was never cancelled.
         Live the loop closed only because OANDA's transaction stream
         reported the fills; offline it never closed at all.
    Now: it publishes its own events. Deliberately its own, not the broker's:
         in the parallel deployment both are on one bus and a component that
         could not tell them apart would act on a fill that never happened.
    """

    def setUp(self):
        super(TestTheSimulatorReportsWhatItDid, self).setUp()
        self.bt = OANDABacktester(setup=self.settings)
        self.sink = Recorder()
        self.bt.set_queue(self.sink)

    def test_a_new_order_is_acknowledged(self):
        self.bt.execute_event(self.order())
        self.assertEqual(self.sink.kinds(), ['SIMULATEDORDER'])
        ack = self.sink.events[0]
        self.assertEqual(ack.id, 1)
        self.assertEqual(ack.price, 11700.0)
        self.assertEqual(ack.signalNumber, "K1")
        self.assertEqual(ack.instrument, "DE30_EUR")

    def test_a_fill_is_reported_with_the_signal_key(self):
        self.bt.execute_event(self.order())
        self.sink.events = []
        self.bt.execute_event(self.candle(l=11699.0, h=11701.0))
        fills = self.sink.of('SIMULATEDFILL')
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].orderID, 1)
        self.assertEqual(fills[0].price, 11700.0)
        self.assertEqual(fills[0].signalNumber, "K1")
        self.assertEqual(fills[0].reason, 'ORDER_FILL')

    def test_an_opening_fill_is_not_marked_as_a_close(self):
        self.bt.execute_event(self.order())
        self.sink.events = []
        self.bt.execute_event(self.candle(l=11699.0, h=11701.0))
        self.assertFalse(self.sink.of('SIMULATEDFILL')[0].has_attr('tradesClosed'))

    def test_the_stop_and_target_are_acknowledged_too(self):
        self.bt.execute_event(self.order())
        self.sink.events = []
        self.bt.execute_event(self.candle(l=11699.0, h=11701.0))
        self.assertEqual(len(self.sink.of('SIMULATEDORDER')), 2)

    def test_a_close_carries_the_profit_and_the_balance(self):
        self.bt.execute_event(self.order())
        self.bt.execute_event(self.candle(l=11699.0, h=11701.0))
        self.sink.events = []
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11719.0, h=11721.0))
        closes = [e for e in self.sink.of('SIMULATEDFILL') if e.has_attr('tradesClosed')]
        self.assertEqual(len(closes), 1)
        self.assertAlmostEqual(closes[0].pl, 20.0)
        self.assertAlmostEqual(closes[0].accountBalance, 100020.0)
        self.assertEqual(closes[0].signalNumber, "K1")
        self.assertEqual(closes[0].reason, 'TAKE_PROFIT_ORDER')

    def test_a_losing_close_reports_a_negative_profit(self):
        self.bt.execute_event(self.order())
        self.bt.execute_event(self.candle(l=11699.0, h=11701.0))
        self.sink.events = []
        self.bt.execute_event(self.candle(T0 + datetime.timedelta(minutes=1),
                                          l=11689.0, h=11691.0))
        close = [e for e in self.sink.of('SIMULATEDFILL') if e.has_attr('tradesClosed')][0]
        self.assertAlmostEqual(close.pl, -10.0)
        self.assertEqual(close.reason, 'STOP_LOSS_ORDER')

    def test_the_events_are_not_the_brokers(self):
        """A handler listening for the real broker must not hear these."""
        self.bt.execute_event(self.order())
        self.bt.execute_event(self.candle(l=11699.0, h=11701.0))
        kinds = set(self.sink.kinds())
        self.assertNotIn('TRANSACTION', kinds)
        self.assertNotIn('CLIENTORDER', kinds)

    def test_the_money_manager_ignores_them_on_its_own(self):
        """
        The property that makes the parallel deployment safe: no change was
        needed in MoneyManager for it to disregard simulated events.
        """
        mm = MoneyManager(setup=self.settings)
        mm.signals, mm.processed = {}, []
        mm.onTrade = mm.orderIssued = False
        mm.set_queue(Recorder())
        mm.execute_event(SimulatedFillEvent({"orderID": 1, "price": 1.0}))
        mm.execute_event(SimulatedOrderEvent({"id": 1, "price": 1.0}))
        self.assertFalse(mm.onTrade)
        self.assertEqual(mm.signals, {})


class TestSimulatedBroker(OfflineCase):

    def setUp(self):
        super(TestSimulatedBroker, self).setUp()
        self.adapter = SimulatedBroker()
        self.sink = Recorder()
        self.adapter.set_queue(self.sink)

    def test_an_acknowledgement_becomes_a_client_order(self):
        self.adapter.execute_event(SimulatedOrderEvent(
            {"id": 7, "batchID": 7, "price": 1.5, "signalNumber": "K1"}))
        self.assertEqual(self.sink.kinds(), ['CLIENTORDER'])
        out = self.sink.events[0]
        self.assertEqual(out.id, 7)
        self.assertEqual(out.signalNumber, "K1")
        self.assertEqual(out.price, 1.5)

    def test_a_fill_becomes_an_order_fill_transaction(self):
        self.adapter.execute_event(SimulatedFillEvent(
            {"orderID": 7, "price": 1.5, "signalNumber": "K1"}))
        self.assertEqual(self.sink.kinds(), ['TRANSACTION'])
        self.assertEqual(self.sink.events[0].type, 'ORDER_FILL')
        self.assertEqual(self.sink.events[0].orderID, 7)

    def test_a_close_keeps_its_closing_marker(self):
        self.adapter.execute_event(SimulatedFillEvent(
            {"orderID": 7, "price": 1.5, "pl": 3.0,
             "tradesClosed": [{"tradeID": 7}]}))
        self.assertTrue(self.sink.events[0].has_attr('tradesClosed'))

    def test_everything_else_passes_through_untouched(self):
        for ev in (StatusEvent('DONE'), self.order(), self.candle()):
            self.adapter.execute_event(ev)
        self.assertEqual(self.sink.events, [])


class Feed(StreamHandler):
    """A source that pushes a fixed list of events."""

    def __init__(self, events):
        import logging
        self.logger = logging.getLogger('parity_deriva.trading.trading')
        self.events = events

    def stream_to_queue(self):
        for event in self.events:
            self.queue_event(event)


class Echo(ExecutionHandler):
    """Derives one event from the first it sees, to test causal ordering."""

    def __init__(self, trigger, derived):
        import logging
        self.logger = logging.getLogger('parity_deriva.trading.trading')
        self.trigger, self.derived = trigger, derived
        self.seen = []

    def execute_event(self, event):
        self.seen.append(str(event))
        if str(event) == self.trigger:
            self.queue_event(self.derived)


class TestReplayEngine(OfflineCase):
    """
    Was: replays were driven by trading.Engine, which runs the source in a
         thread and takes one event per pass off a shared queue. In a replay
         the source floods that queue, so every candle of the week could be
         dispatched before the first order derived from the first candle even
         existed - the simulator had nothing resting to fill and the run
         reported no trades, which reads as a strategy that never triggers
         rather than as a broken harness.
    Now: one source event, then everything it derives, then the next.
    """

    def test_derived_events_are_dispatched_before_the_next_source_event(self):
        engine = ReplayEngine()
        echo = Echo('CANDLE', StatusEvent('DERIVED'))
        engine.add_handler(echo)
        first = self.candle(T0)
        second = self.candle(T0 + datetime.timedelta(minutes=1))
        engine.run(Feed([first, second]))
        self.assertEqual(echo.seen, ['CANDLE', 'STATUS', 'CANDLE', 'STATUS'])

    def test_the_order_book_is_current_when_a_candle_is_examined(self):
        """The property the whole replay rests on, end to end."""
        engine = ReplayEngine()
        strategy = Echo('CANDLE', self.order(price=11700.0, sl=None, tp=None))
        sim = OANDABacktester(setup=self.settings)
        engine.add_handler(strategy)
        engine.add_handler(sim)
        engine.run(Feed([self.candle(T0),
                         self.candle(T0 + datetime.timedelta(minutes=1))]))
        # the order from the first candle is filled by the second
        self.assertEqual([o.state for o in sim.orders][0], 'FILLED')

    def test_it_reports_how_many_events_it_dispatched(self):
        engine = ReplayEngine()
        engine.add_handler(Echo('CANDLE', StatusEvent('X')))
        total = engine.run(Feed([self.candle(T0)]))
        self.assertEqual(total, 2)

    def test_quit_is_called_on_every_handler_at_the_end(self):
        engine = ReplayEngine()
        handler = Echo('NOTHING', None)
        calls = []
        handler.quit = lambda: calls.append(True)
        engine.add_handler(handler)
        engine.run(Feed([self.candle(T0)]))
        self.assertEqual(calls, [True])

    def test_handlers_feeding_each_other_are_stopped(self):
        engine = ReplayEngine()
        engine.max_derived = 20
        engine.add_handler(Echo('STATUS', StatusEvent('LOOP')))
        with self.assertRaises(RuntimeError):
            engine.run(Feed([StatusEvent('LOOP')]))

    def test_a_failing_handler_stops_the_run_rather_than_being_swallowed(self):
        class Boom(ExecutionHandler):
            def __init__(self):
                import logging
                self.logger = logging.getLogger('parity_deriva.trading.trading')
            def execute_event(self, event):
                raise ValueError("boom")
        engine = ReplayEngine()
        engine.add_handler(Boom())
        with self.assertRaises(ValueError):
            engine.run(Feed([self.candle(T0)]))

    def test_the_source_cannot_run_ahead(self):
        """The source is drained first, then replayed, so it cannot flood."""
        collected = Collector()
        feed = Feed([self.candle(T0)])
        feed.set_queue(collected)
        feed.stream_to_queue()
        self.assertEqual(len(collected.events), 1)


class TestTheLoopClosesOffline(OfflineCase):
    """
    The whole point of the three pieces above: with no broker present, a
    straddle now opens, one leg fills, the other is cancelled, and the trade
    is retired - none of which happened before.
    """

    def build(self):
        engine = ReplayEngine()
        mm = MoneyManager(setup=self.settings, units=1)
        mm.signals, mm.processed = {}, []
        mm.onTrade = mm.orderIssued = False
        sim = OANDABacktester(setup=self.settings)
        watcher = Recorder()

        class Tap(ExecutionHandler):
            def __init__(self):
                import logging
                self.logger = logging.getLogger('parity_deriva.trading.trading')
            def execute_event(self, event):
                watcher.put(event)

        for handler in (mm, sim, SimulatedBroker(), Tap()):
            engine.add_handler(handler)
        return engine, mm, sim, watcher

    def straddle(self, key="K1"):
        return [self.order(units=1, price=11705.0, sl=11695.0, tp=11715.0, key=key),
                self.order(units=-1, price=11695.0, sl=11705.0, tp=11685.0, key=key)]

    def test_a_straddle_opens_and_the_losing_leg_is_cancelled(self):
        engine, mm, sim, watcher = self.build()
        from parity_deriva.event.event import SignalEvent
        signals = [SignalEvent(o.to_dict()) for o in self.straddle()]
        events = signals + [
            self.candle(T0 + datetime.timedelta(minutes=1), l=11704.0, h=11706.0)]
        engine.run(Feed(events))

        self.assertTrue(mm.onTrade)
        self.assertIn('ORDERCANCEL', watcher.kinds())
        states = [o.orderStatus for o in mm.signals["K1"]]
        self.assertIn('FILLED', states)
        self.assertIn('CANCELED', states)

    def test_the_trade_is_retired_when_it_closes(self):
        engine, mm, sim, watcher = self.build()
        from parity_deriva.event.event import SignalEvent
        signals = [SignalEvent(o.to_dict()) for o in self.straddle()]
        events = signals + [
            self.candle(T0 + datetime.timedelta(minutes=1), l=11704.0, h=11706.0),
            self.candle(T0 + datetime.timedelta(minutes=2), l=11714.0, h=11716.0)]
        engine.run(Feed(events))

        self.assertFalse(mm.onTrade)
        self.assertEqual(len(mm.processed), 1)
        self.assertEqual(mm.signals, {})
        closed = [o for o in mm.processed[0] if o.orderStatus == 'CLOSED']
        self.assertEqual(len(closed), 1)
        self.assertGreater(closed[0].closeEvent.pl, 0)

    def test_the_signal_key_survives_the_whole_round_trip(self):
        """Order -> simulator -> adapter -> money manager, joined on the key."""
        engine, mm, sim, watcher = self.build()
        from parity_deriva.event.event import SignalEvent
        signals = [SignalEvent(o.to_dict()) for o in self.straddle(key="AG01:X:H1:1")]
        engine.run(Feed(signals + [
            self.candle(T0 + datetime.timedelta(minutes=1), l=11704.0, h=11706.0)]))
        self.assertEqual(list(mm.signals), ["AG01:X:H1:1"])
        for fill in watcher.of('TRANSACTION'):
            self.assertEqual(fill.signalNumber, "AG01:X:H1:1")


if __name__ == "__main__":
    unittest.main()
