"""Serie sintetiche per i test: nessun dato reale."""
from __future__ import annotations

import numpy as np
import pandas as pd


def candles_from_returns(r: np.ndarray, seed: int = 0, start: float = 100_000) -> np.ndarray:
    """(T, 4) in unità minime intere: open = close precedente, high/low con escursione casuale."""
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(r)
    open_ = np.r_[start, close[:-1]]
    ext = np.abs(np.rint(rng.normal(0, 4, (len(r), 2))))
    high = np.maximum(open_, close) + ext[:, 0]
    low = np.minimum(open_, close) - ext[:, 1]
    return np.stack([open_, high, low, close], axis=1).astype(np.float64)


def random_walk(T: int, seed: int = 0, sd: float = 10) -> np.ndarray:
    return candles_from_returns(np.rint(np.random.default_rng(seed).normal(0, sd, T)), seed + 1)


def ar1(T: int, phi: float = 0.3, seed: int = 0, sd: float = 10) -> np.ndarray:
    rng = np.random.default_rng(seed)
    e = rng.normal(0, sd, T)
    r = np.empty(T)
    r[0] = e[0]
    for i in range(1, T):
        r[i] = phi * r[i - 1] + e[i]
    return candles_from_returns(np.rint(r), seed + 1)


def index(T: int) -> pd.DatetimeIndex:
    return pd.date_range("2020-01-06 00:00", periods=T, freq="5min", tz="UTC")


def m5_frame(ts: pd.DatetimeIndex, base: float = 1.1, tick: float = 1e-5, spread: int = 10,
             seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """BID e ASK M5 validi in formato CSV (timestamp in ms)."""
    u = random_walk(len(ts), seed, sd=3)
    bid = pd.DataFrame(np.round((u - u[0, 0]) * tick + base, 5), columns=["open", "high", "low", "close"])
    ask = np.round(bid + spread * tick, 5)
    for d in (bid, ask):
        d.insert(0, "timestamp", ts.as_unit("ms").asi8)
        d["volume"] = 1.0
    return bid, ask
