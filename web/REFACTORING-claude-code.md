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
