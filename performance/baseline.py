"""
A strategy against chance (docs/PIANO-FASE1.md C5): as many trades, entered at
random with the strategy's own profile, on the same candles - and where the
strategy's profit factor falls among `reps` runs of them.

The profile is what the entries copy of the strategy's trades: the hours they
entered at (drawn from its own), the share of longs, the stop and target
distances (a pair drawn from one of its trades), and the longest a trade was
held. A random trade enters at a bar's open on the side it buys (a long on the
ask, a short on the bid) and exits at the first touch of its stop or target, by
backtest/resolution.py's rule: a level is touched when it lies within the bar's
range on the side that closes the position. Past the longest hold it exits at
the close.

The profit factors are per unit of price, the strategy's as much as the
random runs': sizing is left out, so what is compared is the entries alone.

`ponytail:` one trade at a time, the money manager's filters (session, news)
and the order types are left out, and a bar that touches both levels is a
stop. If the baseline needs to be closer, a RandomEntries strategy run through
backtest/ledger.run is the next step.
"""

import datetime

import numpy as np

from parity_deriva.backtest import resolution

#: the random runs a strategy is measured against, by default
REPS = 200
#: fewer trades than this say nothing about chance
MIN_TRADES = 10


def hour(ms):
	return datetime.datetime.fromtimestamp(ms / 1000.0, datetime.timezone.utc).hour


def perUnit(trades):
	"""Each closed trade's result per unit of price: the exit less the entry, in its direction."""
	out = []
	for t in trades:
		if t.get('pl') is None or t.get('entryPrice') is None or t.get('exitPrice') is None:
			continue
		out.append((t['exitPrice'] - t['entryPrice']) * (1 if (t.get('units') or 0) > 0 else -1))
	return out


def profitFactor(results):
	won = sum(r for r in results if r > 0)
	lost = -sum(r for r in results if r < 0)
	return won / lost if lost else None


def profile(trades):
	"""What random entries copy of the strategy: hours, share of longs, stop and target distances, longest hold."""
	done = [t for t in trades if t.get('pl') is not None and t.get('entryPrice') is not None
			and t.get('stopLoss') is not None and t.get('takeProfit') is not None and t.get('entryTime')]
	if len(done) < MIN_TRADES:
		return None
	held = [t['exitIndex'] - t['entryIndex'] for t in done
			if t.get('exitIndex') is not None and t.get('entryIndex') is not None]
	return {'hours': [hour(t['entryTime']) for t in done],
			'long': sum(1 for t in done if (t.get('units') or 0) > 0) / len(done),
			'distances': [(abs(t['entryPrice'] - t['stopLoss']), abs(t['takeProfit'] - t['entryPrice'])) for t in done],
			'hold': max(held or [1]) or 1, 'trades': len(done)}


def pools(times):
	"""The bars an entry may be made on, by the hour they open at; the last bar is none."""
	out = {}
	for i, when in enumerate(times[:-1]):
		out.setdefault(hour(when), []).append(i)
	return out


def randomRun(shape, bars, byHour, rng):
	"""The per-unit results of one run of random entries."""
	times, opens, closes, ask_h, ask_l, bid_h, bid_l = bars
	half = (ask_h - bid_h) / 2.0
	last = len(times) - 1
	anywhere = np.arange(last)
	out = []
	for _ in range(shape['trades']):
		pool = byHour.get(shape['hours'][rng.integers(len(shape['hours']))], anywhere)
		i = int(pool[rng.integers(len(pool))])
		side = 1 if rng.random() < shape['long'] else -1
		stop_d, target_d = shape['distances'][rng.integers(len(shape['distances']))]
		entry = opens[i] + side * half[i]
		stop, target = entry - side * stop_d, entry + side * target_d
		end = min(last, i + shape['hold'])
		# the side that closes it: a long sells on the bid, a short buys on the ask
		if resolution.exit_side(side) == 'bid':
			high, low = bid_h[i:end + 1], bid_l[i:end + 1]
		else:
			high, low = ask_h[i:end + 1], ask_l[i:end + 1]
		hit_stop = (low <= stop) & (stop <= high)
		hit_target = (low <= target) & (target <= high)
		first_stop = int(np.argmax(hit_stop)) if hit_stop.any() else None
		first_target = int(np.argmax(hit_target)) if hit_target.any() else None
		if first_stop is not None and (first_target is None or first_stop <= first_target):
			out.append(-stop_d)
		elif first_target is not None:
			out.append(target_d)
		else:
			out.append((closes[end] - side * half[end] - entry) * side)
	return out


def baseline(trades, candles, reps=REPS, seed=0):
	"""
	{pf, percentile, pfs, reps}: the strategy's profit factor per unit, and
	where it falls among `reps` runs of random entries with its profile on
	`candles` (the payload's rows: time, mid o h l c, ask h l, bid h l). None
	with too few trades or candles to say anything.
	"""
	shape = profile(trades)
	if shape is None or len(candles) < 3:
		return None
	rows = np.asarray([[c[0], c[1], c[4], c[5], c[6], c[7], c[8]] for c in candles], dtype=float)
	bars = tuple(rows[:, k] for k in range(7))
	rng = np.random.default_rng(seed)
	byHour = pools(bars[0])
	pfs = sorted(pf for pf in (profitFactor(randomRun(shape, bars, byHour, rng)) for _ in range(reps))
				 if pf is not None)
	mine = profitFactor(perUnit(trades))
	if mine is None or not pfs:
		return None
	return {'pf': float(mine), 'percentile': 100.0 * sum(1 for pf in pfs if pf < mine) / len(pfs),
			'pfs': [round(float(pf), 4) for pf in pfs], 'reps': len(pfs), 'trades': shape['trades']}
