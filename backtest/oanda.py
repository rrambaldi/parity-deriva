import json
import datetime
import logging

from parity_deriva.etc import settings
from parity_deriva.trading.handler import ExecutionHandler
from parity_deriva.event.event import ClientOrderEvent
from parity_deriva.lib.oanda import OANDAOrder
from parity_deriva.lib.oanda import OANDATrade
from parity_deriva.event.event import Event

class OANDABacktester(ExecutionHandler):

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		# per instance, not per class: one simulator per instrument must keep
		# its own book
		self.trades = []
		self.orders = []
		self.closed_orders = []
		self.closed_trades = []
		self.lastTradeID = 0
		self.lastOrderID = 0
		self._set(args,'setup', settings)
		self._set(args,'currency', 'EUR')
		self._set(args,'balance', 100000.0)

	def dumpOrders(self):
		for o in self.orders:
			self.logger.info("ORDER# %d: %s" % (o.id, o.dump()))

	def dumpTrades(self):
		for t in self.trades:
			self.logger.info("TRADE# %d: %s" % (t.id, t.dump()))

	def cancelOrder(self, event):
		"""
		Cancel a resting order. The simulator numbers its orders itself, so it
		cannot match on the broker's orderID carried by the event: instrument
		and price are the only fields both sides agree on.
		"""
		instrument = getattr(event, 'instrument', None)
		for o in list(self.orders):
			if o.price != event.price:
				continue
			if instrument is not None and getattr(o, 'instrument', None) != instrument:
				continue
			if o.state=='PENDING':
				o.state='CANCELED'
				self.closed_orders.append(o)
				self.orders.remove(o)
				self.logger.debug("CANCELED @price %s" % o.price)
				return
			if o.state=='FILLED':
				self.logger.warning("WARNING CANCEL DI FILL %s" % o.dump())
		return

	def createOrder(self, event):
#		self.logger.debug("== createOrder %s" % event.dump())
		self.logger.debug("== createOrder price %s" % event.price)
		self.lastOrderID = self.lastOrderID + 1
		o = OANDAOrder(self.lastOrderID, event.to_dict())
		o.state = 'PENDING'
		self.orders.append(o)
#		self.dumpOrders()
		'''
		qui dovrei inviare il segnale con l'orderID
		'''


	def handleSLTP(self, o, e):
		if o.type in ['TAKE_PROFIT_ORDER','STOP_LOSS_ORDER']:
			gain = abs((o.price - o.orig.price) * o.units)
			o.state = 'CLOSED'
			o.orig.state = 'CLOSED'
			if o.type=='TAKE_PROFIT_ORDER':
				if o.orig.SLOrder is not None:
					o.orig.SLOrder = 'CANCELED'
			if o.type=='STOP_LOSS_ORDER':
				gain = -gain	
				if o.orig.TPOrder is not None:
					o.orig.TPOrder = 'CANCELED'
			self.balance += gain
			self.logger.info("******** CLOSED TRADE TYPE: %s PL: %d OPEN:%6.2f CLOSED:%6.2f BALANCE:%6.2f"
				% (o.type, gain, o.orig.price, o.price, self.balance))
			return 

		o.batchID = o.id
		o.SLOrder = None
		o.TPOrder = None
		o.type = None
		if o.stopLoss is not None:
			new = Event(o.to_dict())
			new.units = - o.units
			new.price = o.stopLoss
			new.type = 'STOP_LOSS_ORDER'
			new.state = 'PENDING'
			o.SLOrder = new
			new.orig = o
			self.createOrder(new)
			self.logger.debug("== ADDED STOP LOSS @%f" % new.price)

		if o.takeProfit is not None:
			new = Event(o.to_dict())
			new.units = - o.units
			new.price = o.takeProfit
			new.type = 'TAKE_PROFIT_ORDER'
			new.state = 'PENDING'
			o.TPOrder = new
			new.orig = o
			new.orig = o
			self.createOrder(new)
			self.logger.debug("== ADDED TAKE PROFIT @%f" % new.price)


	def checkOrder(self, event):
		self.logger.debug("TIME: %s o:%6.2f h:%6.2f l:%6.2f c:%6.2f"
			% ( event.time, event.mid['o'], event.mid['h'], event.mid['l'], event.mid['c']))
		# snapshot: handleSLTP appends the stop/target to self.orders, and a
		# child must not be matched against the very bar that opened the trade
		for o in list(self.orders):
			if o.state=='PENDING':
				# a real broker fills on touch, so the bounds are inclusive
				if o.units>0 and o.price >= event.ask['l'] and o.price <= event.ask['h']:
					o.state='FILLED'
					self.logger.info("===== FILLED BUY ORDER# %s %f [ %f %f ]" % (o.id, o.price, event.ask['l'], event.ask['h']))
					self.handleSLTP(o, event)
				if o.units<0 and o.price >= event.bid['l'] and o.price <= event.bid['h']:
					o.state='FILLED'
					self.logger.info("===== FILLED SELL ORDER# %s %f [ %f %f ]" % (o.id, o.price, event.bid['l'], event.bid['h']))
					self.handleSLTP(o, event)
			elif o.state=='FILLED':
				pass

	def execute_event(self, event):
		if str(event)=='ORDERCANCEL':
			return self.cancelOrder(event)
	
		if str(event)=='ORDER':
			return self.createOrder(event)

		if str(event)=='CANDLE':
			return self.checkOrder(event)

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
