"""
Which broker the stack talks to, and what that broker can actually do.

The event bus was already provider-agnostic: a strategy publishes a
SignalEvent, the money manager turns it into an OrderEvent, and whatever sits
on the other end sends it somewhere. What was not agnostic was the wiring -
every script imported the OANDA classes by name - and, more importantly,
nothing anywhere said what a broker cannot do.

That second part is why this module holds a Capabilities record rather than
just a table of classes. The four brokers here are not the same shape: one
streams transactions and three have to be polled, one collapses a STOP and a
LIMIT onto market-if-touched where the others tell them apart, two serve
candles with separate bid and ask and two serve one OHLC, and exactly one of
the polled three can say which leg closed a trade. A wiring that needs a
capability asks for it with require() and fails at startup, which is the only
honest place to fail: the alternative is a strategy quietly signalling on mid
prices it believes are asks, or resting orders that never expire because
nobody noticed the broker has no expiry.

No broker here is a subset of another, which is the point of declaring rather
than ranking. IG is the closest to OANDA and still cannot say which leg
closed a trade; Interactive Brokers can say it - because its bracket is three
separate orders - and cannot serve a bid and an ask on a bar.

Nothing here is discovered at runtime. Each provider declares what it
supports, and a declaration that turns out to be wrong is a bug to fix in the
declaration - not something to paper over with a probe whose failure mode is
a silent False.

    from parity_deriva.trading import providers

    p = providers.get_provider()          # settings.PROVIDER
    providers.require(p, 'bid_ask_candles')
    engine.add_handler(p.candles(pairs=pairs, granularity='H1'))
    engine.add_handler(p.execution())
"""

import logging

from parity_deriva.etc import settings
from parity_deriva.lib.spread import spreadModel


class ProviderError(Exception):
	"""A provider was asked for something it does not have."""


class UnknownProvider(ProviderError):
	pass


class CapabilityError(ProviderError):
	"""A wiring requires a capability the chosen provider does not declare."""


class Capabilities(object):
	"""
	What a provider supports, declared by the provider itself.

	Every field defaults to the conservative answer, so a provider that
	forgets to declare something is treated as not having it. That way a new
	provider's omissions surface as a refusal to start rather than as a
	behaviour nobody chose.
	"""

	#: candles carry separate bid and ask OHLC, not just one series
	bid_ask_candles = False
	#: history can be requested by date range, so it can be backfilled
	dated_history = False
	#: how far back one history request reaches, in candles (None = no limit)
	max_history_candles = None
	#: prices are pushed, rather than polled
	price_stream = False
	#: fills and closes are pushed, rather than polled
	transaction_stream = False
	#: order types the provider accepts, in this project's own vocabulary
	order_types = frozenset()
	#: a STOP and a LIMIT at the same price are different orders to the broker
	distinct_stop_limit = False
	#: the broker states which leg closed a trade, rather than us inferring it
	close_reason = False
	#: the broker accepts an expiry on a resting order
	order_expiry = False
	#: a resting order dies at its gtdTime, whoever kills it: the broker
	#: (order_expiry) or this project's own poller cancelling it. What a
	#: strategy whose orders live a fixed number of bars needs
	expiring_orders = False
	#: the order call returns the outcome, rather than only an acknowledgement
	synchronous_orders = False
	#: the stop of an open trade can be moved after the fact. Needed by any
	#: strategy whose exit is a rule rather than a level: one with no target
	#: at all leaves entirely on a stop that climbs behind the price.
	#: "Can be moved" covers replacing it as well as amending it:
	#: OANDA has no amend and does not need one, since replacing a trade's
	#: stop is a single call there. What the flag rules out is a provider
	#: where the move cannot be made at all, because then the backtest would
	#: trade a ladder and the account a fixed stop - the one failure this
	#: project is built to make impossible.
	stop_modify = False
	#: the orders are filled by this stack's own simulator: there is no
	#: broker behind the provider, only a price feed. A wiring reads this to
	#: leave the shadow and the parity monitor out - they would be a second
	#: simulator comparing itself to the first
	paper = False

	def __init__(self, **declared):
		for key, value in declared.items():
			if not hasattr(Capabilities, key):
				raise ProviderError("unknown capability %r" % key)
			setattr(self, key, value)

	def names(self):
		return sorted(k for k in dir(Capabilities) if not k.startswith('_')
					  and not callable(getattr(Capabilities, k)))

	def dump(self):
		return "\n".join("%-22s %s" % (k, getattr(self, k)) for k in self.names())


