"""
An entry filter (docs/PIANO-FASE1.md C4a): conditions on what the market
looked like when a strategy signalled - 'rsi14<55', 'atrpct14>0.3&hour>=7' -
that any strategy's signals must meet, an account's option like the session
and the news. The feature names are portfolio/features.py's.

EntryFilter reads the strategy's own stream a closed bar at a time and the
money manager asks it, when a SIGNAL comes, whether the bar it came on passes
(blocked): the same object in the backtest (backtest/ledger.py), live and in
the shadow (scripts/live.py). A signal it stops is a line in the log,
"SIGNAL IGNORED: filter rsi14<55 (61.2)".
"""

import logging
import operator
import re

from parity_deriva.portfolio.features import NAMES, Features
from parity_deriva.trading.handler import ExecutionHandler

OPERATORS = {'<=': operator.le, '>=': operator.ge, '<': operator.lt, '>': operator.gt}
CONDITION = re.compile(r'^\s*([a-z0-9_]+)\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)\s*$')


class FilterError(ValueError):
	"""A condition that is not one: the message says what is expected."""


def parse(text):
	"""
	'rsi14<55&hour>=7' as [(name, op, value)], [] for none: the conditions
	joined by '&', each a feature, an operator and a number.
	"""
	out = []
	for part in str(text or '').split('&'):
		if not part.strip() or part.strip().lower() == 'none':
			continue
		found = CONDITION.match(part)
		if not found:
			raise FilterError("filter %r: a condition is a feature, < <= > or >=, and a number, "
							  "e.g. rsi14<55; several joined by &" % part.strip())
		name, op, value = found.groups()
		if name not in NAMES:
			raise FilterError("filter %r: no feature %s - there are %s" % (part.strip(), name, ', '.join(NAMES)))
		out.append((name, op, float(value)))
	return out


def text(conditions):
	"""The conditions written back, the one way: what a form and a groupKey hold."""
	return '&'.join('%s%s%g' % c for c in conditions)


class EntryFilter(ExecutionHandler):

	def __init__(self, conditions, granularity=None, instrument=None, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self.conditions = parse(conditions) if isinstance(conditions, str) else list(conditions)
		self.granularity = granularity
		self.instrument = instrument
		self.features = Features()

	def execute_event(self, event):
		if str(event) != 'CANDLE' or self.otherStream(event):
			return
		if self.instrument is not None and getattr(event, 'instrument', None) != self.instrument:
			return
		self.features.add(event)

	def blocked(self, when=None):
		"""Why the last closed bar fails the filter, or None when it passes. A feature still warming fails."""
		values = self.features.values()
		for name, op, value in self.conditions:
			seen = values.get(name)
			if seen is None:
				return "%s%s%g (warming up)" % (name, op, value)
			if not OPERATORS[op](seen, value):
				return "%s%s%g (%s)" % (name, op, value, round(seen, 2))
		return None
