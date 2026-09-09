import copy
import logging
import datetime
from parity_deriva.event.event import SignalEvent
from parity_deriva.trading.handler import ExecutionHandler


class AG01(ExecutionHandler):
	"""
	"""
	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')

		self.prev = {}
		self._set(args,'pairs', [ 'EUR_USD' ])
		self._set(args,'granularity', 'M5')
		self._set(args,'gtdTime', "23:59:59")

		if self.gtdTime is not None:
			self.gtdTime = self.gtdTime.split(':')

		self.ticks = 0
		self.invested = False
		for i in self.pairs:
			self.prev[i] = None
		self.logger.debug("strategy ready")


	def execute_event(self, event):
		if str(event) != 'CANDLE':
			return

		i = event.instrument
#		self.logger.debug("called %s %s" % (str(event), i));
		if i not in self.pairs:
			return ;

		if self.prev[i] == None:
			self.logger.debug("first candle");
			self.prev[i] = event
			return ;

		p = self.prev[i]
#		self.logger.debug("CURR: %s" % event.price_str())
#		self.logger.debug("PREV: %s" % p.price_str())
		if p.direction()==event.direction():
			self.prev[i] = event
			return ;

		## il engulfing
		spread = event.ask['c'] - event.bid['c']
		sb = SignalEvent()
		sb.signalNumber = datetime.datetime.today().strftime("%Y%m%d%H%M%S")
		sb.clientExtension = { 'id': sb.signalNumber
				, 'tag': self.__class__.__name__
				, 'comment': '%s' % self.granularity
			}
		sb.signalType = 'EXCLUSIVE'
		sb.orderType = "STOP"
		sb.instrument = i
		sb.gtdTime = datetime.datetime.today().replace(hour=int(self.gtdTime[0])
				, minute=int(self.gtdTime[1]), second=int(self.gtdTime[0]), microsecond=0)
		sb.time = event.time
		sb.stopLoss = min(p.bid['l'], event.bid['l'])
		sb.price = max(p.ask['h'], event.ask['h'])
		sb.takeProfit = round(sb.price + ( sb.price - sb.stopLoss ) * 1.2 + spread, 1)
		sb.units = 1 ## buy
		sb.type = 'STOP'
		if self.event_queue is not None:
			self.queue_event(sb)
			self.logger.debug( "SENT %s" % sb.info() )

		ss = SignalEvent( sb.to_dict() )
		ss.stopLoss = max(p.ask['h'], event.ask['h'])
		ss.price = min(p.bid['l'], event.bid['l'])
		ss.takeProfit = round(ss.price - ( ss.stopLoss - ss.price ) * 1.2 + spread,1)
		ss.units = -1  ## SELL

		if self.event_queue is not None:
			self.queue_event(ss)
			self.logger.debug( "SENT %s" % ss.info() )

		self.prev[i] = event

