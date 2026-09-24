"""Test 2 (ricampionamento, ancora 17:00 NY, ora legale) e test 3 (mid)."""
import numpy as np
import pandas as pd

from candle_forecast import resample
from candle_forecast.data import is_weekend


def _m5(start: str, end: str) -> pd.DataFrame:
    ts = pd.date_range(start, end, freq="5min", tz="UTC", inclusive="left")
    ts = ts[~is_weekend(ts)]
    x = np.arange(len(ts), dtype=float)
    return pd.DataFrame({"open": x, "high": x + 2, "low": x - 1, "close": x + 1}, index=ts)


def _utc_hours(d, lo, hi):
    ny = d.index.tz_convert("America/New_York")
    sel = (ny >= pd.Timestamp(lo, tz="America/New_York")) & (ny < pd.Timestamp(hi, tz="America/New_York"))
    return set(d.index[sel].hour)


def test_d1_close_moves_with_dst():
    # marzo 2021: ora legale USA dal 14/03. Prima 17:00 NY = 22:00 UTC, dopo = 21:00 UTC
    d = resample.resample(_m5("2021-03-07 00:00", "2021-03-20 00:00"), "D1")
    assert _utc_hours(d, "2021-03-07", "2021-03-13") == {22}
    assert _utc_hours(d, "2021-03-14", "2021-03-20") == {21}
    # novembre 2021: fine ora legale il 07/11, si torna alle 22:00 UTC
    d = resample.resample(_m5("2021-10-31 00:00", "2021-11-13 00:00"), "D1")
    assert _utc_hours(d, "2021-10-31", "2021-11-06") == {21}
    assert _utc_hours(d, "2021-11-07", "2021-11-13") == {22}


def test_d1_five_per_week_no_sunday_and_ohlc():
    px = _m5("2021-03-07 00:00", "2021-03-20 00:00")
    d = resample.resample(px, "D1")
    ny = d.index.tz_convert("America/New_York")
    # ogni daily parte alle 17:00 NY del giorno prima: nessuna candela che *finisce* di domenica
    assert (ny.hour == 17).all()
    assert (d.index + pd.Timedelta(days=1)).tz_convert("America/New_York").dayofweek.isin([0, 1, 2, 3, 4]).all()
    assert (d["n_bars"] == d["n_expected"]).iloc[1:-1].all() and (d["n_expected"] == 288).all()
    first = px[(px.index >= d.index[1]) & (px.index < d.index[2])]
    r = d.iloc[1]
    assert (r.open, r.high, r.low, r.close) == (first.open.iloc[0], first.high.max(), first.low.min(), first.close.iloc[-1])


def test_h4_aligned_to_anchor_and_dst_day():
    d = resample.resample(_m5("2021-11-01 00:00", "2021-11-12 00:00"), "H4")
    ny_hours = set(d.index.tz_convert("America/New_York").hour)
    assert ny_hours == {17, 21, 1, 5, 9, 13}
    # il cambio d'ora cade di domenica a mercato chiuso: le ancore restano, in UTC slittano di un'ora
    assert _utc_hours(d, "2021-11-01", "2021-11-06") == {21, 1, 5, 9, 13, 17}
    assert _utc_hours(d, "2021-11-07", "2021-11-12") == {22, 2, 6, 10, 14, 18}
    assert (d["n_expected"] == 48).all()


def test_w1_sunday_to_friday():
    d = resample.resample(_m5("2021-03-01 00:00", "2021-03-27 00:00"), "W1")
    ny = d.index.tz_convert("America/New_York")
    assert (ny.dayofweek == 6).all() and (ny.hour == 17).all()
    assert d["n_bars"].iloc[1:-1].tolist() == d["n_expected"].iloc[1:-1].tolist()
    assert (d["n_expected"] == 1440).all()


def test_h1_is_utc_hour():
    d = resample.resample(_m5("2021-03-08 00:00", "2021-03-09 00:00"), "H1")
    assert (d.index.minute == 0).all() and (d["n_expected"] == 12).all()


def test_mid_field_by_field():
    df = pd.DataFrame({"bid_open": [1.0], "bid_high": [1.4], "bid_low": [0.8], "bid_close": [1.2],
                       "ask_open": [1.2], "ask_high": [1.5], "ask_low": [1.0], "ask_close": [1.3]})
    m = resample.price_series(df, "mid").iloc[0]
    assert np.allclose(m.to_numpy(), [1.1, 1.45, 0.9, 1.25])
    assert resample.price_series(df, "bid").iloc[0].tolist() == [1.0, 1.4, 0.8, 1.2]
    assert resample.price_series(df, "ask").iloc[0].tolist() == [1.2, 1.5, 1.0, 1.3]
