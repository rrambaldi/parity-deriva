"""
The gate SIM -> DEMO (docs/PIANO-FASE1.md C3, PROCESSO.md section 4): the checks
on the development period, and - only if they all pass - the same form once on
the holdout. Pure functions over what a run left (its trades, its report, its
KPIs): web/service.py gate runs the backtests and keeps the verdict on the card.

A check is a row {check, ok, value, need}, the dialog's line.
"""

import numpy as np

from parity_deriva.etc import settings


def _cfg(setup, name):
	"""A threshold of the setup's, else of etc/settings.py: a setup without one (or a mock) has the default."""
	value = getattr(setup, name, None)
	return value if isinstance(value, (int, float)) and not isinstance(value, bool) else getattr(settings, name)


def results(trades):
	return [t['pl'] for t in trades if t.get('pl') is not None]


def profitFactor(values):
	won = sum(v for v in values if v > 0)
	lost = -sum(v for v in values if v < 0)
	return won / lost if lost else (None if not won else float('inf'))


def bootstrapLow(values, n=1000, seed=0):
	"""The 5th percentile of the profit factor over the trades drawn again: the PF the luck may take away."""
	values = np.asarray(values, dtype=float)
	if len(values) < 2:
		return None
	drawn = values[np.random.default_rng(seed).integers(0, len(values), size=(n, len(values)))]
	won = np.where(drawn > 0, drawn, 0).sum(axis=1)
	lost = -np.where(drawn < 0, drawn, 0).sum(axis=1)
	pfs = np.where(lost > 0, won / np.where(lost > 0, lost, 1), np.inf)
	return float(np.percentile(pfs, 5))


def withoutBest(values, k=3):
	"""The profit factor once the k best trades are taken out: an edge that is three lucky trades is none."""
	return profitFactor(sorted(values)[:-k] if len(values) > k else [])


def drawdown(payload):
	"""The run's worst drawdown: in % of the account for one sized on it, else in price x units."""
	pct = (payload.get('kpi') or {}).get('maxDrawdownPct')
	return pct if pct is not None else (payload.get('report') or {}).get('maxDrawdown')


def row(check, ok, value, need):
	return {'check': check, 'ok': bool(ok), 'value': value, 'need': need}


def number(value, places=2):
	return None if value is None else (round(value, places) if value != float('inf') else 'inf')


def development(payload, neighbours, baseline, setup=None):
	"""
	The checks on the development period: enough trades, a profit factor luck
	does not explain away, a plateau (the set's neighbouring runs - one step
	of one parameter away - profitable too), no edge that is its three best
	trades, above the random entries' 95th percentile.
	"""
	values = results(payload.get('trades') or [])
	low = bootstrapLow(values)
	best = withoutBest(values)
	floor = _cfg(setup, 'GATE_PF_LOW')
	losing = [n for n in neighbours if n['pf'] is None or n['pf'] <= floor]
	pct = baseline and baseline.get('percentile')
	return [
		row('trades', len(values) >= _cfg(setup, 'GATE_MIN_TRADES'), len(values),
			'>= %d' % _cfg(setup, 'GATE_MIN_TRADES')),
		row('profit factor, bootstrap 5th percentile', low is not None and low > floor, number(low), '> %g' % floor),
		row('plateau: the neighbouring runs of the set', neighbours and not losing,
			'%d of %d with PF > %g' % (len(neighbours) - len(losing), len(neighbours), floor) if neighbours
			else 'no neighbour in the set', 'all > %g' % floor),
		row('profit factor without the 3 best trades', best is not None and best > floor, number(best), '> %g' % floor),
		row('random entries beaten', pct is not None and pct >= _cfg(setup, 'GATE_BASELINE_PCT'),
			None if pct is None else '%.0fth percentile' % pct, '>= %gth' % _cfg(setup, 'GATE_BASELINE_PCT')),
	]


def holdout(dev, held, setup=None):
	"""The checks on the holdout: a profit, a profit factor and a drawdown near the development's."""
	net = (held.get('report') or {}).get('net')
	pf, devPf = profitFactor(results(held.get('trades') or [])), profitFactor(results(dev.get('trades') or []))
	dd, devDd = drawdown(held), drawdown(dev)
	ratio, ddRatio = _cfg(setup, 'GATE_HOLDOUT_PF_RATIO'), _cfg(setup, 'GATE_HOLDOUT_DD_RATIO')
	return [
		row('net on the holdout', net is not None and net > 0, number(net), '> 0'),
		row('profit factor on the holdout', pf is not None and devPf is not None and pf >= ratio * devPf,
			number(pf), '>= %g x %s' % (ratio, number(devPf))),
		row('drawdown on the holdout', dd is not None and devDd is not None and dd <= ddRatio * devDd,
			number(dd), '<= %g x %s' % (ddRatio, number(devDd))),
	]


def passed(rows):
	return bool(rows) and all(r['ok'] for r in rows)
