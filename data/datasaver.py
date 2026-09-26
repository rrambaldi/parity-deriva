from decimal import Decimal, getcontext, ROUND_HALF_DOWN
import datetime
import logging
import json
import time
import os
import sys
from parity_deriva.data import market
from parity_deriva.etc import settings

import requests
import pandas as pd

from parity_deriva.event.event import StatusEvent
from parity_deriva.event.event import CandleEvent
from parity_deriva.trading.handler import ExecutionHandler


class CandleSaver(ExecutionHandler):
	pairs = None
	store = {}

	def __init__( self, **args):

		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs')

		if self.pairs is not None:
			for p in self.pairs:
				store_name  = "%s.hd5" % p
				market.guard(market.store(p, self.setup), self.setup)
				self.store[p]=pd.HDFStore(market.store(p, self.setup))


	def execute_event(self, event):
		if str(event)!='CANDLE':
			return

		i = event.instrument
		if i in self.pairs:

			t = event.time.strftime('%Y-%m-%d %H:%M:%S')
			g = event.granularity
			try:
				if t in self.store[i][g].index:
					# HDFStore.__getitem__ returns a fresh DataFrame, so
					# assigning into it would only touch a copy. Drop the row
					# from the table, then fall through to the append below.
					self.store[i].remove(g, where='index == "%s"' % t)
					self.logger.debug("%s %s replacing bar at %s" % (i, g, t))
			except KeyError:
				pass                    # the table does not exist yet

			self.store[i].append( g, event.dataframe())



