# Prompt per Claude Code — refactoring grafico del viewer (`web/`)

Copia tutto ciò che sta sotto la riga e incollalo in Claude Code, dalla radice del repo.

---

## Contesto

Il viewer di backtest (`web/service.py` più `web/static/index.html`, `app.css`,
`app.js`) va portato alla nuova identità visiva di parity-deriva: logo "Alba con
deriva", font IBM Plex, colori e componenti già decisi.

È un **refactoring grafico**: il comportamento non cambia. Stesse API, stessi
parametri dell'URL, stessi id e nomi dei campi, stesse scorciatoie da tastiera,
stessi calcoli, stessi testi (in inglese, minuscoli).

Prima di toccare qualunque file leggi, in quest'ordine:

1. `web/DESIGN.md` — **è la specifica**: colori, token, tipografia, componenti.
2. `loghi/definitivo/README.md`, `loghi/definitivo/tokens.css`,
   `loghi/definitivo/font/fonts.css`.
3. Nel `README.md` del repo le sezioni "Reading a backtest: the viewer",
   "No framework, no charting library" e "Behind a reverse proxy".
4. `web/service.py` (handler statico), `web/static/*`, `tests/web_test.py`.

Regola di ingaggio: se ti serve una decisione che non è né in `DESIGN.md` né qui,
**fermati e chiedimela**: non scegliere per me. Vale soprattutto per i colori: non
introdurre nessun colore che non sia un token di `tokens.css`.

## Obiettivo

Il viewer di oggi, identico nel funzionamento, con:

- testata con logo, nome pagina, badge d'ambiente e interruttore del tema;
- barra dei controlli separata dalla testata;
- tema chiaro e scuro che segue il sistema, forzabile dall'interruttore;
- IBM Plex Sans per l'interfaccia e IBM Plex Mono per prezzi, orari, importi e assi;
- tutti i colori letti dai token, in CSS e nel canvas;
- favicon.

## Vincoli tecnici

- Niente framework, niente librerie, niente CDN, niente build step: HTML, CSS e JS
  scritti a mano come oggi. Nessuna riga nuova in `requirements.txt`.
- Python per i test: l'env conda del progetto, mai il python di sistema. Se non lo
  trovi, chiedimi quale usare.
- URL sempre relativi (`static/...`): il viewer deve continuare a funzionare dietro
  un reverse proxy con prefisso.
- Stile del codice: commenti che spiegano il *perché*, come nel resto dei file. Un
  test esistente che cambia si riscrive con la nota Was/Now, non si cancella.
- I file in `loghi/definitivo/` sono generati o ufficiali (font IBM): si copiano,
  non si modificano. Se uno non va, dimmelo.

---

# SPECIFICA

## Parte 1 — Servire i nuovi asset (`web/service.py`)

Oggi l'handler serve solo `.html`, `.css`, `.js` (`CONTENT_TYPES`) e rifiuta
qualunque percorso con una sottocartella. La seconda regola è una scelta di
sicurezza e **resta**.

- Aggiungi a `CONTENT_TYPES` esattamente queste tre voci, nient'altro:
  `'.woff2': 'font/woff2'`, `'.svg': 'image/svg+xml'`, `'.png': 'image/png'`.
  Niente `.txt`, niente `.ico`, niente `mimetypes.guess_type`.
- Aggiorna il commento su `CONTENT_TYPES` e la docstring di `sendFile` (oggi parlano
  di "three files" e "three extensions") con una nota Was/Now che dica perché ora
  ce ne sono sei.
- Non toccare la normalizzazione del percorso, il divieto di `/`, `Content-Length`
  e `Cache-Control: no-store`.
- In `tests/web_test.py`, accanto al test che oggi controlla `app.js` e `app.css`:
  - un `.woff2`, un `.svg` e un `.png` presenti in `web/static/` rispondono 200 con
    il content-type giusto;
  - `/static/OFL-IBM-Plex.txt` e `/static/__init__.py` restano 404;
  - `/static/fonts/IBMPlexSans-Regular.woff2` (sottocartella) resta 404;
  - `test_the_static_handler_does_not_leave_its_directory` passa invariato.

## Parte 2 — File da copiare in `web/static/` (cartella piatta)

