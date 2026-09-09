"""
Characterisation tests for parity_deriva.trading (handler contract + Engine).

The Engine is where a live execution handler and the local simulator run side
by side on the same event stream, so its dispatch order, its shared state and
the way it dies are all part of the contract.
"""

import logging
import os
import subprocess
import sys
import textwrap
import unittest
from unittest import mock

from parity_deriva.trading.engine import Engine
from parity_deriva.trading.handler import MetaHandler, ExecutionHandler, StreamHandler
from parity_deriva.tests.helpers import Recorder


class _Args(ExecutionHandler):
    """Bare handler used to exercise _set()."""

    def __init__(self, args):
        self.logger = logging.getLogger('parity_deriva.trading.trading')
        self.taken = {}

    def execute_event(self, event):
        pass


class TestSetHelper(unittest.TestCase):
    """
    _set(args, key, default) is the constructor idiom of every component:
    it returns True when the caller supplied the key (which several classes
    use to pick live vs historic mode), False when it fell back to the default.
    """

    def setUp(self):
        self.h = _Args({})

    def test_supplied_value_is_set_and_returns_true(self):
        self.assertTrue(self.h._set({"pairs": ["EUR_USD"]}, 'pairs', ['X']))
        self.assertEqual(self.h.pairs, ["EUR_USD"])

    def test_default_is_set_and_returns_false(self):
        self.assertFalse(self.h._set({}, 'pairs', ['EUR_USD']))
        self.assertEqual(self.h.pairs, ["EUR_USD"])

    def test_return_value_is_how_live_mode_is_detected(self):
        """ForexCandles/BulkSaver switch to historic mode on a supplied dtfrom."""
        self.assertFalse(self.h._set({}, 'dtfrom', 'default'))
        self.assertTrue(self.h._set({"dtfrom": 'given'}, 'dtfrom'))

    def test_a_none_default_sets_no_attribute_at_all(self):
        """
        The default is only applied when it is not None, so _set(args, k) with
        no default leaves the attribute missing rather than None. Callers that
        then read self.<k> get AttributeError.
        """
        self.assertFalse(self.h._set({}, 'pairs'))
        self.assertFalse(hasattr(self.h, 'pairs'))

    def test_falsy_but_not_none_defaults_are_applied(self):
        self.assertFalse(self.h._set({}, 'sleep', 0))
        self.assertEqual(self.h.sleep, 0)
        self.assertFalse(self.h._set({}, 'live', False))
        self.assertIs(self.h.live, False)

    def test_an_explicit_none_in_args_is_still_set(self):
        """Presence in args wins: the None check only guards the default."""
        self.assertTrue(self.h._set({"gtdTime": None}, 'gtdTime', "23:59:59"))
        self.assertIsNone(self.h.gtdTime)


class TestHandlerAbstractContract(unittest.TestCase):
    """
    MetaHandler is an ABC (it was under Python 2 as well, via __metaclass__).
    These tests pin which methods a component must supply at each level.
    """

    def test_metahandler_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            MetaHandler()

    def test_execution_handler_still_needs_execute_event(self):
        self.assertEqual(ExecutionHandler.__abstractmethods__, frozenset({'execute_event'}))
        with self.assertRaises(TypeError):
            ExecutionHandler()

    def test_stream_handler_needs_stream_to_queue(self):
        """StreamHandler supplies execute_event but demands stream_to_queue."""
        self.assertEqual(StreamHandler.__abstractmethods__,
                         frozenset({'stream_to_queue'}))
        with self.assertRaises(TypeError):
            StreamHandler()

    def test_a_complete_execution_handler_instantiates(self):
        self.assertIsInstance(_Args({}), ExecutionHandler)

    def test_a_complete_stream_handler_instantiates(self):
        class Feed(StreamHandler):
            def stream_to_queue(self):
                pass
        self.assertIsInstance(Feed(), StreamHandler)

    def test_stream_handler_execute_event_is_a_no_op(self):
        class Feed(StreamHandler):
            def stream_to_queue(self):
                pass
        self.assertIsNone(Feed().execute_event(object()))


class TestQueuePlumbing(unittest.TestCase):

    def test_set_queue_then_queue_event(self):
        h = _Args({})
        sink = Recorder()
        h.set_queue(sink)
        h.queue_event('E')
        self.assertEqual(sink.events, ['E'])

    def test_queue_event_without_a_queue_is_silently_dropped(self):
        """A handler used outside an Engine emits nothing and does not raise."""
        h = _Args({})
        h.event_queue = None
        self.assertIsNone(h.queue_event('E'))

    def test_set_queue_ignores_none(self):
        h = _Args({})
        sink = Recorder()
        h.set_queue(sink)
        h.set_queue(None)
        h.queue_event('E')
        self.assertEqual(sink.events, ['E'])


