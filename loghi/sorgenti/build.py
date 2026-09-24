"""Genera le proposte di logo parity-deriva in SVG (testo convertito in tracciati)."""
import math
import os
import random
from textpath import fnt

OUT = "out"
os.makedirs(OUT, exist_ok=True)

# Palette ricavata dai 15 riferimenti
INK = "#152C3A"      # petrolio/navy profondo (rif. 1, 2, 3, 6)
TEAL = "#2E6E7E"     # ottanio medio (rif. 5, 7, 13)
GOLD = "#E6BE5A"     # oro caldo (rif. 1, 2, 10)
OCHRE = "#C98A45"    # ocra (rif. 11)
IVORY = "#F4EFE4"    # avorio per fondi scuri
GREY = "#7C8A92"     # grigio tagline (rif. 3)
FOREST = "#0F2E2B"   # verde petrolio profondo (rif. 8, 9, 14)

NAME_L, NAME_R = "PARITY", "DERIVA"
TAG = "STRATEGIE DI VALORE"


# ---------------------------------------------------------------- marchi
def mark_deriva(c):
    """A — Parità in deriva: il segno '=' la cui linea superiore si stacca e sale."""
    return f'''
  <path d="M18 86 H100" stroke="{c['base']}" stroke-width="13" stroke-linecap="round" fill="none"/>
  <path d="M18 61 H50 C68 61 80 54 93 34" stroke="{c['accent']}" stroke-width="13" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  <circle cx="103" cy="19.5" r="8" fill="{c['accent']}"/>'''


def _band(x0, y1, y2, cx, cy, r):
    def xr(y):
        return cx + math.sqrt(max(r * r - (y - cy) ** 2, 0))
    a, b = xr(y1), xr(y2)
    return (f"M{x0} {y1} H{a:.2f} A{r} {r} 0 0 1 {b:.2f} {y2} H{x0} Z")


def mark_monogram(c):
    """B — Monogramma P: asta + pancia fatta di 4 bande (pila di monete / barre)."""
    cx, cy, r = 60, 45, 31
    x0 = 46
    bands = []
    y = 14
    for i in range(4):
        bands.append(_band(x0, y, y + 12.5, cx, cy, r))
        y += 12.5 + 3.67
    d = " ".join(bands)
    return f'''
  <rect x="24" y="14" width="17" height="92" fill="{c['base']}"/>
  <path d="{d}" fill="{c['accent']}"/>'''


def mark_germoglio(c):
    """C — Germoglio: pila di monete da cui sale uno stelo con due foglie."""
    s = c['base']
    a = c['accent']
    return f'''
  <g fill="none" stroke="{s}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">
    <ellipse cx="60" cy="70" rx="24" ry="6.5"/>
    <path d="M36 70 V94 A24 6.5 0 0 0 84 94 V70"/>
    <path d="M36 78 A24 6.5 0 0 0 84 78"/>
    <path d="M36 86 A24 6.5 0 0 0 84 86"/>
    <path d="M60 68 C60 56 58 48 61 38"/>
    <path d="M59.5 52 C50 52 41 47 37 36 C48 34 57 40 59.5 52 Z"/>
  </g>
  <path d="M61 41 C63 26 74 17 90 15 C89 31 78 40 61 41 Z" fill="{a}" stroke="{a}" stroke-width="4" stroke-linejoin="round"/>'''


def mark_orizzonte(c):
    """D — Orizzonte: moneta-sole, linea di parità e cammino con deriva."""
    rnd = random.Random(107)
    n = 12
    x0, x1, y0, y1 = 14, 102, 90, 30
    w = [0.0]
    for _ in range(n - 1):
        w.append(w[-1] + rnd.gauss(0, 5.5))
    pts = []
    for i in range(n):
        t = i / (n - 1)
        noise = w[i] - t * w[-1]          # ponte: rumore nullo agli estremi
        pts.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0) + noise))
    d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
    ex, ey = pts[-1]
    return f'''
  <circle cx="60" cy="58" r="40" fill="{c['accent']}"/>
  <path d="M8 72 H112" stroke="{c['base']}" stroke-width="2.5" stroke-linecap="round"/>
  <path d="{d}" stroke="{c['base']}" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  <circle cx="{ex:.1f}" cy="{ey:.1f}" r="5.5" fill="{c['base']}"/>'''


