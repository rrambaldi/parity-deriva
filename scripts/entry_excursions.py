"""Dove va il prezzo dopo l'ingresso di una strategia: MFE, MAE e chi arriva prima, dopo N barre.

    python -m parity_deriva.scripts.entry_excursions 20260925-004457-0602fb 1       # set salvato, run 1
    python -m parity_deriva.scripts.entry_excursions path/run.json.gz --bars 4,8,16
    python -m parity_deriva.scripts.entry_excursions --selfcheck

La stessa analisi è sulla pagina simulate, pulsante entries di un run (api/sweeps/<id>/<n>/excursions).

Legge un run salvato (i trade e le candele del grafico, saveSweepRun in web/service.py) e le M5
dello store sotto. Da ogni fill (entryTime, entryPrice), nel verso del trade e sul lato che lo
chiude (bid per un long, ask per uno short, come backtest/resolution.py):
  MFE = quanto è andato al massimo a favore, MAE = al massimo contro, close = dove sta alla fine;
in R (distanza dello stop iniziale), in pip e in ATR14 del grafico alla barra prima dell'ingresso.
La finestra va dal fill all'apertura della barra entryIndex+N del grafico, letta sulle M5: la
M5 del fill conta tutta, anche la parte prima. Lo spread c'è già: si entra al fill e si esce
sull'altro lato.
Chi arriva prima: per ogni target k*R contro lo stop a 1R, la prima M5 che tocca l'uno o l'altro.
Una M5 che li tocca tutti e due non dice l'ordine e conta come stop. Nessuno dei due entro N
barre: si chiude a N, al close. E[R] è il risultato medio per trade in R.
Base: per ogni trade BASE barre a caso del run, alla stessa ora UTC e nello stesso verso, entrate
all'apertura (ask per un long, bid per uno short), con uno stop di tanti ATR quanti ne aveva il
trade. edge = strategia - base.
A e B = trade della prima e della seconda metà per data di ingresso. IC 95% con bootstrap a
blocchi di BLOCK trade consecutivi, perché i trade vicini si somigliano.
"""
import argparse
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd

from parity_deriva.data import market, store
from parity_deriva.etc import settings
from parity_deriva.lib.utils import pipSize
# the 95% interval by blocks of trades, shared with the run page's entry analysis
from parity_deriva.performance.entry import block_ci
from parity_deriva.scripts.nm_stats import atr14

BARS = {'M5': (6, 12, 48, 144), 'M15': (4, 8, 16, 32, 96), 'M30': (2, 4, 8, 16, 48),
		'H1': (1, 4, 8, 24), 'H4': (1, 2, 3, 6, 12), 'D': (1, 2, 5, 10)}
TARGETS = (0.5, 1.0, 1.5, 2.0, 3.0)
BASE = 5
BLOCK = 10


def load(args):
	"""Il payload del run: un file, o set e numero del run."""
	if len(args) == 1:
		path = args[0]
	else:
		runs = getattr(settings, 'RUNS_DIR', None) or os.path.join(settings.DATA_DIR, 'runs')
		path = os.path.join(runs, 'sweeps', args[0], '%d.json.gz' % int(args[1]))
	with gzip.open(path, 'rb') as handle:
		return json.load(handle)


def m5frame(payload, path):
	"""Le M5 dello store in path sul periodo del run, con i tempi in ms come le candele del payload."""
	frame = store.load(path, 'M5',
					   pd.Timestamp(payload['from'], unit='ms'),
					   pd.Timestamp(payload['to'], unit='ms') + pd.Timedelta(days=30))
	out = {k: frame[k].to_numpy() for k in ('ask_o', 'bid_o', 'ask_h', 'ask_l', 'ask_c',
											'bid_h', 'bid_l', 'bid_c')}
	out['t'] = frame.index.values.astype('datetime64[ms]').astype('int64')
	return out


def path(m5, t0, t1, long, entry):
	"""A favore e contro per ogni M5 da t0 (la M5 che lo contiene) a t1 escluso, e il close."""
	a = max(np.searchsorted(m5['t'], t0, 'right') - 1, 0)
	b = np.searchsorted(m5['t'], t1, 'left')
	if b <= a:
		return None
	if long:
		return m5['bid_h'][a:b] - entry, entry - m5['bid_l'][a:b], m5['bid_c'][b - 1] - entry
	return entry - m5['ask_l'][a:b], m5['ask_h'][a:b] - entry, entry - m5['ask_c'][b - 1]


def outcomes(fav, adv, close, risk):
	"""In R, per ogni target di TARGETS con lo stop a 1R: +k, -1, o il close se nessuno dei due."""
	stop = np.flatnonzero(adv >= risk)
	stop = stop[0] if len(stop) else len(adv)
	# primo indice in cui il massimo a favore raggiunge k*R: il cummax è in ordine, basta searchsorted
	hit = np.searchsorted(np.maximum.accumulate(fav), np.array(TARGETS) * risk, 'left')
	return np.where(hit < stop, TARGETS, np.where(stop < len(adv), -1.0, close / risk))


