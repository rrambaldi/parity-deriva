import os
import copy
from decimal import Decimal, getcontext
import logging
import logging.config
import queue
import time
import threading
import traceback

class Engine(object):
	heartbeat = 0.5
	event_queue = None
	threads = []
	handlers = []
	
	def __init__(self):
		self.event_queue = queue.Queue()
		self.logger = logging.getLogger('qsforex.trading.trading')

	def put(self, event):
		self.event_queue.put(event)

	def add_handler(self, handler):
		handler.set_queue(self)
		self.handlers.append(handler)

	def quit(self):
		for h in self.handlers:
			f = getattr(h, 'quit', None)
			if callable(f):
				h.quit()

	def run(self):
		try:
			for h in self.handlers:
				f = getattr(h, 'stream_to_queue', None)
				if callable(f):
					self.logger.debug("Runnin thread for %s" % h)
					t = threading.Thread(target=h.stream_to_queue, args=[])
					self.threads.append(t)
					t.start()
					self.threads.append(t)

			self.logger.debug("Running main loop")
			while True:
				try:
					event = self.event_queue.get(True, self.heartbeat)
				except queue.Empty:
					event = None
					pass

				if event is not None:
#					self.logger.debug("GOT: %s" % str(event))
					for h in self.handlers:
						try:
							h.execute_event(event)
						except Exception as e:
							self.logger.critical("ERROR in execute_event: %s" % str(e))
							self.logger.critical(traceback.format_exc())
							os._exit(1)

				for t in self.threads:
					if not t.is_alive():
						os._exit(1)

		except KeyboardInterrupt:
			self.logger.error("GOT CTRL-C")
			self.quit()
			os._exit(1)



