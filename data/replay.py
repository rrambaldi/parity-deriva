from __future__ import print_function

from decimal import Decimal, getcontext, ROUND_HALF_DOWN
import pandas as pd
import datetime
import logging
import json
import time
import os
from qsforex import settings

import requests

from qsforex.event.event import CandleEvent
from qsforex.event.event import StatusEvent
from qsforex.trading.handler import StreamHandler


class ForexCandles(StreamHandler):
	sampling = 10
	alignmentTimezone = 'Europe/Rome'
	last = {}
	url = {}

	def __init__( self, **args):

		self.logger = logging.getLogger('qsforex.trading.trading')
		self._set(args,'setup', settings)

		self._set(args,'pairs','DE30_EUR')
		self._set(args,'granularity','M5')

		delta_d = 0
		delta_m = 0
		delta_h = 0
		delta_s = 0
		if self.granularity[:1] == 'S':
			delta_s = secs
		if self.granularity[:1] == 'M':
			delta_m = int(self.granularity[1:])
		if self.granularity[:1] == 'H':
			delta_h = int(self.granularity[1:])
		if self.granularity[:1] == 'D':
			delta_d = 1
		if self.granularity[:1] == 'W':
			delta_d = 7
		self.interval = pd.Timedelta(days=delta_d,minutes=delta_m,seconds=delta_s,hours=delta_h)

		self.batch_size = 2
		self._set(args,'dtfrom', datetime.datetime(1970,1,1,0,0,0))
		self._set(args, 'batch_size', 500)
		self._set(args, 'dtto',  pd.to_datetime(datetime.datetime.today()))
		self._set(args, 'sleep', 0)

		self.logger.debug("SLEEP %d" % self.sleep)
		
		self.store = {}
		self.curr = {}
		self.last = {}
		self.candles = {}
		self.samples = {}
		self.cent = {}
		for p in self.pairs:
			store_name  = "%s.hd5" % p
			self.candles[p] = 0
			self.curr[p] = pd.to_datetime(self.dtfrom)
			s=pd.HDFStore(os.path.join(self.setup.DATA_DIR,store_name))[self.granularity]
			a=s.to_dict('split')
			self.last[p] = min(s.index.max(), self.dtto)
			self.samples[p] = len(s.loc[self.curr[p]:self.last[p]])
			self.logger.debug("%s %d samples to go" % ( p, self.samples[p]))
			self.cent[p] = self.samples[p] / 100
			i=0
			x={}
			for t in a['index']:
				j=0
				z={}
				for c in a['columns']:
					z[c] = a['data'][j][i]
				x[t]=z
			self.logger.debug("%s %d loaded" % ( p, len(x)))
			self.store[p] = x


	def stream_to_queue(self):
		try:
			sev = StatusEvent('STARTED')
			self.queue_event(sev)
			while True:
				for pair in self.pairs:
					try:
						c = self.store[pair][self.curr[pair]]
						cev = CandleEvent(c)
						cev.granularity = self.granularity
						cev.instrument  = pair
						self.candles[pair] += 1
						self.queue_event(cev)
					except Exception as e:
						pass

					self.curr[pair] = self.curr[pair] + self.interval
					## pair loop
				### infinite loop

				maxtime_reached = True
				for pair in self.pairs:
					maxtime_reached = maxtime_reached and self.curr[pair]>self.last[pair]
					if self.cent[pair]<=self.candles[pair] and self.candles[pair] % self.cent[pair] == 0:
						self.logger.debug("%s %d%%" % ( pair, (self.candles[pair]/self.cent[pair])))
				
				if maxtime_reached:
					self.logger.info("Max time reached on all pairs. Streamer stopped")
					sev = StatusEvent('DONE')
					self.queue_event(sev)
					return

				if self.sleep is not None and self.sleep > 0:
					time.sleep(self.sleep)
		except Exception as e:
			sev = StatusEvent('ERROR')
			self.queue_event(sev)
			self.logger.error("Caught exception when connecting to stream\n" + str(e))
			return None
