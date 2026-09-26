"""Versioni overview (livello alto) delle quattro tavole di architettura.

Stessi colori, icone e flussi delle tavole di dettaglio, ma ogni server è un nodo
solo: nome, ruoli, badge d'ambiente. Niente moduli interni, legenda su una riga.
"""
from lib import Diagram, C, measure

W, H = 1600, 860
PROV = ["bucket", "brand-google-drive", "brand-onedrive", "brand-dropbox"]


def node(d, x, y, w, h, title, sub, env=None, icon="server-2", where=None):
    stroke = {None: C["border"], "practice": C["entry"], "live": C["live"]}[env]
    d.rect(x, y, w, h, fill=C["panel"], stroke=stroke, sw=2 if env == "live" else 1.6, r=14)
    d.icon(icon, x + 20, y + 22, 32)
    d.text(x + 64, y + 46, title, 24, "title", ls=0.01)
    d.text(x + 64, y + 68, sub, 13, "sans400", C["text2"])
    label = {"practice": "practice", "live": "live · real money"}.get(env, "no orders")
    d.pill(x + 64, y + h - 38, label, env or "neutral", 11)
    if where:
        d.text(x + w - 18, y + h - 20, where, 12, "mono400", C["text3"], "end")


def ai_node(d, x, y, w, h):
    d.rect(x, y, w, h, fill=C["ai_bg"], stroke=C["ai"], sw=2.4, r=14)
    d.icon("user", x + 20, y + 24, 30)
    d.text(x + 56, y + 46, "→", 16, "sans500", C["ai_text"])
    d.icon("robot", x + 78, y + 20, 36, C["ai_text"], 1.9)
    d.text(x + 126, y + 46, "you + AI", 24, "title")
    d.text(x + 126, y + 68, "AI assistant as MCP client", 13, "sans400", C["text2"])


def storage_node(d, x, y, w, h):
    d.rect(x, y, w, h, fill=C["panel"], stroke=C["border"], sw=1.6, r=14)
    d.icon("folders", x + 20, y + 20, 30)
    d.text(x + 62, y + 42, "remote storage", 21, "title")
    d.text(x + 62, y + 62, "backup via rclone", 12, "mono400", C["text3"])
    xx = x + 62
    for ic in PROV:
        d.icon(ic, xx, y + h - 34, 20, C["text2"], 1.6)
        xx += 30
    d.text(xx + 2, y + h - 18, "+ any", 12, "sans400", C["text2"])


def region(d, x, y, w, h, icon, title, sub):
    d.rect(x, y, w, h, fill="#EFEBE2", stroke=C["line"], sw=1.2, r=16)
    d.icon(icon, x + 18, y + 14, 26, C["text2"])
    d.text(x + 52, y + 35, title, 20, "title")
    d.text(x + 52 + measure(title, 20, "title") + 12, y + 34, sub, 12, "mono400", C["text3"])


def legend_strip(d, y):
    d.add(f'<line x1="28" y1="{y - 26}" x2="{W - 28}" y2="{y - 26}" stroke="{C["line"]}"/>')
    rows = [("push", "push strategies", "archive → demo / real only"),
            ("ai", "you + AI → MCP", "archive · test · trade data"),
            ("mcp", "MCP", "results, trade status"),
            ("s3", "backup", "rclone, archive only")]
    x = 28
    for kind, a, b in rows:
        d.flow([(x, y), (x + 50, y)], kind)
        d.text(x + 62, y + 4.5, a, 13, "sans600", C["ai_text"] if kind == "ai" else None)
        bx = x + 62 + measure(a, 13, "sans600") + 8
        d.text(bx, y + 4.5, b, 12.5, "sans400", C["text2"])
        x = bx + measure(b, 12.5) + 44
    d.text(W - 28, y + 4.5, "overview · details in the full diagrams", 12, "mono400", C["text3"], "end")
    d.text(28, H - 22, "parity-deriva · architecture overview", 11, "mono400", C["text3"])
    d.text(W - 28, H - 22, "one application · four roles · build, test, trade", 11, "mono400", C["text3"], "end")


LY = 790  # riga della legenda