| da | a |
|---|---|
| `loghi/definitivo/font/IBMPlexSans-Regular.woff2` | `web/static/IBMPlexSans-Regular.woff2` |
| `loghi/definitivo/font/IBMPlexSans-Medium.woff2` | `web/static/IBMPlexSans-Medium.woff2` |
| `loghi/definitivo/font/IBMPlexSans-SemiBold.woff2` | `web/static/IBMPlexSans-SemiBold.woff2` |
| `loghi/definitivo/font/IBMPlexMono-Regular.woff2` | `web/static/IBMPlexMono-Regular.woff2` |
| `loghi/definitivo/font/IBMPlexMono-Medium.woff2` | `web/static/IBMPlexMono-Medium.woff2` |
| `loghi/definitivo/font/OFL.txt` | `web/static/OFL-IBM-Plex.txt` (non servito: accompagna i font per licenza) |
| `loghi/definitivo/font/fonts.css` | `web/static/fonts.css` (gli URL sono già relativi alla stessa cartella) |
| `loghi/definitivo/tokens.css` | `web/static/tokens.css` |
| `loghi/definitivo/parity-deriva_testata_fondo-chiaro.svg` | `web/static/logo-chiaro.svg` |
| `loghi/definitivo/parity-deriva_testata_fondo-scuro.svg` | `web/static/logo-scuro.svg` |
| `loghi/definitivo/favicon.svg` | `web/static/favicon.svg` |
| `loghi/definitivo/png/favicon-32.png` | `web/static/favicon-32.png` |
| `loghi/definitivo/png/apple-touch-icon.png` | `web/static/apple-touch-icon.png` |

## Parte 3 — `index.html`

**`<head>`**, in quest'ordine: `<meta name="color-scheme" content="light dark">`,
`static/fonts.css`, `static/tokens.css`, `static/app.css`, poi le icone
(`<link rel="icon" href="static/favicon.svg" type="image/svg+xml">`, il PNG a 32 px
come alternativa, `apple-touch-icon`).

Subito dopo, **uno script in linea di poche righe** che legge il tema salvato
(`localStorage`, chiave `parity-deriva.theme`, dentro try/catch) e, se vale `light`
o `dark`, lo scrive in `document.documentElement.dataset.theme`. Deve stare nel
`<head>` e non in `app.js` perché `app.js` si carica in fondo alla pagina: senza,
chi ha forzato il tema vedrebbe per un attimo quello sbagliato. Commentalo così.

**Testata** (`<header>`, alta 60 px), da sinistra:

- `<h1>` che contiene due `<img>`: `static/logo-chiaro.svg` (classe `logo-light`)
  e `static/logo-scuro.svg` (classe `logo-dark`), alt `parity-deriva`, alti 28 px.
  Il CSS ne mostra uno solo a seconda del tema (Parte 4).
- separatore verticale, poi `Backtest` (nome della pagina);
- badge d'ambiente `offline · no orders` (il viewer non manda ordini: README,
  "What it runs, and what it will not");
- a destra, il pulsante dell'interruttore del tema: un vero `<button>` con
  `aria-label` e il testo dello stato corrente (`theme: system`, `theme: light`,
  `theme: dark`).

**Barra dei controlli**: il `<form id="controls">` di oggi esce dalla testata e
diventa una barra a sé subito sotto. **Stessi id, stessi `name`, stessi attributi**
di ogni campo e del fieldset `#params`: `app.js` e l'URL dipendono da quelli.

Tutto il resto resta com'è: `#message`, `#chart-panel`, `#chart`, `#reset`,
`#report-panel`, `#report`, `#counts`, `#equity-panel`, `#equity`, `#trades`,
`#trade-rows`, il `<title>`. Nella legenda del grafico le chiavi diventano `entry`,
`exit`, `target`, `stop`, `stop moved to` (oggi `take profit` e `stop loss`,
mentre sul grafico si legge già `target` e `stop`: così combaciano).

## Parte 4 — `app.css`

- Togli il blocco `:root` di oggi: i valori vengono da `tokens.css`. Segui la tabella
  "Cosa sostituisce cosa" in `DESIGN.md` § 3. Alla fine in `app.css` non deve
  restare **nessun colore esadecimale**.
- `body`: `var(--font-sans)`, 13 px, interlinea 1,45, `font-feature-settings: "zero" 1`.
  In `var(--font-mono)`: celle numeriche (`td.num`), orari, campi numerici e di data,
  indirizzo del servizio.
- Componenti come in `DESIGN.md` § 6: testata, barra dei controlli, messaggi,
  pannelli (`--panel`, filetto 1 px `--line`, raggio 10), indicatori, tabella (testata
  sticky, hover, riga selezionata con barra interna 3 px `--entry`), pillole degli
  esiti, pulsanti, campi, `:focus-visible`.
