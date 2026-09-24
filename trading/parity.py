
import collections
import logging

from parity_deriva.etc import settings
from parity_deriva.event.event import StatusEvent
from parity_deriva.trading.handler import ExecutionHandler

#: what a comparison can find
OUTCOME = 'outcome'
SLIPPAGE = 'slippage'
UNPAIRED = 'unpaired'
#: not a finding: a comparison that could not be made
UNDECIDED = 'undecided'

#: outcomes that mean "nobody could tell", as against "the trade ended this
#: way". OANDA states which leg closed a trade; eToro reports only the rate
#: it closed at, and data/etoro.closeReason() infers the leg from that rate
#: and says UNKNOWN where the rate belongs to neither leg clearly - a manual
#: close, a margin call, a gap. Counting that as a mismatch would score our
#: own ignorance as the market disagreeing with the simulator.
UNKNOWN_OUTCOMES = frozenset([None, '', 'UNKNOWN'])


def policy_for(instrument, setup=None):
	"""
	The alarm settings in force for an instrument.

	PARITY_ALARM holds the defaults; PARITY_ALARM_BY_INSTRUMENT overrides
	them key by key, so an override need only name what differs.
	"""
	cfg = setup if setup is not None else settings
	policy = dict(getattr(cfg, 'PARITY_ALARM', {}))
	policy.update(getattr(cfg, 'PARITY_ALARM_BY_INSTRUMENT', {}).get(instrument, {}))
	return policy


class Divergence(object):
	"""One disagreement between the live account and its simulated shadow."""

	def __init__(self, key, kind, detail):
		self.key = key
		self.kind = kind
		self.detail = detail

	def __str__(self):
		return "%s %s %s" % (self.kind, self.key, self.detail)


