"""Diagrammi del processo parity-deriva (da PROCESSO.md, sezione "Grafici per claude.ai").

Otto tavole SVG autonome. Convenzioni del documento: linea piena = c'è già,
linea tratteggiata + etichetta "da fare" = da fare; colori degli stati SIM arancione,
DEMO verde chiaro, LIVE verde pieno, SUSPENDED ambra, DEAD rosso.
"""
import math
import random
from lib import C, measure, wrap
from flowlib import FD, ST, GREEN, RED, AMBER, SIM_OR


# ------------------------------------------------------------------ 1
def g1_processo():
    W, H = 1800, 1100
    d = FD(W, H, "parity-deriva · da un'idea a un trade live",
           "Il processo completo in quattro corsie (Test, Archivio, Trade DEMO, Trade REAL): idea, strategia, "
           "loop SIM, gate holdout, mix, verify, demo, gate promozione, live in ramp e al 100%, SUSPENDED e DEAD.")
    d.header("Da un'idea a un trade live", "il processo completo, corsia per corsia · niente paper trading: la demo gira già sul broker vero", "processo · 1 di 8")
    X0, X1 = 28, W - 28
    d.lane(X0, 136, X1 - X0, 206, "Test", "PC di casa o cloud · SIM, sweep, mix", "flask")
    d.lane(X0, 356, X1 - X0, 150, "Archivio", "cloud · verify, form, record", "database")
    d.lane(X0, 520, X1 - X0, 150, "Trade DEMO", "cloud · account demo", "chart-candle", stroke=ST["DEMO"]["stroke"])
    d.lane(X0, 684, X1 - X0, 200, "Trade REAL", "cloud · server separato, soldi veri", "cash", stroke=RED)

    # Test
    d.add(f'<circle cx="228" cy="246" r="36" fill="#E3DED3" stroke="{C["border"]}" stroke-width="1.6"/>')
    d.icon("bulb", 216, 220, 24, C["text2"])
    d.text(228, 264, "Idea", 13, "sans600", anchor="middle")
    d.box(290, 206, 150, 80, "Strategia", "codice + parametri", icon="file-code")
    d.box(476, 162, 410, 168, "SIM", "periodo di sviluppo", state="SIM", icon="repeat")
    d.box(494, 214, 170, 96, "Sweep", "fino a 500 combinazioni", fill=C["panel"], stroke=SIM_OR, tsize=14)
    d.box(700, 214, 170, 96, "Correlazioni indicatori ↔ P/L", None, todo=True, fill=C["panel"], stroke=SIM_OR, tsize=13)
    d.flow([(664, 240), (700, 240)], "loop")
    d.flow([(700, 288), (664, 288)], "loopd")
    d.diamond(985, 246, 160, 112, ["Gate holdout"], "una volta per versione", todo=True)
    d.box(1100, 206, 170, 80, "Mix", "sullo stesso strumento", icon="stack-2")
    d.flow([(264, 246), (290, 246)], "done")
    d.flow([(440, 246), (476, 246)], "done")
    d.flow([(886, 246), (905, 246)], "todo")
    d.flow([(1065, 246), (1100, 246)], "todo")
    d.label(1082, 232, ["passa"], bg="#EFEBE2")
    d.flow([(985, 190), (985, 150), (365, 150), (365, 206)], "kod")
    d.label(675, 154, ["bocciata: nuova versione (max 3)"], RED, bg="#EFEBE2")

    # Archivio
    d.box(1090, 380, 230, 104, "Verify", "rifà i run con codice e candele dell'archivio", icon="checklist")
    d.box(1440, 380, 230, 104, "Record demo", "sessioni del form lette ogni 5 minuti", icon="clipboard-check")
    d.flow([(1185, 286), (1185, 380)], "done")
    d.label(1197, 335, ["sync.py push"], anchor="start", bg="#EFEBE2")

    # DEMO
    d.box(1090, 546, 290, 100, "DEMO", "parametri congelati · monitor di parità", state="DEMO", icon="chart-candle")
    d.flow([(1205, 484), (1205, 546)], "done")
    d.label(1217, 520, ["push form"], anchor="start", bg="#EFEBE2")
    d.flow([(1380, 596), (1480, 596), (1480, 484)], "done")
    d.label(1492, 560, ["record demo"], anchor="start", bg="#EFEBE2")

    # REAL
    d.flow([(1600, 484), (1600, 704), (760, 704), (760, 728)], "done")
    d.label(1612, 600, ["push_record"], anchor="start", bg="#EFEBE2")
    d.diamond(760, 784, 176, 112, ["Gate", "promozione"], None)
    d.text(560, 780, "giudicato dal", 12, "sans400", C["text2"], "middle")
    d.text(560, 796, "server reale", 12, "sans400", C["text2"], "middle")
    d.text(560, 820, "bocciato: resta in DEMO", 11.5, "sans400", C["text3"], "middle")
    d.text(560, 835, "o torna a SIM", 11.5, "sans400", C["text3"], "middle")
    d.box(920, 744, 180, 82, "LIVE ramp 25%", "i primi 30 trade", state="RAMP", todo=True, tsize=14)
    d.box(1250, 744, 180, 82, "LIVE 100%", "size piena", state="LIVE", tsize=14)
    d.flow([(848, 784), (920, 784)], "done")
    d.flow([(1100, 784), (1250, 784)], "todo")
    d.label(1175, 770, ["30 trade", "dentro la banda"], bg="#EFEBE2")

    # uscite
    d.box(910, 920, 200, 70, "SUSPENDED", "da riverificare", state="SUSP", icon="player-pause", tsize=14)
    d.box(1250, 920, 180, 70, "DEAD", "scartata", state="DEAD", icon="circle-x", tsize=14)
    d.flow([(1010, 826), (1010, 920)], "susp")
    d.label(1022, 868, ["fuori banda / serie di perdite"], AMBER, anchor="start")
    d.flow([(1340, 826), (1340, 920)], "kod")
    d.label(1352, 868, ["DD oltre 1.5×"], RED, anchor="start")
    d.flow([(1110, 955), (1250, 955)], "kod")
    d.label(1180, 942, ["seconda volta"], RED)
    d.flow([(910, 955), (640, 955), (640, 596), (1090, 596)], "susp")
    d.label(652, 945, ["prima volta: si riverifica"], AMBER, anchor="start")
    d.legend_bar(H - 60)
    return d