- `#run` è l'unico elemento con fondo `--accent`. Nessun altro uso dell'ocra oltre a
  focus, link e pulsante ghost.
- Logo per tema, con la stessa struttura a tre blocchi di `tokens.css`:
  `.logo-dark` nascosto di default; con `prefers-color-scheme: dark` e senza
  `[data-theme="light"]` si nasconde `.logo-light` e si mostra `.logo-dark`; con
  `[data-theme="dark"]` lo stesso; con `[data-theme="light"]` il contrario.
- Legenda: `entry` continua `--entry`, `exit` continua `--mark-exit`, `target`
  tratteggiata `--up`, `stop` tratteggiata `--down`, `stop moved to` punteggiata
  `--mark-trail`.
- Colonna side: `.side.long` in `--up`, `.side.short` in `--down`, con un triangolo
  ▲/▼ prima della parola disegnato in CSS (`::before` con i bordi, colore
  `currentColor`). Plex non ha i glifi ▲ ▼ ■: non usarli come testo.
- Il layout resta fluido come oggi (flex con `wrap`).

## Parte 5 — `app.js`

**Colori dal tema.** Una funzione `palette()` legge i token con
`getComputedStyle(document.documentElement).getPropertyValue(...)` e viene chiamata
all'inizio di `draw()` e di `drawEquity()`. Sostituisci ogni colore scritto nel
codice:

| riga di oggi | cosa | diventa |
|---|---|---|
| 155 | campitura del trade `rgba(88,166,255,.07)` | `--trade-span` |
| 176, 378 | griglia `#232932` | `--grid` |
| 181, 285, 383 | testo degli assi `#8b95a6` | `--text-3` |
| 198 | baffi ask-high/bid-low `rgba(139,149,166,.45)` | `--text-3` con `globalAlpha` 0,45 |
| 206 | candele | `--up` / `--down` |
| 249 | target | `--up`, tratteggio invariato |
| 250 | stop | `--down`, tratteggio invariato |
| 257 | stop moved to `#ff9f45` | `--mark-trail`, punteggiato `[2, 3]` invariato |
| 259 | entry `#58a6ff` | `--entry` |
| 260 | exit `#c8a2ff` | `--mark-exit`, spessore 1, `globalAlpha` 0,55 |
| 280, 281 | marcatori entry/exit | vedi sotto |
| 389 | linea di partenza del capitale | `--text-3`, tratteggio invariato |
| 400 | curva del capitale verde/rossa | `--up` / `--down` con la stessa regola di oggi (`last >= start`) |
| 415 | punto del trade selezionato sulla curva | `--entry` |

I numeri di riga sono quelli di oggi: se nel frattempo il file è cambiato, cerca i
valori.

**Marcatori** (`marker` dentro `overlay`): oggi un cerchio di raggio 4 più la linea
verticale di guida al 50 %. Diventano: entry = triangolo in `--entry` che punta in su
per `trade.direction === 'long'` e in giù per `'short'`; exit = quadrato di 8 px in
`--mark-exit`; entrambi con un contorno di 2 px del colore `--panel`. La linea
verticale di guida resta com'è.

**Font nel canvas.** I quattro `ctx.font` / `ectx.font` di oggi
(`11px ui-monospace, ...`) diventano `11px` più il valore di `--font-mono`. Il primo
disegno avviene dopo `document.fonts.ready`: le larghezze delle etichette si misurano
con il font vero, non con quello di riserva. Quando i font sono pronti, ridisegna.

**Tema.** L'interruttore cicla `system → light → dark`: scrive o toglie
`data-theme` su `<html>`, salva la scelta in `localStorage` (try/catch: se fallisce
funziona lo stesso, solo non se la ricorda) e aggiorna il testo del pulsante.
Ridisegna i due canvas quando cambia il tema, sia dall'interruttore sia dal sistema
(`matchMedia('(prefers-color-scheme: dark)')`, evento `change`).

**Non cambia**: dati, chiamate alle API, URL, `writeURL`/`fieldsFromURL`, tastiera
(frecce, `j`/`k`, `escape`), arrotondamenti, testi delle etichette, logica di zoom.

## Parte 6 — Verifica

1. `tests/web_test.py` passa per intero con l'env conda, compresi i test nuovi della
   Parte 1.
2. `grep -nE "#[0-9a-fA-F]{3,8}\b|rgba?\(" web/static/app.js web/static/app.css`
   non trova colori (commenti esclusi).
