"""Test 8 (impara se c'è segnale) e test 9 (non impara sul random walk: il test anti-leakage).
Passano per `run_system`, cioè per la stessa pipeline dei dati veri.
Iperparametri ridotti solo per il tempo del test: non sono quelli del config."""
import numpy as np
import pandas as pd
import pytest

from candle_forecast.cli import run_system
from candle_forecast.evaluate import metrics
from candle_forecast.indicators import channels
from synth import ar1, random_walk

SPLIT = dict(train=0.75, val=0.125, test=0.125, early_stop_tail=0.10)
LGBM = dict(n_estimators=300, learning_rate=0.05, num_leaves=31, seed=42, early_stopping_rounds=20, num_threads=2)
GRU = dict(hidden=32, layers=1, lr=1e-3, batch=256, seeds=[0, 1, 2], max_epochs=8, patience=2, num_threads=2)


def _run(u, N, M):
    """Con gli stessi indicatori del config (APERTO-5): il test anti-leakage copre le feature vere."""
    ts = pd.date_range("2020-01-01", periods=len(u), freq="5min", tz="UTC")
    extra, level, warmup = channels(u, ["sma100", "atr14", "ema21"])
    return run_system(u, ts, None, N, M, SPLIT, ["lgbm", "gru"], LGBM, GRU, log=lambda *_: None,
                      extra=extra, level=level, warmup=warmup)


def _per_model(r, M):
    """Metriche sulla validazione per lgbm e per ciascun seed della GRU, dai CSV delle previsioni."""
    df = r["preds"]["val"]
    cols = lambda name: df[[f"{name}_h{h}_{c}" for h in range(1, M + 1) for c in ("high", "low", "close")]] \
        .to_numpy().reshape(len(df), M, 3)
    Y = cols("true")
    names = ["lgbm"] + [f"gru_s{s}" for s in GRU["seeds"]]
    return {n: metrics(Y, cols(n)) for n in names}


def test_8_learns_ar1():
    r = _run(ar1(20_000, phi=0.3, seed=7), N=21, M=1)
    base = r["metrics"]["val"]
    for name, m in _per_model(r, 1).items():
        for b in ("ZERO", "REPEAT", "MEAN"):
            assert m["h1_mae_close"] < base[b]["h1_mae_close"], (name, b, m["h1_mae_close"], base[b]["h1_mae_close"])
        assert m["h1_dir_acc"] > 0.5 + 3 * m["h1_dir_se"], (name, m["h1_dir_acc"])


def test_9_random_walk_no_edge():
    r = _run(random_walk(50_000, seed=11), N=21, M=2)
    per = _per_model(r, 2) | {b: r["metrics"]["val"][b] for b in ("REPEAT",)}
    for name, m in per.items():
        for h in (1, 2):
            acc, se = m[f"h{h}_dir_acc"], m[f"h{h}_dir_se"]
            assert abs(acc - 0.5) <= 3 * se, f"{name} h{h}: {acc:.4f} fuori da 50% ± 3·{se:.4f}: leakage?"
