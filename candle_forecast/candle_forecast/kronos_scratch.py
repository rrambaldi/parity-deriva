"""Kronos addestrato da zero su EURUSD M5, da confrontare con LightGBM (excursion_lgbm.py sul server).

Stesso taglio di excursion_lgbm: si impara sulle candele prima di --split meno 288 (l'ultimo 10% sceglie
il modello migliore), si prevede dopo. Due fasi, come nel paper di Kronos:
  1. tokenizer (config di Kronos-Tokenizer-base): ogni candela OHLCV diventa due token, grosso e fine;
  2. modello autoregressivo (config di Kronos-mini, 4,1M parametri): impara il token successivo.
Ingresso come Kronos: open, high, low, close, volume, amount (volume x prezzo medio), normalizzati
sulle --lookback candele di contesto, più minuto/ora/giorno/mese.
Test: un punto ogni --every candele dopo --split; per ognuno --samples percorsi di 288 candele.
Per h = 16, 48, 288 si salva la media sui percorsi di massimo, minimo e close raggiunti: sono prezzi,
le unità in ATR le calcola il confronto sul server (una sola definizione per i due modelli):
    python -m parity_deriva.scripts.excursion_lgbm EUR_USD --compare results/kronos/<run>/predictions.csv

Il codice di Kronos (MIT) non è copiato nel repo: si clona in third_party/ al commit fissato.
    python -m candle_forecast.kronos_scratch                 # da zero (serve una GPU per tempi umani)
    python -m candle_forecast.kronos_scratch --pretrained    # Kronos-small già addestrato, nessun training
    python -m candle_forecast.kronos_scratch --smoke         # prova di pochi minuti con modelli minuscoli
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

from . import stores

ROOT = Path(__file__).resolve().parent.parent
KRONOS_REPO = "https://github.com/shiyu-coder/Kronos.git"
KRONOS_COMMIT = "67b630e67f6a18c9e9be918d9b4337c960db1e9a"
HORIZONS, PRED_LEN, EMBARGO, CLIP = (16, 48, 288), 288, 288, 5.0
# config pubblicate su Hugging Face (NeoQuasar/Kronos-Tokenizer-base e NeoQuasar/Kronos-mini)
TOKENIZER_CFG = dict(d_in=6, d_model=256, n_heads=4, ff_dim=512, n_enc_layers=4, n_dec_layers=4, ffn_dropout_p=0.0,
                     attn_dropout_p=0.0, resid_dropout_p=0.0, s1_bits=10, s2_bits=10, beta=0.05, gamma0=1.0,
                     gamma=1.1, zeta=0.05, group_size=4)
PREDICTOR_CFG = dict(s1_bits=10, s2_bits=10, n_layers=4, d_model=256, n_heads=4, ff_dim=512, ffn_dropout_p=0.2,
                     attn_dropout_p=0.0, resid_dropout_p=0.2, token_dropout_p=0.0, learn_te=True)
SMOKE = dict(d_model=32, ff_dim=64, n_heads=2, s1_bits=4, s2_bits=4)


def kronos_modules():
    """Kronos da GitHub al commit fissato, importato così com'è (model/kronos.py, model/module.py)."""
    src = ROOT / "third_party" / "Kronos"
    if not (src / "model" / "kronos.py").exists():
        subprocess.run(["git", "clone", "-q", KRONOS_REPO, str(src)], check=True)
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != KRONOS_COMMIT:
        subprocess.run(["git", "-C", str(src), "-c", "advice.detachedHead=false", "checkout", "-q", KRONOS_COMMIT],
                       check=True)
    sys.path.insert(0, str(src))
    from model.kronos import Kronos, KronosTokenizer, auto_regressive_inference
    return Kronos, KronosTokenizer, auto_regressive_inference


def load(zip_path: Path):
    raw = pd.read_hdf(stores.unpack(zip_path), "/M5")
    ohlc = np.column_stack([(raw[f"bid_{k}"] + raw[f"ask_{k}"]).to_numpy() / 2 for k in "ohlc"])
    vol = raw["volume"].to_numpy(float)
    feat = np.column_stack([ohlc, vol, vol * ohlc.mean(axis=1)]).astype(np.float32)
    ix = pd.DatetimeIndex(raw.index)
    stamp = np.column_stack([ix.minute, ix.hour, ix.dayofweek, ix.day, ix.month]).astype(np.float32)
    return ix, feat, stamp


