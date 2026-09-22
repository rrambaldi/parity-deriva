"""
IG as a data source: candles with a real bid and ask, and deals to poll for.

The three classes here stand in for data/candles.py, data/streaming.py and
data/transaction.py, and IG lands between the two brokers already here.

What it has that eToro does not:

* **a bid and an ask on every candle.** Each of open, high, low and close
  comes as {bid, ask, lastTraded}, so AG01 can buy the high of the ask and
  stop out at the low of the bid on real numbers rather than on a spread
  model. This is the only reason the strategies that need bid/ask can run on
  IG unconfigured.
* **history by date.** from/to on GET /prices, so an offline run can reach
  back and data/bulksaver.py could be pointed here - subject to the weekly
  allowance of 10,000 data points, which is the real ceiling and which
  lib/ig.py watches through what IG reports back.

What it does not have, and what that costs:

* **anything pushed.** IG does push, over Lightstreamer, which is a protocol
  and a dependency this project does not carry. So prices are polled and so
  are deals, exactly as on eToro.
* **an outcome in the reply to an order.** POST /workingorders/otc answers
  with a dealReference. What happened has to be read from GET /confirms,
  which is why IGTransactions is both a handler and a source: it learns which
  deals exist by listening for the acknowledgements the execution handler
  publishes, then asks after each one.
* **which leg closed a trade.** The transaction history reports an open level
  and a close level and no leg, so the leg is inferred from the closing level
  exactly as on eToro - see lib/closereason.py - and the event says it was
  inferred.

One thing to be careful of in the confirmation: IG keeps a deal confirmation
available only briefly after the deal. A poll interval longer than that
window loses the fill, which is why IG_POLL_SECONDS defaults low and why a
confirmation that comes back 404 is logged as a lost confirmation rather than
retried forever.
"""

import datetime
import logging
import sys
import time

from parity_deriva.etc import settings
from parity_deriva.event.event import CandleEvent
from parity_deriva.event.event import StatusEvent
from parity_deriva.event.event import TickEvent
from parity_deriva.event.event import TransactionEvent
from parity_deriva.event.event import OrderCancelEvent
from parity_deriva.lib.closereason import UNKNOWN, closeReason
from parity_deriva.lib.ig import (IGAPI, dealTime, epic,
								  instrumentName, priceTime, resolution,
								  scale, utcnow)
from parity_deriva.lib.utils import granularityToTimedelta
from parity_deriva.trading.handler import StreamHandler


#: What GET /confirms says about a deal. ACCEPTED means the deal exists;
#: REJECTED means it never will, and carries a reason worth publishing.
ACCEPTED = 'ACCEPTED'
REJECTED = 'REJECTED'

#: The confirmation's own status, which is about what the deal *did* rather
#: than whether it was accepted. IG spells it in two vocabularies at once, and
#: which word lives where was established by sending real deals to the demo
#: account rather than read off the documentation:
#:
#: * the top-level ``status`` describes the *position* - OPEN, CLOSED,
#:   PARTIALLY_CLOSED, AMENDED, DELETED;
#: * the statuses inside ``affectedDeals`` describe the deal - OPENED,
#:   FULLY_CLOSED, PARTIALLY_CLOSED, AMENDED, DELETED.
#:
#: So 'OPENED' never appears at the top level. This file used to look for it
#: there, which matched nothing and filed every market fill as a working
#: order; see IGTransactions.outcome. Both spellings are accepted here
#: because both are read.
#:
#: What none of them says is whether the deal is resting or filled: a market
#: fill and a working order both come back OPEN / OPENED. Only the endpoint
#: the order was sent to knows that, so that is what decides it.
OPEN = ('OPEN', 'OPENED')
CLOSED = ('CLOSED', 'FULLY_CLOSED')
PARTIAL = 'PARTIALLY_CLOSED'
AMENDED = 'AMENDED'
DELETED = 'DELETED'


def number(value):
	"""
	A level from the transaction history, as a number.

	IG serves openLevel and closeLevel there as *strings* - '1.14646' - where
	the confirmation and the position list serve them as numbers. Measured on
	the demo account's own history. Left as a string they would reach the
	ledger as a price nothing downstream can subtract, and the comparison the
	parity monitor makes would be between a number and some text.
	"""
	if value is None:
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def price(row, field, which='bid'):
	"""
	One side of one OHLC field, as a float.

	IG sends {"bid": 1.16, "ask": 1.161, "lastTraded": null}, and which of
	the three is populated depends on the market: FX and index CFDs quote bid
	and ask, and lastTraded is null. A missing side comes back None rather
	than as zero, because a zero would be a price a strategy could act on.
	"""
	cell = (row or {}).get(field) or {}
	value = cell.get(which)
	if value is None:
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


