"""LightGBM: dalle 10 candele prima e dai dati a corredo, quanto sale e quanto scende il prezzo dopo.

Ingressi a t0 (tutto in ATR14 di t0, float32):
  le ultime 10 candele: corpo, ombra sopra, ombra sotto, close rispetto al close di t0;
  corredo: ATR in pip, ATR contro la volatilità del giorno, distanza da EMA21/SMA100/SMA288,
  posizione nel range delle ultime 288 candele, ora e giorno della settimana,
  ultima H1 e ultima H4 già chiuse (corpo, ampiezza, distanza del close di t0 dal loro close).
Obiettivi, per h = 16, 48, 288 candele M5:
  size = salita + discesa massime (quanto si muove), asym = salita - discesa (da che parte di più),
  fut = close(t0+h) - close(t0) (dove chiude).
Si impara sul 2015-2020 (ultimo 10% per l'early stopping), si misura sul 2020-2026. Tra i due
periodi si buttano 288 candele, così gli obiettivi del training non guardano dentro il test.
Soldi: si compra se la previsione dice su, si vende se dice giù, si esce dopo h candele;
tutte le operazioni, o solo il 20% e il 5% con la previsione più forte (soglie prese dall'early stopping).

    python -m parity_deriva.scripts.excursion_lgbm EUR_USD GBP_USD
    python -m parity_deriva.scripts.excursion_lgbm EUR_USD --compare predictions.csv   # contro Kronos
Il CSV di Kronos viene da candle_forecast.kronos_scratch: stessi punti di test, stesse misure, soglie
del 20% e del 5% prese sui punti in comune (per tutti e due i modelli allo stesso modo).
Serve lightgbm: gira con l'env candle_forecast, non con il venv di produzione.
"""
import gc
import math
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd

from parity_deriva.scripts.nm_stats import AGG, atr14, hh_t, load_store

N, HORIZONS, EMBARGO = 10, (16, 48, 288), 288
PARAMS = {"objective": "regression", "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 1000,
          "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 10.0,
          "num_threads": 2, "verbose": -1, "seed": 42}


def higher(d, times_close, period, c, a):
    """Ultima candela superiore già chiusa: corpo e ampiezza nel suo ATR, distanza dal suo close nell'ATR di t0."""
    ah = atr14(*(d[k].to_numpy() for k in "hlc"))
    hc = pd.DataFrame({"t": d.index + pd.Timedelta(period), "body": (d.c - d.o).to_numpy() / ah,
                       "range": (d.h - d.l).to_numpy() / ah, "close": d.c.to_numpy()})
    m = pd.merge_asof(pd.DataFrame({"t": times_close}), hc, on="t", direction="backward")
    return [m.body.to_numpy(), m.range.to_numpy(), (c - m.close.to_numpy()) / a]


def build(m5, h1, h4, pip):
    o, h, l, c = (m5[k].to_numpy() for k in "ohlc")
    a = atr14(h, l, c)
    top, bot = np.maximum(o, c), np.minimum(o, c)
    C, H, L = pd.Series(c), pd.Series(h), pd.Series(l)
    cols, names = [], []
    for j in range(N):
        s = lambda x: np.r_[np.full(j, np.nan), x[:len(x) - j]]   # valore della candela t0-j
        cols += [s(c - o) / a, s(h - top) / a, s(bot - l) / a]
        names += [f"body_{j}", f"upw_{j}", f"low_{j}"]
        if j:
            cols.append((s(c) - c) / a)
            names.append(f"close_{j}")
    tr = np.r_[np.nan, np.maximum(h[1:], c[:-1]) - np.minimum(l[1:], c[:-1])]
    cols += [a / pip, a / pd.Series(tr).rolling(288).mean().to_numpy(),
             (c - C.ewm(span=21, adjust=False).mean().to_numpy()) / a,
             (c - C.rolling(100).mean().to_numpy()) / a, (c - C.rolling(288).mean().to_numpy()) / a,
             (H.rolling(288).max().to_numpy() - c) / a, (c - L.rolling(288).min().to_numpy()) / a,
             (m5.index.hour * 60 + m5.index.minute).to_numpy() / 60, m5.index.dayofweek.to_numpy().astype(float)]
    names += ["atr_pip", "atr_vs_day", "d_ema21", "d_sma100", "d_sma288", "to_hi288", "to_lo288", "hour", "dow"]
    close_t = m5.index + pd.Timedelta("5min")
    for tag, d, per in (("h1", h1, "1h"), ("h4", h4, "4h")):
        cols += higher(d, close_t, per, c, a)
        names += [f"{tag}_body", f"{tag}_range", f"{tag}_dist"]
    X = np.column_stack(cols).astype(np.float32)
    Y = {}
    for hz in HORIZONS:
        up = (H.rolling(hz).max().shift(-hz).to_numpy() - c) / a
        dn = (c - L.rolling(hz).min().shift(-hz).to_numpy()) / a
        Y[hz] = {"size": (up + dn).astype(np.float32), "asym": (up - dn).astype(np.float32),
                 "fut": ((C.shift(-hz).to_numpy() - c) / a).astype(np.float32)}
    return X, names, Y, (a / pip).astype(np.float32)


