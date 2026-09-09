
import datetime
import logging
import json
import time
import os
import sys
from parity_deriva.etc import settings

from parity_deriva.event.event import StatusEvent
from parity_deriva.event.event import Event
from parity_deriva.trading.handler import StreamHandler


class EventReplay(StreamHandler):

	def __init__( self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')

		self._set(args, 'setup', settings)
		self._set(args, 'logname', 'EventSaver-20170131.log')

		self.openmode = 'r'
		self.f = open(self.getFileName(), self.openmode)

	def getFileName(self):
		return os.path.join(self.setup.LOG_DIR, self.logname)

	def stream_to_queue(self):
		with self.f as lines:
			for e_str in lines:
				ev = Event(json.loads(e_str))
				self.queue_event(ev)

			self.f.close()
			ev = StatusEvent("DONE")
			self.queue_event(ev)
			self.logger.debug("QUIT")


	def quit(self):
		self.logger.debug("QUIT")
		self.f.close()
