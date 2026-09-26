from lib import Diagram, C, measure

W, H = 1600, 1120
FY = 880  # inizio dei riquadri in basso

NOTES = [
    "one application, configured with a different role on each server",
    "you + AI over MCP: archive (strategies, indicators), test (tuning), demo and real (trade data, read-only)",
    "demo and real always on separate servers, for performance and security",
    "backup via rclone: S3, Google Drive, OneDrive or any remote; only the archive writes",
]

REPO = [("file-code", "strategy code"), ("code", "indicator code"), ("activity", "market ticks"),
        ("report-analytics", "simulation results"), ("calendar", "economic calendar")]


PROVIDERS = [("bucket", "S3"), ("brand-google-drive", "Google Drive"), ("brand-onedrive", "OneDrive"),
             ("brand-dropbox", "Dropbox")]


def storage_box(d, x, y, w=440, h=110, compact=False, note="written by the archive only · conflicts tbd"):
    d.rect(x, y, w, h, fill=C["panel"], stroke=C["border"], sw=1.5, r=12)
    if compact:
        d.icon("folders", x + 16, y + 16, 28)
        d.text(x + 54, y + 36, "remote storage", 18, "title")
        d.text(x + 54, y + 54, "backup via rclone", 12, "mono400", C["text3"])
        xx = x + 18
        for ic, _ in PROVIDERS:
            d.icon(ic, xx, y + 68, 20, C["text2"], 1.6)
            xx += 30
        d.text(xx + 2, y + 84, "+ any remote", 12, "sans400", C["text2"])
        return
    d.icon("folders", x + 20, y + 20, 34)
    d.text(x + 70, y + 40, "remote storage", 22, "title")
    d.text(x + 70 + measure("remote storage", 22, "title") + 10, y + 39, "backup via rclone", 12, "mono400", C["text3"])
    xx = x + 70
    for ic, name in PROVIDERS:
        d.icon(ic, xx, y + 52, 18, C["text2"], 1.6)
        d.text(xx + 22, y + 66, name, 12.5, "sans500", C["text2"])
        xx += 22 + measure(name, 12.5, "sans500") + 14
    d.text(xx, y + 66, "+ any remote", 12.5, "sans400", C["text3"])
    d.text(x + 70, y + 92, note, 12, "sans400", C["text3"])


def region(d, x, y, w, h, icon, title, sub, hx=0, stack=False):
    d.rect(x, y, w, h, fill="#EFEBE2", stroke=C["line"], sw=1.2, r=14)
    d.icon(icon, x + hx + 18, y + 14, 26, C["text2"])
    d.text(x + hx + 52, y + 35, title, 20, "title", C["text"])
    if stack:
        d.text(x + hx + 52, y + 54, sub, 12, "mono400", C["text3"])
    else:
        d.text(x + hx + 52 + measure(title, 20, "title") + 12, y + 34, sub, 12, "mono400", C["text3"])


def instances(d, x, y, w, h, names):
    d.rect(x, y, w, h, fill=C["panel"], stroke=C["border"], sw=1.2, r=8, dash="5 4")
    yy = y + 16
    for n in names:
        if n == "…":
            d.text(x + w / 2, yy + 14, "· · ·", 16, "sans600", C["text3"], "middle")
        else:
            d.icon("server", x + 12, yy, 20, C["text2"])
            d.text(x + 40, yy + 15, n, 13, "sans500")
        yy += 38


