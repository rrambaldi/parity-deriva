"""
Run a strategy over stored candles and write down the trades it made.

The pieces to do this already existed and were spread over three places: the
causally ordered driver in backtest/driver.py, the wiring
scripts/divergence_band.py assembles to collect signals, and the fuller
wiring tests/offline_test.py assembles to close the loop with no broker. What
was missing is the thing a person actually wants out of a backtest - a list of
trades, each with where it went in, where it came out, and which of the stop
and the target took it.

That list is this module. It is deliberately not part of the web service: a
ledger is what the service serves, but it is also what a script would print
and what a test can assert on, and none of that should need an HTTP request.

	from parity_deriva.backtest.ledger import run

	result = run('EUR_USD', 'H1', 'AG01',
				 datetime(2018, 1, 1), datetime(2018, 3, 3))
	for trade in result.trades:
		print(trade['key'], trade['outcome'], trade['pl'])

Two things in here are about the existing code rather than about backtesting,
and both are worth reading before trusting a number that comes out.

**The money manager remembers between runs.** `signals`, `processed`,
`onTrade` and `orderIssued` are class attributes on MoneyManager, so two runs
in one process share them: the second starts believing the first's trade is
still open and refuses every signal. Live that never shows, because a process
runs one stack and then exits. A service that runs one per request would
report a full first backtest and an empty second one, which looks like a
strategy that stopped working. moneyManager() below returns one that
remembers nothing, and web_test.py has a test whose only job is to catch a
regression here.

**Order ids inside one signal are not unique.** The money manager learns an
order's id by matching the acknowledgement's price against the orders it
issued, and in AG01 the long leg's stop is exactly the short leg's entry - so
when the long fills, the acknowledgement of its stop child matches the short
leg and overwrites that leg's id. Nothing downstream has noticed, because the
simulator cancels by price rather than by id and the order it finds first is
the right one. This ledger does not rely on it either way: it keeps its own
map, filled on a first-come basis from the ORDER events the money manager
published, and an acknowledgement that finds no free slot is a child and is
ignored.
"""

import datetime
import logging
import time

from parity_deriva.backtest.driver import Cancelled, ReplayEngine
from parity_deriva.backtest.oanda import OANDABacktester
from parity_deriva.backtest.offline import SimulatedBroker
from parity_deriva.backtest import shadow
from parity_deriva.data import calendar as calendar_module
from parity_deriva.etc import settings
from parity_deriva.portfolio.filters import EntryFilter
from parity_deriva.portfolio.moneymanager import MoneyManager
from parity_deriva.portfolio.session import SESSION_CLOSE, SessionCloser, TradeTimer
from parity_deriva.portfolio.trailer import Trailer
from parity_deriva.strategy import plugins
from parity_deriva.trading.handler import ExecutionHandler


#: The strategies that place orders, which is the only kind a ledger can be
#: written for. scripts/live.py knows a longer list: the BO engines measure
#: how long a run of candle directions persists and place nothing, so a
#: backtest of one produces no trades at all rather than an empty result worth
#: looking at.
#:
#: A strategy that is not published with this repository adds itself through
#: strategy/plugins.py, so this list is longer on a checkout that has one
#: installed and complete on one that does not.
STRATEGIES = {
	'AG01': ('parity_deriva.strategy.AG01', 'AG01'),
	'AG02': ('parity_deriva.strategy.AG02', 'AG02'),
	# AG01 with the leg that would trade into a support or a resistance
	# dropped. Its own entry because it is its own backtest, not a setting
	# of AG01's: the two answer different questions about the same candles.
	'AG01-MOD': ('parity_deriva.strategy.AG01MOD', 'AG01MOD'),
	# the five of strategy/5-STRATEGIE.md, which enter at the close of a bar
	# with a market order rather than by leaving one resting
	'H401-PULLBACK-EMA': ('parity_deriva.strategy.H401', 'H401'),
	'H402-BREAKOUT': ('parity_deriva.strategy.H402', 'H402'),
	'H403-BOLLINGER': ('parity_deriva.strategy.H403', 'H403'),
	'H404-LIVELLI-DAILY': ('parity_deriva.strategy.H404', 'H404'),
	'H405-MOMENTUM-RSI': ('parity_deriva.strategy.H405', 'H405'),
	# the six of strategy/6-STRATEGIE-M15.md: an M15 entry with an H1
	# context, some of them closing their own trade before the bracket does
	'M1501-MTP': ('parity_deriva.strategy.M1501', 'M1501'),
	'M1502-SBR': ('parity_deriva.strategy.M1502', 'M1502'),
	'M1503-SRP': ('parity_deriva.strategy.M1503', 'M1503'),
	'M1504-BMR': ('parity_deriva.strategy.M1504', 'M1504'),
	'M1505-BBO': ('parity_deriva.strategy.M1505', 'M1505'),
	'M1506-BRT': ('parity_deriva.strategy.M1506', 'M1506'),
}
STRATEGIES.update(plugins.backtest())

