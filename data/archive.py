"""
The bars the brokers served, kept as an archive of our own, and how far that
is from the downloaded one (the stores: dukascopy's, and what was imported).

- record(): the feeds in market.json's "record" ({"feeds": ["oanda EUR_USD
  M5", ...], "every": minutes}), the bars since the last one held, off the
  broker's API (trading/providers.history) into candles.db. A live session
  records its own as it trades (data/candledb.CandleRecorder); this records
  with none running, and places no order.
- compact(): candles.db into MARKET/archive/<provider>/<INSTRUMENT>.hd5, in
  the stores' own format, so a run reads it like a store (market.Sourced).
- compare(): two sources of one series - the stores, an archive - bar by
  bar: the difference in pips, the spreads, the bars either lacks, the shift
  that fits them best (a bar-open convention, a DST, a broker's server
  clock) and day by day, with the days that stand out.

Nothing here judges with a threshold of its own but the days that stand out:
the numbers first, an alarm once they have been looked at (PARITY_ALARM).
"""
import os

import pandas as pd

from parity_deriva.data import market, store
from parity_deriva.data.candledb import LEGS, SIDES, iso
from parity_deriva.lib.utils import granularityToTimedelta, pipSize

#: the account a feed recorded with no session is kept under in candles.db
RECORDED = 'record'
#: the most bars a comparison reads on each side
COMPARE_BARS = 600000
#: a day stands out when its p95 is this many times the days' median p95
STANDOUT = 3.0


def feeds(kept):
	"""(provider, instrument, granularity) of each feed market.json says to record."""
	out = []
	for line in (kept.get('record') or {}).get('feeds') or []:
		words = str(line).split()
		if len(words) == 3:
			out.append((words[0].lower(), words[1].upper(), words[2].upper()))
	return out


def record(service, kept, report):
	"""Every feed to record, from the bar after the last one candles.db holds of it."""
	from parity_deriva.trading import providers
	db = service.candles
	for name, instrument, granularity in feeds(kept):
		with db.connect() as connection:
			last = connection.execute(
				"SELECT MAX(time) FROM candles WHERE provider=? AND account=? AND instrument=? "
				"AND granularity=?", (name, RECORDED, instrument, granularity)).fetchone()[0]
		try:
			provider = providers.get_provider(name, service.setup)
			bars = [e for e in providers.history(
				provider, instrument, granularity,
				since=pd.Timestamp(last).to_pydatetime() if last else None) if e.complete]
		except Exception as exc:
			report("%s %s %s: %s: %s" % (name, instrument, granularity, type(exc).__name__, exc))
			continue
		for event in bars:
			event.instrument, event.granularity = instrument, granularity
			db.write(event, name, RECORDED, session='recorder')
		report("%s %s %s: %d bars recorded" % (name, instrument, granularity, len(bars)))


def compact(service, report):
	"""
	candles.db into the archive, a store a provider: the bars after the last
	one each already holds. Two accounts of one provider are one feed here,
	the bar seen last winning.
	"""
	from parity_deriva.web.service import importer
	db = service.candles
	with db.connect() as connection:
		groups = connection.execute(
			"SELECT DISTINCT provider, instrument, granularity FROM candles").fetchall()
	for provider, instrument, granularity in groups:
		path = os.path.join(market.directory(service.setup), market.ARCHIVE, provider,
							'%s.hd5' % instrument)
		last = None
		if os.path.exists(path):
			held = store.indexes(path).get(granularity)
			last = held.max() if held is not None and len(held) else None
		rows = [r for r in db.select(instrument, granularity, dtfrom=last)
				if r['provider'] == provider and r['mid_c'] is not None]
		frame = framed(rows)
		if last is not None:
			frame = frame[frame.index > last]
		if not len(frame.index):
			continue
		os.makedirs(os.path.dirname(path), exist_ok=True)
		added, _, _ = importer().merge(path, '/' + granularity, frame)
		report("archive %s %s %s: %d bars" % (provider, instrument, granularity, added))


def framed(rows):
	"""candles.db rows as a store's frame: a side it lacks is its mid, as the store has it."""
	columns = ['%s_%s' % (side, leg) for side in SIDES for leg in LEGS]
	if not rows:
		return pd.DataFrame(columns=columns + ['volume'], index=pd.DatetimeIndex([]))
	frame = pd.DataFrame(rows).sort_values('received_at')
	frame = frame.drop_duplicates('time', keep='last')
	for side in ('ask', 'bid'):
		for leg in LEGS:
			frame['%s_%s' % (side, leg)] = frame['%s_%s' % (side, leg)].fillna(frame['mid_' + leg])
	frame.index = pd.DatetimeIndex(pd.to_datetime(frame['time'])).as_unit('us')
	frame.index.name = None
	frame['volume'] = frame['volume'].fillna(0).astype('int64')
	return frame[columns + ['volume']].astype(dict((c, 'float64') for c in columns)).sort_index()


