"""
Twelve Data, the price feed nobody trades on.

Every other client in lib/ talks to a broker, and a broker's candles are the
prices that broker filled at. Twelve Data is a data vendor: its EUR/USD bar
is a bar nobody here can buy, which is exactly why the paper session runs on
it - a simulation on a broker's own candles measures that broker against
itself, and a simulation on a third party's measures every broker against
the same yardstick.

What was measured on the basic plan on 2026-09-24, with this project's key:

    /api_usage           plan basic, 8 credits a minute, 800 a day - and the
                         call itself costs one, so the budget is counted here
                         and not asked for
    /time_series         1 credit per symbol whatever `outputsize` (1..5000)
    freshness            forex is real time: at 10:26:30 UTC the 10:25 bar
                         was already there, still forming; at 10:31:01 the
                         10:30 bar was not there yet. A closed bar is there
                         at once, a new one a minute or two later
    symbols              EUR/USD and GBP/USD answer; GDAXI answers 404 on
                         this plan. TWELVEDATA_INSTRUMENTS is filled by hand
                         for the same reason ETORO_INSTRUMENTS is
    volume               absent on forex

800 a day is 288 bars of M5 per instrument with 224 to spare, so two
instruments at every bar fit and three do not. The daily count lives in
candles.db (data/candledb.py), where every session can read it, and a call
that would spend past TWELVEDATA_DAILY_LIMIT - TWELVEDATA_RESERVE is refused
before it is made. The reserve is for the warm-up a strategy asks for when a
session starts, which is one call of up to 5000 bars.
"""

import datetime
import json
import logging
import time

import requests

from parity_deriva.etc import settings
from parity_deriva.lib.ratelimit import RateLimiter

HOST = 'api.twelvedata.com'
SOURCE = 'twelvedata'

#: this project's granularity as Twelve Data spells it. Refuses the rest:
#: rounding H3 to 4h would hand a strategy bars it did not ask for
INTERVALS = {
	'M1': '1min', 'M5': '5min', 'M15': '15min', 'M30': '30min',
	'H1': '1h', 'H4': '4h', 'D': '1day',
}


class TwelveDataError(Exception):
	pass


def interval(granularity):
	if granularity in INTERVALS:
		return INTERVALS[granularity]
	raise TwelveDataError("Twelve Data has no interval for %r; it serves %s"
						  % (granularity, ", ".join(sorted(INTERVALS))))


def symbol(instrument, setup=None):
	"""
	'EUR_USD' as Twelve Data spells it, from configuration only.

	Not derived: 'DE30_EUR' is not 'DE30/EUR' and the index the vendor
	serves, if any, is a different basket under a different ticker. An
	unmapped instrument raises rather than guessing, as it does on eToro.
	"""
	cfg = setup if setup is not None else settings
	table = getattr(cfg, 'TWELVEDATA_INSTRUMENTS', {}) or {}
	if instrument not in table:
		raise TwelveDataError(
			"%s is not in TWELVEDATA_INSTRUMENTS; resolve it with "
			"scripts/twelvedata_instruments.py and add it" % instrument)
	return table[instrument]


def candleTime(text):
	"""'2026-09-24 10:25:00' (asked for in UTC) as a naive UTC datetime."""
	return datetime.datetime.strptime(str(text)[:19], '%Y-%m-%d %H:%M:%S')