def trade(side, fut, pip_a, mask, hz):
    """pip a operazione (prima dello spread), quante volte indovina, t Hansen-Hodrick."""
    pnl = side * fut * pip_a
    hit = mask & (fut != 0)
    return {"n": int(mask.sum()), "acc": (np.sign(fut[hit]) == side[hit]).mean(), "pip": pnl[mask].mean(),
            "t": hh_t(np.where(mask, pnl, 0), mask, hz - 1)}


def compare(name, path, times, test, Y, P, close, pip_a, pip):
    """Kronos e LightGBM sugli stessi punti di test; Kronos dà prezzi, qui diventano ATR come per LightGBM."""
    k = pd.read_csv(path)
    ts = pd.to_datetime(k.ts)
    ts = ts.dt.tz_convert(None) if ts.dt.tz is not None else ts
    pos = times.get_indexer(pd.DatetimeIndex(ts))
    j = np.searchsorted(test, pos)
    ok = (pos >= 0) & (j < len(test)) & (test[np.minimum(j, len(test) - 1)] == pos)
    k, pos, j = k[ok], pos[ok], j[ok]
    c0, a0 = close[pos], pip_a[pos] * pip
    print(f"-- confronto {name}: {len(pos)} punti di test in comune; close_t0 del PC contro il server: "
          f"differenza massima {np.abs(k.close_t0.to_numpy() - c0).max() / pip:.2f} pip", flush=True)
    every = int(np.median(np.diff(pos))) if len(pos) > 1 else 1
    rows = []
    for hz in HORIZONS:
        up, dn = (k[f"hi_{hz}"].to_numpy() - c0) / a0, (c0 - k[f"lo_{hz}"].to_numpy()) / a0
        kr = {"size": up + dn, "asym": up - dn, "fut": (k[f"cl_{hz}"].to_numpy() - c0) / a0}
        ft, pa = Y[hz]["fut"][test][j], pip_a[test][j]
        lag = math.ceil(hz / every)       # trade() usa lag - 1 ritardi: i punti distano `every` candele
        for tgt in ("size", "asym", "fut"):
            y = Y[hz][tgt][test][j]
            for model, p in (("LightGBM", P[(hz, tgt)][j]), ("Kronos", kr[tgt])):
                r = {"pair": name, "h": hz, "obiettivo": tgt, "modello": model, "IC": np.corrcoef(p, y)[0, 1]}
                if tgt != "size":
                    for top in (1.0, 0.2, 0.05):
                        tr_ = trade(np.sign(p), ft, pa, np.abs(p) >= np.quantile(np.abs(p), 1 - top), lag)
                        r[f"pip_{int(top * 100)}%"], r[f"t_{int(top * 100)}%"] = tr_["pip"], tr_["t"]
                        r[f"n_{int(top * 100)}%"] = tr_["n"]
                rows.append(r)
    return rows


