import json
import datetime
from qsforex.etc import settings
from qsforex.trading.handler import ExecutionHandler
from qsforex.event.event import ClientOrderEvent
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
		self.logger = logging.getLogger('qsforex.trading.trading')
		self._set(args,'setup', settings)

		self.api = "/v3/accounts/%s/orders" % str(self.setup.ACCOUNT_ID)
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


	def execute_event(self, event):
		if str(event)=='ORDERCANCEL':
			return self.cancelOrder(event)
	
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
