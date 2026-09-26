"""Mini libreria per i diagrammi di architettura parity-deriva (SVG autonomi).

Colori e font da loghi/definitivo/tokens.css (tema chiaro); icone Tabler outline
dalla cartella "icone-per diagrammi". I font vengono ridotti ai soli glifi usati e
incorporati come woff2, così l'SVG si vede uguale su qualunque macchina.
"""
import re, io, base64, html
from fontTools.ttLib import TTFont
from fontTools import subset

import os as _os
# Percorsi relativi alla radice del progetto parity-deriva (questo file sta in
# docs/architecture/sorgenti/); si possono cambiare con le
# variabili d'ambiente PD_ICONS e PD_LOGHI.
_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", ".."))
ICONS = _os.environ.get("PD_ICONS", _os.path.join(_ROOT, "icone-per diagrammi", "tabler-outline"))
LOGHI = _os.environ.get("PD_LOGHI", _os.path.join(_ROOT, "loghi", "definitivo"))

C = dict(
    bg="#F6F3EC", panel="#FFFFFF", raised="#FBF9F5", line="#E3DED3", border="#7D8E99",
    text="#152C3A", text2="#4A5B67", text3="#5E6E78",
    ochre="#C98A45", entry="#2A74C7", up="#15855F", down="#C9423A",
    ai="#A8702F", ai_text="#9A5F22", ai_bg="#FBF1E4",
    info_bg="#EAF1FA", error_bg="#FBEAE8", live="#C9423A",
)

FONTS = {
    "sans400": ("IBM Plex Sans", 400, "IBMPlexSans-Regular.woff2"),
    "sans500": ("IBM Plex Sans", 500, "IBMPlexSans-Medium.woff2"),
    "sans600": ("IBM Plex Sans", 600, "IBMPlexSans-SemiBold.woff2"),
    "mono400": ("IBM Plex Mono", 400, "IBMPlexMono-Regular.woff2"),
    "title": ("PD Instrument", 400, "PDInstrument-Regular.woff2"),
}
_tt = {}


def _font(key):
    if key not in _tt:
        f = TTFont(f"{LOGHI}/font/{FONTS[key][2]}")
        _tt[key] = (f, f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm)
    return _tt[key]


def measure(s, size, key="sans400"):
    f, cmap, hmtx, upm = _font(key)
    w = 0
    for ch in s:
        g = cmap.get(ord(ch)) or cmap.get(ord("?"))
        w += hmtx[g][0]
    return w * size / upm


def wrap(s, width, size, key="sans400"):
    words, lines, cur = s.split(" "), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if measure(t, size, key) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def esc(s):
    return html.escape(s, quote=True)


