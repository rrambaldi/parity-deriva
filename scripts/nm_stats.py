"""N candele passate -> M future: c'è una relazione? Mid da bid/ask.

    python -m parity_deriva.scripts.nm_stats EUR_USD GBP_USD      # store: M5, H1 e H4 ricampionati
    python -m parity_deriva.scripts.nm_stats data/*_h4_*.csv      # CSV BID+ASK, come li scarica l'exporter
    python -m parity_deriva.scripts.nm_stats --selfcheck

Tutto in unità di ATR14 al tempo t, così anni calmi e agitati si confrontano.
Si usa ogni candela. Le finestre future si sovrappongono, quindi gli errori standard sono di
Hansen-Hodrick (covarianze fino al ritardo M-1, pesi uguali), giusti per finestre di M passi.
A e B = prima e seconda metà del periodo; la data del taglio è in tabella.
Le coppie non si mischiano: ognuna è un'analisi a sé, la sintesi finale le mette in fila.
I CSV si leggono direttamente, non passano dallo store: lì le strategie leggono le chiavi.
"""
import os
import re
import sys

import numpy as np
import pandas as pd

from parity_deriva.data import market
from parity_deriva.etc import settings

GRID = [(5, 20), (5, 40), (10, 40), (10, 80)]
AGG = {"o": "first", "h": "max", "l": "min", "c": "last"}


def mid(raw):
    return pd.DataFrame({k: (raw[f"bid_{k}"] + raw[f"ask_{k}"]) / 2 for k in "ohlc"})


def load_store(name):
    raw = pd.read_hdf(market.store(name), "/M5")
    m5 = mid(raw)
    # ponytail: H4 a blocchi fissi dalle 22:00 UTC, senza ora legale (d'estate la chiusura NY è alle 21)
    return name, raw, {"M5": m5,
                       "H1": m5.resample("1h").agg(AGG).dropna(),
                       "H4": m5.resample("4h", offset="2h").agg(AGG).dropna()}


def load_csv(path):
    """eurusd_h4_20030101_20260920-BID.csv e il suo -ASK accanto."""
    sides = {}
    for s in ("bid", "ask"):
        d = pd.read_csv(re.sub(r"-(BID|ASK)\.csv$", f"-{s.upper()}.csv", path, flags=re.I), index_col="timestamp")
        sides[s] = d[["open", "high", "low", "close"]].set_axis(list("ohlc"), axis=1)
    raw = pd.concat(sides, axis=1, join="inner")
    raw.columns = [f"{s}_{k}" for s, k in raw.columns]
    raw.index = pd.to_datetime(raw.index, unit="ms")
    if (raw.ask_c < raw.bid_c).mean() > 0.5:
        sys.exit(f"{path}: ASK sotto BID quasi sempre, i due file sono scambiati?")
    raw = raw[raw.bid_h > raw.bid_l]  # via le candele piatte: mercato chiuso, non prezzi
    base = os.path.basename(path)
    pair = base.split("_")[0].upper()
    tf = re.search(r"_([mhdw]\d+)_", base, re.I)
    return pair, raw, {tf.group(1).upper() if tf else "CSV": mid(raw)}


def atr14(h, l, c):
    tr = np.maximum(h[1:], c[:-1]) - np.minimum(l[1:], c[:-1])
    return np.r_[np.nan, pd.Series(tr).rolling(14).mean().to_numpy()]


def frame(d, N, M, pip):
    o, h, l, c = (d[k].to_numpy() for k in "ohlc")
    atr = atr14(h, l, c)
    H, L, C, Oc = (pd.Series(x, index=d.index) for x in (h, l, c, c > o))
    f = pd.DataFrame({
        "past": (C - C.shift(N)) / atr,
        "ups": Oc.rolling(N).sum(),                      # candele verdi tra le ultime N
        "fut": (C.shift(-M) - C) / atr,
        "up": (H.rolling(M).max().shift(-M) - C) / atr,  # massima salita nelle M future
        "dn": (C - L.rolling(M).min().shift(-M)) / atr,  # massima discesa nelle M future
        "atr_pip": atr / pip,
    }, index=d.index).dropna()                           # solo testa e coda: le righe restano in fila
    f["A"] = np.arange(len(f)) < len(f) // 2
    return f


