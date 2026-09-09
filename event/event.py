
import datetime
import json
import pandas as pd
from parity_deriva.lib.candle import Candle


def parse_time(v):
	"""
	Parse an event timestamp. OANDA sends "%Y-%m-%dT%H:%M:%S.%f000Z"; the
	event log written by EventSaver stores datetime.isoformat(). Accept both
	(and datetime objects unchanged) so a saved log replays with its real
	timestamps instead of the 1970-01-01 fallback.
	"""
	if isinstance(v, datetime.datetime):
		return v
	try:
		return datetime.datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%f000Z")
	except (TypeError, ValueError):
		pass
	try:
		return datetime.datetime.fromisoformat(str(v).replace('Z', '+00:00'))
	except (TypeError, ValueError):
		return datetime.datetime(1970,1,1,0,0,0)

class Event(object):

	def __init__(self, data=None):
		self._type = self.__class__.__name__.replace('Event','').upper()
		self._created = datetime.datetime.today()

		if data is None:
			return

		if (isinstance(data,dict)):
			for k in data:
				self.__set__(k,data[k])
			return

		self.__set__(self._type.lower(), data)

	def __set__(self,k,v):
		if k in ['price','h','l','c','o']:
			try:
				v = float(v)
			except:
				v = "0.0"

		if k=='time':
			v = parse_time(v)

		setattr(self,k,v)

	def __get__(self,k):
		if k in self.__dict__:
			return self.__dict__[k]
		return None

	def __str__(self):
		return self._type

	def __repr__(self):
		return str(self)

	def has_attr(self, k):
		return k in self.__dict__

	def to_dict(self):
		d = {}
		for k in self.__dict__:
			if k in ['_created','_type']:
				continue
			d[k] = self.__dict__[k]
		return d

	def to_json(self, _all=False):
		d = {}
		for k in self.__dict__:
			if not _all and k in ['_created','_type']:
				continue
			if isinstance(self.__dict__[k], datetime.datetime):
				d[k] = self.__dict__[k].isoformat()
				continue
			d[k]=self.__dict__[k]
		return json.dumps(d)


	def dump(self):
		txt = "%s @%s\n" % (self._type, self._created)
		for k in self.__dict__:
			if k in ['_type','_created']:
				continue
			txt += "%s: %s\n" % (k, self.__dict__[k])
		return txt

class CandleEvent(Event):
	bid = None
	ask = None
	mid = None
	time = None
	volume = 0
	complete = False

	def __set__(self,k,v):
		if k in ['ask','bid','mid']:
			new = {}
			for p in ['h','l','c','o']:
				try:
					new[p] = float(v[p])
				except:
					new[p] = "0.0"
			v = new

		if k=='time':
			v = parse_time(v)

		setattr(self,k,v)

	def price(self, price='M'):
		if price=='M':
			return self.mid
		if price=='A':
			return self.ask
		if price=='B':
			return self.bid
		return self.mid

	def direction(self, price='M'):
		c=self.price()
		if c['o'] > c['c']:
			return -1
		if c['o'] < c['c']:
			return 1
		return 0

	def isBear(self, price='M'):
		c=self.price()
		return c['c'] < c['o']

	def isBull(self, price='M'):
		c=self.price()
		return c['c'] > c['o']

	def vola(self, price='M'):
		c=self.price()
		return c['h'] - c['l']

	def ratio(self, price='M'):
		c=self.price()
		return abs((c['o']-c['c'])/(c['h'] - c['l']))

	def price_str(self, price='M'):
		c=self.price()
		return "T:%s %s" % (self.time, str(c))

	def dump(self):
		txt = "CANDLE T: %s V: %d C: %s" % (self.time, self.volume, self.complete)
		if self.ask:
			txt = "%s\nASK: %s" % (txt,str(self.ask))
		if self.bid:
			txt = "%s\nBID: %s" % (txt,str(self.bid))
		if self.mid:
			txt = "%s\nMID: %s" % (txt,str(self.mid))

		return txt

	def to_dict(self):
		res = {}
		res['volume'] = int(self.volume)
		for x in ['ask','bid','mid']:
			for p in ['c','h','l','o']:
				res[ "%s_%s" % (x,p) ] = float(self.__dict__[x][p])
		return res

	def dataframe(self):
		dat = self.to_dict()
		idx = pd.date_range( self.time.strftime('%Y-%m-%d %H:%M:%S'), periods=1)
		return pd.DataFrame(dat, index=idx)

class TransactionEvent(Event):
	def info(self):
		txt= "TRANSACTION"
		for k in self.__dict__.keys():
			txt = "%s\n %s: %s" % ( txt, k, self.__dict__[k] )
		return txt

	def __repr__(self):
		return str(self)

class TickEvent(Event):
	def info(self):
		return "TICK Type: %s, Instrument: %s, Time: %s, Bid: %s, Ask: %s" % (
			str(self.type), str(self.instrument), 
			str(self.time), str(self.bid), str(self.ask)
		)

class SignalEvent(Event):
	def info(self):
		return "SIGNAL %s %s %s %d @%f %s %s" % (
			self.instrument
			, ( "BUY" if self.units > 0 else "SELL" )
			, self.orderType
			, abs(self.units)
			, self.price
			, ( "SL@" + str(self.stopLoss) if self.stopLoss else "" )
			, ( "TP@" + str(self.takeProfit) if self.takeProfit else "" )
		)


class ClientOrderEvent(Event):
	pass

class OrderFillEvent(Event):
	pass

class OrderCancelEvent(Event):
	def info(self):
		return "ORDER CANCEL orderID: %d" % self.orderID

class OrderEvent(Event):
	def info(self):
		return "ORDER %s %s %s %d @%f %s %s" % (
			self.instrument
			, ( "BUY" if self.units > 0 else "SELL" )
			, self.orderType
			, abs(self.units)
			, self.price
			, ( "SL@" + str(self.stopLoss) if self.stopLoss else "" )
			, ( "TP@" + str(self.takeProfit) if self.takeProfit else "" )
		)


class StatusEvent(Event):
	def info(self):
		return "STATUS msg: %s" % ( self.status )

