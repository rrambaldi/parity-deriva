from decimal import Decimal, getcontext, ROUND_HALF_DOWN
import pandas as pd
import datetime
import logging
import json
import time

import requests

from qsforex.etc import settings
from qsforex.lib.utils import datetimeToString, granularityToTimedelta, timestampFromString, dctFromOanda
from qsforex.lib.ohlc import ohlc as ohlc
from qsforex.event.event import CandleEvent
from qsforex.event.event import StatusEvent
from qsforex.trading.handler import StreamHandler


class ForexCandles(StreamHandler):
	sampling = 10
	alignmentTimezone = 'Europe/Rome'
	last = {}
	url = {}
	ask = {}
	bid = {}
	mid = {}

	def __init__( self, **args):

		self.logger = logging.getLogger('qsforex.trading.trading')
		self._set(args,'setup', settings)

		self._set(args,'pairs','DE30_EUR')
		self._set(args,'granularity','M5')

		self.minsec = granularityToTimedelta(self.granularity)
		self.resample = '5s'
		if self.granularity[:1] == 'S':
			self.secs = 1 * int(self.granularity[1:])
			self.resample = str(self.secs) + 's'
		if self.granularity[:1] == 'M':
			self.secs = 60 * int(self.granularity[1:])
			self.resample = self.granularity[1:] + "min"
		if self.granularity[:1] == 'H':
			self.secs = 60*60 * int(self.granularity[1:])
			self.resample = self.granularity[1:] + "h"

		self._set(args,'sampling', 10)
#		self.sleep = int(secs / self.sampling)
		self.sleep = 2

		self.live = True
		self.batch_size = 2
		if self._set(args,'dtfrom', datetime.datetime(1970,1,1,0,0,0)):
			self.live = False
			self._set(args, 'batch_size', 500)
			self._set(args, 'dtto',  datetime.datetime.today())
			self._set(args, 'sleep', 0)

		self.logger.debug("SLEEP %d" % self.sleep)
		self.logger.debug("MINSEC %s" % self.minsec)
		
		for p in self.pairs:
			self.last[p] = self.dtfrom
			self.url[p] = "https://" + self.setup.API_DOMAIN + "/v3/instruments/" + p + "/candles"

		self.headers = {'Authorization' : 'Bearer ' + self.setup.ACCESS_TOKEN}
		self.params = {'price' : 'ABM'
				, 'granularity' : 'S5' #self.granularity
				, 'count': self.batch_size
				, 'includeFirst': 'true'
				, 'alignmentTimezone': self.alignmentTimezone }

		self.aggregate = { 'o': 'first'
 					, 'h': 'max' 
					, 'l': 'min' 
					, 'c': 'last'
		}
		self.init(self.pairs)


	def init(self,pairs):
		for p in pairs:
			d=self.request(p)
			if d is None:
				# request() has already logged and queued a StatusEvent('ERROR')
				self.logger.error("%s no initial candles, skipping seed" % p)
				continue
			msg = json.loads(d.text)
			ask = bid = mid = None
			for c in msg['candles']:
				if c['complete']:
					ask = dctFromOanda(c,'ask')
					bid = dctFromOanda(c,'bid')
					mid = dctFromOanda(c,'mid')
			if ask is None:
				self.logger.error("%s no complete candle in seed response" % p)
				continue
			self.ask[p] = pd.DataFrame.from_dict( ask, orient='index')
			self.bid[p] = pd.DataFrame.from_dict( bid, orient='index')
			self.mid[p] = pd.DataFrame.from_dict( mid, orient='index')

	def request(self, instrument):
		try:
			requests.packages.urllib3.disable_warnings()
			s = requests.Session()

			p = self.params
			if not self.live:
				p['from'] = self.last[instrument].strftime('%Y-%m-%dT%H:%M:%S.%f') + "000Z"

			self.logger.debug("URL %s" % self.url[instrument])
#			self.logger.debug("P %s" % p)
			req = requests.Request('GET', self.url[instrument], headers=self.headers, params=p)
			pre = req.prepare()
			resp = s.send(pre, stream=False, verify=False)
			if resp.status_code != 200:
				s.close()
				self.logger.error("Response error code %d" % resp.status_code)
				sev = StatusEvent('ERROR')
				self.queue_event(sev)
				return None

