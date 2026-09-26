"""
The band a strategy's capital should stay in (docs/PIANO-FASE1.md C5): the
yardstick demo and live are judged by against what the simulation allowed.

Each trade of the simulation becomes a return, its P&L over the balance before
it, so that a run sized on risk (a trade a percentage of the account) reads
the same at any size. Those returns are drawn again with replacement, `n`
times, as long a sequence as asked; at every trade i the 5th, 50th and 95th
percentile of the compounded return so far are the band. In the same draw, the
worst run of losses of each sequence: how long a losing streak the strategy
may have without anything being wrong.

Pure functions over plain lists, numpy for the arithmetic: the gate, the card,
promote() and the live protections all call the same one.
"""

import numpy as np

#: how many sequences are drawn, by default
DRAWS = 1000


def returns(trades, start=None):
	"""
	Each closed trade's P&L over the balance before it, in order.

	A trade carries the balance after it (backtest/ledger.py); the one before
	is that less its P&L. A trade with no balance - a viewer plugin's, a run
	with no account - uses `start` compounded by the returns so far.
	"""
	out, balance = [], start
	for trade in trades:
		pl = trade.get('pl')
		if pl is None:
			continue
		after = trade.get('balance')
		before = after - pl if after is not None else balance
		if not before or before <= 0:
			continue
		out.append(pl / before)
		balance = before + pl
	return out


def streak(values):
	"""The longest run of losses in a sequence of returns."""
	best = run = 0
	for value in values:
		run = run + 1 if value < 0 else 0
		best = max(best, run)
	return best


def band(values, n=DRAWS, length=None, seed=0):
	"""
	{p5, p50, p95, streak}: the compounded return after each of `length`
	trades (the sample's own count when absent), and the worst losing streak's
	50th and 95th percentile, over `n` sequences drawn from `values`.
	None with no returns to draw from.
	"""
	values = np.asarray(values, dtype=float)
	if not len(values):
		return None
	length = int(length or len(values))
	rng = np.random.default_rng(seed)
	drawn = values[rng.integers(0, len(values), size=(n, length))]
	curves = np.cumprod(1.0 + drawn, axis=1) - 1.0
	low, mid, high = np.percentile(curves, [5, 50, 95], axis=0)
	# the longest run of losses of each sequence, a column at a time
	run = np.zeros(n, dtype=int)
	worst = np.zeros(n, dtype=int)
	for column in (drawn < 0).T:
		run = np.where(column, run + 1, 0)
		worst = np.maximum(worst, run)
	return {'p5': [round(float(v), 6) for v in low], 'p50': [round(float(v), 6) for v in mid],
			'p95': [round(float(v), 6) for v in high], 'n': int(n), 'trades': len(values),
			'streak': {'p50': float(np.percentile(worst, 50)), 'p95': float(np.percentile(worst, 95))}}


def below(curve, drawn):
	"""
	The first trade (1-based) at which a curve of compounded returns falls
	under the band's 5th percentile, or None: what C1b and the live
	protections ask of a demo or a live record.
	"""
	for i, value in enumerate(curve):
		if i < len(drawn['p5']) and value < drawn['p5'][i]:
			return i + 1
	return None


def compounded(values):
	"""The compounded return after each of a sequence of returns."""
	out, total = [], 1.0
	for value in values:
		total *= 1.0 + value
		out.append(total - 1.0)
	return out
