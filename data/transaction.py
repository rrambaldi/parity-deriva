from decimal import Decimal, getcontext, ROUND_HALF_DOWN
import logging
import json
import os

import requests

from qsforex.etc import settings
from qsforex.event.event import TickEvent
from qsforex.data.price import PriceHandler
from qsforex.lib.candle import Candle
from qsforex.event.event import TransactionEvent
from qsforex.trading.handler import StreamHandler



class StreamingForexTransactions(StreamHandler):

	def __init__( self, **args):
		self.logger = logging.getLogger('qsforex.trading.trading')
		self._set(args,'setup', settings)

		self._set(args,'pairs','DE30_EUR')
		self._set(args,'granularity','M5')
		if self.setup.API_VERSION!='3':
			self.logger.error("Unsupported %s" % self.setup.API_VERSION)
			os._exit(-1)
		self.logger.debug("initialized...")

	def connect_to_stream(self):
		pair_list = ",".join(self.pairs)
		try:
			requests.packages.urllib3.disable_warnings()
			s = requests.Session()

			url = "https://" + self.setup.STREAM_DOMAIN + "/v3/accounts/" + self.setup.ACCOUNT_ID + "/transactions/stream"
			params = {'instruments' : pair_list }

			self.logger.debug("url: %s params: %s" % (url, params))
			headers = {'Authorization' : 'Bearer ' + self.setup.ACCESS_TOKEN}
			req = requests.Request('GET', url, headers=headers, params=params)
			pre = req.prepare()
			resp = s.send(pre, stream=True, verify=False)
			return resp
		except Exception as e:
			s.close()
			self.logger.error("Exception on connect: " + str(e))

	def stream_to_queue(self):
		response = self.connect_to_stream()
		self.logger.debug("connected...")
		if response is None:
			self.logger.error("no response from transaction stream")
			return
		if response.status_code != 200:
			self.logger.error("response code: %d" % response.status_code)
			return
		for line in response.iter_lines(1):
			if line:
				try:
					dline = line.decode('utf-8')
					msg = json.loads(dline)
				except Exception as e:
					self.logger.error( "Caught exception when converting message into json: %s" % str(e))
					continue

				valid_data=False
				if msg['type']=='HEARTBEAT':
#					self.logger.debug("(trade) HB")
					continue

				self.logger.debug("(trade) TYPE: %s orderID: %s price: %s" % (msg['type']
							, ( msg['orderID'] if 'orderID' in msg else 0 )
							, ( msg['price'] if 'price' in msg else "" )
				))
#				self.logger.debug(msg)
#				if not msg['type'] in ['TAKE_PROFIT_ORDER','STOP_LOSS_ORDER','ORDER_CANCEL','STOP_ORDER_REJECT']:
#,'STOP_ORDER','MARKET_ORDER','ORDER_FILL'
#					self.logger.debug(msg)

				te = TransactionEvent(msg)
				self.queue_event(te)
				continue

