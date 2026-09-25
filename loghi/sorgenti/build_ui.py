"""Asset per lo studio dei componenti: testata senza tagline, grafico prezzi zoomato su un trade, curva capitale.
Valori d'esempio (sintetici), non risultati di backtest."""
import os
import random
import build as B
import build_mix as M
from textpath import Font, fnt

OUT = "out_ui"
os.makedirs(OUT, exist_ok=True)
MONO = Font("../definitivo/font/IBMPlexMono-Regular.woff2")   # richiede il modulo brotli

THEMES = {
    "dark": dict(panel="#102433", grid="#1A3244", axis="#8E9CA6", text="#F4EFE4", entry="#3F8FDB",
                 up="#26A37A", down="#E5655C", neutral="#F4EFE4", trail="#B7C2C9", shade="#3F8FDB", shade_op=0.09,
                 ink="#F4EFE4", accent="#C98A45", hy="#C98A45", base="#F4EFE4"),
    "light": dict(panel="#FFFFFF", grid="#EEEAE2", axis="#5E6E78", text="#152C3A", entry="#2A74C7",
                  up="#15855F", down="#C9423A", neutral="#152C3A", trail="#5E6E78", shade="#2A74C7", shade_op=0.07,
                  ink="#152C3A", accent="#C98A45", hy="#C98A45", base="#152C3A"),
}


# ------------------------------------------------------------------ testata: marchio + nome, senza tagline
def header_lockup(mode):
    t = THEMES[mode]
    c = dict(base=t["base"], accent=t["accent"])
    nf = fnt("marcellus", 400)
    NS = 40
    cap = nf.cap_height(NS)
    ms = 64                                   # altezza marchio
    v0, v1 = 11.5, 96
    k = ms / (v1 - v0)
    gap = 18
    tx = 104 * k + gap
    W = tx + nf.width("PARITY-DERIVA", NS, 0.16) + 4
    H = ms
    mark = f'<g transform="translate({-8 * k:.2f} {-v0 * k:.2f}) scale({k:.4f})">{M.mark_m1(c)}</g>'
    wm, _ = B.wordmark(nf, NS, 0.16, tx, H / 2 + cap / 2, t["ink"], t["hy"], anchor="start")
    return B.svg(W, H, mark + wm)


# ------------------------------------------------------------------ dati d'esempio
def candles(seed=4):
    rnd = random.Random(seed)
    keys = [(0, 1.22360), (10, 1.22250), (19, 1.22400), (26, 1.22560), (29, 1.22470), (34, 1.22840), (47, 1.22720)]
    target = []
    for (a, ya), (b, yb) in zip(keys, keys[1:]):
        for i in range(a, b):
            target.append(ya + (yb - ya) * (i - a) / (b - a))
    target.append(keys[-1][1])
    bars, prev = [], target[0]
    for i, tc in enumerate(target):
        o = prev
        c = tc + rnd.gauss(0, 0.00018)
        h = max(o, c) + abs(rnd.gauss(0, 0.00012))
        l = min(o, c) - abs(rnd.gauss(0, 0.00012))
        bars.append([o, h, l, c])
        prev = c
    # scenario: entrata long a 20, stop spostato a 29, uscita al target a 34
    bars[20][0] = 1.22415
    bars[34][1] = max(bars[34][1], 1.22872)
    return bars


ENTRY, SL, TP, TRAIL = 1.22415, 1.22190, 1.22865, 1.22395
I_ENTRY, I_TRAIL, I_EXIT = 20, 29, 34


def label(txt, x, y, color, anchor="start", size=11):
    d, _ = MONO.path(txt, size, 0.0, x, y, anchor)
    return f'<path d="{d}" fill="{color}"/>'