# ---------------------------------------------------------------- scritte
def wordmark(font, size, track, x, y, color, hyphen_color, anchor="middle"):
    runs, w = font.runs(f"{NAME_L}-{NAME_R}", size, track, x, y, anchor)
    main = "".join(d for ch, d in runs if ch != "-")
    hy = "".join(d for ch, d in runs if ch == "-")
    return (f'<path d="{main}" fill="{color}"/>'
            f'<path d="{hy}" fill="{hyphen_color}"/>'), w


CONCEPTS = {
    "A-deriva": dict(
        mark=mark_deriva, vb=(13, 93),
        name=("jost", 500, 0.30), tag=("jost", 400, 0.46),
        light=dict(bg="#FFFFFF", base=INK, accent=GOLD, text=INK, hy=GOLD, tag=TEAL),
        dark=dict(bg=INK, base=IVORY, accent=GOLD, text=IVORY, hy=GOLD, tag="#9FC0C8"),
        frame=False,
    ),
    "B-monogramma": dict(
        mark=mark_monogram, vb=(14, 106),
        name=("cormorant-garamond", 600, 0.20), tag=("jost", 400, 0.46),
        light=dict(bg="#FFFFFF", base=INK, accent=GOLD, text=INK, hy=GOLD, tag=GREY),
        dark=dict(bg=INK, base=IVORY, accent=GOLD, text=IVORY, hy=GOLD, tag="#C9B98F"),
        frame=False,
    ),
    "C-germoglio": dict(
        mark=mark_germoglio, vb=(13, 103),
        name=("jost", 400, 0.32), tag=("jost", 600, 0.40),
        light=dict(bg="#FFFFFF", base=TEAL, accent=GOLD, text=INK, hy=GOLD, tag=TEAL, frame=INK),
        dark=dict(bg=FOREST, base="#CFE0DC", accent=GOLD, text=IVORY, hy=GOLD, tag="#9CC2BB", frame="#CFE0DC"),
        frame=True,
    ),
    "D-orizzonte": dict(
        mark=mark_orizzonte, vb=(18, 98),
        name=("marcellus", 400, 0.18), tag=("jost", 400, 0.46),
        light=dict(bg="#FFFFFF", base=INK, accent=OCHRE, text=INK, hy=OCHRE, tag=GREY),
        dark=dict(bg="#0B1A26", base=IVORY, accent=OCHRE, text=IVORY, hy=OCHRE, tag="#C7AA9B"),
        frame=False,
    ),
}


def svg(w, h, body, bg=None, title="parity-deriva"):
    bgr = f'<rect width="{w:.0f}" height="{h:.0f}" fill="{bg}"/>' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" '
            f'width="{w:.0f}" height="{h:.0f}" role="img" aria-label="{title}">'
            f'<title>{title}</title>{bgr}{body}</svg>\n')