class Diagram:
    def __init__(self, w, h, title, desc):
        self.w, self.h, self.title, self.desc = w, h, title, desc
        self.out = []
        self.icons = set()
        self.used = {k: set() for k in FONTS}

    def add(self, s):
        self.out.append(s)

    # ---------- primitive ----------
    def text(self, x, y, s, size=13, key="sans400", fill=None, anchor="start", ls=None):
        self.used[key].update(s)
        fam, wt, _ = FONTS[key]
        style = f'font-family:\'{fam}\';font-weight:{wt};font-size:{size}px'
        if ls:
            style += f';letter-spacing:{ls}em'
        a = "" if anchor == "start" else f' text-anchor="{anchor}"'
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" style="{style}" fill="{fill or C["text"]}"{a}>{esc(s)}</text>')

    def para(self, x, y, s, width, size=13, key="sans400", fill=None, lh=1.4):
        lines = wrap(s, width, size, key)
        for i, l in enumerate(lines):
            self.text(x, y + i * size * lh, l, size, key, fill)
        return y + len(lines) * size * lh

    def rect(self, x, y, w, h, fill="none", stroke=None, sw=1, r=10, dash=None, extra=""):
        s = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r}" fill="{fill}"{s}{d}{extra}/>')

    def icon(self, name, x, y, size=20, color=None, sw=1.75):
        self.icons.add(name)
        col = color or C["text"]
        self.add(f'<use href="#i-{name}" xlink:href="#i-{name}" x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" '
                 f'style="color:{col};stroke-width:{sw * 24 / size * size / 24:.2f}"/>')

    def pill(self, x, y, s, kind="neutral", size=12, anchor="start"):
        key = "sans600" if kind == "live" else "sans500"
        tw = measure(s, size, key)
        dot = kind == "live"
        w = tw + 20 + (12 if dot else 0)
        h = size + 12
        if anchor == "end":
            x -= w
        if kind == "live":
            self.rect(x, y, w, h, fill=C["live"], r=h / 2)
            self.add(f'<circle cx="{x + 13:.1f}" cy="{y + h / 2:.1f}" r="3.5" fill="#FFFFFF"/>')
            self.text(x + 22, y + h / 2 + size * 0.36, s, size, key, "#FFFFFF")
        else:
            col = {"neutral": C["text2"], "practice": C["entry"], "ai": C["ai_text"]}[kind]
            brd = {"neutral": C["border"], "practice": C["entry"], "ai": C["ai"]}[kind]
            self.rect(x, y, w, h, fill=C["panel"], stroke=brd, sw=1.2, r=h / 2)
            self.text(x + 10, y + h / 2 + size * 0.36, s, size, key, col)
        return w

    # ---------- frecce ----------
    FLOW = {
        "push": dict(color=C["text"], sw=2.6, dash=None, start=False, end=True),
        "mcp": dict(color=C["entry"], sw=2, dash=None, start=True, end=True),
        "mcp1": dict(color=C["entry"], sw=2, dash=None, start=False, end=True),
        "s3": dict(color=C["text3"], sw=1.8, dash="7 5", start=False, end=True),
        "ai": dict(color=C["ai"], sw=3.2, dash=None, start=True, end=True),
        "ai1": dict(color=C["ai"], sw=3.2, dash=None, start=False, end=True),
        "https": dict(color=C["text3"], sw=1.8, dash="2 4", start=False, end=True),
        "s3ro": dict(color=C["text3"], sw=1.8, dash="7 5", start=False, end=True),
    }

    def flow(self, pts, kind):
        f = self.FLOW[kind]
        d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
        m = ""
        if f["end"]:
            m += f' marker-end="url(#ah-{kind})"'
        if f["start"]:
            m += f' marker-start="url(#as-{kind})"'
        dash = f' stroke-dasharray="{f["dash"]}"' if f["dash"] else ""
        cap = "butt" if f["dash"] else "round"
        self.add(f'<path d="{d}" fill="none" stroke="{f["color"]}" stroke-width="{f["sw"]}" '
                 f'stroke-linejoin="round" stroke-linecap="{cap}"{dash}{m}/>')

    def flow_label(self, x, y, lines, kind, anchor="middle", bg=None):
        """Etichetta su fondo pieno: prima riga in grassetto del colore del flusso."""
        col = C["ai_text"] if kind.startswith("ai") else self.FLOW[kind]["color"]
        size0, size1 = 13, 12
        ws = [measure(lines[0], size0, "sans600")] + [measure(l, size1) for l in lines[1:]]
        w = max(ws) + 12
        h = 6 + size0 * 1.3 + (len(lines) - 1) * size1 * 1.35 + 2
        x0 = x - w / 2 if anchor == "middle" else (x if anchor == "start" else x - w)
        self.rect(x0, y - size0 - 3, w, h, fill=bg or C["bg"], r=4)
        tx = x if anchor == "middle" else (x0 + 6 if anchor == "start" else x0 + w - 6)
        self.text(tx, y, lines[0], size0, "sans600", col, anchor)
        for i, l in enumerate(lines[1:]):
            self.text(tx, y + size0 * 1.3 + i * size1 * 1.35, l, size1, "sans400", C["text2"], anchor)

    # ---------- componenti ----------
    def header(self, title, subtitle, scenario):
        logo = open(f"{LOGHI}/parity-deriva_orizzontale_fondo-chiaro.svg").read()
        logo = re.sub(r"<metadata>.*?</metadata>", "", logo, flags=re.S)
        inner = re.search(r"<svg[^>]*>(.*)</svg>", logo, re.S).group(1)
        inner = re.sub(r"<title>.*?</title>", "", inner)
        # il file ha 40 px di margine: ritaglio la viewBox sul logo
        self.add(f'<svg x="28" y="26" width="230" height="64" viewBox="30 36 740 150" aria-hidden="true">{inner}</svg>')
        self.add(f'<line x1="282" y1="30" x2="282" y2="90" stroke="{C["line"]}" stroke-width="1.5"/>')
        self.text(306, 62, title, 36, "title", ls=0.01)
        self.text(307, 88, subtitle, 15, "sans400", C["text2"])
        self.pill(self.w - 32, 44, scenario, "neutral", 12, anchor="end")
        self.add(f'<line x1="28" y1="116" x2="{self.w - 28}" y2="116" stroke="{C["line"]}" stroke-width="1"/>')

    def server(self, x, y, w, h, title, roles, env=None, icon="server-2", dashed=False, where=None):
        stroke = {None: C["border"], "practice": C["entry"], "live": C["live"]}[env]
        sw = 2 if env == "live" else 1.5
        self.rect(x, y, w, h, fill=C["panel"], stroke=stroke, sw=sw, r=12, dash="8 6" if dashed else None)
        self.icon(icon, x + 20, y + 20, 30, C["text"])
        self.text(x + 62, y + 42, title, 24, "title", ls=0.01)
        self.text(x + 62, y + 64, roles, 13, "sans400", C["text2"])
        label = {"practice": "practice", "live": "live · real money"}.get(env, "no orders")
        kind = env or "neutral"
        bw = measure(label, 11, "sans600") + 34
        by = y + 18
        if 62 + measure(title, 24, "title") + 20 + bw + 16 > w:
            by = y + 48  # titolo lungo: il badge scende sulla riga dei ruoli
        self.pill(x + w - 16, by, label, kind, 11, "end")
        if where:
            self.text(x + w - 16, y + 64, where, 12, "mono400", C["text3"], "end")
        self.add(f'<line x1="{x + 16}" y1="{y + 80}" x2="{x + w - 16}" y2="{y + 80}" stroke="{C["line"]}"/>')
        return y + 94

    def module(self, x, y, w, h, icon, title, items=(), sub=None, fill=None, stroke=None, dash=None, isz=13):
        self.rect(x, y, w, h, fill=fill or C["raised"], stroke=stroke or C["line"], sw=2 if stroke == C["ai"] else 1.2, r=8, dash=dash)
        self.icon(icon, x + 12, y + 12, 22, C["text"])
        self.text(x + 42, y + 29, title, 14, "sans600")
        yy = y + 29
        if sub:
            yy += 18
            self.text(x + 42, yy, sub, 12, "sans400", C["text3"])
        yy += 12
        for it in items:
            if isinstance(it, tuple):
                ic, t = it
                self.icon(ic, x + 14, yy + 3, 17, C["text2"], 1.6)
                yy2 = self.para(x + 40, yy + 16, t, w - 60, isz, "sans400", C["text2"], 1.35)
                yy = yy2 - isz * 1.35 + 10 + 4
            else:
                self.add(f'<circle cx="{x + 20}" cy="{yy + 12}" r="2" fill="{C["text3"]}"/>')
                yy2 = self.para(x + 30, yy + 16, it, w - 42, isz, "sans400", C["text2"], 1.35)
                yy = yy2 - isz * 1.35 + 8
        return yy

    def footer_cards(self, y, scenario_idx, notes):
        w = (self.w - 56 - 32) / 3
        h = self.h - y - 52
        xs = [28 + i * (w + 16) for i in range(3)]
        # legenda
        x = xs[0]
        self.rect(x, y, w, h, fill=C["panel"], stroke=C["line"], r=10)
        self.icon("list", x + 18, y + 16, 20)
        self.text(x + 46, y + 32, "legend", 18, "title")
        rows = [
            ("push", "push strategies", "archive → demo / real, only by push"),
            ("ai", "you + AI → MCP", "archive, test tuning, trade data"),
            ("mcp", "MCP", "results, trade status, strategies to test"),
            ("s3", "backup · rclone", "from archive only · S3, Drive, OneDrive…"),
            ("https", "browser · https", "web app, direct upload"),
        ]
        yy = y + 62
        for kind, a, b in rows:
            self.flow([(x + 20, yy), (x + 78, yy)], kind)
            self.text(x + 94, yy + 4.5, a, 13, "sans600", C["ai_text"] if kind == "ai" else None)
            self.text(x + 94 + measure(a, 13, "sans600") + 8, yy + 4.5, b, 12.5, "sans400", C["text2"])
            yy += 26
        # scenari
        x = xs[1]
        self.rect(x, y, w, h, fill=C["panel"], stroke=C["line"], r=10)
        self.icon("stack-2", x + 18, y + 16, 20)
        self.text(x + 46, y + 32, "deployment scenarios", 18, "title")
        sc = [("simple", "archive + test + demo  |  real"),
              ("medium", "archive + test  |  demo  |  real"),
              ("complex", "N independent servers"),
              ("mixed", "archive + demo (cloud) · test (home) · real (cloud)")]
        yy = y + 64
        for i, (a, b) in enumerate(sc):
            cur = (i == scenario_idx)
            cx = x + 32
            if cur:
                self.rect(x + 12, yy - 17, w - 24, 26, fill=C["bg"], r=6)
                self.add(f'<circle cx="{cx}" cy="{yy - 4}" r="10" fill="{C["text"]}"/>')
                self.text(cx, yy + 0.5, str(i + 1), 12, "sans600", C["panel"], "middle")
            else:
                self.add(f'<circle cx="{cx}" cy="{yy - 4}" r="10" fill="none" stroke="{C["border"]}" stroke-width="1.2"/>')
                self.text(cx, yy + 0.5, str(i + 1), 12, "sans500", C["text2"], "middle")
            self.text(x + 52, yy + 0.5, a, 13, "sans600")
            self.text(x + 122, yy + 0.5, b, 12.5, "sans400", C["text2"])
            yy += 30
        # note
        x = xs[2]
        self.rect(x, y, w, h, fill=C["panel"], stroke=C["line"], r=10)
        self.icon("notes", x + 18, y + 16, 20)
        self.text(x + 46, y + 32, "key notes", 18, "title")
        yy = y + 62
        for n in notes:
            self.add(f'<circle cx="{x + 24}" cy="{yy - 4}" r="2.2" fill="{C["text3"]}"/>')
            yy = self.para(x + 36, yy, n, w - 56, 12.5, "sans400", C["text2"], 1.35) + 6
        self.text(28, self.h - 22, "parity-deriva · architecture", 11, "mono400", C["text3"])
        self.text(self.w - 28, self.h - 22, "one application · four roles · build, test, trade", 11, "mono400", C["text3"], "end")

    def ai_client(self, x, y, w, h, compact=False):
        """Il punto d'ingresso evidenziato: una persona che lavora con un assistente IA, client MCP."""
        self.rect(x, y, w, h, fill=C["ai_bg"], stroke=C["ai"], sw=2.2, r=12)
        if compact:
            self.icon("user", x + 16, y + (h - 26) / 2, 26, C["text"])
            self.text(x + 48, y + h / 2 + 5, "→", 14, "sans500", C["ai_text"])
            self.icon("robot", x + 66, y + (h - 28) / 2, 28, C["ai_text"], 1.9)
            self.text(x + 106, y + h / 2 - 3, "you + AI", 20, "title")
            self.text(x + 106, y + h / 2 + 16, "AI assistant as MCP client", 12, "sans400", C["text2"])
            return
        self.icon("user", x + 18, y + 20, 30, C["text"])
        self.text(x + 56, y + 42, "→", 16, "sans500", C["ai_text"])
        self.icon("robot", x + 78, y + 18, 34, C["ai_text"], 1.9)
        self.text(x + 124, y + 42, "you + AI", 24, "title")
        self.pill(x + w - 14, y + 16, "MCP client", "ai", 11, "end")
        self.text(x + 18, y + 76, "one MCP client, three uses: manage strategies and", 12.5, "sans400", C["text2"])
        self.text(x + 18, y + 94, "indicators, tune the tests, read the trades", 12.5, "sans400", C["text2"])

    # ---------- output ----------
    def _fontcss(self):
        css = []
        for k, (fam, wt, fn) in FONTS.items():
            chars = self.used[k]
            if not chars:
                continue
            opts = subset.Options()
            opts.flavor = "woff2"
            opts.layout_features = ["kern", "liga", "zero"]
            opts.name_IDs = ["*"]
            opts.notdef_outline = True
            f = TTFont(f"{LOGHI}/font/{fn}")
            sub = subset.Subsetter(opts)
            sub.populate(text="".join(sorted(chars)) + " ")
            sub.subset(f)
            buf = io.BytesIO()
            f.flavor = "woff2"
            f.save(buf)
            b64 = base64.b64encode(buf.getvalue()).decode()
            css.append(f"@font-face{{font-family:'{fam}';font-weight:{wt};src:url(data:font/woff2;base64,{b64}) format('woff2')}}")
        return "\n".join(css)

    def _defs(self):
        out = []
        for n in sorted(self.icons):
            s = open(f"{ICONS}/{n}.svg").read()
            body = re.search(r"<svg[^>]*>(.*)</svg>", s, re.S).group(1)
            body = re.sub(r"\s+", " ", body).strip()
            out.append(f'<symbol id="i-{n}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                       f'stroke-linecap="round" stroke-linejoin="round">{body}</symbol>')
        for kind, f in self.FLOW.items():
            c = f["color"]
            out.append(f'<marker id="ah-{kind}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="4.2" markerHeight="4.2" '
                       f'markerUnits="strokeWidth" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="{c}"/></marker>')
            out.append(f'<marker id="as-{kind}" viewBox="0 0 10 10" refX="1.5" refY="5" markerWidth="4.2" markerHeight="4.2" '
                       f'markerUnits="strokeWidth" orient="auto"><path d="M10 0 L0 5 L10 10 z" fill="{c}"/></marker>')
        return "\n".join(out)

    def save(self, path):
        body = "\n".join(self.out)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
               f'viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" role="img" aria-labelledby="t d">\n'
               f'<title id="t">{esc(self.title)}</title>\n<desc id="d">{esc(self.desc)}</desc>\n'
               f'<style>\n{self._fontcss()}\ntext{{font-feature-settings:"kern" 1;font-kerning:normal}}\n</style>\n'
               f'<defs>\n{self._defs()}\n</defs>\n'
               f'<rect width="{self.w}" height="{self.h}" fill="{C["bg"]}"/>\n{body}\n</svg>\n')
        open(path, "w").write(svg)
        return len(svg)
