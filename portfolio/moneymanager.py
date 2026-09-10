
import datetime
from parity_deriva.etc import settings
from parity_deriva.event.event import OrderEvent
from parity_deriva.event.event import OrderCancelEvent
from parity_deriva.trading.handler import ExecutionHandler
import logging


class MoneyManager(ExecutionHandler):
	signals = {}
	processed = []
	onTrade = False
	orderIssued = False

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args,'setup', settings)
		self._set(args,'units', 1)
		# set by StatusEvent('HALT') from the parity monitor, cleared by
		# StatusEvent('RESUME'). Nothing else stopped this component before:
		# an alarm could be raised and orders kept going out.
		self.halted = False
		self.logger.debug("initialized...")

	def addOrder(self, oe):
		oe.batchID = 0
		if oe.signalNumber in self.signals:
			self.signals[oe.signalNumber].append(oe)
			return
		self.signals[oe.signalNumber] = [ oe ]


	def handleSignal(self, se):
		if self.halted:
			self.logger.warning("SIGNAL IGNORED: halted by the parity alarm")
			return
		if self.onTrade or (self.orderIssued and se.signalNumber not in self.signals):
			self.logger.info("SIGNAL IGNORED: onTrade")
			return
 
		ev_dict=se.to_dict()
		ev_dict['units'] = ev_dict['units'] * self.units
		oe = OrderEvent(ev_dict)
		self.queue_event(oe)
		self.addOrder(oe)
		self.logger.info("SENT %s" % oe.info())
		self.orderIssued = True


	def closeTrade(self, event):
		self.logger.debug("Trade closed...")
		self.onTrade = False
		self.orderIssued = False
		orderID = int(event.orderID)
		self.logger.debug("CLOSED: %s PRICE: %s PL: %s COSTS: %s BALANCE: %s"
			% ( orderID, event.price, event.pl, event.financing, event.accountBalance))
#		self.logger.debug(event.dump())
		for s in list(self.signals.keys()):
			# reset per group: otherwise the first match closes out every
			# group visited after it as well
			found = False
			for o in self.signals[s]:
#				self.logger.debug("%s %s" % (s, o.dump()))
				if o.has_attr('orderID') and o.orderID==orderID:
					found = True
					o.orderStatus = 'CLOSED'
					o.closeEvent = event
			if found:
				self.processed.append(self.signals[s])
				del(self.signals[s])
			

	def handleFilled(self,event):
		if event.has_attr('tradesClosed'):
			return self.closeTrade(event)

#		self.logger.debug("GOT %s" % event.info())
		self.onTrade = True
		self.orderIssued = True
		orderID = int(event.orderID)
		for s in self.signals.keys():
			found = False
			for o in self.signals[s]:
#				self.logger.debug("batch: %s: %s" % ( s, o.dump()))
				if o.has_attr('orderID') and o.orderID==orderID:
					found = True

			# cancelliamo gli altri ordini
			if not found:
				self.logger.info("OrderID %d not found in %s" % (orderID, s))
				continue

			for o in self.signals[s]:
				if o.has_attr('orderID') and o.orderID==orderID:
					o.orderStatus = 'FILLED'
					continue

				o.orderStatus = 'CANCELED'
				if not o.has_attr('orderID'):
					self.logger.warning("Order without OrderID: %s" % o.dump())
					continue
				oce = OrderCancelEvent({ 'orderID': o.orderID
					, 'price': o.price
					, 'instrument': o.instrument })
				self.queue_event(oce)
				self.logger.info("SENT %s orderID: %d" % (str(oce), o.orderID))
			return
		self.logger.info("NOT FOUND OrderID %s" % (event.dump()))
		for s in self.signals.keys():
			for o in self.signals[s]:
				self.logger.debug("Signal# %s: %s" % ( s, o.dump()))
		#

	def handleClientOrder(self,event):
		if event.signalNumber not in self.signals:
			self.logger.error("Missing %s" % event.signalNumber)
			return
		for o in self.signals[event.signalNumber]:
			if o.price == event.price:
				#self.logger.error(event.dump())
				o.batchID=int(event.batchID)
				o.orderID=int(event.id)
				self.logger.info("saved id %d batch: %d" % (o.orderID, o.batchID))

	def execute_event(self, event):
		if str(event) == 'STATUS':
			status = getattr(event, 'status', None)
			if status == 'HALT':
				if not self.halted:
					self.logger.critical("HALTED by the parity alarm")
				self.halted = True
			elif status == 'RESUME':
				if self.halted:
					self.logger.warning("RESUMED")
				self.halted = False
			return

		if str(event) not in ['SIGNAL','TRANSACTION','CLIENTORDER']:
			return
		
		if str(event)=='TRANSACTION' and event.type=='ORDER_FILL':
			return self.handleFilled(event)

		if str(event)=='CLIENTORDER':
			return self.handleClientOrder(event)
		
		if str(event)=='SIGNAL':
			return self.handleSignal(event)


