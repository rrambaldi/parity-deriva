"""
Which broker the stack talks to, and what that broker can actually do.

The event bus was already provider-agnostic: a strategy publishes a
SignalEvent, the money manager turns it into an OrderEvent, and whatever sits
on the other end sends it somewhere. What was not agnostic was the wiring -
every script imported the OANDA classes by name - and, more importantly,
nothing anywhere said what a broker cannot do.

That second part is why this module holds a Capabilities record rather than
just a table of classes. OANDA and eToro are not the same shape: one streams
transactions and the other has to be polled, one distinguishes a STOP from a
LIMIT and the other collapses both onto market-if-touched, one serves candles
with separate bid and ask and the other serves one OHLC. A wiring that needs
a capability asks for it with require() and fails at startup, which is the
only honest place to fail: the alternative is a strategy quietly signalling
on mid prices it believes are asks, or resting orders that never expire
because nobody noticed the broker has no expiry.

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
	#: the order call returns the outcome, rather than only an acknowledgement
	synchronous_orders = False

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

		Both providers get the same bar-driven simulator: it reads candles and
		orders off the bus and knows nothing about either API. Where the
		simulator is *finer* than the real broker - it tells a STOP from a
		LIMIT, which eToro does not - the difference shows up as divergence,
		which is the point of running the two side by side.
		"""
		from parity_deriva.backtest.oanda import OANDABacktester
		args.setdefault('setup', self.setup)
		return OANDABacktester(**args)

	# --------------------------------------------------------------- naming

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
		synchronous_orders=True,
	)

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
		# One OHLC per candle, no bid/ask split. Setting ETORO_SPREAD turns
		# this True - see lib/etoro.SpreadModel for what that then means.
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
		if getattr(self.setup, 'ETORO_SPREAD', None) is not None:
			# Copied before being changed: a spread model is configuration of
			# this instance, and mutating the class attribute would have one
			# configured session grant bid/ask to every other provider object
			# in the process.
			declared = dict((name, getattr(EToroProvider.capabilities, name))
							for name in EToroProvider.capabilities.names())
			declared['bid_ask_candles'] = True
			self.capabilities = Capabilities(**declared)

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


#: every provider that can be named in settings.PROVIDER
PROVIDERS = {
	OANDAProvider.name: OANDAProvider,
	EToroProvider.name: EToroProvider,
}


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