#: outcomes, in the vocabulary trading/parity.py and the simulator share
TAKE_PROFIT = 'TAKE_PROFIT_ORDER'
STOP_LOSS = 'STOP_LOSS_ORDER'
#: entered and never closed within the range asked for. Not an outcome the
#: broker reported - it is the data running out - so it is named differently
#: from the two that are.
STILL_OPEN = 'STILL_OPEN'


class LedgerError(Exception):
	"""A backtest that cannot be run as asked."""


def load_strategy(name):
	if name not in STRATEGIES:
		raise LedgerError(
			"no strategy %r; this runs %s. The BO engines place no orders, so "
			"there would be no trades to write down."
			% (name, ", ".join(sorted(STRATEGIES))))
	module, attr = STRATEGIES[name]
	__import__(module)
	import sys
	return getattr(sys.modules[module], attr)


def moneyManager(units=1, setup=None, risk=None, balance=None,
				 maxStopPips=None, session=None, calendar=None, slScale=None,
				 tpScale=None, inverse=False, trailing=None, trailProfit=False,
				 trailPips=None, filters=None, weekdays=None):
	"""
	A MoneyManager that remembers nothing from a previous run.

	See the module docstring: its state lives on the class, so a second run in
	the same process would start with the first one's trade still open and
	refuse every signal. Resetting on the instance shadows the class
	attributes, which is the smallest change that keeps runs independent
	without touching a component the live path depends on.
	"""
	mm = MoneyManager(setup=setup if setup is not None else settings, units=units,
					  risk=risk, balance=balance, maxStopPips=maxStopPips,
					  session=session, calendar=calendar, slScale=slScale,
					  tpScale=tpScale, inverse=inverse, trailing=trailing,
					  trailProfit=trailProfit, trailPips=trailPips, filters=filters,
					  weekdays=weekdays)
	mm.signals = {}
	mm.processed = []
	mm.onTrade = False
	mm.orderIssued = False
	return mm


#: how often Progress speaks, in seconds of wall clock. Not every bar: a
#: million fine bars would be a million calls into whatever is listening, to
#: report a line nobody can read more than a few times a second anyway.
PROGRESS_EVERY = 0.25