class Provider(object):
	"""
	The handlers that talk to one broker, plus what that broker can do.

	Subclasses supply the factories they have. Asking for one a provider does
	not implement raises rather than returning None, because a wiring that
	registered None on the engine would run with a silently missing leg.
	"""

	name = None
	capabilities = Capabilities()

	def __init__(self, setup=None):
		self.setup = setup if setup is not None else settings
		self.logger = logging.getLogger('parity_deriva.trading.trading')

	# ------------------------------------------------------------ factories

	def candles(self, **args):
		raise CapabilityError("%s has no candle source" % self.name)

	def prices(self, **args):
		raise CapabilityError("%s has no price source" % self.name)

	def transactions(self, **args):
		raise CapabilityError("%s has no transaction source" % self.name)

	def execution(self, **args):
		raise CapabilityError("%s has no execution handler" % self.name)

	def simulator(self, **args):
		"""
		The shadow broker for this provider.

		Every provider gets the same bar-driven simulator: it reads candles and
		orders off the bus and knows nothing about any of the APIs. Where the
		simulator is *finer* than the real broker - it tells a STOP from a
		LIMIT, which eToro does not - the difference shows up as divergence,
		which is the point of running the two side by side.
		"""
		from parity_deriva.backtest.oanda import OANDABacktester
		from parity_deriva.etc import settings
		args.setdefault('setup', self.setup)
		# this broker's commission, else the backtest's (backtest/oanda.py costs)
		args.setdefault('commission', settings.dotenv('PARITY_DERIVA_COMMISSION_%s' % self.name.upper())
						or getattr(self.setup, 'COMMISSION', None))
		return OANDABacktester(**args)

	# --------------------------------------------------------------- naming

	def configured(self):
		"""Are this provider's credentials there at all?"""
		return False

	def accounts(self):
		"""
		The accounts these credentials reach: [{id, name, type, currency,
		balance, demo}]. A provider that cannot list them raises.
		"""
		raise CapabilityError("%s cannot list its accounts" % self.name)

	def balance(self, account=None):
		"""What `account` (or the configured one) holds, for the sizing."""
		for row in self.accounts():
			if account in (None, '', row['id']):
				return row['balance']
		raise ProviderError("%s has no account %r" % (self.name, account))

	def precision(self, instrument):
		"""Decimal places this provider accepts for an order price."""
		from parity_deriva.lib.utils import pricePrecision
		return pricePrecision(instrument, self.setup)

	def granularity(self, granularity):
		"""
		This project's granularity as the provider spells it.

		The project's own vocabulary is OANDA's ('M1', 'H1', ...), which is
		historical rather than principled; a provider that spells them
		differently translates here and refuses what it does not have.
		"""
		return granularity


class OANDAProvider(Provider):

	name = 'oanda'
	capabilities = Capabilities(
		bid_ask_candles=True,
		dated_history=True,
		max_history_candles=None,
		price_stream=True,
		transaction_stream=True,
		order_types=frozenset(['MARKET', 'STOP', 'LIMIT']),
		distinct_stop_limit=True,
		close_reason=True,
		order_expiry=True,
		expiring_orders=True,
		synchronous_orders=True,
		# not an amend: PUT /trades/{id}/orders cancels the stop the trade
		# carries and attaches the new one, in one transaction batch. The
		# difference that matters is that the trade is never left without a
		# stop, which two calls of our own could not promise.
		# See execution/execution.OANDAExecutionHandler.modifyStop.
		stop_modify=True,
	)

	def configured(self):
		return bool(getattr(self.setup, 'ACCESS_TOKEN', ''))

	def accounts(self):
		import requests
		base = "https://%s/v3/accounts" % self.setup.API_DOMAIN
		headers = {'Authorization': 'Bearer ' + self.setup.ACCESS_TOKEN}
		out = []
		for row in requests.get(base, headers=headers, timeout=20).json().get('accounts', []):
			summary = requests.get("%s/%s/summary" % (base, row['id']), headers=headers,
								   timeout=20).json().get('account', {})
			out.append({'id': row['id'], 'name': summary.get('alias') or row['id'],
						'type': 'fxTrade', 'currency': summary.get('currency'),
						'balance': float(summary.get('balance') or 0),
						'demo': str(getattr(self.setup, 'DOMAIN', '')) != 'real'})
		return out

	def candles(self, **args):
		from parity_deriva.data.candles import ForexCandles
		args.setdefault('setup', self.setup)
		return ForexCandles(**args)

	def prices(self, **args):
		from parity_deriva.data.streaming import StreamingForexPrices
		return StreamingForexPrices(**args)

	def transactions(self, **args):
		from parity_deriva.data.transaction import StreamingForexTransactions
		args.setdefault('setup', self.setup)
		return StreamingForexTransactions(**args)

	def execution(self, **args):
		from parity_deriva.execution.execution import OANDAExecutionHandler
		args.setdefault('setup', self.setup)
		return OANDAExecutionHandler(**args)