class ParityMonitor(ExecutionHandler):
	"""
	Watch a live account against its simulated shadow and raise the alarm.

	Both are on the bus: the real execution handler and the broker's
	transactions on one side, the simulator on the other, publishing its own
	event types so the two can be told apart. This handler joins them on the
	signal key - which is a function of the candle that produced the signal,
	so the same key appears on both sides and in any later replay - and counts
	where they disagree.

	Which broker is behind the live side does not matter here, but what it can
	report does. OANDA pushes a transaction naming the leg that closed a
	trade; eToro pushes nothing and never names the leg, so its poller infers
	it and marks it inferred, and says UNKNOWN when the closing rate belongs
	to neither leg clearly. An UNKNOWN on either side makes that trade
	undecidable rather than divergent - see UNKNOWN_OUTCOMES.

	Judgement is deliberately not per trade. A simulator reading bars cannot
	say which of the stop and the target a single bar reached first, so a
	proportion of disagreements is structural rather than a sign of anything:
	scripts/divergence_band.py measures it, and the threshold belongs above
	it. So the monitor keeps a rolling window and compares a rate, and refuses
	to judge at all until it has seen min_sample trades.

	Nothing here is a measurement. Every number comes from PARITY_ALARM, and
	an unset check is off rather than guessed.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		# _set() only assigns when the default is not None, so seed them first
		self.instrument = None
		self.policy = None
		self._set(args, 'instrument')
		self._set(args, 'policy')
		if self.policy is None:
			self.policy = policy_for(self.instrument, self.setup)

		self.real = {}
		self.simulated = {}
		self.window = collections.deque(maxlen=self.policy.get('window', 200))
		self.divergences = []
		self.reconciled = 0
		self.undecided = 0
		self.halted = False

	# ---------------------------------------------------------------- intake

	def execute_event(self, event):
		kind = str(event)
		if kind == 'TRANSACTION' and getattr(event, 'type', None) == 'ORDER_FILL':
			self._record(self.real, event)
		elif kind == 'SIMULATEDFILL':
			self._record(self.simulated, event)
		elif kind == 'STATUS' and getattr(event, 'status', None) == 'RESUME':
			self.halted = False

	def _record(self, side, event):
		key = getattr(event, 'signalNumber', None)
		if key is None:
			return
		entry = side.setdefault(key, {})
		if event.has_attr('tradesClosed'):
			entry['close'] = event
		else:
			entry['fill'] = event
		self._reconcile(key)
		# Also judge on intake, not only on a successful reconcile: if one
		# side goes silent - a dead transaction stream, an account rejecting
		# every order - nothing ever reconciles, and an alarm that only fired
		# on reconciliation would stay quiet through exactly the failure it
		# exists to catch.
		self._judge()

	# ------------------------------------------------------------ comparison

	def _reconcile(self, key):
		"""Compare a key once both sides have closed it."""
		mine = self.real.get(key, {})
		theirs = self.simulated.get(key, {})
		if 'close' not in mine or 'close' not in theirs:
			return

		found = []
		real, simulated = self._outcome(mine['close']), self._outcome(theirs['close'])
		decidable = (real not in UNKNOWN_OUTCOMES
					 and simulated not in UNKNOWN_OUTCOMES)
		if not decidable:
			self.undecided += 1
			self.logger.warning(
				"PARITY undecided %s: real %s, simulated %s" % (key, real, simulated))
			self.queue_event(StatusEvent({
				'status': 'PARITY', 'kind': UNDECIDED, 'key': key,
				'detail': "real %s, simulated %s" % (real, simulated),
				'instrument': self.instrument}))
		elif real != simulated:
			found.append(Divergence(key, OUTCOME,
									"real %s, simulated %s" % (real, simulated)))

		limit = self.policy.get('max_slippage')
		if limit is not None and 'fill' in mine and 'fill' in theirs:
			gap = abs(float(mine['fill'].price) - float(theirs['fill'].price))
			if gap > limit:
				found.append(Divergence(key, SLIPPAGE,
										"%.6f > %.6f" % (gap, limit)))

		self.reconciled += 1
		# An undecidable comparison enters the window only if something else
		# about the trade diverged. Scoring it as agreement would dilute the
		# rate and hide real mismatches behind trades nobody could judge.
		if decidable or found:
			self.window.append(bool(found))
		self.divergences.extend(found)
		# Was: a divergence was a log line and nothing else. The web page
		#      reads the JSONL event log, not the logger, so it saw the HALT
		#      and never what led up to it.
		# Now: each finding goes on the bus too. status is neither HALT,
		#      RESUME nor LIQUIDATE, so the money manager passes it by, and
		#      the event log picks it up like any other event.
		for one in found:
			self.logger.warning("PARITY %s" % one)
			self.queue_event(StatusEvent({
				'status': 'PARITY', 'kind': one.kind, 'key': one.key,
				'detail': one.detail, 'instrument': self.instrument}))
		del self.real[key]
		del self.simulated[key]
		self._judge()

	def _outcome(self, close):
		"""Which leg closed the trade: the two sides must agree on this."""
		return getattr(close, 'reason', None)

	# -------------------------------------------------------------- decision

	def unpaired(self):
		"""Keys one side closed and the other never did."""
		out = []
		for key, entry in self.real.items():
			if 'close' in entry and 'close' not in self.simulated.get(key, {}):
				out.append(key)
		for key, entry in self.simulated.items():
			if 'close' in entry and 'close' not in self.real.get(key, {}):
				out.append(key)
		return out

	def rate(self):
		"""Fraction of the window that disagreed, or None below min_sample."""
		if len(self.window) < self.policy.get('min_sample', 0):
			return None
		if not self.window:
			return None
		return sum(1 for x in self.window if x) / float(len(self.window))

	def breaches(self):
		"""Which configured limits are currently exceeded."""
		out = []
		rate = self.rate()
		limit = self.policy.get('max_outcome_mismatch')
		if rate is not None and limit is not None and rate > limit:
			out.append("outcome mismatch %.3f > %.3f over %d trades"
					   % (rate, limit, len(self.window)))
		limit = self.policy.get('max_unpaired')
		if limit is not None:
			stranded = len(self.unpaired())
			if stranded > limit:
				out.append("%d trades reported by one side only > %d"
						   % (stranded, limit))
		limit = self.policy.get('max_undecided')
		if limit is not None and self.undecided > limit:
			out.append("%d trades whose outcome could not be judged > %d"
					   % (self.undecided, limit))
		return out

	def _judge(self):
		breaches = self.breaches()
		if not breaches:
			return
		for breach in breaches:
			self.logger.error("PARITY ALARM: %s" % breach)
		# the page's log line for the alarm being up, under 'warn' as much as
		# under 'halt' - once per change of what is breached, not once per
		# intake: _judge runs on every fill, and a breached window would
		# otherwise write the same line hundreds of times a day
		if breaches != getattr(self, '_breached', None):
			self._breached = list(breaches)
			self.queue_event(StatusEvent({'status': 'PARITY_ALARM',
										  'breaches': breaches,
										  'instrument': self.instrument}))
		if self.policy.get('action') != 'halt' or self.halted:
			return
		self.halted = True
		self.logger.critical("PARITY ALARM: halting on %s" % "; ".join(breaches))
		self.queue_event(StatusEvent('HALT'))
