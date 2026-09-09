
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

