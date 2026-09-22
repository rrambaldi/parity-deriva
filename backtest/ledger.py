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

from parity_deriva.backtest.driver import ReplayEngine
from parity_deriva.backtest.oanda import OANDABacktester
from parity_deriva.backtest.offline import SimulatedBroker
from parity_deriva.data.replay import ForexCandles
from parity_deriva.etc import settings
from parity_deriva.portfolio.moneymanager import MoneyManager
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


def moneyManager(units=1, setup=None):
	"""
	A MoneyManager that remembers nothing from a previous run.

	See the module docstring: its state lives on the class, so a second run in
	the same process would start with the first one's trade still open and
	refuse every signal. Resetting on the instance shadows the class
	attributes, which is the smallest change that keeps runs independent
	without touching a component the live path depends on.
	"""
	mm = MoneyManager(setup=setup if setup is not None else settings, units=units)
	mm.signals = {}
	mm.processed = []
	mm.onTrade = False
	mm.orderIssued = False
	return mm


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
					'takeProfit': leg['takeProfit'],
					'entryTime': leg['entryTime'],
					'entryPrice': leg['entryPrice'],
					'exitTime': leg['exitTime'],
					'exitPrice': leg['exitPrice'],
					'outcome': leg['outcome'],
					'pl': leg['pl'],
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
				 candles, trades, counts):
		self.instrument = instrument
		self.granularity = granularity
		self.strategy = strategy
		self.dtfrom = dtfrom
		self.dtto = dtto
		self.candles = candles
		self.trades = trades
		self.counts = counts


def run(instrument, granularity, strategy='AG01', dtfrom=None, dtto=None,
		units=1, setup=None, source=None):
	"""
	Replay stored candles through the whole offline stack and collect trades.

	The wiring is the one tests/offline_test.py pins: strategy, money manager,
	simulator, and the adapter that says "the simulator is the broker here".
	The driver is the causally ordered one, which is not an optimisation - a
	threaded engine replaying from memory dispatches every candle of the month
	before the first order derived from the first candle exists, and reports
	no trades at all.

	`source` is there for tests, which have candles of their own and no store.
	"""
	cfg = setup if setup is not None else settings
	strategy_class = load_strategy(strategy)
	dtfrom = dtfrom if dtfrom is not None else datetime.datetime(1970, 1, 1)
	dtto = dtto if dtto is not None else datetime.datetime.today()

	ledger = Ledger(instrument=instrument, granularity=granularity)
	engine = ReplayEngine()
	for handler in (strategy_class(pairs=[instrument], granularity=granularity),
					moneyManager(units=units, setup=cfg),
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
					OANDABacktester(setup=cfg, granularity=granularity),
					SimulatedBroker(),
					ledger):
		engine.add_handler(handler)

	if source is None:
		source = ForexCandles(setup=cfg, pairs=[instrument],
							  granularity=granularity,
							  dtfrom=dtfrom, dtto=dtto)
	engine.run(source)

	return Result(instrument, granularity, strategy, dtfrom, dtto,
				  ledger.candles, ledger.trades(), ledger.counts())


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
