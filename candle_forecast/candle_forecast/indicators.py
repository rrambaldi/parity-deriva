"""Canali aggiuntivi (APERTO-5, Parte 7): SMA ed EMA sul close, ATR di Wilder su high/low/close.
sma, ema, true_range e atr sono copiati tali e quali da parity_deriva/lib/indicators.py, le curve che
leggono strategie e grafici: causali (il valore a t dipende solo da 0..t), None finché non sono calde.
Copiati perché il training gira anche dove parity_deriva non c'è; test_indicators.py verifica che
restino identici all'originale quando è importabile."""
from __future__ import annotations

import numpy as np

from .config import parse_indicator


# ---- da parity_deriva/lib/indicators.py, invariati
def sma(values, period):
    """Simple moving average, None until there are `period` values."""
    if period < 1:
        raise ValueError("sma period must be at least 1, got %r" % (period,))
    out = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values, period):
    """
    Exponential moving average, seeded with the simple average of its first
    `period` closes and None before that. See the module docstring for why the
    seeding is not an implementation detail.
    """
    if period < 1:
        raise ValueError("ema period must be at least 1, got %r" % (period,))
    out = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    current = sum(values[:period]) / float(period)
    out[period - 1] = current
    for i in range(period, len(values)):
        current = values[i] * k + current * (1 - k)
        out[i] = current
    return out


def true_range(highs, lows, closes):
    """
    max(high - low, |high - prev close|, |low - prev close|), per bar.

    The first bar has no previous close, so its true range is its own range.
    That is Wilder's own handling, and it matters only for the first bar of a
    series - which the warm-up has already excluded from anything.
    """
    out = []
    for i in range(len(highs)):
        if i == 0:
            out.append(highs[i] - lows[i])
            continue
        previous = closes[i - 1]
        out.append(max(highs[i] - lows[i],
                       abs(highs[i] - previous),
                       abs(lows[i] - previous)))
    return out


def atr(highs, lows, closes, period):
    """
    Wilder's ATR, which is what "ATR(14)" means on a chart.

    Wilder's smoothing and not a plain mean of the true ranges: the two differ
    by enough to move a 1.5 x ATR threshold across a pivot, and this is the one
    every broker platform draws. It is also the one ftw_ab/indicators.py
    computes, and that is the point of the choice rather than a coincidence -
    the line on the chart is the line the rule read, or it is decoration.

    Seeded with the mean of the first `period` true ranges and None before
    that, so the recursion starts where the data does.
    """
    if period < 1:
        raise ValueError("atr period must be at least 1, got %r" % (period,))
    ranges = true_range(highs, lows, closes)
    out = [None] * len(ranges)
    if len(ranges) < period:
        return out
    current = sum(ranges[:period]) / float(period)
    out[period - 1] = current
    for i in range(period, len(ranges)):
        # the recursion ewm(alpha=1/period, adjust=False) runs, written out
        current += (ranges[i] - current) / period
        out[i] = current
    return out



def channels(units: np.ndarray, names: list[str]) -> tuple[np.ndarray, np.ndarray, int]:
    """(T, K) canali in unità minime, maschera `level` (True = livello di prezzo, da esprimere come
    delta dal close di riferimento; False = ampiezza, come ATR) e prima candela in cui sono tutti caldi."""
    o, h, l, c = (units[:, j].tolist() for j in range(4))
    cols, level = [], []
    for name in names:
        kind, n = parse_indicator(name)
        if kind == "sma":
            v = sma(c, n)
        elif kind == "ema":
            v = ema(c, n)
        else:
            v = atr(h, l, c, n)
        cols.append(np.array([np.nan if x is None else x for x in v]))
        level.append(kind != "atr")
    extra = np.stack(cols, axis=1) if cols else np.empty((len(units), 0))
    warm = np.isfinite(extra).all(axis=1)
    warmup = int(np.argmax(warm)) if warm.any() else len(units)
    return extra, np.array(level, dtype=bool), warmup