def utcnow():
	return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class TwelveDataAPI(object):
	"""
	The REST client, paced against the per-minute limit and stopped by the
	daily one. Returns (status, payload) like the other clients in lib/, with
	(None, None) when the request could not be made and (None, {'status':
	'error', 'code': 'budget', ...}) when it was not allowed to be.
	"""

	def __init__(self, setup=None, db=None):
		self.setup = setup if setup is not None else settings
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self.key = getattr(self.setup, 'TWELVEDATA_API_KEY', '') or ''
		if not self.key:
			raise TwelveDataError("TWELVEDATA_API_KEY is not set: put it in .env")
		self.minute = RateLimiter(int(getattr(self.setup, 'TWELVEDATA_MINUTE_LIMIT', 8)), 60)
		self.daily = int(getattr(self.setup, 'TWELVEDATA_DAILY_LIMIT', 800))
		self.reserve = int(getattr(self.setup, 'TWELVEDATA_RESERVE', 40))
		if db is None:
			from parity_deriva.data.candledb import CandleDB
			db = CandleDB(getattr(self.setup, 'CANDLE_DB'))
		self.db = db

	# ---------------------------------------------------------------- budget

	def spent(self):
		return self.db.spentToday(SOURCE)

	def allowed(self, credits=1, reserve=True):
		"""Can this many credits be spent now without touching the reserve?"""
		ceiling = self.daily - (self.reserve if reserve else 0)
		return self.spent() + credits <= ceiling

	def budget(self):
		return {'calls_today': self.spent(), 'limit': self.daily,
				'reserve': self.reserve, 'last_call': self.db.lastCall(SOURCE)}

	# ----------------------------------------------------------------- calls

	def get(self, path, credits=1, reserve=True, **params):
		spent = self.spent()
		ceiling = self.daily - (self.reserve if reserve else 0)
		if spent + credits > ceiling:
			self.logger.error("Twelve Data budget: %d of %d credits spent today, "
							  "%d kept in reserve; %s not asked"
							  % (spent, self.daily, self.reserve, path))
			return None, {'status': 'error', 'code': 'budget',
						  'message': 'daily budget spent'}
		self.minute.take()
		query = dict(params)
		query.setdefault('timezone', 'UTC')
		query.setdefault('format', 'JSON')
		started = time.time()
		try:
			requests.packages.urllib3.disable_warnings()
			session = requests.Session()
			req = requests.Request('GET', "https://%s/%s" % (HOST, path.lstrip('/')),
								   params=dict(query, apikey=self.key))
			resp = session.send(req.prepare(), stream=False, verify=True)
		except Exception as exc:
			self.logger.error("Twelve Data %s failed: %s" % (path, exc))
			self._log(query, None, started, credits)
			return None, None
		status = getattr(resp, 'status_code', None)
		payload = None
		text = getattr(resp, 'text', '') or ''
		if text:
			try:
				payload = json.loads(text)
			except ValueError:
				self.logger.error("Twelve Data %s returned an unparseable body" % path)
		# the vendor's own errors come as {"status": "error", "code": N}
		code = (payload or {}).get('code') if isinstance(payload, dict) else None
		if status == 429 or code == 429:
			retry = (getattr(resp, 'headers', {}) or {}).get('Retry-After')
			self.minute.penalise(retry if retry is not None else 60)
			self.logger.error("Twelve Data rate limited, holding off %s s" % (retry or 60))
		elif isinstance(payload, dict) and payload.get('status') == 'error':
			self.logger.error("Twelve Data %s: %s %s"
							  % (path, code, payload.get('message')))
		self._log(query, status, started, credits)
		return status, payload

	def _log(self, query, status, started, credits):
		try:
			self.db.logCall(SOURCE, query.get('symbol'), query, status,
							int((time.time() - started) * 1000), credits)
			self.logger.info("Twelve Data %s %s -> %s; %d/%d credits today"
							 % (query.get('symbol'), query.get('interval', ''),
								status, self.spent(), self.daily))
		except Exception as exc:
			# the count is a safeguard, not the reason the session runs
			self.logger.error("Twelve Data call not counted: %s" % exc)

	def timeSeries(self, instrument, granularity, outputsize=2, start=None, end=None,
				   reserve=True):
		"""
		The bars of one instrument, oldest first. One credit whatever the
		size, so a warm-up asks once for everything it needs.
		"""
		params = {'symbol': symbol(instrument, self.setup),
				  'interval': interval(granularity),
				  'outputsize': int(outputsize)}
		if start is not None:
			params['start_date'] = start.strftime('%Y-%m-%d %H:%M:%S')
		if end is not None:
			params['end_date'] = end.strftime('%Y-%m-%d %H:%M:%S')
		status, payload = self.get('time_series', reserve=reserve, **params)
		if not isinstance(payload, dict) or payload.get('status') != 'ok':
			return status, payload, []
		rows = sorted(payload.get('values') or [], key=lambda r: r.get('datetime', ''))
		return status, payload, rows

	def search(self, text):
		status, payload = self.get('symbol_search', symbol=text, outputsize=30)
		return status, (payload or {}).get('data') or []
