"""Sorgente CSV (`pack-csv` -> `prepare`): candele già H4, piatte del weekend tolte e contate."""
import numpy as np
import pandas as pd
import pytest

from candle_forecast import cli, config, data
from synth import m5_frame

TOML = """
results_dir = "results"
[split]
train = 0.75
val = 0.125
test = 0.125
early_stop_tail = 0.10
[instruments.X]
tick = 0.00001
csv = { H4 = "X_H4.csv" }
[grid]
instruments = ["X"]
timeframes = ["H4"]
N = [3]
M = [1]
models = ["lgbm"]
price_series = "mid"
exclude_gap_windows = false
indicators = []
"""


def _project(tmp_path, bid, ask):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "h4.toml").write_text(TOML)
    for name, d in (("bid", bid), ("ask", ask)):
        d.to_csv(tmp_path / f"{name}.csv", index=False)
    cfg = config.load(tmp_path / "config" / "h4.toml")
    cli.pack_csv(cfg, "X", "H4", tmp_path / "bid.csv", tmp_path / "ask.csv")
    return cfg


@pytest.fixture
def frames():
    """Due settimane H4 in UTC, con il weekend riempito di candele piatte come fanno alcuni export."""
    ts = pd.date_range("2020-01-06", periods=6 * 14, freq="4h", tz="UTC")
    bid, ask = m5_frame(ts)
    wk = np.asarray((ts.dayofweek == 5) | ((ts.dayofweek == 6) & (ts.hour < 20)))   # si riapre domenica 20:00 UTC
    for d in (bid, ask):
        last = d["close"].where(~wk).ffill()[wk].to_numpy()
        d.loc[wk, data.OHLC] = np.repeat(last[:, None], 4, axis=1)
    return bid, ask, int(wk.sum())


def test_flat_dropped_and_counted(tmp_path, frames):
    bid, ask, n_flat = frames
    cfg = _project(tmp_path, bid, ask)
    out = cli.prepare(cfg, "X", "H4")
    report = (out / "data_report.txt").read_text()
    assert f"(mercato chiuso) {n_flat}, con ASK sotto BID 0" in report
    assert "buchi fuori dal weekend: 0" in report
    z = np.load(out / "candles.npz")
    assert len(z["ts"]) == len(bid) - n_flat
    mid_open = (bid.loc[0, "open"] + ask.loc[0, "open"]) / 2 / 1e-5
    assert z["units"][0, 0] == pytest.approx(mid_open)                  # mid della prima candela: niente ricampionamento


def test_ask_below_bid_dropped(tmp_path, frames):
    bid, ask, n_flat = frames
    ask.loc[3, "open"] = bid.loc[3, "open"] - 2e-5
    ask.loc[3, "low"] = min(ask.loc[3, "low"], ask.loc[3, "open"])
    out = cli.prepare(_project(tmp_path, bid, ask), "X", "H4")
    assert f"(mercato chiuso) {n_flat}, con ASK sotto BID 1" in (out / "data_report.txt").read_text()
    ts = pd.to_datetime(np.load(out / "candles.npz")["ts"], utc=True)
    assert pd.Timestamp(bid.loc[3, "timestamp"], unit="ms", tz="UTC") not in ts
    assert len(ts) == len(bid) - n_flat - 1
