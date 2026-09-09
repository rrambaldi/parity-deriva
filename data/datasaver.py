from decimal import Decimal, getcontext, ROUND_HALF_DOWN
import datetime
import logging
import json
import time
import os
import sys
from qsforex.etc import settings

import requests
import pandas as pd

from qsforex.event.event import StatusEvent
from qsforex.event.event import CandleEvent
from qsforex.trading.handler import ExecutionHandler


class CandleSaver(ExecutionHandler):
	pairs = None
	store = {}

	def __init__( self, **args):

		self.logger = logging.getLogger('qsforex.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs')

		if self.pairs is not None:
			for p in self.pairs:
				store_name  = "%s.hd5" % p
				self.store[p]=pd.HDFStore(os.path.join(self.setup.DATA_DIR,store_name))


	def execute_event(self, event):
		if str(event)!='CANDLE':
			return

		i = event.instrument
		if i in self.pairs:

			t = event.time.strftime('%Y-%m-%d %H:%M:%S')
			g = event.granularity
			try:
				if t in self.store[i][g].index:
					self.store[i][g].loc[t] = event.to_dict()
					return 
			except Exception as e:
				pass

			self.store[i].append( g, event.dataframe())



