import json
import datetime
from parity_deriva.etc import settings
from parity_deriva.trading.handler import ExecutionHandler
from parity_deriva.event.event import ClientOrderEvent
from parity_deriva.event.event import TransactionEvent
import http.client as httplib
import logging
from urllib.parse import urlencode
import urllib3
urllib3.disable_warnings()



class SimulatedExecution(object):
	"""
	Provides a simulated execution handling environment. This class
	actually does nothing - it simply receives an order to execute.

	Instead, the Portfolio object actually provides fill handling.
	"""
	def execute_order(self, event):
		pass


class OANDAExecutionHandler(ExecutionHandler):

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args,'setup', settings)

		# the account, and the orders collection inside it. Two paths rather
		# than one because a stop is moved through the *trade* it belongs to,
		# which hangs off the account and not off the orders collection.
		self.account = "/v3/accounts/%s" % str(self.setup.ACCOUNT_ID)
		self.api = "%s/orders" % self.account
		self.headers = {
			"Content-Type": "application/json",
			"Authorization": "Bearer " + self.setup.ACCESS_TOKEN
		}

	def cancelOrder(self, event):
		params = ""
		conn= httplib.HTTPSConnection(self.setup.API_DOMAIN)
		conn.request(
			"PUT"
			, "%s/%d/cancel" % (self.api, event.orderID)
			, params
			, self.headers
		)
		response = conn.getresponse().read()
		if response is None:
			self.logger.warning("ORDER NOT SENT")
			return 

		resp = json.loads(response)
		self.logger.debug("CANCEL RESPONSE: %s" % resp)
		return


	def modifyStop(self, event):
		"""
		Move the stop of a trade that is already open.

		There is no "move this stop" call at OANDA, and there does not need to
		be one: PUT /v3/accounts/{id}/trades/{tradeID}/orders cancels the stop
		the trade currently carries and attaches the new one, in a single
		transaction batch. That is the cancel-then-create this needs, done by
		the broker in one step - which matters, because between a cancel of
		ours and a create of ours the trade would sit on the account with no
		stop at all, and the ladder exists precisely for the bars where price
		is running.

		The trade is named by its tradeID, not by the entry order's id: at
		OANDA those are two different numbers, and a stop hangs off the trade.
		portfolio/trailer.py reads the id off the opening fill's `tradeOpened`
		and puts it on the event. Without one there is nothing to address, so
		the move is refused and logged rather than guessed at - a stop moved
		onto the wrong trade is worse than a stop not moved.
		"""
		tradeID = getattr(event, 'tradeID', None)
		# Event.__set__ turns a price it cannot read into the string "0.0"
		# rather than leaving it None, so "is None" is not the test: a level of
		# zero is the absence of one, and sending it would ask the account to
		# move the stop to nothing.
		try:
			price = float(getattr(event, 'price', None))
		except (TypeError, ValueError):
			price = None
		if tradeID is None or not price:
			self.logger.error("STOP NOT MOVED: no trade to move it on (%s)"
				% event.to_json())
			return

		# GTC, like the stop the order created on fill: a stop that expires
		# with the session leaves the trade naked overnight.
		params = json.dumps({"stopLoss": {
			"price": str(price), "timeInForce": "GTC"}})
		conn = httplib.HTTPSConnection(self.setup.API_DOMAIN)
		conn.request(
			"PUT"
			, "%s/trades/%s/orders" % (self.account, tradeID)
			, params
			, self.headers
		)
		response = conn.getresponse().read()
		if response is None:
			self.logger.warning("STOP NOT SENT")
			return

		resp = json.loads(response)
		if "errorCode" in resp:
			# Loud, and on purpose. The simulator shadowing this handler moves
			# its own stop whatever the account says, so a refusal here is the
			# two sides parting company: the backtest is walking a ladder the
			# account is not.
			self.logger.error("STOP MODIFY REJECTED: %s (trade %s @%s)"
				% (resp['errorCode'], tradeID, price))
			return

		self.logger.info("MOVED STOP trade %s -> %s" % (tradeID, price))
		return resp

	def execute_event(self, event):
		if str(event)=='ORDERCANCEL':
			return self.cancelOrder(event)

		if str(event)=='STOPMODIFY':
			return self.modifyStop(event)
	
		if str(event)!='ORDER':
			return

		'''
{"batchID": "1253"
, "_type": "TRANSACTION"
, "triggerCondition": "TRIGGER_DEFAULT"
, "price": 11720.0
, "stopLossOnFill": {"timeInForce": "GTC", "price": "11730.0"}
, "userID": 0
, "takeProfitOnFill": {"timeInForce": "GTC", "price": "11710.0"}
, "timeInForce": "GTD"
, "instrument": "DE30_EUR"
, "reason": "CLIENT_ORDER"
, "id": "1253"
, "time": "1970-01-01T00:00:00"
, "units": "-1"
, "_created": "2017-01-30T11:10:27.551657"
, "gtdTime": "2017-01-30T14:17:00.000000000Z"
, "type": "STOP_ORDER"
, "positionFill": "DEFAULT"
, "accountID": "101-000-0000000-000"}

ORDER:
{"order":
{"instrument":"DE30_EUR"
,"type":"STOP"
,"units":"-1"
,"price":"11690.2"
,"timeInForce":"GTD"
,"gtdTime":"2017-01-31T15:55:00.000Z"
,"stopLossOnFill":{"price":"11704.2"}
,"takeProfitOnFill":{"price":"11680.2"}
}}
		'''
		gtdTime = datetime.datetime.today().replace(hour=23,minute=0,second=0)
		if event.gtdTime is not None:
			gtdTime = event.gtdTime

		order=  {
			"instrument" : event.instrument
			, "timeInForce": "GTD" 
			, "units" : str(event.units)
			, "type" : event.orderType
			, "price" : str(event.price)
			, "gtdTime" : gtdTime.strftime("%Y-%m-%dT%H:%M:%S.000Z")
		}
		if event.stopLoss is not None:
			order["stopLossOnFill"] = {  "price": str(event.stopLoss) }
		if event.takeProfit is not None:
			order["takeProfitOnFill"] = { "price": str(event.takeProfit) }
		# Tag the order with the signal that produced it. OANDA echoes
		# clientExtensions back on every transaction the order generates, so a
		# fill arrives already carrying the key its simulated counterpart is
		# filed under - no bookkeeping of our own is needed to join the two.
		# Note: OANDA refuses clientExtensions on accounts linked to MT4, and
		# limits the length of each field; check them against
		# GET /v3/accounts/{id} before relying on long ids.
		if event.has_attr('clientExtension') and event.clientExtension:
			order["clientExtensions"] = event.clientExtension
	
		self.logger.debug("GOT REQUEST %s" % event.info() )

		params = json.dumps({ 'order':  order })
		conn= httplib.HTTPSConnection(self.setup.API_DOMAIN)
		conn.request(
			"POST"
			, self.api
			, params
			, self.headers
		)
		response = conn.getresponse().read()
		if response is None:
			self.logger.warning("ORDER NOT SENT")
			return 

		resp = json.loads(response)
