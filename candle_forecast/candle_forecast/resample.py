"""Serie di prezzo (L0-P2, L0-P4) e ricampionamento M5 -> timeframe con ancora 17:00 New York (L0-P3)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import BAR, NY, OHLC

SHIFT = pd.Timedelta(hours=7)          # 17:00 New York + 7h = 00:00: l'ancora diventa mezzanotte
SPAN = {"H4": pd.Timedelta(hours=4), "D1": pd.Timedelta(days=1), "W1": pd.Timedelta(days=5)}


def price_series(df: pd.DataFrame, kind: str = "mid") -> pd.DataFrame:
    """mid campo per campo: (bid + ask) / 2. Su high e low è un'approssimazione (L0-P4)."""
    if kind in ("bid", "ask"):
        out = df[[f"{kind}_{c}" for c in OHLC]].copy()
        out.columns = OHLC
        return out
    if kind != "mid":
        raise ValueError(kind)
    return pd.DataFrame({c: (df[f"bid_{c}"] + df[f"ask_{c}"]) / 2 for c in OHLC}, index=df.index)


def _localize(wall: pd.DatetimeIndex) -> pd.DatetimeIndex:
    # Le ancore (17, 21, 01, 05, 09, 13) esistono sempre; le 01:00 del giorno del ritorno
    # all'ora solare sono ambigue: si prende la prima (ora legale), inizio reale dell'intervallo.
    return wall.tz_localize(NY, ambiguous=np.ones(len(wall), dtype=bool)).tz_convert("UTC")


def bucket_bounds(index: pd.DatetimeIndex, tf: str) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Inizio e fine (UTC) della candela `tf` che contiene ogni barra M5."""
    if tf == "M5":
        return index, index + BAR
    if tf == "H1":                     # New York è a ore intere da UTC: H1 = ora UTC
        s = index.floor("h")
        return s, s + pd.Timedelta(hours=1)
    wall = index.tz_convert(NY).tz_localize(None) + SHIFT
    if tf == "H4":
        s = wall.floor("4h")
    elif tf == "D1":
        s = wall.normalize()
    elif tf == "W1":                   # lunedì 00:00 spostato = domenica 17:00 New York
        s = wall.normalize() - pd.to_timedelta(wall.dayofweek, unit="D")
    else:
        raise ValueError(tf)
    s = s - SHIFT
    return _localize(s), _localize(s + SPAN[tf])


def resample(px: pd.DataFrame, tf: str) -> pd.DataFrame:
    """OHLC per candela, indicizzato per inizio intervallo in UTC (L0-P5),
    con barre M5 presenti (`n_bars`) e attese (`n_expected`)."""
    start, end = bucket_bounds(px.index, tf)
    g = px.groupby(start)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                        "low": g["low"].min(), "close": g["close"].last(),
                        "n_bars": g["open"].size()})
    ends = pd.Series(end, index=start).groupby(level=0).first()
    out["n_expected"] = ((ends - out.index.to_series()) / BAR).astype(int).to_numpy()
    out.index.name = "timestamp"
    return out


def incomplete_stats(candles: pd.DataFrame) -> str:
    """Statistiche delle candele incomplete, per la decisione APERTO-3."""
    inc = candles["n_bars"] < candles["n_expected"]
    frac = candles["n_bars"] / candles["n_expected"]
    lines = [f"candele: {len(candles)}, incomplete: {int(inc.sum())} ({inc.mean():.2%})"]
    if inc.any():
        q = frac[inc].quantile([0, 0.1, 0.5, 0.9]).round(3).to_dict()
        lines.append(f"frazione di barre presenti nelle incomplete (quantili): {q}")
        worst = candles[inc].assign(frac=frac[inc]).sort_values("frac").head(10)
        lines += [f"  {t}  {r.n_bars}/{r.n_expected}" for t, r in worst.iterrows()]
    return "\n".join(lines)
