
from __future__ import print_function

import datetime
import logging
import json
import time
import os
import sys
from qsforex.etc import settings

from qsforex.event.event import StatusEvent
from qsforex.event.event import Event
from qsforex.trading.handler import StreamHandler


class EventReplay(StreamHandler):

	def __init__( self, **args):
		self.logger = logging.getLogger('qsforex.trading.trading')

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