class TestEngine(unittest.TestCase):

    def setUp(self):
        # handlers/threads are class attributes, so isolate each test
        Engine.handlers = []
        Engine.threads = []
        self.addCleanup(setattr, Engine, 'handlers', [])
        self.addCleanup(setattr, Engine, 'threads', [])

    def test_add_handler_wires_the_engine_as_the_queue(self):
        e = Engine()
        h = _Args({})
        e.add_handler(h)
        self.assertIs(h.event_queue, e)
        self.assertIn(h, e.handlers)

    def test_a_handler_can_queue_back_into_the_engine(self):
        e = Engine()
        h = _Args({})
        e.add_handler(h)
        h.queue_event('E')
        self.assertEqual(e.event_queue.get_nowait(), 'E')

    def test_default_heartbeat(self):
        self.assertEqual(Engine().heartbeat, 0.5)

    def test_quit_is_forwarded_only_to_handlers_that_define_it(self):
        e = Engine()
        quitter = _Args({})
        quitter.quit = mock.Mock()
        plain = _Args({})
        e.add_handler(quitter)
        e.add_handler(plain)
        e.quit()
        quitter.quit.assert_called_once_with()

    def test_handlers_and_threads_are_shared_class_attributes(self):
        """
        `handlers = []` and `threads = []` live on the class, so two Engine
        instances in one process share them. Only ever one Engine is built per
        script today, but a test harness that builds several will leak.
        """
        first = Engine()
        first.add_handler(_Args({}))
        second = Engine()
        self.assertEqual(len(second.handlers), 1)
        self.assertIs(first.handlers, second.handlers)

    def test_each_engine_gets_its_own_queue(self):
        self.assertIsNot(Engine().event_queue, Engine().event_queue)


# The Engine main loop is an infinite loop that calls os._exit, so it can only
# be exercised out of process.
# The Engine main loop is an infinite loop that ends in os._exit, so it can
# only be exercised out of process.
ENGINE_SCRIPT = textwrap.dedent('''
    import logging, sys, threading
    logging.disable(logging.CRITICAL)
    from parity_deriva.trading.handler import StreamHandler, ExecutionHandler
    from parity_deriva.trading.engine import Engine
    from parity_deriva.event.event import StatusEvent, TickEvent

    OUT, COUNT = sys.argv[1], int(sys.argv[2])

    # the sink sets this once it has dispatched everything the feeder queued,
    # which lets the feeder return at a known point instead of racing
    all_dispatched = threading.Event()

    class Feed(StreamHandler):
        def __init__(self):
            self.logger = logging.getLogger('parity_deriva.trading.trading')
        def stream_to_queue(self):
            for i in range(COUNT):
                self.queue_event(TickEvent({"instrument": "X", "units": i}))
            self.queue_event(StatusEvent("DONE"))
            all_dispatched.wait(20)

    class Sink(ExecutionHandler):
        seen = 0
        def __init__(self):
            self.logger = logging.getLogger('parity_deriva.trading.trading')
            self.fh = open(OUT, "w")
        def execute_event(self, event):
            self.fh.write("%s\\n" % str(event))
            self.fh.flush()
            Sink.seen += 1
            if Sink.seen >= COUNT + 1:
                all_dispatched.set()

    e = Engine()
    e.heartbeat = 0.02
    e.add_handler(Sink())
    e.add_handler(Feed())
    e.run()
''')


class TestEngineRunLoop(unittest.TestCase):
    """Out-of-process tests for Engine.run()."""

    def _run(self, count, tmpname):
        import tempfile
        out = os.path.join(tempfile.mkdtemp(prefix="parity_deriva-engine-"), tmpname)
        script = os.path.join(os.path.dirname(out), "drive.py")
        with open(script, "w") as fh:
            fh.write(ENGINE_SCRIPT)
        env = dict(os.environ)
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        env['PYTHONPATH'] = root + os.pathsep + env.get('PYTHONPATH', '')
        proc = subprocess.run([sys.executable, script, out, str(count)],
                              env=env, capture_output=True, timeout=60)
        with open(out) as fh:
            seen = fh.read().split()
        return proc.returncode, seen

    def test_every_queued_event_reaches_the_handlers_in_order(self):
        code, seen = self._run(4, "dispatched.txt")
        self.assertEqual(seen, ['TICK', 'TICK', 'TICK', 'TICK', 'STATUS'])
        self.assertEqual(code, 1)

    def test_the_engine_dispatches_one_event_per_liveness_check(self):
        """
        The loop body handles exactly one event and then walks self.threads,
        so a larger batch still arrives whole as long as the producer is alive.
        """
        code, seen = self._run(50, "batch.txt")
        self.assertEqual(len(seen), 51)
        self.assertEqual(code, 1)

    def test_a_finished_stream_thread_terminates_the_whole_process(self):
        """
        run() polls `for t in self.threads: if not t.is_alive(): os._exit(1)`
        after each dispatched event, so a producer that simply returns takes
        the process down with status 1 - there is no graceful drain and no
        shutdown hook. Whether anything still sitting in the queue gets
        delivered first is a genuine race between the dispatch loop and thread
        teardown, so a supervisor must treat exit 1 as "the feed ended" rather
        than as a crash, and must not rely on a closing DONE event arriving.
        """
        code, seen = self._run(4, "shutdown.txt")
        self.assertEqual(code, 1)
        self.assertGreaterEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
