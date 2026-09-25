"""Mix Alba × Classico: sole a metà sull'orizzonte con riflesso (Alba) + cammino con deriva (Classico)."""
import math
import os
import build as B
from build_d import bridge, d_of, modes, NAME, TAGF

OUT = "out_mix"
os.makedirs(OUT, exist_ok=True)

CX, CY, R, HY = 60, 60, 38, 70
HW = math.sqrt(R * R - (HY - CY) ** 2)
XL, XR = CX - HW, CX + HW                      # orizzonte ∩ sole


def sun(fill, extra=""):
    return f'<path d="M{XL:.2f} {HY} A{R} {R} 0 1 1 {XR:.2f} {HY} Z" fill="{fill}"{extra}/>'


def bands(fill):
    out = ""
    for y1, y2 in ((75.5, 80.5), (85, 89), (93, 96)):
        ym = (y1 + y2) / 2
        w = math.sqrt(R * R - (ym - CY) ** 2)
        h = y2 - y1
        out += f'<rect x="{CX - w:.2f}" y="{y1}" width="{2 * w:.2f}" height="{h}" rx="{h / 2}" fill="{fill}"/>'
    return out


def horizon(color, w=3):
    return f'<path d="M8 {HY} H112" stroke="{color}" stroke-width="{w}" stroke-linecap="round"/>'


def walk_mapped(x0, x1, y0, y1):
    """Stessa forma del cammino di D1 (seme 129), rimappata in un riquadro."""
    base = bridge(129, 9, 0, 1, 0, 1, 6.0 / 50)   # forma normalizzata (y: 0 → 1 = salita)
    base = [(t, 2 * t - v) for t, v in base]      # stesso verso del rumore di D1 (asse y SVG invertito)
    return [(x0 + t * (x1 - x0), y0 + v * (y1 - y0)) for t, v in base]


# M1 — il cammino parte dall'orizzonte sul bordo del sole ed esce in alto a destra (come D1)
WALK_M1 = walk_mapped(XL, 101, HY, 18)
# M2 — il cammino è inciso nel sole, con margine dal bordo
WALK_M2 = walk_mapped(30, 76, HY, 40)
# M3 — il cammino va da bordo a bordo del sole: parte dall'orizzonte, il punto finale sta sul contorno
_ang = math.radians(42)
_END3 = (CX + R * math.cos(_ang), CY - R * math.sin(_ang))
WALK_M3 = walk_mapped(XL, _END3[0], HY, _END3[1])


def mark_m1(c):
    ex, ey = WALK_M1[-1]
    return f'''
  {sun(c['accent'])}
  {bands(c['accent'])}
  {horizon(c['base'])}
  <path d="{d_of(WALK_M1)}" stroke="{c['base']}" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  <circle cx="{ex:.2f}" cy="{ey:.2f}" r="6.5" fill="{c['base']}"/>'''


def mark_m2(c):
    mid = f"m{c.get('uid', '')}"
    ex, ey = WALK_M2[-1]
    return f'''
  <defs><mask id="{mid}" maskUnits="userSpaceOnUse" x="0" y="0" width="120" height="120">
    <rect width="120" height="120" fill="#fff"/>
    <path d="{d_of(WALK_M2)}" stroke="#000" stroke-width="6.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
    <circle cx="{ex:.2f}" cy="{ey:.2f}" r="7.5" fill="#000"/>
  </mask></defs>
  {sun(c['accent'], f' mask="url(#{mid})"')}
  {bands(c['accent'])}
  {horizon(c['base'])}'''


def mark_m3(c):
    ex, ey = WALK_M3[-1]
    return f'''
  {sun(c['accent'])}
  {bands(c['accent'])}
  {horizon(c['base'])}
  <path d="{d_of(WALK_M3)}" stroke="{c['base']}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  <circle cx="{ex:.2f}" cy="{ey:.2f}" r="6" fill="{c['base']}"/>'''


def favicon(c, uid):
    """Per 16-32 px: sole inciso con tre segmenti, orizzonte, una sola banda di riflesso."""
    pts = [(31, 70), (47, 50), (58, 59), (80, 36)]
    r, cy, hy = 46, 64, 74
    hw = math.sqrt(r * r - (hy - cy) ** 2)
    return f'''<rect width="120" height="120" rx="26" fill="{c['bg']}"/>
  <defs><mask id="f{uid}" maskUnits="userSpaceOnUse" x="0" y="0" width="120" height="120">
    <rect width="120" height="120" fill="#fff"/>
    <path d="{d_of(pts)}" stroke="#000" stroke-width="11" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  </mask></defs>
  <path d="M{60 - hw:.2f} {hy} A{r} {r} 0 1 1 {60 + hw:.2f} {hy} Z" fill="{c['accent']}" mask="url(#f{uid})"/>
  <path d="M10 {hy + 1} H110" stroke="{c['base']}" stroke-width="7" stroke-linecap="round"/>
  <rect x="26" y="88" width="68" height="9" rx="4.5" fill="{c['accent']}"/>'''


VARIANTS = {
    "M1-alba-deriva": dict(mark=mark_m1, vb=(11.5, 96), mono=False),
    "M2-alba-incisa": dict(mark=mark_m2, vb=(22, 96), mono=True),
    "M3-alba-inscritta": dict(mark=mark_m3, vb=(22, 96), mono=False),
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
    for mode, c in modes(False).items():
        with open(f"{OUT}/M-favicon_{mode}.svg", "w") as fh:
            fh.write(B.svg(120, 120, favicon(c, c["uid"])))
    print("ok", n)