# ------------------------------------------------------------------ 1
def platform():
    d = Diagram(W, H, "parity-deriva · platform architecture",
                "General model: one application with four roles. The archive is always present; test, demo and real "
                "servers can be one or more each. Strategies reach demo and real only by push; MCP carries results and trade status back.")
    d.header("platform architecture", "one application, four roles: the general model behind every deployment", "general model")

    # web app
    x, y, w, h = 40, 140, 500, 110
    d.rect(x, y, w, h, fill=C["panel"], stroke=C["border"], sw=1.5, r=12)
    d.icon("world-www", x + 20, y + 20, 36)
    d.text(x + 70, y + 44, "web app", 24, "title")
    d.text(x + 20, y + 80, "on every server,", 13, "sans400", C["text2"])
    d.text(x + 20, y + 98, "in the browser (https)", 13, "sans400", C["text2"])
    d.add(f'<line x1="{x + 216}" y1="{y + 18}" x2="{x + 216}" y2="{y + h - 18}" stroke="{C["line"]}"/>')
    d.text(x + 234, y + 32, "ROLES", 11, "sans500", C["text3"], ls=0.06)
    roles = [("archive", "archivist"), ("flask", "tester"), ("chart-candle", "trader demo"), ("cash", "trader real")]
    for i, (ic, r) in enumerate(roles):
        rx = x + 234 + (i % 2) * 124
        ry = y + 46 + (i // 2) * 32
        d.icon(ic, rx, ry, 22, C["text"])
        d.text(rx + 28, ry + 16, r, 13, "sans500")

    # archive
    ax, ay, aw, ah = 540, 300, 460, 360
    y0 = d.server(ax, ay, aw, ah, "archive server", "archivist · always present")
    d.module(ax + 16, y0, 236, ah - (y0 - ay) - 16, "database", "data repository", REPO)
    cx = ax + 264
    cw = aw - 264 - 16
    hh = (ah - (y0 - ay) - 16 - 12) / 2
    d.module(cx, y0, cw, hh, "topology-star", "MCP service", sub="exposed · full access",
             items=["AI manages strategies and indicators here"], isz=12.5, stroke=C["ai"])
    d.module(cx, y0 + hh + 12, cw, hh, "cloud-upload", "direct upload", sub="from the web app",
             items=["ticks, calendar, strategy and indicator code"], isz=12.5)

    # test
    tx, ty, tw, th = 40, 300, 400, 360
    y0 = d.server(tx, ty, tw, th, "test servers", "tester · one or more", dashed=False)
    d.text(tx + tw - 16, ty + 64, "can share the archive server", 12, "sans400", C["text3"], "end")
    instances(d, tx + 16, y0, 150, th - (y0 - ty) - 16, ["test server 1", "test server 2", "…"])
    d.module(tx + 178, y0, tw - 178 - 16, th - (y0 - ty) - 16, "flask", "research",
             [("settings", "build strategies"), ("chart-bar", "run simulations, read the analysis"),
              ("adjustments-horizontal", "MCP: AI tunes parameters and the test algorithm")], isz=12.5, stroke=C["ai"])

    # demo / real
    for (yy, hh, env, title, role, ic) in [(250, 285, "practice", "trade demo servers", "trader demo · one or more", "demo server"),
                                          (555, 290, "live", "trade real servers", "trader real · one or more", "real server")]:
        x, w = 1120, 440
        y0 = d.server(x, yy, w, hh, title, role, env=env)
        instances(d, x + 16, y0, 146, hh - (y0 - yy) - 16, [f"{ic} 1", f"{ic} 2", "…"])
        d.module(x + 174, y0, w - 174 - 16, hh - (y0 - yy) - 16, "chart-candle", "trading",
                 [("send", "receives strategies only by push"),
                  ("topology-star", "sends results and trade status to the archive (MCP)"),
                  ("search", "MCP: AI reads the trade data, read-only")], isz=12.5, stroke=C["ai"])

    # flussi
    d.ai_client(580, 140, 400, 110)
    d.flow([(894, 250), (894, 300)], "ai")
    d.flow_label(906, 280, ["strategies, indicators"], "ai", "start")
    d.flow([(620, 250), (620, 272), (240, 272), (240, 300)], "ai")
    d.flow_label(430, 277, ["tune the tests"], "ai")
    d.flow([(980, 195), (1340, 195), (1340, 250)], "ai")
    d.flow([(1340, 195), (1584, 195), (1584, 700), (1560, 700)], "ai1")
    d.flow_label(1160, 200, ["trade data · read"], "ai")
    d.flow([(440, 470), (540, 470)], "mcp")
    d.flow_label(490, 504, ["MCP", "strategies,", "indicators,", "results"], "mcp")
    d.flow([(1000, 335), (1120, 335)], "push")
    d.flow_label(1060, 318, ["push strategies"], "push")
    d.flow([(1000, 440), (1120, 440)], "mcp")
    d.flow_label(1060, 470, ["MCP", "results,", "trade status"], "mcp")
    d.flow([(1000, 600), (1120, 600)], "push")
    d.flow_label(1060, 583, ["push strategies"], "push")
    d.flow([(1000, 645), (1120, 645)], "mcp")
    d.flow_label(1060, 675, ["MCP", "results,", "trade status"], "mcp")
    d.flow([(770, 660), (770, 730)], "s3")
    d.flow_label(782, 700, ["backup · rclone", "from archive only"], "s3", "start")
    storage_box(d, 530, 730, 510, 110)

    d.footer_cards(FY, None, NOTES)
    return d


# ------------------------------------------------------------------ 2
def simple():
    d = Diagram(W, H, "parity-deriva · simple deployment (2 servers)",
                "Server 1 runs archive, test and demo trading; server 2 runs real trading only. Strategies go from the archive "
                "to server 2 by push; MCP brings results and trade status back. The archive backs up with rclone. You + AI use MCP on both servers.")
    d.header("simple deployment · 2 servers", "archive, test and demo together; real trading on its own server", "scenario 1 of 4")

    # tu + IA
    d.rect(40, 170, 176, 110, fill=C["panel"], stroke=C["border"], sw=1.5, r=12)
    d.icon("user", 58, 190, 30)
    d.text(98, 212, "you", 24, "title")
    d.text(58, 246, "trader, researcher,", 12.5, "sans400", C["text2"])
    d.text(58, 264, "developer", 12.5, "sans400", C["text2"])
    d.rect(40, 380, 176, 150, fill=C["ai_bg"], stroke=C["ai"], sw=2.2, r=12)
    d.icon("robot", 58, 398, 34, C["ai_text"], 1.9)
    d.text(100, 422, "AI assistant", 21, "title")
    d.pill(58, 440, "MCP client", "ai", 11)
    d.text(58, 486, "strategies, indicators,", 12.5, "sans400", C["text2"])
    d.text(58, 504, "test tuning,", 12.5, "sans400", C["text2"])
    d.text(58, 522, "trade data", 12.5, "sans400", C["text2"])
    d.flow([(128, 280), (128, 380)], "ai")
    d.flow_label(140, 335, ["asks"], "ai", "start")
    sx, sy, sw_, sh = 270, 150, 860, 470
    y0 = d.server(sx, sy, sw_, sh, "server 1 · archive + test + demo", "archivist · tester · trader demo", env="practice")
    mw = (sw_ - 32 - 24) / 3
    mh = 214
    d.module(sx + 16, y0, mw, mh, "database", "data repository", REPO)
    d.module(sx + 16 + mw + 12, y0, mw, mh, "flask", "simulation · test",
             [("settings", "backtest engine"), ("chart-bar", "strategy analysis"), ("code", "indicator loading"),
              ("file-code", "strategy creation")])
    d.module(sx + 16 + 2 * (mw + 12), y0, mw, mh, "chart-candle", "demo trading",
             [("player-play", "strategy execution on the demo account"), ("list-check", "position management"),
              ("device-desktop-analytics", "performance monitoring")])
    d.module(sx + 16, y0 + mh + 12, sw_ - 32, sh - (y0 - sy) - mh - 28, "topology-star", "MCP service",
             sub="full access · strategies, indicators, results, trade status",
             items=["AI: manages strategies and indicators, tunes parameters and the test algorithm, reads demo trades",
                    "archive and demo share the instance: strategies reach demo trading locally"], isz=12.5, stroke=C["ai"])

    rx, ry, rw, rh = 1250, 150, 316, 470
    y0 = d.server(rx, ry, rw, rh, "server 2 · real", "trader real only", env="live")
    d.module(rx + 16, y0, rw - 32, 214, "cash", "real trading",
             [("player-play", "strategy execution on the live account"), ("list-check", "position management"),
              ("device-desktop-analytics", "performance monitoring"), ("shield-lock", "isolated instance")])
    d.module(rx + 16, y0 + 226, rw - 32, rh - (y0 - ry) - 226 - 16, "topology-star", "MCP service",
             sub="trade status only", items=["AI reads the trade data, read-only", "no access to the archive data"],
             isz=12.5, stroke=C["ai"])

    d.flow([(216, 225), (270, 225)], "https")
    d.flow_label(243, 206, ["https"], "https")
    d.flow([(216, 500), (286, 500)], "ai")
    d.flow_label(243, 481, ["MCP"], "ai")
    d.flow([(128, 530), (128, 850), (1408, 850), (1408, 620)], "ai1")
    d.flow_label(800, 855, ["you + AI → MCP · real trade data, read-only"], "ai")
    d.flow([(1130, 320), (1250, 320)], "push")
    d.flow_label(1190, 302, ["push strategies"], "push")
    d.flow([(1130, 440), (1250, 440)], "mcp")
    d.flow_label(1190, 472, ["MCP", "results,", "trade status"], "mcp")
    d.flow([(700, 620), (700, 712)], "s3")
    d.flow_label(712, 664, ["backup · rclone", "from archive only"], "s3", "start")
    storage_box(d, 450, 712, 510, 110, note="strategies, indicators, results · archive writes only")

    d.footer_cards(FY, 0, NOTES)
    return d


# ------------------------------------------------------------------ 3
def medium():
    d = Diagram(W, H, "parity-deriva · medium deployment (3 servers)",
                "Server 1 runs archive and test, server 2 demo trading, server 3 real trading. The archive pushes "
                "strategies to both trading servers, which send results and trade status back over MCP.")
    d.header("medium deployment · 3 servers", "archive and test together; demo and real each on their own server", "scenario 2 of 4")

    top, hh = 200, 490
    # archive + test
    ax, aw = 40, 490
    y0 = d.server(ax, top, aw, hh, "server 1 · archive + test", "archivist · tester")
    inner = hh - (y0 - top) - 16
    d.module(ax + 16, y0, 222, inner, "database", "data repository", REPO)
    cx, cw = ax + 250, aw - 250 - 16
    sh = (inner - 3 * 10) / 4
    for i, (ic, t, s) in enumerate([("flask", "simulation · backtest", "AI tunes parameters, algorithm"),
                                     ("chart-bar", "analysis · reporting", "runs and results"),
                                     ("cloud-upload", "direct upload", "from the web app"),
                                     ("topology-star", "MCP service", "full access · servers and AI")]):
        d.module(cx, y0 + i * (sh + 10), cw, sh, ic, t, sub=s, stroke=C["ai"] if i == 3 else None)

    # demo, real
    for (x, w, env, title, role, eng, where) in [
            (630, 430, "practice", "server 2 · demo", "trader demo", "demo trading engine", "demo account"),
            (1160, 400, "live", "server 3 · real", "trader real", "real trading engine", "live account")]:
        y0 = d.server(x, top, w, hh, title, role, env=env)
        items = [("player-play", f"execute strategies on the {where}"), ("list-check", "manage positions"),
                 ("device-desktop-analytics", "performance monitoring")]
        if env == "live":
            items.append(("shield-lock", "isolated, high security"))
        d.module(x + 16, y0, w - 32, 200, "chart-candle" if env == "practice" else "cash", eng, items)
        d.module(x + 16, y0 + 212, w - 32, hh - (y0 - top) - 212 - 16, "topology-star", "MCP service",
                 sub="trade status only",
                 items=[("send", "sends results and trade status to the archive"),
                        ("download", "AI reads the trade data, read-only")], isz=12.5, stroke=C["ai"])

    # flussi
    d.flow([(530, 330), (630, 330)], "push")
    d.flow_label(580, 313, ["push"], "push")
    d.flow([(530, 460), (630, 460)], "mcp")
    d.flow_label(580, 492, ["MCP", "results,", "trade status"], "mcp")
    d.flow([(420, 200), (420, 140), (1360, 140), (1360, 200)], "push")
    d.flow_label(890, 145, ["push strategies · archive → real"], "push", bg=C["bg"])
    d.flow([(1320, 200), (1320, 174), (470, 174), (470, 200)], "mcp")
    d.flow_label(890, 179, ["MCP · results, trade status from real"], "mcp")
    d.flow([(100, 690), (100, 740)], "s3")
    d.flow_label(112, 720, ["backup · rclone"], "s3", "start")
    storage_box(d, 40, 740, 262, 104, compact=True)
    d.ai_client(600, 760, 360, 76, compact=True)
    d.flow([(600, 798), (330, 798), (330, 690)], "ai")
    d.flow_label(465, 803, ["strategies, indicators, tests"], "ai")
    d.flow([(780, 760), (780, 690)], "ai")
    d.flow_label(792, 730, ["trade data"], "ai", "start")
    d.flow([(960, 798), (1360, 798), (1360, 690)], "ai")
    d.flow_label(1160, 803, ["trade data · read-only"], "ai")
    d.footer_cards(FY, 1, NOTES)
    return d


# ------------------------------------------------------------------ 4
def cloud_home():
    d = Diagram(W, H, "parity-deriva · cloud + home lab deployment",
                "Archive + demo and real trading run as cloud virtual machines; the test server runs on a PC at home and "
                "talks to the archive over MCP; a remote storage reached with rclone holds the backup. You + AI use MCP on archive, test and real.")
    d.header("cloud + home lab", "archive, demo and real in the cloud; research on the PC at home", "scenario 4 of 4 · mixed")

    region(d, 28, 136, 1052, 574, "cloud", "cloud", "virtual machines · any provider")
    region(d, 1100, 136, 472, 574, "home", "home lab", "on premises · PC at home")

    top, hh = 196, 470
    # server 1
    x, w = 52, 452
    y0 = d.server(x, top, w, hh, "server 1 · archive + demo", "archivist · trader demo", env="practice", where="cloud vm")
    inner = hh - (y0 - top) - 16
    d.module(x + 16, y0, 206, inner, "database", "data repository", REPO)
    d.module(x + 234, y0, w - 250, 160, "chart-candle", "demo trading",
             [("player-play", "demo account"), ("list-check", "positions")], isz=12.5)
    d.module(x + 234, y0 + 172, w - 250, inner - 172, "topology-star", "MCP service", sub="exposed",
             items=["AI manages strategies and indicators; reads demo trades"], isz=12.5, stroke=C["ai"])

    # server 2
    x, w = 624, 432
    y0 = d.server(x, top, w, hh, "server 2 · real", "trader real", env="live", where="cloud vm")
    d.module(x + 16, y0, w - 32, 190, "cash", "live trading",
             [("send", "strategies arrive only by push"), ("shield-lock", "separate instance, high security"),
              ("device-desktop-analytics", "performance monitoring")])
    d.module(x + 16, y0 + 202, w - 32, hh - (y0 - top) - 202 - 16, "topology-star", "MCP service",
             sub="read-only · trade status",
             items=[("send", "sends trade results to the archive"),
                    ("download", "AI reads the trade data")], isz=12.5, stroke=C["ai"])

    # server 3
    x, w = 1124, 424
    y0 = d.server(x, top, w, hh, "server 3 · test", "tester", where="local pc")
    d.module(x + 16, y0, w - 32, 190, "flask", "backtesting · analysis",
             [("settings", "strategy development"), ("chart-bar", "simulation analysis"),
              ("microscope", "runs on its own, no orders")])
    d.module(x + 16, y0 + 202, w - 32, hh - (y0 - top) - 202 - 16, "topology-star", "MCP service",
             items=[("adjustments-horizontal", "AI tunes parameters and the test algorithm"),
                    ("arrows-exchange", "strategies and indicators in, results out; can read the storage")],
             isz=12.5, stroke=C["ai"])

    # flussi
    d.flow([(504, 320), (624, 320)], "push")
    d.flow_label(564, 303, ["push"], "push", bg="#EFEBE2")
    d.flow([(504, 440), (624, 440)], "mcp")
    d.flow_label(564, 472, ["MCP", "results,", "trade status"], "mcp", bg="#EFEBE2")
    d.flow([(1336, 196), (1336, 182), (400, 182), (400, 196)], "mcp")
    d.flow_label(870, 187, ["MCP · strategies and indicators ↔ simulation results"], "mcp", bg="#EFEBE2")
    d.flow([(160, 666), (160, 752)], "s3")
    d.flow_label(172, 727, ["backup · rclone"], "s3", "start", bg=C["bg"])
    d.ai_client(320, 736, 310, 76, compact=True)
    d.flow([(420, 736), (420, 666)], "ai")
    d.flow_label(432, 722, ["strategies, indicators"], "ai", "start", bg=C["bg"])
    d.flow([(630, 766), (840, 766), (840, 666)], "ai")
    d.flow_label(852, 722, ["trade data"], "ai", "start", bg=C["bg"])
    d.flow([(630, 792), (1336, 792), (1336, 666)], "ai")
    d.flow_label(1100, 797, ["tune the tests"], "ai", bg=C["bg"])
    d.flow([(1500, 666), (1500, 840), (290, 840)], "s3ro")
    d.flow_label(1040, 845, ["optional · read-only access to the storage"], "s3", bg=C["bg"])
    storage_box(d, 28, 752, 262, 102, compact=True)
    d.footer_cards(FY, 3, NOTES)
    return d


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "out"
    import os
    os.makedirs(out, exist_ok=True)
    for name, fn in [("1-platform-architecture", platform), ("2-simple-deployment", simple),
                     ("3-medium-deployment", medium), ("4-cloud-home-lab", cloud_home)]:
        n = fn().save(f"{out}/parity-deriva_{name}.svg")
        print(name, n)
