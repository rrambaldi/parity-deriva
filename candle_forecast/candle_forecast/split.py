"""Segmenti cronologici per indice di candela (DEC-6, L0-P1, L0-P9) e blocco del test."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np


class TestLocked(PermissionError):
    pass


def bounds(T: int, train: float, val: float, es_tail: float) -> dict[str, tuple[int, int]]:
    """Intervalli di candele [lo, hi). Il training si divide in fit + ultimo `es_tail` per l'early stopping."""
    a = int(T * train)
    b = int(T * (train + val))
    e = a - int(a * es_tail)
    return {"fit": (0, e), "es": (e, a), "train": (0, a), "val": (a, b), "test": (b, T)}


def in_segment(t: np.ndarray, N: int, M: int, lo: int, hi: int) -> np.ndarray:
    """Tutte le N+M candele del campione stanno in [lo, hi)."""
    return t[(t - N + 1 >= lo) & (t + M < hi)]


def segments(t: np.ndarray, N: int, M: int, b: dict[str, tuple[int, int]],
             open_test: bool = False) -> dict[str, np.ndarray | None]:
    """Campioni per segmento. Il test resta None se non si apre esplicitamente."""
    out: dict[str, np.ndarray | None] = {k: in_segment(t, N, M, *b[k]) for k in ("fit", "es", "train", "val")}
    out["test"] = in_segment(t, N, M, *b["test"]) if open_test else None
    return out


def require_open(seg: dict[str, np.ndarray | None]) -> np.ndarray:
    if seg.get("test") is None:
        raise TestLocked("test bloccato: serve --open-test")
    return seg["test"]  # type: ignore[return-value]


def log_open_test(results_dir: Path, argv: list[str]) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    with open(results_dir / "open_test.log", "a") as f:
        f.write(f"{dt.datetime.now().astimezone().isoformat()}  {' '.join(argv)}\n")