# ------------------------------------------------------------------ 1
def platform():
    d = Diagram(W, H, "parity-deriva · platform overview",
                "High-level view: archive at the centre, test servers on the left, demo and real trading on the right, "
                "you + AI reaching archive, test and trading through MCP, backup via rclone.")
    d.header("platform · overview", "one application, four roles · web app on every server", "general model")
    ai_node(d, 620, 146, 360, 100)
    node(d, 640, 330, 320, 130, "archive", "archivist · always present", icon="database")
    node(d, 160, 330, 320, 130, "test", "tester · one or more", icon="flask")
    d.rect(1080, 210, 490, 460, fill="#EFEBE2", stroke=C["line"], sw=1.2, r=16)
    d.icon("chart-candle", 1098, 224, 26, C["text2"])
    d.text(1132, 245, "trading servers", 20, "title")
    d.text(1132 + measure("trading servers", 20, "title") + 12, 244, "demo and real always apart", 12, "mono400", C["text3"])
    node(d, 1110, 270, 430, 130, "trade demo", "trader demo · one or more", "practice", icon="chart-candle")
    node(d, 1110, 510, 430, 130, "trade real", "trader real · one or more", "live", icon="cash")
    storage_node(d, 650, 600, 300, 100)

    d.flow([(800, 246), (800, 330)], "ai")
    d.flow_label(812, 292, ["strategies, indicators"], "ai", "start")
    d.flow([(620, 196), (320, 196), (320, 330)], "ai")
    d.flow_label(470, 201, ["tuning"], "ai")
    d.flow([(980, 180), (1325, 180), (1325, 210)], "ai")
    d.flow_label(1150, 185, ["trade data · read-only"], "ai")
    d.flow([(480, 395), (640, 395)], "mcp")
    d.flow_label(560, 424, ["MCP", "strategies, results"], "mcp")
    d.flow([(960, 370), (1080, 370)], "push")
    d.flow_label(1020, 353, ["push"], "push")
    d.flow([(960, 425), (1080, 425)], "mcp")
    d.flow_label(1020, 454, ["MCP", "results,", "trade status"], "mcp")
    d.flow([(800, 460), (800, 600)], "s3")
    d.flow_label(812, 536, ["backup"], "s3", "start")
    legend_strip(d, LY)
    return d


# ------------------------------------------------------------------ 2
def simple():
    d = Diagram(W, H, "parity-deriva · simple deployment overview",
                "Two servers: archive + test + demo, and real. You + AI use MCP on both.")
    d.header("simple · 2 servers", "archive, test and demo together; real trading on its own", "scenario 1 of 4")
    ai_node(d, 80, 320, 360, 110)
    node(d, 580, 310, 400, 130, "server 1", "archive + test + demo", "practice")
    node(d, 1180, 310, 340, 130, "server 2", "real trading only", "live")
    storage_node(d, 630, 600, 300, 100)

    d.flow([(440, 375), (580, 375)], "ai")
    d.flow_label(510, 356, ["MCP"], "ai")
    d.flow_label(510, 408, ["strategies,", "tuning,", "demo trades"], "ai")
    d.flow([(260, 320), (260, 220), (1350, 220), (1350, 310)], "ai1")
    d.flow_label(805, 225, ["you + AI → MCP · real trade data, read-only"], "ai")
    d.flow([(980, 350), (1180, 350)], "push")
    d.flow_label(1080, 333, ["push strategies"], "push")
    d.flow([(980, 405), (1180, 405)], "mcp")
    d.flow_label(1080, 434, ["MCP", "results, trade status"], "mcp")
    d.flow([(780, 440), (780, 600)], "s3")
    d.flow_label(792, 526, ["backup"], "s3", "start")
    legend_strip(d, LY)
    return d