# ------------------------------------------------------------------ 2
def g2_loop():
    W, H = 1600, 980
    d = FD(W, H, "parity-deriva · SIM, il loop di simulazione",
           "Quattro tappe in senso orario (sweep, leggere i risultati, correlazioni, filtro candidato come nuovo parametro); "
           "quando niente migliora si passa al gate holdout: passa verso la scheda e la DEMO, bocciata verso una nuova versione.")
    d.header("SIM · il loop di simulazione", "sul periodo di sviluppo, finché qualcosa migliora in modo chiaro", "processo · 2 di 8")
    cx, cy, R = 520, 540, 250
    d.add(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="{ST["SIM"]["fill"]}" stroke-width="44"/>')
    # archi fra le tappe (senso orario, partendo in alto)
    def arc(a0, a1, kind):
        p0 = (cx + R * math.sin(math.radians(a0)), cy - R * math.cos(math.radians(a0)))
        p1 = (cx + R * math.sin(math.radians(a1)), cy - R * math.cos(math.radians(a1)))
        d.path(f"M{p0[0]:.1f} {p0[1]:.1f} A{R} {R} 0 0 1 {p1[0]:.1f} {p1[1]:.1f}", kind)
    arc(28, 62, "loop"); arc(118, 152, "loopd"); arc(208, 242, "loopd"); arc(298, 332, "loop")
    nodes = [
        (0, "1 · Sweep", ["parametri + ore (session) + news", "+ stop/target + strumento,", "fino a 500 combinazioni"], False, "settings"),
        (90, "2 · Leggi i risultati", ["paramEffects, score,", "altopiano, non picco"], False, "chart-bar"),
        (180, "3 · Correlazioni indicatori ↔ P/L", ["indicatori sulla barra prima", "del segnale, 5 fasce, bootstrap"], True, "chart-dots"),
        (270, "4 · Filtro candidato", ["→ nuovo parametro", "dello sweep"], False, "filter"),
    ]
    for ang, t, sub, todo, ic in nodes:
        x = cx + R * math.sin(math.radians(ang)); y = cy - R * math.cos(math.radians(ang))
        w, h = (270, 118) if ang in (0, 180) else (230, 104)
        d.box(x - w / 2, y - h / 2, w, h, t, sub, todo=todo, icon=ic, stroke=SIM_OR, tsize=14, ssize=12)
    d.text(cx, cy - 6, "SIM", 44, "title", ST["SIM"]["text"], "middle")
    d.text(cx, cy + 22, "loop sul periodo di sviluppo", 13, "sans400", C["text2"], "middle")
    # uscita verso il gate
    gx, gy = 1090, 540
    d.flow([(cx + R + 115, cy), (gx - 110, gy)], "done")
    d.label((cx + R + 115 + gx - 110) / 2, gy - 14, ["niente migliora più"])
    d.diamond(gx, gy, 220, 150, ["Gate holdout"], None, todo=True)
    for i, l in enumerate(["≥ 100 trade · PF bootstrap basso > 1", "altopiano · PF senza i 3 migliori > 1",
                           "batte la baseline casuale", "holdout: net > 0, PF ≥ 0.7×, DD ≤ 1.5×"]):
        d.text(gx, gy + 100 + i * 18, l, 12, "sans400", C["text2"], "middle")
    d.flow([(gx + 110, gy), (1250, gy), (1250, 330), (1290, 330)], "okd")
    d.label(1258, 440, ["passa"], GREEN, anchor="start")
    d.box(1290, 290, 270, 90, "Scheda di riferimento → DEMO", "PF, expectancy, max DD, banda Monte Carlo", state="DEMO", todo=True, tsize=14, ssize=12)
    d.flow([(gx, gy + 180), (gx, 800), (1290, 800)], "kod")
    d.box(1290, 760, 270, 90, "Bocciata → nuova versione", "dopo 3 versioni bocciate: DEAD", state="DEAD", tsize=14, ssize=12)
    d.label(1150, 787, ["bocciata"], RED)
    # nota
    d.rect(1290, 470, 270, 110, fill=C["panel"], stroke=C["line"], r=10)
    d.icon("alert-triangle", 1306, 486, 20, SIM_OR)
    for i, l in enumerate(["Un filtro candidato non è mai", "una regola: diventa un parametro", "dello sweep e deve superare", "l'holdout."]):
        d.text(1336, 502 + i * 19, l, 13, "sans500" if i == 0 else "sans400", C["text"] if i == 0 else C["text2"])
    d.legend_bar(H - 56, extra_states=False, note="con 20 indicatori provati, circa uno esce 'buono' per caso")
    return d


# ------------------------------------------------------------------ 3
def g3_holdout():
    W, H = 1600, 700
    d = FD(W, H, "parity-deriva · come si usa lo storico",
           "Lo storico diviso in sviluppo (tre quarti, per sweep e correlazioni) e holdout (ultimo quarto, almeno un anno, "
           "aperto una volta per versione); la demo vede dati nuovi; dopo tre versioni bocciate la strategia è DEAD.")
    d.header("Come si usa lo storico", "sviluppo e holdout · l'holdout si apre una volta per versione", "processo · 3 di 8")
    x0, x1, y, h = 80, 1260, 330, 70
    xs = x0 + (x1 - x0) * 0.75
    d.rect(x0, y, xs - x0, h, fill="#DCE8F6", stroke=C["entry"], sw=1.6, r=10)
    d.rect(xs, y, x1 - xs, h, fill="#3A4852", stroke="#2A353D", sw=1.6, r=10)
    d.rect(xs - 10, y + 1, 20, h - 2, fill="#3A4852")  # raccordo
    d.rect(xs - 11, y, 1.6, h, fill=C["entry"])
    d.text((x0 + xs) / 2, y + 32, "SVILUPPO", 18, "sans600", C["entry"], "middle", ls=0.08)
    d.text((x0 + xs) / 2, y + 54, "sweep e correlazioni vedono solo questo", 13, "sans400", C["text"], "middle")
    d.icon("lock", xs + 20, y + 22, 26, "#F4EFE4")
    d.text(xs + 56, y + 32, "HOLDOUT", 18, "sans600", "#F4EFE4", ls=0.08)
    d.text(xs + 56, y + 54, "≥ 1 anno · ultimo ~25%", 13, "sans400", "#DCE3E8")
    d.text(x0, y + h + 24, "anni vecchi", 12, "mono400", C["text3"])
    d.text(x1, y + h + 24, "oggi", 12, "mono400", C["text3"], "end")
    d.tag(xs + 20, y + h + 12)
    d.text(xs + 90, y + h + 25, "holdout nello sweep", 12, "sans400", C["text3"])
    # tante frecce avanti e indietro sopra lo sviluppo
    rnd = random.Random(7)
    for i in range(9):
        ax = x0 + 60 + i * 95
        ay = y - 28 - (i % 3) * 16
        w = 50 + rnd.randint(0, 20)
        d.path(f"M{ax} {ay} Q{ax + w / 2} {ay - 22} {ax + w} {ay}", "loop")
        d.path(f"M{ax + w} {ay + 8} Q{ax + w / 2} {ay + 26} {ax} {ay + 8}", "loop")
    d.label((x0 + xs) / 2, y - 110, ["loop: prova, correggi, riprova"], SIM_OR)
    # una freccia con l'occhio sopra l'holdout
    hx = (xs + x1) / 2
    d.flow([(hx, y - 100), (hx, y - 8)], "done")
    d.icon("eye", hx - 16, y - 142, 32, C["text"])
    d.label(hx, y - 156, ["si guarda una volta"])
    # demo
    d.box(1310, y - 4, 250, 78, "DEMO", "dati nuovi, mai visti", state="DEMO", icon="chart-candle")
    d.flow([(x1 + 4, y + h / 2), (1310, y + h / 2)], "done")
    # contatori delle versioni
    cy0 = 540
    d.text(x0, cy0 - 14, "versioni provate sull'holdout della stessa strategia", 13, "sans600")
    for i, (t, dead) in enumerate([("versione 1", False), ("versione 2", False), ("versione 3", True)]):
        bx = x0 + i * 250
        d.rect(bx, cy0, 220 if not dead else 250, 58, fill=C["panel"], stroke=RED if dead else C["border"], sw=1.6, r=10)
        d.icon("versions", bx + 14, cy0 + 17, 24, C["text2"])
        d.text(bx + 48, cy0 + 35, t, 15, "sans600")
        d.icon("circle-x", bx + 140, cy0 + 16, 26, RED)
        if dead:
            d.text(bx + 172, cy0 + 35, "→ DEAD", 14, "sans600", RED)
        if i < 2:
            d.flow([(bx + 220, cy0 + 29), (bx + 250, cy0 + 29)], "msg")
    d.text(x0 + 800, cy0 + 26, "dopo 3 bocciature l'holdout è consumato:", 14, "sans500")
    d.text(x0 + 800, cy0 + 46, "la strategia è DEAD", 14, "sans500", RED)
    d.legend_bar(H - 56, extra_states=False, note="si conta anche quante combinazioni e filtri sono stati provati")
    return d


# ------------------------------------------------------------------ 4
def g4_mix():
    W, H = 1600, 860
    d = FD(W, H, "parity-deriva · mix di strategie sullo stesso strumento",
           "Tre strategie sullo stesso strumento con le loro posizioni long e short nel tempo; evidenziate tre situazioni "
           "(long e short insieme, posizioni che si sommano, doppione) e a destra i controlli del mix, fatti e da fare.")
    d.header("Il mix sullo stesso strumento", "più strategie, un solo strumento, controllate insieme", "processo · 4 di 8")
    x0, x1 = 170, 1130
    n = 120
    rnd = random.Random(11)
    p = 1.0850
    candles = []
    for i in range(n):
        o = p
        c = o + rnd.gauss(0.00005, 0.0009)
        hi = max(o, c) + abs(rnd.gauss(0, 0.0004)); lo = min(o, c) - abs(rnd.gauss(0, 0.0004))
        candles.append((o, hi, lo, c)); p = c
    pmin = min(c[2] for c in candles); pmax = max(c[1] for c in candles)
    cy0, cy1 = 150, 330
    sx = lambda i: x0 + (i + 0.5) * (x1 - x0) / n
    sy = lambda v: cy1 - (v - pmin) / (pmax - pmin) * (cy1 - cy0)
    d.rect(x0 - 10, cy0 - 14, x1 - x0 + 20, cy1 - cy0 + 28, fill=C["panel"], stroke=C["line"], r=10)
    d.text(x0, cy0 + 4, "EURUSD", 13, "mono400", C["text3"])
    bw = (x1 - x0) / n * 0.6
    for i, (o, hi, lo, c) in enumerate(candles):
        col = GREEN if c >= o else RED
        d.add(f'<line x1="{sx(i):.1f}" y1="{sy(hi):.1f}" x2="{sx(i):.1f}" y2="{sy(lo):.1f}" stroke="{col}" stroke-width="1"/>')
        d.add(f'<rect x="{sx(i) - bw / 2:.1f}" y="{sy(max(o, c)):.1f}" width="{bw:.1f}" height="{max(1, abs(sy(o) - sy(c))):.1f}" fill="{col}"/>')
    rows = [
        ("Strategia A (H4)", [(4, 22, "L"), (34, 52, "L"), (60, 74, "L"), (92, 110, "S")]),
        ("Strategia B (M15)", [(10, 20, "S"), (40, 46, "L"), (62, 70, "L"), (80, 86, "S"), (98, 104, "S"), (112, 118, "L")]),
        ("Strategia C (H1)", [(28, 36, "S"), (41, 47, "L"), (63, 71, "L"), (81, 87, "S"), (99, 105, "S"), (113, 119, "L")]),
    ]
    ry0 = 380
    for k, (name, iv) in enumerate(rows):
        y = ry0 + k * 64
        d.text(x0 - 150, y + 22, name, 13, "sans600")
        d.rect(x0, y + 4, x1 - x0, 28, fill=C["panel"], stroke=C["line"], r=6)
        for a, b, s in iv:
            col = GREEN if s == "L" else RED
            d.rect(sx(a) - 3, y + 8, sx(b) - sx(a) + 6, 20, fill=col, r=4)
            d.text(sx(a) + 4, y + 22, "▲ long" if s == "L" else "▼ short", 10.5, "sans600", "#FFFFFF")
    # zone
    def zone(a, b, k0, k1, lines, lx, ly):
        xa, xb = sx(a) - 8, sx(b) + 8
        ya, yb = ry0 + k0 * 64 - 2, ry0 + k1 * 64 + 38
        d.rect(xa, ya, xb - xa, yb - ya, stroke=C["text"], sw=1.6, r=8, dash="6 4")
        d.flow([((xa + xb) / 2, yb), ((xa + xb) / 2, ly - 16)], "msg")
        d.label(lx, ly, lines, anchor="middle", bg=C["bg"])
    zone(10, 20, 0, 1, ["long e short insieme", "netting o doppio spread?"], sx(15), 640)
    zone(62, 70, 0, 2, ["posizioni che si sommano", "una sola scommessa grande,", "tetto al rischio totale"], sx(66), 640)
    zone(80, 105, 1, 2, ["doppione", "B e C entrano insieme:", "il mix non diversifica"], sx(93), 640)
    # pannello controlli
    px, py = 1200, 140
    d.rect(px, py, 370, 560, fill=C["panel"], stroke=C["line"], r=12)
    d.icon("scale", px + 18, py + 18, 24)
    d.text(px + 52, py + 38, "Controlli del mix", 21, "title")
    d.ok_tag(px + 20, py + 64)
    for i, t in enumerate(["summed / together", "margine: trade rifiutato se non coperto", "correlazioni fra i run", "diversificazione: DD risparmiato"]):
        d.icon("circle-check", px + 20, py + 94 + i * 30, 18, GREEN)
        d.text(px + 46, py + 108 + i * 30, t, 13, "sans400", C["text"])
    d.tag(px + 20, py + 230)
    todo = ["long e short insieme e netting", "posizioni sommate ≤ 3% del capitale", "doppioni: trade sovrapposti < 50%",
            "senza questo run", "pesi per run", "banda Monte Carlo del mix", "holdout del mix"]
    for i, t in enumerate(todo):
        d.rect(px + 20, py + 262 + i * 38, 330, 30, fill=C["panel"], stroke=SIM_OR, sw=1.2, r=6, dash="5 4")
        d.text(px + 32, py + 282 + i * 38, t, 13, "sans400", C["text"])
    d.text(x0 - 150, 760, "per ora un mix lavora su un solo strumento · le correlazioni fra strumenti diversi sono in roadmap", 13, "sans400", C["text2"])
    d.legend_bar(H - 56, extra_states=False, note="▲ long verde · ▼ short rosso, come nel viewer")
    return d


# ------------------------------------------------------------------ 5
def g5_sequenza():
    W, H = 1300, 1580
    d = FD(W, H, "parity-deriva · prima di andare live",
           "Diagramma di sequenza fra Trade DEMO, Archivio e Trade REAL: record demo, push del form con il record, "
           "giudizio della promozione con la checklist, prova salvata, verdetto, abilitazione e avvio.")
    d.header("Prima di andare live", "la promozione la giudica il server reale, non chi la chiede", "processo · 5 di 8")
    cols = [(210, "Trade DEMO", "chart-candle", ST["DEMO"]["stroke"]), (600, "Archivio", "database", C["border"]),
            (1000, "Trade REAL", "cash", RED)]
    top, bot = 150, 1420
    for x, t, ic, stk in cols:
        d.rect(x - 130, top, 260, 70, fill=C["panel"], stroke=stk, sw=2 if stk == RED else 1.6, r=12)
        d.icon("server-2", x - 112, top + 20, 30)
        d.text(x - 72, top + 44, t, 22, "title")
        d.add(f'<line x1="{x}" y1="{top + 70}" x2="{x}" y2="{bot}" stroke="{stk}" stroke-width="1.6" stroke-dasharray="4 5"/>')
    xd, xa, xr = 210, 600, 1000

    def num(x, y, n):
        d.add(f'<circle cx="{x}" cy="{y}" r="12" fill="{C["text"]}"/>')
        d.text(x, y + 4.5, str(n), 12, "sans600", C["panel"], "middle")

    y = 290
    num(xd - 150, y, 1)
    d.path(f"M{xd} {y - 10} h60 v34 h-54", "done")
    d.label(xd + 70, y + 12, ["sessione demo", "giorni e trade chiusi"], anchor="start")
    y = 390
    num(xd - 150, y, 2)
    d.flow([(xd, y), (xa - 4, y)], "done")
    d.label((xd + xa) / 2, y - 12, ["sessioni lette ogni 5 minuti"])
    y = 470
    num(xa - 150, y, 3)
    d.box(xa - 110, y - 26, 220, 52, "raccoglie il record demo", None, fill=C["panel"], tsize=13.5)
    y = 570
    num(xa - 150, y, 4)
    d.flow([(xa, y), (xr - 4, y)], "done")
    d.label((xa + xr) / 2, y - 12, ["push form + record", "push_record · token promote"])
    # giudizio
    y = 640
    num(xr - 250, y + 20, 5)
    bx, bw, bh = xr - 225, 450, 330
    d.rect(bx, y, bw, bh, fill=C["panel"], stroke=RED, sw=1.8, r=12)
    d.icon("checklist", bx + 18, y + 18, 24)
    d.text(bx + 52, y + 38, "Giudizio della promozione", 20, "title")
    d.text(bx + 52, y + 58, "promote() sul server reale, con le sue soglie", 12, "sans400", C["text3"])
    items = [("≥ 20 giorni in demo", True), ("≥ 30 trade chiusi", True), ("0 allarmi di parità", True),
             ("solo account demo nel record", True), ("net ≥ 0", False), ("curva dentro la banda Monte Carlo", False),
             ("serie di perdite ≤ la peggiore della scheda", False)]
    for i, (t, ok) in enumerate(items):
        iy = y + 84 + i * 34
        d.icon("square-check" if ok else "square", bx + 20, iy, 22, GREEN if ok else SIM_OR)
        d.text(bx + 52, iy + 16, t, 14, "sans500" if ok else "sans400", C["text"])
        if not ok:
            d.tag(bx + bw - 16, iy + 2, anchor="end")
    y = 1010
    num(xr - 250, y, 6)
    d.box(xr - 170, y - 30, 340, 62, "salva il giudizio come prova", "promotions/*.json, con cosa manca", fill=C["panel"], tsize=13.5, ssize=11.5)
    y = 1100
    num(xa - 150, y, 7)
    d.flow([(xr, y), (xa + 4, y)], "done")
    d.label((xa + xr) / 2, y - 12, ["verdetto: promosso / cosa manca"])
    y = 1180
    num(xr - 250, y, 8)
    d.box(xr - 170, y - 30, 340, 62, "abilitazione del codice a mano", "arriva come bozza", fill=C["panel"], tsize=13.5, ssize=11.5)
    y = 1270
    num(xr - 250, y, 9)
    d.box(xr - 170, y - 30, 340, 62, "avvio: conferma del capitale a rischio", "rifiuta un form non promosso", fill=C["panel"], tsize=13.5, ssize=11.5)
    d.flow([(xr, y + 32), (xr, y + 58)], "todo")
    d.box(xr - 120, y + 58, 240, 44, "25% del target", None, state="RAMP", todo=True, tsize=13.5)
    # protezioni
    d.rect(xr - 225, 1440, 450, 64, fill=RED, r=12)
    d.icon("shield-lock", xr - 205, 1456, 26, "#FFFFFF")
    d.text(xr - 170, 1466, "Protezioni sempre accese", 15, "sans600", "#FFFFFF")
    d.text(xr - 170, 1488, "loss limit 3% al giorno · stop all · allarme di parità halt", 12.5, "sans400", "#FBEAE8")
    d.legend_bar(H - 46, extra_states=False, note="☑ c'è già · ☐ da fare")
    return d


# ------------------------------------------------------------------ 6
def g6_stati():
    W, H = 1600, 840
    d = FD(W, H, "parity-deriva · LIVE: ramp, protezioni, sospensione",
           "Diagramma a stati: DEMO, LIVE ramp 25%, LIVE 100%, SUSPENDED, DEAD, con le transizioni e le loro soglie; "
           "sopra, le protezioni a livello server già attive.")
    d.header("LIVE · ramp, protezioni, sospensione", "gli stati di una strategia dopo la promozione", "processo · 6 di 8")
    # contenitore LIVE con la fascia delle protezioni di server
    d.rect(430, 150, 920, 330, fill="#EFEBE2", stroke=C["line"], sw=1.2, r=14)
    d.icon("cash", 448, 166, 24, C["text2"])
    d.text(482, 186, "LIVE", 20, "title")
    d.text(482 + measure("LIVE", 20, "title") + 12, 185, "soldi veri · server reale", 12, "mono400", C["text3"])
    d.rect(450, 206, 880, 46, fill=C["panel"], stroke=C["line"], r=8)
    d.icon("shield-lock", 464, 217, 22, C["text"])
    d.text(496, 234, "livello server", 13.5, "sans600")
    d.ok_tag(600, 221)
    d.text(676, 234, "loss limit 3% al giorno · stop all · allarme di parità (warn / halt)", 13, "sans400", C["text2"])
    def st(x, y, w, h, key, sub, icon):
        d.box(x, y, w, h, ST[key]["label"], sub, state=key, icon=icon, tsize=17, ssize=12.5, sw=2.2)
    st(60, 300, 250, 110, "DEMO", "parametri congelati", "chart-candle")
    st(470, 300, 250, 110, "RAMP", "i primi 30 trade", "rocket")
    st(1060, 300, 250, 110, "LIVE", "capitale target pieno", "cash")
    st(560, 640, 250, 100, "SUSP", "la sessione si ferma", "player-pause")
    st(1060, 640, 250, 100, "DEAD", "scartata", "circle-x")
    d.flow([(310, 355), (470, 355)], "done")
    d.label(390, 342, ["promozione"])
    d.flow([(720, 355), (1060, 355)], "todo")
    d.label(890, 342, ["30 trade dentro la banda"])
    d.text(890, 452, "da LIVE ramp o LIVE 100%", 12, "sans500", C["text3"], "middle")
    d.flow([(685, 480), (685, 640)], "susp")
    d.label(697, 540, ["sotto il 5° percentile della banda", "oppure serie di perdite > 1.5× la peggiore"], AMBER, anchor="start")
    d.flow([(1185, 480), (1185, 640)], "kod")
    d.label(1197, 560, ["drawdown > 1.5×", "max DD della scheda"], RED, anchor="start")
    d.flow([(810, 690), (1060, 690)], "kod")
    d.label(935, 677, ["seconda volta"], RED)
    d.flow([(560, 690), (185, 690), (185, 410)], "susp")
    d.label(372, 695, ["prima volta: si riverifica"], AMBER)
    d.legend_bar(H - 46, extra_states=False, note="tutte le transizioni verso SUSPENDED e DEAD sono da fare")
    return d


# ------------------------------------------------------------------ 7
def g7_montecarlo():
    W, H = 1600, 900
    d = FD(W, H, "parity-deriva · la banda Monte Carlo",
           "Banda 5°–95° percentile ottenuta rimescolando i trade della simulazione, con la mediana; una curva demo/live "
           "che resta dentro la banda e una che scende sotto il 5° percentile verso SUSPENDED; linea di fine ramp a 30 trade.")
    d.header("La banda Monte Carlo", "il metro della demo e del live, dalla scheda di riferimento", "processo · 7 di 8")
    rnd = random.Random(3)
    sim = [1.6 if rnd.random() < 0.45 else -1.0 for _ in range(300)]  # trade della simulazione, in R
    N, runs, cap0, risk = 60, 2000, 10000, 100
    paths = []
    for _ in range(runs):
        s = rnd.sample(sim, N)
        eq = [cap0]
        for r in s:
            eq.append(eq[-1] + r * risk)
        paths.append(eq)
    pct = lambda k, q: sorted(p[k] for p in paths)[int(q * (runs - 1))]
    lo = [pct(k, 0.05) for k in range(N + 1)]
    hi = [pct(k, 0.95) for k in range(N + 1)]
    md = [pct(k, 0.5) for k in range(N + 1)]
    # curva verde: una permutazione vicina alla mediana
    best = min(paths, key=lambda p: sum(abs(a - b) for a, b in zip(p, md)))
    green = best
    # curva rossa: segue la simulazione fino a 28 trade, poi una serie di perdite
    red = list(best[:29])
    seq = [-1, -1, 1.6, -1, -1, -1, 1.6, -1, -1, -1, -1, -1, -1, 1.6, -1, -1, -1, -1, -1, -1]
    for r in seq:
        red.append(red[-1] + r * risk)
        if red[-1] < lo[len(red) - 1]:
            break
    cross = len(red) - 1
    for r in (-1, 1.6, -1):   # due o tre trade dopo l'uscita dalla banda, poi la sessione si ferma
        red.append(red[-1] + r * risk)
    x0, x1, y0, y1 = 150, 1250, 170, 740
    vmin = min(min(lo), min(red)) - 200; vmax = max(hi) + 200
    sx = lambda k: x0 + k / N * (x1 - x0)
    sy = lambda v: y1 - (v - vmin) / (vmax - vmin) * (y1 - y0)
    d.rect(x0, y0, x1 - x0, y1 - y0, fill=C["panel"], stroke=C["line"], r=0)
    for k in range(0, N + 1, 10):
        d.add(f'<line x1="{sx(k):.1f}" y1="{y0}" x2="{sx(k):.1f}" y2="{y1}" stroke="{C["line"]}"/>')
        d.text(sx(k), y1 + 20, str(k), 12, "mono400", C["text3"], "middle")
    step = 500
    v = math.ceil(vmin / step) * step
    while v <= vmax:
        d.add(f'<line x1="{x0}" y1="{sy(v):.1f}" x2="{x1}" y2="{sy(v):.1f}" stroke="{C["line"]}"/>')
        d.text(x0 - 10, sy(v) + 4, f"{v:,.0f}".replace(",", " "), 12, "mono400", C["text3"], "end")
        v += step
    d.text((x0 + x1) / 2, y1 + 46, "numero di trade", 13, "sans500", C["text2"], "middle")
    d.text(x0, y0 - 14, "capitale", 13, "sans500", C["text2"])
    band = "M" + " L".join(f"{sx(k):.1f} {sy(hi[k]):.1f}" for k in range(N + 1)) + " L" + \
           " L".join(f"{sx(k):.1f} {sy(lo[k]):.1f}" for k in range(N, -1, -1)) + " Z"
    d.add(f'<path d="{band}" fill="#E3DED3" fill-opacity="0.85"/>')
    d.add(f'<path d="M' + " L".join(f"{sx(k):.1f} {sy(md[k]):.1f}" for k in range(N + 1)) +
          f'" fill="none" stroke="{C["text3"]}" stroke-width="1.6" stroke-dasharray="6 5"/>')
    d.add(f'<path d="M' + " L".join(f"{sx(k):.1f} {sy(green[k]):.1f}" for k in range(N + 1)) +
          f'" fill="none" stroke="{GREEN}" stroke-width="2.6" stroke-linejoin="round"/>')
    d.add(f'<path d="M' + " L".join(f"{sx(k):.1f} {sy(red[k]):.1f}" for k in range(len(red))) +
          f'" fill="none" stroke="{RED}" stroke-width="2.6" stroke-linejoin="round"/>')
    d.add(f'<circle cx="{sx(cross):.1f}" cy="{sy(red[cross]):.1f}" r="7" fill="{RED}" stroke="{C["panel"]}" stroke-width="2"/>')
    # fine ramp
    d.add(f'<line x1="{sx(30):.1f}" y1="{y0}" x2="{sx(30):.1f}" y2="{y1}" stroke="{C["text"]}" stroke-width="1.6" stroke-dasharray="3 4"/>')
    d.label(sx(30), y0 + 26, ["fine ramp: 25% → 100%", "se dentro la banda"])
    # etichette a destra
    lx = 1280
    def lab(y, col, t, s, dash=None, fill=None):
        if fill:
            d.rect(lx, y - 12, 40, 16, fill=fill, r=3)
        else:
            dd = f' stroke-dasharray="{dash}"' if dash else ""
            d.add(f'<line x1="{lx}" y1="{y - 4}" x2="{lx + 40}" y2="{y - 4}" stroke="{col}" stroke-width="2.6"{dd}/>')
        d.text(lx + 52, y, t, 13.5, "sans600", col if col != C["text3"] else C["text"])
        for i, l in enumerate(s):
            d.text(lx + 52, y + 19 + i * 17, l, 12.5, "sans400", C["text2"])
    lab(sy(hi[N]) + 10, C["text3"], "banda 5°–95° percentile", ["trade della simulazione", "rimescolati"], fill="#E3DED3")
    lab(sy(md[N]) + 50, C["text3"], "mediana della simulazione", [], dash="6 5")
    lab(sy(green[N]) + 90, GREEN, "demo / live in linea", ["→ avanti"])
    lab(sy(red[-1]) + 20, RED, "sotto il 5° percentile", [f"al trade {cross} → SUSPENDED"])
    d.legend_bar(H - 46, extra_states=False, note="curve d'esempio: 300 trade simulati (45% vinti a +1.6R, persi a −1R), 2000 rimescolamenti")
    return d


# ------------------------------------------------------------------ 8
def g8_roadmap():
    W, H = 1700, 860
    d = FD(W, H, "parity-deriva · cosa c'è e cosa manca nel processo",
           "Roadmap in cinque colonne (Strategia, SIM, Mix, DEMO, LIVE) con tessere di quello che c'è già e di quello da fare, "
           "e in basso l'ordine dei cantieri.")
    d.header("Cosa c'è e cosa manca", "il processo fase per fase · in basso l'ordine dei cantieri", "processo · 8 di 8")
    cols = [
        ("Strategia", "file-code", None, ["codice + DESCRIPTION", "bozze degli assistenti AI via MCP"],
         ["ipotesi e 'non opera quando'", "scheda con stato e versione"]),
        ("SIM", "repeat", "SIM", ["sweep fino a 500 combinazioni", "paramEffects e score", "pagina del run con heatmap"],
         ["holdout", "motore correlazioni", "baseline casuale", "banda Monte Carlo"]),
        ("Mix", "stack-2", None, ["summed / together", "correlazioni e diversificazione", "verify sull'archivio"],
         ["long/short insieme e netting", "doppioni", "senza questo run", "pesi"]),
        ("DEMO", "chart-candle", "DEMO", ["stesso broker del live", "monitor di parità", "record demo"],
         ["confronto con la scheda", "mix intero in demo"]),
        ("LIVE", "cash", "LIVE", ["promozione giudicata dal server reale", "loss limit 3%", "stop all"],
         ["gate su net e banda", "ramp 25%", "protezioni per strategia", "avvisi"]),
    ]
    cw, gap, x0, y0 = 312, 14, 28, 140
    for i, (t, ic, stt, done, todo) in enumerate(cols):
        x = x0 + i * (cw + gap)
        d.rect(x, y0, cw, 500, fill="#EFEBE2", stroke=C["line"], r=12)
        if stt:
            d.state_chip(x + cw - 16 - measure(ST[stt]["label"], 11.5, "sans600") - 18, y0 + 18, stt)
        d.icon(ic, x + 16, y0 + 16, 26, C["text"])
        d.text(x + 52, y0 + 38, t, 22, "title")
        y = y0 + 70
        for s in done:
            d.rect(x + 14, y, cw - 28, 48, fill=GREEN, r=8)
            d.icon("circle-check", x + 26, y + 14, 20, "#FFFFFF")
            for j, l in enumerate(wrap(s, cw - 90, 13, "sans500")):
                d.text(x + 54, y + 29 - (len(wrap(s, cw - 90, 13, "sans500")) - 1) * 8 + j * 16, l, 13, "sans500", "#FFFFFF")
            y += 58
        y += 8
        for s in todo:
            d.rect(x + 14, y, cw - 28, 48, fill=C["panel"], stroke=SIM_OR, sw=1.6, r=8, dash="6 4")
            d.icon("square", x + 26, y + 14, 20, SIM_OR)
            d.text(x + 54, y + 29, s, 13, "sans500", C["text"])
            y += 58
    # cantieri
    steps = ["gate in promote()", "scheda", "mix", "holdout", "correlazioni", "Monte Carlo", "mix live", "protezioni live"]
    nums = ["1", "2", "3-4", "5", "6", "7", "8", "9"]
    y = 690
    d.text(28, y - 22, "ordine dei cantieri", 13, "sans600")
    x = 28
    for k, (n, s) in enumerate(zip(nums, steps)):
        w = measure(s, 13, "sans500") + 60
        d.rect(x, y, w, 40, fill=C["panel"], stroke=SIM_OR, sw=1.5, r=20)
        d.add(f'<circle cx="{x + 20}" cy="{y + 20}" r="13" fill="{SIM_OR}"/>')
        d.text(x + 20, y + 24.5, n, 11 if len(n) > 1 else 12, "sans600", "#FFFFFF", "middle")
        d.text(x + 40, y + 25, s, 13, "sans500")
        x += w
        if k < len(steps) - 1:
            d.flow([(x + 4, y + 20), (x + 26, y + 20)], "msg")
            x += 30
    d.legend_bar(H - 46, extra_states=False, note="piccoli: giorno della settimana nello sweep, commissioni e financing, colonna R")
    return d


ALL = [("1-processo-completo", g1_processo), ("2-loop-simulazione", g2_loop), ("3-sviluppo-holdout", g3_holdout),
       ("4-mix-stesso-strumento", g4_mix), ("5-prima-di-andare-live", g5_sequenza), ("6-stati-e-protezioni-live", g6_stati),
       ("7-banda-monte-carlo", g7_montecarlo), ("8-cosa-ce-e-cosa-manca", g8_roadmap)]

if __name__ == "__main__":
    import os, sys
    out = sys.argv[1] if len(sys.argv) > 1 else "out_processo"
    only = sys.argv[2:] or None
    os.makedirs(out, exist_ok=True)
    for name, fn in ALL:
        if only and not any(name.startswith(o) for o in only):
            continue
        print(name, fn().save(f"{out}/parity-deriva_processo_{name}.svg"))
