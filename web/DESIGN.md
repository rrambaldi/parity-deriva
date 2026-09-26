##### VERSIONE 1
# parity-deriva — linee guida grafiche dell'applicativo

Queste regole servono a costruire o modificare il layout del viewer in `web/static`
(e di ogni schermata futura) senza dover rifare le scelte ogni volta. Valgono per
chi scrive il codice a mano e per un agente che lavora sul repo.

I file pronti stanno in `loghi/definitivo/`: logo, favicon, font e `tokens.css`.
La storia delle scelte è in `loghi/riassunto-sessione-logo.md`.

## 1. Principi

1. **Ogni colore ha un solo compito.** È la regola già scritta in testa a
   `web/static/app.css` e resta la regola madre: un colore che significa due
   cose non significa niente.
2. **Il colore non è mai l'unico segnale.** Verde e rosso vanno sempre insieme
   a un simbolo (▲ ▼), a un segno (+ −), a una parola (`target`, `stop`) o a un
   tratteggio. Verde e rosso sono al limite della distinguibilità per chi ha
   un'anomalia del rosso-verde (distanza 7,0 in scuro e 7,8 in chiaro, sotto la
   soglia di 8).
3. **L'ocra è del marchio e dell'azione principale.** Non entra mai in grafici,
   tabelle, esiti o messaggi: dista 10,6–13,2 dal rosso dello stop, sotto la
   soglia di 15 sotto cui due colori si confondono anche a vista normale.
4. **Niente CDN.** Font, logo e stili si servono dal repo, come dice il README
   per gli script. Gli URL restano relativi (funziona dietro reverse proxy).
5. **I testi dell'interfaccia restano in inglese**, minuscoli, come oggi
   (`trades`, `win rate`, `show the whole range`). Queste linee guida sono in
   italiano, l'applicativo no.
6. **Mai un simbolo di valuta su un numero che non è in quella valuta.** Il P&L
   è `price x units` o `quote ccy` (vedi README, "Two things the numbers are not").

## 2. Tema

- **Segue il sistema operativo** (`prefers-color-scheme`), con un interruttore
  che lo forza. L'interruttore scrive `data-theme="light"` o `data-theme="dark"`
  su `<html>`; senza attributo vale il sistema. La scelta si può ricordare in
  `localStorage` (è una comodità per chi usa quel browser, avvolta in try/catch).
- I due temi usano **gli stessi nomi di token** con valori diversi: il codice
  non sa mai in che tema è. Tutti i valori stanno in `loghi/definitivo/tokens.css`.