class EToroProvider(Provider):

	name = 'etoro'
	capabilities = Capabilities(
		# One OHLC per candle, no bid/ask split. ETORO_SPREAD, or the market
		# folder's spread.json, turns this True - see lib/spread.py.
		bid_ask_candles=False,
		# Candle history is "the last N", N <= 1000. There is no from/to, so
		# nothing can be backfilled and data/bulksaver.py cannot be pointed
		# here.
		dated_history=False,
		max_history_candles=1000,
		# No push anywhere in the public API: prices, fills and closes are
		# all polled.
		price_stream=False,
		transaction_stream=False,
		# mkt and mit. A STOP and a LIMIT both become mit with a triggerRate,
		# so the two are indistinguishable to the broker even though the
		# strategies and the simulator tell them apart.
		order_types=frozenset(['MARKET', 'STOP', 'LIMIT']),
		distinct_stop_limit=False,
		# A closed trade reports its rate, not which leg took it, so the leg
		# is inferred from the rate. See data/etoro.closeReason().
		close_reason=False,
		# Resting orders have no expiry. data/etoro.EToroTransactions can
		# cancel them on our side when the order's own gtdTime passes.
		order_expiry=False,
		# 200 means accepted for processing, not executed.
		synchronous_orders=False,
	)

	def __init__(self, setup=None):
		Provider.__init__(self, setup)
		# Copied before being changed: a spread model is configuration of
		# this instance, and mutating the class attribute would have one
		# configured session grant bid/ask to every other provider object
		# in the process.
		declared = dict((name, getattr(EToroProvider.capabilities, name))
						for name in EToroProvider.capabilities.names())
		if spreadModel(self.setup, 'ETORO_SPREAD').enabled():
			declared['bid_ask_candles'] = True
		# eToro does not expire an order, and data/etoro.EToroTransactions
		# cancels one once its gtdTime has passed: the order still dies
		if getattr(self.setup, 'ETORO_ENFORCE_EXPIRY', True):
			declared['expiring_orders'] = True
		self.capabilities = Capabilities(**declared)

	def configured(self):
		return bool(getattr(self.setup, 'ETORO_API_KEY', '')
					and getattr(self.setup, 'ETORO_USER_KEY', ''))

	def accounts(self):
		"""
		The one account the keys reach, demo or real as DOMAIN says. Its
		balance is the portfolio's cash credit, in dollars: eToro keeps every
		account in USD.
		"""
		from parity_deriva.lib.etoro import EToroAPI
		api = EToroAPI(setup=self.setup)
		status, payload = api.get('portfolio')
		if status != 200:
			raise ProviderError("eToro portfolio: status %s" % status)
		portfolio = (payload or {}).get('clientPortfolio') or {}
		return [{'id': 'demo' if api.demo else 'real',
				 'name': 'virtual portfolio' if api.demo else 'real portfolio',
				 'type': 'eToro', 'currency': 'USD',
				 'balance': float(portfolio.get('credit') or 0), 'demo': api.demo}]

	def candles(self, **args):
		from parity_deriva.data.etoro import EToroCandles
		args.setdefault('setup', self.setup)
		return EToroCandles(**args)

	def prices(self, **args):
		from parity_deriva.data.etoro import EToroRates
		args.setdefault('setup', self.setup)
		return EToroRates(**args)

	def transactions(self, **args):
		from parity_deriva.data.etoro import EToroTransactions
		args.setdefault('setup', self.setup)
		return EToroTransactions(**args)

	def execution(self, **args):
		from parity_deriva.execution.etoro import EToroExecutionHandler
		args.setdefault('setup', self.setup)
		return EToroExecutionHandler(**args)

	def precision(self, instrument):
		from parity_deriva.lib.etoro import pricePrecision
		return pricePrecision(instrument, self.setup)

	def granularity(self, granularity):
		from parity_deriva.lib.etoro import interval
		return interval(granularity)


