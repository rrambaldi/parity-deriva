"""
Twelve Data candles, one request per bar per instrument.

The other polled sources (eToro, IG, MT5) ask every few seconds and let the
broker's quota absorb it. This one cannot: 800 credits a day is 288 bars of
M5, so the loop is built around the clock rather than around a sleep. For
each instrument it knows when the next bar closes, waits until then plus
TWELVEDATA_POLL_DELAY, asks once for the last two bars, and moves on to the
bar after - whether or not the bar it wanted had appeared yet.

That last clause is the whole design. A new bar shows up on the vendor's
side a minute or two after it closes (measured: see lib/twelvedata.py), and
a poller that asked again until it appeared - IGCandles.due() does exactly
that, against a quota that can take it - would spend three to six credits a
bar and run out by the afternoon. Asking for two bars instead of one means
the bar that was not there yet is picked up on the next boundary, one bar
late and for nothing extra; and the previous bar is read again every time,
so a bar the vendor re-served with other prices is seen and said out loud.

One series, so bid and ask are a model or nothing: TWELVEDATA_SPREAD, or the
market folder's spread.json, is the same rule as eToro's (lib/spread.py), off
until measured. scripts/spread_profile.py builds that file.

    ponytail: polls through the weekend too - 288 empty calls a day per
    instrument fit the budget. Pause FX from Friday 22:00 to Sunday 22:00
    UTC the day a third instrument has to fit.
"""

import datetime
import logging
import sys
import time

import pandas as pd

from parity_deriva.etc import settings
from parity_deriva.event.event import CandleEvent, StatusEvent
from parity_deriva.lib.spread import spreadModel
from parity_deriva.lib.twelvedata import TwelveDataAPI, candleTime, utcnow
from parity_deriva.lib.utils import granularityToTimedelta
from parity_deriva.trading.handler import StreamHandler

#: how many bars a history request may carry: the vendor's own ceiling
MAX_BARS = 5000
#: the longest the loop sleeps in one go, so a SIGTERM is honoured promptly
NAP = 30.0


class TwelveDataCandles(StreamHandler):

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', settings)
		self._set(args, 'pairs', ['EUR_USD'])
		self._set(args, 'granularity', 'M5')
		self.api = args.get('api') or TwelveDataAPI(setup=self.setup)
		self.period = granularityToTimedelta(self.granularity)
		if self.period is None:
			raise ValueError("unknown granularity %r" % self.granularity)
		self.delay = float(getattr(self.setup, 'TWELVEDATA_POLL_DELAY', 20))
		self.spread = spreadModel(self.setup, 'TWELVEDATA_SPREAD')
		self.live = True
		self.dtfrom = None
		self.dtto = None
		if self._set(args, 'dtfrom'):
			self.live = False
			self._set(args, 'dtto', utcnow())
		self.last = dict((p, None) for p in self.pairs)
		self.seen = dict((p, {}) for p in self.pairs)
		self.nextCall = dict((p, None) for p in self.pairs)
		self.num_blocks = dict((p, 0) for p in self.pairs)
		self.num_candles = dict((p, 0) for p in self.pairs)

	# ----------------------------------------------------------------- bars

	def complete(self, when, now=None):
		"""Has this bar's period elapsed? The vendor sends no flag."""
		now = now if now is not None else utcnow()
		return when + self.period <= now

	def event(self, pair, row):
		"""One CandleEvent, with bid and ask only if a spread was configured."""
		mid = dict((leg, float(row[name])) for leg, name in
				   (('o', 'open'), ('h', 'high'), ('l', 'low'), ('c', 'close')))
		data = {
			'time': candleTime(row['datetime']),
			'volume': int(float(row.get('volume') or 0)),
			'complete': True,
			'mid': mid,
		}
		bid, ask = self.spread.apply(pair, mid, data['time'], self.period)
		if bid is not None:
			# only when modelled: CandleEvent turns a None side into a dict
			# of zeros, and a strategy would read a bid of 0.0
			data['bid'], data['ask'] = bid, ask
		event = CandleEvent(data)
		event.instrument = pair
		event.granularity = self.granularity
		return event

	def emit(self, pair, rows, now=None):
		"""Emit the complete bars newer than the last one sent; count them."""
		sent = 0
		for row in rows:
			when = candleTime(row['datetime'])
			if not self.complete(when, now):
				continue
			prices = tuple(row.get(k) for k in ('open', 'high', 'low', 'close'))
			if self.last[pair] is not None and when <= self.last[pair]:
				before = self.seen[pair].get(when)
				if before is not None and before != prices:
					self.logger.warning(
						"REVISED %s %s: served %s, now %s; the strategy acted "
						"on the first" % (pair, when, before, prices))
					self.seen[pair][when] = prices
				continue
			if not self.live and when > self.dtto:
				continue
			self.queue_event(self.event(pair, row))
			self.last[pair] = when
			self.seen[pair][when] = prices
			for old in sorted(self.seen[pair])[:-3]:
				del self.seen[pair][old]
			sent += 1
		self.num_candles[pair] += sent
		return sent

	# ----------------------------------------------------------------- live

	def poll(self, pair, now=None):
		status, payload, rows = self.api.timeSeries(pair, self.granularity, outputsize=2)
		self.num_blocks[pair] += 1
		if not rows:
			if payload is None or (isinstance(payload, dict)
								   and payload.get('code') != 'budget'):
				self.queue_event(StatusEvent('ERROR'))
			return 0
		return self.emit(pair, rows, now)

	def schedule(self, pair, now=None):
		"""The instant of the next request: the next close, plus the delay."""
		now = now if now is not None else utcnow()
		boundary = pd.Timestamp(now).floor(self.period) + self.period
		self.nextCall[pair] = boundary.to_pydatetime() + datetime.timedelta(seconds=self.delay)
		return self.nextCall[pair]

	def due(self, pair, now=None):
		now = now if now is not None else utcnow()
		return self.nextCall[pair] is None or now >= self.nextCall[pair]

	def history(self, pair):
		"""One request for the window asked for: a warm-up, not a stream."""
		since, until = self.dtfrom, self.dtto
		status, payload, rows = self.api.timeSeries(
			pair, self.granularity, outputsize=MAX_BARS, start=since, end=until,
			reserve=False)
		self.num_blocks[pair] += 1
		if not rows:
			if payload is None or (isinstance(payload, dict)
								   and payload.get('code') != 'budget'):
				self.queue_event(StatusEvent('ERROR'))
			return 0
		return self.emit(pair, rows, until)

	def stream_to_queue(self):
		try:
			self.queue_event(StatusEvent('STARTED'))
			if not self.live:
				for pair in self.pairs:
					sent = self.history(pair)
					self.logger.debug("%s history sent %d candles" % (pair, sent))
				self.queue_event(StatusEvent('DONE'))
				return
			while True:
				now = utcnow()
				for pair in self.pairs:
					if not self.due(pair, now):
						continue
					self.poll(pair, now)
					self.schedule(pair, now)
				soonest = min(self.nextCall.values())
				wait = (soonest - utcnow()).total_seconds()
				time.sleep(min(NAP, max(1.0, wait)))
		except Exception as exc:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			self.queue_event(StatusEvent('ERROR'))
			self.logger.error("TWELVEDATA CANDLES ERROR: @%d : %s"
							  % (exc_tb.tb_lineno, str(exc)))
			return None