def windows(feat, stamp, starts, length, lookback):
    """Finestre normalizzate sulle prime `lookback` candele (solo passato), tagliate a +-CLIP."""
    idx = starts[:, None] + np.arange(length)
    x, st = feat[idx], stamp[idx]
    mean, std = x[:, :lookback].mean(axis=1, keepdims=True), x[:, :lookback].std(axis=1, keepdims=True)
    return np.clip((x - mean) / (std + 1e-5), -CLIP, CLIP), st, mean, std


def train_stage(name, model, step_loss, val_loss, starts_fit, starts_val, args, lr, clip_norm, dev):
    opt = torch.optim.AdamW(model.parameters(), lr=lr, betas=(0.9, 0.95), weight_decay=0.1)
    total = args.epochs * args.steps
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=total, pct_start=0.03, div_factor=10)
    rng = np.random.default_rng(args.seed)
    best, best_state, t0, log = math.inf, None, time.time(), []
    for ep in range(args.epochs):
        model.train()
        for i in range(args.steps):
            loss = step_loss(rng.choice(starts_fit, args.batch))
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), clip_norm)
            opt.step()
            sched.step()
            if ep == 0 and i == 19:
                eta = (time.time() - t0) / 20 * total
                print(f"[{name}] {dev}: circa {eta / 3600:.1f} ore per questa fase", flush=True)
        model.eval()
        vrng = np.random.default_rng(0)
        with torch.no_grad():
            v = float(np.mean([val_loss(vrng.choice(starts_val, args.batch)).item() for _ in range(args.val_steps)]))
        log.append({"epoch": ep + 1, "val_loss": v, "minuti": round((time.time() - t0) / 60, 1)})
        print(f"[{name}] epoca {ep + 1}/{args.epochs}  val {v:.4f}  ({log[-1]['minuti']} min)", flush=True)
        if v < best:
            best, best_state = v, {k: t.detach().clone() for k, t in model.state_dict().items()}
    model.load_state_dict(best_state)
    return log