class IGProvider(Provider):

	name = 'ig'
	capabilities = Capabilities(
		# Every one of open, high, low and close comes as {bid, ask,
		# lastTraded}, so this is real rather than modelled - the one broker
		# here besides OANDA where AG01 can read the prices it thinks it is
		# reading. data/ig.py computes mid as their average, since IG serves
		# none.
		bid_ask_candles=True,
		# from/to on GET /prices, so a backfill is possible. The real ceiling
		# is not a per-request one: IG meters history by *data points*, 10,000
		# a week across everything using the key, and lib/ig.py watches what it
		# reports back rather than counting locally.
		dated_history=True,
		max_history_candles=None,
		# IG does push, over Lightstreamer - a protocol and a dependency this
		# project does not carry. A capability says what this stack can do, not
		# what the broker's documentation mentions, so both streams are false
		# and data/ig.py polls.
		price_stream=False,
		transaction_stream=False,
		order_types=frozenset(['MARKET', 'STOP', 'LIMIT']),
		# A working order is typed LIMIT or STOP and IG treats them as
		# different orders, which eToro does not.
		distinct_stop_limit=True,
		# The transaction history reports an open level and a close level and
		# no leg, so the leg is inferred from the closing level exactly as on
		# eToro. See lib/closereason.py.
		close_reason=False,
		# GOOD_TILL_DATE with a goodTillDate, so the strategies' end-of-day
		# expiry is the broker's to enforce rather than ours.
		order_expiry=True,
		expiring_orders=True,
		# POST answers with a dealReference. What happened has to be read from
		# GET /confirms afterwards.
		synchronous_orders=False,
	)

	def configured(self):
		return bool(getattr(self.setup, 'IG_API_KEY', '')
					and getattr(self.setup, 'IG_IDENTIFIER', ''))

	def accounts(self):
		from parity_deriva.lib.ig import IGAPI
		api = IGAPI(setup=self.setup)
		status, payload = api.get('accounts')
		if status != 200:
			raise ProviderError("IG accounts: status %s" % status)
		return [{'id': row.get('accountId'), 'name': row.get('accountName'),
				 'type': row.get('accountType'), 'currency': row.get('currency'),
				 'balance': float((row.get('balance') or {}).get('balance') or 0),
				 'demo': api.demo}
				for row in (payload or {}).get('accounts', [])]

	def candles(self, **args):
		from parity_deriva.data.ig import IGCandles
		args.setdefault('setup', self.setup)
		return IGCandles(**args)

	def prices(self, **args):
		from parity_deriva.data.ig import IGRates
		args.setdefault('setup', self.setup)
		return IGRates(**args)

	def transactions(self, **args):
		from parity_deriva.data.ig import IGTransactions
		args.setdefault('setup', self.setup)
		return IGTransactions(**args)

	def execution(self, **args):
		from parity_deriva.execution.ig import IGExecutionHandler
		args.setdefault('setup', self.setup)
		return IGExecutionHandler(**args)

	def precision(self, instrument):
		from parity_deriva.lib.ig import pricePrecision
		return pricePrecision(instrument, self.setup)

	def granularity(self, granularity):
		from parity_deriva.lib.ig import resolution
		return resolution(granularity)


