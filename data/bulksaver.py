import argparse
from dateutil import parser
from decimal import Decimal, getcontext, ROUND_HALF_DOWN
import pandas as pd
import datetime
import logging
import logging.config
import os
import json
import time
from parity_deriva.data import market
from parity_deriva.etc import settings
from parity_deriva.lib.utils import granularityToTimedelta, getLogger

import requests

from parity_deriva.event.event import CandleEvent
from parity_deriva.event.event import StatusEvent
from parity_deriva.trading.handler import StreamHandler


class BulkSaver(StreamHandler):
	sampling = 10
	alignmentTimezone = 'Europe/Rome'
	curr = {}
	url = {}

	def __init__( self, **args):

		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args,'setup', settings)

		self._set(args,'pairs','DE30_EUR')
		self._set(args,'granularity','M5')

		self.timedelta = granularityToTimedelta(self.granularity)

		self.live = True
		self.batch_size = 2
		self._set(args, 'dtfrom', datetime.datetime(1970,1,1,0,0,0))
		self._set(args, 'batch_size', 2000)
		self._set(args, 'dtto', datetime.datetime.today())
		if self.dtfrom>self.dtto:
			os._exit(-1)

		self.store_name = {}
		for p in self.pairs:
			self.curr[p] = self.dtfrom
			self.url[p] = "https://" + self.setup.API_DOMAIN + "/v3/instruments/" + p + "/candles"
			self.store_name[p]  = "%s.hd5" % p
			store=pd.HDFStore(market.store(p, self.setup))
			try:
				tmmin = store['/'+self.granularity].index.min()
				tmmax = store['/'+self.granularity].index.max()
				self.logger.debug("%s %s data from %s to %s", p, self.granularity, tmmin, tmmax)
				if self.dtfrom < tmmin and self.dtto < tmmin:
					self.dtto = tmmin
				else:
					self.curr[p] = tmmax + self.timedelta
			except Exception as e:
				self.logger.error(e)
				pass
			store.close()
			

		self.headers = {'Authorization' : 'Bearer ' + self.setup.ACCESS_TOKEN}
		self.params = {'price' : 'ABM'
				, 'granularity' : self.granularity
				, 'count': self.batch_size
				, 'includeFirst': 'true'
				, 'alignmentTimezone': self.alignmentTimezone }

	def request(self, instrument):
		msg = ""
		try:
			requests.packages.urllib3.disable_warnings()
			s = requests.Session()

			p = self.params
			p['from'] = self.curr[instrument].strftime('%Y-%m-%dT%H:%M:%S.%f') + "000Z"

			self.logger.debug("URL %s" % self.url[instrument])
			self.logger.debug("%s P %s" % (instrument, p))
			req = requests.Request('GET', self.url[instrument], headers=self.headers, params=p)
			pre = req.prepare()
			resp = s.send(pre, stream=False, verify=False)
			if resp is None:
				self.logger.error("No Response")
				return None

			if resp.status_code != 200:
				self.logger.error("Response error code %d" % resp.status_code)
				return None

			msg = json.loads(resp.text)

			return msg
		except Exception as e:
			s.close()
			self.logger.error("%s last data was %s" %( instrument, self.curr[instrument]))
			self.logger.error("URL %s" % self.url[instrument])
			self.logger.error("P %s" % p)
			self.logger.error("RESPONSE %s" % msg)
			self.logger.error("REQUEST ERROR: %s\n" + str(e))
			return None

	def create_dict(self, msg):
		block = {}
		for c in msg['candles']:
			t = datetime.datetime.strptime(c['time'], "%Y-%m-%dT%H:%M:%S.%f000Z")
			res={}
			res['volume'] = int(c['volume'])
			for x in ['ask','bid','mid']:
				for p in ['c','h','l','o']:
					res[ x+'_'+p] = float(c[x][p])
			block[t] = res
		return block

	def save_dict(self, pair, block):
		v=pd.DataFrame.from_dict(block,orient='index')
		self.curr[pair] = v.index.max() + self.timedelta
		market.guard(market.store(pair, self.setup), self.setup)
		store=pd.HDFStore(market.store(pair, self.setup))
		store.append(self.granularity,v)
		store.close()
		self.logger.debug("%s saved %d candles from %s to %s"
			% ( pair, len(v.index), v.index.min(), v.index.max()))

		
	def stream_to_queue(self):
		try:
			sev = StatusEvent('STARTED')
			self.queue_event(sev)
			while True:
				for pair in self.pairs:

					if self.curr[pair] > self.dtto:
						continue ;

					self.logger.debug("%s requested" % pair)
					msg = self.request(pair)
					if msg is None:
						sev = StatusEvent('ERROR')
						self.queue_event(sev)
						time.sleep(60)
						continue

					self.logger.debug("%s parsing response" % pair)

					block = self.create_dict(msg)
					if len(block)==0:
						self.logger.debug("%s NO DATA RESPONSE WAS: %s" % (pair, msg))
						self.curr[pair] = self.curr[pair] + self.timedelta * ( self.batch_size // 2 )
						continue

					self.logger.debug("%s saving..." % pair)
					self.save_dict(pair, block)
					## pair loop
				### infinite loop

				maxtime_reached = True
				for pair in self.pairs:
					maxtime_reached = maxtime_reached and self.curr[pair]>self.dtto
				
				if maxtime_reached:
					self.logger.info("Max time reached on all pairs. Streamer stopped")
					sev = StatusEvent('DONE')
					self.queue_event(sev)
					return

		except Exception as e:
			sev = StatusEvent('ERROR')
			self.queue_event(sev)
			self.logger.error("Caught exception when connecting to stream\n" + str(e))
			return None


if __name__ == '__main__':
	# Set up logging
	logger = getLogger()

	# Set the number of decimal places to 2
	getcontext().prec = 2
	today = str(datetime.date.today())


	arg_parser = argparse.ArgumentParser(description='Oanda downloader')
	arg_parser.add_argument('--pairs', default='EUR_USD', required=False, help='Instrument')
	arg_parser.add_argument('--dtfrom', default='2006-01-01', required=False, help='From date')
	arg_parser.add_argument('--dtto', default=today, required=False, help='Today')
	arg_parser.add_argument('--timeframe', default='M1', required=False, help='timeframe')
	arg_parser.add_argument('--timezone', default='Europe/Rome', required=False, help='timezone')
	arg_parser.add_argument('--price', default='ABM', required=False, help='Mid/Ask/Bid')
	args = arg_parser.parse_args()

	pairs = args.pairs.split(",")
	children = {}
	retok = {}
	for p in pairs:
		pid = os.fork()
		if pid:
			children[pid] = p
			retok[p] = False
		else:
			bs= BulkSaver( pairs= [ p ]
				, granularity=args.timeframe
				, sleep = 0
				, batch_size = 2000
				, dtfrom=parser.parse(args.dtfrom)
				, dtto=parser.parse(args.dtto)
			)
			bs.stream_to_queue()
			os._exit(0)

	alldone=False
	while not alldone:
		ret=os.waitpid(0, 0)
		proc = children[ret[0]]
		logger.info("CHILD %s ENDED WITH: %d" % (proc, ret[1]))
		if ret[1]!=0:
			pid = os.fork()
			if pid:
				children[pid] = proc
				retok[proc] = False
			else:
				bs= BulkSaver( pairs= [ proc ]
					, granularity=args.timeframe
					, sleep = 0
					, batch_size = 2000
					, dtfrom=parser.parse(args.dtfrom)
					, dtto=parser.parse(args.dtto)
				)
				bs.stream_to_queue()
				os._exit(0)
			continue

		retok[proc] = True
		alldone = True
		for p in retok:
			logger.info( "STATS: %s %s " % (p, retok[p]))
			alldone = alldone and retok[p]

			

