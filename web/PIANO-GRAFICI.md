# Piano: gesti veloci sui grafici a candele (run e live)

## Contesto

I grafici a candele sono due: la pagina **run** (`web/static/app.js`) e la pagina **live** (`web/static/livechart.js`).
Sono disegnati a mano su un canvas, senza librerie. È una scelta voluta, spiegata in cima a `app.js`.

Oggi (26/09) ho già aggiunto zoom e pan sugli assi. L'utente chiede di fare **tutte e 5** le idee che avevo proposto per lavorare più veloce, con i dettagli che avevo già detto.

Obiettivo: leggere un backtest o una sessione live senza perdere tempo. Spostarsi, misurare, zoomare su un pezzo, saltare a un periodo, tutto con mouse e tastiera.

Testi delle pagine in inglese, piano in italiano.

## Già fatto (non ancora committato)

| Gesto | Effetto | Dove |
|---|---|---|
| Rotella sul grafico | zoom sul tempo, attorno alla barra sotto il mouse | run + live (c'era già) |
| Trascini il grafico | pan sul tempo; con i prezzi manuali anche su e giù | run + live |
| Trascini l'asse dei tempi | a destra meno barre, a sinistra di più; l'ultima barra resta ferma | run + live |
| Rotella sull'asse dei prezzi, o shift + rotella | zoom sui prezzi attorno al prezzo sotto il mouse | run + live |
| Trascini l'asse dei prezzi | in su allarga, in giù stringe | run + live |
| Doppio clic sull'asse dei prezzi | prezzi di nuovo automatici | run + live |
| "show the whole range" / Esc | riporta tutto, prezzi compresi | run |
| Bottone "fit prices" | compare solo con la scala manuale | live |

Il codice che c'è già e che va riusato:
- `app.js`:
  - stato: `state.scale`, `state.range`
  - funzioni: `setScale()`, `zone(event)`, `showReset()`, `zoomTo()`, `setViewByTime()`, `viewTimes()`, `barUnder()`, `retune()`/`tune()`, `resetView()`, `equityPoints()`, `runBarAt()`, `pipSize()`, `price()`, `stamp()`, `fitCanvas()`
  - costanti: `AXIS`, `ZOOM_STEP`, `MIN_BARS`, `PAN_SLOP`, `AXIS_DRAG`
- `livechart.js`: `state.scale`, `state.range`, `setScale()`, `zone(cv, event)`, `zoomTo()`, `follow()`, `barAt()`, `barUnder()`, `hairline()`, `fit()`, `state.pip`, la casella "segui" (`#chart-follow`)
- `menu.js`: `palette()` e `DASHES`, già condivisi dalle due pagine. Qui vanno i pochi helper comuni.
- Durante un drag `zoomTo()` di `app.js` non chiama `retune()`: la serie di barre più fini la chiede il mouseup.

## Passo 0: salvare il piano nel progetto

Copiare questo file in `parity_deriva/web/PIANO-GRAFICI.md`, accanto a `REFACTORING-claude-code.md`.

## Passo 1: un canvas trasparente sopra il grafico (serve a 2, 3 e 4)

Mirino, misura e riquadro cambiano a ogni movimento del mouse. Ridisegnare tutto il grafico (fino a 17.000 candele) a ogni movimento costa troppo. Quindi un secondo canvas trasparente sopra il grafico, ridisegnato da solo.

- `run.html`: `<canvas id="chart">` va dentro `<div class="chart-stack">` con `<canvas id="chart-over" aria-hidden="true">`.
- `live.html`: lo stesso con `#live-chart` e `#live-over`.
- `app.css`:
  - `.chart-stack { position: relative }`
  - `.chart-stack .over { position: absolute; inset: 0; pointer-events: none; background: transparent; border: 1px solid transparent; }`
  - Il bordo trasparente serve a far coincidere l'area interna con quella del grafico sotto.
  - **Attenzione**: `app.css` lo modifica anche un'altra sessione. Solo modifiche a stringa esatta, mai riscrivere il file.
- Ogni `draw()` ridimensiona anche l'overlay con `fitCanvas()`/`fit()` e poi chiama `drawOver()`.
- `drawOver()` pulisce e disegna mirino, misura e riquadro, se ci sono.

## Passo 2: mirino con prezzo e ora sugli assi (run + live)

- Una linea verticale e una orizzontale tratteggiate sotto il mouse, solo quando il mouse è nell'area delle candele.
- Un'etichetta col **prezzo** sotto il mouse, sull'asse dei prezzi a sinistra. Il prezzo si calcola da `state.range` e dalla y.
- Un'etichetta con **data e ora** della barra sotto il mouse, sull'asse dei tempi in basso (`stamp()`).
- Le etichette sono riquadri pieni (colore del testo, scritta color pannello). L'helper comune `axisTag(ctx, pal, text, x, y, side)` va in `menu.js`.
- Si nasconde con mouseleave e durante pan e drag degli assi.
- Pagina live: la linea verticale (`hairline`) sul grafico e sulla striscia dello skew resta com'è. Sull'overlay si aggiungono la linea orizzontale e le due etichette.
- Nel mousemove si salva solo `pointer = {x, y}` e si chiama `drawOver()`, mai `draw()`.

## Passo 3: misura con shift + trascina (run + live)

- Shift + mousedown nell'area delle candele fa partire una misura invece del pan.
- Il punto A e il punto B si salvano in **coordinate dei dati** (`{ms, price}`), così la misura resta giusta anche dopo zoom e pan.
- Sull'overlay:
  - un rettangolo da A a B, verde se B sta sopra A, rosso se sta sotto, con alpha 0.12
  - un'etichetta vicino a B: `+23.4 pips · +0.21% · 14 M15 bars · 3h 30m`
- Come si calcolano i numeri:
  - pip = Δprezzo / `pipSize()` sulla run, / `state.pip` sulla live
  - % = Δprezzo / prezzo di A
  - barre = barre della serie disegnata, col suo timeframe (`drawnAt()`)
  - durata = |msB − msA| in giorni, ore e minuti
- Gli stessi numeri vanno anche nella riga di lettura: `#chart-zoom` sulla run, `#chart-hover` sulla live.
- Dopo il mouseup la misura resta visibile. Sparisce al mousedown successivo o con Esc.
- `dragged = true`, così il clic finale non seleziona un trade.
- Shift + rotella resta lo zoom dei prezzi: nessun conflitto.

## Passo 4: zoom a riquadro con ctrl + trascina (run + live)

- Ctrl (o ⌘ su Mac) + mousedown nell'area delle candele disegna un rettangolo tratteggiato sull'overlay.
- Al mouseup, se i due lati sono almeno 8 px:
  - tempo: da `barUnder()` dei due bordi, poi `zoomTo(from, span)` (rispetta `MIN_BARS`)
  - prezzi: dalle due y, poi `setScale()`
- Rettangolo troppo piccolo: non succede niente. Esc durante il drag annulla.
- `contextmenu` con ctrl premuto va bloccato con `preventDefault`: su Mac ctrl+clic apre il menu contestuale.
- Pagina run: dopo il mouseup il `retune()` che c'è già chiede le barre più fini.
- Pagina live: toglie la spunta a "segui".

## Passo 5: tastiera (run + live, stessi tasti)

| Tasto | Effetto |
|---|---|
| shift + ← / shift + → | pan di 1/4 della finestra |
| + (o =) / − | zoom ×1.25 attorno al centro |
| Home / End | all'inizio / alla fine, stessa ampiezza |
| F | prezzi automatici (`state.scale = null`) |
| Esc | run: come oggi (reset) più la misura cancellata; live: fit prices più la misura cancellata |

- Le frecce da sole **no**: sulla run passano già alla run prima e dopo (`walkRuns()` in `app.js`).
  - Quel gestore va cambiato: deve ignorare anche `shiftKey`, altrimenti shift+← cambierebbe run.
- Tasti ignorati se il fuoco è in input, select, textarea o button, se c'è ctrl, alt o meta, o se è aperto un `dialog`.
- Helper comune `chartKey(event)` in `menu.js`: restituisce `'left' | 'right' | 'in' | 'out' | 'home' | 'end' | 'fit' | null`. Ogni grafico poi usa il suo `zoomTo()` e il suo `setScale()`.
- Pagina live: pan e Home tolgono la spunta a "segui", End la rimette.

## Passo 6: bottoni di intervallo

- **Run**: `1D 1W 1M 3M` in `#chart-tools`, prima di "show the whole range", che fa da "tutto".
  - La finestra finisce sull'ultima barra che vedi. Se prima non c'è abbastanza run, parte dall'inizio della run.
  - Nuova funzione `showTimes(fromMs, toMs)` in `app.js`:
    1. Se la finestra non sta dentro la serie fine caricata, torna alle barre della run (`state.series = null`).
    2. Poi `setViewByTime()`, `draw()` e `retune(true)`.
  - Detto qui perché è un bug facile: senza il punto 1, `setViewByTime()` si ferma ai bordi della serie fine già caricata e "3M" mostrerebbe solo quel pezzo.
  - Altro dettaglio: su una run H4, "1D" sono 6 barre e `MIN_BARS` (8) la allargherebbe a 32 ore. Quindi `tune()` deve accettare una finestra da fuori: `showTimes()` gli passa quella esatta, così 1D resta un giorno, su barre più fini.
- **Live**: `1h 4h 1D all` in `#chart-controls`, con la finestra che finisce sull'ultima barra.
  - Rimettono la spunta a "segui".
  - "all" = `view = null`.
  - Per convertire i tempi in barre: `barAt(ultimo − durata)`.

## Passo 7: navigatore sulla curva del capitale (solo run)

Serve a saltare da un anno all'altro in un colpo.

- Nuovo `<canvas id="chart-nav" height="44">` subito sotto `#chart`, sopra `#curve-panel`. Il grafico del capitale vero sta molto più in basso ed è un `<details>` che si può chiudere, quindi da solo non basta.
- Cosa disegna:
  - la curva del capitale di tutta la run (`equityPoints()`, a gradini come in `drawEquity()`, linea sottile)
  - se la run ha meno di 2 trade chiusi, le chiusure delle barre della run, in tenue
  - la finestra visibile come riquadro, con la parte fuori ombreggiata
  - asse x = barre della run, come in `drawEquity()`
  - finestra calcolata con `runBarAt()` dei due estremi di `viewTimes()`
- Gesti:
  - trascini dentro il riquadro: sposti la finestra
  - trascini un bordo (±6 px): allarghi o stringi quel lato
  - clic fuori: centri la finestra lì
  - rotella: zoom come sul grafico
  - cursori: `grab`, `ew-resize`, `pointer`
- Tutto passa da `showTimes()`. Mentre trascini usa le barre della run, che sono veloci. Al mouseup chiama `retune()`.
- Si ridisegna alla fine di ogni `draw()`: costa poco, una sola linea.
- Sul grafico del capitale in basso (`drawEquity()`) ombreggio la stessa finestra, solo come disegno. Il clic che seleziona un trade resta com'è.
- Nascosto se la run non ha candele.

## Passo 8: guide

- `run.html`, lista della guida (?): una riga ciascuno per mirino, shift+trascina, ctrl+trascina, tasti, bottoni di intervallo e navigatore.
- `live.html`: `title` su `#live-chart` con l'elenco corto dei gesti. La pagina live non ha una guida.

## File toccati

- `web/static/app.js`: passi 1–7, la parte più grossa
- `web/static/livechart.js`: passi 1–6
- `web/static/menu.js`: `axisTag()`, `chartKey()`
- `web/static/run.html`, `web/static/live.html`: overlay, navigatore, bottoni, guida
- `web/static/app.css`: overlay, navigatore, gruppo di bottoni (a stringa esatta, c'è un'altra sessione)
- Niente Python: non serve riavviare il server, i file statici si leggono dal disco a ogni richiesta.

## Fuori dal piano

- Touch e pinch su telefono e tablet: servirebbero i pointer events su entrambi i grafici. Da fare a parte, se serve.
- Già esistente, da tenere presente: sull'ultimo trade provato (run WK01, trade 163) il target lontano porta la scala a 1.07–2.37 e schiaccia le candele. Adesso si rimedia con la scala manuale.

## Verifica

Dopo ogni passo:
1. `node --check` sui `.js` toccati.
2. Uno script CDP in scratchpad (Chrome headless con `--remote-debugging-port=9333` e il WebSocket di Node 22, come oggi) con eventi mouse e tastiera veri, `Input.dispatchMouseEvent` e `Input.dispatchKeyEvent`:
   - **run**: set `20260926-004422-eb583a`, run 1 (è su disco, aprirla non fa partire backtest). Si legge `state` dalla console (`state.view`, `state.scale`, `state.selected`).
   - **live**: sul dev non ci sono sessioni, quindi candele finte con `LiveChart.setData()` e prezzi arrotondati a 5 decimali (con più decimali le etichette escono illeggibili).
   - Controlli:
     - mirino ed etichette nello screenshot
     - numeri della misura giusti su un caso noto
     - riquadro → `state.view` e `state.scale` attesi
     - ogni tasto → finestra attesa, e shift+← che **non** cambia run
     - 1D/1W/1M/3M → durata giusta anche su run H4
     - navigatore: drag, bordi, clic fuori
     - nessun `Runtime.exceptionThrown`
3. Screenshot finali a 1300 px e a 380 px, senza scroll orizzontale.
4. `git diff --stat` solo sui file sopra. Niente commit e niente prod finché l'utente non lo dice.

## Stato (26/09/2026): fatto, non committato

Tutti i passi 0–8 sono fatti e provati in Chrome headless (run WK01 M15, run H401 H4, live con candele finte). Rispetto al piano:

- **Pip corretto**: `pipSize()` in `app.js` prima prendeva i decimali dalle candele, che sono prezzi medi e hanno un decimale in più (1.171125). Così i pip uscivano 10 volte troppi, anche nel valore "H-L … pips" che c'era già. Adesso prende i decimali dalle colonne ask/bid (`c[5]`), quelli quotati dal broker (`state.quoted`). I prezzi sul grafico restano a 6 decimali.
- **Risposte vecchie scartate**: in `tune()` c'è il contatore `state.asked`. Se le barre fini arrivano dopo che il grafico è stato già spostato, non si applicano e si rifà la richiesta. Prima un 1D seguito subito da 1W tornava a 1D, e con la rotella succedeva lo stesso.
- **Pagina live**: l'elenco dei gesti è nel `title` di un "gestures" tra i comandi, non sul canvas, dove il tooltip coprirebbe le candele.
- **Pagina live**: durante misura e riquadro si ridisegna tutto il grafico, così la linea verticale (`hairline`) segue il mouse.
