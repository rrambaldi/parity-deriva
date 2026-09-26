"""Componenti per i diagrammi di processo (PROCESSO.md): stati, gate, corsie, etichette 'da fare'.

Estende lib.Diagram: stessi font, logo, icone Tabler. Regola del documento:
quello che c'è già ha linea piena, quello da fare linea tratteggiata ed etichetta "da fare".
"""
from lib import Diagram, C, measure, wrap

# colori degli stati (PROCESSO.md, "Grafici per claude.ai"), resi nei toni della palette
ST = {
    "SIM": dict(stroke="#C0661A", fill="#FBEBDD", text="#8A4510", label="SIM"),
    "DEMO": dict(stroke="#4E9F76", fill="#E6F3EC", text="#2F6E4E", label="DEMO"),
    "RAMP": dict(stroke="#15855F", fill="#D3ECE0", text="#0E5E43", label="LIVE ramp 25%"),
    "LIVE": dict(stroke="#0E5E43", fill="#15855F", text="#FFFFFF", label="LIVE 100%"),
    "SUSP": dict(stroke="#B7860B", fill="#FBF1D6", text="#7A5A06", label="SUSPENDED"),
    "DEAD": dict(stroke="#C9423A", fill="#FBEAE8", text="#9B2C25", label="DEAD"),
    "GATE": dict(stroke="#A88B1A", fill="#FFF4C2", text="#5E4E0E", label="gate"),
}
SIM_OR = ST["SIM"]["stroke"]
GREEN = "#15855F"
RED = "#C9423A"
AMBER = ST["SUSP"]["stroke"]


