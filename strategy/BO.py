
import copy

import logging
import datetime
from qsforex.event.event import SignalEvent
from qsforex.trading.handler import ExecutionHandler

class BO(ExecutionHandler):
	"""
	"""
	def __init__(self, **args):
		self.logger = logging.getLogger('qsforex.trading.trading')

		self.stats_after = 500
		self.prev = {}
		self.received = 0
		self.num = {}
		self.week = {}
		self._set(args,'pair', 'EUR_USD' )
		self._set(args,'granularity', 'M5')
		self._set(args,'gtdTime', "23:59:59")
		self._set(args,'minTime', "09:30:00")
		self._set(args,'maxTime', "18:00:00")
		self._set(args,'depth', 14)

		self.minTime = datetime.datetime.strptime(self.minTime,"%H:%M:%S")
		self.maxTime = datetime.datetime.strptime(self.maxTime,"%H:%M:%S")

		if self.gtdTime is not None:
			self.gtdTime = self.gtdTime.split(':')

		self.ticks = 0
		self.invested = False
		self.num[0] = 0
		self.numh = {}
		for w in range(0,6):
			self.week[w] = {}
			for j in range(1,self.depth+1):
				self.week[w][j] = 0

		for h in range(0,24):
			self.numh[h] = {}
			self.numh[h][0] = 0
		for j in range(1,self.depth+1):
			self.prev[j] = None
			self.num[j] = 0
			for h in range(0,24):
				self.numh[h][j]=0
		
		self.logger.debug("strategy ready")


	def execute_event(self, event):
		if str(event) == 'DONE':
			printStats(logging.INFO)
			return 

		if str(event) != 'CANDLE':
			return

		i = event.instrument
#		self.logger.debug("called %s %s" % (str(event), i));
		if i != self.pair:
			return ;

		ready=True
		for j in range(1,self.depth+1):
			if self.prev[j]==None:
				ready=False
		
		if ready:
			self.calculate(event)

		if self.received % self.stats_after == 0 and self.received>0:
			self.printStats(logging.DEBUG)

		for j in range(1,self.depth):
			self.prev[j] = self.prev[j+1]
		self.prev[self.depth] = event

	def calculate(self,event):
		return

	def printStats(self,lvl):
		tot = 0
		toth = {}
		for h in range(self.minTime.hour,self.maxTime.hour+1):
			toth[h] = 0
			for j in range(0,self.depth):
				if self.numh[h][j]!=None:
					toth[h] += self.numh[h][j]
	
		for j in range(0,self.depth):
			if self.num[j]!=None:
				tot += self.num[j]

		self.logger.log(lvl,"%s %s ALL TOT %d" % ( self.__class__.__name__, self.pair, tot))
		if tot>0:
			for j in range(0,self.depth):
				if self.num[j]!=None:
					self.logger.log(lvl,"%s %s ALL %d NUM: %d PERC: %6.2f" \
						% ( self.__class__.__name__, self.pair, j, self.num[j],  self.num[j] / (tot*1.0) * 100.0 ))

		for h in range(self.minTime.hour,self.maxTime.hour+1):
			for j in range(0,self.depth):
				if self.numh[h][j]!=None:
					x = 0
					if toth[h]>9:
						x=self.numh[h][j] / (toth[h]*1.0) * 100.0

					self.logger.log(lvl,"%s %s HOUR %02d-%d NUM: %d PERC: %6.2f" \
						% ( self.__class__.__name__, self.pair, h, j, self.numh[h][j], x))

		for w in range(0,6):
			totw = 0
			for j in range(1,self.depth+1):
				totw += self.week[w][j]

			if totw>0:
				for w in range(0,6):
					for j in range(1,self.depth+1):
						self.logger.log(lvl,"%s %s DAY %d-%d NUM: %d PERC: %6.2f" \
							% ( self.__class__.__name__, self.pair, w, j, self.week[w][j] \
								, self.week[w][j] / (totw*1.0) * 100.0 ))

