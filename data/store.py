"""
Read a candle series out of an HDF5 store, stored or derived.

A store only has to hold its finest series. Asked for a granularity it does
not hold, this builds it from the finest one it does, the same way
scripts/resample_store.py would have written it: a day of M5 candles is the
daily candle, so a strategy that reads days runs off an M5-only store, and the
simulator still has the M5 bars to fill its orders against - the way a live
account sees prices move inside the day its strategy reads.

Derived bars are aligned on UTC midnight, which is the alignment the D
history that came before the M5 export was stamped with. W is not derived:
a 7-day bin would start on the epoch's Thursday.
"""

import pandas as pd

from parity_deriva.lib.utils import granularityToTimedelta

AGGREGATE = {'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}

#: what a store can be asked for beyond what it holds
DERIVABLE = ('M5', 'M15', 'M30', 'H1', 'H4', 'D')


def aggregation_map(columns):
	"""ask_h takes the max, bid_o the first, volume the sum."""
	how = {}
	for column in columns:
		if column == 'volume':
			how[column] = 'sum'
			continue
		side, _, leg = column.partition('_')
		if side in ('ask', 'bid', 'mid') and leg in AGGREGATE:
			how[column] = AGGREGATE[leg]
		else:
			raise ValueError("unexpected column %r in store" % column)
	return how


def resample(frame, target):
	"""Aggregate a flat candle frame up to the target granularity."""
	rule = granularityToTimedelta(target)
	if rule is None:
		raise ValueError("unknown granularity %r" % target)
	out = frame.resample(rule).agg(aggregation_map(frame.columns))
	# A period with no source bars is the market being closed, and there is no
	# candle to report for it. Such a row has NaN prices but volume 0, because
	# summing nothing gives 0 rather than NaN - so the emptiness has to be
	# judged on the prices, not on the whole row.
	prices = [c for c in out.columns if c != 'volume']
	if prices:
		out = out[~out[prices].isna().all(axis=1)]
	if 'volume' in out.columns:
		out['volume'] = out['volume'].fillna(0).astype('int64')
	return out


def _stored(store):
	"""granularity -> index, for every key that is a candle series."""
	out = {}
	for key in store.keys():
		name = key.lstrip('/')
		if granularityToTimedelta(name) is None:
			continue
		try:
			index = pd.DatetimeIndex(store.select_column(key, 'index'))
		except Exception:
			# not a candle frame, so not a series anything can be read from
			continue
		if len(index):
			out[name] = index
	return out


def _source(stored, granularity):
	"""The stored series a missing granularity is built from, or None."""
	span = granularityToTimedelta(granularity)
	if granularity not in DERIVABLE or span is None:
		return None
	finer = [g for g in stored if granularityToTimedelta(g) < span]
	return min(finer, key=granularityToTimedelta) if finer else None


def indexes(path):
	"""
	granularity -> DatetimeIndex, for what the store holds and what it can
	derive. A derived index is the fine one floored to the bar, which is what
	resample() keeps without reading a single price.
	"""
	store = pd.HDFStore(path, mode='r')
	try:
		stored = _stored(store)
	finally:
		store.close()
	out = dict(stored)
	for name in DERIVABLE:
		source = None if name in out else _source(stored, name)
		if source is not None:
			out[name] = stored[source].floor(granularityToTimedelta(name)).unique()
	return out


def derived_from(path, granularity):
	"""The stored granularity this one is built from, or None if stored."""
	store = pd.HDFStore(path, mode='r')
	try:
		stored = [k.lstrip('/') for k in store.keys()
				  if granularityToTimedelta(k) is not None]
	finally:
		store.close()
	return None if granularity in stored else _source(stored, granularity)


def _window(store, key, dtfrom, dtto):
	"""
	The rows of one key between two timestamps, read as rows and not as a
	series to be sliced afterwards.

	A chart zooming into an hour of a ten year M5 store asks for a few dozen
	bars; reading all 876 000 of them to throw 875 900 away is most of a
	second, every time the wheel turns. A table format answers a `where`
	directly; a fixed one cannot, and there the slice is the fallback rather
	than the plan.
	"""
	if dtfrom is None and dtto is None:
		return store[key]
	terms = []
	if dtfrom is not None:
		terms.append("index>=Timestamp('%s')" % pd.Timestamp(dtfrom))
	if dtto is not None:
		terms.append("index<=Timestamp('%s')" % pd.Timestamp(dtto))
	try:
		return store.select(key, where=' & '.join(terms))
	except (TypeError, NotImplementedError, ValueError):
		frame = store[key]
		if dtfrom is not None:
			frame = frame[frame.index >= pd.Timestamp(dtfrom)]
		if dtto is not None:
			frame = frame[frame.index <= pd.Timestamp(dtto)]
		return frame


def load(path, granularity, dtfrom=None, dtto=None):
	"""
	The candle frame for a granularity, read or derived. KeyError if neither.

	The window is optional and it is a window on the bars, not on the file:
	a derived series reads its source from the start of the bar the window
	opens in, or that first bar would be built from part of itself and drawn
	as a candle that never traded.
	"""
	key = '/' + granularity.lstrip('/')
	# open read-only and close it: pd.HDFStore(path)[key] leaves the handle
	# open in append mode, which then refuses any read-only open of the same
	# file elsewhere in the process
	store = pd.HDFStore(path, mode='r')
	try:
		if key in store:
			return _window(store, key, dtfrom, dtto)
		source = _source(_stored(store), granularity.lstrip('/'))
		if source is None:
			raise KeyError("%s holds no %s candles and nothing finer to build "
						   "them from" % (path, granularity))
		# the source has to cover whole bars at both ends: from the start of
		# the bar the window opens in to the end of the one it closes in, or
		# the two edge candles are built from part of themselves and drawn as
		# candles that never traded. What is read wide is then cut back to
		# the window, so a windowed read returns exactly the rows a full one
		# would have returned sliced
		step = granularityToTimedelta(granularity.lstrip('/'))
		frame = _window(store, '/' + source,
						None if dtfrom is None else pd.Timestamp(dtfrom).floor(step),
						None if dtto is None else pd.Timestamp(dtto).floor(step) + step)
	finally:
		store.close()
	frame = resample(frame, granularity.lstrip('/'))
	if dtfrom is not None:
		frame = frame[frame.index >= pd.Timestamp(dtfrom)]
	if dtto is not None:
		frame = frame[frame.index <= pd.Timestamp(dtto)]
	return frame


if __name__ == '__main__':
	# self-check: M5 bars over two days become two D bars, and the index a
	# derived series advertises is the one load() returns
	import os
	import tempfile
	times = pd.date_range('2024-01-01 22:00', periods=12 * 6, freq='5min')
	cols = {'%s_%s' % (s, l): [1.0 + i / 1000.0 for i in range(len(times))]
			for s in ('ask', 'bid', 'mid') for l in 'ohlc'}
	cols['volume'] = [1] * len(times)
	path = os.path.join(tempfile.mkdtemp(), 'X.hd5')
	pd.DataFrame(cols, index=times).to_hdf(path, key='/M5', format='table')
	d = load(path, 'D')
	assert list(d.index) == [pd.Timestamp('2024-01-01'), pd.Timestamp('2024-01-02')]
	assert d['volume'].tolist() == [24, 48]
	assert d['ask_o'].iloc[1] == cols['ask_o'][24] and d['ask_c'].iloc[1] == cols['ask_c'][-1]
	assert list(indexes(path)['D']) == list(d.index)
	assert list(indexes(path)['H4']) == list(load(path, 'H4').index)
	assert derived_from(path, 'D') == 'M5' and derived_from(path, 'M5') is None
	assert 'M1' not in indexes(path) and 'W' not in indexes(path)
	print("ok")
