from decimal import Decimal, getcontext, ROUND_HALF_DOWN
import pandas as pd
import datetime
import logging
import json
import time
import sys
import os

import requests

from parity_deriva.etc import settings
from parity_deriva.lib.utils import datetimeToString, granularityToTimedelta, timestampFromString, dctFromOanda
from parity_deriva.lib.ohlc import ohlc as ohlc
from parity_deriva.event.event import CandleEvent
from parity_deriva.event.event import StatusEvent
from parity_deriva.trading.handler import StreamHandler


class ForexCandles(StreamHandler):
	sampling = 10
	alignmentTimezone = 'Europe/Rome'
	last = {}
	url = {}
	ask = {}
	bid = {}
	mid = {}

	def __init__( self, **args):

		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args,'setup', settings)

		self._set(args,'pairs','DE30_EUR')
		self._set(args,'granularity','M5')

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
		
		self.num_blocks = {}
		self.num_candles = {}
		for p in self.pairs:
			self.last[p] = self.dtfrom
			self.num_blocks[p] = 0
			self.num_candles[p] = 0
			self.url[p] = "https://" + self.setup.API_DOMAIN + "/v3/instruments/" + p + "/candles"

		self.headers = {'Authorization' : 'Bearer ' + self.setup.ACCESS_TOKEN}
		self.params = {'price' : 'ABM'
				, 'granularity' : self.granularity
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

					self.num_blocks[pair]+=1
					tot = 0
					for c in msg['candles']:
						cev = CandleEvent(c)
						if not cev.complete:
							continue

#						self.logger.debug("got %s" % (cev.price_str()))
#						continue
#						self.logger.debug("got %s" % (cev.price_str()))
#						if self.live and cev.time <= self.last[pair]:
#							continue 
				
						cev.granularity = msg['granularity']
						cev.instrument  = msg['instrument']
						self.last[pair] = cev.time
						tot+=1
						self.queue_event(cev)

					## response loop
					self.num_candles[pair]+=tot
					if not self.live:
						self.logger.debug("%s %d block sent %d candles" % (pair,self.num_blocks[pair],tot))
					## pair loop
				### infinite loop

				if not self.live:
					maxtime_reached = True
					for pair in self.pairs:
						self.logger.debug("last pair was: %s" % ( self.last[pair].strftime('%Y-%m-%d %H:%M:%S') ))
						maxtime_reached = maxtime_reached and self.last[pair]>=self.dtto
					
					if maxtime_reached:
						self.logger.info("Max time reached on all pairs. Streamer stopped")
						sev = StatusEvent('DONE')
						self.queue_event(sev)
						return

				if self.sleep is not None and self.sleep > 0:
					time.sleep(self.sleep)
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			sev = StatusEvent('ERROR')
			self.queue_event(sev)
			self.logger.debug("CANDLES ERROR:  @%d : %s"  % ( exc_tb.tb_lineno, str(e)))
			return None