class FD(Diagram):
    FLOW = {
        **Diagram.FLOW,
        "done": dict(color=C["text"], sw=2.2, dash=None, start=False, end=True),
        "todo": dict(color=C["text"], sw=2.2, dash="7 5", start=False, end=True),
        "loop": dict(color=SIM_OR, sw=2.6, dash=None, start=False, end=True),
        "loopd": dict(color=SIM_OR, sw=2.6, dash="7 5", start=False, end=True),
        "ok": dict(color=GREEN, sw=2.6, dash=None, start=False, end=True),
        "okd": dict(color=GREEN, sw=2.6, dash="7 5", start=False, end=True),
        "ko": dict(color=RED, sw=2.6, dash=None, start=False, end=True),
        "kod": dict(color=RED, sw=2.6, dash="7 5", start=False, end=True),
        "susp": dict(color=AMBER, sw=2.6, dash="7 5", start=False, end=True),
        "msg": dict(color=C["text2"], sw=1.8, dash=None, start=False, end=True),
    }

    def path(self, d, kind):
        f = self.FLOW[kind]
        dash = f' stroke-dasharray="{f["dash"]}"' if f["dash"] else ""
        m = f' marker-end="url(#ah-{kind})"' if f["end"] else ""
        self.add(f'<path d="{d}" fill="none" stroke="{f["color"]}" stroke-width="{f["sw"]}" '
                 f'stroke-linecap="round" stroke-linejoin="round"{dash}{m}/>')

    def label(self, x, y, lines, color=None, anchor="middle", bg=None, size=12.5, bold_first=True):
        """Etichetta su fondo pieno, per frecce."""
        col = color or C["text"]
        s0 = size + 0.5
        ws = [measure(lines[0], s0, "sans600" if bold_first else "sans400")] + [measure(l, size) for l in lines[1:]]
        w = max(ws) + 12
        h = 6 + s0 * 1.3 + (len(lines) - 1) * size * 1.35 + 2
        x0 = {"middle": x - w / 2, "start": x, "end": x - w}[anchor]
        self.rect(x0, y - s0 - 3, w, h, fill=bg or C["bg"], r=4)
        tx = {"middle": x, "start": x0 + 6, "end": x0 + w - 6}[anchor]
        self.text(tx, y, lines[0], s0, "sans600" if bold_first else "sans400", col, anchor)
        for i, l in enumerate(lines[1:]):
            self.text(tx, y + s0 * 1.3 + i * size * 1.35, l, size, "sans400", C["text2"], anchor)

    def tag(self, x, y, text="da fare", anchor="start"):
        """Etichetta 'da fare': pillola tratteggiata."""
        w = measure(text, 10.5, "sans600") + 14
        x0 = x - w if anchor == "end" else x
        self.rect(x0, y, w, 17, fill=C["panel"], stroke=SIM_OR, sw=1.1, r=8.5, dash="3 2")
        self.text(x0 + 7, y + 12.3, text, 10.5, "sans600", ST["SIM"]["text"])
        return w

    def ok_tag(self, x, y, text="c'è già", anchor="start"):
        w = measure(text, 10.5, "sans600") + 14
        x0 = x - w if anchor == "end" else x
        self.rect(x0, y, w, 17, fill=GREEN, r=8.5)
        self.text(x0 + 7, y + 12.3, text, 10.5, "sans600", "#FFFFFF")
        return w

    def box(self, x, y, w, h, title, sub=None, state=None, todo=False, icon=None, tsize=15, ssize=12.5,
            center=False, fill=None, stroke=None, sw=1.6):
        s = ST.get(state) if state else None
        fl = fill or (s["fill"] if s else C["panel"])
        stk = stroke or (s["stroke"] if s else C["border"])
        tcol = s["text"] if s and state == "LIVE" else C["text"]
        scol = "#E6F4EC" if state == "LIVE" else C["text2"]
        self.rect(x, y, w, h, fill=fl, stroke=stk, sw=sw, r=10, dash="7 5" if todo else None)
        tx = x + 14
        if icon:
            self.icon(icon, x + 14, y + 13, 22, s["text"] if s else C["text"])
            tx = x + 44
        ty = y + 29
        if center:
            tw = measure(title, tsize, "sans600")
            self.text(x + w / 2, ty, title, tsize, "sans600", tcol, "middle")
        else:
            lines = wrap(title, x + w - tx - (70 if todo else 12), tsize, "sans600")
            for i, l in enumerate(lines):
                self.text(tx, ty + i * tsize * 1.25, l, tsize, "sans600", tcol)
            ty += (len(lines) - 1) * tsize * 1.25
        if todo:
            self.tag(x + w - 10, y + 10, anchor="end")
        if sub:
            yy = ty + 20
            for part in (sub if isinstance(sub, list) else [sub]):
                for l in wrap(part, w - (tx - x) - 12 if not center else w - 24, ssize):
                    if center:
                        self.text(x + w / 2, yy, l, ssize, "sans400", scol, "middle")
                    else:
                        self.text(tx, yy, l, ssize, "sans400", scol)
                    yy += ssize * 1.38
        return y + h

    def diamond(self, cx, cy, w, h, title, sub=None, todo=False):
        s = ST["GATE"]
        pts = f"{cx},{cy - h / 2} {cx + w / 2},{cy} {cx},{cy + h / 2} {cx - w / 2},{cy}"
        dash = ' stroke-dasharray="7 5"' if todo else ""
        self.add(f'<polygon points="{pts}" fill="{s["fill"]}" stroke="{s["stroke"]}" stroke-width="1.8" stroke-linejoin="round"{dash}/>')
        lines = title if isinstance(title, list) else [title]
        y0 = cy - (len(lines) - 1) * 8 + 5
        for i, l in enumerate(lines):
            self.text(cx, y0 + i * 16, l, 13.5, "sans600", s["text"], "middle")
        if sub:
            self.text(cx, cy + h / 2 + 18, sub, 12, "sans400", C["text2"], "middle")

    def state_chip(self, x, y, key, text=None):
        s = ST[key]
        t = text or s["label"]
        w = measure(t, 11.5, "sans600") + 18
        self.rect(x, y, w, 20, fill=s["fill"], stroke=s["stroke"], sw=1.4, r=6)
        self.text(x + 9, y + 14, t, 11.5, "sans600", s["text"])
        return w

    def legend_bar(self, y, extra_states=True, note=None):
        self.add(f'<line x1="28" y1="{y - 24}" x2="{self.w - 28}" y2="{y - 24}" stroke="{C["line"]}"/>')
        x = 28
        self.flow([(x, y), (x + 48, y)], "done")
        self.text(x + 58, y + 4.5, "c'è già", 13, "sans600")
        x += 58 + measure("c'è già", 13, "sans600") + 26
        self.flow([(x, y), (x + 48, y)], "todo")
        self.text(x + 58, y + 4.5, "da fare", 13, "sans600")
        x += 58 + measure("da fare", 13, "sans600") + 10
        x += self.tag(x, y - 8) + 30
        if extra_states:
            for k in ("SIM", "DEMO", "RAMP", "LIVE", "SUSP", "DEAD"):
                x += self.state_chip(x, y - 10, k) + 8
            x += 16
            s = ST["GATE"]
            self.add(f'<polygon points="{x + 10},{y - 10} {x + 20},{y} {x + 10},{y + 10} {x},{y}" fill="{s["fill"]}" stroke="{s["stroke"]}" stroke-width="1.4"/>')
            self.text(x + 28, y + 4.5, "gate", 13, "sans600")
        if note:
            self.text(self.w - 28, y + 4.5, note, 12, "mono400", C["text3"], "end")
        self.text(28, self.h - 20, "parity-deriva · processo: da un'idea a un trade live", 11, "mono400", C["text3"])
        self.text(self.w - 28, self.h - 20, "soglie proposte, da tarare in etc/settings.py", 11, "mono400", C["text3"], "end")

    def lane(self, x, y, w, h, title, sub, icon, stroke=None):
        self.rect(x, y, w, h, fill="#EFEBE2", stroke=stroke or C["line"], sw=1.4 if stroke else 1.2, r=12)
        self.rect(x, y, 150, h, fill="#E7E2D6", r=12)
        self.icon(icon, x + 16, y + 16, 24, C["text2"])
        self.text(x + 16, y + 64, title, 19, "title")
        yy = y + 84
        for l in wrap(sub, 124, 11.5):
            self.text(x + 16, yy, l, 11.5, "sans400", C["text3"])
            yy += 15
