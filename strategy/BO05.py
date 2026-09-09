import copy
import logging
import datetime
from parity_deriva.event.event import SignalEvent
from parity_deriva.trading.handler import ExecutionHandler
from parity_deriva.strategy.BO import BO

class BO05(BO):
	"""
	"""
	def __init__(self, **args):
		super(BO05,self).__init__(**args)
		
	def calculate(self, event):
		self.received+=1
		p=self.prev
		dbg = ""
		for j in range(1,self.depth+1):
			dbg = "%s %s" % ( dbg, p[j].direction())

#			self.logger.debug("SEQ: %s" % dbg)
###### PATTERN
#  G R R
		if p[1].time.hour >= self.minTime.hour and p[1].time.hour<=self.maxTime.hour and \
		p[1].direction()<0 and p[2].direction()<0 and p[3].direction()<0:
			out=0
			for j in range(4,self.depth+1):
				out += 1
				if p[j].direction()<0:
					break
			else:
				out = 0	# never resolved inside the window
			if out==0:
				self.logger.debug("==== DEAD %s" % ( self.__class__.__name__))
			self.logger.debug("%s %s EVENT P1 %s : IN %d" % (self.__class__.__name__, self.pair, p[1].time, out))
			self.num[out] += 1
			self.numh[p[1].time.hour][out] += 1
			self.week[p[1].time.weekday()][out] += 1
#		p = self.prev[i]
#		self.logger.debug("CURR: %s" % event.price_str())
#		self.logger.debug("PREV: %s" % p.price_str())
#		if p.direction()==event.direction():
#			self.prev[i] = event
#			return ;



class BO06(BO):
	"""
	"""
	def __init__(self, **args):
		super(BO06,self).__init__(**args)

	def calculate(self, event):
		self.received+=1
		p=self.prev
		dbg = ""
		for j in range(1,self.depth+1):
			dbg = "%s %s" % ( dbg, p[j].direction())

#			self.logger.debug("SEQ: %s" % dbg)
###### PATTERN
#  G G G
		if p[1].time.hour >= self.minTime.hour and p[1].time.hour<=self.maxTime.hour and \
		p[1].direction()>0 and p[2].direction()>0 and p[3].direction()>0:
			out=0
			for j in range(4,self.depth+1):
				out += 1
				if p[j].direction()<0:
					break
			else:
				out = 0	# never resolved inside the window
			if out==0:
				self.logger.debug("==== DEAD %s" % ( self.__class__.__name__))
			self.logger.debug("%s %s EVENT P1 %s : IN %d" % (self.__class__.__name__, self.pair, p[1].time, out))
			self.num[out] += 1
			self.numh[p[1].time.hour][out] += 1
			self.week[p[1].time.weekday()][out] += 1