#			self.logger.debug(resp.text)
			return resp
		except Exception as e:
			s.close()
			self.logger.error("Exception: %s" % str(e))
			sev = StatusEvent('ERROR')
			self.queue_event(sev)
			return None

	def price_request(self, instrument, dtfrom=None):
		try:
			requests.packages.urllib3.disable_warnings()
			s = requests.Session()

			url = "https://" + self.setup.API_DOMAIN + "/v3/accounts/" + self.setup.ACCOUNT_ID + "/pricing"

			self.headers = {'Authorization' : 'Bearer ' + self.setup.ACCESS_TOKEN}
			p = { 'instruments' : instrument
			}
			if (dtfrom is not None):
				p['since'] = datetimeToString(dtfrom)

#			self.logger.debug("URL %s" % self.url[instrument])
#			self.logger.debug("P %s" % p)
			req = requests.Request('GET', url, headers=self.headers, params=p)
			pre = req.prepare()
			resp = s.send(pre, stream=False, verify=False)
			if resp.status_code != 200:
				s.close()
				self.logger.error("Response error code %d" % resp.status_code)
				sev = StatusEvent('ERROR')
				self.queue_event(sev)
				return None

#			self.logger.debug(resp.headers)
#			self.logger.debug(resp.text)

			return resp
		except Exception as e:
			s.close()
			self.logger.error("Exception: %s" % str(e))
			sev = StatusEvent('ERROR')
			self.queue_event(sev)
			return None

	def stream_to_queue(self):
		try:
			sev = StatusEvent('STARTED')
			self.queue_event(sev)
			while True:
				for pair in self.pairs:
					response = self.request(pair)
						
					if response is None:
						self.logger.debug("NADA")
						continue

					try:
						msg = json.loads(response.text)
					except Exception as e:
						self.logger.error( "Caught exception when converting message into json: %s" % str(e))
						sev = StatusEvent('ERROR')
						self.queue_event(sev)
						continue

					block = 0
					for c in msg['candles']:
						cev = CandleEvent(c)
						if not cev.complete:
							continue

						if c['complete']:
							ctime = datetime.datetime.strptime(c['time'], "%Y-%m-%dT%H:%M:%S.%f000Z")
							ask = dctFromOanda(c,'ask', True)
							bid = dctFromOanda(c,'bid', True)
							mid = dctFromOanda(c,'mid', True)
							self.ask[pair].loc[ ctime ] = ask
							self.bid[pair].loc[ ctime ] = bid
							self.mid[pair].loc[ ctime ] = mid

#						self.logger.debug("got %s" % (cev.price_str()))
#						continue
#						self.logger.debug("got %s" % (cev.price_str()))
#						if self.live and cev.time <= self.last[pair]:
#							continue 
				
						if (cev.time-self.last[pair])>self.minsec:
							self.logger.warning("MISSING CANDLES %s - %s" % ( cev.time, self.last[pair]))
							ret = self.price_request(pair, self.last[pair])
						'''
						cev.granularity = msg['granularity']
						cev.instrument  = msg['instrument']
						self.last[pair] = cev.time
						self.queue_event(cev)
						block += 1
						'''

					beg = ctime.replace(hour=0, minute=0, second=0)
					delta = ctime - beg
					if ((delta.seconds % self.secs)==0):
						a=self.ask[pair].tail(12).resample('1min').agg( self.aggregate )
						b=self.bid[pair].tail(12).resample('1min').agg( self.aggregate )
						m=self.mid[pair].tail(12).resample('1min').agg( self.aggregate )
						self.logger.debug("ASK:"+str(a))
						self.logger.debug("BID:"+str(b))
						self.logger.debug("MID:"+str(m))

						cev = CandleEvent(c)
						cev.time = ctime - datetime.timedelta(seconds=self.secs)
						cev.granularity = msg['granularity']
						cev.instrument  = msg['instrument']
						cev.ask = a.loc[cev.time].to_dict()
						cev.bid = b.loc[cev.time].to_dict()
						cev.mid = m.loc[cev.time].to_dict()
						self.queue_event(cev)
						block += 1
						self.logger.debug("sent candle %s" % cev.price_str())
						'''
						self.params['granularity']='M1'
						r = self.request(pair)
						msg = json.loads(r.text)
						for c in msg['candles']:
							if c['complete']:
								a = dctFromOanda(c,'ask')
								b = dctFromOanda(c,'bid')
								m = dctFromOanda(c,'mid')
						self.params['granularity']='S5'
						'''
					## response loop
					if not self.live:
						self.logger.debug("%s sent %d candles" % (pair,block))
					## pair loop
				### infinite loop

				if not self.live:
					maxtime_reached = True
					for pair in self.pairs:
						maxtime_reached = maxtime_reached and self.last[pair]>self.dtto
					
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
			self.logger.debug("CANDLES ERROR:" + str(e))
			return None
