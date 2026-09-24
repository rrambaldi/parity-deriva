"""Finestre N+M in delta dal close di riferimento, in unità minime (DEC-2, Parte 3, Parte 7)."""
from __future__ import annotations

import numpy as np

TARGET_COLS = ("high", "low", "close")


def to_units(prices: np.ndarray, tick: float) -> np.ndarray:
    """Prezzi -> unità minime. I prezzi validati sono multipli del tick, il mid di mezzo tick:
    l'arrotondamento a 0.5 toglie solo il rumore della virgola mobile (così close vero = 0 è esatto)."""
    return np.rint(np.asarray(prices, dtype=np.float64) / tick * 2) / 2


def valid_refs(T: int, N: int, M: int, breaks: np.ndarray | None = None,
               warmup: int = 0) -> tuple[np.ndarray, int]:
    """Indici t con finestra t-N+1 ... t+M completa, dopo il riscaldamento degli indicatori
    (prima candela dell'input >= `warmup`) e, se `breaks` è dato, senza buchi dentro (L0-P6).
    Ritorna (t validi, finestre escluse per buchi)."""
    t = np.arange(warmup + N - 1, T - M)
    if breaks is None or not breaks.any():
        return t, 0
    cb = np.cumsum(breaks)
    # un buco "dentro" la finestra è un break su una barra da t-N+2 a t+M
    ok = cb[t + M] - cb[t - N + 1] == 0
    return t[ok], int((~ok).sum())


def windows(units: np.ndarray, t: np.ndarray, N: int, M: int, extra: np.ndarray | None = None,
            level: np.ndarray | None = None, chunk: int = 50_000) -> tuple[np.ndarray, np.ndarray]:
    """X: (S, N, 4+K) = candele t-N+1 ... t (open, high, low, close[, canali extra]) - close[t];
    Y: (S, M, 3) = (high, low, close) di t+1 ... t+M - close[t]. Tutto in unità minime.
    `extra` (T, K): canali aggiuntivi già in unità minime (Parte 7). `level[k]` True: livello di
    prezzo, delta dal close[t] come le candele (default); False: ampiezza (ATR), presa com'è."""
    feats = units if extra is None else np.hstack([units, extra])
    S, C = len(t), feats.shape[1]
    minus_ref = np.ones(C, dtype=np.float64)
    if extra is not None and level is not None:
        minus_ref[4:] = level
    X = np.empty((S, N, C), dtype=np.float32)
    Y = np.empty((S, M, 3), dtype=np.float32)
    back, fwd = np.arange(-N + 1, 1), np.arange(1, M + 1)
    for a in range(0, S, chunk):                       # a pezzi: con N=108 la matrice intera in float64 non ci sta
        tt = t[a:a + chunk]
        ref = units[tt, 3][:, None, None]
        X[a:a + chunk] = feats[tt[:, None] + back] - ref * minus_ref
        Y[a:a + chunk] = units[tt[:, None] + fwd][:, :, 1:4] - ref
    return X, Y