3. `python scripts/web.py`, poi apri il link del README
   `?instrument=EUR_USD&granularity=H1&strategy=AG01&from=2018-01-01&to=2018-03-03&trade=21`
   e controlla:
   - i file `.woff2` arrivano con `font/woff2` e i testi sono in Plex (Network nel
     browser);
   - tema chiaro e scuro seguendo il sistema; l'interruttore li forza e dopo un
     reload la scelta resta;
   - sul trade 21 zoomato: target, stop, entry, exit (ed eventuale stop moved to)
     con colori e tratteggi della tabella sopra; marcatori ▲/▼ e ■;
   - riga selezionata, curva del capitale, frecce, `j`/`k`, `escape` come prima.
4. Se hai un browser headless a disposizione, fai quattro screenshot (chiaro e scuro,
   intervallo intero e trade zoomato) e mostrameli. Se non ce l'hai, dimmelo e li
   faccio io.

## Parte 7 — Ordine di lavoro

1. Parte 1 (handler e test) — i test devono passare prima di tutto il resto.
2. Parte 2 (copia dei file) e controllo che vengano serviti.
3. Parte 3 e Parte 4 insieme (struttura e stile).
4. Parte 5 (canvas, font, tema).
5. Parte 6 (verifica).

Fermati alla fine di ogni punto e dimmi in due righe cosa hai fatto. Non passare al
punto successivo se i test del punto corrente non passano.

## Cosa non fare

- Non cambiare comportamento, API, id e nomi dei campi, parametri dell'URL,
  scorciatoie, formati dei numeri o testi dell'interfaccia.
- Non inventare colori, non usare l'ocra fuori da logo, `Run`, focus e link, non
  scrivere colori esadecimali in `app.css` o `app.js`.
- Non aggiungere librerie, CDN, framework CSS o un build step.
- Non permettere sottocartelle nell'handler statico e non servire altre estensioni.
- Non modificare i file in `loghi/definitivo/`.
- Nessun simbolo di valuta sui numeri (README, "Two things the numbers are not").
- Non fare commit: li faccio io quando ho visto il risultato.

---

# Aggiunta del 25/09/2026 · parametri sì/no come segmentato `Y | N`

Si può dare a Claude Code da sola, anche se le parti sopra sono già state fatte.

**Contesto.** Nei parametri della strategia i valori sì/no (es. `intraday`) sono
oggi due checkbox nude `Y` e `N`, che si possono spuntare entrambe per provare i
due valori. Hanno l'aspetto di sistema e non stanno nello stile. Roberto ha scelto
il segmentato `Y | N` (proposta 2 di `web/checkbox-proposte.html`, regole in
`web/DESIGN.md` § 6, Campi).

**Cosa fare.**

1. Trova dove si generano i gruppi `Y`/`N` dei parametri (cerca `type = 'checkbox'`
   o `'checkbox'` in `web/static/*.js`, vicino al codice che costruisce i campi dei
   parametri). Dimmi file e funzione prima di cambiarli.
2. Cambia solo la struttura attorno agli input, così:

   ```html
   <div class="field">
     <span class="field-label" id="lbl-intraday">intraday</span>
     <div class="yn" role="group" aria-labelledby="lbl-intraday">
       <label><input type="checkbox" name="intraday" value="1"> Y</label>
       <label><input type="checkbox" name="intraday" value="0"> N</label>
     </div>
   </div>
   ```

   **Tieni `name`, `value`, `id` e gli event listener che il codice usa oggi**: il
   form, l'URL e l'API non devono accorgersi di niente. L'etichetta del gruppo è la
   stessa che il campo ha già; l'`id` dell'etichetta si costruisce dal nome del
   parametro.
3. Aggiungi a `web/static/app.css`, nella sezione dei campi:

   ```css
   /* parametri sì/no: segmentato Y | N (DESIGN.md § 6, Campi) */
   .yn { display: inline-flex; gap: 2px; height: var(--control-h); box-sizing: border-box; padding: 2px; border: 1px solid var(--border); border-radius: var(--radius-s); background: var(--bg); }
   .yn label { position: relative; display: inline-flex; align-items: center; justify-content: center; min-width: 40px; padding: 0 10px; border-radius: 4px; color: var(--text-3); font: 500 13px var(--font-sans); text-transform: none; letter-spacing: 0; cursor: pointer; user-select: none; transition: background-color .12s, color .12s; }
   .yn input { position: absolute; inset: 0; margin: 0; opacity: 0; cursor: pointer; }
   .yn label:hover { color: var(--text); background: var(--line); }
   .yn label:has(input:checked) { color: var(--panel); background: var(--text); font-weight: 600; }
   .yn label:has(input:focus-visible) { outline: 2px solid var(--focus); outline-offset: 1px; }
   .yn label:has(input:disabled) { opacity: .45; cursor: default; }
   .yn:not(:has(input:checked)) { border-color: var(--down); }
   ```

   L'input resta nel DOM (trasparente sopra il segmento): clic, `Tab`, `Spazio` e
   lettori di schermo funzionano come con la checkbox di oggi. Niente colori nuovi:
   solo token.

