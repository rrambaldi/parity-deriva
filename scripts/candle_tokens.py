"""Un token per candela, poi conteggi: dopo certi token il prezzo va da una parte?

Il token è la forma della candela in unità di ATR14:
  corpo (close-open) in 5 classi: DD D = U UU (quantili 20/40/60/80 della prima metà);
  ombra sopra lunga "^" o corta, ombra sotto lunga "_" o corta (mediana della prima metà).
5 x 2 x 2 = 20 token. Es. "UU^" = candela verde grande con ombra sopra lunga.
Dati a corredo: l'ultima candela già chiusa di due timeframe superiori, solo il corpo (5 classi),
per non disperdere i conteggi. Già chiusa: la candela superiore ancora aperta non si guarda.

Prima metà: per ogni contesto, spostamento medio delle h candele dopo.
Seconda metà: si scommette nel verso imparato e si conta quanto rende, prima dello spread.

    python -m parity_deriva.scripts.candle_tokens EUR_USD GBP_USD
"""
import sys

import numpy as np
import pandas as pd

from parity_deriva.scripts.nm_stats import AGG, atr14, hh_t, load_store

BODY = ["DD", "D", "=", "U", "UU"]
V = 20                  # token possibili
MIN_N = 100             # un contesto visto meno volte nella prima metà non si usa
HORIZONS = (1, 4)
PERIOD = {"M5": "5min", "M15": "15min", "H1": "1h", "H4": "4h", "D1": "24h"}
PLAN = [("M5", ("H1", "H4")), ("M15", ("H1", "H4")), ("H1", ("H4", "D1"))]


def label(tok):
    return BODY[tok // 4] + ("^" if tok & 2 else "") + ("_" if tok & 1 else "")


def body_class(o, c, a, A):
    body = (c - o) / a
    return np.searchsorted(np.quantile(body[A], [.2, .4, .6, .8]), body)


def tokens(d):
    """Token di ogni candela, ATR, close, orari di chiusura e maschera della prima metà."""
    a_full = atr14(*(d[k].to_numpy() for k in "hlc"))
    d, a = d.iloc[15:], a_full[15:]
    o, h, l, c = (d[k].to_numpy() for k in "ohlc")
    A = np.arange(len(c)) < len(c) // 2
    up, lo = (h - np.maximum(o, c)) / a, (np.minimum(o, c) - l) / a
    tok = body_class(o, c, a, A) * 4 + (up > np.median(up[A])) * 2 + (lo > np.median(lo[A]))
    return tok, a, c, A, d.index


def higher(times_close, d, period):
    """Corpo (5 classi) dell'ultima candela superiore chiusa entro ogni orario di chiusura."""
    a = atr14(*(d[k].to_numpy() for k in "hlc"))[15:]
    d = d.iloc[15:]
    b = body_class(d.o.to_numpy(), d.c.to_numpy(), a, np.arange(len(d)) < len(d) // 2)
    hc = pd.DataFrame({"t": d.index + pd.Timedelta(period), "b": b})
    return pd.merge_asof(pd.DataFrame({"t": times_close}), hc, on="t", direction="backward")["b"].to_numpy()


def run(name, base, frames, sups, pip, spread):
    tok, a, c, A, idx = tokens(frames[base])
    close_t = idx + pd.Timedelta(PERIOD[base])
    T, C = pd.Series(tok), pd.Series(c)
    S1, S2 = (pd.Series(higher(close_t, frames[s], PERIOD[s])) for s in sups)
    variants = {"1 token": T, "2 token": T + T.shift(1) * V,
                "solo sup": S1 * 5 + S2, "1 token + sup": T * 25 + S1 * 5 + S2}
    out = []
    for var, ctx in variants.items():
        for h in HORIZONS:
            fut = (C.shift(-h) - C) / a
            ok = (ctx.notna() & fut.notna()).to_numpy()
            df = pd.DataFrame({"ctx": ctx, "fut": fut, "A": A})[ok]
            g = df[df.A].groupby("ctx").fut.agg(["mean", "std", "count"])
            g = g[g["count"] >= MIN_N]
            # ponytail: t semplice diviso sqrt(h) per la sovrapposizione, basta per scegliere i contesti forti
            g["t"] = g["mean"] / g["std"] * np.sqrt(g["count"] / h)
            test = df[~df.A]
            side = np.sign(test.ctx.map(g["mean"])).fillna(0).to_numpy()
            strong = np.abs(test.ctx.map(g["t"]).fillna(0).to_numpy()) >= 2
            fut_b = test.fut.to_numpy()
            pnl = side * fut_b * a[ok][~df.A.to_numpy()] / pip
            r = {"pair": name, "tf": base, "sup": "+".join(sups), "contesto": var, "h": h,
                 "ctx_usati": len(g), "ctx_forti": int((g.t.abs() >= 2).sum())}
            for tag, m in [("tutti", side != 0), ("forti", (side != 0) & strong)]:
                hit = m & (fut_b != 0)
                r[f"n_{tag}"] = int(m.sum())
                r[f"acc_{tag}"] = (np.sign(fut_b[hit]) == side[hit]).mean() if hit.any() else np.nan
                r[f"pip_{tag}"] = pnl[m].mean() if m.any() else np.nan
                r[f"t_{tag}"] = hh_t(pnl, m, h - 1)
            r["spread"] = spread
            out.append(r)
            if var == "1 token + sup" and h == 4:
                top = g.reindex(g.t.abs().sort_values(ascending=False).index).head(8)
                tm = test.groupby("ctx").fut.agg(["mean", "count"])
                ix = top.index.astype(int)
                show = pd.DataFrame({
                    "candela": [label(x // 25) for x in ix],
                    sups[0]: [BODY[x // 5 % 5] for x in ix], sups[1]: [BODY[x % 5] for x in ix],
                    "n_A": top["count"].astype(int).to_numpy(), "fut_A": top["mean"].to_numpy(), "t_A": top.t.to_numpy(),
                    "n_B": tm["count"].reindex(top.index).fillna(0).astype(int).to_numpy(),
                    "fut_B": tm["mean"].reindex(top.index).to_numpy()})
                print(f"-- {name} {base} + {'/'.join(sups)}: gli 8 contesti più forti nella prima metà (A) "
                      f"e come vanno nella seconda (B), h={h}, fut in ATR")
                print(show.round(3).to_string(index=False), "\n")
    return out


if __name__ == "__main__":
    pd.set_option("display.width", 220)
    rows = []
    for name in sys.argv[1:] or ["EUR_USD"]:
        _, raw, tfs = load_store(name)
        pip = 0.01 if "JPY" in name else 1e-4
        spread = ((raw.ask_c - raw.bid_c) / pip).median()
        m5 = tfs["M5"]
        # ponytail: D1 a blocchi dalle 22:00 UTC come H4, senza ora legale
        frames = {**tfs, "M15": m5.resample("15min").agg(AGG).dropna(),
                  "D1": m5.resample("24h", offset="22h").agg(AGG).dropna()}
        for base, sups in PLAN:
            rows += run(name, base, frames, sups, pip, spread)
    print("===== SINTESI: si impara sulla prima metà, si scommette sulla seconda (pip a operazione, prima dello spread)")
    print("tutti = ogni contesto visto almeno 100 volte; forti = solo quelli con |t| >= 2 nella prima metà")
    print(pd.DataFrame(rows).round(3).to_string(index=False))
