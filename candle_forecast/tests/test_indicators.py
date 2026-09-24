"""APERTO-5: SMA100, ATR14, EMA21 come canali; riscaldamento; nessun look-ahead."""
import numpy as np
import pytest

from candle_forecast import samples
from candle_forecast.indicators import channels
from synth import random_walk

NAMES = ["sma100", "atr14", "ema21"]


def test_known_values_small_periods():
    u = np.array([[10, 12, 9, 11], [11, 14, 10, 13], [13, 13, 8, 9], [9, 10, 7, 10]], dtype=float)
    extra, level, warmup = channels(u, ["sma2", "ema2", "atr2"])
    np.testing.assert_allclose(extra[1:, 0], [12, 11, 9.5])                  # (11+13)/2, (13+9)/2, (9+10)/2
    # EMA2: seme = media dei primi 2 close = 12, poi k = 2/3
    np.testing.assert_allclose(extra[1:, 1], [12, 12 / 3 + 9 * 2 / 3, (12 / 3 + 6) / 3 + 10 * 2 / 3])
    # TR: 3, max(4,3,1)=4, max(5,0,5)=5, max(3,1,2)=3; ATR2 Wilder: seme 3.5, poi +(tr-atr)/2
    np.testing.assert_allclose(extra[1:, 2], [3.5, 4.25, 3.625])
    assert level.tolist() == [True, True, False]
    assert warmup == 1 and np.isnan(extra[0]).all()


def test_warmup_is_sma100():
    u = random_walk(500)
    extra, level, warmup = channels(u, NAMES)
    assert warmup == 99
    assert np.isnan(extra[98, 0]) and np.isfinite(extra[99:]).all()
    t, _ = samples.valid_refs(500, 21, 1, warmup=warmup)
    assert t[0] == 99 + 21 - 1                    # la prima candela dell'input è la 99


def test_channels_in_window():
    u = random_walk(500)
    extra, level, warmup = channels(u, NAMES)
    t = np.array([300])
    X, _ = samples.windows(u, t, 21, 1, extra, level)
    assert X.shape == (1, 21, 7)
    np.testing.assert_allclose(X[0, -1, 4], extra[300, 0] - u[300, 3], rtol=1e-6)   # SMA: delta da ref
    np.testing.assert_allclose(X[0, -1, 5], extra[300, 1], rtol=1e-6)              # ATR: ampiezza
    np.testing.assert_allclose(X[0, -1, 6], extra[300, 2] - u[300, 3], rtol=1e-6)   # EMA: delta da ref
    assert np.isfinite(X).all()


@pytest.mark.parametrize("N,M", [(21, 1), (108, 2)])
def test_indicators_no_look_ahead(N, M):
    u = random_walk(600)
    t = np.array([400])
    e0, lv, _ = channels(u, NAMES)
    X0, Y0 = samples.windows(u, t, N, M, e0, lv)
    later = u.copy()
    later[401:] += 777                            # futuro diverso, indicatori ricalcolati da capo
    e1, _, _ = channels(later, NAMES)
    X1, _ = samples.windows(later, t, N, M, e1, lv)
    np.testing.assert_array_equal(X0, X1)
    cut = u[:401]                                 # serie troncata a t: stessi indicatori fino a t
    e2, _, _ = channels(cut, NAMES)
    np.testing.assert_allclose(e0[:401], e2, rtol=0, atol=1e-9, equal_nan=True)


def test_same_as_parity_deriva():
    """Le copie restano identiche alle curve di parity_deriva (dove parity_deriva c'è)."""
    orig = pytest.importorskip("parity_deriva.lib.indicators")
    from candle_forecast import indicators as mine
    u = random_walk(400)
    h, l, c = (u[:, j].tolist() for j in (1, 2, 3))
    assert mine.sma(c, 100) == orig.sma(c, 100)
    assert mine.ema(c, 21) == orig.ema(c, 21)
    assert mine.atr(h, l, c, 14) == orig.atr(h, l, c, 14)