class CapitalProvider(IGProvider):
	"""
	Capital.com: IG's handlers, talking through lib/capital.CapitalAPI.

	The same capabilities as IG, measured the same way: bid and ask on every
	bar, history by date, a working order with a goodTillDate, no leg named on
	a close. Unlike IG, nothing meters the history.
	"""

	name = 'capital'

	def __init__(self, setup=None):
		from parity_deriva.lib.capital import setupOf
		IGProvider.__init__(self, setupOf(setup if setup is not None else settings))

	def configured(self):
		return bool(getattr(self.setup, 'IG_API_KEY', '')
					and getattr(self.setup, 'IG_IDENTIFIER', ''))

	def api(self):
		from parity_deriva.lib.capital import CapitalAPI
		return CapitalAPI(setup=self.setup)

	def accounts(self):
		api = self.api()
		status, payload = api.get('accounts')
		if status != 200:
			raise ProviderError("Capital.com accounts: status %s" % status)
		return [{'id': row.get('accountId'), 'name': row.get('accountName'),
				 'type': row.get('accountType'), 'currency': row.get('currency'),
				 'balance': float((row.get('balance') or {}).get('balance') or 0),
				 'demo': api.demo}
				for row in (payload or {}).get('accounts', [])]

	def candles(self, **args):
		args.setdefault('api', self.api())
		return IGProvider.candles(self, **args)

	def transactions(self, **args):
		args.setdefault('api', self.api())
		return IGProvider.transactions(self, **args)

	def execution(self, **args):
		args.setdefault('api', self.api())
		return IGProvider.execution(self, **args)


class IBProvider(Provider):
	"""
	Interactive Brokers through the Client Portal Web API.

	The one provider here that cannot authenticate itself: the Web API is
	served by a gateway you run, and a human logs into it in a browser. So a
	stack pointed here starts by asking the gateway whether anybody did, and
	says so plainly when nobody has. See lib/ib.py.
	"""

	name = 'ib'
	capabilities = Capabilities(
		# The history route serves one OHLC, the way eToro does. IB_SPREAD,
		# or the market folder's spread.json, turns this True - see
		# lib/spread.py.
		bid_ask_candles=False,
		# A start time and a duration, so a window can be walked back.
		dated_history=True,
		max_history_candles=None,
		# IB does push, over a WebSocket on the same gateway, which is a second
		# protocol this module does not speak. Polled, therefore.
		price_stream=False,
		transaction_stream=False,
		order_types=frozenset(['MARKET', 'STOP', 'LIMIT']),
		# MKT, STP and LMT are three different orders, and a stop's level goes
		# in a different field than a limit's.
		distinct_stop_limit=True,
		# The one thing IB does better than the other two polled brokers, and
		# it falls out of the bracket being three orders: the stop and the
		# target are child orders with ids of their own, so the child that
		# filled names the leg. No inference, no UNKNOWN.
		close_reason=True,
		# Time in force is DAY, GTC or an immediate variety - no expiry
		# instant - so data/ib.py cancels a resting order once the gtdTime it
		# was issued with has passed.
		order_expiry=False,
		# An order id comes back, but the order is PreSubmitted: what it did
		# has to be polled for. And the submission itself may answer with a
		# question before there is an order at all.
		synchronous_orders=False,
	)

	def __init__(self, setup=None):
		Provider.__init__(self, setup)
		if spreadModel(self.setup, 'IB_SPREAD').enabled():
			# Copied before being changed, for the reason the eToro provider
			# copies its own: mutating the class attribute would have one
			# configured session grant bid/ask to every other provider object
			# in the process.
			declared = dict((name, getattr(IBProvider.capabilities, name))
							for name in IBProvider.capabilities.names())
			declared['bid_ask_candles'] = True
			self.capabilities = Capabilities(**declared)

	def configured(self):
		return bool(getattr(self.setup, 'IB_ACCOUNT_ID', ''))

	def candles(self, **args):
		from parity_deriva.data.ib import IBCandles
		args.setdefault('setup', self.setup)
		return IBCandles(**args)

	def prices(self, **args):
		from parity_deriva.data.ib import IBRates
		args.setdefault('setup', self.setup)
		return IBRates(**args)

	def transactions(self, **args):
		from parity_deriva.data.ib import IBTransactions
		args.setdefault('setup', self.setup)
		return IBTransactions(**args)

	def execution(self, **args):
		from parity_deriva.execution.ib import IBExecutionHandler
		args.setdefault('setup', self.setup)
		return IBExecutionHandler(**args)

	def precision(self, instrument):
		from parity_deriva.lib.ib import pricePrecision
		return pricePrecision(instrument, self.setup)

	def granularity(self, granularity):
		from parity_deriva.lib.ib import bar
		return bar(granularity)


