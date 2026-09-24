"""Test 4 (campioni), 5 (anti-look-ahead), 6 (split), 7 (baseline)."""
import numpy as np
import pytest

from candle_forecast import baselines, samples, split
from synth import random_walk

TICK = 1e-5


def test_hand_built_sample():
    px = np.array([[1.10000, 1.10020, 1.09990, 1.10010],
                   [1.10010, 1.10030, 1.10000, 1.10025],
                   [1.10025, 1.10050, 1.10020, 1.10040],
                   [1.10040, 1.10045, 1.10000, 1.10005]])
    u = samples.to_units(px, TICK)
    X, Y = samples.windows(u, np.array([2]), N=2, M=1)
    assert X.shape == (1, 2, 4) and Y.shape == (1, 1, 3)
    np.testing.assert_array_equal(X[0], [[-30, -10, -40, -15], [-15, 10, -20, 0]])
    np.testing.assert_array_equal(Y[0], [[5, -40, -35]])
    assert X[0, -1, 3] == 0


def test_mid_half_units_exact():
    u = samples.to_units(np.array([(1.10001 + 1.10002) / 2]), TICK)
    assert u[0] == 110001.5


def test_extra_channels_as_delta():
    u = random_walk(50)
    extra = u[:, 3:4] + 7                                 # canale finto: close + 7
    X, _ = samples.windows(u, np.array([20]), 5, 1, extra=extra)
    assert X.shape == (1, 5, 5) and X[0, -1, 4] == 7


def test_gap_windows_excluded():
    brk = np.zeros(100, dtype=bool)
    brk[50] = True                                        # buco fra la barra 49 e la 50
    t, excl = samples.valid_refs(100, 5, 2, brk)
    assert not np.any((t - 5 + 1 < 50) & (t + 2 >= 50))
    # la finestra t-4 ... t+2 attraversa il buco per t = 48 ... 53
    assert 47 in t and 54 in t and not np.isin(np.arange(48, 54), t).any()
    assert excl == 6


# ---- test 5: anti-look-ahead
def test_truncated_series_identical_in_common_part():
    u = random_walk(500)
    N, M = 21, 2
    t_full, _ = samples.valid_refs(500, N, M)
    t_cut, _ = samples.valid_refs(300, N, M)
    Xf, Yf = samples.windows(u, t_full, N, M)
    Xc, Yc = samples.windows(u[:300], t_cut, N, M)
    k = len(t_cut)
    np.testing.assert_array_equal(t_full[:k], t_cut)
    np.testing.assert_array_equal(Xf[:k], Xc)
    np.testing.assert_array_equal(Yf[:k], Yc)


@pytest.mark.parametrize("N,M", [(21, 1), (37, 2), (108, 2)])
def test_future_edits_do_not_leak(N, M):
    u = random_walk(400)
    t = np.array([250])
    X0, Y0 = samples.windows(u, t, N, M)
    after_t = u.copy()
    after_t[251:] += 999
    X1, Y1 = samples.windows(after_t, t, N, M)
    np.testing.assert_array_equal(X0, X1)
    assert not np.array_equal(Y0, Y1)
    after_tm = u.copy()
    after_tm[251 + M:] += 999
    X2, Y2 = samples.windows(after_tm, t, N, M)
    np.testing.assert_array_equal(X0, X2)
    np.testing.assert_array_equal(Y0, Y2)


# ---- test 6: split
@pytest.mark.parametrize("N,M", [(21, 1), (108, 2)])
def test_no_candle_in_two_segments(N, M):
    T = 5000
    t, _ = samples.valid_refs(T, N, M)
    b = split.bounds(T, 0.75, 0.125, 0.10)
    seg = split.segments(t, N, M, b, open_test=True)
    used = {}
    for k in ("fit", "es", "val", "test"):
        cov = np.unique((seg[k][:, None] + np.arange(-N + 1, M + 1)).ravel())
        lo, hi = b[k]
        assert cov.min() >= lo and cov.max() < hi
        used[k] = set(cov)
    keys = list(used)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert not used[keys[i]] & used[keys[j]]


def test_test_locked_by_default():
    t, _ = samples.valid_refs(1000, 21, 1)
    seg = split.segments(t, 21, 1, split.bounds(1000, 0.75, 0.125, 0.1))
    assert seg["test"] is None
    with pytest.raises(split.TestLocked):
        split.require_open(seg)


def test_run_system_computes_no_test_metrics_without_flag():
    from candle_forecast.cli import run_system
    u = random_walk(3000)
    import pandas as pd
    ts = pd.date_range("2020-01-01", periods=3000, freq="5min", tz="UTC")
    r = run_system(u, ts, None, 21, 1, dict(train=0.75, val=0.125, test=0.125, early_stop_tail=0.1),
                   [], {}, {}, open_test=False, log=lambda *_: None)
    assert list(r["metrics"]) == ["val"] and r["n"]["test"] is None
    r = run_system(u, ts, None, 21, 1, dict(train=0.75, val=0.125, test=0.125, early_stop_tail=0.1),
                   [], {}, {}, open_test=True, log=lambda *_: None)
    assert list(r["metrics"]) == ["val", "test"]


# ---- test 7: baseline
def test_baselines_expected_values():
    # ultima candela: open -10, high +5, low -20, close 0  -> forma (+15, -10, +10)
    X = np.zeros((2, 3, 4), dtype=np.float32)
    X[:, -1] = [-10, 5, -20, 0]
    Ytr = np.array([[[4, -2, 1], [6, -4, 3]], [[8, -6, -1], [10, -8, 1]]], dtype=np.float32)
    rep = baselines.repeat(X, 2)
    np.testing.assert_array_equal(rep[0], [[15, -10, 10], [25, 0, 20]])
    z = baselines.zero(2, Ytr)
    np.testing.assert_array_equal(z[0], [[6, -4, 0], [8, -6, 0]])
    mn = baselines.mean(2, Ytr)
    np.testing.assert_array_equal(mn[1], [[6, -4, 0], [8, -6, 2]])
