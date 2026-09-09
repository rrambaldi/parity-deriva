
from decimal import Decimal, getcontext, ROUND_HALF_DOWN
import pandas as pd
import datetime
import logging
import logging.config
import json
import time
from parity_deriva.etc import settings

import requests
from parity_deriva.lib.utils import timestampFromString, getLogger

from parity_deriva.event.event import CandleEvent
from parity_deriva.event.event import StatusEvent
from parity_deriva.trading.handler import ExecutionHandler

class Analyzer(ExecutionHandler):

	def __init__( self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args,'setup', settings)
		self._set(args,'day', None)
		self._set(args,'from', None)
		self._set(args,'to', None)

		print(self.day)
		self._set(args,'pairs',['DE30_EUR'])
		self.url = {}
		for p in self.pairs:
			self.url[p] = "https://" + self.setup.API_DOMAIN + "/v3/accounts/" + self.setup.ACCOUNT_ID  + "/trades?state=CLOSED&instrument="+p

		self.headers = {'Authorization' : 'Bearer ' + self.setup.ACCESS_TOKEN}

	def request(self, instrument):
		try:
			requests.packages.urllib3.disable_warnings()
			s = requests.Session()

			req = requests.Request('GET', self.url[instrument], headers=self.headers, params={})
			pre = req.prepare()
			resp = s.send(pre, stream=False, verify=False)
			if resp.status_code != 200:
				s.close()
				self.logger.error("URL: %s" % self.url[instrument])
				self.logger.error("Response error code %d" % resp.status_code)
				return None

			return resp
		except Exception as e:
			s.close()
			self.logger.error("Exception: %s" % str(e))
			return None
	'''
   "trades" : [
      {
         "openTime" : "2017-02-03T12:32:09.575587743Z",
         "closingTransactionIDs" : [
            "2717"
         ],
         "financing" : "-0.0396",
         "instrument" : "DE30_EUR",
         "averageClosePrice" : "11643.8",
         "realizedPL" : "450.0000",
         "state" : "CLOSED",
         "closeTime" : "2017-02-03T12:33:29.561515488Z",
         "stopLossOrder" : {
            "triggerCondition" : "TRIGGER_DEFAULT",
            "timeInForce" : "GTC",
            "tradeID" : "2713",
            "cancellingTransactionID" : "2718",
            "state" : "CANCELLED",
            "createTime" : "2017-02-03T12:32:09.575587743Z",
            "id" : "2715",
            "type" : "STOP_LOSS",
            "price" : "11653.3",
            "cancelledTime" : "2017-02-03T12:33:29.561515488Z"
         },
	'''
	def execute_event(self, e):
		pass

	def analyze(self, instrument):
		result = self.request(instrument)
		if result is None:
			return

		tot = 0
		tot_p = 0
		tot_l = 0
		num_p = 0
		num_l = 0
		max_p = 0
		max_l = 0
		curr_p = 0
		curr_l = 0
		cons_p = 0
		cons_l = 0
		draw_p = 0
		draw_l = 0
		max_cons_p = 0
		max_cons_l = 0
		costs = 0
		prev = 0
		data = json.loads(result.text)
		for t in data['trades']:
			opn = timestampFromString(t['openTime']).replace(microsecond=0)
			cls = timestampFromString(t['closeTime']).replace(microsecond=0)
			if self.day is not None and not opn.date()==self.day:
				continue
			costs = costs + float(t['financing'])
			pl = float(t['realizedPL'])
			self.logger.info("%s %s %s PL: %6.2f" % ( opn, cls, (cls-opn), pl))
			tot += 1
			if pl>0:
				draw_l = 0
				curr_l = 0
				num_p += 1
				tot_p += pl
				max_p = max(max_p,pl)
				if prev > 0:
					curr_p += 1
					draw_p += pl
					cons_p = max(curr_p,cons_p)
					max_cons_p = max(max_cons_p, draw_p)

			if pl<0:
				draw_p = 0
				curr_p = 0
				num_l += 1
				tot_l += abs(pl)
				if abs(pl)>max_l:
					max_l = abs(pl)

				if prev < 0:
					curr_l += 1
					draw_l += abs(pl)
					cons_l = max(curr_l,cons_l)
					max_cons_l = max(max_cons_l, draw_l)

			prev = pl

		self.logger.info("	RESULT PL: %6.2f" % (tot_p-tot_l))
		self.logger.info("WN TOT: %6.2f  NUM: %6.2f  AVG: %6.2f  MAX: %6.2f" % (tot_p, num_p, tot_p / num_p, max_p))
		self.logger.info("LS TOT: %6.2f  NUM: %6.2f  AVG: %6.2f  MAX: %6.2f" % (tot_l, num_l, tot_l / num_l, max_l))
		self.logger.info("WN MAX.CONS: %d  MAX.AMOUNT: %6.2f" % ( cons_p, max_cons_p))
		self.logger.info("LS MAX.CONS: %d  MAX.AMOUNT: %6.2f" % ( cons_l, max_cons_l))
		self.logger.info("WIN/LOSS RATIO: %6.2f" % (tot_p/(tot_p+tot_l)))
		self.logger.info("TOTAL COSTS: %6.2f" % costs)
		self.logger.info("OPTIMAL F: %6.2f" % (2*(tot_p/(tot_p+tot_l))-1))
		c = (tot_p / num_p) / (tot_l / num_l)
		p = (tot_p/(tot_p+tot_l))
		self.logger.info("OPTIMAL F-AVG: %6.2f" % (((c+1)*p-1)/c))
		c = max_p / max_l
		self.logger.info("OPTIMAL F-MAX: %6.2f" % (((c+1)*p-1)/c))




if __name__ == '__main__':
	logger = getLogger()

	a = Analyzer(pairs= ['DE30_EUR']
			, day=datetime.datetime.strptime('2017-02-06', '%Y-%m-%d').date()
	)
	a.analyze('DE30_EUR')
	print("FINE")