class IGCandles(StreamHandler):
	"""
	Candles from IG, polled, with bid and ask as IG serves them.

	Same constructor contract as data/candles.ForexCandles - pairs,
	granularity, and dtfrom/dtto for an offline run - so a wiring can swap one
	for the other.

	Two differences from OANDA are IG's:

	* **no completeness flag.** IG has no equivalent of OANDA's `complete`,
	  and its newest row is the bar still forming, so completeness is computed
	  from the row's own start plus the interval. Emitting a forming bar would
	  have a strategy signal on a high that is not yet the high - the same
	  trap the eToro source documents, and it bites the same way.
	* **a weekly budget.** Every row served spends from the 10,000-point
	  allowance, which is shared with anything else using the key. The reply
	  says what is left and that figure is recorded and warned on, rather than
	  counted locally where it would be wrong.

	Mid is computed as the average of bid and ask rather than read: IG does
	not serve a mid, and a strategy reading candle.mid on OANDA is reading
	OANDA's own mid, so the two are not quite the same number. The difference
	is half a spread and it is stated here rather than hidden.
	"""

	sampling = 10

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs', ['EUR_USD'])
		self._set(args, 'granularity', 'M5')

		self.api = args.get('api') or IGAPI(setup=self.setup)
		self.resolution = resolution(self.granularity)
		self.period = granularityToTimedelta(self.granularity)
		self.sleep = int(getattr(self.setup, 'IG_POLL_SECONDS', 5))

		# Live, only the newest bars matter: opening a session by firing a
		# batch of stale candles at a strategy would have it signal on a
		# reversal from last Tuesday. data/candles.py asks for two live for
		# the same reason. Offline, reaching back is the whole point.
		self.batch_size = 2
		self.live = True
		if self._set(args, 'dtfrom', datetime.datetime(1970, 1, 1, 0, 0, 0)):
			self.live = False
			self._set(args, 'batch_size', 500)
			self._set(args, 'dtto', utcnow())
			self._set(args, 'sleep', 0)
		else:
			self._set(args, 'batch_size')

		self.last = {}
		self.epics = {}
		self.scales = {}
		self.num_blocks = {}
		self.num_candles = {}
		for pair in self.pairs:
			self.last[pair] = self.dtfrom if not self.live else None
			self.epics[pair] = epic(pair, self.setup)
			self.scales[pair] = scale(pair, self.setup)
			self.num_blocks[pair] = 0
			self.num_candles[pair] = 0

	# ------------------------------------------------------------- requests

	def params(self, pair):
		"""
		What to ask for: the newest N bars live, a date range offline.

		IG accepts either, and the two are not combined - a range with a max
		would silently truncate the range from an end nobody chose.
		"""
		if self.live:
			return {'resolution': self.resolution, 'max': self.batch_size,
					'pageSize': 0}
		start = self.last[pair] or self.dtfrom
		return {'resolution': self.resolution,
				'from': priceTime(start),
				'to': priceTime(self.dtto),
				'max': self.batch_size,
				'pageSize': 0}

	def request(self, pair):
		status, payload = self.api.get('prices', parts=(self.epics[pair],),
									   params=self.params(pair))
		if status == 404:
			# IG answers 404 for an epic that exists but has no history at
			# this resolution, which is a configuration problem and not a
			# transient one, so it is said plainly rather than counted as an
			# error to be retried.
			self.logger.error(
				"%s: IG has no %s history for epic %s"
				% (pair, self.resolution, self.epics[pair]))
			self.queue_event(StatusEvent('ERROR'))
			return None
		if status != 200 or not payload:
			self.logger.error("%s prices: status %s" % (pair, status))
			self.queue_event(StatusEvent('ERROR'))
			return None
		self.api.noteAllowance(payload)
		return payload

	def rows(self, payload):
		return (payload or {}).get('prices') or []

	def complete(self, when, now=None):
		"""
		Has this bar's period elapsed?

		IG sends no flag and its newest row is still forming, so the question
		is answered from the row's start and the interval, against a UTC clock
		because snapshotTimeUTC is what was read. With no period known for the
		granularity nothing is called complete: refusing to emit is
		recoverable, emitting a partial bar is not.
		"""
		if self.period is None:
			return False
		now = now if now is not None else utcnow()
		return when + self.period <= now

	def event(self, pair, row):
		"""
		One CandleEvent carrying bid, ask and a computed mid.

		A row missing either side is refused rather than half-filled: a
		candle whose ask is real and whose bid is a copy of it would have
		AG01 place a stop where no price was ever quoted.
		"""
		factor = self.scales[pair]
		bid = {}
		ask = {}
		for short, full in (('o', 'openPrice'), ('h', 'highPrice'),
							('l', 'lowPrice'), ('c', 'closePrice')):
			b = price(row, full, 'bid')
			a = price(row, full, 'ask')
			if b is None or a is None:
				return None
			bid[short] = b / factor
			ask[short] = a / factor
		mid = dict((k, (bid[k] + ask[k]) / 2.0) for k in bid)

		event = CandleEvent({
			'time': dealTime(row.get('snapshotTimeUTC') or row.get('snapshotTime')),
			'volume': row.get('lastTradedVolume') or 0,
			'complete': True,
			'bid': bid,
			'ask': ask,
			'mid': mid,
		})
		event.instrument = pair
		event.granularity = self.granularity
		return event

	# ---------------------------------------------------------------- stream

	def poll(self, pair, now=None):
		"""Emit whatever complete bars this instrument has that are new."""
		payload = self.request(pair)
		if payload is None:
			return 0

		self.num_blocks[pair] += 1
		sent = 0
		for row in self.rows(payload):
			when = dealTime(row.get('snapshotTimeUTC') or row.get('snapshotTime'))
			if not self.complete(when, now):
				continue
			if self.last[pair] is not None and when <= self.last[pair]:
				continue
			if not self.live and when > self.dtto:
				continue
			event = self.event(pair, row)
			if event is None:
				self.logger.warning(
					"%s: IG row at %s has no bid or no ask; skipped" % (pair, when))
				continue
			self.last[pair] = when
			sent += 1
			self.queue_event(event)
		self.num_candles[pair] += sent
		return sent

	def stream_to_queue(self):
		try:
			self.queue_event(StatusEvent('STARTED'))
			while True:
				done = 0
				for pair in self.pairs:
					sent = self.poll(pair)
					if not self.live:
						self.logger.debug("%s %d block sent %d candles"
										  % (pair, self.num_blocks[pair], sent))
					done += sent

				if not self.live:
					# Offline, the window walks forward on self.last until a
					# pass brings nothing back; unlike eToro, asking again
					# with a later 'from' is a different question.
					if done == 0:
						self.logger.info("Offline pass done; no further IG history")
						self.queue_event(StatusEvent('DONE'))
						return
					continue

				if self.sleep and self.sleep > 0:
					time.sleep(self.sleep)
		except Exception as exc:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			self.queue_event(StatusEvent('ERROR'))
			self.logger.debug("IG CANDLES ERROR: @%d : %s"
							  % (exc_tb.tb_lineno, str(exc)))
			return None


