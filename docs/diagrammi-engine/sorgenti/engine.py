"""Diagramma del motore di simulazione, da docs/ENGINE.md.

Un bus di eventi, gli stessi handler, due cablaggi: backtest (ReplayEngine) e live
(trading/engine.py, con il simulatore come ombra e il parity monitor).
"""
from lib import C, measure
from flowlib import FD, RED, GREEN

INK = C["text"]


def ev(d, x, y, names, anchor="middle"):
    """Nome dell'evento sulla freccia: pillola in monospazio."""
    t = " · ".join(names)
    w = measure(t, 11.5, "mono400") + 14
    x0 = {"middle": x - w / 2, "start": x, "end": x - w}[anchor]
    d.rect(x0, y - 11, w, 20, fill=C["panel"], stroke=C["border"], sw=1, r=10)
    d.text(x0 + 7, y + 3.5, t, 11.5, "mono400", INK)


def node(d, x, y, w, h, title, file, icon, kind=None, note=None):
    stroke = {None: C["border"], "broker": RED, "sim": INK}[kind]
    sw = {None: 1.5, "broker": 2, "sim": 2.4}[kind]
    d.rect(x, y, w, h, fill=C["panel"], stroke=stroke, sw=sw, r=10)
    d.icon(icon, x + 14, y + 14, 22, INK)
    d.text(x + 44, y + 30, title, 14.5, "sans600")
    d.text(x + 14, y + 54, file, 11.5, "mono400", C["text3"])
    if note:
        d.text(x + 14, y + 72, note, 12, "sans400", C["text2"])


def panel(d, x, y, w, h, title, sub, icon, driver_lines):
    d.rect(x, y, w, h, fill="#EFEBE2", stroke=C["line"], sw=1.2, r=14)
    d.icon(icon, x + 18, y + 16, 26, C["text2"])
    d.text(x + 54, y + 38, title, 22, "title")
    d.text(x + 54 + measure(title, 22, "title") + 12, y + 37, sub, 12, "mono400", C["text3"])
    yy = y + h - 16 - (len(driver_lines) - 1) * 18
    for i, l in enumerate(driver_lines):
        d.text(x + 20, yy + i * 18, l, 12.5, "sans500" if i == 0 else "sans400", INK if i == 0 else C["text2"])


def card(d, x, y, w, h, title, icon, lines):
    d.rect(x, y, w, h, fill=C["panel"], stroke=C["line"], r=12)
    d.icon(icon, x + 16, y + 16, 22)
    d.text(x + 46, y + 34, title, 18, "title")
    yy = y + 62
    for l in lines:
        if isinstance(l, tuple):
            n, t = l
            d.add(f'<circle cx="{x + 26}" cy="{yy - 4}" r="9" fill="{INK}"/>')
            d.text(x + 26, yy, n, 10.5, "sans600", C["panel"], "middle")
            d.text(x + 42, yy, t, 12.5, "sans400", C["text2"])
        else:
            d.add(f'<circle cx="{x + 24}" cy="{yy - 4}" r="2.2" fill="{C["text3"]}"/>')
            d.text(x + 34, yy, l, 12.5, "sans400", C["text2"])
        yy += 21