def source(setup, name):
	"""The setup a source's candles are read with: '' the stores, else an archive."""
	return market.Sourced(setup, name) if name else setup


def span(setup, name, instrument, granularity):
	"""(first, last) bar a source holds of a series, read or built, or None."""
	path = market.store(instrument, source(setup, name))
	held = store.indexes(path).get(granularity) if os.path.exists(path) else None
	return (held.min(), held.max()) if held is not None and len(held) else None


def compare(setup, instrument, granularity, a, b, dtfrom=None, dtto=None):
	"""
	Source b against source a ('' the stores, or an archive's provider), bar
	by bar on the open time, in pips of the instrument: what a run on one
	would see that a run on the other would not.
	"""
	period = granularityToTimedelta(granularity)
	pip = pipSize(instrument, setup)
	spans = []
	for name in (a, b):
		held = span(setup, name, instrument, granularity)
		if held is None:
			raise market.MarketError("%s has no %s %s" % (name or 'the market', instrument, granularity))
		spans.append(held)
	# no window asked for: the one both hold, an archive being weeks and a
	# store years
	dtfrom = dtfrom or max(s[0] for s in spans)
	dtto = dtto or min(s[1] for s in spans)
	if dtto < dtfrom:
		raise market.MarketError("the two hold no bars in common: %s .. %s against %s .. %s" % (
			spans[0][0].date(), spans[0][1].date(), spans[1][0].date(), spans[1][1].date()))
	# read a few bars past both ends, the most a clock is looked for off,
	# so a shift does not leave the window's edge bars unpaired
	margin = 3 * period
	frames = []
	for name in (a, b):
		frame = store.load(market.store(instrument, source(setup, name)), granularity,
						   pd.Timestamp(dtfrom) - margin, pd.Timestamp(dtto) + margin)
		if len(frame.index) > COMPARE_BARS:
			raise market.MarketError("%d bars: narrow the window, a comparison reads %d at most"
									 % (len(frame.index), COMPARE_BARS))
		frames.append(frame[~frame.index.duplicated(keep='last')])
	one, two = frames
	# the shift of b's clock, in bars, that brings its moves nearest a's: 0
	# when they agree on when a bar opens, else the offset to look into (a
	# bar-open convention, a DST, a server clock) - and the bars are set
	# against each other after it, or every number would be the offset's.
	# Bar to bar moves and not prices: a level one source is off by, a day
	# or all along, must not pass for a clock
	moves = one['mid_c'].diff().rename('a')
	shifts = {}
	for k in range(-3, 4):
		moved = two['mid_c'].diff().set_axis(two.index + k * period).rename('b')
		paired = pd.concat([moves, moved], axis=1, join='inner').dropna()
		if len(paired.index):
			shifts[k] = round(float(((paired['b'] - paired['a']) / pip).abs().median()), 3)
	shift = min(shifts, key=lambda k: (shifts[k], abs(k))) if shifts else 0
	if shift:
		two = two.set_axis(two.index + shift * period)
	inside = lambda frame: frame[(frame.index >= pd.Timestamp(dtfrom)) & (frame.index <= pd.Timestamp(dtto))]
	one, two = inside(one), inside(two)
	both = one.join(two, how='inner', lsuffix='_a', rsuffix='_b')

	def stats(series):
		series = series.dropna().abs()
		return None if series.empty else {
			'median': round(float(series.median()), 3), 'p95': round(float(series.quantile(0.95)), 3),
			'max': round(float(series.max()), 3)}

	def spread(frame):
		values = ((frame['ask_c'] - frame['bid_c']) / pip).dropna()
		return None if values.empty else round(float(values.median()), 3)

	dClose = (both['mid_c_b'] - both['mid_c_a']) / pip
	days = []
	for day, part in dClose.abs().groupby(dClose.index.floor('D')):
		days.append({'day': int(day.value // 10 ** 6), 'bars': int(len(part)),
					 'median': round(float(part.median()), 3),
					 'p95': round(float(part.quantile(0.95)), 3), 'max': round(float(part.max()), 3)})
	typical = float(pd.Series([d['p95'] for d in days]).median()) if days else 0.0
	return {'instrument': instrument, 'granularity': granularity, 'pip': pip, 'a': a, 'b': b,
			'bars': {'a': int(len(one.index)), 'b': int(len(two.index)), 'both': int(len(both.index)),
					 'onlyA': int(len(one.index.difference(two.index))),
					 'onlyB': int(len(two.index.difference(one.index)))},
			'from': iso(both.index.min()) if len(both.index) else None,
			'to': iso(both.index.max()) if len(both.index) else None,
			'dClose': stats(dClose),
			'dHigh': stats((both['mid_h_b'] - both['mid_h_a']) / pip),
			'dLow': stats((both['mid_l_b'] - both['mid_l_a']) / pip),
			'spread': {'a': spread(one), 'b': spread(two)},
			'shift': shift if shifts else None, 'shifts': shifts, 'days': days,
			'standout': [d for d in days if typical and d['p95'] > STANDOUT * typical]}
