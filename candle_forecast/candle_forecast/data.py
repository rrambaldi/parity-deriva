"""Adapter BID/ASK M5, validazione e report (Parte 1). Nessuna correzione silenziosa."""
from __future__ import annotations

import tarfile
from pathlib import Path

import numpy as np
import pandas as pd

OHLC = ["open", "high", "low", "close"]
BAR = pd.Timedelta(minutes=5)
BARS = {"M5": BAR, "H1": pd.Timedelta(hours=1), "H4": pd.Timedelta(hours=4)}   # passo fisso: sorgenti .tbz
NY = "America/New_York"


class DataError(RuntimeError):
    """Il caricamento si ferma: il report dice cosa non va. La politica la decide Roberto."""

    def __init__(self, report: str):
        super().__init__(report)
        self.report = report


def read_store(path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """APERTO-1: lo store HDF5 di parity_deriva (chiave /M5, colonne bid_o ... ask_c). Per una chiave
    presente è ciò che fa parity_deriva.data.store.load. APERTO-2: indice naive in UTC, inizio barra
    (L0-P5). Il mid dello store non si usa: si ricalcola da BID e ASK (L0-P2)."""
    frame = pd.read_hdf(path, "/M5")
    frame.index = pd.DatetimeIndex(frame.index).tz_localize("UTC")
    return _sides(frame)


def read_tbz(path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Un archivio come `data/EURUSD_M5.tbz` di parity_deriva: due CSV esportati, `...-BID.csv` e
    `...-ASK.csv` (timestamp in ms UTC, inizio barra, L0-P5; open, high, low, close[, volume]).
    Si tolgono, e si contano nel report (H4-1), le candele piatte su BID e ASK (high = low: il mercato
    chiuso che alcuni export riempiono, weekend e Natale) e quelle con un prezzo ASK sotto il BID
    (poche aperture della domenica 2006-2009): per il resto sono candele mancanti (APERTO-3)."""
    sides = {}
    with tarfile.open(path) as tar:
        for m in tar.getmembers():
            for side in ("bid", "ask"):
                if m.name.endswith(f"-{side.upper()}.csv"):
                    d = pd.read_csv(tar.extractfile(m), index_col="timestamp")[OHLC]
                    sides[side] = d.rename(columns=lambda c: f"{side}_{c[0]}")
    if len(sides) != 2:
        raise DataError(f"== {Path(path).name} ==\n- servono un -BID.csv e un -ASK.csv, trovati: {sorted(sides)}")
    frame = pd.concat([sides["bid"], sides["ask"]], axis=1)   # un timestamp su un lato solo: NaN, lo ferma validate
    frame.index = pd.to_datetime(frame.index, unit="ms", utc=True)
    flat = (frame["bid_h"] == frame["bid_l"]) & (frame["ask_h"] == frame["ask_l"])
    crossed = pd.concat([frame[f"ask_{c}"] < frame[f"bid_{c}"] for c in "ohlc"], axis=1).any(axis=1) & ~flat
    dropped = {"piatte su BID e ASK (mercato chiuso)": int(flat.sum()), "con ASK sotto BID": int(crossed.sum())}
    return *_sides(frame[~flat & ~crossed]), dropped


def _sides(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame.index.name = "timestamp"
    sides = []
    for side in ("bid", "ask"):
        d = frame[[f"{side}_{c[0]}" for c in OHLC]]
        d.columns = OHLC
        sides.append(d)
    return sides[0], sides[1]


def _examples(mask: pd.Series | np.ndarray, index: pd.Index, k: int = 5) -> list[str]:
    return [str(t) for t in index[np.asarray(mask)][:k]]


def check_side(df: pd.DataFrame, tick: float, name: str) -> list[tuple[str, int, list[str]]]:
    """Violazioni su un singolo file: (controllo, quante, primi esempi)."""
    out: list[tuple[str, int, list[str]]] = []
    idx = df.index
    if not idx.is_monotonic_increasing:
        bad = np.r_[False, np.diff(idx.as_unit("ns").asi8) < 0]
        out.append((f"{name}: indice non crescente", int(bad.sum()), _examples(bad, idx)))
    dup = idx.duplicated()
    if dup.any():
        out.append((f"{name}: timestamp duplicati", int(dup.sum()), _examples(dup, idx)))
    nan = df[OHLC].isna().any(axis=1)
    if nan.any():
        out.append((f"{name}: NaN", int(nan.sum()), _examples(nan, idx)))
    hi = df["high"] < df[["open", "close"]].max(axis=1)
    if hi.any():
        out.append((f"{name}: high < max(open, close)", int(hi.sum()), _examples(hi, idx)))
    lo = df["low"] > df[["open", "close"]].min(axis=1)
    if lo.any():
        out.append((f"{name}: low > min(open, close)", int(lo.sum()), _examples(lo, idx)))
    q = df[OHLC].to_numpy() / tick                                   # DEC-3
    off = (np.abs(q - np.rint(q)) > 1e-6).any(axis=1)
    if off.any():
        out.append((f"{name}: prezzi non multipli dell'unità minima {tick}", int(off.sum()),
                    _examples(off, idx)))
    return out


def check_pair(bid: pd.DataFrame, ask: pd.DataFrame) -> list[tuple[str, int, list[str]]]:
    out: list[tuple[str, int, list[str]]] = []
    if not bid.index.equals(ask.index):
        only_b = bid.index.difference(ask.index)
        only_a = ask.index.difference(bid.index)
        out.append(("timestamp diversi fra BID e ASK", len(only_b) + len(only_a),
                    [f"solo BID {t}" for t in only_b[:3]] + [f"solo ASK {t}" for t in only_a[:3]]))
        return out
    for c in OHLC:
        bad = ask[c] < bid[c]
        if bad.any():
            out.append((f"ask_{c} < bid_{c}", int(bad.sum()), _examples(bad, bid.index)))
    return out


def format_report(title: str, violations: list[tuple[str, int, list[str]]]) -> str:
    lines = [f"== {title} =="]
    if not violations:
        lines.append("nessuna violazione")
    for what, n, ex in violations:
        lines.append(f"- {what}: {n}  esempi: {', '.join(ex)}")
    return "\n".join(lines)


def validate(bid: pd.DataFrame, ask: pd.DataFrame, symbol: str, tick: float) -> pd.DataFrame:
    """DataFrame M5 in UTC con bid_* e ask_*. Solleva DataError con il report se c'è una violazione."""
    v = check_side(bid, tick, "BID") + check_side(ask, tick, "ASK")
    if not v:
        v = check_pair(bid, ask)
    if v:
        raise DataError(format_report(f"validazione {symbol}", v))
    return pd.concat([bid.add_prefix("bid_"), ask.add_prefix("ask_")], axis=1)


def load_store(path: str | Path, symbol: str, tick: float) -> pd.DataFrame:
    return validate(*read_store(path), symbol, tick)


def is_weekend(ts: pd.DatetimeIndex) -> np.ndarray:
    """Chiusura normale: da venerdì 17:00 a domenica 17:00 New York (L0-P3)."""
    ny = ts.tz_convert(NY)
    d, h = ny.dayofweek, ny.hour
    return np.asarray(((d == 4) & (h >= 17)) | (d == 5) | ((d == 6) & (h < 17)))


def find_gaps(index: pd.DatetimeIndex, bar: pd.Timedelta = BAR) -> pd.DataFrame:
    """Buchi = barre attese mancanti fuori dalla chiusura del weekend.
    Una riga per coppia di barre consecutive con almeno una barra attesa mancante;
    `pos` è la posizione della barra dopo il buco."""
    delta = np.diff(index.as_unit("ns").asi8)
    rows = []
    for i in np.flatnonzero(delta > bar.value):
        slots = pd.date_range(index[i] + bar, index[i + 1] - bar, freq=bar)
        missing = int((~is_weekend(slots)).sum())
        if missing:
            rows.append((i + 1, index[i], index[i + 1], missing, (index[i + 1] - index[i]) / pd.Timedelta(minutes=1)))
    return pd.DataFrame(rows, columns=["pos", "last_before", "first_after", "missing_bars", "delta_min"])


def breaks(n: int, gaps: pd.DataFrame) -> np.ndarray:
    """breaks[i] = True se fra la barra i-1 e la barra i c'è un buco segnalato (L0-P6, se attivo)."""
    b = np.zeros(n, dtype=bool)
    b[gaps["pos"].to_numpy(dtype=int)] = True
    return b


def gap_report(symbol: str, index: pd.DatetimeIndex, gaps: pd.DataFrame, top: int = 30) -> str:
    wk = is_weekend(index).sum()
    lines = [f"== buchi {symbol} ({index[0]} -> {index[-1]}, {len(index)} barre) ==",
             f"barre dentro la chiusura del weekend: {wk}",
             f"buchi fuori dal weekend: {len(gaps)}, barre mancanti totali: {int(gaps['missing_bars'].sum())}"]
    if len(gaps):
        bins = [0, 1, 2, 6, 12, 48, 288, 10**9]
        labels = ["1", "2", "3-6", "7-12", "13-48", "49-288", ">288"]
        dist = pd.cut(gaps["missing_bars"], bins, labels=labels).value_counts().reindex(labels)
        lines.append("distribuzione barre mancanti per buco: " + ", ".join(f"{k}: {v}" for k, v in dist.items()))
        lines.append(f"i {top} più lunghi:")
        for r in gaps.sort_values("missing_bars", ascending=False).head(top).itertuples():
            lines.append(f"  {r.last_before} -> {r.first_after}  mancanti {r.missing_bars}  (delta {r.delta_min:.0f} min)")
    return "\n".join(lines)
