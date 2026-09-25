"""
The six M15 strategies of 6-STRATEGIE-M15.md, and what they have in common.

The document (strategy/6-STRATEGIE-M15.md, kept beside this file) gives six
systems for 15 minute candles with an H1 context, and a common part: closed
candles only, spread and slippage in the test, stop and target decided before
the order, size from the stop, a daily limit and a number of attempts.

H4.py already holds most of that - a closed bar, a market order at the next
open, the bracket, the volatility filter - so this class extends it with what
the M15 document adds:

* **the H1 context** is folded out of the M15 bars (lib/streaming.Hourly), the
  way H404 folds its days out of 4H bars: an hour is read once a bar of the
  next hour has arrived, so the context is one M15 bar late and never early.
* **a stop entry.** "Entra sopra il massimo della candela di conferma" is an
  order resting at the confirming bar's high - the ask's, since a buy stop
  triggers on the ask - that waits `entryBars` bars and then expires. The
  strategies that say "alla candela successiva" enter at market, as H4 does.
* **a third way out.** Early exits ("chiusura M15 dalla parte opposta di EMA
  50") and time stops ("se dopo 4-8 candele non si sviluppa un movimento
  favorevole") are not levels, so they cannot ride on the bracket. The
  strategy learns its trade is open from the fill that carries its own
  signalNumber - the way portfolio/session.TradeTimer learns from the fills -
  and closes it with a CloseTradeEvent, which the simulator and the brokers
  already answer.
* **money management** that is the strategy's to keep. One position at a time
  is portfolio/moneymanager.py's, and so is the size from the risk: the risk
  per trade (0.25-0.5% in the document) is the page's `risk` field. What is
  left is here: a minimum reward in R, a maximum number of attempts on one
  move, and a daily stop - counted in R, because the account's balance is not
  something every broker reports on a fill, and at the document's 0.5% per
  trade its 1.5% is three losing trades.

News is the page's news filter, trailing to break even is the page's trailing
stop: both are rules for every strategy already and are not repeated here.
"""

import datetime

from parity_deriva.event.event import CloseTradeEvent
from parity_deriva.lib.streaming import Hourly
from parity_deriva.strategy.H4 import H4, span

#: what the fill says when the strategy closed the trade itself: the thesis
#: broke before the stop did
EARLY_EXIT = 'EARLY_EXIT'
#: and when the trade went nowhere for as long as the strategy waits
TIME_STOP = 'TIME_STOP'


class Reading(object):
	"""
	One instrument's M15 series, and the H1 series and levels built from it.

	`hour` is the H1 bar that the last M15 bar completed, or None, so that a
	strategy that keeps something per hour knows when to advance it.
	"""

	def __init__(self, m15, h1=None, levels=None):
		self.m15 = m15
		self.h1 = h1
		self.levels = levels
		self.hourly = Hourly() if h1 is not None or levels is not None \
			else None
		self.hour = None

	def add(self, candle):
		self.m15.add(candle)
		self.hour = None
		if self.hourly is None:
			return
		self.hour = self.hourly.add(candle)
		if self.hour is None:
			return
		if self.h1 is not None:
			self.h1.add(self.hour)
		if self.levels is not None:
			self.levels.add(self.hour)

	def atr(self):
		return self.m15.atr()

	def structure(self, bars):
		"""
		+1 for higher highs and higher lows on H1, -1 for lower both, 0 for
		neither, None while there are not enough hours.

		The last `bars` hours against the `bars` before them: the new window's
		high above the old one's and its low above the old one's low is "massimi
		e minimi crescenti" said in numbers.
		"""
		window = self.h1.window(2 * bars)
		if window is None:
			return None
		old, new = window[:bars], window[bars:]
		highs = max(c.mid['h'] for c in new) - max(c.mid['h'] for c in old)
		lows = min(c.mid['l'] for c in new) - min(c.mid['l'] for c in old)
		if highs > 0 and lows > 0:
			return 1
		if highs < 0 and lows < 0:
			return -1
		return 0