class MT5Provider(Provider):
	"""
	MetaTrader 5, through the terminal running under Wine and the bridge
	scripts/mt5_bridge.sh serves. Demo accounts only: see lib/mt5.py.
	"""

	name = 'mt5'
	capabilities = Capabilities(
		# Bars are bid with the bar's spread in points, so ask is derived
		# from the broker's own figure rather than a configured one.
		bid_ask_candles=True,
		dated_history=True,
		max_history_candles=None,
		# The package has no push; data/mt5.py polls.
		price_stream=False,
		transaction_stream=False,
		order_types=frozenset(['MARKET', 'STOP', 'LIMIT']),
		distinct_stop_limit=True,
		# A closing deal's reason is SL or TP when the broker's leg took it.
		close_reason=True,
		# ORDER_TIME_SPECIFIED where the symbol allows it, and our own
		# cancel at gtdTime where it does not.
		order_expiry=True,
		expiring_orders=True,
		# order_send answers with the outcome: DONE for a fill, PLACED for a
		# resting order, a retcode for a refusal.
		synchronous_orders=True,
		# TRADE_ACTION_SLTP on the position, target sent again with it.
		stop_modify=True,
	)

	def configured(self):
		from parity_deriva.lib.mt5 import credentials, terminals, MT5Error
		for entry in terminals(self.setup):
			try:
				credentials(self.setup, entry)
				return True
			except MT5Error:
				continue
		return False

	def accounts(self):
		"""
		One row per terminal in MT5_TERMINALS - each is one account. A
		terminal that cannot be reached is logged and left out, so one bridge
		down does not hide the others; all of them down raises.
		"""
		from parity_deriva.lib.mt5 import connect, terminals, MT5Error
		out, errors = [], []
		for entry in terminals(self.setup):
			try:
				api = connect(self.setup, entry)
				row, error = api.account_info()
				if not row:
					raise MT5Error("account_info: %s" % (error,))
			except MT5Error as exc:
				self.logger.error("MT5 terminal %s: %s" % (entry.get('bridge'), exc))
				errors.append(str(exc))
				continue
			out.append({'id': str(row['login']), 'name': row.get('name') or str(row['login']),
						'type': 'MT5', 'currency': row.get('currency'),
						'balance': float(row.get('balance') or 0), 'demo': api.demo})
		if not out and errors:
			raise ProviderError("MT5: %s" % "; ".join(errors))
		return out

	def candles(self, **args):
		from parity_deriva.data.mt5 import MT5Candles
		args.setdefault('setup', self.setup)
		return MT5Candles(**args)

	def transactions(self, **args):
		from parity_deriva.data.mt5 import MT5Transactions
		args.setdefault('setup', self.setup)
		return MT5Transactions(**args)

	def execution(self, **args):
		from parity_deriva.execution.mt5 import MT5ExecutionHandler
		args.setdefault('setup', self.setup)
		return MT5ExecutionHandler(**args)

	def precision(self, instrument):
		from parity_deriva.lib.mt5 import pricePrecision
		return pricePrecision(instrument, self.setup)

	def granularity(self, granularity):
		from parity_deriva.lib.mt5 import timeframe
		timeframe(granularity)
		return granularity


