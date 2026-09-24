import copy
import logging
import datetime
from parity_deriva.event.event import SignalEvent
from parity_deriva.trading.handler import ExecutionHandler
from parity_deriva.lib.utils import expiryAt, roundPrice, signalNumber


class AG02(ExecutionHandler):
	#: what the page selects when this strategy is picked; see AG01; the constructor
	#: defaults to the same two, so there is one place to change them
	INSTRUMENT = 'EUR_USD'
	GRANULARITY = 'M5'

	#: See AG01.DESCRIPTION for why this lives on the class.
	DESCRIPTION = (
		"Mean reversion: lo stesso segnale di AG01 - due candele di colore "
		"opposto - preso dalla parte opposta, con ordini limite invece che stop. "
		"Sell limit sul massimo delle due (ask) e buy limit sul minimo (bid): si "
		"vende la rottura in alto e si compra quella in basso, aspettando che "
		"rientri. STOP e TAKE PROFIT sono scambiati rispetto ad AG01: il TAKE "
		"PROFIT è l'estremo opposto della coppia, cioè si punta all'altra "
		"estremità delle due candele; lo STOP sta 1,2 volte quella distanza "
		"oltre l'ingresso, più lo spread. Rischio più largo del target, quindi, "
		"che è il prezzo di un sistema che punta a rientrare. Scadenza a fine "
		"giornata come AG01.")

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
		sb.signalNumber = signalNumber(self.__class__.__name__, i,
				self.granularity, event.time)
		sb.clientExtension = { 'id': sb.signalNumber
				, 'tag': self.__class__.__name__
				, 'comment': '%s' % self.granularity
			}
		sb.signalType = 'EXCLUSIVE'
		sb.orderType = "LIMIT"
		sb.instrument = i
		# from the candle, not from the machine's clock: see
		# lib/utils.expiryAt for the replay this was silently breaking
		sb.gtdTime = expiryAt(event.time, self.granularity, self.gtdTime)
		sb.time = event.time
		sb.takeProfit = min(p.bid['l'], event.bid['l'])
		sb.price = max(p.ask['h'], event.ask['h'])
		sb.stopLoss = roundPrice(i, sb.price + ( sb.price - sb.takeProfit ) * 1.2 + spread)
		sb.units = -1 ## sell
		sb.type = 'LIMIT'
		if self.event_queue is not None:
			self.queue_event(sb)
			self.logger.debug( "SENT %s" % sb.info() )

		ss = SignalEvent( sb.to_dict() )
		ss.takeProfit = max(p.ask['h'], event.ask['h'])
		ss.price = min(p.bid['l'], event.bid['l'])
		ss.stopLoss = roundPrice(i, ss.price - ( ss.takeProfit - ss.price ) * 1.2 + spread)
		ss.units = +1  ## SELL

		if self.event_queue is not None:
			self.queue_event(ss)
			self.logger.debug( "SENT %s" % ss.info() )

		self.prev[i] = event

