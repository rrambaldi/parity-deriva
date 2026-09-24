"""LightGBM: un regressore per ogni valore di target, input N x C appiattito (Parte 5, L0-P8)."""
from __future__ import annotations

from typing import Any

from pathlib import Path

import lightgbm as lgb
import numpy as np

from ..samples import TARGET_COLS


def fit(X_fit: np.ndarray, Y_fit: np.ndarray, X_es: np.ndarray, Y_es: np.ndarray,
        p: dict[str, Any]) -> list[lgb.Booster]:
    params = dict(objective="regression", learning_rate=p["learning_rate"], num_leaves=p["num_leaves"],
                  seed=p["seed"], deterministic=True, num_threads=p.get("num_threads", 0), verbose=-1)
    yf, ye = Y_fit.reshape(len(Y_fit), -1).T.copy(), Y_es.reshape(len(Y_es), -1).T.copy()  # righe contigue
    # un solo Dataset binnato, si cambia solo l'etichetta: con N=108 la matrice pesa
    dtrain = lgb.Dataset(X_fit.reshape(len(X_fit), -1), label=yf[0], free_raw_data=True).construct()
    dval = lgb.Dataset(X_es.reshape(len(X_es), -1), label=ye[0], reference=dtrain).construct()
    boosters = []
    for j in range(len(yf)):
        dtrain.set_label(yf[j])
        dval.set_label(ye[j])
        boosters.append(lgb.train(params, dtrain, num_boost_round=p["n_estimators"], valid_sets=[dval],
                                  callbacks=[lgb.early_stopping(p["early_stopping_rounds"], verbose=False)]))
    return boosters


def save(boosters: list[lgb.Booster], d: Path) -> list[str]:
    """Un file di testo per regressore, `lgbm_h{h}_{high|low|close}.txt`, tagliato al best_iteration."""
    d.mkdir(parents=True, exist_ok=True)
    names = []
    for j, b in enumerate(boosters):
        h, c = divmod(j, 3)
        name = f"lgbm_h{h + 1}_{TARGET_COLS[c]}.txt"
        b.save_model(d / name, num_iteration=b.best_iteration)
        names.append(name)
    return names


def load(d: Path, M: int) -> list[lgb.Booster]:
    return [lgb.Booster(model_file=str(d / f"lgbm_h{h + 1}_{c}.txt")) for h in range(M) for c in TARGET_COLS]


def predict(boosters: list[lgb.Booster], X: np.ndarray, M: int) -> np.ndarray:
    flat = X.reshape(len(X), -1)
    return np.stack([b.predict(flat, num_iteration=b.best_iteration) for b in boosters], axis=1) \
        .reshape(len(X), M, 3).astype(np.float32)
