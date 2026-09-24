"""Sviluppo della direzione D · Orizzonte: tre varianti del marchio + declinazioni."""
import math
import os
import random
import build as B

OUT = "out_d"
os.makedirs(OUT, exist_ok=True)

INK, IVORY, NIGHT, OCHRE = B.INK, B.IVORY, "#0B1A26", B.OCHRE

CX, CY, R, HY = 60, 58, 40, 72
X_IN = CX - math.sqrt(R * R - (HY - CY) ** 2)      # orizzonte ∩ bordo sinistro della moneta
X_OUT = CX + math.sqrt(R * R - (HY - CY) ** 2)


def bridge(seed, n, x0, x1, y0, y1, sd):
    """Cammino casuale con deriva: ponte browniano fra due punti fissati."""
    rnd = random.Random(seed)
    w = [0.0]
    for _ in range(n - 1):
        w.append(w[-1] + rnd.gauss(0, sd))
    return [(x0 + i / (n - 1) * (x1 - x0), y0 + i / (n - 1) * (y1 - y0) + w[i] - i / (n - 1) * w[-1])
            for i in range(n)]


def d_of(pts):
    return "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in pts)


# cammino di D1: parte esattamente dall'orizzonte sul bordo della moneta ed esce in alto a destra
WALK1 = bridge(129, 9, X_IN, 101, HY, 22, 6.0)


def mark_classico(c):
    ex, ey = WALK1[-1]
    return f'''
  <circle cx="{CX}" cy="{CY}" r="{R}" fill="{c['accent']}"/>
  <path d="M10 {HY} H110" stroke="{c['base']}" stroke-width="3" stroke-linecap="round"/>
  <path d="{d_of(WALK1)}" stroke="{c['base']}" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  <circle cx="{ex:.2f}" cy="{ey:.2f}" r="6.5" fill="{c['base']}"/>'''


# cammino di D2: resta ben dentro la moneta (è inciso), con margine dal bordo
_sx = (80 - 29) / (101 - X_IN)
_sy = (HY - 37) / (HY - 22)
WALK2 = [(29 + (x - X_IN) * _sx, HY + (y - HY) * _sy) for x, y in WALK1]   # stessa forma di D1, ridotta


def mark_incisa(c):
    mid = f"k{c.get('uid', '')}"
    ex, ey = WALK2[-1]
    cut = 5
    return f'''
  <defs><mask id="{mid}" maskUnits="userSpaceOnUse" x="0" y="0" width="120" height="120">
    <rect width="120" height="120" fill="#fff"/>
    <path d="M0 {HY} H120" stroke="#000" stroke-width="{cut}"/>
    <path d="{d_of(WALK2)}" stroke="#000" stroke-width="6.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
    <circle cx="{ex:.2f}" cy="{ey:.2f}" r="7.5" fill="#000"/>
  </mask></defs>
  <circle cx="{CX}" cy="{CY}" r="{R}" fill="{c['accent']}" mask="url(#{mid})"/>
  <path d="M8 {HY} H{X_IN - 6.5:.2f} M{X_OUT + 6.5:.2f} {HY} H112" stroke="{c['accent']}" stroke-width="{cut}" stroke-linecap="round"/>'''


def mark_alba(c):
    cx, cy, r, hy = 60, 60, 38, 70
    hw = math.sqrt(r * r - (hy - cy) ** 2)
    seg = f"M{cx - hw:.2f} {hy} A{r} {r} 0 1 1 {cx + hw:.2f} {hy} Z"
    bands = ""
    for y1, y2 in ((75.5, 80.5), (85, 89), (93, 96)):
        ym = (y1 + y2) / 2
        w = math.sqrt(r * r - (ym - cy) ** 2)
        h = y2 - y1
        bands += f'<rect x="{cx - w:.2f}" y="{y1}" width="{2 * w:.2f}" height="{h}" rx="{h / 2}" fill="{c["accent"]}"/>'
    return f'''
  <path d="{seg}" fill="{c['accent']}"/>
  {bands}
  <path d="M8 {hy} H112" stroke="{c['base']}" stroke-width="3" stroke-linecap="round"/>'''


NAME = ("marcellus", 400, 0.18)
TAGF = ("jost", 400, 0.46)


def modes(mono):
    m = dict(
        light=dict(bg="#FFFFFF", base=INK, accent=OCHRE, text=INK, hy=OCHRE, tag="#6B7A83", uid="L"),
        dark=dict(bg=NIGHT, base=IVORY, accent=OCHRE, text=IVORY, hy=OCHRE, tag="#C7AA9B", uid="D"),
    )
    if mono:
        m["mono_light"] = dict(bg="#FFFFFF", base=INK, accent=INK, text=INK, hy=INK, tag=INK, uid="ML")
        m["mono_dark"] = dict(bg=NIGHT, base=IVORY, accent=IVORY, text=IVORY, hy=IVORY, tag=IVORY, uid="MD")
    return m


VARIANTS = {
    "D1-classico": dict(mark=mark_classico, vb=(15.5, 98), mono=False),
    "D2-incisa": dict(mark=mark_incisa, vb=(18, 98), mono=True),
    "D3-alba": dict(mark=mark_alba, vb=(22, 96), mono=True),
}

if __name__ == "__main__":
    n = 0
    for key, v in VARIANTS.items():
        md = modes(v["mono"])
        cfg = dict(mark=v["mark"], vb=v["vb"], name=NAME, tag=TAGF, frame=False, ts=22, ts2=21, **md)
        for mode in md:
            for with_bg in (True, False):
                res = B.build(key, cfg, mode, with_bg)
                suffix = "" if with_bg else "-trasparente"
                for kind, s in res.items():
                    with open(f"{OUT}/{key}_{kind}_{mode}{suffix}.svg", "w") as fh:
                        fh.write(s)
                    n += 1
    print("ok", n)


# ---------------------------------------------------------------- favicon semplificata di D2
def favicon(c, uid):
    """Per 16-32 px: stessa idea di D2 con tre soli segmenti e tagli più spessi."""
    pts = [(30, HY), (47, 50), (58, 60), (82, 34)]
    return f'''<rect width="120" height="120" rx="26" fill="{c['bg']}"/>
  <defs><mask id="f{uid}" maskUnits="userSpaceOnUse" x="0" y="0" width="120" height="120">
    <rect width="120" height="120" fill="#fff"/>
    <path d="M0 {HY} H120" stroke="#000" stroke-width="9"/>
    <path d="{d_of(pts)}" stroke="#000" stroke-width="11" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  </mask></defs>
  <circle cx="{CX}" cy="{CY + 2}" r="46" fill="{c['accent']}" mask="url(#f{uid})"/>'''


if __name__ == "__main__":
    for mode, c in modes(True).items():
        if mode in ("light", "dark"):
            with open(f"{OUT}/D2-incisa_favicon_{mode}.svg", "w") as fh:
                fh.write(B.svg(120, 120, favicon(c, c["uid"])))
    print("favicon ok")
