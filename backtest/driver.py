
import collections
import logging
import traceback


class Cancelled(Exception):
	"""
	A replay stopped because whoever asked for it asked it to stop.

	Not a fault, so the engine re-raises it without the critical log a broken
	handler earns: the run is over because somebody pressed a button, and the
	journal filling with a traceback for that teaches whoever reads it to
	ignore tracebacks.

	Raised from a handler - see ledger.Progress, whose report can say no - and
	caught by whoever started the run. Nothing partial is returned: half a
	backtest presented as a backtest is worse than no backtest.
	"""


class Collector(object):
	"""Queue stand-in that just keeps what a source produces."""

	def __init__(self):
		self.events = []

	def put(self, event):
		self.events.append(event)


class ReplayEngine(object):
	"""
	A synchronous, causally ordered engine for offline replay.

	trading.Engine runs a thread per source and a loop that takes one event
	off a shared queue per pass. Live that is right: candles arrive minutes
	apart, so an order derived from one is resting long before the next
	arrives. In a replay the source floods the queue as fast as it can read
	memory, and the ordering stops being causal - every candle of the week can
	be dispatched before the first order derived from the first candle even
	exists. The simulator then has nothing resting to fill and the run reports
	no trades at all, which looks like a strategy that never triggers rather
	than like a broken harness.

	This driver dispatches one source event and then everything that event
	derives, to quiescence, before touching the next one. The result is what a
	replay is supposed to mean: at the moment candle N is examined, exactly
	the orders that candles 1..N-1 produced are on the book.

	    engine = ReplayEngine()
	    for handler in (strategy, money_manager, simulator, SimulatedBroker()):
	        engine.add_handler(handler)
	    engine.run(ForexCandles(pairs=['EUR_USD'], granularity='H1', ...))
	"""

	#: guard against a handler pair that feeds each other forever
	max_derived = 10000

	def __init__(self):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self.handlers = []
		self.pending = collections.deque()
		self.dispatched = 0

	def add_handler(self, handler):
		handler.set_queue(self)
		self.handlers.append(handler)

	def put(self, event):
		self.pending.append(event)

	def quit(self):
		for handler in self.handlers:
			f = getattr(handler, 'quit', None)
			if callable(f):
				handler.quit()

	def feed(self, event):
		"""Dispatch one event and everything it derives, in order."""
		self.put(event)
		derived = 0
		while self.pending:
			current = self.pending.popleft()
			self.dispatched += 1
			derived += 1
			if derived > self.max_derived:
				raise RuntimeError(
					"more than %d events derived from one source event; "
					"handlers are feeding each other" % self.max_derived)
			for handler in self.handlers:
				try:
					handler.execute_event(current)
				except Cancelled:
					raise
				except Exception as exc:
					self.logger.critical("ERROR in execute_event: %s" % str(exc))
					self.logger.critical(traceback.format_exc())
					raise

	def run(self, source):
		"""
		Drive a stream handler offline.

		The source is drained first and replayed afterwards, rather than being
		left to push straight into the bus, precisely so that it cannot run
		ahead of the handlers.
		"""
		collected = Collector()
		source.set_queue(collected)
		source.stream_to_queue()
		self.logger.info("replaying %d events" % len(collected.events))
		for event in collected.events:
			self.feed(event)
		self.quit()
		return self.dispatched