def main(argv=None):
    p = argparse.ArgumentParser(prog="kronos_scratch")
    p.add_argument("--store", default="stores/EUR_USD.hd5.zip")
    p.add_argument("--split", default="2020-11-11", help="primo giorno di test, lo stesso di excursion_lgbm")
    p.add_argument("--lookback", type=int, default=256)
    p.add_argument("--train-len", type=int, default=321, help="candele per finestra di training")
    p.add_argument("--epochs", type=int, default=15)
    p.add_argument("--steps", type=int, default=2000, help="passi per epoca")
    p.add_argument("--val-steps", type=int, default=100)
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--every", type=int, default=96, help="un punto di test ogni tante candele")
    p.add_argument("--samples", type=int, default=8, help="percorsi generati per punto")
    p.add_argument("--gen-batch", type=int, default=32, help="punti di test generati insieme")
    p.add_argument("--temperature", type=float, default=1.0)
    p.add_argument("--top-p", type=float, default=0.99)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--pretrained", action="store_true", help="Kronos-small pubblicato, senza training")
    p.add_argument("--smoke", action="store_true", help="modelli minuscoli, pochi passi, pochi punti")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    if args.smoke:
        args.epochs, args.steps, args.val_steps, args.batch, args.samples = 1, 30, 5, 8, 2
    torch.manual_seed(args.seed)
    torch.set_num_threads(os.cpu_count() or 1)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    Kronos, KronosTokenizer, generate = kronos_modules()

    ix, feat, stamp = load(ROOT / args.store)
    split = int(np.searchsorted(ix, pd.Timestamp(args.split)))
    L, W = args.lookback, args.train_len
    run = args.out or ("pretrained-small" if args.pretrained else "smoke" if args.smoke else "scratch-mini")
    out = ROOT / "results" / "kronos" / run
    out.mkdir(parents=True, exist_ok=True)
    meta = {"args": vars(args), "kronos_commit": KRONOS_COMMIT, "device": dev, "store": args.store,
            "test_from": str(ix[split])}

    if args.pretrained:
        tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base").to(dev)
        model = Kronos.from_pretrained("NeoQuasar/Kronos-small").to(dev)
    else:
        tcfg, pcfg = dict(TOKENIZER_CFG), dict(PREDICTOR_CFG)
        if args.smoke:
            tcfg.update(SMOKE, n_enc_layers=2, n_dec_layers=2)
            pcfg.update(SMOKE, n_layers=1)
        meta["tokenizer_cfg"], meta["predictor_cfg"] = tcfg, pcfg
        end = split - EMBARGO - W                     # ultima partenza: la finestra finisce prima dell'embargo
        starts = np.arange(0, end)
        cut = int(len(starts) * 0.9)
        fit, val = starts[:cut], starts[cut + W:]     # le finestre di validazione non toccano quelle di fit
        tensor = lambda a: torch.from_numpy(np.ascontiguousarray(a)).to(dev)

        tok = KronosTokenizer(**tcfg).to(dev)

        def tok_loss(s):
            x = tensor(windows(feat, stamp, s, W, L)[0])
            (z_pre, z), bsq_loss, _, _ = tok(x)
            return (F.mse_loss(z_pre, x) + F.mse_loss(z, x) + bsq_loss) / 2

        def tok_val(s):
            x = tensor(windows(feat, stamp, s, W, L)[0])
            return F.mse_loss(tok(x)[0][1], x)

        meta["tokenizer_log"] = train_stage("tokenizer", tok, tok_loss, tok_val, fit, val, args, 2e-4, 2.0, dev)
        tok.eval()
        model = Kronos(**pcfg).to(dev)

        def lm_loss(s):
            x, st, _, _ = windows(feat, stamp, s, W, L)
            with torch.no_grad():
                t0, t1 = tok.encode(tensor(x), half=True)
            s1, s2 = model(t0[:, :-1], t1[:, :-1], tensor(st[:, :-1]))
            return model.head.compute_loss(s1, s2, t0[:, 1:], t1[:, 1:])[0]

        meta["predictor_log"] = train_stage("predictor", model, lm_loss, lm_loss, fit, val, args, 3e-4, 3.0, dev)
        torch.save(tok.state_dict(), out / "tokenizer.pt")
        torch.save(model.state_dict(), out / "predictor.pt")
    model.eval()
    tok.eval()

    pts = np.arange(max(split, L), len(ix) - PRED_LEN, args.every)
    if args.smoke:
        pts = pts[:6]
    rows, t_start = [], time.time()
    for b in range(0, len(pts), args.gen_batch):
        t0 = pts[b:b + args.gen_batch]
        x, st, mean, std = windows(feat, stamp, t0 - L + 1, L, L)
        y_st = stamp[t0[:, None] + 1 + np.arange(PRED_LEN)]
        rep = lambda a: torch.from_numpy(np.repeat(a, args.samples, axis=0)).to(dev)
        z = generate(tok, model, rep(x), rep(st), rep(y_st), 512, PRED_LEN, CLIP, args.temperature, 0, args.top_p, 1)
        z = z[:, -PRED_LEN:, :4] * np.repeat(std[:, :, :4], args.samples, axis=0) \
            + np.repeat(mean[:, :, :4], args.samples, axis=0)
        z = z.reshape(len(t0), args.samples, PRED_LEN, 4)
        hi, lo, cl = z.max(axis=-1), z.min(axis=-1), z[..., 3]     # candela generata: massimo e minimo su OHLC
        for j, t in enumerate(t0):
            r = {"ts": ix[t].isoformat(), "close_t0": float(feat[t, 3])}
            for h in HORIZONS:
                r[f"hi_{h}"] = float(hi[j, :, :h].max(axis=1).mean())
                r[f"lo_{h}"] = float(lo[j, :, :h].min(axis=1).mean())
                r[f"cl_{h}"] = float(cl[j, :, h - 1].mean())
            rows.append(r)
        if b == 0:
            eta = (time.time() - t_start) / len(t0) * len(pts)
            print(f"[test] {len(pts)} punti, circa {eta / 3600:.1f} ore", flush=True)
    pd.DataFrame(rows).to_csv(out / "predictions.csv", index=False)
    meta["test_points"] = len(rows)
    (out / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    print(f"FATTO: {out / 'predictions.csv'} ({len(rows)} punti)")


if __name__ == "__main__":
    main()
