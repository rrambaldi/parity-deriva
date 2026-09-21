"""
Keeping a client under a published rate limit, for any broker that has one.

This started inside lib/etoro.py, where a 429 on the write pool is a lost
order and a 429 on the market pool is a candle a strategy never sees. IG and
Interactive Brokers publish limits of their own - IG counts trading and
non-trading requests separately and meters historical price *points* by the
week, IB caps the gateway globally per second - so the same counter is now
shared rather than written three times with three sets of off-by-ones.

The shape is deliberately dumb: count what has been spent in the trailing
window, sleep before the call that would exceed it. It does not read
RateLimit headers, because not every broker sends them and a limiter that
only works where they are sent is a limiter nobody can reason about. What it
does honour is a Retry-After, through penalise(), so a refusal still slows
the client down.
"""

import collections
import time


class RateLimiter(object):
	"""
	Keeps one pool's request rate under its published limit.

	A pool is whatever the broker actually meters - for eToro a group of
	endpoints, for IG the distinction between trading and everything else -
	so the caller decides what shares a limiter. Pacing per route when the
	broker meters per group would not correspond to anything enforced.
	"""

	def __init__(self, limit, window, sleep=time.sleep, clock=time.time):
		self.limit = limit
		self.window = window
		self.calls = collections.deque()
		self.until = 0.0
		self._sleep = sleep
		self._clock = clock

	def _prune(self, now):
		while self.calls and now - self.calls[0] >= self.window:
			self.calls.popleft()

	def take(self):
		now = self._clock()
		if self.until > now:
			self._sleep(self.until - now)
			now = self._clock()
		self._prune(now)
		if len(self.calls) >= self.limit:
			wait = self.window - (now - self.calls[0])
			if wait > 0:
				self._sleep(wait)
				now = self._clock()
				self._prune(now)
		self.calls.append(now)

	def penalise(self, seconds):
		"""Refuse to spend anything for this many seconds (a 429's Retry-After)."""
		try:
			seconds = float(seconds)
		except (TypeError, ValueError):
			return
		if seconds > 0:
			self.until = self._clock() + seconds


class Allowance(object):
	"""
	A quota the broker reports back, rather than one we count ourselves.

	IG meters historical price *points* - 10,000 a week - and reports what is
	left in the metadata of every price response. That is a budget no local
	counter can track: it is consumed by every application using the same key,
	including a browser session someone left open, so the only honest figure
	is the one the API just stated.

	This holds the last such figure and says when it is worth warning about.
	It deliberately does not block: a limit we did not count down is not one
	to enforce guesses against, and a run stopped by our own arithmetic when
	the broker would have served the data is the worse failure.
	"""

	def __init__(self, warn_below=0.1):
		self.remaining = None
		self.total = None
		self.expiry = None
		self.warn_below = warn_below

	def update(self, remaining, total=None, expiry=None):
		try:
			self.remaining = int(remaining)
		except (TypeError, ValueError):
			return False
		if total is not None:
			try:
				self.total = int(total)
			except (TypeError, ValueError):
				self.total = None
		self.expiry = expiry
		return True

	def low(self):
		"""Is what is left small enough to be worth saying out loud?"""
		if self.remaining is None or not self.total:
			return False
		return float(self.remaining) / float(self.total) < self.warn_below

	def __str__(self):
		if self.remaining is None:
			return "unknown"
		if self.total:
			return "%d of %d left, resets in %ss" % (
				self.remaining, self.total, self.expiry)
		return "%d left" % self.remaining