class TwelveDataProvider(Provider):
	"""
	Twelve Data's candles with this stack's simulator as the broker.

	The paper session. Every broker session runs a shadow simulator on its
	own broker's candles, which measures that broker against itself; this
	one runs the same strategy on a third party's candles and fills its
	orders itself, so every broker can be measured against the same series
	nobody traded on. The execution handler IS backtest/oanda.OANDABacktester
	and the "transaction stream" is backtest/offline.SimulatedBroker, which
	promotes its fills to the events a broker would send - the wiring
	scripts/live.py already knows, and the page reads it as any session.

	The capabilities are the simulator's, not a broker's: it tells a STOP
	from a LIMIT, names the leg that closed a trade, expires a resting order
	at its gtdTime and moves a stop. What it cannot do is quote a bid and an
	ask, because Twelve Data serves one series - so TWELVEDATA_SPREAD or the
	shared spread.json is a model, off until set, exactly as eToro's is.
	"""

	name = 'twelvedata'
	capabilities = Capabilities(
		bid_ask_candles=False,
		dated_history=True,
		max_history_candles=5000,
		price_stream=False,
		transaction_stream=False,
		order_types=frozenset(['MARKET', 'STOP', 'LIMIT']),
		distinct_stop_limit=True,
		close_reason=True,
		order_expiry=True,
		expiring_orders=True,
		synchronous_orders=True,
		stop_modify=True,
		paper=True,
	)

	def __init__(self, setup=None):
		Provider.__init__(self, setup)
		if spreadModel(self.setup, 'TWELVEDATA_SPREAD').enabled():
			# copied before being changed, for the reason the eToro provider
			# copies its own
			declared = dict((name, getattr(TwelveDataProvider.capabilities, name))
							for name in TwelveDataProvider.capabilities.names())
			declared['bid_ask_candles'] = True
			self.capabilities = Capabilities(**declared)

	def configured(self):
		return bool(getattr(self.setup, 'TWELVEDATA_API_KEY', ''))

	def accounts(self):
		"""
		The one account: the simulator's, opened with EQUITY. Its currency is
		None on purpose - the simulator keeps its balance in the instrument's
		quote currency, whichever that is, and scripts/live.quoteBalance
		takes an account without a currency at face value.
		"""
		return [{'id': 'paper', 'name': 'Twelve Data · simulatore', 'type': 'paper',
				 'currency': None, 'balance': float(getattr(self.setup, 'EQUITY', 100000)),
				 'demo': True}]

	def candles(self, **args):
		from parity_deriva.data.twelvedata import TwelveDataCandles
		args.setdefault('setup', self.setup)
		return TwelveDataCandles(**args)

	def execution(self, **args):
		# float: EQUITY is a Decimal and the simulator adds floats to it
		from parity_deriva.backtest.oanda import OANDABacktester
		args.setdefault('setup', self.setup)
		args.setdefault('balance', float(getattr(self.setup, 'EQUITY', 100000)))
		args.pop('sized', None)
		return OANDABacktester(**args)

	def transactions(self, **args):
		from parity_deriva.backtest.offline import SimulatedBroker
		return SimulatedBroker(setup=self.setup)

	def simulator(self, **args):
		raise CapabilityError("%s is the paper account: its execution handler "
							  "is the simulator, so there is no shadow to run "
							  "beside it" % self.name)

	def granularity(self, granularity):
		from parity_deriva.lib.twelvedata import interval
		interval(granularity)
		return granularity


#: every provider that can be named in settings.PROVIDER
PROVIDERS = {
	OANDAProvider.name: OANDAProvider,
	EToroProvider.name: EToroProvider,
	IGProvider.name: IGProvider,
	CapitalProvider.name: CapitalProvider,
	IBProvider.name: IBProvider,
	MT5Provider.name: MT5Provider,
	TwelveDataProvider.name: TwelveDataProvider,
}


def history(provider, instrument, granularity, since=None, bars=500):
	"""
	The provider's own complete bars after `since` (or the last `bars` of
	them), read to the end: a warm-up or a price, not a stream.
	"""
	import datetime
	from parity_deriva.lib.utils import granularityToTimedelta
	period = granularityToTimedelta(granularity)
	start = since + period if since is not None \
		else datetime.datetime.utcnow() - bars * period
	source = provider.candles(pairs=[instrument], granularity=granularity,
							  dtfrom=start)
	got = []

	class Collect(object):
		def put(self, event):
			if str(event) == 'CANDLE':
				got.append(event)
	source.set_queue(Collect())
	source.stream_to_queue()
	return got


def available():
	return sorted(PROVIDERS)


def get_provider(name=None, setup=None):
	"""
	The provider named, or the one settings.PROVIDER names.

	An unknown name raises instead of falling back to a default: silently
	trading on the wrong broker is not a recoverable mistake.
	"""
	cfg = setup if setup is not None else settings
	if name is None:
		name = getattr(cfg, 'PROVIDER', OANDAProvider.name)
	key = str(name).strip().lower()
	if key not in PROVIDERS:
		raise UnknownProvider("unknown provider %r; available: %s"
							  % (name, ", ".join(available())))
	return PROVIDERS[key](setup=cfg)


def require(provider, *names):
	"""
	Refuse to continue unless the provider declares every capability named.

	Meant to be called by a wiring before it registers anything, so that an
	unsupported combination is a startup error naming what is missing, rather
	than a run that looks fine and is not.
	"""
	missing = []
	for name in names:
		if not hasattr(Capabilities, name):
			raise ProviderError("unknown capability %r" % name)
		if not getattr(provider.capabilities, name):
			missing.append(name)
	if missing:
		raise CapabilityError(
			"provider %s does not support: %s" % (provider.name, ", ".join(missing)))
	return provider
