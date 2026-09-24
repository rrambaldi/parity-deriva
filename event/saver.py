
import datetime
import logging
import json
import time
import os
import sys
from parity_deriva.etc import settings

from parity_deriva.event.event import StatusEvent
from parity_deriva.trading.handler import ExecutionHandler


class EventSaver(ExecutionHandler):

	def __init__( self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self.started = datetime.date.today()
		today=self.started.strftime("%Y%m%d")

		self._set(args, 'setup', settings)
		self._set(args, 'logname', 'EventSaver')
		self._set(args, 'overwrite', False)

		self.openmode = 'a'
		if self.overwrite==True:
			self.openmode = 'w'
		self.f = open(self.getFileName(), self.openmode)

	def getFileName(self):
		name = self.logname+"-"+self.started.strftime("%Y%m%d")+'.log'
		return os.path.join(self.setup.LOG_DIR, name)

	def execute_event(self, event):
		try:
			now = datetime.date.today()
			if now != self.started:
				# Was: the file was closed and reopened at midnight - under
				#      the same name, because getFileName() reads self.started
				#      and nothing ever moved it on. Every event after the
				#      first midnight still went into day one's file, and with
				#      overwrite=True the reopen truncated it as well.
				# Now: the date rolls before the reopen, so the new file is
				#      today's.
				self.started = now
				self.f.close()
				self.f = open(self.getFileName(), self.openmode)
				
			d = event.to_json(True) + "\n"
			self.f.write(d)
			self.f.flush()
		except Exception as e:
			self.logger.error("ERROR SAVING:"+str(e))
			self.f.close()
			self.f = open(self.getFileName(), 'a')


	def quit(self):
		self.logger.debug("QUIT")
		self.f.close()
