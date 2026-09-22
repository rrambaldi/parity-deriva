"""
Tests for parity_deriva.scripts.live: what the wiring registers, and in what
order.

Nothing here touches the network. The provider is a stub whose handlers are
labels and the engine is a list, because the question being asked is which
components end up on the bus for a given strategy. A missing one does not
fail loudly: the stack runs, quietly, without the rule that component was
there to apply - which for a strategy whose whole exit is a moving stop means
a trade that never leaves.
"""

import contextlib
import io
import logging
import unittest
from unittest import mock

from parity_deriva.portfolio.trailer import Trailer
from parity_deriva.scripts import live
from parity_deriva.strategy import plugins
from parity_deriva.trading.providers import Capabilities


#: A strategy that exits on a stop the runner has to move. Registered for the
#: length of a test rather than named from live.STRATEGIES: the ones that
#: really exit this way are not all shipped here (strategy/plugins.py), and a
#: test that named one would pass or fail depending on the checkout.
MOVING_STOP = ('parity_deriva.tests.helpers', 'MovingStopStrategy',
               ('bid_ask_candles', 'stop_modify'), 'pairs')


class FakeEngine(object):

    def __init__(self):
        self.handlers = []
        self.heartbeat = None
        self.ran = False

    def add_handler(self, handler):
        self.handlers.append(handler)

    def run(self):
        self.ran = True


class FakeProvider(object):

    def __init__(self, name='oanda', **caps):
        self.name = name
        self.capabilities = Capabilities(**caps)

    def execution(self, **args):
        return _label('execution')

    def simulator(self, **args):
        return _label('simulator')

    def candles(self, **args):
        return _label('candles')

    def transactions(self, **args):
        return _label('transactions')


def _label(what):
    o = mock.MagicMock()
    o._label = what
    return o


OANDA_LIKE = dict(bid_ask_candles=True, stop_modify=True)


class LiveCase(unittest.TestCase):

    def setUp(self):
        self.engine = FakeEngine()
        patches = [
            mock.patch.object(live, 'Engine', lambda: self.engine),
            mock.patch.object(live, 'getLogger', lambda: logging.getLogger('test')),
            mock.patch.object(live, 'EventSaver', lambda *a, **k: _label('saver')),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        known = dict(live.STRATEGIES, MOVING_STOP=MOVING_STOP)
        p = mock.patch.object(live, 'STRATEGIES', known)
        p.start()
        self.addCleanup(p.stop)

    def run_main(self, provider, *argv):
        """main() prints its plan; the suite does not need to read it."""
        self.out = io.StringIO()
        with mock.patch.object(live.providers, 'get_provider',
                               lambda *a, **k: provider):
            with contextlib.redirect_stdout(self.out):
                return live.main(list(argv))

    def kinds(self):
        return [getattr(h, '_label', type(h).__name__)
                for h in self.engine.handlers]


class TestTheTrailerIsRegistered(LiveCase):

    def test_a_strategy_that_needs_a_moving_stop_gets_the_trailer(self):
        """
        The ladder is not in the order - an order states its stop once and
        never speaks again - so without this component the stop of such a
        trade would sit where it was placed for the life of the trade.
        """
        self.run_main(FakeProvider(**OANDA_LIKE), '--strategy', 'MOVING_STOP',
                      '--instrument', 'EUR_USD', '--granularity', 'D')
        self.assertIn('Trailer', self.kinds())

    def test_it_sits_between_the_execution_handler_and_the_shadow(self):
        """
        Same order as backtest/ledger.py uses offline: a stop moved on this
        bar applies from the next. The two have to agree, or the comparison
        the shadow exists for compares two different rules.
        """
        self.run_main(FakeProvider(**OANDA_LIKE), '--strategy', 'MOVING_STOP',
                      '--instrument', 'EUR_USD', '--granularity', 'D')
        kinds = self.kinds()
        self.assertLess(kinds.index('execution'), kinds.index('Trailer'))
        self.assertLess(kinds.index('Trailer'), kinds.index('simulator'))

    def test_it_reads_the_granularity_the_strategy_signals_on(self):
        """With two streams of one instrument on the bus, nothing in a candle
        says which one the ladder should be walked from."""
        self.run_main(FakeProvider(**OANDA_LIKE), '--strategy', 'MOVING_STOP',
                      '--instrument', 'EUR_USD', '--granularity', 'D')
        trailer = [h for h in self.engine.handlers if isinstance(h, Trailer)][0]
        self.assertEqual(trailer.granularity, 'D')

    def test_a_strategy_with_a_fixed_stop_does_not_get_one(self):
        """AG01's bracket says where it exits when it is placed."""
        self.run_main(FakeProvider(bid_ask_candles=True), '--strategy', 'AG01',
                      '--instrument', 'EUR_USD', '--granularity', 'H1')
        self.assertNotIn('Trailer', self.kinds())


class TestTheCapabilityGate(LiveCase):

    def test_a_provider_that_cannot_move_a_stop_registers_nothing(self):
        rc = self.run_main(FakeProvider(name='ig', bid_ask_candles=True),
                           '--strategy', 'MOVING_STOP', '--instrument', 'EUR_USD')
        self.assertEqual(rc, 2)
        self.assertEqual(self.engine.handlers, [])

    def test_oanda_starts_the_same_strategy(self):
        rc = self.run_main(FakeProvider(**OANDA_LIKE), '--strategy', 'MOVING_STOP',
                           '--instrument', 'EUR_USD', '--granularity', 'D')
        self.assertEqual(rc, 0)
        self.assertTrue(self.engine.ran)

    def test_whatever_a_plugin_registered_can_actually_be_loaded(self):
        """
        A strategy from strategy/plugins.py is named in one file and defined
        in another, so a rename breaks it in a way nothing else notices: the
        runner offers it and fails only when somebody selects it. Nothing is
        installed on a plain checkout and this passes trivially, which is the
        correct answer there.
        """
        for name in plugins.live():
            self.assertTrue(live.load_strategy(name)[0], name)


if __name__ == "__main__":
    unittest.main()
