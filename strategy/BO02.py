import copy
import logging
import datetime
from parity_deriva.event.event import SignalEvent
from parity_deriva.trading.handler import ExecutionHandler


class BO02(ExecutionHandler):
	"""
	"""
	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')

		self.stats_after = 500
		self.prev = {}
		self.received = 0
		self.num = {}
		self._set(args,'pair', 'EUR_USD' )
		self._set(args,'granularity', 'M5')
		self._set(args,'gtdTime', "23:59:59")
		self._set(args,'minTime', "09:30:00")
		self._set(args,'maxTime', "18:00:00")
		self._set(args,'depth', 10)

		self.minTime = datetime.datetime.strptime(self.minTime,"%H:%M:%S")
		self.maxTime = datetime.datetime.strptime(self.maxTime,"%H:%M:%S")

		if self.gtdTime is not None:
			self.gtdTime = self.gtdTime.split(':')

		self.ticks = 0
		self.invested = False
		self.num[0] = 0
		self.numh = {}
		self.week = {}
		for w in range(0,7):
			self.week[w] = {}
			for j in range(0,self.depth+1):
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
			self.received+=1
			p=self.prev
			dbg = ""
			for j in range(1,self.depth+1):
				dbg = "%s %s" % ( dbg, p[j].direction())

#			self.logger.debug("SEQ: %s" % dbg)
###### PATTERN
#  G R R
			if p[1].time.hour >= self.minTime.hour and p[1].time.hour<=self.maxTime.hour and \
			p[1].direction()<0 and p[2].direction()>0 and p[3].direction()>0:
				out=0
				for j in range(4,self.depth+1):
					out += 1
					if p[j].direction()>0:
						break
				else:
					out = 0	# never resolved inside the window
				if out==0:
					self.logger.debug("=========== DEAD")
				self.logger.debug("BO02 %s EVENT P1 %s : IN %d" % (i, p[1].time, out))
				self.num[out] += 1
				self.numh[p[1].time.hour][out] += 1
				self.week[p[1].time.weekday()][out] += 1
#		p = self.prev[i]
#		self.logger.debug("CURR: %s" % event.price_str())
#		self.logger.debug("PREV: %s" % p.price_str())
#		if p.direction()==event.direction():
#			self.prev[i] = event
#			return ;
		if self.received % self.stats_after == 0 and self.received>0:
			self.printStats()

		for j in range(1,self.depth):
			self.prev[j] = self.prev[j+1]
		self.prev[self.depth] = event

	def printStats(self):
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

		if tot>0:
			for j in range(0,self.depth):
				if self.num[j]!=None:
					self.logger.debug("BO02 %s ALL %d NUM: %d PERC: %6.2f" % ( self.pair, j, self.num[j],  self.num[j] / (tot*1.0) * 100.0 ))

		for h in range(self.minTime.hour,self.maxTime.hour+1):
			for j in range(0,self.depth):
				if self.numh[h][j]!=None:
					x = 0
					if toth[h]>9:
						x=self.numh[h][j] / (toth[h]*1.0) * 100.0

					self.logger.debug("BO02 %s HOUR %02d-%d NUM: %d PERC: %6.2f" % ( self.pair, h, j, self.numh[h][j], x))


		for w in range(0,7):
			totw = 0
			for j in range(0,self.depth+1):
				totw += self.week[w][j]

			if totw>0:
				for j in range(0,self.depth+1):
					self.logger.debug("BO02 %s DAY %d-%d NUM: %d PERC: %6.2f" % ( self.pair, w, j, self.week[w][j], self.week[w][j] / (totw*1.0) * 100.0 ))


