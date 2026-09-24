"""GRU: 1 strato da 32, testa lineare su 3M uscite, MSE, Adam (Parte 5, L0-P8).
Input e target divisi per la deviazione standard per colonna del training: scalatura tecnica
della rete, le previsioni tornano in unità minime prima della valutazione (DEC-2 invariato)."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn


class Net(nn.Module):
    def __init__(self, n_in: int, hidden: int, layers: int, n_out: int):
        super().__init__()
        self.gru = nn.GRU(n_in, hidden, num_layers=layers, batch_first=True)
        self.head = nn.Linear(hidden, n_out)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.gru(x)
        return self.head(out[:, -1])


def col_std(A: np.ndarray, chunk: int = 20_000) -> np.ndarray:
    """Deviazione standard per ultima colonna, a pezzi e in float64 (A può essere grande)."""
    C = A.shape[-1]
    n, s, ss = 0, np.zeros(C), np.zeros(C)
    for a in range(0, len(A), chunk):
        x = A[a:a + chunk].reshape(-1, C).astype(np.float64)
        n, s, ss = n + len(x), s + x.sum(0), ss + (x * x).sum(0)
    sd = np.sqrt(np.maximum(ss / n - (s / n) ** 2, 0))
    return np.where(sd > 0, sd, 1.0).astype(np.float32)


class Scaled:
    """Modello addestrato + costanti di scala."""

    def __init__(self, net: Net, sx: np.ndarray, sy: np.ndarray, M: int):
        self.net, self.sx, self.sy, self.M = net, sx, sy, M

    @torch.no_grad()
    def predict(self, X: np.ndarray, batch: int = 8192) -> np.ndarray:
        self.net.eval()
        out = [self.net(torch.from_numpy(X[a:a + batch] / self.sx)).numpy() for a in range(0, len(X), batch)]
        return (np.concatenate(out) * self.sy).reshape(len(X), self.M, 3).astype(np.float32)


def save(model: Scaled, path: Path, info: dict[str, Any]) -> None:
    """Pesi, costanti di scala e forma della rete in un solo file: si ricarica senza il config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    g = model.net.gru
    torch.save({"state_dict": model.net.state_dict(), "sx": torch.from_numpy(model.sx),
                "sy": torch.from_numpy(model.sy), "M": model.M, "n_in": g.input_size,
                "hidden": g.hidden_size, "layers": g.num_layers, "seed": info["seed"],
                "epochs": info["epochs"], "best_es_mse": info["best_es_mse"]}, path)


def load(path: Path) -> Scaled:
    z = torch.load(path, weights_only=True)
    net = Net(z["n_in"], z["hidden"], z["layers"], 3 * z["M"])
    net.load_state_dict(z["state_dict"])
    return Scaled(net, z["sx"].numpy(), z["sy"].numpy(), z["M"])


def fit(X_fit: np.ndarray, Y_fit: np.ndarray, X_es: np.ndarray, Y_es: np.ndarray,
        p: dict[str, Any], seed: int) -> tuple[Scaled, dict[str, Any]]:
    torch.manual_seed(seed)
    torch.set_num_threads(p.get("num_threads", 1))
    rng = np.random.default_rng(seed)
    M = Y_fit.shape[1]
    sx = col_std(X_fit)
    sy = col_std(Y_fit.reshape(len(Y_fit), -1)[:, None, :]).reshape(-1)
    net = Net(X_fit.shape[2], p["hidden"], p["layers"], 3 * M)
    opt = torch.optim.Adam(net.parameters(), lr=p["lr"])
    loss_fn = nn.MSELoss()
    model = Scaled(net, sx, sy, M)
    ye = Y_es.reshape(len(Y_es), -1) / sy

    def es_loss() -> float:
        pred = model.predict(X_es).reshape(len(X_es), -1) / sy
        return float(((pred - ye) ** 2).mean())

    best, best_state, bad, history = np.inf, None, 0, []
    for epoch in range(p["max_epochs"]):
        net.train()
        for idx in np.array_split(rng.permutation(len(X_fit)), max(1, len(X_fit) // p["batch"])):
            idx.sort()                               # lettura più contigua, stesso batch
            xb = torch.from_numpy(X_fit[idx] / sx)
            yb = torch.from_numpy(Y_fit[idx].reshape(len(idx), -1) / sy)
            opt.zero_grad()
            loss_fn(net(xb), yb).backward()
            opt.step()
        cur = es_loss()
        history.append(round(cur, 6))
        if cur < best:
            best, best_state, bad = cur, copy.deepcopy(net.state_dict()), 0
        else:
            bad += 1
            if bad >= p["patience"]:
                break
    net.load_state_dict(best_state)
    return model, {"seed": seed, "epochs": len(history), "best_es_mse": best, "es_history": history}