def run(name, kronos=None):
    _, raw, tfs = load_store(name)
    pip = 0.01 if "JPY" in name else 1e-4
    spread = float(((raw.ask_c - raw.bid_c) / pip).median())
    X, names, Y, pip_a = build(tfs["M5"], tfs["H1"], tfs["H4"], pip)
    times, close = tfs["M5"].index, tfs["M5"].c.to_numpy()
    del raw, tfs
    gc.collect()
    ok = ~np.isnan(X).any(axis=1)
    for hz in HORIZONS:
        ok &= ~np.isnan(Y[hz]["fut"])
    rows_ok = np.flatnonzero(ok)
    mid = rows_ok[len(rows_ok) // 2]
    train = rows_ok[rows_ok < mid - EMBARGO]
    test = rows_ok[rows_ok >= mid]
    cut = int(len(train) * 0.9)
    # ponytail: niente embargo tra fit ed early stopping, serve solo a fermare gli alberi
    fit, es = train[:cut], train[cut:]
    print(f"######## {name}: fit {len(fit)}  es {len(es)}  test {len(test)} (dal {times[mid]:%Y-%m-%d})  "
          f"spread mediano {spread:.2f} pip", flush=True)
    res, imp, P = [], [], {}
    Xf, Xe, Xt = X[fit], X[es], X[test]
    past = -Xt[:, names.index(f"close_{N - 1}")]                # movimento delle ultime 10 candele
    past_es = -Xe[:, names.index(f"close_{N - 1}")]
    del X
    gc.collect()
    for hz in HORIZONS:
        ft, pa = Y[hz]["fut"][test], pip_a[test]
        for tgt in ("size", "asym", "fut"):
            y = Y[hz][tgt]
            m = lgb.train(PARAMS, lgb.Dataset(Xf, y[fit], feature_name=names), num_boost_round=2000,
                          valid_sets=[lgb.Dataset(Xe, y[es], feature_name=names)],
                          callbacks=[lgb.early_stopping(100, verbose=False)])
            p, pe = m.predict(Xt), m.predict(Xe)
            P[(hz, tgt)] = p
            r = {"pair": name, "h": hz, "obiettivo": tgt, "alberi": m.best_iteration,
                 "IC": np.corrcoef(p, y[test])[0, 1]}
            if tgt == "size":
                base = np.abs(y[test] - y[fit].mean()).mean()
                r["MAE vs costante"] = np.abs(y[test] - p).mean() / base - 1
            else:
                for top in (1.0, 0.2, 0.05):
                    thr = np.quantile(np.abs(pe), 1 - top)
                    tr_ = trade(np.sign(p), ft, pa, np.abs(p) >= thr, hz)
                    r[f"pip_{int(top * 100)}%"], r[f"t_{int(top * 100)}%"] = tr_["pip"], tr_["t"]
                    r[f"acc_{int(top * 100)}%"] = tr_["acc"]
            res.append(r)
            g = pd.Series(m.feature_importance("gain"), index=names)
            imp.append({"pair": name, "h": hz, "obiettivo": tgt,
                        "più usati": ", ".join(g.sort_values(ascending=False).index[:5])})
            print(f"  h={hz} {tgt}: {m.best_iteration} alberi", flush=True)
        # regola semplice per confronto: ritorno indietro sulle ultime 10 candele
        r = {"pair": name, "h": hz, "obiettivo": "regola: contro le ultime 10"}
        for top in (1.0, 0.2, 0.05):
            thr = np.quantile(np.abs(past_es), 1 - top)
            tr_ = trade(-np.sign(past), ft, pa, np.abs(past) >= thr, hz)
            r[f"pip_{int(top * 100)}%"], r[f"t_{int(top * 100)}%"], r[f"acc_{int(top * 100)}%"] = tr_["pip"], tr_["t"], tr_["acc"]
        res.append(r)
    cmp = compare(name, kronos, times, test, Y, P, close, pip_a, pip) if kronos else []
    return res, imp, spread, cmp


def selfcheck():
    """Niente futuro negli ingressi: tagliando la serie dopo la riga k, gli ingressi fino a k non cambiano."""
    rng = np.random.default_rng(0)
    idx = pd.date_range("2020-01-06", periods=6000, freq="5min")
    c = 1.1 + np.cumsum(rng.normal(0, 1e-4, len(idx)))
    o = np.r_[c[0], c[:-1]]
    m5 = pd.DataFrame({"o": o, "h": np.maximum(o, c) + 5e-5, "l": np.minimum(o, c) - 5e-5, "c": c}, index=idx)
    k = 5000
    full, cut = (build(d, d.resample("1h").agg(AGG).dropna(), d.resample("4h").agg(AGG).dropna(), 1e-4)[0]
                 for d in (m5, m5.iloc[:k]))
    assert np.allclose(full[:k], cut, equal_nan=True), "un ingresso guarda nel futuro"
    print("selfcheck ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selfcheck"]:
        selfcheck()
        sys.exit()
    pd.set_option("display.width", 250)
    args = sys.argv[1:]
    kronos = args[args.index("--compare") + 1] if "--compare" in args else None
    allres, allimp, allcmp, spreads = [], [], [], {}
    for name in [a for a in args if a not in ("--compare", kronos)] or ["EUR_USD"]:
        res, imp, spreads[name], cmp = run(name, kronos)
        allres += res
        allimp += imp
        allcmp += cmp
        gc.collect()
    df = pd.DataFrame(allres)
    print("\n===== TEST 2020-2026 (mai visto). IC = correlazione previsione/realtà.")
    print("size: MAE vs costante < 0 = il modello prevede quanto si muove meglio della sola ATR.")
    print("asym, fut: pip a operazione PRIMA dello spread, uscita dopo h candele; 100% = sempre, 20%/5% = previsioni più forti.")
    print("spread mediano:", {k: round(v, 2) for k, v in spreads.items()})
    print(df.round(3).to_string(index=False))
    print("\n===== ingressi più usati (gain)")
    print(pd.DataFrame(allimp).to_string(index=False))
    if allcmp:
        print("\n===== LightGBM contro Kronos sugli stessi punti di test (pip PRIMA dello spread)")
        print(pd.DataFrame(allcmp).round(3).to_string(index=False))
