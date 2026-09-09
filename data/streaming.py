from decimal import Decimal, getcontext, ROUND_HALF_DOWN
import logging
import json

import requests

from parity_deriva.etc import settings
from parity_deriva.event.event import TickEvent
from parity_deriva.data.price import PriceHandler
from parity_deriva.lib.candle import Candle
from parity_deriva.event.event import TransactionEvent
from parity_deriva.trading.handler import StreamHandler


class StreamingForexPrices(StreamHandler, PriceHandler):
	events_queue = None

	def __init__(
		self, pairs, setup = None
	):
		if setup is None:
			setup = settings
		self.domain = setup.STREAM_DOMAIN
		self.access_token = setup.ACCESS_TOKEN
		self.account_id = setup.ACCOUNT_ID
		self.events_queue = getattr(setup, 'events_queue', None)
		self.api_version = setup.API_VERSION

		self.pairs = pairs
		self.prices = self._set_up_prices_dict()
		self.logger = logging.getLogger(__name__)

	def connect_to_stream(self):
		pairs_oanda = ["%s_%s" % (p[:3], p[3:]) for p in self.pairs]
		pair_list = ",".join(pairs_oanda)
		try:
			requests.packages.urllib3.disable_warnings()
			s = requests.Session()
			url = "https://" + self.domain + "/v1/prices"
			params = {'instruments' : pair_list, 'accountId' : self.account_id}

			if self.api_version=='3':
				url = "https://" + self.domain + "/v3/accounts/" + self.account_id + "/pricing/stream"
				params = {'instruments' : pair_list }

			headers = {'Authorization' : 'Bearer ' + self.access_token}
			req = requests.Request('GET', url, headers=headers, params=params)
			pre = req.prepare()
			resp = s.send(pre, stream=True, verify=False)
			return resp
		except Exception as e:
			s.close()
			print("Caught exception when connecting to stream\n" + str(e))

	def stream_to_queue(self):
		response = self.connect_to_stream()
		if response is None:
			self.logger.error("no response from price stream")
			return
		if response.status_code != 200:
			return
		for line in response.iter_lines(1):
			if line:
				try:
					dline = line.decode('utf-8')
					msg = json.loads(dline)
				except Exception as e:
					self.logger.error(
						"Caught exception when converting message into json: %s" % str(e)
					)
					return

				valid_data=False
				if self.api_version == '3' and msg['type']=='HEARTBEAT':
					self.logger.debug("HB")
					pass

				if self.api_version == '3' and msg['type']=='PRICE':
					if msg['tradeable']:
						self.logger.debug(msg)
						valid_data=True
					else:
						self.logger.debug("Not tradable data")

					getcontext().rounding = ROUND_HALF_DOWN 
					instrument = msg["instrument"].replace("_", "")
					time = msg["time"]
					bid = Decimal(str(msg["closeoutBid"])).quantize(
						Decimal("0.00001")
					)
					ask = Decimal(str(msg["closeoutAsk"])).quantize(
						Decimal("0.00001")
					)

				if self.api_version == '1' and ("instrument" in msg or "tick" in msg):
					valid_data=True
					self.logger.debug(msg)
					getcontext().rounding = ROUND_HALF_DOWN 
					instrument = msg["tick"]["instrument"].replace("_", "")
					time = msg["tick"]["time"]
					bid = Decimal(str(msg["tick"]["bid"])).quantize(
						Decimal("0.00001")
					)
					ask = Decimal(str(msg["tick"]["ask"])).quantize(
						Decimal("0.00001")
					)

				if valid_data:
					self.prices[instrument]["bid"] = bid
					self.prices[instrument]["ask"] = ask
					# Invert the prices (GBP_USD -> USD_GBP)
					inv_pair, inv_bid, inv_ask = self.invert_prices(instrument, bid, ask)
					self.prices[inv_pair]["bid"] = inv_bid
					self.prices[inv_pair]["ask"] = inv_ask
					self.prices[inv_pair]["time"] = time
					tev = TickEvent({
						"type": "TICK", "instrument": instrument,
						"time": time, "bid": bid, "ask": ask
					})
					self.queue_event(tev)