class Progress(ExecutionHandler):
	"""
	Says where a replay has got to, for a caller that has to wait for it.

	A run over eleven years of M5 is a million bars through the bus and a
	page that shows nothing until it ends is a page that looks broken. This
	rides the same bus and reports the two things worth seeing: which bar the
	simulator is on, and what the account is worth at that bar.

	It counts the strategy's own candles and not the fine ones the orders
	rest on - `otherStream` is the same guard every handler that counts bars
	uses - so "bar 4000 of 18878" is a share of the run and not of a stream
	the reader never asked about. The balance is read off the money manager
	rather than recomputed here: it is the account, and a second opinion
	about the account is a bug waiting for a screenshot.
	"""

	def __init__(self, report, account=None, ledger=None, instrument=None,
				 granularity=None, every=PROGRESS_EVERY):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self.report = report
		self.account = account
		self.ledger = ledger
		self.instrument = instrument
		self.granularity = granularity
		self.every = every
		self.bars = 0
		self.at = None
		self._spoke = 0.0

	def execute_event(self, event):
		if str(event) != 'CANDLE':
			return
		if self.instrument is not None and event.instrument != self.instrument:
			return
		if self.otherStream(event):
			return
		self.bars += 1
		self.at = getattr(event, 'time', None)
		now = time.time()
		if now - self._spoke < self.every:
			return
		self._spoke = now
		self.say()

	def tally(self):
		"""
		The trades closed so far, won and lost.

		Counted the way performance/report.py counts them - profit above zero
		is a win, below it a loss - so the line that runs during the backtest
		and the report that lands at the end of it cannot disagree. A trade
		still open is in neither: it has not gone anywhere yet.
		"""
		if self.ledger is None:
			return {}
		done = [one for one in self.ledger.trades()
				if one.get('exitTime') is not None]
		won = sum(1 for one in done if (one.get('pl') or 0) > 0)
		lost = sum(1 for one in done if (one.get('pl') or 0) < 0)
		# the balance after each close, in the order they closed: the capital
		# curve so far, for a page that draws it while the run is going
		curve = sorted(((one['exitTime'], one['balance']) for one in done
						if one.get('balance') is not None),
					   key=lambda point: point[0])
		return {'trades': len(done), 'won': won, 'lost': lost, 'curve': curve}

	def say(self):
		"""
		The report itself, also called once at the end so the last bar is not
		left out by the clock.

		The listener answers: anything but False and the replay carries on,
		False and it stops here. That is how a run is cancelled - there is no
		second channel and no flag on the engine, because the thing that knows
		a run should stop is the thing being told where it has got to.
		"""
		balance = getattr(self.account, 'balance', None)
		where = {'bars': self.bars, 'at': self.at,
				 'balance': None if balance is None else float(balance)}
		where.update(self.tally())
		answer = self.report(where)
		if answer is False:
			raise Cancelled("stopped after %d bars, at %s"
							% (self.bars, self.at))