class M15(H4):
	"""One strategy of the M15 document. Subclasses answer signal()."""

	GRANULARITY = 'M15'

	#: enter on a stop at the confirming bar's high (low, for a short) rather
	#: than at market on the next open, and for how many bars it waits
	STOP_ENTRY = False
	ENTRY_BARS = 1
	#: how far beyond a level or an extreme a stop sits, in M15 ATR: enough
	#: for the spread, which the document asks to be in the test
	BUFFER_ATR = 0.2
	#: a trade whose target is nearer than this many R is not taken
	MIN_REWARD = 1.0
	#: trades on one move - what a move is, each strategy says. None or 0 is
	#: no limit
	MAX_ATTEMPTS = None
	#: no new trade once the day's closed trades add up to this many R lost;
	#: 0 is no limit
	DAILY_STOP_R = 3.0
	#: close after this many bars if the trade has not gone `FOLLOW_R` in its
	#: favour; None or 0 is no time stop
	TIME_STOP_BARS = None
	FOLLOW_R = 0.5
	#: refuse a signal bar wider than this many ATR - "già eccessivamente
	#: esteso" - None or 0 is no limit
	MAX_BAR_ATR = None

	def __init__(self, **args):
		#: signalNumber -> the trade it asked for, instrument -> the trade open
		self.sent = {}
		self.held = {}
		#: (instrument, move) -> trades filled on it
		self.attempts = {}
		#: the day the R below belongs to, and the R the day has closed
		self.day = None
		self.dayR = 0.0
		self.clock = None
		H4.__init__(self, **args)

	#: what each parameter of the page's form means: a line under its field
	#: (web/service.handlerFields). Merged over the class and the classes it
	#: extends.
	PARAM_HELP = {
		'entryBars': 'how many bars a stop entry order stays live',
		'bufferAtr': 'stop buffer beyond the level, in ATR',
		'minReward': 'minimum reward to take a trade, in R',
		'dailyStopR': 'daily loss limit before trading stops, in R',
		'followR': 'trade progress needed by the time stop, in R',
		'maxAttempts': 'max trades on the same move',
		'timeStopBars': 'bars before the time stop, if going nowhere',
		'maxBarAtr': 'max width of the signal bar, in ATR',
	}

	def setup(self, args):
		self._set(args, 'entryBars', self.ENTRY_BARS)
		self._set(args, 'bufferAtr', self.BUFFER_ATR)
		self._set(args, 'minReward', self.MIN_REWARD)
		self._set(args, 'dailyStopR', self.DAILY_STOP_R)
		self._set(args, 'followR', self.FOLLOW_R)
		self.maxAttempts = self.timeStopBars = self.maxBarAtr = None
		self._set(args, 'maxAttempts', self.MAX_ATTEMPTS)
		self._set(args, 'timeStopBars', self.TIME_STOP_BARS)
		self._set(args, 'maxBarAtr', self.MAX_BAR_ATR)

	# ------------------------------------------------------ the subclass

	def signal(self, state, candle):
		"""
		A dict for a trade on this closed bar, or None.

		side, stop and target as in H4; `key`, optional, names the move the
		trade belongs to, for the attempts; anything else is kept with the
		trade and handed back to exit(). Called on every bar, open trade or
		not, so a strategy can advance what it keeps here.
		"""
		return None

	def exit(self, state, candle, held):
		"""EARLY_EXIT to close the open trade on this bar, or None."""
		return None

	# --------------------------------------------------------- the flow

	def execute_event(self, event):
		kind = str(event)
		if kind == 'TRANSACTION':
			if getattr(event, 'type', None) == 'ORDER_FILL':
				self.filled(event)
			return
		if kind != 'CANDLE' or self.otherStream(event):
			return
		if event.instrument not in self.state:
			return

		state = self.state[event.instrument]
		self.feed(state, event)
		held = self.held.get(event.instrument)
		if held is not None:
			self.follow(state, event, held)
		found = self.signal(state, event)
		if found and held is None:
			self.consider(state, event, found)

	def consider(self, state, candle, found):
		"""The shared refusals, then the order."""
		i, side = candle.instrument, found['side']
		stop, target = found['stop'], found['target']
		if self.stopped(candle.time):
			self.logger.debug("no trade: the day has lost %s R" % self.dayR)
			return
		key = found.get('key')
		if self.maxAttempts and key is not None \
				and self.attempts.get((i, key), 0) >= self.maxAttempts:
			return
		entry = self.entry(candle, side)
		risk = side * (entry - stop)
		if risk <= 0 or side * (target - entry) <= 0:
			# a stop or a target on the wrong side of the entry: the middle
			# band a reversion aims at can be behind a stop entry already
			return
		if self.minReward and side * (target - entry) < self.minReward * risk:
			self.logger.debug("no trade: target under %s R" % self.minReward)
			return
		atr = state.atr()
		if self.maxBarAtr and atr and span(candle) > self.maxBarAtr * atr:
			self.logger.debug("no trade: the bar is already %s ATR wide"
							  % (span(candle) / atr))
			return
		if not self.volatile(state):
			return
		expiry = None
		if self.STOP_ENTRY:
			# the order is placed when this bar has closed and lives through
			# `entryBars` bars: gone a second before the next one would open,
			# so the simulator and a broker let it live exactly as long
			expiry = candle.time + (1 + self.entryBars) * self.bar \
				- datetime.timedelta(seconds=1)
		se = self.emit(candle, side, stop, target,
					   entry=entry if self.STOP_ENTRY else None, expiry=expiry)
		self.sent[se.signalNumber] = dict(found, instrument=i)

	def entry(self, candle, side):
		"""The price the order carries: the close, or the bar's far end."""
		if self.STOP_ENTRY:
			return candle.ask['h'] if side > 0 else candle.bid['l']
		return candle.ask['c'] if side > 0 else candle.bid['c']

	def levels(self, candle, side, stop, ratio):
		"""A target `ratio` times the stop distance from the entry."""
		entry = self.entry(candle, side)
		return entry + side * ratio * abs(entry - stop)

	def nearer(self, candle, side, stop, level, ratio):
		"""
		The first target the trade reaches: the next level, or `ratio` R.

		The document gives both ("livello tecnico successivo", "2R, se
		raggiungibile prima del livello opposto"). With no partial close there
		is one target, so it is the one price gets to first.
		"""
		multiple = self.levels(candle, side, stop, ratio)
		if level is None:
			return multiple
		return min(level, multiple) if side > 0 else max(level, multiple)

	# ------------------------------------------------------- the trade

	def filled(self, event):
		sent = self.sent.get(getattr(event, 'signalNumber', None))
		if sent is None:
			return
		i = sent['instrument']
		if event.has_attr('tradesClosed'):
			held = self.held.pop(i, None)
			if held is not None:
				self.book(held, event)
			return
		entry = float(event.price)
		units = float(getattr(event, 'units', 0) or 0)
		self.held[i] = dict(sent, entry=entry, bars=0, best=0.0,
							risk=abs(entry - sent['stop']),
							# the side the account holds, which the page's
							# inverse can have turned round
							actual=1 if units > 0 else -1 if units < 0
							else sent['side'])
		key = sent.get('key')
		if key is not None:
			self.attempts[(i, key)] = self.attempts.get((i, key), 0) + 1

	def follow(self, state, candle, held):
		"""One more bar of an open trade: the time stop and the early exit."""
		held['bars'] += 1
		side = held['side']
		reach = candle.mid['h'] if side > 0 else candle.mid['l']
		held['best'] = max(held['best'], side * (reach - held['entry']))
		if held.get('closing'):
			return
		reason = self.exit(state, candle, held)
		if reason is None and self.timeStopBars \
				and held['bars'] >= self.timeStopBars \
				and held['best'] < self.followR * held['risk']:
			reason = TIME_STOP
		if reason is None:
			return
		held['closing'] = True
		self.logger.info("%s %s: %s at %s" % (reason, candle.instrument,
											   self.tag(), candle.time))
		self.queue_event(CloseTradeEvent({
			'instrument': candle.instrument,
			'time': candle.time,
			'reason': reason,
		}))

	def book(self, held, event):
		"""Add a closed trade's R to its day."""
		if not held['risk']:
			return
		moved = float(event.price) - held['entry']
		self.today(self.clock)
		self.dayR += held['actual'] * moved / held['risk']

	def today(self, when):
		if when is None:
			return
		if self.day != when.date():
			self.day, self.dayR = when.date(), 0.0

	def stopped(self, when):
		self.today(when)
		return bool(self.dailyStopR) and self.dayR <= -self.dailyStopR

	def feed(self, state, candle):
		"""Advance the reading. A subclass that keeps more calls this first."""
		#: the last bar seen, which is the day a closing fill is booked to
		self.clock = candle.time
		state.add(candle)