def measure(trades, candles, m5, N, pip, base=BASE, seed=0):
	"""Una riga per trade (who='strategia') e per ingresso a caso (who='base')."""
	c = np.asarray(candles, float)
	times = c[:, 0].astype('int64')
	atr = atr14(c[:, 2], c[:, 3], c[:, 4])
	minute = (times // 60000) % 1440
	rng = np.random.default_rng(seed)
	rows = []

	def add(who, when, t0, t1, long, entry, risk, atr_now):
		p = path(m5, t0, t1, long, entry)
		if p is None or not risk > 0:
			return
		fav, adv, close = p
		rows.append(dict(who=who, when=when, R=risk, mfe=fav.max(), mae=adv.max(), close=close,
						 atr=atr_now, **dict(('k%g' % k, v) for k, v in zip(TARGETS, outcomes(fav, adv, close, risk)))))

	for t in trades:
		i = t.get('entryIndex')
		if i is None or t.get('stopLoss') is None or i < 15 or i + N >= len(c):
			continue
		long = t['direction'] == 'long'
		risk = abs(t['entryPrice'] - t['stopLoss'])
		add('strategia', t['entryTime'], t['entryTime'], times[i + N], long, t['entryPrice'], risk, atr[i - 1])
		same = np.flatnonzero((minute == minute[i]) & (np.arange(len(c)) >= 15) & (np.arange(len(c)) + N < len(c)))
		for j in rng.choice(same, min(base, len(same)), replace=False):
			a = np.searchsorted(m5['t'], times[j], 'left')
			if a >= len(m5['t']):
				continue
			entry = m5['ask_o'][a] if long else m5['bid_o'][a]
			add('base', times[j], times[j], times[j + N], long, entry, risk / atr[i - 1] * atr[j - 1], atr[j - 1])
	f = pd.DataFrame(rows)
	if not f.empty:
		f['pip'] = pip
	return f


def num(x):
	"""Un numero per il JSON: NaN (un gruppo vuoto) diventa None."""
	return None if x is None or not np.isfinite(x) else round(float(x), 4)


def summary(f):
	"""Le due tabelle di un N: stats per (chi, unità) e la griglia stop 1R / target k*R."""
	s, b = f[f.who == 'strategia'].sort_values('when'), f[f.who == 'base']
	half = s.when.median()
	stats = []
	for who, d in (('strategia', s), ('base', b)):
		for unit, div in (('R', d.R), ('pip', d.pip), ('ATR', d.atr)):
			mfe, mae = d.mfe / div, d.mae / div
			stats.append(dict(who=who, unit=unit, mfe=[num(mfe.quantile(q)) for q in (.1, .5, .9)],
							  mae=[num(mae.quantile(q)) for q in (.1, .5, .9)],
							  close=num((d.close / div).mean()), up=num((d.close > 0).mean())))
	grid = []
	for k in TARGETS:
		col = 'k%g' % k
		lo, hi = block_ci(s[col])
		grid.append(dict(k=k, target=num((s[col] == k).mean()), stop=num((s[col] == -1).mean()),
						 e=num(s[col].mean()), lo=num(lo), hi=num(hi), base=num(b[col].mean()),
						 edge=num(s[col].mean() - b[col].mean()),
						 a=num(s[col][s.when <= half].mean()), b=num(s[col][s.when > half].mean())))
	return dict(trades=len(s), random=len(b), half=int(half), stats=stats, grid=grid)


def analyse(payload, m5, bars=None):
	"""Tutto quello che lo script stampa e la pagina mostra, per ogni N di bars."""
	tf, inst = payload['granularity'], payload['instrument']
	pip = 0.01 if 'JPY' in inst else pipSize(inst, settings)
	out = dict(strategy=payload['strategy'], instrument=inst, granularity=tf,
			   trades=len(payload['trades']), coherence=coherence(payload['trades'], m5), bars=[])
	for N in bars or BARS.get(tf, (1, 4, 16)):
		f = measure(payload['trades'], payload['candles'], m5, N, pip)
		if f.empty or not (f.who == 'strategia').any():
			out['bars'].append(dict(N=N, trades=0))
		else:
			out['bars'].append(dict(summary(f), N=N))
	return out


def report(r, tf):
	N = r['N']
	if not r['trades']:
		print(f"===== N={N}: nessun trade con {N} barre dopo l'ingresso\n")
		return
	print(f"===== N={N} barre {tf}   trade {r['trades']}, base {r['random']}")
	rows = {}
	for x in r['stats']:
		rows[(x['who'], x['unit'])] = dict(zip(('MFE q10', 'MFE q50', 'MFE q90'), x['mfe']),
										   **dict(zip(('MAE q10', 'MAE q50', 'MAE q90'), x['mae'])),
										   **{'close media': x['close'], 'P(close>0)': x['up']})
	print(pd.DataFrame(rows).T.astype(float).round(2).to_string())
	grid = pd.DataFrame(r['grid']).set_index('k').astype(float)
	grid.index = ['%gR' % k for k in grid.index]
	grid.columns = ['P(target)', 'P(stop)', 'E[R]', 'IC95 lo', 'IC95 hi', 'E[R] base', 'edge', 'E[R] A', 'E[R] B']
	print(f"-- stop a 1R, target k*R, altrimenti chiuso dopo {N} barre (A|B al {pd.Timestamp(r['half'], unit='ms'):%Y-%m-%d})")
	print(grid.round(3).to_string(), '\n')


def coherence(trades, m5):
	"""Dal fill all'uscita vera: un trade chiuso a target deve aver toccato il target, a stop lo stop.
	{'target': [visti, toccati], 'stop': [...]}"""
	seen = {'TAKE_PROFIT_ORDER': [0, 0], 'STOP_LOSS_ORDER': [0, 0]}
	for t in trades:
		level = t['takeProfit'] if t['outcome'] == 'TAKE_PROFIT_ORDER' else t['stopFinal'] or t['stopLoss']
		if t['outcome'] not in seen or level is None or t['exitTime'] is None:
			continue
		long, entry = t['direction'] == 'long', t['entryPrice']
		p = path(m5, t['entryTime'], t['exitTime'] + 1, long, entry)
		if p is None:
			continue
		# lo stop di un long e il target di uno short si toccano scendendo fino al livello, gli
		# altri salendo; un gap che lo salta conta (il 24/12 lo spread di 17 pip lo fa al fill)
		fav, adv = p[0].max(), p[1].max()
		lo, hi = (entry - adv, entry + fav) if long else (entry - fav, entry + adv)
		down = long == (t['outcome'] == 'STOP_LOSS_ORDER')
		seen[t['outcome']][0] += 1
		seen[t['outcome']][1] += bool(lo <= level + 1e-9 if down else hi >= level - 1e-9)
	return {'target': seen['TAKE_PROFIT_ORDER'], 'stop': seen['STOP_LOSS_ORDER']}


def selfcheck():
	"""Un long a 1.1000 con lo stop a 1.0990 (R = 10 pip): sale a 1.1025, poi scende a 1.0985."""
	t0 = 1_700_000_000_000
	step = 300_000
	bid_h = np.array([1.1005, 1.1015, 1.1025, 1.1010, 1.0995])
	bid_l = np.array([1.0995, 1.1000, 1.1010, 1.0990, 1.0985])
	m5 = {'t': t0 + step * np.arange(5), 'bid_h': bid_h, 'bid_l': bid_l,
		  'bid_c': np.array([1.1000, 1.1012, 1.1020, 1.0995, 1.0990])}
	fav, adv, close = path(m5, t0, t0 + 5 * step, True, 1.1000)
	assert np.isclose(fav.max(), 0.0025) and np.isclose(adv.max(), 0.0015) and np.isclose(close, -0.0010)
	got = outcomes(fav, adv, close, 0.0010)
	# 0.5R e 1R alla seconda M5, 1.5R e 2R alla terza, lo stop alla quarta: 3R mai, quindi stop
	assert got.tolist() == [0.5, 1.0, 1.5, 2.0, -1.0], got
	# una M5 che tocca target e stop insieme conta come stop
	both = outcomes(np.array([0.0012]), np.array([0.0011]), 0.0, 0.0010)
	assert both[1] == -1.0, both
	# il fill a metà di una M5 prende quella M5 intera
	assert np.isclose(path(m5, t0 + 2 * step + 1000, t0 + 3 * step, True, 1.1000)[0].max(), 0.0025)
	print('selfcheck ok')


if __name__ == '__main__':
	ap = argparse.ArgumentParser()
	ap.add_argument('run', nargs='*', help='il file .json.gz, oppure id del set e numero del run')
	ap.add_argument('--bars', help='gli N, separati da virgole; di base dipendono dal timeframe')
	ap.add_argument('--selfcheck', action='store_true')
	a = ap.parse_args()
	if a.selfcheck:
		selfcheck()
		sys.exit()
	if len(a.run) not in (1, 2):
		ap.error('serve il file del run, o id del set e numero del run')
	pd.set_option('display.width', 220)
	payload = load(a.run)
	m5 = m5frame(payload, market.store(payload['instrument']))
	r = analyse(payload, m5, [int(x) for x in a.bars.split(',')] if a.bars else None)
	print(f"######## {r['strategy']} {r['instrument']} {r['granularity']}  {pd.Timestamp(payload['from'], unit='ms'):%Y-%m-%d}"
		  f" -> {pd.Timestamp(payload['to'], unit='ms'):%Y-%m-%d}  trade {r['trades']}")
	print('coerenza con il ledger (dal fill all\'uscita, M5): '
		  + ', '.join('%s %d/%d toccati' % (k, v[1], v[0]) for k, v in r['coherence'].items() if v[0]) + '\n')
	for x in r['bars']:
		report(x, r['granularity'])
