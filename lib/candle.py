
class Candle(object):
	o = 0
	h = 0
	l = 0
	c = 0

	def __init__(self, o=0, h=0, l=0, c=0):
		if  isinstance(o, dict):
			self.set( o)
			return

		if o:
			self.o = float(o)
		if h:
			self.h = float(h)
		if l:
			self.l = float(l)
		if c:
			self.c = float(c)

	def set(self, candle):
		for k in [ 'o', 'h', 'l', 'c' ]:
			if k in candle:
				setattr(self, k, float(candle[k]))

	def __str__(self):
		return "O: %f H: %f L: %f C: %f" % ( self.o, self.h, self.l, self.c )

