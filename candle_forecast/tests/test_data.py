"""Test 1: validazione dati -> report e arresto, passando dallo store HDF5 (APERTO-1). Più buchi e fuso."""
import numpy as np
import pandas as pd
import pytest

from candle_forecast import data
from synth import m5_frame

TICK = 1e-5


def _write(tmp_path, bid, ask):
    """Uno store come lo scrive parity_deriva/scripts/import_csv.py: indice naive UTC, chiave /M5."""
    # una riga per timestamp del BID, così com'è (ordine e duplicati compresi); l'ASK si allinea
    # per timestamp: un timestamp che manca su un lato, nello store, è una riga con NaN
    a = ask.set_index("timestamp").reindex(bid["timestamp"])
    frame = pd.DataFrame({f"{side}_{c[0]}": d[c].to_numpy() for side, d in (("bid", bid), ("ask", a))
                          for c in data.OHLC}, index=pd.to_datetime(bid["timestamp"].to_numpy(), unit="ms"))
    frame["volume"] = 1
    path = tmp_path / "X.hd5"
    frame.to_hdf(path, key="/M5", format="table")
    return path


@pytest.fixture
def frames():
    return m5_frame(pd.date_range("2020-01-06 00:00", periods=200, freq="5min", tz="UTC"))


def test_valid_loads(tmp_path, frames):
    df = data.load_store(_write(tmp_path, *frames), "X", TICK)
    assert list(df.columns) == [f"{s}_{c}" for s in ("bid", "ask") for c in data.OHLC]
    assert str(df.index.tz) == "UTC" and df.index[0] == pd.Timestamp("2020-01-06", tz="UTC")


def _bad_monotonic(b, a):
    b.loc[[10, 11], "timestamp"] = b.loc[[11, 10], "timestamp"].to_numpy()
    return b, a


def _bad_dup(b, a):
    b.loc[11, "timestamp"] = b.loc[10, "timestamp"]
    return b, a


def _bad_nan(b, a):
    b.loc[5, "close"] = np.nan
    return b, a


def _bad_high(b, a):
    b.loc[7, "high"] = min(b.loc[7, "open"], b.loc[7, "close"]) - 1e-5
    return b, a


def _bad_low(b, a):
    b.loc[7, "low"] = max(b.loc[7, "open"], b.loc[7, "close"]) + 1e-5
    return b, a


def _bad_ts_pair(b, a):
    return b, a.drop(index=20)


def _bad_ask_lt_bid(b, a):
    a.loc[3, "close"] = b.loc[3, "close"] - 2e-5
    a.loc[3, "low"] = min(a.loc[3, "low"], a.loc[3, "close"])
    return b, a


def _bad_decimals(b, a):
    b.loc[9, ["open", "high", "low", "close"]] += 3e-6
    return b, a


@pytest.mark.parametrize("breaker, needle", [
    (_bad_monotonic, "non crescente"), (_bad_dup, "duplicati"), (_bad_nan, "NaN"),
    (_bad_high, "high <"), (_bad_low, "low >"), (_bad_ts_pair, "ASK: NaN"),
    (_bad_ask_lt_bid, "ask_close < bid_close"), (_bad_decimals, "unità minima"),
])
def test_violation_stops_with_report(tmp_path, frames, breaker, needle):
    b, a = breaker(*(f.copy() for f in frames))
    with pytest.raises(data.DataError) as e:
        data.load_store(_write(tmp_path, b, a), "X", TICK)
    assert needle in e.value.report
    assert "esempi" in e.value.report


def test_gaps_ignore_weekend_and_find_holes():
    # venerdì 10 gen 2020 16:55 NY = 21:55 UTC; riapertura domenica 17:00 NY = 22:00 UTC
    week1 = pd.date_range("2020-01-10 20:00", "2020-01-10 21:55", freq="5min", tz="UTC")
    week2 = pd.date_range("2020-01-12 22:00", "2020-01-13 02:00", freq="5min", tz="UTC")
    idx = week1.append(week2).delete([5, 6, 7])          # buco di 3 barre il venerdì
    g = data.find_gaps(idx)
    assert g["missing_bars"].tolist() == [3]
    assert data.breaks(len(idx), g).sum() == 1


def test_read_store_same_as_parity_deriva(tmp_path, frames):
    """pd.read_hdf sulla chiave /M5 legge ciò che legge parity_deriva.data.store.load."""
    store = pytest.importorskip("parity_deriva.data.store")
    path = _write(tmp_path, *frames)
    bid, ask = data.read_store(path)
    ref = store.load(str(path), "M5")
    np.testing.assert_array_equal(bid["close"].to_numpy(), ref["bid_c"].to_numpy())
    np.testing.assert_array_equal(ask["high"].to_numpy(), ref["ask_h"].to_numpy())
    assert (bid.index.tz_localize(None) == ref.index).all()