**Verifica.** `tests/web_test.py` passa; sulla pagina dei parametri il gruppo è alto
quanto i campi accanto; `Y`, `N`, entrambi e nessuno (bordo rosso) in chiaro e in
scuro; `Tab` porta su ogni segmento con l'anello ocra e `Spazio` lo accende e lo
spegne; la simulazione lanciata con entrambi accesi prova ancora i due valori.

Non fare commit.

---

# Aggiunta del 25/09/2026 · testata dei dialog "titolo con contesto"

Si può dare a Claude Code da sola.

**Contesto.** I dialog (`index.html`: data, runs, live, help; `sim.html`: sets, run,
analysis; `live.html`: sim) aprono tutti con
`<form method="dialog" class="dialog-head"><strong>…</strong><button …>close</button></form>`.
Oggi la testata è una fascia di colore diverso staccata dalla tabella, la chiusura è
un cerchio con fondo proprio, il titolo non dice né quanti elementi ci sono né che
cosa se ne fa. Roberto ha scelto la proposta 2 di `web/dialog-testata-proposte.html`
(regole in `web/DESIGN.md` § 6, Dialog).

**Cosa fare.**

1. Per ogni dialog, stessa struttura. Tieni gli `id` esistenti, il
   `<form method="dialog">` (è lui che chiude) e tutto quello che `app.js`, `sim.js`
   e `live.js` leggono o scrivono:

   ```html
   <dialog id="sets-dialog" class="sheet" aria-labelledby="sets-title">
     <form method="dialog" class="dialog-head">
       <div class="dialog-heading">
         <h2 class="dialog-title" id="sets-title">simulation sets <span class="dialog-count" id="sets-count"></span></h2>
         <p class="dialog-sub">kept on disk, newest first · click a row to open it · names can be edited in place</p>
       </div>
       <div class="dialog-tools">
         <!-- pulsanti propri del dialog, con classe dei secondari -->
         <span class="kbd" aria-hidden="true">esc</span>
         <button type="submit" class="dialog-close" aria-label="close">
           <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
         </button>
       </div>
     </form>
     <div class="dialog-body flush"> …contenuto di oggi… </div>
   </dialog>
   ```

   - La riga d'aiuto di ogni dialog: ricavala dal commento HTML che lo precede
     (inglese, minuscolo, frasi brevi separate da ` · `). **Fammi vedere gli otto
     testi prima di scriverli.**
   - `dialog-body flush` per i dialog che sono una tabella (sets, runs, sim, run con
     l'iframe); `dialog-body` semplice per gli altri.
   - Conteggio (`.dialog-count`) solo dove c'è una lista: sets, runs, sim. Lo scrive
     la funzione che riempie la lista (es. `openSets()` in `sim.js`), vuoto finché
     la lista non è arrivata.
   - Dialog di un run (`#run-title`, e `#analysis-title` allo stesso modo): il titolo
     diventa `run <span class="id">41/17</span>` e i parametri (`paramsText`) vanno
     in un `<p class="dialog-sub mono">` sotto, separati da ` · `.
2. In `web/static/app.css` togli le regole di oggi su `.dialog-head` e la fascia di
   colore della testata; aggiungi:

   ```css
   /* dialog: foglio e testata "titolo con contesto" (DESIGN.md § 6, Dialog) */
   dialog.sheet { box-sizing: border-box; padding: 0; border: 1px solid var(--line); border-radius: var(--radius-m); background: var(--panel); color: var(--text); overflow: hidden; }
   dialog.sheet[open] { display: flex; flex-direction: column; }
   dialog.sheet::backdrop { background: var(--backdrop); } /* token in tokens.css: nessun rgba in app.css */
   .dialog-head { flex: none; display: flex; align-items: center; gap: 16px; margin: 0; padding: 13px 12px 13px 20px; border-bottom: 1px solid var(--line); }
   .dialog-heading { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
   .dialog-title { display: flex; align-items: center; gap: 8px; margin: 0; min-width: 0; white-space: nowrap; } /* font e misura: aggiunta "font dei titoli" */
   .dialog-count { flex: none; font: 500 11px/18px var(--font-mono); color: var(--text-2); padding: 0 7px; border: 1px solid var(--line); border-radius: var(--radius-pill); }
   .dialog-count:empty { display: none; }
   .dialog-sub { margin: 0; font-size: var(--fs-small); color: var(--text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
   .dialog-sub.mono { font-family: var(--font-mono); }
   .dialog-tools { margin-left: auto; flex: none; display: flex; align-items: center; gap: 8px; }
   .kbd { font: 500 11px/18px var(--font-mono); color: var(--text-3); padding: 0 5px; border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 4px; }
   .dialog-close { flex: none; width: var(--control-h); height: var(--control-h); display: grid; place-items: center; padding: 0; border: 0; border-radius: var(--radius-s); background: transparent; color: var(--text-2); cursor: pointer; transition: background-color .12s, color .12s; }
   .dialog-close:hover { background: var(--line); color: var(--text); }
   .dialog-close:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
   .dialog-body { min-height: 0; flex: 1; overflow: auto; padding: 16px 20px 20px; }
   .dialog-body.flush { padding: 0; }
   .dialog-body.flush th:first-child, .dialog-body.flush td:first-child { padding-left: 20px; }
   ```

   Le larghezze e altezze di oggi per singolo dialog (`#sets-dialog`, `#run-dialog`,
   …) restano. Font e misura del titolo sono nell'aggiunta successiva, "font dei
   titoli e testata più stretta": falle insieme.

**Verifica.** `tests/web_test.py` passa; ogni dialog si apre e si chiude con la X,
con `esc` e (dove c'è) con il suo pulsante; il titolo è annunciato (in DevTools,
Accessibility: il dialog ha nome); conteggio e riga d'aiuto corretti in sets, runs e
sim; titolo e prima colonna allineati; in chiaro e in scuro. Screenshot di sets e run
in entrambi i temi, se hai un browser headless.

Non fare commit.

---

# Aggiunta del 25/09/2026 · font dei titoli e testata più stretta

Si può dare a Claude Code da sola; se c'è anche l'aggiunta sulla testata dei dialog,
falle insieme.

**Contesto.** Con tutto in Plex a 15 px nome della pagina, titoli dei dialog e dei
pannelli si confondono con il resto. Roberto ha scelto un carattere solo per i
titoli, **PD Instrument** (Instrument Serif allargato ×1,08, file in
`loghi/definitivo/font/`), e una testata con il nome PARITY-DERIVA più stretto
(0,07 em invece di 0,16, con crenatura). Regole in `web/DESIGN.md` § 4, § 6 (Testata,
Dialog, Titoli dei pannelli) e § 7.

**Cosa fare.**

1. Copia in `web/static/` (piatto, come nella Parte 2) e **sovrascrivi** dove c'è già:
   - `loghi/definitivo/font/PDInstrument-Regular.woff2` → `PDInstrument-Regular.woff2`
   - `loghi/definitivo/font/OFL-Instrument-Serif.txt` → `OFL-Instrument-Serif.txt`
   - `loghi/definitivo/font/fonts.css` → `fonts.css` (ha il `@font-face` nuovo)
   - `loghi/definitivo/tokens.css` → `tokens.css` (ha `--font-title`, `--fs-h-page`,
     `--fs-h-dialog`, `--fs-h-panel`, `--tracking-title`)
   - `loghi/definitivo/parity-deriva_testata_fondo-chiaro.svg` → `logo-chiaro.svg`
   - `loghi/definitivo/parity-deriva_testata_fondo-scuro.svg` → `logo-scuro.svg`

   Se in `web/static/` ci sono copie modificate di `fonts.css` o `tokens.css`, non
   sovrascriverle: dimmelo e fondi a mano. La nuova testata è più stretta (viewBox
   408 × 64 invece di 460 × 64): l'altezza resta 28 px, la larghezza la calcola il
   browser; controlla che niente la fissi in px.
2. In `web/static/app.css`, dopo le regole della tipografia:

   ```css
   /* titoli: nome della pagina, dialog, pannelli (DESIGN.md § 4) */
   .page-title, .dialog-title, .panel-title { margin: 0; font-family: var(--font-title); font-weight: 400; font-synthesis: none; letter-spacing: var(--tracking-title); color: var(--text); }
   .page-title { font-size: var(--fs-h-page); line-height: 1.1; }
   .dialog-title { font-size: var(--fs-h-dialog); line-height: 1.15; }
   .panel-title { font-size: var(--fs-h-panel); line-height: 1.2; }
   .dialog-title .id { font: 500 17px var(--font-mono); letter-spacing: 0; }
   ```

3. Applica le classi:
   - `.page-title` al nome della pagina nella testata di `index.html`, `sim.html`,
     `live.html` (dove oggi l'`h1` è `parity-deriva · simulate`/`· live`, la testata
     diventa come quella di `index.html`: logo, separatore, nome della pagina);
   - `.dialog-title` è già sull'`h2` di ogni dialog se hai fatto l'aggiunta sulla
     testata dei dialog; altrimenti mettila sul titolo di oggi (`<strong>`);
   - `.panel-title` ai titoli delle sezioni: i `<summary>` di `index.html`
     (`#curve-head`, `#levels-head`, legend, `#equity-head`: la classe va sul testo
     del titolo dentro il summary, non sul summary), gli `h3` di `live.html`,
     `KPI comparison` in `sim.html`. **Prima fammi vedere l'elenco.**
   - Non l'intestazione del grafico prezzi (`EUR_USD · H1 · AG01`): resta in Plex.

**Verifica.** `tests/web_test.py` passa. In Network `PDInstrument-Regular.woff2`
arriva con `font/woff2`; in Elements → Computed → Rendered Fonts i titoli usano
"PD Instrument" e non c'è grassetto simulato; la testata mostra il nome più stretto,
alta 28 px; chiaro e scuro. Screenshot di backtest e di un dialog, se puoi.

Non fare commit.

---

# Aggiunta del 25/09/2026 · risorse del server come spia unica

Si può dare a Claude Code da sola.

**Contesto.** In testata c'è una striscia sempre visibile con cpu, ram e dischi
(`cpu ▬ 63%  ram ▬ 2.4/3.7G  / ▬ 73%  /mnt ▬ 78%`): verde pieno anche quando i valori
salgono, mono 16 px, circa 690 px di testata. Roberto ha scelto la proposta 4 di
`web/risorse-proposte.html`: una spia sola con il dettaglio al clic. Regole in
`web/DESIGN.md` § 6, Risorse del server.

**Cosa fare.**

1. Trova il codice che disegna la striscia e quello che la aggiorna (cerca `cpu`,
   `ram`, `/mnt` in `web/static/*.js` e l'endpoint in `web/service.py`). **Dimmi
   file, funzioni, endpoint e che campi restituisce prima di cambiare.** Endpoint,
   intervallo di aggiornamento e dati restano come sono.
2. Copia `loghi/definitivo/tokens.css` in `web/static/tokens.css` (ha i token nuovi
   `--backdrop` e `--shadow-float`; se la copia in `web/static` è stata modificata,
   fondi a mano).
3. Al posto della striscia, nella testata fra l'indirizzo del servizio e
   l'interruttore del tema:

   ```html
   <div class="sys" id="sys">
     <button type="button" class="sys-chip" id="sys-chip" aria-expanded="false" aria-controls="sys-pop" aria-label="server resources">
       <span class="sys-mini" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
       <span id="sys-text">server</span>
     </button>
     <div class="sys-pop" id="sys-pop" role="dialog" aria-labelledby="sys-title" hidden>
       <h2 class="panel-title" id="sys-title">server</h2>
       <div id="sys-rows"></div>
       <p class="sys-foot" id="sys-foot"></p>
     </div>
   </div>
   ```

4. In `web/static/app.css`:

   ```css
   /* risorse del server: spia in testata + pannello (DESIGN.md § 6) */
   .sys { position: relative; flex: none; }
   .sys-chip { display: inline-flex; align-items: center; gap: 8px; height: 30px; padding: 0 10px 0 9px; border: 1px solid var(--line); border-radius: var(--radius-s); background: transparent; color: var(--text-2); font: 500 12px var(--font-sans); white-space: nowrap; cursor: pointer; }
   .sys-chip:hover { border-color: var(--border); color: var(--text); }
   .sys-chip:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
   .sys-chip.warn { border-color: var(--border); color: var(--text); }
   .sys-chip.crit { border-color: var(--down); color: var(--down); }
   .sys-chip.stale { border-style: dashed; color: var(--text-3); }
   .sys-chip .v { font: 500 12px var(--font-mono); }
   .sys-mini { display: flex; align-items: flex-end; gap: 2px; height: 14px; }
   .sys-mini i { width: 3px; border-radius: 1px; background: var(--text-3); }
   .sys-mini i.warn { background: var(--text); }
   .sys-mini i.crit { background: var(--down); }
   .sys-ico { width: 12px; height: 12px; flex: none; color: var(--down); }
   #sys-text { display: inline-flex; align-items: center; gap: 4px; }
   .sys-pop { position: absolute; right: 0; top: calc(100% + 8px); z-index: 20; width: 300px; box-sizing: border-box; padding: 12px 14px; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-m); box-shadow: var(--shadow-float); }
   .sys-pop[hidden] { display: none; }
   .sys-pop .panel-title { margin: 0 0 8px; }
   .sys-row { --fill: var(--text-3); --val: var(--text-2); --w: 400; display: grid; grid-template-columns: 56px 1fr auto; align-items: center; gap: 4px 10px; padding: 5px 0; }
   .sys-row.warn { --fill: var(--text); --val: var(--text); --w: 600; }
   .sys-row.crit { --fill: var(--down); --val: var(--down); --w: 600; }
   .sys-row .l { font-size: 12px; color: var(--text-3); white-space: nowrap; }
   .sys-row .path { font-family: var(--font-mono); }
   .sys-row .track { display: block; height: 4px; border-radius: 2px; background: var(--line); overflow: hidden; }
   .sys-row .fill { display: block; height: 100%; border-radius: 2px; background: var(--fill); }
   .sys-row .v { display: inline-flex; align-items: center; justify-content: flex-end; gap: 3px; min-width: 4ch; font: var(--w) 12px var(--font-mono); color: var(--val); }
   .sys-row .abs { grid-column: 2 / 4; margin-top: -2px; font: 11px var(--font-mono); color: var(--text-3); }
   .sys-foot { margin: 8px 0 0; padding-top: 8px; border-top: 1px solid var(--line); font-size: 11px; color: var(--text-3); }
   ```

5. Nel JS che oggi aggiorna la striscia (stesse chiamate, stesso intervallo):
   - soglie in due costanti, `HIGH = 80` e `CRIT = 90`; stato di una misura:
     `crit` se ≥ CRIT, `warn` se ≥ HIGH, altrimenti niente;
   - quattro misure nell'ordine cpu, ram, disk `/`, disk `/mnt` (i dischi che
     l'endpoint restituisce, nel suo ordine), valori in percentuale intera;
   - **spia**: la classe è lo stato della misura peggiore; testo `server ok` se
     nessuna è ≥ HIGH, altrimenti `<nome> <span class="v">NN%</span>` della
     peggiore più ` +n` per le altre ≥ HIGH; se è critica, prima del testo l'icona
     (svg triangolo con "!", classe `sys-ico`, `aria-hidden`). `title` della spia:
     le quattro percentuali, `cpu 63 % · ram 65 % · disk / 73 % · disk /mnt 78 %`;
   - **barrette**: altezza `max(2, round(v / 100 * 14))` px, classe dello stato;
   - **righe del pannello**: `<div class="sys-row [warn|crit]" role="meter"
     aria-valuemin="0" aria-valuemax="100" aria-valuenow="NN" aria-valuetext="NN %
     · high|critical" aria-label="disk /mnt">`, dentro etichetta (`disk
     <span class="path">/mnt</span>`), barra, valore (con l'icona se critico) e, se
     l'endpoint li ha, i valori assoluti in `.abs` (`2.4 / 3.7 GB`); se non li ha,
     niente riga `.abs` e dimmelo;
   - **piede**: `updated N s ago · high from 80 %, critical from 90 %`, con N
     ricalcolato a ogni giro;
   - **dati vecchi**: se una chiamata fallisce o l'ultimo dato buono è più vecchio di
     tre intervalli, spia `stale` con testo `server ?` e `title` `no data since
     hh:mm:ss`; le righe restano con l'ultimo dato;
   - aggiorna i nodi esistenti, non ricreare il pannello: se è aperto non deve
     chiudersi né perdere il focus;
   - apertura: clic sulla spia apre e chiude (`hidden` e `aria-expanded`); clic
     fuori da `#sys` chiude; `esc` chiude e rimette il focus sulla spia.

**Verifica.** `tests/web_test.py` passa (e un test per il nuovo formato, se
l'endpoint o la sua forma cambiano: non dovrebbero). In pagina: `server ok` con i
valori di oggi; forza un valore a 84 e a 93 (dalla console o con un finto
endpoint) e controlla spia, barrette, pannello e icona; `Tab` arriva alla spia con
l'anello ocra, `Invio` apre, `esc` chiude; con il servizio fermo la spia passa a
`server ?`; chiaro e scuro; la testata non va più a capo a 1280 px.

Non fare commit.
