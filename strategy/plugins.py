"""
Strategies the engine can run and does not contain.

Some strategies are not published with this repository. They live in their
own checkout under strategy/private/, which .gitignore keeps out of here,
and this module is the only place that knows whether it is present.

The rule the rest of the code follows: **nothing outside this file names a
private strategy.** backtest/ledger.py, scripts/live.py and web/service.py
merge what is offered here into their own tables and stay readable with the
directory absent - which is the state anyone who is not us checks this out
in, and the state the tests run in.

An absent directory is an ImportError and is normal. A directory that is
present but broken is also an ImportError, and that one is not normal, so it
is logged rather than swallowed: a strategy that silently stops being
offered looks exactly like a strategy that was never installed, and the two
want very different reactions.

A viewer plugin - a strategy with its own engine, drawn by the web service
rather than replayed through the simulator - is a dictionary:

	name     what the page calls it
	description  optional: the line the page prints over its chart, which a
			 handler class spells DESCRIPTION
	instrument, granularity  optional: what the page selects when the
			 strategy is picked. A handler class spells them INSTRUMENT and
			 GRANULARITY
	setupBars    optional: how many bars back from the signal the entry rule
			 reads, which the page boxes. A handler class spells it
			 SETUP_BARS
	fields   () -> the form: a list of {name, label, value, and either
			 choices for a menu or min/max/step for a number box}
	params   (get) -> the parameters, built from a function that takes a
			 query string key and returns its text or None. Raises one of
			 `errors` for anything it will not accept
	run      (instrument, granularity, params, dtfrom=, dtto=, setup=) ->
			 a result the service's payload() can read. A run that also
			 takes progress= is handed a function to call now and then
			 with {bars, at, balance, trades, won, lost, curve} - as
			 backtest/ledger.Progress reports - which raises to stop it
	errors   the exceptions a bad request raises, which the service turns
			 into a 400 rather than a traceback
"""

import logging

#: the trading logger, because a missing strategy is a fact about a run
LOGGER = 'parity_deriva.trading.trading'


def _registry():
	try:
		from parity_deriva.strategy.private import registry
	except ImportError as exc:
		# "no module named ...private" is the ordinary case: nothing is
		# installed. Anything else is a real break inside an installed
		# registry, and saying so beats offering fewer strategies in silence.
		if 'private' not in str(exc):
			logging.getLogger(LOGGER).warning(
				"private strategies present but not loadable: %s", exc)
		return None
	return registry


def backtest():
	"""name -> (module, class), for backtest/ledger.py."""
	return dict(getattr(_registry(), 'BACKTEST', {}) or {})


def live():
	"""name -> (module, class, capabilities, argument), for scripts/live.py."""
	return dict(getattr(_registry(), 'LIVE', {}) or {})


def viewers():
	"""name -> plugin, for web/service.py."""
	return dict(getattr(_registry(), 'VIEWERS', {}) or {})
