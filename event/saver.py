
import datetime
import logging
import json
import time
import os
import sys
from qsforex.etc import settings

from qsforex.event.event import StatusEvent
from qsforex.trading.handler import ExecutionHandler


class EventSaver(ExecutionHandler):

	def __init__( self, **args):
		self.logger = logging.getLogger('qsforex.trading.trading')
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