def build(key, cfg, mode, with_bg):
    c = cfg[mode]
    nf = fnt(cfg["name"][0], cfg["name"][1])
    tf = fnt(cfg["tag"][0], cfg["tag"][1])
    out = {}

    # --- marchio da solo
    m = cfg["mark"](c)
    out["mark"] = svg(120, 120, m, c["bg"] if with_bg else None)

    # --- verticale (marchio sopra, nome, tagline)
    NS, TS = 64, cfg.get("ts", 20)
    ncap = nf.cap_height(NS)
    nw = nf.width("PARITY-DERIVA", NS, cfg["name"][2])
    tw = tf.width(TAG, TS, cfg["tag"][2])
    pad = 70
    W = max(nw, tw) + pad * 2
    msize = 230
    v0, v1 = cfg["vb"]
    k = msize / 120
    top = 64
    my = top - v0 * k                    # il contenuto del marchio parte da `top`
    mark_bottom = top + (v1 - v0) * k
    ny = mark_bottom + 64 + ncap
    ty = ny + 34 + tf.cap_height(TS)
    H = ty + 64
    body = f'<g transform="translate({(W - msize) / 2:.1f} {my}) scale({msize / 120:.4f})">{m}</g>'
    wm, _ = wordmark(nf, NS, cfg["name"][2], W / 2, ny, c["text"], c["hy"])
    tg, _ = tf.path(TAG, TS, cfg["tag"][2], W / 2, ty)
    body += wm + f'<path d="{tg}" fill="{c["tag"]}"/>'
    if cfg.get("frame"):
        # cornice con apertura in alto dove "esce" il germoglio (rif. 15, 5, 7)
        fx0, fy0 = 26, top + (v1 - v0) * k * 0.55
        fx1, fy1 = W - 26, H - 22
        gap = msize * 0.5
        fc = c["frame"]
        body += (f'<path d="M{W / 2 - gap:.1f} {fy0:.1f} H{fx0} V{fy1:.1f} H{fx1} V{fy0:.1f} H{W / 2 + gap:.1f}" '
                 f'fill="none" stroke="{fc}" stroke-width="3"/>')
        H += 0
    out["stacked"] = svg(W, H, body, c["bg"] if with_bg else None)

    # --- orizzontale (marchio a sinistra, testo a destra)
    NS2, TS2 = 58, cfg.get("ts2", 17)
    ncap2 = nf.cap_height(NS2)
    nw2 = nf.width("PARITY-DERIVA", NS2, cfg["name"][2])
    tw2 = tf.width(TAG, TS2, cfg["tag"][2])
    ms = 150
    gapx = 44
    padx, pady = 40, 36
    H2 = ms + pady * 2
    tx = padx + ms + gapx
    block = ncap2 + 26 + tf.cap_height(TS2)
    ny2 = (H2 - block) / 2 + ncap2
    ty2 = ny2 + 26 + tf.cap_height(TS2)
    W2 = tx + max(nw2, tw2) + padx
    k2 = ms / 120
    my2 = H2 / 2 - ((v0 + v1) / 2) * k2
    body2 = f'<g transform="translate({padx} {my2:.1f}) scale({k2:.4f})">{m}</g>'
    wm2, _ = wordmark(nf, NS2, cfg["name"][2], tx, ny2, c["text"], c["hy"], anchor="start")
    tg2, _ = tf.path(TAG, TS2, cfg["tag"][2], tx, ty2, anchor="start")
    body2 += wm2 + f'<path d="{tg2}" fill="{c["tag"]}"/>'
    out["horizontal"] = svg(W2, H2, body2, c["bg"] if with_bg else None)

    # --- icona quadrata
    cy_mark = (v0 + v1) / 2
    ic = (f'<rect width="120" height="120" rx="26" fill="{c["bg"]}"/>'
          f'<g transform="translate(60 60) scale(0.74) translate(-60 {-cy_mark:.1f})">{m}</g>')
    out["icon"] = svg(120, 120, ic, None)
    return out


if __name__ == "__main__":
    for key, cfg in CONCEPTS.items():
        for mode in ("light", "dark"):
            for with_bg in (True, False):
                res = build(key, cfg, mode, with_bg)
                suffix = "" if with_bg else "-trasparente"
                for kind, s in res.items():
                    fn = f"{OUT}/{key}_{kind}_{mode}{suffix}.svg"
                    with open(fn, "w") as fh:
                        fh.write(s)
    print("ok", len(os.listdir(OUT)))
