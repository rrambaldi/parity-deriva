
import datetime

class ohlc(object):
	o = 0.0
	h = 0.0
	l = 0.0
	c = 0.0
	t = None
	v = 0

	def __init__(self, t=None, o=0.0, h=0.0, l=0.0, c=0.0, v=0):
		if isinstance(t, dict):
			self.from_dict(t)
			return
		self.from_vals(t,o,h,l,c,v)

	def direction(self):
		if self.o > self.c:
			return -1
		if self.o < self.c:
			return 1
		return 0

	def isBear(self):
		return self.c < self.o

	def isBull(self):
		return self.c > self.o

	def vola(self):
		return self.h - self.l

	def ratio(self):
		return abs((self.o-self.c)/(self.h - self.l))

	def from_oanda(self, dct, typ):
		self.o=float(dct['type']['o'])
		self.h=float(dct['type']['h'])
		self.l=float(dct['type']['l'])
		self.c=float(dct['type']['c'])
		self.v=int(dct['volume'])
		self.t = datetime.datetime.strptime(dct['time'], "%Y-%m-%dT%H:%M:%S.%f000Z")

	def from_dict(self,dct,tm=None):
		if tm is None and 'time' in dct:
			tm = dct['time']
		vol=None
		if 'volume' in dct:
			vol=dct['volume']
		self.from_vals(tm, dct['o'], dct['h'], dct['l'], dct['c'], vol)

	def from_vals(self,t=None,o=0.0, h=0.0, l=0.0, c=0.0, v=0):
		self.o=float(o)
		self.h=float(h)
		self.l=float(l)
		self.c=float(c)
		self.v=int(v or 0)
		if t is not None and not isinstance(t,datetime.datetime):
			self.t = datetime.datetime.strptime(t, "%Y-%m-%dT%H:%M:%S.%f000Z")
		else:
			self.t = t

	def __str__(self):
		return "%s O:%s H:%s L:%s C:%s V:%d" % (self.t, self.o, self.h, self.l, self.c, self.v)


	def to_dict(self, only_price=False):
		if only_price:
			return { 'o': self.o
					, 'h': self.h
					, 'l': self.l
					, 'c': self.c
				}

		ret = {}
		ret[self.t] = { 'o': self.o
					, 'h': self.h
					, 'l': self.l
					, 'c': self.c
		}
		return ret


