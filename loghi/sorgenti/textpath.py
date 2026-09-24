"""Converte testo in path SVG (niente dipendenza dai font installati)."""
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen

FS = "node_modules/@fontsource"


def fnt(family, weight):
    return Font(f"{FS}/{family}/files/{family}-latin-{weight}-normal.woff")


def ntos(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s != "-0" else "0"


class Font:
    _cache = {}

    def __init__(self, path):
        if path not in Font._cache:
            f = TTFont(path)
            Font._cache[path] = f
        self.f = Font._cache[path]
        self.gs = self.f.getGlyphSet()
        self.cmap = self.f.getBestCmap()
        self.upm = self.f["head"].unitsPerEm
        self.hmtx = self.f["hmtx"]
        os2 = self.f["OS/2"]
        self.cap = getattr(os2, "sCapHeight", 0) or 700

    def cap_height(self, size):
        return self.cap * size / self.upm

    def _glyphs(self, text):
        return [self.cmap[ord(c)] for c in text]

    def width(self, text, size, track=0.0):
        sc = size / self.upm
        gl = self._glyphs(text)
        w = sum(self.hmtx[g][0] for g in gl) * sc
        return w + track * size * (len(gl) - 1)

    def runs(self, text, size, track, x, y, anchor="middle"):
        """Ritorna lista di (char, d) per poter colorare singoli caratteri."""
        sc = size / self.upm
        w = self.width(text, size, track)
        ox = {"start": x, "middle": x - w / 2, "end": x - w}[anchor]
        out = []
        for ch, g in zip(text, self._glyphs(text)):
            pen = SVGPathPen(self.gs, ntos=ntos)
            tp = TransformPen(pen, (sc, 0, 0, -sc, ox, y))
            self.gs[g].draw(tp)
            out.append((ch, pen.getCommands()))
            ox += self.hmtx[g][0] * sc + track * size
        return out, w

    def path(self, text, size, track, x, y, anchor="middle"):
        runs, w = self.runs(text, size, track, x, y, anchor)
        return "".join(d for _, d in runs), w