class IGRates(StreamHandler):
	"""
	The market snapshot for each configured instrument, polled.

	GET /markets/{epic} carries a bid and an offer along with the market's
	dealing rules, so this is one request per instrument rather than one for
	all of them - IG has no route that quotes a list. That is why it is not
	used in the trading path: it exists so a spread can be watched, and so
	marketStatus can be read before wondering why an order did not fill.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs', ['EUR_USD'])

		self.api = args.get('api') or IGAPI(setup=self.setup)
		self.sleep = int(getattr(self.setup, 'IG_POLL_SECONDS', 5))
		self.epics = dict((p, epic(p, self.setup)) for p in self.pairs)
		self.scales = dict((p, scale(p, self.setup)) for p in self.pairs)
		self.running = True

	def quit(self):
		self.running = False

	def poll(self):
		sent = 0
		for pair in self.pairs:
			status, payload = self.api.get('market', parts=(self.epics[pair],))
			if status != 200 or not payload:
				self.logger.error("%s market: status %s" % (pair, status))
				self.queue_event(StatusEvent('ERROR'))
				continue
			snapshot = payload.get('snapshot') or {}
			if snapshot.get('bid') is None or snapshot.get('offer') is None:
				# IG nulls both outside market hours rather than holding the
				# last quote, so this is the market being shut, not an error.
				self.logger.debug("%s is not quoting (%s)"
								  % (pair, snapshot.get('marketStatus')))
				continue
			factor = self.scales[pair]
			self.queue_event(TickEvent({
				'type': 'TICK',
				'instrument': pair,
				'time': dealTime(snapshot.get('updateTime')),
				'bid': float(snapshot['bid']) / factor,
				'ask': float(snapshot['offer']) / factor,
				'marketStatus': snapshot.get('marketStatus'),
			}))
			sent += 1
		return sent

	def stream_to_queue(self):
		try:
			while self.running:
				self.poll()
				if self.sleep and self.sleep > 0:
					time.sleep(self.sleep)
		except Exception as exc:
			self.queue_event(StatusEvent('ERROR'))
			self.logger.error("IG RATES ERROR: %s" % str(exc))
			return None


class IGTransactions(StreamHandler):
	"""
	The fills and closes OANDA would have pushed, asked for instead.

	It is both a handler and a source, for the same reason its eToro
	counterpart is: as a handler it listens for the CLIENTORDER
	acknowledgements the execution handler publishes, which is how it learns a
	deal exists without the wiring telling it; as a source it polls the routes
	that can answer for those deals and publishes TransactionEvents in the
	shape the money manager and the parity monitor already read.

	A deal nobody acknowledged is invisible here - a position opened from
	IG's own platform, say. That is a real blind spot and this is the honest
	place to state it: the parity monitor counts unpaired trades separately
	for exactly this kind of reason.
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs', ['EUR_USD'])

		self.api = args.get('api') or IGAPI(setup=self.setup)
		self.sleep = int(getattr(self.setup, 'IG_POLL_SECONDS', 5))
		self.enforce_expiry = bool(getattr(self.setup, 'IG_ENFORCE_EXPIRY', False))

		#: dealReference -> what we know about a deal we are waiting on
		self.deals = {}
		#: dealId -> the open position a fill produced
		self.positions = {}
		#: dealIds already reported closed, so a re-read does not repeat
		self.closed = set()
		#: how many times each reference has been asked after without an
		#: answer, so a confirmation that has expired is given up on
		self.attempts = {}
		#: dealIds gone from the account whose closing deal has not been
		#: recorded yet, and how many polls have asked after them
		self.unrecorded = {}
		self.max_attempts = int(getattr(self.setup, 'IG_CONFIRM_ATTEMPTS', 20))
		self.running = True

	def quit(self):
		self.running = False

	# ------------------------------------------------------------- listening

	def execute_event(self, event):
		kind = str(event)
		if kind == 'CLIENTORDER':
			return self.watch(event)
		if kind == 'ORDERCANCEL':
			# The execution handler sends the delete; drop the deal here so
			# the poll stops asking about it.
			reference = (getattr(event, 'dealReference', None)
						 or getattr(event, 'orderID', None))
			if reference is not None:
				self.deals.pop(reference, None)
				# and the working order it became, if IG had confirmed it:
				# an order that has been deleted will never trigger, so
				# pollFills would otherwise look for it for the rest of the
				# session.
				for deal_id, position in list(self.positions.items()):
					if position.get('resting') \
							and position.get('dealReference') == reference:
						del self.positions[deal_id]

	def watch(self, event):
		"""Start following a deal the execution handler just placed."""
		reference = getattr(event, 'dealReference', None) or getattr(event, 'id', None)
		if reference is None:
			self.logger.error("CLIENTORDER without a deal reference: %s"
							  % event.to_json())
			return
		self.deals[reference] = {
			'dealReference': reference,
			'dealId': getattr(event, 'dealId', None),
			'signalNumber': getattr(event, 'signalNumber', None),
			'instrument': getattr(event, 'instrument', None),
			'price': getattr(event, 'price', None),
			'units': getattr(event, 'units', None),
			'gtdTime': getattr(event, 'gtdTime', None),
			'stopLoss': getattr(event, 'stopLoss', None),
			'takeProfit': getattr(event, 'takeProfit', None),
			'orderType': str(getattr(event, 'orderType', '') or '').upper(),
			'resting': str(getattr(event, 'orderType', '') or '').upper() != 'MARKET',
		}
		self.attempts[reference] = 0
		self.logger.debug("watching IG deal %s for signal %s"
						  % (reference, self.deals[reference]['signalNumber']))

	# ----------------------------------------------------------------- fills

	def confirm(self, reference):
		status, payload = self.api.get('confirm', parts=(reference,))
		if status == 404:
			# Either the deal has not been processed yet or the confirmation
			# has aged out. Which of the two it is cannot be told apart from
			# here, so it is counted rather than judged.
			return None
		if status != 200 or not payload:
			self.logger.error("confirm %s: status %s" % (reference, status))
			return None
		return payload

	def pollDeals(self, now=None):
		"""Ask after every deal still without an outcome."""
		sent = 0
		for reference in list(self.deals):
			known = self.deals[reference]
			payload = self.confirm(reference)
			if payload is None:
				self.attempts[reference] = self.attempts.get(reference, 0) + 1
				if self.attempts[reference] >= self.max_attempts:
					# Said out loud, because a deal whose confirmation was
					# never read may well exist on the account: the parity
					# monitor will see it as unpaired and that is the correct
					# reading of what happened.
					self.logger.error(
						"IG never confirmed deal %s after %d attempts; it may "
						"still be live on the account"
						% (reference, self.attempts[reference]))
					del self.deals[reference]
					self.attempts.pop(reference, None)
				elif known.get('resting') and self.enforce_expiry:
					self.expire(known, now)
				continue

			status = str(payload.get('dealStatus') or '').upper()
			if status == REJECTED:
				self.logger.error("IG rejected deal %s: %s"
								  % (reference, payload.get('reason')))
				self.queue_event(TransactionEvent({
					'type': 'ORDER_REJECT',
					# the money manager knows this deal by the reference the
					# acknowledgement carried, and reads it as orderID - the
					# name OANDA's transactions use. Without it the rejection
					# reaches the manager as an event about nothing and the
					# signal is never released.
					'orderID': reference,
					'dealReference': reference,
					'instrument': known['instrument'],
					'price': known['price'],
					'units': known['units'],
					'signalNumber': known['signalNumber'],
					'rejectReason': payload.get('reason'),
					'errorMessage': payload.get('reason'),
					'time': dealTime(payload.get('date')),
				}))
				del self.deals[reference]
				continue

			if status == ACCEPTED:
				sent += self.reportFill(known, payload)
				del self.deals[reference]
				continue

			# Anything else is a confirmation in a state this project does not
			# produce - an amendment made on the platform, say - and is logged
			# rather than turned into a fill for a signal it did not come from.
			self.logger.info("IG deal %s came back as %s / %s"
							 % (reference, status, payload.get('status')))
			del self.deals[reference]
		return sent

	def outcome(self, payload):
		"""
		What the confirmation says happened to the deal, as one word.

		The per-deal status in ``affectedDeals`` is preferred over the
		top-level one because that is where IG puts the word that tells an
		open from a close; the top level describes the position and answers
		OPEN either way. Where a confirmation carries no affected deal, the
		top-level status is all there is.
		"""
		for deal in payload.get('affectedDeals') or []:
			status = str(deal.get('status') or '').upper()
			if status:
				return status
		return str(payload.get('status') or '').upper()

	def reportFill(self, known, payload):
		"""
		Publish the fill of an accepted deal.

		A resting order that IG accepted is not a fill: the working order now
		exists and will fill later, so the acknowledged deal is kept under its
		dealId and the position is picked up from GET /positions when the
		order triggers - pollFills below. Only a deal that opened a position
		right away is published as an ORDER_FILL here.

		Which of the two it is comes from the endpoint the order was sent to,
		not from the confirmation: IG answers OPEN / OPENED for a market deal
		that filled and for a working order that is merely resting, so the
		status cannot tell them apart. This is measured, and it is what the
		first live order on the demo account was for.

		A confirmation that says the deal *closed* something is not this
		project's doing - nothing here sends a closing deal - so it is logged
		and published as nothing at all.
		"""
		deal_id = payload.get('dealId')
		name = known['instrument'] or instrumentName(payload.get('epic'), self.setup)
		direction = str(payload.get('direction') or '').upper()
		size = payload.get('size')
		units = None
		if size is not None:
			units = -abs(float(size)) if direction == 'SELL' else abs(float(size))
		status = self.outcome(payload)

		if status in CLOSED or status == PARTIAL:
			self.logger.warning(
				"IG deal %s came back as %s: a position was closed by something "
				"other than this stack, so it is not published as an entry"
				% (known['dealReference'], status))
			return 0

		if known.get('resting'):
			# Accepted, but nothing is open yet: this is a working order
			# resting on the book. Remember it so the expiry we enforce and
			# the fill that follows can both find it.
			self.positions[deal_id] = {
				'dealId': deal_id,
				'dealReference': known['dealReference'],
				'signalNumber': known['signalNumber'],
				'instrument': name,
				'units': units,
				'openLevel': None,
				'stopLoss': payload.get('stopLevel') or known['stopLoss'],
				'takeProfit': payload.get('limitLevel') or known['takeProfit'],
				'gtdTime': known.get('gtdTime'),
				'orderType': known.get('orderType'),
				'opened': None,
				'resting': True,
			}
			self.logger.debug("IG working order %s accepted for signal %s"
							  % (deal_id, known['signalNumber']))
			return 0

		if status not in OPEN:
			# Not one of the words a market entry produces. Published anyway -
			# IG accepted the deal and it is about to appear among the open
			# positions - but said out loud, because it is a shape this code
			# has not seen.
			self.logger.warning("IG deal %s was accepted with status %s"
								% (known['dealReference'], status))

		when = dealTime(payload.get('date'))
		self.queue_event(TransactionEvent({
			'type': 'ORDER_FILL',
			'id': deal_id,
			'orderID': known['dealReference'],
			'dealId': deal_id,
			'dealReference': known['dealReference'],
			'instrument': name,
			'units': units,
			'price': payload.get('level'),
			'time': when,
			'reason': 'MARKET_ORDER' if not known.get('resting') else 'LIMIT_ORDER',
			'signalNumber': known['signalNumber'],
		}))
		self.positions[deal_id] = {
			'dealId': deal_id,
			'dealReference': known['dealReference'],
			'signalNumber': known['signalNumber'],
			'instrument': name,
			'units': units,
			'openLevel': payload.get('level'),
			'stopLoss': payload.get('stopLevel') or known['stopLoss'],
			'takeProfit': payload.get('limitLevel') or known['takeProfit'],
			'gtdTime': known.get('gtdTime'),
			'orderType': known.get('orderType'),
			'opened': when,
			'resting': False,
		}
		return 1

	# ------------------------------------------------------- triggered orders

	def reason(self, position):
		"""The fill reason, in the vocabulary OANDA's transactions use."""
		kind = str(position.get('orderType') or '').upper()
		if kind in ('STOP', 'LIMIT'):
			return kind + '_ORDER'
		return 'MARKET_ORDER'

	def pollFills(self, live=None):
		"""
		Publish the fill of a working order that has triggered.

		A resting order is not a position, so nothing is published when IG
		accepts it. When it triggers, IG opens a position that keeps *both*
		the working order's dealId and the dealReference this project chose -
		measured on the demo account, since neither is documented - so the
		join is on the dealId already in hand.

		Until this existed nothing ever left the resting state: a strategy
		whose entries are STOP orders, which is every strategy here, would
		have had its entries fill at IG and never hear of it, and pollCloses
		skips a resting position so the close would have been lost too.
		"""
		resting = [deal_id for deal_id, p in self.positions.items()
				   if p.get('resting')]
		if not resting:
			return 0
		if live is None:
			live = self.open_positions()
		if live is None:
			return 0

		sent = 0
		for deal_id in resting:
			row = live.get(deal_id)
			if row is not None:
				sent += self.reportTrigger(deal_id, row.get('position') or {})
		return sent

	def reportTrigger(self, deal_id, position):
		"""
		Publish one triggered order as an entry, from the position IG holds.

		The levels come from the position rather than from the order: IG
		fills at the level the market reached, which on a stop is not the
		level that was asked for, and a fill reported at the requested level
		would understate every gap.
		"""
		known = self.positions[deal_id]
		when = dealTime(position.get('createdDateUTC') or position.get('createdDate'))
		level = position.get('level')
		units = known.get('units')
		if units is None and position.get('size') is not None:
			size = abs(float(position['size']))
			units = -size if str(position.get('direction')).upper() == 'SELL' else size

		self.queue_event(TransactionEvent({
			'type': 'ORDER_FILL',
			'id': deal_id,
			'orderID': known['dealReference'],
			'dealId': deal_id,
			'dealReference': known['dealReference'],
			'instrument': known['instrument'],
			'units': units,
			'price': level,
			'time': when,
			'reason': self.reason(known),
			'signalNumber': known['signalNumber'],
		}))
		known.update({
			'units': units,
			'openLevel': level,
			'opened': when,
			'resting': False,
			'stopLoss': position.get('stopLevel') or known.get('stopLoss'),
			'takeProfit': position.get('limitLevel') or known.get('takeProfit'),
		})
		self.logger.debug("IG working order %s triggered at %s for signal %s"
						  % (deal_id, level, known['signalNumber']))
		return 1

	# ---------------------------------------------------------------- closes

	def open_positions(self):
		"""Every position IG currently holds, by dealId."""
		status, payload = self.api.get('positions')
		if status != 200 or payload is None:
			self.logger.error("positions: status %s" % status)
			return None
		out = {}
		for row in payload.get('positions') or []:
			position = row.get('position') or {}
			if position.get('dealId') is not None:
				out[position['dealId']] = row
		return out

	def since(self):
		"""
		The 'from' of the transaction read: the oldest position still open.

		Deliberately not widened to a fixed lookback - a wider window costs
		nothing in requests but pulls in trades from before this session,
		which have no signal to be joined to and would be reported as closes
		that never opened here.
		"""
		days = [p['opened'] for p in self.positions.values()
				if p.get('opened') is not None]
		when = min(days) if days else utcnow()
		return priceTime(when)

	def history(self):
		status, payload = self.api.get(
			'transactions', params={'type': 'ALL_DEAL', 'from': self.since(),
									'pageSize': 0})
		if status != 200 or payload is None:
			self.logger.error("transactions: status %s" % status)
			return None
		return payload.get('transactions') or []

	def closes(self):
		"""
		The closing deals IG has recorded, by the dealId each one closed.

		Read from GET /history/activity, and that choice is the point of this
		method. The transaction history is the only route with a profit
		figure on it, and it *lags*: a position closed at 23:12:18 was still
		absent from the transactions at 23:13:25, while an earlier close had
		appeared there within five seconds. Measured on the demo account, and
		it matters because the close is noticed within one poll - five
		seconds - so the level would have been read as missing and published
		as nothing.

		The activity is current, names the dealId it affected, and carries
		the level the closing deal was done at, which is the only thing the
		leg can be inferred from.
		"""
		status, payload = self.api.get('activity', params={
			'from': self.since(), 'detailed': 'true', 'pageSize': 50})
		if status != 200 or payload is None:
			self.logger.error("activity: status %s" % status)
			return None

		out = {}
		for row in payload.get('activities') or []:
			details = row.get('details') or {}
			for action in details.get('actions') or []:
				kind = str(action.get('actionType') or '').upper()
				affected = action.get('affectedDealId')
				if affected is None or 'CLOSE' not in kind:
					continue
				out[affected] = {
					'level': details.get('level'),
					'date': row.get('date'),
					'dealReference': details.get('dealReference'),
				}
		return out

	def pollCloses(self, live=None):
		"""
		Report the positions IG no longer holds.

		The join is done on what is *missing* from GET /positions rather than
		on what appears in the history, because the history keys a transaction
		by a reference this project did not choose and the position list keys
		by the dealId the confirmation gave us. Matching on presence is the
		only join both sides can make.

		Missing from the open positions is not enough to report a close,
		though: the closing deal has to have been *recorded* before there is
		a level to report, and until it is the position is left alone and
		asked after again. A close published without a level is worse than a
		close published late - an absent price becomes 0.0 on the way into an
		event, which is a number the ledger would believe.

		``live`` is passed in by poll() so that one read of the open positions
		serves both the fills and the closes; on its own this reads them.
		"""
		if not self.positions:
			return 0
		if live is None:
			live = self.open_positions()
		if live is None:
			return 0

		gone = [deal_id for deal_id, p in self.positions.items()
				if deal_id not in live and not p.get('resting')
				and deal_id not in self.closed]
		if not gone:
			return 0

		closing = self.closes()
		if closing is None:
			return 0
		# the profit lives only here, and may not have landed yet
		rows = self.history() or []

		sent = 0
		for deal_id in gone:
			record = closing.get(deal_id)
			if record is None:
				self.unrecorded[deal_id] = self.unrecorded.get(deal_id, 0) + 1
				if self.unrecorded[deal_id] < self.max_attempts:
					continue
				self.logger.error(
					"position %s is gone from the account and no closing deal "
					"was recorded after %d polls; reporting the close without "
					"a level" % (deal_id, self.unrecorded[deal_id]))
			position = self.positions.pop(deal_id)
			self.unrecorded.pop(deal_id, None)
			sent += self.reportClose(position, self.rowFor(position, rows),
									 record)
			self.closed.add(deal_id)
		return sent

	def rowFor(self, position, rows):
		"""
		The history row that belongs to a position, or an empty one.

		IG's transaction history does not carry the dealId, so the match is on
		the instrument and on the open level the confirmation reported - the
		two things both records state. Where no row matches, the close is
		still published, with the levels this handler already knows and
		without a profit figure: a close that happened is worth reporting
		even when the ledger entry for it cannot be found.
		"""
		want = position.get('openLevel')
		for row in rows:
			if want is None:
				break
			try:
				if abs(float(row.get('openLevel')) - float(want)) < 1e-9:
					return row
			except (TypeError, ValueError):
				continue
		return {}

	def reportClose(self, position, row, record=None):
		"""
		Publish the close of one position.

		The level comes from the activity record where there is one, and from
		the transaction row otherwise - the two agree, and only the first is
		prompt. reason is inferred from it - see lib/closereason.py - and the
		event says so, so nothing downstream mistakes an inference for
		something the broker reported. accountBalance is None because neither
		route carries it, and inventing one would put a figure in the ledger
		that no read ever returned.

		A close with no level at all is published without a price rather than
		with one: an event coerces a missing price to 0.0, and a trade that
		closed at zero is a story the ledger would tell for ever.
		"""
		level = number((record or {}).get('level'))
		if level is None:
			level = number(row.get('closeLevel'))
		reason = closeReason(level, position.get('stopLoss'),
							 position.get('takeProfit'))
		if reason == UNKNOWN:
			self.logger.warning(
				"position %s closed at %s, which is not clearly the stop (%s) "
				"or the target (%s); reporting the outcome as UNKNOWN"
				% (position['dealId'], level, position.get('stopLoss'),
				   position.get('takeProfit')))

		when = (row.get('dateUtc') or (record or {}).get('date')
				or row.get('date'))
		payload = {
			'type': 'ORDER_FILL',
			'orderID': position['dealReference'],
			'dealId': position['dealId'],
			'dealReference': position['dealReference'],
			'instrument': position['instrument'],
			'units': position['units'],
			'time': dealTime(when) if when is not None else utcnow(),
			'pl': profitAndLoss(row.get('profitAndLoss')),
			'accountBalance': None,
			'tradesClosed': [{'tradeID': position['dealId'],
							  'units': position['units'],
							  'price': level}],
			'reason': reason,
			'reasonInferred': True,
			'signalNumber': position['signalNumber'],
			'openLevel': number(row.get('openLevel')) or position.get('openLevel'),
		}
		if level is not None:
			payload['price'] = level
		self.queue_event(TransactionEvent(payload))
		return 1

	# ---------------------------------------------------------------- expiry

	def expire(self, known, now=None):
		"""
		Cancel a resting order whose own expiry has passed.

		IG *does* have an expiry - GOOD_TILL_DATE with a goodTillDate - and
		execution/ig.py sends it, so this is off by default and there is
		normally nothing for it to do. It exists for the case where an order
		was placed without one, and it works the way the eToro path does:
		publish an OrderCancelEvent, which the execution handler turns into a
		delete and the simulator applies to its own book, so both sides drop
		the order together.
		"""
		when = known.get('gtdTime')
		if when is None:
			return False
		# Local time deliberately, where everything else here is UTC: gtdTime
		# is not a broker timestamp. The strategies build it with
		# datetime.today().replace(hour=...), so AG01's "expire at 23:59:59"
		# means the operator's evening, and converting it would move the
		# expiry by the machine's offset.
		now = now if now is not None else datetime.datetime.today()
		if when > now:
			return False

		self.logger.info("IG deal %s is past its gtdTime %s; cancelling it here"
						 % (known['dealReference'], when))
		self.queue_event(OrderCancelEvent({
			'dealReference': known['dealReference'],
			'dealId': known.get('dealId'),
			'price': known['price'],
			'instrument': known['instrument'],
			'reason': 'GTD_EXPIRY_ENFORCED',
		}))
		self.deals.pop(known['dealReference'], None)
		return True

	# ---------------------------------------------------------------- stream

	def poll(self, now=None):
		"""
		One cycle: confirmations, then triggers, then closes.

		The open positions are read once and handed to both of the last two.
		Two reads a cycle would be a third of the per-minute allowance for a
		list that cannot have changed between them.
		"""
		sent = self.pollDeals(now)
		live = self.open_positions() if self.positions else None
		sent += self.pollFills(live)
		sent += self.pollCloses(live)
		return sent

	def stream_to_queue(self):
		try:
			while self.running:
				self.poll()
				if self.sleep and self.sleep > 0:
					time.sleep(self.sleep)
		except Exception as exc:
			self.queue_event(StatusEvent('ERROR'))
			self.logger.error("IG TRANSACTIONS ERROR: %s" % str(exc))
			return None


def profitAndLoss(value):
	"""
	IG's profit figure as a number.

	It comes back as a string with the currency stuck to the front - 'E-4.20'
	for a loss of 4.20 euro - so the digits have to be pulled out. Anything
	that does not parse becomes None rather than zero: a missing P&L is not a
	flat trade, and the difference matters to whatever reads the ledger.
	"""
	if value is None:
		return None
	if isinstance(value, (int, float)):
		return float(value)
	text = "".join(c for c in str(value) if c in '0123456789.-+')
	try:
		return float(text)
	except ValueError:
		return None
