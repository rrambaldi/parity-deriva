from decimal import Decimal, getcontext, ROUND_HALF_DOWN
import pandas as pd
import datetime
import logging
import json
import time
import os
import collections
import heapq
import threading
from parity_deriva.etc import settings
from parity_deriva.data import store

import requests

from parity_deriva.backtest.driver import Cancelled
from parity_deriva.event.event import CandleEvent
from parity_deriva.event.event import StatusEvent
from parity_deriva.trading.handler import StreamHandler


#: how often the preparing says where it is, as a share of the bars
READ_STEPS = 100

#: the stores read, as frames, by (path, granularity, mtime, size): a sweep
#: runs the same instrument dozens of times, and each run used to read and
#: unpack the whole file again - both of its series, eleven years of M5
#: included - to use a window of it. A file written since (an import) has
#: another mtime and size, so it is read afresh and its old frame dropped.
#: ponytail: four frames, ~90 MB each for eleven years of M5; a bigger LRU
#: if sweeps over several instruments at once start missing
FRAMES = collections.OrderedDict()
FRAMES_MAX = 4
_frames = threading.Lock()


def frame(path, granularity):
	"""
	The store's frame for a granularity, and whether it came from memory.

	Deduplicated keeping the last row of a time, as the dictionary each run
	used to build from it did. Shared: nobody may write to it.
	"""
	st = os.stat(path)
	key = (path, granularity, st.st_mtime_ns, st.st_size)
	with _frames:
		if key in FRAMES:
			FRAMES.move_to_end(key)
			return FRAMES[key], True
	f = store.load(path, granularity)
	f = f[~f.index.duplicated(keep='last')]
	with _frames:
		for old in [k for k in FRAMES if k[:2] == key[:2]]:
			del FRAMES[old]
		FRAMES[key] = f
		while len(FRAMES) > FRAMES_MAX:
			FRAMES.popitem(last=False)
	return f, False


COLUMNS = [(side, part, '%s_%s' % (side, part))
		   for side in ('ask', 'bid', 'mid') for part in ('o', 'h', 'l', 'c')]


