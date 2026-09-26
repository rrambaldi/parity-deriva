"""CLI: `prepare` crea i dati (report, candele, campioni per segmento); `run` addestra e valuta."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import baselines, config, data, evaluate, indicators, resample, samples, split, stores


def system_name(s: dict[str, Any]) -> str:
    ind = "_" + "-".join(s["indicators"]) if s["indicators"] else ""
    return f"{s['instrument']}_{s['timeframe']}_{s['price_series']}_N{s['N']}_M{s['M']}{ind}"


# ---------------------------------------------------------------- prepare

def prepare(cfg: dict[str, Any], instrument: str, timeframe: str) -> Path:
    inst = cfg["instruments"][instrument]
    kind = cfg["grid"]["price_series"]
    out = cfg["results_dir"] / f"{instrument}_{timeframe}_{kind}"
    out.mkdir(parents=True, exist_ok=True)

    if not inst["store_zip"].exists():
        raise SystemExit(f"{inst['store_zip']} mancante: lancia `pack-stores` dove c'è lo store")
    store_path = stores.unpack(inst["store_zip"])
    try:
        m5 = data.load_store(store_path, instrument, inst["tick"])
    except data.DataError as e:
        (out / "data_report.txt").write_text(e.report + "\n")
        raise
    report = [data.format_report(f"validazione {instrument} ({inst['store_zip'].name}, "
                                 f"sha256 {stores.zip_sha(inst['store_zip'])[:12]})", [])]
    gaps = data.find_gaps(m5.index)
    report.append(data.gap_report(instrument, m5.index, gaps))
    gaps.to_csv(out / "gaps.csv", index=False)

    candles = resample.resample(resample.price_series(m5, kind), timeframe)
    report.append(f"== candele {timeframe} ==\n" + resample.incomplete_stats(candles))
    if timeframe == "M5":
        brk = data.breaks(len(candles), gaps)
    else:
        # APERTO-3: le candele incomplete si tengono come sono. Il break (usato solo con
        # exclude_gap_windows) è sulla candela che contiene la barra dopo il buco.
        pos = candles.index.get_indexer(resample.bucket_bounds(m5.index[gaps["pos"]], timeframe)[0])
        brk = np.zeros(len(candles), dtype=bool)
        brk[pos] = True
    units = samples.to_units(candles[data.OHLC].to_numpy(), inst["tick"])
    names = cfg["grid"].get("indicators", [])
    extra, level, warmup = indicators.channels(units, names)
    np.savez(out / "candles.npz", ts=candles.index.as_unit("ns").asi8, units=units, breaks=brk,
             n_bars=candles["n_bars"].to_numpy(), n_expected=candles["n_expected"].to_numpy(),
             extra=extra, level=level, warmup=warmup, names=np.array(names))
    gap_rule = "escluse (L0-P6)" if cfg["grid"]["exclude_gap_windows"] else "tenute: la candela mancante non esiste (APERTO-3)"
    report.append(f"== indicatori {names} (APERTO-5) ==\n"
                  f"riscaldamento: scartate le prime {warmup} candele, primo input valido {candles.index[warmup]}\n"
                  f"finestre che attraversano un buco: {gap_rule}")

    # campioni per segmento, per ogni (N, M) del config: sono i dati di training/validazione/test
    s = cfg["split"]
    T = len(candles)
    b = split.bounds(T, s["train"], s["val"], s["early_stop_tail"])
    ts = candles.index
    report.append("== segmenti (candele [lo, hi)) ==\n" + "\n".join(
        f"  {k:5s} {lo:>8d}-{hi:<8d} {ts[lo]} -> {ts[hi - 1]}" for k, (lo, hi) in b.items()))
    counts, lines = {}, ["== campioni per sistema =="]
    for n in cfg["grid"]["N"]:
        for m in cfg["grid"]["M"]:
            t, excl = samples.valid_refs(T, n, m, brk if cfg["grid"]["exclude_gap_windows"] else None, warmup)
            seg = split.segments(t, n, m, b, open_test=True)   # indici soltanto: nessuna metrica
            np.savez(out / f"samples_N{n}_M{m}.npz", **{k: v for k, v in seg.items()})
            c = {k: int(len(v)) for k, v in seg.items()} | {"excluded_gaps": excl}
            counts[f"N{n}_M{m}"] = c
            lines.append(f"  N={n:<3d} M={m}  " + "  ".join(f"{k}={v}" for k, v in c.items()))
    report.append("\n".join(lines))
    (out / "samples.json").write_text(json.dumps({"bounds": b, "counts": counts}, indent=1))
    text = "\n\n".join(report)
    (out / "data_report.txt").write_text(text + "\n")
    print(text)
    return out


def load_prepared(cfg: dict[str, Any], instrument: str, timeframe: str) -> dict[str, Any]:
    d = cfg["results_dir"] / f"{instrument}_{timeframe}_{cfg['grid']['price_series']}" / "candles.npz"
    if not d.exists():
        raise FileNotFoundError(f"{d} mancante: lancia prima `prepare`")
    z = np.load(d)
    return {"ts": pd.DatetimeIndex(pd.to_datetime(z["ts"], utc=True)), "units": z["units"],
            "breaks": z["breaks"], "n_bars": z["n_bars"], "n_expected": z["n_expected"],
            "extra": z["extra"], "level": z["level"], "warmup": int(z["warmup"]), "names": list(z["names"])}


def pack_stores(cfg: dict[str, Any], names: list[str]) -> None:
    """Zippa gli store dalla cartella market di parity_deriva in stores/ (da mettere in git)."""
    from parity_deriva.data import market
    for name in names:
        inst = cfg["instruments"][name]
        src = Path(market.directory()) / inst["store"]
        written = stores.pack(src, inst["store_zip"])
        size = inst["store_zip"].stat().st_size / 1e6
        print(f"{name}: {src} -> {inst['store_zip']} ({size:.1f} MB) "
              + ("scritto" if written else "invariato, non riscritto"))


# ---------------------------------------------------------------- run

def run_system(units: np.ndarray, ts: pd.DatetimeIndex, breaks: np.ndarray | None, N: int, M: int,
               split_cfg: dict[str, float], models: list[str], lgbm_p: dict[str, Any],
               gru_p: dict[str, Any], open_test: bool = False, log=print,
               extra: np.ndarray | None = None, level: np.ndarray | None = None,
               warmup: int = 0) -> dict[str, Any]:
    """Un sistema completo su un array di candele in unità minime (più canali `extra` opzionali).
    Ritorna metriche per modello e segmento, previsioni barra per barra, info di training.
    Non scrive su disco."""
    T = len(units)
    t, excl = samples.valid_refs(T, N, M, breaks, warmup)
    b = split.bounds(T, split_cfg["train"], split_cfg["val"], split_cfg["early_stop_tail"])
    seg = split.segments(t, N, M, b, open_test=open_test)
    win = lambda idx: samples.windows(units, idx, N, M, extra, level)
    if models:
        X_fit, Y_fit = win(seg["fit"])
        X_es, Y_es = win(seg["es"])
    else:                                              # solo baseline: dal training servono i target, non X
        Y_fit, Y_es = samples.windows(units, seg["fit"], 1, M)[1], samples.windows(units, seg["es"], 1, M)[1]
    Y_train = np.concatenate([Y_fit, Y_es])            # baseline: statistiche su tutto il training
    eval_segs = ["val"] + (["test"] if open_test else [])
    ev = {k: win(seg[k]) for k in eval_segs}
    res: dict[str, Any] = {"n": {k: (len(v) if v is not None else None) for k, v in seg.items()},
                           "excluded_gaps": excl, "metrics": {}, "preds": {}, "info": {}, "models": {}}
    preds: dict[str, dict[str, np.ndarray]] = {k: baselines.all_baselines(ev[k][0], Y_train, M) for k in eval_segs}

    if "lgbm" in models:
        from .models import lgbm
        t0 = time.time()
        boosters = lgbm.fit(X_fit, Y_fit, X_es, Y_es, lgbm_p)
        res["info"]["lgbm"] = {"seed": lgbm_p["seed"], "best_iterations": [bo.best_iteration for bo in boosters],
                               "seconds": round(time.time() - t0, 1)}
        log(f"    lgbm {res['info']['lgbm']}")
        for k in eval_segs:
            preds[k]["lgbm"] = lgbm.predict(boosters, ev[k][0], M)
        res["models"]["lgbm"] = boosters
    if "gru" in models:
        from .models import gru
        res["info"]["gru"] = []
        res["models"]["gru"] = {}
        for sd in gru_p["seeds"]:
            t0 = time.time()
            model, info = gru.fit(X_fit, Y_fit, X_es, Y_es, gru_p, sd)
            info["seconds"] = round(time.time() - t0, 1)
            res["info"]["gru"].append(info)
            res["models"]["gru"][sd] = (model, info)
            log(f"    gru seed {sd}: epoche {info['epochs']}, es_mse {info['best_es_mse']:.4f}, {info['seconds']}s")
            for k in eval_segs:
                preds[k][f"gru_s{sd}"] = model.predict(ev[k][0])

    for k in eval_segs:
        Y = ev[k][1]
        m = {name: evaluate.metrics(Y, P) for name, P in preds[k].items()}
        base = {n: m[n] for n in ("ZERO", "REPEAT", "MEAN")}
        gru_keys = [n for n in m if n.startswith("gru_s")]
        if gru_keys:
            m["gru"] = evaluate.summarize_seeds([m[n] for n in gru_keys])
        for name in [n for n in m if not n.startswith("gru_s")]:
            if name not in base:
                m[name] |= evaluate.diffs(m[name], base)
        res["metrics"][k] = {n: v for n, v in m.items() if not n.startswith("gru_s")}
        res["preds"][k] = evaluate.predictions_frame(ts[seg[k]], Y, preds[k])
    return res


def run(cfg: dict[str, Any], args: argparse.Namespace) -> None:
    rd: Path = cfg["results_dir"]
    if args.open_test:
        split.log_open_test(rd, sys.argv)
    todo = [s for s in config.systems(cfg)
            if (not args.N or s["N"] in args.N) and (not args.M or s["M"] in args.M)]
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    for s in todo:
        key = (s["instrument"], s["timeframe"])
        if key not in cache:
            cache[key] = load_prepared(cfg, *key)
        d = cache[key]
        print(f"== {system_name(s)}  modelli: {args.models}")
        t0 = time.time()
        if d["names"] != s["indicators"]:
            raise SystemExit(f"indicatori preparati {d['names']} diversi dal config {s['indicators']}: rilancia `prepare`")
        r = run_system(d["units"], d["ts"], d["breaks"] if cfg["grid"]["exclude_gap_windows"] else None,
                       s["N"], s["M"], cfg["split"], [m for m in args.models if m != "baselines"],
                       cfg["lgbm"], cfg["gru"], args.open_test,
                       extra=d["extra"], level=d["level"], warmup=d["warmup"])
        out = rd / system_name(s)
        out.mkdir(parents=True, exist_ok=True)
        rows = []
        for seg, per_model in r["metrics"].items():
            r["preds"][seg].to_csv(out / f"predictions_{seg}.csv", index=False, float_format="%.4f")
            for model, m in per_model.items():
                seeds = (cfg["lgbm"]["seed"] if model == "lgbm" else
                         " ".join(map(str, cfg["gru"]["seeds"])) if model == "gru" else "")
                rows.append({**{k: s[k] for k in ("instrument", "timeframe", "N", "M")}, "model": model,
                             "price_series": s["price_series"], "indicators": " ".join(s["indicators"]) or "none",
                             "segment": seg, "seeds": seeds,
                             "n_train": r["n"]["train"], "n_fit": r["n"]["fit"], "n_es": r["n"]["es"],
                             f"n_{seg}": r["n"][seg], "excluded_gaps": r["excluded_gaps"], **m})
        # i modelli si tengono tutti (results/<sistema>/models/), si decide dopo se usarli
        if "lgbm" in r["models"]:
            from .models import lgbm
            r["info"]["lgbm"]["files"] = lgbm.save(r["models"]["lgbm"], out / "models")
        if "gru" in r["models"]:
            from .models import gru
            for (sd, (model, info)), meta in zip(r["models"]["gru"].items(), r["info"]["gru"]):
                gru.save(model, out / "models" / f"gru_s{sd}.pt", info)
                meta["file"] = f"gru_s{sd}.pt"
        (out / "train_info.json").write_text(json.dumps(r["info"], indent=1, default=float))
        evaluate.upsert_summary(rd / "summary.csv", rows)
        for row in rows:
            h = " ".join(f"h{i}: dir {row[f'h{i}_dir_acc']:.4f}±{row[f'h{i}_dir_se']:.4f} "
                         f"mae_c {row[f'h{i}_mae_close']:.2f}" for i in range(1, s["M"] + 1))
            print(f"  {row['segment']:4s} {row['model']:7s} n={row[f'n_{row['segment']}']}  {h}")
        print(f"  ({time.time() - t0:.0f}s)")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="candle_forecast")
    p.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config" / "systems.toml"))
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("prepare", help="valida i CSV, report buchi, candele e campioni per segmento")
    pp.add_argument("--instrument")
    pp.add_argument("--timeframe")
    pk = sub.add_parser("pack-stores", help="zippa gli store da DATA_DIR in stores/ (per git)")
    pk.add_argument("--instrument", nargs="*", help="default: tutti quelli in [instruments]")
    pr = sub.add_parser("run", help="baseline + modelli sulla validazione")
    pr.add_argument("--N", type=int, nargs="*")
    pr.add_argument("--M", type=int, nargs="*")
    pr.add_argument("--models", nargs="+", default=["baselines", "lgbm", "gru"],
                    choices=["baselines", "lgbm", "gru"])
    pr.add_argument("--open-test", action="store_true", help="calcola le metriche sul test (loggato)")
    a = p.parse_args(argv)
    cfg = config.load(a.config)
    if a.cmd == "pack-stores":
        pack_stores(cfg, a.instrument or list(cfg["instruments"]))
    elif a.cmd == "prepare":
        for i in [a.instrument] if a.instrument else cfg["grid"]["instruments"]:
            for tf in [a.timeframe] if a.timeframe else cfg["grid"]["timeframes"]:
                prepare(cfg, i, tf)
    else:
        run(cfg, a)


if __name__ == "__main__":
    main()