class Ledger(ExecutionHandler):
	"""
	Watches the bus and writes down what happened to each signal.

	It records rather than decides: every judgement about what fills and when
	was already made by the simulator, and repeating any of it here would give
	two answers to the same question. What this adds is the join - signal to
	orders to fill to close - which no single event carries.

	The bookkeeping is per signal group, because that is what the strategies
	produce: AG01 brackets a reversal with two opposite orders, at most one of
	which becomes a trade. A group where neither filled is not a trade and is
	not listed as one; it is counted, because "the strategy signalled 93 times
	and entered 41 of them" is the first thing worth knowing about a run.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'instrument', None)
		self._set(args, 'granularity', None)

		#: the candles the run actually saw, in order. Taken off the bus
		#: rather than read from the store a second time, so the chart cannot
		#: show one window while the simulator filled against another.
		self.candles = []
		#: signalNumber -> the group of legs that signal produced
		self.groups = {}
		#: the order the groups were first seen in, so the ledger comes out
		#: chronological without sorting on a timestamp that may repeat
		self.order = []
		#: orderID -> the leg it belongs to
		self.legs = {}

	# ------------------------------------------------------------- recording

	def group(self, key):
		if key not in self.groups:
			self.groups[key] = {'key': key, 'legs': [], 'signalTime': None}
			self.order.append(key)
		return self.groups[key]

	def onCandle(self, event):
		if self.instrument is not None and event.instrument != self.instrument:
			return
		if self.granularity is not None \
				and getattr(event, 'granularity', None) != self.granularity:
			return
		self.candles.append(event)

	def onSignal(self, event):
		key = getattr(event, 'signalNumber', None)
		if key is None:
			return
		group = self.group(key)
		if group['signalTime'] is None:
			group['signalTime'] = getattr(event, 'time', None)

	def onOrder(self, event):
		"""
		One leg, as the money manager issued it.

		The levels are taken here and not from the acknowledgement: this is
		the order as the strategy meant it, before any broker had an opinion
		about it, and it is the only place the stop and the target appear
		together with the entry.
		"""
		key = getattr(event, 'signalNumber', None)
		if key is None:
			return
		group = self.group(key)
		group['legs'].append({
			'orderID': None,
			'units': float(getattr(event, 'units', 0) or 0),
			'price': _float(getattr(event, 'price', None)),
			'stopLoss': _float(getattr(event, 'stopLoss', None)),
			# where the stop ended up, for a strategy whose exit is one that
			# walks. None means it was never moved, which is not the same as
			# "it is where it started": one is a ladder that never got going
			# and the other is a record nobody kept.
			'stopFinal': None,
			'takeProfit': _float(getattr(event, 'takeProfit', None)),
			'status': 'PENDING',
			'entryTime': None,
			'entryPrice': None,
			'exitTime': None,
			'exitPrice': None,
			'outcome': None,
			'pl': None,
			'balance': None,
		})

	def onClientOrder(self, event):
		"""
		Give a leg the id the broker gave it - once.

		"Once" is the whole of the rule. The simulator acknowledges the stop
		and the target it creates on a fill with the same signal key, and in
		AG01 one of those sits at exactly the price of the other leg, so a
		match on price alone would move that leg's id onto a child order. A
		leg that already has an id is not a candidate, and an acknowledgement
		that finds no candidate is a child.
		"""
		key = getattr(event, 'signalNumber', None)
		if key is None or key not in self.groups:
			return
		price = _float(getattr(event, 'price', None))
		order_id = _int(getattr(event, 'id', None))
		if order_id is None:
			return
		for leg in self.groups[key]['legs']:
			if leg['orderID'] is None and leg['price'] == price:
				leg['orderID'] = order_id
				self.legs[order_id] = leg
				return

	def onCancel(self, event):
		order_id = _int(getattr(event, 'orderID', None))
		leg = self.legs.get(order_id)
		if leg is not None and leg['status'] == 'PENDING':
			leg['status'] = 'CANCELED'

	def onTransaction(self, event):
		if str(getattr(event, 'type', '')) == 'ORDER_REJECT':
			return self.onReject(event)
		if event.has_attr('tradesClosed'):
			return self.onClose(event)
		return self.onFill(event)

	def onFill(self, event):
		"""
		An opening fill - if it belongs to a leg.

		The simulator reports every order it fills, children included, so a
		fill whose id is not one of the ids this ledger handed out is the stop
		or the target doing its job. That same event comes back a moment later
		as the close, carrying the leg it belongs to, which is where it is
		recorded.
		"""
		leg = self.legs.get(_int(getattr(event, 'orderID', None)))
		if leg is None or leg['status'] != 'PENDING':
			return
		leg['status'] = 'FILLED'
		leg['entryTime'] = getattr(event, 'time', None)
		leg['entryPrice'] = _float(getattr(event, 'price', None))
		leg['outcome'] = STILL_OPEN

	def onClose(self, event):
		leg = self.legs.get(_int(getattr(event, 'orderID', None)))
		if leg is None:
			return
		leg['status'] = 'CLOSED'
		leg['exitTime'] = getattr(event, 'time', None)
		leg['exitPrice'] = _float(getattr(event, 'price', None))
		leg['outcome'] = getattr(event, 'reason', None)
		leg['pl'] = _float(getattr(event, 'pl', None))
		leg['balance'] = _float(getattr(event, 'accountBalance', None))

	def onStopModify(self, event):
		"""
		Follow the stop of an open trade as portfolio/trailer.py walks it.

		Was: nothing read these. The leg kept the stop its order was issued
		     with, so a trade that exited on a stop the trailer had moved was
		     written down next to a level it had not been at for days - and
		     the strategies whose whole exit is that ladder (they set no take
		     profit at all) reported every close as STOP_LOSS_ORDER against a
		     price the exit did not match. Reading the table, a winning trade
		     looked like a bug in the simulator.
		Now: the level is recorded as it moves, and the exit can be checked
		     against the stop that was actually standing when it happened.

		By the entry order's id, which is the one the trailer addresses: see
		backtest/oanda.modifyStop, which moves the same order for the same
		reason. Only an open leg is followed - a stop that has already been
		taken is not moved by the simulator either, and a late event must not
		rewrite what a closed trade exited on.
		"""
		leg = self.legs.get(_int(getattr(event, 'orderID', None)))
		if leg is None or leg['status'] != 'FILLED':
			return
		leg['stopFinal'] = _float(getattr(event, 'price', None))

	def onReject(self, event):
		key = getattr(event, 'signalNumber', None)
		if key not in self.groups:
			return
		price = _float(getattr(event, 'price', None))
		for leg in self.groups[key]['legs']:
			if leg['price'] == price and leg['status'] == 'PENDING':
				leg['status'] = 'REJECTED'
				return

	def execute_event(self, event):
		kind = str(event)
		if kind == 'CANDLE':
			return self.onCandle(event)
		if kind == 'SIGNAL':
			return self.onSignal(event)
		if kind == 'ORDER':
			return self.onOrder(event)
		if kind == 'CLIENTORDER':
			return self.onClientOrder(event)
		if kind == 'ORDERCANCEL':
			return self.onCancel(event)
		if kind == 'STOPMODIFY':
			return self.onStopModify(event)
		if kind == 'TRANSACTION':
			return self.onTransaction(event)

	# -------------------------------------------------------------- the list

	def trades(self):
		"""
		One record per leg that became a position, in the order they opened.

		A group whose legs all died produced no trade and is not here. It is
		not lost either - counts() has it, because a strategy that signals
		ninety times and enters forty is telling you something about itself
		that the forty cannot.
		"""
		out = []
		for key in self.order:
			group = self.groups[key]
			for leg in group['legs']:
				if leg['entryTime'] is None:
					continue
				out.append({
					'key': key,
					'instrument': self.instrument,
					'granularity': self.granularity,
					'direction': 'long' if leg['units'] > 0 else 'short',
					'units': leg['units'],
					'signalTime': group['signalTime'],
					'orderPrice': leg['price'],
					'stopLoss': leg['stopLoss'],
					'stopFinal': leg['stopFinal'],
					'takeProfit': leg['takeProfit'],
					'entryTime': leg['entryTime'],
					'entryPrice': leg['entryPrice'],
					'exitTime': leg['exitTime'],
					'exitPrice': leg['exitPrice'],
					'outcome': leg['outcome'],
					'pl': leg['pl'],
					# the result in risk units: -1 is a trade that lost what
					# its initial stop put at risk, whatever the size
					'r': rMultiple(leg['pl'], leg['entryPrice'], leg['stopLoss'], leg['units']),
					'balance': leg['balance'],
				})
		out.sort(key=lambda t: (t['entryTime'], t['key']))
		return out

	def counts(self):
		"""What became of every signal, whether or not it became a trade."""
		entered = closed = 0
		for group in self.groups.values():
			for leg in group['legs']:
				if leg['entryTime'] is not None:
					entered += 1
					if leg['status'] == 'CLOSED':
						closed += 1
		return {
			'signals': len(self.groups),
			'entered': entered,
			'closed': closed,
			'stillOpen': entered - closed,
			'neverEntered': len(self.groups) - entered,
			'candles': len(self.candles),
		}


class Result(object):
	"""What one backtest produced, with nothing computed from it yet."""

	def __init__(self, instrument, granularity, strategy, dtfrom, dtto,
				 candles, trades, counts, balance=None, risk=None, fine=None,
				 maxStopPips=None):
		self.instrument = instrument
		self.granularity = granularity
		self.strategy = strategy
		self.dtfrom = dtfrom
		self.dtto = dtto
		self.candles = candles
		self.trades = trades
		self.counts = counts
		#: what the account started with, which is where a trade's 'balance'
		#: is counted from. Reported because a curve of balances is not
		#: readable without the level it began at.
		self.balance = balance
		#: the fraction of capital risked per trade, or None when the size
		#: was a fixed number of units
		self.risk = risk
		#: the finer granularity the orders were filled against, or None when
		#: they were filled against the strategy's own bars. Reported because
		#: two runs of the same strategy over the same window are not the same
		#: measurement when one of them resolved its exits a minute at a time.
		self.fine = fine
		#: the widest stop a signal was allowed to carry, in pips, or None.
		#: Reported for the same reason as the risk: it decides which trades
		#: are in the list, not only how they were sized.
		self.maxStopPips = maxStopPips


def run(instrument, granularity, strategy='AG01', dtfrom=None, dtto=None,
		units=1, setup=None, source=None, balance=None, risk=None, fine=True,
		maxStopPips=None, progress=None, session=None, intraday=False,
		closeAt=None, news=None, newsImpacts=None, maxBars=None,
		strategyArgs=None, slScale=None, tpScale=None, inverse=False,
		trailing=None, trailProfit=False, trailPips=None, filters=None, weekdays=None):
	"""
	Replay stored candles through the whole offline stack and collect trades.

	`weekdays` is the days a signal is taken on, '12345' Monday to Friday
	(portfolio/moneymanager.py), None every day.

	`filters` is an entry filter's conditions, 'rsi14<55&hour>=7'
	(portfolio/filters.py): read off the strategy's own bars, asked by the
	money manager when a signal comes.

	The wiring is the one tests/offline_test.py pins: strategy, money manager,
	simulator, and the adapter that says "the simulator is the broker here".
	The driver is the causally ordered one, which is not an optimisation - a
	threaded engine replaying from memory dispatches every candle of the month
	before the first order derived from the first candle exists, and reports
	no trades at all.

	`source` is there for tests, which have candles of their own and no store.

	`balance` is what the simulated account starts with. settings.EQUITY is
	the default, which is the same figure the live portfolio starts from.

	`risk` chooses how a position is sized, and it is the one argument here
	that changes which trades are taken rather than only how they are
	counted:

	* None - `units` is the size, fixed, the way this has always worked. The
	  starting balance then does nothing but set the level of the curve.
	* a fraction - a trade is sized so that exiting on its stop costs that
	  fraction of the capital, and the capital is re-read from the account at
	  the start of each calendar month. `units` is ignored. A signal carrying
	  no stop cannot be sized and is not sent, so a strategy that sets none
	  trades nothing at all this way.

	`fine` chooses the bars the orders are filled against, which is not the
	same question as the bars the strategy reads:

	* True, the default - the finest series the store holds that covers this
	  window, and the strategy's own bars when it holds nothing finer. See
	  backtest/shadow.py: a day's candle cannot say whether the stop or the
	  target came first and a minute's usually can.
	* a granularity - that one, whether or not it is the finest.
	* False - the strategy's own bars, which is what this did before the
	  choice existed.

	It changes the answer, not only its precision: a trade whose stop and
	target both sat inside one daily bar was decided by the simulator's
	tie-break and is now decided by the market.

	`session`, `intraday` and `news` are when this account trades, and they
	are rules about the account rather than about a setup - which is why they
	are arguments here and not edits to five strategies:

	* `session` - ('07:00', '16:00') in UTC, or None for the whole day. It
	  refuses the *signal* and not the order: an order placed inside the
	  window rests until it fills or expires.
	* `intraday` - close whatever is open when the day ends, at the close of
	  the bar the cut falls inside. The cut is `closeAt`, or the session's
	  own end, or the end of the UTC day. See portfolio/session.py.
	* `news` - (minutes before, minutes after) around each event of the
	  calendar in DATA_DIR, on the instrument's own currencies. A signal
	  inside one of those windows is refused. `newsImpacts` says which events
	  count and defaults to the high impact ones. No calendar file, or no
	  minutes: no rule, and the run is what it was before.

	`maxStopPips` is the widest stop a signal may carry and still be traded,
	in pips, or None for no ceiling. It applies to every strategy, because it
	is a rule about the account rather than about a setup - see
	MoneyManager.tooWide, which refuses the signal rather than pulling its
	stop in.

	`maxBars` closes a trade that has been open that many of the strategy's
	own bars (portfolio/session.TradeTimer), or None for no limit.

	`slScale` and `tpScale` multiply the distance of the initial stop and
	target from the entry (MoneyManager.scaleLevels); None leaves them.

	`inverse` turns every order round, `trailing` is None for the strategy's
	own stop, 0 for one that never moves and 1 for one that follows, and
	`trailProfit` makes the target a floor the stop follows from: see
	MoneyManager.turnRound and MoneyManager.trail. `trailPips` is how far
	behind the stop follows, in pips, instead of the initial stop's distance.

	`strategyArgs` are keyword arguments for the strategy's constructor -
	the numbers it reads through _set() - or None for its own defaults.
	"""
	cfg = setup if setup is not None else settings
	balance = float(cfg.EQUITY) if balance is None else float(balance)
	strategy_class = load_strategy(strategy)
	dtfrom = dtfrom if dtfrom is not None else datetime.datetime(1970, 1, 1)
	dtto = dtto if dtto is not None else datetime.datetime.today()

	# the stream the orders rest on, which is the strategy's own unless the
	# store holds something finer. Given a source, a caller brought its own
	# candles and there is no store to look in.
	if source is not None:
		fine = None
	elif fine is True:
		fine = shadow.finer(instrument, granularity, dtfrom, dtto, cfg)
	elif not fine:
		fine = None

	ledger = Ledger(instrument=instrument, granularity=granularity)
	diary = _calendar(instrument, news, newsImpacts, cfg)
	entry = EntryFilter(filters, granularity=granularity, instrument=instrument) if filters else None
	manager = moneyManager(units=units, setup=cfg, risk=risk, balance=balance,
						   maxStopPips=maxStopPips, session=session,
						   calendar=diary, slScale=slScale, tpScale=tpScale,
						   inverse=inverse, trailing=trailing,
						   trailProfit=trailProfit, trailPips=trailPips, filters=entry,
						   weekdays=weekdays)
	# the cut the day ends at: what was asked for, the session's own end, or
	# the end of the UTC day. Nothing here invents an hour of its own
	closer = None
	if intraday:
		closer = SessionCloser(
			at=closeAt or (session[1] if session else '23:59'),
			granularity=granularity, instrument=instrument)
	timer = TradeTimer(maxBars, granularity=granularity,
					   instrument=instrument) if maxBars else None
	# `progress` is a callback and not a flag: this module says where the
	# replay is and what the account holds, and whoever asked decides whether
	# that is a line on a page, a log, or nothing at all
	watcher = None if progress is None else Progress(
		progress, account=manager, ledger=ledger, instrument=instrument,
		granularity=granularity)
	engine = ReplayEngine()
	for handler in ((entry,) if entry is not None else ()) + (
					strategy_class(pairs=[instrument], granularity=granularity,
								   **(strategyArgs or {})),
					manager,
					# Before the simulator, so that a stop moved on this bar
					# applies from the next one: the ladder is read off a bar
					# that has closed, and a stop that could be moved and
					# then taken within the same bar would be reading the
					# future. Inert unless an order carries a ladder, so AG01
					# and AG02 are unaffected by its being here.
					Trailer(granularity=granularity),
					# named: with two granularities of one instrument on the
					# bus the simulator has no way to tell which stream it
					# should fill against
					OANDABacktester(setup=cfg, granularity=fine or granularity,
									balance=balance),
					SimulatedBroker(),
					ledger) \
				+ ((closer,) if closer is not None else ()) \
				+ ((timer,) if timer is not None else ()) \
				+ ((watcher,) if watcher is not None else ()):
		engine.add_handler(handler)

	if source is None:
		source = shadow.source(instrument, granularity, fine, dtfrom, dtto,
							   cfg, progress=_reading(progress))
	engine.run(source)
	if watcher is not None:
		# the clock may have swallowed the last few thousand bars, and a run
		# that ends at 97% reads as a run that stopped
		watcher.say()

	return Result(instrument, granularity, strategy, dtfrom, dtto,
				  ledger.candles, ledger.trades(), ledger.counts(), balance,
				  risk, fine, maxStopPips)


def _calendar(instrument, news, impacts, cfg):
	"""
	The events this run stands aside for, or None.

	Loaded here rather than handed in, so that a backtest from the command
	line and one from the page read the same file the same way. No minutes
	means no rule, and no rule means no file is read at all: a run that was
	not asked to watch the news does not depend on whether a calendar has
	ever been imported.
	"""
	if not news:
		return None
	before, after = news
	if not before and not after:
		return None
	frame = calendar_module.load(setup=cfg)
	if not len(frame):
		return None
	return calendar_module.Calendar(
		frame, currencies=calendar_module.currencies(instrument),
		impacts=tuple(impacts) if impacts else (calendar_module.HIGH,),
		before=before, after=after)


def _reading(report):
	"""
	The reading phase, in the shape the rest of a run's progress arrives in.

	data/replay.py knows how many rows it has read and nothing about who
	wants to know; this turns its three arguments into the same dictionary
	ledger.Progress sends, marked as the phase it is, and turns a listener's
	refusal into the same Cancelled a refusal during the replay raises.
	"""
	if report is None:
		return None

	def reading(stage, done, total):
		answer = report({'loading': True, 'stage': stage, 'read': done,
						 'toRead': total})
		if answer is False:
			raise Cancelled("stopped while %s, after %d of %d candles"
							% (stage, done, total))
	return reading


def rMultiple(pl, entry, stop, units):
	"""pl over what the initial stop risked, or None with no stop or no result."""
	if pl is None or entry is None or stop is None or not units or entry == stop:
		return None
	return pl / (abs(entry - stop) * abs(units))


def _float(value):
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _int(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return None