class ForexCandles(StreamHandler):
	sampling = 10
	alignmentTimezone = 'Europe/Rome'
	last = {}
	url = {}

	def __init__( self, **args):

		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args,'setup', settings)

		# who to tell how far the reading has got, or None. Set directly
		# rather than through _set, which cannot hold a default of None.
		self.progress = args.get('progress')

		self._set(args,'pairs','DE30_EUR')
		self._set(args,'granularity','M5')

		delta_d = 0
		delta_m = 0
		delta_h = 0
		delta_s = 0
		if self.granularity[:1] == 'S':
			delta_s = int(self.granularity[1:])
		if self.granularity[:1] == 'M':
			delta_m = int(self.granularity[1:])
		if self.granularity[:1] == 'H':
			delta_h = int(self.granularity[1:])
		if self.granularity[:1] == 'D':
			delta_d = 1
		if self.granularity[:1] == 'W':
			delta_d = 7
		self.interval = pd.Timedelta(days=delta_d,minutes=delta_m,seconds=delta_s,hours=delta_h)

		self.batch_size = 2
		self._set(args,'dtfrom', datetime.datetime(1970,1,1,0,0,0))
		self._set(args, 'batch_size', 500)
		self._set(args, 'dtto',  pd.to_datetime(datetime.datetime.today()))
		self._set(args, 'sleep', 0)

		self.logger.debug("SLEEP %d" % self.sleep)
		
		self.frames = {}
		self.curr = {}
		self.last = {}
		self.candles = {}
		self.samples = {}
		self.cent = {}
		for p in self.pairs:
			store_name  = "%s.hd5" % p
			self.candles[p] = 0
			self.curr[p] = pd.to_datetime(self.dtfrom)
			# a granularity the store does not hold is built from the finest
			# one it does, so a D run replays off an M5-only store. Read once
			# per file and granularity for the whole process (frame())
			path = os.path.join(self.setup.DATA_DIR, store_name)
			label = "%s %s" % (p, self.granularity)
			cached = any(k[:2] == (path, self.granularity) for k in list(FRAMES))
			self.report(("%s: in memory" if cached else "%s: reading the file") % label, 0, 0)
			s, cached = frame(path, self.granularity)
			self.last[p] = min(s.index.max(), self.dtto)
			self.samples[p] = len(s.loc[self.curr[p]:self.last[p]])
			self.logger.debug("%s %d samples to go%s"
							  % (p, self.samples[p], " (in memory)" if cached else ""))
			self.cent[p] = max(1, self.samples[p] // READ_STEPS)
			self.frames[p] = s


	def report(self, stage, done, total):
		"""
		How far the reading has got, for whoever asked to be told.

		The reading is the part with nothing to show: a run over eleven years
		of M5 spends the best part of a minute here, before a single bar has
		reached the bus, and a page that says nothing for that minute looks
		like a page that has hung.

		The listener answers, like the one ledger.Progress reports to: False
		stops the run. That refusal is an exception and it must not be caught
		by the catch-all in stream_to_queue, which is why that one lets a
		Cancelled through.
		"""
		if self.progress is None:
			return
		self.progress(stage, done, total)

	def to_candle(self, tm, row):
		"""
		Turn one flat store row ({ask,bid,mid}_{o,h,l,c} + volume) back into
		the nested OANDA candle layout that CandleEvent expects.
		"""
		out = { 'time': tm.to_pydatetime() if hasattr(tm,'to_pydatetime') else tm
			, 'volume': int(row['volume'])
			, 'complete': True }
		for x in ['ask','bid','mid']:
			out[x] = dict((p, row["%s_%s" % (x,p)]) for p in ['o','h','l','c'])
		return out

	def rows(self, pair):
		"""
		The times and rows the walk of the old stream_to_queue emitted: the
		bars at the first time plus a whole number of intervals, up to the
		last time of every pair (the first one even past it), each as the
		store holds it. Selected on the frame's index instead of walking
		every interval - weekends and all - through a dictionary.
		"""
		f = self.frames[pair]
		start = pd.Timestamp(self.curr[pair])
		last = max(pd.Timestamp(v) for v in self.last.values())
		index = f.index
		keep = (index >= start) & ((index <= last) | (index == start))
		keep &= ((index - start) % self.interval) == pd.Timedelta(0)
		window = f[keep]
		columns = dict((name, window[name].tolist()) for _s, _p, name in COLUMNS)
		volume = window['volume'].tolist()
		for i, tm in enumerate(window.index):
			out = {'time': tm.to_pydatetime(), 'volume': int(volume[i]),
				   'complete': True}
			for side in ('ask', 'bid', 'mid'):
				out[side] = dict((part, columns['%s_%s' % (side, part)][i])
								 for part in ('o', 'h', 'l', 'c'))
			yield tm, out

	def stream_to_queue(self):
		try:
			sev = StatusEvent('STARTED')
			self.queue_event(sev)
			# pairs in their order within a time, as the walk emitted them
			def stream(n, pair):
				for tm, candle in self.rows(pair):
					yield tm, n, pair, candle
			streams = [stream(n, pair) for n, pair in enumerate(self.pairs)]
			for _tm, _n, pair, candle in heapq.merge(*streams, key=lambda r: (r[0], r[1])):
				cev = CandleEvent(candle)
				cev.granularity = self.granularity
				cev.instrument  = pair
				self.candles[pair] += 1
				self.queue_event(cev)
				if self.candles[pair] % self.cent[pair] == 0:
					self.report("%s %s: preparing the bars" % (pair, self.granularity),
								self.candles[pair], self.samples[pair])
			for pair in self.pairs:
				self.curr[pair] = max(pd.Timestamp(self.curr[pair]),
									  pd.Timestamp(self.last[pair]) + self.interval)
			self.logger.info("Max time reached on all pairs. Streamer stopped")
			sev = StatusEvent('DONE')
			self.queue_event(sev)
			return
		except Cancelled:
			# asked for, not a fault. Swallowing it here would leave the run
			# going on half a stream, which is the one outcome worth more
			# care than the rest of this catch-all
			raise
		except Exception as e:
			sev = StatusEvent('ERROR')
			self.queue_event(sev)
			self.logger.error("Caught exception when connecting to stream\n" + str(e))
			return None
