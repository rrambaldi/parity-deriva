"""Metriche, CSV delle previsioni per sistema, riepilogo comune (Parte 6)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .samples import TARGET_COLS

KEY = ["instrument", "timeframe", "N", "M", "model", "price_series", "indicators", "segment"]


def metrics(Y: np.ndarray, P: np.ndarray) -> dict[str, float]:
    """Per ogni orizzonte: direzione del close (esclusi vero = 0 o previsto = 0), errore standard
    0.5/sqrt(n), MAE su high, low, close in unità minime."""
    out: dict[str, float] = {}
    for h in range(Y.shape[1]):
        yc, pc = Y[:, h, 2], P[:, h, 2]
        keep = (yc != 0) & (pc != 0)
        n = int(keep.sum())
        out[f"h{h+1}_dir_n"] = n
        out[f"h{h+1}_dir_excluded"] = int((~keep).sum())
        out[f"h{h+1}_dir_acc"] = float((np.sign(yc[keep]) == np.sign(pc[keep])).mean()) if n else np.nan
        out[f"h{h+1}_dir_se"] = 0.5 / np.sqrt(n) if n else np.nan
        for j, c in enumerate(TARGET_COLS):
            out[f"h{h+1}_mae_{c}"] = float(np.abs(Y[:, h, j] - P[:, h, j]).mean())
    return out


def diffs(model: dict[str, float], base: dict[str, dict[str, float]]) -> dict[str, float]:
    """Differenza modello - baseline su accuratezza di direzione e MAE."""
    keys = [k for k in next(iter(base.values())) if k.endswith(("_dir_acc", "_mae_high", "_mae_low", "_mae_close"))]
    return {f"{k}_minus_{b}": model[k] - m[k] for b, m in base.items() for k in keys}


def summarize_seeds(per_seed: list[dict[str, float]]) -> dict[str, float]:
    """GRU: media e intervallo fra seed."""
    df = pd.DataFrame(per_seed)
    out: dict[str, float] = df.mean().to_dict()
    for k in df.columns:
        if k.endswith(("_dir_acc", "_mae_close", "_mae_high", "_mae_low")):
            out[f"{k}_min"], out[f"{k}_max"] = float(df[k].min()), float(df[k].max())
    return out


def predictions_frame(ts: pd.DatetimeIndex, Y: np.ndarray, preds: dict[str, np.ndarray]) -> pd.DataFrame:
    """Una riga per campione: timestamp della candela t, target veri, tutte le previsioni."""
    cols: dict[str, Any] = {"timestamp": ts}
    for name, A in {"true": Y, **preds}.items():
        for h in range(Y.shape[1]):
            for j, c in enumerate(TARGET_COLS):
                cols[f"{name}_h{h+1}_{c}"] = A[:, h, j]
    return pd.DataFrame(cols)


def upsert_summary(path: Path, rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Aggiorna results/summary.csv: una riga per (sistema, modello, segmento)."""
    new = pd.DataFrame(rows)
    if path.exists():
        old = pd.read_csv(path)
        keys_new = set(map(tuple, new[KEY].astype(str).to_numpy()))
        old = old[[tuple(r) not in keys_new for r in old[KEY].astype(str).to_numpy()]]
        new = pd.concat([old, new], ignore_index=True)
    new.to_csv(path, index=False)
    return new
