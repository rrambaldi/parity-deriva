import copy
import logging
import datetime
from parity_deriva.event.event import SignalEvent
from parity_deriva.trading.handler import ExecutionHandler
from parity_deriva.lib.utils import expiryAt, roundPrice, signalNumber


class AG01(ExecutionHandler):
	#: what the page selects when this strategy is picked; the constructor
	#: defaults to the same two, so there is one place to change them
	INSTRUMENT = 'EUR_USD'
	GRANULARITY = 'M5'

	#: What this strategy does, in the words the chart prints over it. Read
	#: off the class rather than kept in the page, for the same reason
	#: INDICATORS is: a description in the viewer is a description that stops
	#: being true the first time the rule below changes and nobody looks.
	DESCRIPTION = (
		"Breakout. Due candele di colore opposto sono il segnale, e la coppia "
		"viene incassata fra due ordini stop opposti: buy stop sul massimo delle "
		"due letto sull'ask, sell stop sul minimo letto sul bid. Ne entra uno "
		"solo, quello che il mercato tocca per primo; l'altro viene cancellato. "
		"STOP: l'estremo opposto della coppia - il buy ha lo stop sul minimo, il "
		"sell sul massimo, quindi il rischio è l'ampiezza delle due candele. "
		"TAKE PROFIT: 1,2 volte quella distanza oltre l'ingresso, più lo spread "
		"di chiusura della candela di segnale. Entrambi gli ordini scadono a "
		"fine giornata della candela che li ha generati: su barre giornaliere "
		"vuol dire che hanno la barra successiva per entrare, e nient'altro.")

	#: What a signal of this strategy is tagged with, in its key and in the
	#: extension the broker echoes back. None is the class's own name, which
	#: is what it has always been and what every join between a live run and
	#: its replay is made on. A variant that is this rule with a filter on it
	#: - AG01MOD - names itself here rather than being read as a different
	#: strategy under a name nobody chose.
	TAG = None

	#: How many bars back from the signal the entry rule reads: the candle
	#: that changed colour and the one before it, whose high and low are the
	#: two orders' prices. The chart boxes them when the trade is selected.
	SETUP_BARS = 2

	#: The curves the chart draws over this strategy's candles. Empty, and
	#: said out loud rather than left absent: this strategy reads two bars
	#: against each other and no average at all, so a moving average on its
	#: chart would be decoration - a line nobody traded, next to trades
	#: somebody did. lib/indicators.py has the shapes when one is wanted.
	INDICATORS = ()

	"""
	"""
	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')

		self.prev = {}
		self._set(args,'pairs', [ self.INSTRUMENT ])
		self._set(args,'granularity', self.GRANULARITY)
		self._set(args,'gtdTime', "23:59:59")

		if self.gtdTime is not None:
			self.gtdTime = self.gtdTime.split(':')

		self.ticks = 0
		self.invested = False
		for i in self.pairs:
			self.prev[i] = None
		self.logger.debug("strategy ready")


	def tag(self):
		return self.TAG or self.__class__.__name__

	def allow(self, units, prev, event):
		"""
		May this leg be sent? Both of them, always.

		The hook exists for AG01MOD, which drops the leg that would trade
		into a level. It is asked once per leg and handed the two bars the
		setup is made of, so a filter can read nothing the rule that produced
		the leg could not.
		"""
		return True

	def execute_event(self, event):
		if str(event) != 'CANDLE' or self.otherStream(event):
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
		sb.signalNumber = signalNumber(self.tag(), i,
				self.granularity, event.time)
		sb.clientExtension = { 'id': sb.signalNumber
				, 'tag': self.tag()
				, 'comment': '%s' % self.granularity
			}
		sb.signalType = 'EXCLUSIVE'
		sb.orderType = "STOP"
		sb.instrument = i
		# from the candle, not from the machine's clock: see
		# lib/utils.expiryAt for the replay this was silently breaking
		sb.gtdTime = expiryAt(event.time, self.granularity, self.gtdTime)
		sb.time = event.time
		sb.stopLoss = min(p.bid['l'], event.bid['l'])
		sb.price = max(p.ask['h'], event.ask['h'])
		sb.takeProfit = roundPrice(i, sb.price + ( sb.price - sb.stopLoss ) * 1.2 + spread)
		sb.units = 1 ## buy
		sb.type = 'STOP'
		if self.event_queue is not None and self.allow(1, p, event):
			self.queue_event(sb)
			self.logger.debug( "SENT %s" % sb.info() )

		ss = SignalEvent( sb.to_dict() )
		ss.stopLoss = max(p.ask['h'], event.ask['h'])
		ss.price = min(p.bid['l'], event.bid['l'])
		ss.takeProfit = roundPrice(i, ss.price - ( ss.stopLoss - ss.price ) * 1.2 + spread)
		ss.units = -1  ## SELL

		if self.event_queue is not None and self.allow(-1, p, event):
			self.queue_event(ss)
			self.logger.debug( "SENT %s" % ss.info() )

		self.prev[i] = event