def price_chart(mode):
    t = THEMES[mode]
    bars = candles()
    W, H = 1350, 400
    L, R, T, Bm = 12, 86, 14, 30
    pw, ph = W - L - R, H - T - Bm
    lo, hi = 1.22120, 1.22960
    n = len(bars)
    step = pw / n
    X = lambda i: L + step * (i + 0.5)
    Y = lambda p: T + (hi - p) / (hi - lo) * ph
    out = [f'<rect width="{W}" height="{H}" fill="{t["panel"]}"/>']
    # griglia e asse prezzi
    p = 1.2215
    while p < hi:
        y = Y(p)
        out.append(f'<path d="M{L} {y:.1f} H{L + pw}" stroke="{t["grid"]}" stroke-width="1"/>')
        out.append(label(f"{p:.4f}", W - 8, y + 4, t["axis"], "end"))
        p += 0.0010
    # campitura del trade
    x0, x1 = X(I_ENTRY) - step / 2, X(I_EXIT) + step / 2
    out.append(f'<rect x="{x0:.1f}" y="{T}" width="{x1 - x0:.1f}" height="{ph}" fill="{t["shade"]}" fill-opacity="{t["shade_op"]}"/>')
    # candele
    bw = step * 0.56
    for i, (o, h, l, c) in enumerate(bars):
        col = t["up"] if c >= o else t["down"]
        x = X(i)
        out.append(f'<path d="M{x:.1f} {Y(h):.1f} V{Y(l):.1f}" stroke="{col}" stroke-width="1.2"/>')
        top, bot = Y(max(o, c)), Y(min(o, c))
        out.append(f'<rect x="{x - bw / 2:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{max(bot - top, 1.2):.1f}" rx="1" fill="{col}"/>')
    # livelli: richiesti (tratteggiati, etichette a destra) e ottenuti (pieni, etichette a sinistra)
    def hline(pr, color, dash, text, side, below=False):
        y = Y(pr)
        da = f' stroke-dasharray="{dash}"' if dash else ""
        s = [f'<path d="M{L} {y:.1f} H{L + pw}" stroke="{color}" stroke-width="1.5"{da}/>']
        if side == "right":
            s.append(label(f"{text} {pr:.5f}", L + pw - 6, y + (15 if below else -5), color, "end"))
        else:
            s.append(label(f"{text} {pr:.5f}", L + 6, y - 5, color, "start"))
        return "".join(s)
    out.append(hline(TP, t["up"], "6 4", "target", "right"))
    out.append(hline(SL, t["down"], "6 4", "stop", "right"))
    out.append(hline(TRAIL, t["trail"], "2 3", "stop moved to", "right", below=True))
    out.append(hline(ENTRY, t["entry"], None, "entry", "left"))
    out.append(hline(TP, t["neutral"], None, "exit", "left").replace(f'stroke="{t["neutral"]}" stroke-width="1.5"', f'stroke="{t["neutral"]}" stroke-width="1" stroke-opacity="0.55"'))
    # marcatori: triangolo su (long) all'entrata, quadrato all'uscita
    xe, ye = X(I_ENTRY), Y(ENTRY)
    out.append(f'<path d="M{xe:.1f} {ye - 7:.1f} L{xe + 6:.1f} {ye + 4:.1f} L{xe - 6:.1f} {ye + 4:.1f} Z" fill="{t["entry"]}" stroke="{t["panel"]}" stroke-width="2"/>')
    xx, yx = X(I_EXIT), Y(TP)
    out.append(f'<rect x="{xx - 5:.1f}" y="{yx - 5:.1f}" width="10" height="10" fill="{t["neutral"]}" stroke="{t["panel"]}" stroke-width="2"/>')
    # asse tempi
    for i in range(2, n, 6):
        hh = (8 + i) % 24
        dd = 15 + (8 + i) // 24
        out.append(label(f"01-{dd:02d} {hh:02d}:00", X(i), H - 10, t["axis"], "middle"))
    return B.svg(W, H, "".join(out))


def equity_chart(mode):
    t = THEMES[mode]
    rnd = random.Random(9)
    W, H = 460, 190
    L, R, T, Bm = 10, 66, 12, 12
    pw, ph = W - L - R, H - T - Bm
    bal = [100000.0]
    for _ in range(22):
        bal.append(bal[-1] + (rnd.choice([1, 1, 1, -1, -1]) * rnd.uniform(180, 520)))
    lo, hi = min(bal) - 300, max(bal) + 300
    X = lambda i: L + pw * i / (len(bal) - 1)
    Y = lambda v: T + (hi - v) / (hi - lo) * ph
    out = [f'<rect width="{W}" height="{H}" fill="{t["panel"]}"/>']
    v = (int(lo / 500) + 1) * 500
    while v < hi:
        out.append(f'<path d="M{L} {Y(v):.1f} H{L + pw}" stroke="{t["grid"]}" stroke-width="1"/>')
        out.append(label(f"{v:,.0f}".replace(",", " "), W - 8, Y(v) + 4, t["axis"], "end"))
        v += 500
    out.append(f'<path d="M{L} {Y(bal[0]):.1f} H{L + pw}" stroke="{t["axis"]}" stroke-width="1" stroke-dasharray="3 3"/>')
    d = f"M{X(0):.1f} {Y(bal[0]):.1f}"
    for i in range(1, len(bal)):
        d += f" H{X(i):.1f} V{Y(bal[i]):.1f}"
    col = t["up"] if bal[-1] >= bal[0] else t["down"]      # colore dal risultato netto, come in app.js
    out.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="1.5" stroke-linejoin="round"/>')
    sel = 15                                              # chiusura del trade selezionato
    out.append(f'<circle cx="{X(sel):.1f}" cy="{Y(bal[sel]):.1f}" r="3.5" fill="{t["entry"]}"/>')
    return B.svg(W, H, "".join(out))


if __name__ == "__main__":
    for mode in THEMES:
        open(f"{OUT}/testata_{mode}.svg", "w").write(header_lockup(mode))
        open(f"{OUT}/grafico-prezzi_{mode}.svg", "w").write(price_chart(mode))
        open(f"{OUT}/grafico-capitale_{mode}.svg", "w").write(equity_chart(mode))
    print("ok")