def hh_t(x, mask, L):
    """t della media di x sulle righe in mask; errori Hansen-Hodrick fino al ritardo L.
    Le righe fuori da mask restano a zero, così i ritardi sono ritardi nel tempo."""
    x, mask = np.asarray(x, float), np.asarray(mask, bool)
    if mask.sum() < 2:
        return np.nan
    mu = x[mask].mean()
    u = np.where(mask, x - mu, 0.0)
    s = u @ u + 2 * sum(u[k:] @ u[:-k] for k in range(1, L + 1))
    return mu * mask.sum() / np.sqrt(s) if s > 0 else np.nan


def table(f, col, L):
    A, ud = f.A.to_numpy(), (f.up - f.dn).to_numpy()
    rows = {}
    for g in sorted(f[col].unique()):
        m = (f[col] == g).to_numpy()
        rows[g] = {"n": m.sum(), "fut": f.fut[m].mean(), "t_fut": hh_t(f.fut, m, L),
                   "P(fut>0)": (f.fut[m] > 0).mean(), "up": f.up[m].mean(), "dn": f.dn[m].mean(),
                   "up-dn": ud[m].mean(), "t_up-dn": hh_t(ud, m, L),
                   "fut_A": f.fut[m & A].mean(), "fut_B": f.fut[m & ~A].mean()}
    return pd.DataFrame(rows).T.round(3)


def analyse(name, raw, tfs):
    pip = 0.01 if "JPY" in name.upper() else 1e-4
    spread = ((raw.ask_c - raw.bid_c) / pip).median()
    print(f"######## {name}  {raw.index[0]:%Y-%m-%d} -> {raw.index[-1]:%Y-%m-%d}  spread mediano {spread:.2f} pip\n")
    rows = []
    for tf, d in tfs.items():
        for N, M in GRID:
            f = frame(d, N, M, pip)
            L, A = M - 1, f.A.to_numpy()
            f["q"] = pd.qcut(f.past, 5, labels=False) + 1
            r = {"pair": name, "tf": tf, "N": N, "M": M, "n": len(f), "taglio": f"{f.index[len(f) // 2]:%Y-%m}",
                 "spread": spread, "atr_pip": f.atr_pip.median()}
            z = ((f.past - f.past.mean()) / f.past.std() * (f.fut - f.fut.mean()) / f.fut.std()).to_numpy()
            r["corr"] = z.mean()
            for part, m in [("", np.ones_like(A)), ("_A", A), ("_B", ~A)]:
                r[f"t{part}"] = hh_t(z, m, L)
            for q in (1, 5):
                m = (f.q == q).to_numpy()
                r[f"q{q}"], r[f"t_q{q}"] = f.fut[m].mean(), hh_t(f.fut, m, L)
                r[f"q{q}_A"], r[f"q{q}_B"] = f.fut[m & A].mean(), f.fut[m & ~A].mean()
            rows.append(r)
            print(f"===== {name} {tf}  N={N}  M={M}  (n={len(f)}, ATR mediano {r['atr_pip']:.1f} pip, taglio A|B {r['taglio']})")
            print("-- per quintile del movimento delle ultime N (q1 = giù forte, q5 = su forte)")
            print(table(f, "q", L).to_string())
            print(f"-- per numero di candele verdi tra le ultime {N}")
            print(table(f, "ups", L).to_string(), "\n")
    return rows


def selfcheck():
    """Somme sovrapposte di M passi indipendenti: l'errore vero della media è M/sqrt(n)."""
    rng = np.random.default_rng(0)
    M, n = 20, 200_000
    x = np.convolve(rng.standard_normal(n + M - 1), np.ones(M), "valid")
    se = x.mean() / hh_t(x, np.ones(n, bool), M - 1)
    assert abs(se / (M / np.sqrt(n)) - 1) < 0.1, se
    print("selfcheck ok")


if __name__ == "__main__":
    args = sys.argv[1:] or ["EUR_USD"]
    if args == ["--selfcheck"]:
        selfcheck()
        sys.exit()
    pd.set_option("display.width", 220)
    print("Unità: ATR14 della candela t. fut = close(t+M)-close(t); up/dn = massima salita/discesa nelle M future.\n")
    csvs = sorted({re.sub(r"-ASK\.csv$", "-BID.csv", a, flags=re.I) for a in args if a.lower().endswith(".csv")})
    rows = [r for a in [a for a in args if not a.lower().endswith(".csv")] for r in analyse(*load_store(a))]
    rows += [r for p in csvs for r in analyse(*load_csv(p))]
    print("===== SINTESI (una riga per coppia e sistema; t = errori Hansen-Hodrick)")
    print("corr: movimento passato contro futuro, <0 torna indietro, >0 continua. "
          "q1/q5: fut medio dopo il 20% di discese/salite più forti.")
    print(pd.DataFrame(rows).round(3).to_string(index=False))