def engine():
    W, H = 1800, 1160
    d = FD(W, H, "parity-deriva · the simulation engine",
           "One event bus, the same handlers, two wirings. Backtest: candles from the store, strategy, money manager, "
           "simulator and SimulatedBroker close the loop. Live: the broker's candles, execution and the real broker, with "
           "the simulator running as a shadow and the parity monitor comparing the two sides.")
    d.header("The simulation engine", "one event bus · the same handlers run live and in a backtest · docs/ENGINE.md",
             "engine")
    # ---------------- backtest
    PX, PY, PW, PH = 28, 136, 856, 640
    panel(d, PX, PY, PW, PH, "backtest", "offline · backtest/driver.py · ReplayEngine", "flask",
          ["one candle at a time: everything it causes is dispatched before the next one",
           "when candle N is read, the book holds exactly the orders of candles 1 to N−1 · guard: 10 000 events per candle"])
    node(d, 56, 200, 200, 84, "candle source", "data/replay.py", "database", note="CANDLE → every handler")
    node(d, 336, 200, 190, 84, "strategy", "strategy/*.py", "file-code", note="its own granularity")
    node(d, 610, 200, 240, 84, "money manager", "portfolio/moneymanager.py", "scale", note="one trade at a time · size")
    node(d, 610, 430, 240, 84, "simulator", "backtest/oanda.py", "settings", kind="sim", note="ask/bid fills, one behaviour")
    node(d, 336, 430, 190, 84, "SimulatedBroker", "backtest/offline.py", "arrows-exchange", note="plays the broker")
    node(d, 56, 430, 200, 84, "ledger", "backtest/ledger.py", "report-analytics", note="reads all, writes trades")
    node(d, 530, 604, 168, 70, "trailer", "portfolio/trailer.py", "adjustments-horizontal")
    node(d, 706, 604, 168, 70, "session · timer", "portfolio/session.py", "clock")
    d.flow([(256, 242), (336, 242)], "done"); ev(d, 296, 222, ["CANDLE"])
    d.flow([(526, 242), (610, 242)], "done"); ev(d, 568, 222, ["SIGNAL"])
    d.flow([(780, 284), (780, 430)], "done"); ev(d, 780, 356, ["ORDER"])
    d.flow([(610, 472), (526, 472)], "done"); ev(d, 568, 452, ["SIMULATED*"])
    d.flow([(431, 430), (431, 360), (660, 360), (660, 284)], "done")
    ev(d, 546, 360, ["CLIENTORDER", "TRANSACTION"])
    d.flow([(640, 604), (640, 514)], "done"); ev(d, 632, 560, ["STOPMODIFY"], "end")
    d.flow([(790, 604), (790, 514)], "done"); ev(d, 798, 560, ["CLOSETRADE"], "start")
    d.add(f'<line x1="256" y1="472" x2="336" y2="472" stroke="{C["text3"]}" stroke-width="1.6" stroke-dasharray="3 4"/>')
    d.text(296, 492, "reads all", 11, "sans400", C["text3"], "middle")
    # ---------------- live
    LX = 916
    panel(d, LX, PY, PW, PH, "live", "trading/engine.py · scripts/live.py", "cash",
          ["one thread per source (candles, transactions), one queue, heartbeat 0.5 s",
           "right live: bars arrive minutes apart, so an order is resting long before the next bar"])
    node(d, 944, 200, 200, 84, "broker's candles", "provider stream", "chart-candle", note="CANDLE → every handler")
    node(d, 1224, 200, 190, 84, "strategy", "strategy/*.py", "file-code", note="same code as backtest")
    node(d, 1498, 200, 240, 84, "money manager", "portfolio/moneymanager.py", "scale", note="same rules as backtest")
    node(d, 1498, 400, 240, 84, "execution", "execution/*.py", "send", note="sends to the broker")
    node(d, 1224, 400, 190, 84, "broker", "real account", "building-bank", kind="broker", note="demo or real money")
    node(d, 1498, 590, 240, 84, "simulator · shadow", "backtest/oanda.py", "settings", kind="sim", note="same candles, same orders")
    node(d, 1224, 590, 190, 84, "parity monitor", "trading/parity.py", "scale", note="warn / halt")
    node(d, 944, 400, 200, 84, "event saver", "event/saver.py", "file-text", note="every event, one JSON line")
    d.flow([(1144, 242), (1224, 242)], "done"); ev(d, 1184, 222, ["CANDLE"])
    d.flow([(1414, 242), (1498, 242)], "done"); ev(d, 1456, 222, ["SIGNAL"])
    d.flow([(1680, 284), (1680, 400)], "done"); ev(d, 1680, 342, ["ORDER"])
    d.flow([(1498, 442), (1414, 442)], "done")
    d.flow([(1319, 400), (1319, 340), (1548, 340), (1548, 284)], "done")
    ev(d, 1434, 340, ["TRANSACTION"])
    d.flow([(1738, 242), (1760, 242), (1760, 632), (1738, 632)], "done"); ev(d, 1760, 520, ["ORDER"], "end")
    d.flow([(1498, 632), (1414, 632)], "done"); ev(d, 1456, 612, ["SIMULATED*"])
    d.flow([(1319, 484), (1319, 590)], "done"); ev(d, 1319, 538, ["real side"])
    d.add(f'<line x1="1144" y1="442" x2="1224" y2="442" stroke="{C["text3"]}" stroke-width="1.6" stroke-dasharray="3 4"/>')
    d.text(1184, 462, "reads all", 11, "sans400", C["text3"], "middle")
    d.text(1100, 620, "the simulator reports with its own", 12, "sans400", C["text2"], "middle")
    d.text(1100, 637, "events: its fills never reach", 12, "sans400", C["text2"], "middle")
    d.text(1100, 654, "the money manager or the account", 12, "sans400", C["text2"], "middle")
    # ---------------- sotto: tre schede
    y0, h = 800, 262
    cw = (W - 56 - 32) / 3
    card(d, 28, y0, cw, h, "Handler order (backtest/ledger.run)", "list", [
        ("1", "strategy"), ("2", "money manager"), ("3", "trailer — before the simulator"),
        ("4", "simulator"), ("5", "SimulatedBroker"), ("6", "ledger"),
        ("7", "session closer, trade timer, progress (if asked)"),
        "a stop moved on a bar applies from the next bar;", "live wires the same order (scripts/live.py)"])
    card(d, 28 + cw + 16, y0, cw, h, "How the simulator fills", "settings", [
        ("1", "expired order (gtdTime) dropped first"),
        ("2", "market order: this bar's open"),
        ("3", "resting order: its level, if inside low–high"),
        ("4", "gap over the level: fills at the open"),
        ("5", "stop already passed at the open: the open"),
        ("6", "several on one bar: nearest to the open first (OCO)"),
        ("7", "stop or target closes; CLOSETRADE at the close"),
        "buy on the ask, sell on the bid: the spread is in the prices",
        "P/L = (exit − entry) × units · financing 0, no commission"])
    card(d, 28 + 2 * (cw + 16), y0, cw, h, "No look-ahead · what a bar cannot say", "eye", [
        "a strategy acts on a closed bar; its order fills from the next",
        "the trailer moves the stop for the next bar",
        "two granularities are ordered by close time, never open time",
        "live: first bar allowed is the one forming at start (notBefore)",
        "stop and target in one bar: the bar cannot say which came first",
        "backtest/resolution.py measures it: coarse vs fine bars",
        "backtest/shadow.py fills on the finest bars in the store (M5)",
        "fallback to a coarser series over 1 000 000 fine bars",
        "EventSaver + EventReplay: a live session can be run again"])
    # legenda
    y = H - 50
    d.add(f'<line x1="28" y1="{y - 24}" x2="{W - 28}" y2="{y - 24}" stroke="{C["line"]}"/>')
    x = 28
    d.flow([(x, y), (x + 48, y)], "done"); d.text(x + 58, y + 4.5, "event on the bus", 13, "sans600")
    x += 58 + measure("event on the bus", 13, "sans600") + 16
    ev(d, x, y, ["EVENT"], "start"); x += measure("EVENT", 11.5, "mono400") + 30
    d.add(f'<line x1="{x}" y1="{y}" x2="{x + 48}" y2="{y}" stroke="{C["text3"]}" stroke-width="1.6" stroke-dasharray="3 4"/>')
    d.text(x + 58, y + 4.5, "reads every event", 13, "sans600"); x += 58 + measure("reads every event", 13, "sans600") + 30
    d.rect(x, y - 10, 40, 20, fill=C["panel"], stroke=INK, sw=2.4, r=5)
    d.text(x + 50, y + 4.5, "the simulator: one behaviour, every comparison rests on it", 13, "sans600")
    x += 50 + measure("the simulator: one behaviour, every comparison rests on it", 13, "sans600") + 30
    d.rect(x, y - 10, 40, 20, fill=C["panel"], stroke=RED, sw=2, r=5)
    d.text(x + 50, y + 4.5, "the real broker", 13, "sans600")
    d.text(W - 28, y + 4.5, "STATUS (DONE · HALT · LIQUIDATE · RESUME): anyone to everyone", 12, "mono400", C["text3"], "end")
    d.text(28, H - 20, "parity-deriva · the simulation engine", 11, "mono400", C["text3"])
    d.text(W - 28, H - 20, "source: docs/ENGINE.md", 11, "mono400", C["text3"], "end")
    return d


if __name__ == "__main__":
    import os, sys
    out = sys.argv[1] if len(sys.argv) > 1 else "out_engine"
    os.makedirs(out, exist_ok=True)
    print(engine().save(f"{out}/parity-deriva_engine.svg"))