# ------------------------------------------------------------------ 3
def medium():
    d = Diagram(W, H, "parity-deriva · medium deployment overview",
                "Three servers: archive + test, demo, real. The archive pushes strategies to both; you + AI use MCP on all three.")
    d.header("medium · 3 servers", "archive and test together; demo and real each on their own", "scenario 2 of 4")
    node(d, 100, 330, 340, 130, "server 1", "archive + test", icon="database")
    node(d, 630, 330, 340, 130, "server 2", "demo trading", "practice", icon="chart-candle")
    node(d, 1160, 330, 340, 130, "server 3", "real trading", "live", icon="cash")
    ai_node(d, 620, 580, 360, 100)
    storage_node(d, 40, 590, 240, 100)

    d.flow([(440, 370), (630, 370)], "push")
    d.flow_label(535, 353, ["push"], "push")
    d.flow([(440, 425), (630, 425)], "mcp")
    d.flow_label(535, 454, ["MCP"], "mcp")
    d.flow([(360, 330), (360, 250), (1400, 250), (1400, 330)], "push")
    d.flow_label(880, 255, ["push strategies · archive → real"], "push")
    d.flow([(1360, 330), (1360, 288), (400, 288), (400, 330)], "mcp")
    d.flow_label(880, 293, ["MCP · results, trade status"], "mcp")
    d.flow([(620, 630), (330, 630), (330, 460)], "ai")
    d.flow_label(475, 635, ["strategies, tuning"], "ai")
    d.flow([(800, 580), (800, 460)], "ai")
    d.flow_label(812, 526, ["trade data"], "ai", "start")
    d.flow([(980, 630), (1330, 630), (1330, 460)], "ai")
    d.flow_label(1155, 635, ["trade data · read-only"], "ai")
    d.flow([(160, 460), (160, 590)], "s3")
    d.flow_label(172, 530, ["backup"], "s3", "start")
    legend_strip(d, LY)
    return d


# ------------------------------------------------------------------ 4
def cloud_home():
    d = Diagram(W, H, "parity-deriva · cloud + home lab overview",
                "Archive + demo and real in the cloud, test on the PC at home, backup via rclone; you + AI use MCP on all three.")
    d.header("cloud + home lab", "archive, demo and real in the cloud; research on the PC at home", "scenario 4 of 4 · mixed")
    region(d, 28, 140, 1040, 330, "cloud", "cloud", "virtual machines · any provider")
    region(d, 1090, 140, 482, 330, "home", "home lab", "on premises · PC at home")
    node(d, 70, 260, 360, 130, "server 1", "archive + demo", "practice", icon="database", where="cloud vm")
    node(d, 640, 260, 360, 130, "server 2", "real trading", "live", icon="cash", where="cloud vm")
    node(d, 1150, 260, 360, 130, "server 3", "test", icon="flask", where="local pc")
    ai_node(d, 560, 560, 360, 100)
    storage_node(d, 28, 560, 250, 100)

    d.flow([(430, 300), (640, 300)], "push")
    d.flow_label(535, 283, ["push strategies"], "push", bg="#EFEBE2")
    d.flow([(430, 355), (640, 355)], "mcp")
    d.flow_label(535, 384, ["MCP"], "mcp", bg="#EFEBE2")
    d.flow([(1330, 260), (1330, 216), (250, 216), (250, 260)], "mcp")
    d.flow_label(790, 221, ["MCP · strategies, indicators ↔ simulation results"], "mcp", bg="#EFEBE2")
    d.flow([(560, 610), (340, 610), (340, 390)], "ai")
    d.flow_label(450, 615, ["strategies"], "ai")
    d.flow([(820, 560), (820, 390)], "ai")
    d.flow_label(832, 520, ["trade data"], "ai", "start")
    d.flow([(920, 610), (1330, 610), (1330, 390)], "ai")
    d.flow_label(1125, 615, ["tuning"], "ai")
    d.flow([(150, 390), (150, 560)], "s3")
    d.flow_label(162, 520, ["backup"], "s3", "start")
    d.flow([(1460, 390), (1460, 712), (150, 712), (150, 660)], "s3ro")
    d.flow_label(1100, 717, ["optional · test reads the storage"], "s3")
    legend_strip(d, LY)
    return d


if __name__ == "__main__":
    import os, sys
    out = sys.argv[1] if len(sys.argv) > 1 else "out"
    os.makedirs(out, exist_ok=True)
    for name, fn in [("1-platform", platform), ("2-simple", simple), ("3-medium", medium), ("4-cloud-home-lab", cloud_home)]:
        print(name, fn().save(f"{out}/parity-deriva_overview_{name}.svg"))
