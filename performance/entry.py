"""
Which entries a strategy should not have taken (docs/PIANO-FASE1.md C4c): for
each feature of portfolio/features.py, the run's trades split in five bands
(quintiles) by its value on the bar the signal came on, and each band's count,
win rate, profit factor and expectancy in R with its 95% interval. A band of
at least MIN_TRADES trades whose whole interval sits above the run's own
expectancy is a candidate: the quintile's edge is a filter to try in a set
(portfolio/filters.py), never one to take on trust - with N features looked
at, about N x 5% of the bands come out good by chance, and the answer says so.

Pure functions over a run's payload (its trades, with signalIndex and r, and
its candles): the run page and the MCP tool get_entry_analysis call the same.
The trades are the run's, so the development period's: no run reads the
holdout (web/holdout.py).
"""

import datetime
import types

import numpy as np

from parity_deriva.portfolio.features import NAMES, Features

BANDS = 5
MIN_TRADES = 30
#: trades drawn together in the bootstrap: neighbouring trades resemble each other
BLOCK = 10


def block_ci(x, block=BLOCK, n=1000, seed=0):
	"""The 95% interval of the mean, drawing blocks of consecutive values; (nan, nan) for too few."""
	x = np.asarray(x, float)
	if len(x) < 2 * block:
		return np.nan, np.nan
	rng = np.random.default_rng(seed)
	k = int(np.ceil(len(x) / block))
	starts = rng.integers(0, len(x) - block + 1, size=(n, k))
	drawn = (starts[:, :, None] + np.arange(block)).reshape(n, -1)[:, :len(x)]
	return tuple(np.percentile(x[drawn].mean(axis=1), [2.5, 97.5]))


def number(x, places=4):
	return None if x is None or not np.isfinite(x) else round(float(x), places)


def featureRows(candles):
	"""The features after each of the payload's candles (time, mid o h l c, ...), in order."""
	features, out = Features(), []
	for row in candles:
		features.add(types.SimpleNamespace(
			time=datetime.datetime.fromtimestamp(row[0] / 1000.0, datetime.timezone.utc).replace(tzinfo=None),
			mid={'o': row[1], 'h': row[2], 'l': row[3], 'c': row[4]}))
		out.append(features.values())
	return out


def band(trades):
	"""A band's figures: count, win rate, profit factor, expectancy in R and its interval."""
	rs = [t['r'] for t in trades]
	won = sum(t['pl'] for t in trades if t['pl'] > 0)
	lost = -sum(t['pl'] for t in trades if t['pl'] < 0)
	low, high = block_ci(rs)
	return {'n': len(trades), 'winRate': number(sum(1 for t in trades if t['pl'] > 0) / len(trades)) if trades else None,
			'pf': number(won / lost) if lost else None, 'expectancyR': number(np.mean(rs)) if rs else None,
			'ci': [number(low), number(high)]}


def analysis(payload, bands=BANDS, minimum=MIN_TRADES):
	"""
	{overall, features: [{name, bands: [{lo, hi, ...}]}], candidates, tried,
	chance}: the run's entries by feature. A trade counts where it has a
	result, an R (an initial stop) and the feature's value on its signal bar.
	"""
	trades = [t for t in payload.get('trades') or []
			  if t.get('pl') is not None and t.get('r') is not None and t.get('signalIndex') is not None]
	rows = featureRows(payload.get('candles') or [])
	overall = band(trades) if trades else None
	out, candidates = [], []
	for name in NAMES:
		seen = [(rows[t['signalIndex']][name], t) for t in trades
				if t['signalIndex'] < len(rows) and rows[t['signalIndex']][name] is not None]
		if len(seen) < bands:
			out.append({'name': name, 'bands': [], 'trades': len(seen)})
			continue
		values = np.array([v for v, _ in seen], float)
		edges = np.unique(np.percentile(values, np.linspace(0, 100, bands + 1)))
		groups = []
		for i in range(len(edges) - 1):
			lo, hi = edges[i], edges[i + 1]
			inside = [t for v, t in seen if (lo <= v <= hi if i == len(edges) - 2 else lo <= v < hi)]
			if not inside:
				continue
			figures = dict(band(inside), lo=number(lo), hi=number(hi))
			groups.append(figures)
			if overall and figures['n'] >= minimum and figures['ci'][0] is not None \
					and figures['ci'][0] > overall['expectancyR']:
				# the filter that keeps this band: its edges, as conditions
				keep = '&'.join(c for c in (
					'%s>=%g' % (name, figures['lo']) if i > 0 else '',
					'%s<%g' % (name, figures['hi']) if i < len(edges) - 2 else '') if c)
				candidates.append(dict(figures, name=name, filter=keep))
		out.append({'name': name, 'bands': groups, 'trades': len(seen)})
	tried = sum(len(f['bands']) for f in out)
	return {'overall': overall, 'features': out, 'candidates': candidates, 'tried': tried,
			'chance': round(tried * 0.05, 1), 'minimum': minimum}