- Il canvas non legge le variabili CSS da solo: `app.js` le legge con
  `getComputedStyle(document.documentElement).getPropertyValue('--up')` a ogni
  disegno e ridisegna quando cambia il tema (listener su
  `matchMedia('(prefers-color-scheme: dark)')` e sull'interruttore).

## 3. Colori

### Marchio (uguali nei due temi)

| token | valore | uso |
|---|---|---|
| `--brand-ochre` | `#C98A45` | sole del logo, trattino del nome, pulsante principale |
| `--brand-ink` | `#152C3A` | inchiostro petrolio: testo e logo su fondo chiaro |
| `--brand-ivory` | `#F4EFE4` | avorio: testo e logo su fondo scuro |
| `--brand-night` | `#0B1A26` | notte: fondo scuro, fondo dell'icona |

### Superfici e testo

| token | chiaro | scuro | uso |
|---|---|---|---|
| `--bg` | `#F6F3EC` | `#0B1A26` | fondo pagina |
| `--panel` | `#FFFFFF` | `#102433` | pannelli, testata, barra dei controlli, grafici |
| `--raised` | `#FBF9F5` | `#16304A` | elementi sollevati (menu aperti, tooltip) |
| `--line` | `#E3DED3` | `#22394B` | separatori, righe di tabella |
| `--border` | `#7D8E99` | `#52708A` | bordi di campi e pulsanti secondari (≥ 3:1) |
| `--grid` | `#EEEAE2` | `#1A3244` | griglia dei grafici, recessiva |
| `--text` | `#152C3A` | `#F4EFE4` | testo principale |
| `--text-2` | `#4A5B67` | `#B3BFC7` | testo secondario |
| `--text-3` | `#5E6E78` | `#8E9CA6` | etichette, note, assi (≥ 4,5:1 su bg e panel) |

### Azione e focus

| token | chiaro | scuro | uso |
|---|---|---|---|
| `--accent` | `#C98A45` | `#C98A45` | riempimento del **solo** pulsante principale (`Run`) |
| `--on-accent` | `#152C3A` | `#0B1A26` | testo sul pulsante principale (4,96:1 / 6,05:1) |
| `--accent-text` | `#9A5F22` | `#E7A45E` | ocra come testo: link, pulsante ghost |
| `--focus` | `#A8702F` | `#C98A45` | anello di focus di tastiera, sempre visibile |

### Dati (grafici, tabelle, indicatori)

| token | chiaro | scuro | significa | non significa mai |
|---|---|---|---|---|
| `--entry` | `#2A74C7` | `#3F8FDB` | entry; riga selezionata ↔ trade zoomato; info; practice | long, guadagno |
| `--up` | `#15855F` | `#26A37A` | **long**, target, guadagno, candela in salita | successo di sistema |
| `--down` | `#C9423A` | `#E5655C` | **short**, stop, perdita, candela in discesa, errore di input | ambiente live |
| `--mark-exit` | = `--text` | = `--text` | linea e marcatore di uscita | — |
| `--mark-trail` | = `--text-3` | = `--text-3` | "stop moved to" (punteggiato) | — |
| `--trade-span` | blu 7 % | blu 9 % | campitura delle barre del trade | — |

La terna blu/verde/rosso è validata in tutte le coppie sui due fondi (distanza a
vista normale ≥ 17,6; contrasto ≥ 3:1). Una quarta tinta nei dati non passa:
per questo exit e "stop moved to" sono neutri e si distinguono per forma.

Long verde e short rosso seguono la convenzione buy/sell. Siccome verde e rosso
indicano anche target/stop e guadagno/perdita, la direzione va **sempre**
scritta con ▲/▼ e la parola (`▲ long`, `▼ short`): un short in guadagno ha la
direzione in rosso e il P&L in verde, ed è corretto così.

### Riservati

| token | valore | uso |
|---|---|---|
| `--live` / `--on-live` | `#C9423A` / `#FFFFFF` | badge **LIVE · real money**, pieno. Nient'altro è pieno di rosso. |

### Cosa sostituisce cosa in `web/static/app.css`

| oggi | valore di oggi | diventa |
|---|---|---|
| `--bg` `--panel` `--line` `--text` | grigi neutri | `--bg` `--panel` `--line` `--text` (petrolio/avorio) |
| `--dim` | `#8b95a6` | `--text-3` (etichette) o `--text-2` (testo secondario) |
| `--up` `--tp` | `#3fb68b` | `--up` |
| `--down` `--sl` | `#e2555a` | `--down` |
| `--entry` | `#58a6ff` | `--entry` |
| `--exit` | `#c8a2ff` viola | `--mark-exit` neutro (il viola era indistinguibile dal blu per i daltonici: 2,6) |
| `--trail` | `#ff9f45` arancio | `--mark-trail` neutro, punteggiato |
| `--long` | `#58a6ff` | `--up` |
| `--short` | `#e8a33d` ambra | `--down` |
| `--font` | tutto monospace 13 px | `--font-sans` per l'interfaccia, `--font-mono` per i dati |

## 4. Tipografia

| ruolo | font | peso | dimensione | note |
|---|---|---|---|---|
| logo | Marcellus | — | — | **solo dentro gli SVG del logo**, mai come testo |
| interfaccia | IBM Plex Sans | 400 / 500 / 600 | 13 px (corpo) | cifre tabulari già di default |
| dati | IBM Plex Mono | 400 / 500 | 13 px (tabelle), 11–12 px (assi) | prezzi, orari, importi, ID, codice |
| titoli | PD Instrument (Instrument Serif allargato ×1,08) | 400 | 24 · 22 · 18 px | nome della pagina, dialog, pannelli; spaziatura +0,01 em |

- Attivare lo zero barrato su tutto: `font-feature-settings: "zero" 1;`
  (entrambi i font lo hanno).
- Scala: 11 (etichette maiuscole, intestazioni di tabella, spaziatura 0,06em) ·
  12 (note, legenda, assi) · 13 (corpo, celle, campi) · 15 (testo in evidenza) ·
  17 (valori secondari) · 22 (valori principali). Interlinea 1,45.
- **Titoli** in `--font-title` (PD Instrument): 24 px il nome della pagina
  (`--fs-h-page`), 22 px i dialog (`--fs-h-dialog`), 18 px i pannelli
  (`--fs-h-panel`); interlinea 1,1–1,2; spaziatura `--tracking-title` (0,01 em);
  minuscolo come ogni testo. Solo titoli: etichette, campi, tabelle e numeri
  restano in Plex. Scelta del 25/09/2026 fra Jost, Manrope, Instrument Serif e
  Newsreader (`web/font-titoli-proposte.html`), poi fra le varianti di
  Instrument (`web/titoli-instrument-varianti.html`): con tutto in Plex a 15 px la
  pagina non aveva un punto d'attacco.
- PD Instrument ha un peso solo: la gerarchia la fanno misura e carattere. Sui
  titoli sempre `font-weight: 400` e `font-synthesis: none` (`h2`/`h3` sono in
  grassetto di default e il browser lo simulerebbe, male).
- Pesi di Plex: 400 testo, 500 valori, 600 pulsante principale e testo in evidenza.
- **Plex non ha ▲ ▼ ■ ●**: disegnarli come SVG in linea (triangolo 10 px, quadrato
  8 px) invece di affidarsi al font di sistema, che cambierebbe da macchina a
  macchina.
- Il meno dei numeri negativi è `−` (U+2212), non il trattino: ha la stessa
  larghezza del `+` e le colonne restano allineate.
- Numeri a destra nelle tabelle; sempre il segno sui P&L; separatore delle
  migliaia con spazio sottile nel monospace (`100 000`).

## 5. Spazi e forme

- Unità 4 px: 4 · 8 · 12 · 16 · 20 · 24; margine di pagina 28 px.
- Raggi: 6 px campi e pulsanti · 10 px pannelli · 999 px pillole (badge, esiti).
- Altezza dei controlli 34 px (i bersagli restano ≥ 34 px anche con il mouse).
- Niente ombre decorative e niente sfumature: i pannelli si separano con
  `--line` e con il cambio di superficie `--bg` → `--panel`. Unica ombra: quella
  degli elementi sospesi sopra il contenuto senza fondo scuro dietro (il pannello
  delle risorse del server), `--shadow-float`. I dialog hanno invece `--backdrop`.

## 6. Componenti

Le tavole di riferimento sono sulla tela "Logo parity-deriva", pagina
**Font e componenti** (link nel riassunto).

### Testata (60 px, `--panel`, bordo inferiore `--line`)
- A sinistra `parity-deriva_testata_fondo-*.svg` alto 28 px (marchio + nome,
  **senza tagline**: a quella misura non si legge; nome a 0,07 em, § 7),
  separatore verticale, nome della pagina (`Backtest`) in `--font-title` 24 px.
- Badge d'ambiente sempre visibile, subito dopo il nome pagina:
  - `offline · no orders` — bordo `--border`, testo `--text-2` (il viewer di oggi);
  - `practice` — bordo e testo `--entry`;
  - `● live · real money` — pieno `--live`, testo `--on-live`, peso 700.
  Chi guarda deve sapere in ogni istante se sta muovendo soldi veri.
- A destra: indirizzo del servizio in `--font-mono` `--text-3` e l'interruttore
  del tema. Fra i due, la spia delle risorse del server (sotto).

### Risorse del server (testata)
Scelta del 25/09/2026: proposta 4 "una spia sola" di `web/risorse-proposte.html`, al
posto della striscia cpu / ram / dischi sempre visibile (verde pieno, mono 16 px,
~690 px di testata).

- **Spia**: pulsante alto 30 px, bordo `--line`, raggio 6 px, 12 px Plex 500. Dentro
  quattro barrette verticali 3 × 14 px, una per misura (cpu, ram, disk /, disk
  /mnt), alte quanto il valore; accanto una parola:
  - tutto sotto l'80 %: `server ok`, testo `--text-2`;
  - almeno una misura all'80 % o più: la peggiore, `ram 84 %`, e `+1`, `+2` se ce ne
    sono altre (valore in mono);
  - dati vecchi (nessuna risposta da tre giri di aggiornamento): `server ?` in
    `--text-3`, bordo tratteggiato, nel tooltip l'ora dell'ultimo dato.
  Il tooltip della spia elenca sempre le quattro percentuali, così i numeri si
  vedono anche senza aprire.
- **Tre stati**, uguali per spia, barrette e pannello:
  - normale (< 80 %): riempimento `--text-3`, valore `--text-2`;
  - alto (80–89 %): riempimento `--text`, valore `--text` 600, bordo della spia
    `--border`;
  - critico (≥ 90 %): `--down` con l'icona "!" (triangolo 12 px) accanto al valore e
    bordo della spia `--down`. Mai il colore da solo.
  **Mai verde**: il verde è long / target / guadagno, e un disco pieno non lo è.
- **Pannello** al clic, sotto la spia allineato a destra, largo 300 px: fondo
  `--panel`, filetto `--line`, raggio 10 px, `--shadow-float`. Titolo `server`
  (titolo di pannello, § 4); una riga per misura: etichetta 12 px `--text-3`
  (`disk /mnt`, il percorso in mono), barra 4 px sul fondo `--line`, valore mono
  12 px a destra, sotto il valore assoluto in mono 11 px `--text-3`
  (`2.4 / 3.7 GB`, `4 cores · load 2.5`); in fondo `updated 5 s ago · high from
  80 %, critical from 90 %`. Si chiude con un altro clic, con un clic fuori e con
  `esc` (il focus torna alla spia).
- Tutto in percentuale; i valori assoluti solo nel pannello.
- Accessibilità: la spia è un `<button>` con `aria-expanded` e `aria-controls`; ogni
  riga del pannello è `role="meter"` con `aria-valuenow` e `aria-valuetext`
  (`93 % · critical`). Nessun `aria-live`: l'aggiornamento periodico sarebbe rumore.

### Barra dei controlli (`--panel`, sotto la testata)
- Gli stessi campi di oggi: instrument, granularity, strategy, from, to,
  risk %, capital, più il fieldset dei parametri del plugin.
- Etichetta sopra il campo: 11 px maiuscolo `--text-3`. Campo: 34 px,
  fondo `--bg`, bordo `--border`, raggio 6. Valori numerici e date in mono.
- `Run` a destra, **unico elemento ocra pieno dello schermo**. Durante
  l'esecuzione: disabilitato, testo `Running…`, fondo `--line`, testo `--text-3`.

### Messaggi (sotto la barra)
- Errore/rifiuto: fondo `--error-bg`, filetto 1 px `--down`, parola chiave in
  grassetto `--down` (`refused · …`). Info: `--info-bg` e `--entry`.
- Il testo spiega cosa è stato rifiutato e perché, come fa già il servizio.

### Pannello del grafico prezzi
- Intestazione: `EUR_USD · H1 · AG01` (15 px, 600), `trade #21` in `--entry`
  quando è zoomato, pulsante secondario `show the whole range` a destra.
- Candele: corpo e stoppino `--up` / `--down`, stoppino 1,2 px.
- Livelli richiesti, **etichetta a destra**: target `--up` tratteggio 6-4,
  stop `--down` tratteggio 6-4, stop moved to `--mark-trail` punteggiato 2-3
  (etichetta sotto la linea se è vicina all'entry). Spessore 1,5 px.
- Prezzi ottenuti, **etichetta a sinistra**: entry `--entry` continua 1,5 px;
  exit `--mark-exit` continua 1 px al 55 %.
- Marcatori: entry ▲ (long) o ▼ (short) in `--entry` con contorno 2 px del colore
  del pannello; exit ■ in `--mark-exit`. Resta la linea verticale di guida al
  50 % che c'è oggi sulla barra di entrata e su quella di uscita.
- Barre del trade campite con `--trade-span`; baffi ask-high/bid-low sottili in `--text-3`.
- Assi in mono 11 px `--text-3`, griglia `--grid`. Legenda sotto il grafico,
  12 px `--text-2`, con il campione di linea (continua/tratteggiata/punteggiata)
  e la nota `times are UTC`.

### Indicatori (report)
- Etichetta 11 px maiuscolo `--text-3`, valore sotto in Plex Sans 500
  (22 px la prima riga: trades, won, lost, win rate, net, profit factor,
  max drawdown; 17 px la seconda: signals, entered, never entered, still open,
  candles, took).
- Colore sul valore solo se ha segno o esito: won/net positivo `--up`,
  lost/max drawdown/net negativo `--down`. Tutto il resto `--text`.

### Curva del capitale
- Linea a gradini 1,5 px colorata dal risultato netto, come oggi: `--up` se il
  saldo finale è ≥ del capitale iniziale, `--down` altrimenti (è un guadagno o una
  perdita, quindi rientra nella regola del § 3). Linea di partenza tratteggiata
  `--text-3` al capitale iniziale; la chiusura del trade selezionato è un punto
  `--entry` (il legame con la riga selezionata).
- Intestazione `capital` + nota `balance after each close · re-read monthly`.
- Pannello a parte, mai un secondo asse sul grafico prezzi.

### Tabella dei trade
- Intestazione sticky: 11 px maiuscolo `--text-3`, filetto `--border`.
- Righe 13 px, filetto `--line`; numeri, prezzi e orari in mono allineati a destra.
- Side: `▲ long` in `--up`, `▼ short` in `--down`.
- Esito come pillola con bordo: `target` `--up`, `stop` `--down`,
  `still open` `--text-3`. P&L con segno, colorato; `—` in `--text-3` se aperto.
- Hover: fondo `--raised`. **Selezionata**: fondo `--row-selected` e barra
  interna di 3 px `--entry` sulla prima cella — è il legame "questa riga è quel
  trade lassù" del codice di oggi.
- Tastiera come oggi: frecce o `j`/`k`, `escape` per tornare all'intervallo intero.

### Titoli dei pannelli
- `--font-title` 18 px (`report`, `closed trades`, `equity curve`, …); a destra,
  sulla stessa linea di base, un dato di contesto in mono 12 px `--text-3`
  (`AG01 · EUR_USD · H1`, `3 today`).
- Dove il titolo è un `<summary>` che apre e chiude la sezione resta tale: la
  classe va sul testo del titolo, non sul `<summary>`.
- Fa eccezione l'intestazione del grafico prezzi (`EUR_USD · H1 · AG01`): sono
  codici, restano in Plex.

### Pulsanti
- Principale: `--accent` + `--on-accent`, 600 — uno per schermata.
- Secondario: trasparente, bordo `--border`, testo `--text`, 500.
- Ghost: solo testo `--accent-text`.
- Focus: anello 2 px `--focus` staccato di 2 px dal pulsante; mai togliere l'outline
  senza sostituirlo.

### Campi
- Normale: bordo `--border`. Focus: bordo `--focus` + alone 3 px al 20 %.
- Errore: bordo `--down` e messaggio sotto in 12 px `--down` (`not a date`).
- **Parametri sì/no** (es. `intraday` nella simulazione, dove si possono provare
  entrambi i valori): **segmentato `Y | N`**, alto 34 px come gli altri campi, così
  la riga dei parametri resta allineata. I due segmenti sono checkbox indipendenti,
  non radio: accesi entrambi = prova entrambi i valori.
  - Contenitore: bordo `--border`, fondo `--bg`, padding 2 px, raggio 6 px.
  - Segmento spento: testo `--text-3`, 500; hover fondo `--line` e testo `--text`.
  - Segmento acceso: fondo `--text`, testo `--panel`, 600. Neutro di proposito:
    l'ocra è di `Run` e del focus, blu, verde e rosso sono dei dati.
  - Nessuno dei due acceso = zero combinazioni: bordo del gruppo `--down`.
  - Focus: anello 2 px `--focus` sul segmento; disabilitato: opacità 0,45.
  - Prova interattiva e CSS: `web/checkbox-proposte.html`, proposta 2 (scelta il
    25/09/2026). Istruzioni per Claude Code: in fondo a `REFACTORING-claude-code.md`.

### Dialog
Tutti gli otto dialog (data, runs, live, help, sets, run, analysis, sim) hanno la
stessa forma. Scelta del 25/09/2026: proposta 2 "titolo con contesto" di
`web/dialog-testata-proposte.html`.

- Foglio: fondo `--panel`, filetto `--line`, raggio 10 px, nessuna ombra; dietro
  `--backdrop` (nero al 55 %).
- **Testata** su fondo `--panel` (niente fascia di colore diverso), filetto `--line`
  sotto, padding 13 · 12 · 13 · 20 px. Da sinistra:
  - titolo in `h2`, collegato al dialog con `aria-labelledby` (oggi `<strong>`, che
    lo screen reader non annuncia); minuscolo come tutti i testi; PD Instrument
    22 px (§ 4). Dove il dialog mostra una lista (sets, runs, sim) il titolo è
    seguito dal **conteggio** in pillola: mono 11 px, `--text-2`, bordo `--line`;
  - sotto il titolo una **riga d'aiuto** in 12 px `--text-3`: che cosa c'è e che cosa
    se ne fa (per sets: `kept on disk, newest first · click a row to open it · names
    can be edited in place`). Il testo nasce dal commento HTML che oggi precede ogni
    dialog. Nei dialog di un run il titolo resta corto (`run 41/17`, id in mono) e i
    parametri scendono nella riga d'aiuto, in mono;
  - a destra: i pulsanti propri del dialog (secondari, es. `backtest page`), il
    promemoria `esc` (mono 11 px, bordo `--line`, `esc` chiude già da sé) e la
    **chiusura**: quadrata 34 px, senza fondo né bordo, X in svg 16 px `--text-2`,
    hover fondo `--line`, focus anello `--focus`. Mai tonda con fondo proprio.
- Corpo: 16 · 20 · 20 px di margine per testo e moduli; a filo per le tabelle, con
  la prima colonna a 20 px, sullo stesso filo del titolo. Intestazione della
  tabella sticky come nella tabella dei trade.

## 7. Logo

File in `loghi/definitivo/` (dettagli nel README della cartella).

| versione | quando |
|---|---|
| verticale | copertine, pagina di presentazione, documenti |
| orizzontale | intestazioni di documenti, firme, slide |
| testata (senza tagline) | barra in alto dell'applicativo, da 24 a 40 px di altezza |
| marchio da solo | quando il nome è già scritto accanto |
| icona (quadrato arrotondato) | app, avatar; la versione quadrata per apple-touch-icon |
| favicon semplificata | sotto i 32 px (sole inciso a tre segmenti, una banda) |
| monocolore inchiostro/avorio | stampa a un colore, timbri, incisione, fondi colorati o fotografici |

- Colori fissi: sole e riflesso ocra, cammino e nome inchiostro (fondo chiaro) o
  avorio (fondo scuro), trattino ocra. Non ricolorare.
- Area di rispetto: attorno al logo almeno l'altezza del sole.
- Misure minime: verticale 120 px di larghezza, orizzontale 160 px, testata
  24 px di altezza, marchio 32 px. Sotto: favicon.
- Spaziatura del nome: 0,18 em nelle versioni con tagline; **0,07 em con la
  crenatura del font nella testata** (dal 25/09/2026: a 0,16 staccava troppo dai
  titoli in PD Instrument). Script: `loghi/sorgenti/build_testata.py`.
- Su fondi ocra o affollati: versione monocolore.
- Mai Marcellus come testo dell'interfaccia; mai il logo dentro l'area dei dati.

## 8. Accessibilità (controlli fatti)

- Contrasto del testo ≥ 4,5:1 su `--bg` e `--panel` in entrambi i temi (valori
  nelle tabelle sopra); bordi dei campi ≥ 3:1.
- Terna dei dati validata in tutte le coppie con lo strumento di validazione
  delle palette (fascia di luminosità, croma, separazione per daltonismo,
  distanza a vista normale, contrasto): verde/rosso in fascia d'avviso, quindi
  sempre con simboli, segni o tratteggi.
- Focus visibile ovunque; tutte le azioni raggiungibili da tastiera;
  pulsanti veri (`<button>`), campi con `<label>`.

## 9. Per chi implementa

Il piano completo, passo per passo e con i test, è in
`web/REFACTORING-claude-code.md` (un prompt da dare a Claude Code). In breve:

1. `web/service.py` serve solo `.html`, `.css`, `.js` e rifiuta le sottocartelle:
   aggiungere `.woff2`, `.svg`, `.png` a `CONTENT_TYPES`, **senza** permettere
   sottocartelle, e coprirlo con un test in `tests/web_test.py`.
2. Copiare font, `fonts.css`, `tokens.css`, logo della testata e favicon
   **direttamente** in `web/static/` (cartella piatta) e collegarli in
   `index.html` prima di `app.css`, con URL relativi.
3. In `app.css` sostituire il blocco `:root` con i token (tabella del § 3) e
   separare `--font-sans` (interfaccia) da `--font-mono` (dati).
4. In `app.js` togliere i colori scritti nel codice e leggerli dai token al
   momento del disegno; aspettare `document.fonts.ready` prima del primo disegno;
   ridisegnare al cambio di tema.
5. Testata con logo, badge d'ambiente e interruttore del tema; ▲ ▼ ■ disegnati
   senza affidarsi al font.
6. Comportamento, API, id dei campi, parametri dell'URL e scorciatoie non cambiano.
### VERSIONE 2
# parity-deriva — linee guida grafiche dell'applicativo

Queste regole servono a costruire o modificare il layout del viewer in `web/static`
(e di ogni schermata futura) senza dover rifare le scelte ogni volta. Valgono per
chi scrive il codice a mano e per un agente che lavora sul repo.

I file pronti stanno in `loghi/definitivo/`: logo, favicon, font e `tokens.css`.
La storia delle scelte è in `loghi/riassunto-sessione-logo.md`.

## 1. Principi

1. **Ogni colore ha un solo compito.** È la regola già scritta in testa a
   `web/static/app.css` e resta la regola madre: un colore che significa due
   cose non significa niente.
2. **Il colore non è mai l'unico segnale.** Verde e rosso vanno sempre insieme
   a un simbolo (▲ ▼), a un segno (+ −), a una parola (`target`, `stop`) o a un
   tratteggio. Verde e rosso sono al limite della distinguibilità per chi ha
   un'anomalia del rosso-verde (distanza 7,0 in scuro e 7,8 in chiaro, sotto la
   soglia di 8).
3. **L'ocra è del marchio e dell'azione principale.** Non entra mai in grafici,
   tabelle, esiti o messaggi: dista 10,6–13,2 dal rosso dello stop, sotto la
   soglia di 15 sotto cui due colori si confondono anche a vista normale.
4. **Niente CDN.** Font, logo e stili si servono dal repo, come dice il README
   per gli script. Gli URL restano relativi (funziona dietro reverse proxy).
5. **I testi dell'interfaccia restano in inglese**, minuscoli, come oggi
   (`trades`, `win rate`, `show the whole range`). Queste linee guida sono in
   italiano, l'applicativo no.
6. **Mai un simbolo di valuta su un numero che non è in quella valuta.** Il P&L
   è `price x units` o `quote ccy` (vedi README, "Two things the numbers are not").

## 2. Tema

- **Segue il sistema operativo** (`prefers-color-scheme`), con un interruttore
  che lo forza. L'interruttore scrive `data-theme="light"` o `data-theme="dark"`
  su `<html>`; senza attributo vale il sistema. La scelta si può ricordare in
  `localStorage` (è una comodità per chi usa quel browser, avvolta in try/catch).
- I due temi usano **gli stessi nomi di token** con valori diversi: il codice
  non sa mai in che tema è. Tutti i valori stanno in `loghi/definitivo/tokens.css`.
- Il canvas non legge le variabili CSS da solo: `app.js` le legge con
  `getComputedStyle(document.documentElement).getPropertyValue('--up')` a ogni
  disegno e ridisegna quando cambia il tema (listener su
  `matchMedia('(prefers-color-scheme: dark)')` e sull'interruttore).

## 3. Colori

### Marchio (uguali nei due temi)

| token | valore | uso |
|---|---|---|
| `--brand-ochre` | `#C98A45` | sole del logo, trattino del nome, pulsante principale |
| `--brand-ink` | `#152C3A` | inchiostro petrolio: testo e logo su fondo chiaro |
| `--brand-ivory` | `#F4EFE4` | avorio: testo e logo su fondo scuro |
| `--brand-night` | `#0B1A26` | notte: fondo scuro, fondo dell'icona |

### Superfici e testo

| token | chiaro | scuro | uso |
|---|---|---|---|
| `--bg` | `#F6F3EC` | `#0B1A26` | fondo pagina |
| `--panel` | `#FFFFFF` | `#102433` | pannelli, testata, barra dei controlli, grafici |
| `--raised` | `#FBF9F5` | `#16304A` | elementi sollevati (menu aperti, tooltip) |
| `--line` | `#E3DED3` | `#22394B` | separatori, righe di tabella |
| `--border` | `#7D8E99` | `#52708A` | bordi di campi e pulsanti secondari (≥ 3:1) |
| `--grid` | `#EEEAE2` | `#1A3244` | griglia dei grafici, recessiva |
| `--text` | `#152C3A` | `#F4EFE4` | testo principale |
| `--text-2` | `#4A5B67` | `#B3BFC7` | testo secondario |
| `--text-3` | `#5E6E78` | `#8E9CA6` | etichette, note, assi (≥ 4,5:1 su bg e panel) |

### Azione e focus

| token | chiaro | scuro | uso |
|---|---|---|---|
| `--accent` | `#C98A45` | `#C98A45` | riempimento del **solo** pulsante principale (`Run`) |
| `--on-accent` | `#152C3A` | `#0B1A26` | testo sul pulsante principale (4,96:1 / 6,05:1) |
| `--accent-text` | `#9A5F22` | `#E7A45E` | ocra come testo: link, pulsante ghost |
| `--focus` | `#A8702F` | `#C98A45` | anello di focus di tastiera, sempre visibile |

### Dati (grafici, tabelle, indicatori)

| token | chiaro | scuro | significa | non significa mai |
|---|---|---|---|---|
| `--entry` | `#2A74C7` | `#3F8FDB` | entry; riga selezionata ↔ trade zoomato; info; practice | long, guadagno |
| `--up` | `#15855F` | `#26A37A` | **long**, target, guadagno, candela in salita | successo di sistema |
| `--down` | `#C9423A` | `#E5655C` | **short**, stop, perdita, candela in discesa, errore di input | ambiente live |
| `--mark-exit` | = `--text` | = `--text` | linea e marcatore di uscita | — |
| `--mark-trail` | = `--text-3` | = `--text-3` | "stop moved to" (punteggiato) | — |
| `--trade-span` | blu 7 % | blu 9 % | campitura delle barre del trade | — |

La terna blu/verde/rosso è validata in tutte le coppie sui due fondi (distanza a
vista normale ≥ 17,6; contrasto ≥ 3:1). Una quarta tinta nei dati non passa:
per questo exit e "stop moved to" sono neutri e si distinguono per forma.

Long verde e short rosso seguono la convenzione buy/sell. Siccome verde e rosso
indicano anche target/stop e guadagno/perdita, la direzione va **sempre**
scritta con ▲/▼ e la parola (`▲ long`, `▼ short`): un short in guadagno ha la
direzione in rosso e il P&L in verde, ed è corretto così.

### Riservati

| token | valore | uso |
|---|---|---|
| `--live` / `--on-live` | `#C9423A` / `#FFFFFF` | badge **LIVE · real money**, pieno. Nient'altro è pieno di rosso. |

### Cosa sostituisce cosa in `web/static/app.css`

| oggi | valore di oggi | diventa |
|---|---|---|
| `--bg` `--panel` `--line` `--text` | grigi neutri | `--bg` `--panel` `--line` `--text` (petrolio/avorio) |
| `--dim` | `#8b95a6` | `--text-3` (etichette) o `--text-2` (testo secondario) |
| `--up` `--tp` | `#3fb68b` | `--up` |
| `--down` `--sl` | `#e2555a` | `--down` |
| `--entry` | `#58a6ff` | `--entry` |
| `--exit` | `#c8a2ff` viola | `--mark-exit` neutro (il viola era indistinguibile dal blu per i daltonici: 2,6) |
| `--trail` | `#ff9f45` arancio | `--mark-trail` neutro, punteggiato |
| `--long` | `#58a6ff` | `--up` |
| `--short` | `#e8a33d` ambra | `--down` |
| `--font` | tutto monospace 13 px | `--font-sans` per l'interfaccia, `--font-mono` per i dati |

## 4. Tipografia

| ruolo | font | peso | dimensione | note |
|---|---|---|---|---|
| logo | Marcellus | — | — | **solo dentro gli SVG del logo**, mai come testo |
| interfaccia | IBM Plex Sans | 400 / 500 / 600 | 13 px (corpo) | cifre tabulari già di default |
| dati | IBM Plex Mono | 400 / 500 | 13 px (tabelle), 11–12 px (assi) | prezzi, orari, importi, ID, codice |
| titoli | PD Instrument (Instrument Serif allargato ×1,08) | 400 | 24 · 22 · 18 px | nome della pagina, dialog, pannelli; spaziatura +0,01 em |

- Attivare lo zero barrato su tutto: `font-feature-settings: "zero" 1;`
  (entrambi i font lo hanno).
- Scala: 11 (etichette maiuscole, intestazioni di tabella, spaziatura 0,06em) ·
  12 (note, legenda, assi) · 13 (corpo, celle, campi) · 15 (testo in evidenza) ·
  17 (valori secondari) · 22 (valori principali). Interlinea 1,45.
- **Titoli** in `--font-title` (PD Instrument): 24 px il nome della pagina
  (`--fs-h-page`), 22 px i dialog (`--fs-h-dialog`), 18 px i pannelli
  (`--fs-h-panel`); interlinea 1,1–1,2; spaziatura `--tracking-title` (0,01 em);
  minuscolo come ogni testo. Solo titoli: etichette, campi, tabelle e numeri
  restano in Plex. Scelta del 25/09/2026 fra Jost, Manrope, Instrument Serif e
  Newsreader (`web/font-titoli-proposte.html`), poi fra le varianti di
  Instrument (`web/titoli-instrument-varianti.html`): con tutto in Plex a 15 px la
  pagina non aveva un punto d'attacco.
- PD Instrument ha un peso solo: la gerarchia la fanno misura e carattere. Sui
  titoli sempre `font-weight: 400` e `font-synthesis: none` (`h2`/`h3` sono in
  grassetto di default e il browser lo simulerebbe, male).
- Pesi di Plex: 400 testo, 500 valori, 600 pulsante principale e testo in evidenza.
- **Plex non ha ▲ ▼ ■ ●**: disegnarli come SVG in linea (triangolo 10 px, quadrato
  8 px) invece di affidarsi al font di sistema, che cambierebbe da macchina a
  macchina.
- Il meno dei numeri negativi è `−` (U+2212), non il trattino: ha la stessa
  larghezza del `+` e le colonne restano allineate.
- Numeri a destra nelle tabelle; sempre il segno sui P&L; separatore delle
  migliaia con spazio sottile nel monospace (`100 000`).

## 5. Spazi e forme

- Unità 4 px: 4 · 8 · 12 · 16 · 20 · 24; margine di pagina 28 px.
- Raggi: 6 px campi e pulsanti · 10 px pannelli · 999 px pillole (badge, esiti).
- Altezza dei controlli 34 px (i bersagli restano ≥ 34 px anche con il mouse).
- Niente ombre decorative e niente sfumature: i pannelli si separano con
  `--line` e con il cambio di superficie `--bg` → `--panel`. Unica ombra: quella
  degli elementi sospesi sopra il contenuto senza fondo scuro dietro (il pannello
  delle risorse del server), `--shadow-float`. I dialog hanno invece `--backdrop`.

## 6. Componenti

Le tavole di riferimento sono sulla tela "Logo parity-deriva", pagina
**Font e componenti** (link nel riassunto).

### Testata (60 px, `--panel`, bordo inferiore `--line`)
- A sinistra `parity-deriva_testata_fondo-*.svg` alto 28 px (marchio + nome,
  **senza tagline**: a quella misura non si legge; nome a 0,07 em, § 7),
  separatore verticale, nome della pagina (`Backtest`) in `--font-title` 24 px.
- Badge d'ambiente sempre visibile, subito dopo il nome pagina:
  - `offline · no orders` — bordo `--border`, testo `--text-2` (il viewer di oggi);
  - `practice` — bordo e testo `--entry`;
  - `● live · real money` — pieno `--live`, testo `--on-live`, peso 700.
  Chi guarda deve sapere in ogni istante se sta muovendo soldi veri.
- A destra: indirizzo del servizio in `--font-mono` `--text-3` e l'interruttore
  del tema. Fra i due, la spia delle risorse del server (sotto).

### Risorse del server (testata)
Scelta del 25/09/2026: proposta 4 "una spia sola" di `web/risorse-proposte.html`, al
posto della striscia cpu / ram / dischi sempre visibile (verde pieno, mono 16 px,
~690 px di testata).

- **Spia**: pulsante alto 30 px, bordo `--line`, raggio 6 px, 12 px Plex 500. Dentro
  quattro barrette verticali 3 × 14 px, una per misura (cpu, ram, disk /, disk
  /mnt), alte quanto il valore; accanto una parola:
  - tutto sotto l'80 %: `server ok`, testo `--text-2`;
  - almeno una misura all'80 % o più: la peggiore, `ram 84 %`, e `+1`, `+2` se ce ne
    sono altre (valore in mono);
  - dati vecchi (nessuna risposta da tre giri di aggiornamento): `server ?` in
    `--text-3`, bordo tratteggiato, nel tooltip l'ora dell'ultimo dato.
  Il tooltip della spia elenca sempre le quattro percentuali, così i numeri si
  vedono anche senza aprire.
- **Tre stati**, uguali per spia, barrette e pannello:
  - normale (< 80 %): riempimento `--text-3`, valore `--text-2`;
  - alto (80–89 %): riempimento `--text`, valore `--text` 600, bordo della spia
    `--border`;
  - critico (≥ 90 %): `--down` con l'icona "!" (triangolo 12 px) accanto al valore e
    bordo della spia `--down`. Mai il colore da solo.
  **Mai verde**: il verde è long / target / guadagno, e un disco pieno non lo è.
- **Pannello** al clic, sotto la spia allineato a destra, largo 300 px: fondo
  `--panel`, filetto `--line`, raggio 10 px, `--shadow-float`. Titolo `server`
  (titolo di pannello, § 4); una riga per misura: etichetta 12 px `--text-3`
  (`disk /mnt`, il percorso in mono), barra 4 px sul fondo `--line`, valore mono
  12 px a destra, sotto il valore assoluto in mono 11 px `--text-3`
  (`2.4 / 3.7 GB`, `4 cores · load 2.5`); in fondo `updated 5 s ago · high from
  80 %, critical from 90 %`. Si chiude con un altro clic, con un clic fuori e con
  `esc` (il focus torna alla spia).
- Tutto in percentuale; i valori assoluti solo nel pannello.
- Accessibilità: la spia è un `<button>` con `aria-expanded` e `aria-controls`; ogni
  riga del pannello è `role="meter"` con `aria-valuenow` e `aria-valuetext`
  (`93 % · critical`). Nessun `aria-live`: l'aggiornamento periodico sarebbe rumore.

### Barra dei controlli (`--panel`, sotto la testata)
- Gli stessi campi di oggi: instrument, granularity, strategy, from, to,
  risk %, capital, più il fieldset dei parametri del plugin.
- Etichetta sopra il campo: 11 px maiuscolo `--text-3`. Campo: 34 px,
  fondo `--bg`, bordo `--border`, raggio 6. Valori numerici e date in mono.
- `Run` a destra, **unico elemento ocra pieno dello schermo**. Durante
  l'esecuzione: disabilitato, testo `Running…`, fondo `--line`, testo `--text-3`.

### Messaggi (sotto la barra)
- Errore/rifiuto: fondo `--error-bg`, filetto 1 px `--down`, parola chiave in
  grassetto `--down` (`refused · …`). Info: `--info-bg` e `--entry`.
- Il testo spiega cosa è stato rifiutato e perché, come fa già il servizio.

### Pannello del grafico prezzi
- Intestazione: `EUR_USD · H1 · AG01` (15 px, 600), `trade #21` in `--entry`
  quando è zoomato, pulsante secondario `show the whole range` a destra.
- Candele: corpo e stoppino `--up` / `--down`, stoppino 1,2 px.
- Livelli richiesti, **etichetta a destra**: target `--up` tratteggio 6-4,
  stop `--down` tratteggio 6-4, stop moved to `--mark-trail` punteggiato 2-3
  (etichetta sotto la linea se è vicina all'entry). Spessore 1,5 px.
- Prezzi ottenuti, **etichetta a sinistra**: entry `--entry` continua 1,5 px;
  exit `--mark-exit` continua 1 px al 55 %.
- Marcatori: entry ▲ (long) o ▼ (short) in `--entry` con contorno 2 px del colore
  del pannello; exit ■ in `--mark-exit`. Resta la linea verticale di guida al
  50 % che c'è oggi sulla barra di entrata e su quella di uscita.
- Barre del trade campite con `--trade-span`; baffi ask-high/bid-low sottili in `--text-3`.
- Assi in mono 11 px `--text-3`, griglia `--grid`. Legenda sotto il grafico,
  12 px `--text-2`, con il campione di linea (continua/tratteggiata/punteggiata)
  e la nota `times are UTC`.

### Indicatori (report)
- Etichetta 11 px maiuscolo `--text-3`, valore sotto in Plex Sans 500
  (22 px la prima riga: trades, won, lost, win rate, net, profit factor,
  max drawdown; 17 px la seconda: signals, entered, never entered, still open,
  candles, took).
- Colore sul valore solo se ha segno o esito: won/net positivo `--up`,
  lost/max drawdown/net negativo `--down`. Tutto il resto `--text`.

### Curva del capitale
- Linea a gradini 1,5 px colorata dal risultato netto, come oggi: `--up` se il
  saldo finale è ≥ del capitale iniziale, `--down` altrimenti (è un guadagno o una
  perdita, quindi rientra nella regola del § 3). Linea di partenza tratteggiata
  `--text-3` al capitale iniziale; la chiusura del trade selezionato è un punto
  `--entry` (il legame con la riga selezionata).
- Intestazione `capital` + nota `balance after each close · re-read monthly`.
- Pannello a parte, mai un secondo asse sul grafico prezzi.

### Tabella dei trade
- Intestazione sticky: 11 px maiuscolo `--text-3`, filetto `--border`.
- Righe 13 px, filetto `--line`; numeri, prezzi e orari in mono allineati a destra.
- Side: `▲ long` in `--up`, `▼ short` in `--down`.
- Esito come pillola con bordo: `target` `--up`, `stop` `--down`,
  `still open` `--text-3`. P&L con segno, colorato; `—` in `--text-3` se aperto.
- Hover: fondo `--raised`. **Selezionata**: fondo `--row-selected` e barra
  interna di 3 px `--entry` sulla prima cella — è il legame "questa riga è quel
  trade lassù" del codice di oggi.
- Tastiera come oggi: frecce o `j`/`k`, `escape` per tornare all'intervallo intero.

### Titoli dei pannelli
- `--font-title` 18 px (`report`, `closed trades`, `equity curve`, …); a destra,
  sulla stessa linea di base, un dato di contesto in mono 12 px `--text-3`
  (`AG01 · EUR_USD · H1`, `3 today`).
- Dove il titolo è un `<summary>` che apre e chiude la sezione resta tale: la
  classe va sul testo del titolo, non sul `<summary>`.
- Fa eccezione l'intestazione del grafico prezzi (`EUR_USD · H1 · AG01`): sono
  codici, restano in Plex.

### Pulsanti
- Principale: `--accent` + `--on-accent`, 600 — uno per schermata.
- Secondario: trasparente, bordo `--border`, testo `--text`, 500.
- Ghost: solo testo `--accent-text`.
- Focus: anello 2 px `--focus` staccato di 2 px dal pulsante; mai togliere l'outline
  senza sostituirlo.

### Campi
- Normale: bordo `--border`. Focus: bordo `--focus` + alone 3 px al 20 %.
- Errore: bordo `--down` e messaggio sotto in 12 px `--down` (`not a date`).
- **Parametri sì/no** (es. `intraday` nella simulazione, dove si possono provare
  entrambi i valori): **segmentato `Y | N`**, alto 34 px come gli altri campi, così
  la riga dei parametri resta allineata. I due segmenti sono checkbox indipendenti,
  non radio: accesi entrambi = prova entrambi i valori.
  - Contenitore: bordo `--border`, fondo `--bg`, padding 2 px, raggio 6 px.
  - Segmento spento: testo `--text-3`, 500; hover fondo `--line` e testo `--text`.
  - Segmento acceso: fondo `--text`, testo `--panel`, 600. Neutro di proposito:
    l'ocra è di `Run` e del focus, blu, verde e rosso sono dei dati.
  - Nessuno dei due acceso = zero combinazioni: bordo del gruppo `--down`.
  - Focus: anello 2 px `--focus` sul segmento; disabilitato: opacità 0,45.
  - Prova interattiva e CSS: `web/checkbox-proposte.html`, proposta 2 (scelta il
    25/09/2026). Istruzioni per Claude Code: in fondo a `REFACTORING-claude-code.md`.

### Dialog
Tutti gli otto dialog (data, runs, live, help, sets, run, analysis, sim) hanno la
stessa forma. Scelta del 25/09/2026: proposta 2 "titolo con contesto" di
`web/dialog-testata-proposte.html`.

- Foglio: fondo `--panel`, filetto `--line`, raggio 10 px, nessuna ombra; dietro
  `--backdrop` (nero al 55 %).
- **Testata** su fondo `--panel` (niente fascia di colore diverso), filetto `--line`
  sotto, padding 13 · 12 · 13 · 20 px. Da sinistra:
  - titolo in `h2`, collegato al dialog con `aria-labelledby` (oggi `<strong>`, che
    lo screen reader non annuncia); minuscolo come tutti i testi; PD Instrument
    22 px (§ 4). Dove il dialog mostra una lista (sets, runs, sim) il titolo è
    seguito dal **conteggio** in pillola: mono 11 px, `--text-2`, bordo `--line`;
  - sotto il titolo una **riga d'aiuto** in 12 px `--text-3`: che cosa c'è e che cosa
    se ne fa (per sets: `kept on disk, newest first · click a row to open it · names
    can be edited in place`). Il testo nasce dal commento HTML che oggi precede ogni
    dialog. Nei dialog di un run il titolo resta corto (`run 41/17`, id in mono) e i
    parametri scendono nella riga d'aiuto, in mono;
  - a destra: i pulsanti propri del dialog (secondari, es. `backtest page`), il
    promemoria `esc` (mono 11 px, bordo `--line`, `esc` chiude già da sé) e la
    **chiusura**: quadrata 34 px, senza fondo né bordo, X in svg 16 px `--text-2`,
    hover fondo `--line`, focus anello `--focus`. Mai tonda con fondo proprio.
- Corpo: 16 · 20 · 20 px di margine per testo e moduli; a filo per le tabelle, con
  la prima colonna a 20 px, sullo stesso filo del titolo. Intestazione della
  tabella sticky come nella tabella dei trade.

## 7. Logo

File in `loghi/definitivo/` (dettagli nel README della cartella).

| versione | quando |
|---|---|
| verticale | copertine, pagina di presentazione, documenti |
| orizzontale | intestazioni di documenti, firme, slide |
| testata (senza tagline) | barra in alto dell'applicativo, da 24 a 40 px di altezza |
| marchio da solo | quando il nome è già scritto accanto |
| icona (quadrato arrotondato) | app, avatar; la versione quadrata per apple-touch-icon |
| favicon semplificata | sotto i 32 px (sole inciso a tre segmenti, una banda) |
| monocolore inchiostro/avorio | stampa a un colore, timbri, incisione, fondi colorati o fotografici |

- Colori fissi: sole e riflesso ocra, cammino e nome inchiostro (fondo chiaro) o
  avorio (fondo scuro), trattino ocra. Non ricolorare.
- Area di rispetto: attorno al logo almeno l'altezza del sole.
- Misure minime: verticale 120 px di larghezza, orizzontale 160 px, testata
  24 px di altezza, marchio 32 px. Sotto: favicon.
- Spaziatura del nome: 0,18 em nelle versioni con tagline; **0,07 em con la
  crenatura del font nella testata** (dal 25/09/2026: a 0,16 staccava troppo dai
  titoli in PD Instrument). Script: `loghi/sorgenti/build_testata.py`.
- Su fondi ocra o affollati: versione monocolore.
- Mai Marcellus come testo dell'interfaccia; mai il logo dentro l'area dei dati.

## 8. Accessibilità (controlli fatti)

- Contrasto del testo ≥ 4,5:1 su `--bg` e `--panel` in entrambi i temi (valori
  nelle tabelle sopra); bordi dei campi ≥ 3:1.
- Terna dei dati validata in tutte le coppie con lo strumento di validazione
  delle palette (fascia di luminosità, croma, separazione per daltonismo,
  distanza a vista normale, contrasto): verde/rosso in fascia d'avviso, quindi
  sempre con simboli, segni o tratteggi.
- Focus visibile ovunque; tutte le azioni raggiungibili da tastiera;
  pulsanti veri (`<button>`), campi con `<label>`.

## 9. Per chi implementa

Il piano completo, passo per passo e con i test, è in
`web/REFACTORING-claude-code.md` (un prompt da dare a Claude Code). In breve:

1. `web/service.py` serve solo `.html`, `.css`, `.js` e rifiuta le sottocartelle:
   aggiungere `.woff2`, `.svg`, `.png` a `CONTENT_TYPES`, **senza** permettere
   sottocartelle, e coprirlo con un test in `tests/web_test.py`.
2. Copiare font, `fonts.css`, `tokens.css`, logo della testata e favicon
   **direttamente** in `web/static/` (cartella piatta) e collegarli in
   `index.html` prima di `app.css`, con URL relativi.
3. In `app.css` sostituire il blocco `:root` con i token (tabella del § 3) e
   separare `--font-sans` (interfaccia) da `--font-mono` (dati).
4. In `app.js` togliere i colori scritti nel codice e leggerli dai token al
   momento del disegno; aspettare `document.fonts.ready` prima del primo disegno;
   ridisegnare al cambio di tema.
5. Testata con logo, badge d'ambiente e interruttore del tema; ▲ ▼ ■ disegnati
   senza affidarsi al font.
6. Comportamento, API, id dei campi, parametri dell'URL e scorciatoie non cambiano.
