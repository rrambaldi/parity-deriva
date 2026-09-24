
from abc import ABCMeta, abstractmethod

class MetaHandler(metaclass=ABCMeta):

	@abstractmethod
	def set_queue(self, event_queue):
		raise NotImplementedError("Should implement set_queue()")

	@abstractmethod
	def queue_event(self, event):
		raise NotImplementedError("Should implement queue_event()")

	@abstractmethod
	def execute_event(self, event):
		"""
		Send the order to the brokerage.
		"""
		raise NotImplementedError("Should implement execute_event()")

class ExecutionHandler(MetaHandler):
	event_queue = None
	"""
	Provides an abstract base class to handle all execution in the
	backtesting and live trading system.
	"""

	def _set(self, args, key, default=None):
		if key in args:
			self.logger.debug("Setting %s: %s" % (key, args[key]))
			setattr(self, key, args[key])
			return True

		if default is not None:
			self.logger.debug("Default %s: %s" % (key, default))
			setattr(self, key, default)

		return False

	def otherStream(self, event):
		"""
		Is this candle from a stream other than the one this handler reads?

		A backtest can put two granularities of one instrument on the bus -
		the daily bars a strategy signals on and the minute bars its orders
		are filled against (backtest/shadow.py) - and every handler that
		counts bars has to say which of the two it is counting. The simulator
		and portfolio/trailer.py already did, each with its own copy of this
		test; a strategy did not, and would have taken every minute of the
		day for a day of its own.

		An event with no granularity at all passes. Every live source sets
		one, so that case is a hand-made candle in a test, and dropping those
		would be this guard deciding what the tests are allowed to feed.
		"""
		granularity = getattr(event, 'granularity', None)
		return granularity is not None \
			and getattr(self, 'granularity', None) not in (None, granularity)

	def set_queue(self, event_queue):
#		self.logger.debug("Set event queue: %s" % self.event_queue)
		if event_queue is not None:
			self.event_queue = event_queue

	def queue_event(self, event):
#		self.logger.debug("Queue event %s" % str(self.event_queue))
		if self.event_queue is not None:
			self.event_queue.put(event)



class StreamHandler(ExecutionHandler):

	@abstractmethod
	def stream_to_queue(self):
		"""
		"""
		raise NotImplementedError("Should implement stream_to_queue()")

	def execute_event(self, event):
		pass

