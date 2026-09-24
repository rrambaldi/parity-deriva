"""I modelli salvati si ricaricano e danno le stesse previsioni."""
import numpy as np

from candle_forecast import samples
from candle_forecast.indicators import channels
from candle_forecast.models import gru, lgbm
from synth import ar1

N, M = 21, 2


def _data():
    u = ar1(3000, seed=3)
    extra, level, warmup = channels(u, ["sma100", "atr14", "ema21"])
    t, _ = samples.valid_refs(len(u), N, M, warmup=warmup)
    X, Y = samples.windows(u, t, N, M, extra, level)
    return X[:2000], Y[:2000], X[2000:2400], Y[2000:2400], X[2400:]


def test_lgbm_roundtrip(tmp_path):
    Xf, Yf, Xe, Ye, Xv = _data()
    p = dict(n_estimators=60, learning_rate=0.1, num_leaves=7, seed=1, early_stopping_rounds=5, num_threads=1)
    boosters = lgbm.fit(Xf, Yf, Xe, Ye, p)
    names = lgbm.save(boosters, tmp_path)
    assert names == [f"lgbm_h{h}_{c}.txt" for h in (1, 2) for c in ("high", "low", "close")]
    np.testing.assert_allclose(lgbm.predict(lgbm.load(tmp_path, M), Xv, M), lgbm.predict(boosters, Xv, M),
                               rtol=0, atol=1e-5)


def test_gru_roundtrip(tmp_path):
    Xf, Yf, Xe, Ye, Xv = _data()
    p = dict(hidden=8, layers=1, lr=1e-3, batch=256, max_epochs=2, patience=1, num_threads=1)
    model, info = gru.fit(Xf, Yf, Xe, Ye, p, seed=0)
    gru.save(model, tmp_path / "gru_s0.pt", info)
    again = gru.load(tmp_path / "gru_s0.pt")
    np.testing.assert_array_equal(again.predict(Xv), model.predict(Xv))
