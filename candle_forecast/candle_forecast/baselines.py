"""Baseline ZERO, REPEAT, MEAN (L0-P7). Tutte ritornano (S, M, 3): high, low, close."""
from __future__ import annotations

import numpy as np


def zero(S: int, Y_train: np.ndarray) -> np.ndarray:
    """close = 0 su ogni orizzonte; high e low = media dei rispettivi target nel training."""
    p = np.zeros((S, *Y_train.shape[1:]), dtype=np.float32)
    p[:, :, :2] = Y_train[:, :, :2].mean(axis=0)
    return p


def repeat(X: np.ndarray, M: int) -> np.ndarray:
    """La prossima candela ripete la forma dell'ultima (misurata dal suo open), con open = ref = 0;
    dall'orizzonte 2 la stessa forma riparte dal close previsto."""
    last = X[:, -1, :4]
    shape = np.stack([last[:, 1] - last[:, 0], last[:, 2] - last[:, 0], last[:, 3] - last[:, 0]], axis=1)
    p = np.empty((len(X), M, 3), dtype=np.float32)
    start = np.zeros(len(X), dtype=np.float32)
    for h in range(M):
        p[:, h] = start[:, None] + shape
        start = p[:, h, 2]
    return p


def mean(S: int, Y_train: np.ndarray) -> np.ndarray:
    return np.broadcast_to(Y_train.mean(axis=0), (S, *Y_train.shape[1:])).astype(np.float32)


def all_baselines(X: np.ndarray, Y_train: np.ndarray, M: int) -> dict[str, np.ndarray]:
    return {"ZERO": zero(len(X), Y_train), "REPEAT": repeat(X, M), "MEAN": mean(len(X), Y_train)}
