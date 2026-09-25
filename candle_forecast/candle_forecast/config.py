"""Caricamento e validazione di config/systems.toml."""
from __future__ import annotations

import itertools
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

TIMEFRAMES = ("M5", "H1", "H4", "D1", "W1")
PRICE_SERIES = ("mid", "bid", "ask")   # L0-P2
MODELS = ("lgbm", "gru")


class ConfigError(ValueError):
    pass


def load(path: str | Path) -> dict[str, Any]:
    path = Path(path).resolve()
    cfg = tomllib.loads(path.read_text())
    root = path.parent.parent                    # config/ sta nella radice del progetto
    cfg["root"] = root
    cfg["results_dir"] = root / cfg.get("results_dir", "results")
    pd_root = str((root / cfg.get("parity_deriva_root", "..")).resolve())
    if pd_root not in sys.path:
        sys.path.insert(0, pd_root)
    for inst in cfg["instruments"].values():
        if "store" in inst:
            inst["store_zip"] = root / "stores" / f"{inst['store']}.zip"   # in git; `prepare` lo scompatta
        # candele già nel timeframe, BID e ASK in un CSV (`pack-csv`): `prepare` le usa senza ricampionare
        inst["csv_zip"] = {tf: root / "stores" / f"{name}.zip" for tf, name in inst.get("csv", {}).items()}
    validate(cfg)
    return cfg


def validate(cfg: dict[str, Any]) -> None:
    s = cfg["split"]
    if abs(s["train"] + s["val"] + s["test"] - 1) > 1e-9:            # DEC-6
        raise ConfigError(f"frazioni di split non sommano a 1: {s}")
    if not 0 < s["early_stop_tail"] < 1:                                 # L0-P9
        raise ConfigError("early_stop_tail deve stare in (0, 1)")
    g = cfg["grid"]
    for name in g["instruments"]:
        if name not in cfg["instruments"]:
            raise ConfigError(f"strumento {name} senza sezione [instruments.{name}]")
        if not cfg["instruments"][name]["tick"] > 0:                    # DEC-3
            raise ConfigError(f"tick di {name} deve essere > 0")
        if bad := set(cfg["instruments"][name].get("csv", {})) - {"M5", "H1", "H4"}:
            raise ConfigError(f"{name}: CSV solo per timeframe a passo fisso (M5, H1, H4), non {bad}")
    if bad := set(g["timeframes"]) - set(TIMEFRAMES):
        raise ConfigError(f"timeframe non supportati: {bad}")
    if g["price_series"] not in PRICE_SERIES:
        raise ConfigError(f"price_series deve essere in {PRICE_SERIES}")
    if bad := set(g["models"]) - set(MODELS):
        raise ConfigError(f"modelli sconosciuti: {bad}")
    for name in g.get("indicators", []):
        parse_indicator(name)
    if any(n < 1 for n in g["N"]) or any(m < 1 for m in g["M"]):
        raise ConfigError("N e M devono essere >= 1")


def parse_indicator(name: str) -> tuple[str, int]:
    """'sma100' -> ('sma', 100). APERTO-5."""
    m = re.fullmatch(r"(sma|ema|atr)(\d+)", name)
    if not m or int(m.group(2)) < 1:
        raise ConfigError(f"indicatore non riconosciuto: {name!r} (sma<n>, ema<n>, atr<n>)")
    return m.group(1), int(m.group(2))


def systems(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Una voce per (strumento, timeframe, N, M); i modelli girano tutti sugli stessi campioni."""
    g = cfg["grid"]
    return [dict(instrument=i, timeframe=tf, N=n, M=m, price_series=g["price_series"],
                 indicators=list(g.get("indicators", [])))
            for i, tf, n, m in itertools.product(g["instruments"], g["timeframes"], g["N"], g["M"])]