#		self.logger.debug(resp)
		if "errorCode" in resp:
			self.logger.error("ORDER REJECTED: %s" % resp['errorCode'])
			# Was: logged and dropped. The money manager was then left
			#      believing an order was outstanding that the broker had
			#      refused, and it refuses every new signal number while it
			#      believes that - so a rejected bracket stopped the strategy
			#      for good.
			# Now: published, and normalised to the same 'ORDER_REJECT' type
			#      data/etoro.py publishes when it finds a rejection by
			#      polling, so the money manager has one branch rather than
			#      one per broker.
			rejection = TransactionEvent({
				'type': 'ORDER_REJECT',
				'instrument': event.instrument,
				'price': event.price,
				'units': event.units,
				'orderType': event.orderType,
				'rejectReason': resp['errorCode'],
				'errorMessage': resp.get('errorMessage'),
			})
			rejection.signalNumber = event.signalNumber
			self.queue_event(rejection)
			return

		coe = ClientOrderEvent(resp['orderCreateTransaction'])
		coe.signalNumber = event.signalNumber
		self.queue_event(coe)
		del conn

		'''
 {"orderCreateTransaction":{
	"type":"STOP_ORDER"
	,"instrument":"DE30_EUR"
	,"units":"-1"
	,"price":"11583.3"
	,"timeInForce":"GTD"
	,"gtdTime":"2017-01-31T20:00:00.000000000Z"
	,"triggerCondition":"TRIGGER_DEFAULT"
	,"positionFill":"DEFAULT"
	,"takeProfitOnFill":{"price":"11575.2","timeInForce":"GTC"}
	,"stopLossOnFill":{"price":"11591.3","timeInForce":"GTC"}
	,"reason":"CLIENT_ORDER"
	,"id":"1508"
	,"userID":0
	,"accountID":"101-000-0000000-000"
	,"batchID":"1508"
	,"time":"2017-01-31T15:59:02.470947825Z"}
,"relatedTransactionIDs":["1508"]
,"lastTransactionID":"1508"
}


{"orderRejectTransaction":{
	"type":"STOP_ORDER_REJECT"
	,"instrument":"DE30_EUR"
	,"units":"-1"
	,"price":"11592.0"
	,"timeInForce":"GTD"
	,"gtdTime":"2017-01-31T20:00:00.000000000Z"
	,"triggerCondition":"TRIGGER_DEFAULT"
	,"positionFill":"DEFAULT"
	,"takeProfitOnFill":{"price":"11577.54","timeInForce":"GTC"}
	,"stopLossOnFill":{"price":"11605.3","timeInForce":"GTC"}
	,"rejectReason":"TAKE_PROFIT_ON_FILL_PRICE_PRECISION_EXCEEDED"
	,"reason":"CLIENT_ORDER"
	,"id":"1481"
	,"userID":0
	,"accountID":"101-000-0000000-000"
	,"batchID":"1481"
	,"time":"2017-01-31T15:44:01.190608105Z"}
,"relatedTransactionIDs":["1481"]
,"lastTransactionID":"1481"
,"errorMessage":"The Take Profit on fill specified contains a price with more precision than is allowed by the Order's instrument"
,"errorCode":"TAKE_PROFIT_ON_FILL_PRICE_PRECISION_EXCEEDED"}


		'''
