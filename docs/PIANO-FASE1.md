# Piano di dettaglio – fase 1: singola strategia

Come si costruisce quello che manca alla fase 1. Le regole (stati, gate,
soglie) sono in [PROCESSO.md](PROCESSO.md): qui c'è **come** si fanno, cantiere
per cantiere. Mix e più strumenti sono in [ROADMAP.md](ROADMAP.md).

Per ogni cantiere: obiettivo, scelte, file, dati, pagine/API/MCP, test,
"fatto quando", dipendenze, dimensione (S piccolo, M medio, L grande).

---

## 0. Regole comuni a tutti i cantieri

- **Stesso codice in simulazione e in live.** Ogni regola nuova che decide un
  trade (filtro, protezione) è un pezzo solo, usato dal backtest, dalla
  sessione live e dal simulatore ombra. Due implementazioni della stessa
  regola sono proprio la divergenza che il controllo di parità deve trovare.
- **Soglie in un posto solo**: `etc/settings.py`, lette da `.env` con
  `dotenv()` come `PROMOTE_DAYS`. Nessun numero scritto nel codice.
- **Testi delle pagine in inglese**, con la traduzione in
  `web/static/i18n/it.json`.
- **Nessuna dipendenza nuova**: stdlib, numpy e pandas che ci sono già.
- **Test**: `unittest` in `tests/`, uno per cantiere almeno; `tests/app_check.js`
  dopo ogni modifica ad `app.js` o `menu.js`. Screenshot con Chrome headless
  per le pagine toccate.
- **Deploy**: solo con `deploy-prod.sh`, dopo la suite verde su dev.

---

## 1. Decisioni aperte

Servono prima di partire con i cantieri indicati. Per ognuna c'è la mia
proposta.

| # | Domanda | Proposta | Serve a |
|---|---|---|---|
| D1 | Quando nasce una **versione** nuova? | Al gate SIM → DEMO. La versione è l'impronta del codice (hash del sorgente della strategia e degli indicatori che usa) più i parametri congelati (`groupKey` dei campi del form). In SIM i parametri cambiano senza creare versioni. Etichetta leggibile: `M1502 v3`, contata per strategia + strumento + granularità. | C2, C3 |
| D2 | Il **ramp** dal 25% al 100%: automatico o con un click? | Con un click. Dopo 30 trade dentro la banda la pagina live dice "ready for full size" e un pulsante riavvia il form al 100%, con la stessa conferma del capitale di oggi. Sono soldi veri: decide una persona. | C6 |
| D3 | Che **canale** usano gli avvisi? | Sempre nella pagina (banner su live e logs) e nel log. In più l'email con `smtplib` (stdlib) se in `.env` ci sono i campi `SMTP_*`. Telegram eventualmente dopo. | C6 |
| D4 | L'holdout è **per strumento** o **per strategia**? | Per strumento + granularità: una data di taglio sola, uguale per tutte le strategie su quello strumento. È più semplice e più severo: nessuna strategia viene provata su quel pezzo di storico. | C3 |

---

## 2. Ordine di lavoro

L'ordine segue le dipendenze, non il numero del cantiere:

```text
C1a  gate "net ≥ 0" in promote()          subito, non dipende da niente
C7c  colonna R nei trade                  serve a C4 e C5
C2   scheda strategia                     serve a C1b, C3, C6
C5   banda Monte Carlo + baseline         serve a C1b, C3, C6
C3   holdout + gate SIM → DEMO            usa C2 e C5
C1b  banda e serie di perdite in promote  usa C2 e C5
C7d  push e verify senza mix              prima della prima demo vera
C4   motore correlazioni + filtri         usa C3 (solo periodo di sviluppo)
C6   live: ramp, protezioni, avvisi       usa C2, C5
C7a  giorno della settimana               quando si vuole
C7b  commissioni e financing              quando si vuole
```

---

## C1. Gate sulla performance in `promote()`

**Obiettivo.** Il server reale non promuove una strategia in perdita in demo,
né una che in demo è andata peggio di quanto la simulazione permetteva.

### C1a – net ≥ 0 (S)

- **Scelte.** `judge()` in `web/livesessions.py` calcola già `net` sui trade
  chiusi del record: `promote()` lo aggiunge ai controlli.
- **File.** `web/livesessions.py` (`promote`), `etc/settings.py`
  (`PROMOTE_MIN_NET`, default `0`).
- **Dati.** Nessun formato nuovo. Il motivo del rifiuto va in `need`, come gli
  altri: `"net on demo ≥ 0, it has -123.40"`.
- **Pagina.** Niente di nuovo: la pagina live dell'archivio mostra già il
  verdetto e `need`.
- **Test.** `tests/real_money_test.py`: record con net negativo rifiutato, con
  net positivo accettato, gli altri controlli invariati.
- **Fatto quando.** Un record in perdita torna con `ok: false` e il motivo.
- **Dipende da.** Niente.

### C1b – banda e serie di perdite (S, dopo C2 e C5)

- **Scelte.**
  - `record()` aggiunge a ogni sessione il suo capitale (`capitalOf(meta)`),
    così ogni trade diventa un rendimento: `pl / capitale`.
  - Il push al server reale (`web/servers.py`, `push`) porta anche la scheda
    di riferimento (C2).
  - `promote()` mette in fila i trade demo di tutte le sessioni per tempo e
    costruisce la curva cumulata. Controlla che:
    - ogni punto stia sopra il 5° percentile della banda nello stesso punto;
    - la peggiore serie di perdite non superi quella della scheda.
  - Senza scheda: rifiutato ("no reference card"). Con `PROMOTE_NEEDS_CARD`
    a `0` si torna al comportamento di C1a, per la transizione.
- **File.** `web/livesessions.py` (`record`, `promote`), `web/servers.py`
  (`push`, `record`), `web/mcp.py` (schema di `push_record`), `etc/settings.py`.
- **Test.** Curva dentro la banda: promossa. Un punto sotto il 5°: rifiutata
  con il numero del trade. Serie troppo lunga: rifiutata. Scheda mancante:
  rifiutata.
- **Fatto quando.** Il verdetto nomina il primo trade fuori banda o la serie
  troppo lunga.
- **Dipende da.** C2, C5.

---

## C2. Scheda strategia (M)

**Obiettivo.** Ogni versione ha una scheda che dice in che stato è, da dove
viene, con che numeri va giudicata e cosa le è successo.

- **Scelte.**
  - Identità (D1): `id = sha1(codeHash + groupKey(fields))[:16]`. L'etichetta
    `M1502 v3` è contata per strategia + strumento + granularità.
  - Un modulo solo cambia lo stato: `cards.move(id, to, why, by)`. Aggiunge
    una riga allo storico e rifiuta i passaggi che non esistono (la tabella
    di PROCESSO.md § 8).
  - La scheda viaggia con la strategia: PC → archivio con `sync.py push`,
    archivio → server di trade con il push del form. I server di trade
    aggiornano lo stato (promozione, SUSPENDED, DEAD); l'archivio lo legge
    con `live_status` e aggiorna la sua copia.
  - Conflitti: vince la riga di storico più recente, e le righe si uniscono
    senza doppioni. `ponytail:` fusione per data; un proprietario per stato
    se due server cambiano la stessa scheda nello stesso minuto.
- **Dati.** `DATA_DIR/cards/<id>.json`:

  ```json
  {
    "id": "3f2a…", "label": "M1502 v3",
    "strategy": "M1502", "codeHash": "…", "fields": {"instrument": "EUR_USD", "granularity": "M15", "…": "…"},
    "state": "SIM",
    "history": [{"at": 1759000000000, "from": null, "to": "SIM", "why": "frozen from set 20260926-…/41", "by": "pc"}],
    "source": {"sweep": "20260926-…", "n": 41},
    "reference": {
      "trades": 214, "pf": 1.38, "expectancy": 12.1, "expectancyR": 0.18,
      "winRate": 0.46, "maxDD": 812.0, "maxDDpct": 7.9, "worstStreak": 7,
      "band": {"p5": [], "p50": [], "p95": []},
      "sample": {"from": "2015-01-01", "to": "2024-03-01"}
    },
    "holdout": {"cut": "2024-03-01", "opened": true, "verdict": {}, "attempt": 1},
    "tries": {"combos": 1840, "filters": 12}
  }
  ```

- **File.** Nuovo `web/cards.py` (salva, legge, `move`, id);
  `web/service.py` (`/api/cards`, `/api/cards/<id>`); `scripts/sync.py` (la
  scheda con il run); `web/servers.py` (la scheda con il form); `web/mcp.py`
  (`list_cards`, `get_card` in lettura per gli assistenti; il tool di push
  accetta la scheda); `web/static/run.html` e `app.js` (etichetta e stato del
  run, pannello con lo storico); `web/static/live.js` (stato accanto a ogni
  form); `i18n/it.json`.
- **Test.** Nuovo `tests/cards_test.py`: id stabile; passaggi validi e non
  validi; storico che si unisce senza doppioni; la scheda arriva dal PC
  all'archivio e dall'archivio al server di trade (con i server finti che
  `servers_test.py` usa già).
- **Fatto quando.** Un run si congela in una scheda, la scheda arriva fino al
  server reale con lo storico intero, e ogni cambio di stato vi resta
  scritto.
- **Dipende da.** D1.

---

## C3. Holdout nello sweep + gate SIM → DEMO (M)

**Obiettivo.** Nessuna simulazione vede l'ultimo pezzo di storico, tranne una
volta per versione, al gate. Il gate è un pulsante che fa tutti i controlli
e scrive il verdetto nella scheda.

- **Scelte.**
  - Registro `DATA_DIR/holdout.json`: per strumento + granularità (D4), la
    data di taglio e i tentativi.
  - Taglio di default: `min(fine − 25% della durata, fine − 365 giorni)`,
    scritto alla prima scheda di quello strumento e poi fisso. I dati nuovi
    che arrivano dopo allungano l'holdout, non lo spostano.
  - Ogni backtest e ogni sweep con `to` oltre il taglio viene **tagliato**, con
    un avviso sulla pagina: `holdout starts 2024-03-01: the run stops there`.
    Solo il gate può leggere oltre.
  - Il conteggio delle combinazioni provate (somma dei set) va nel registro e
    poi nella scheda.
  - **Il gate**, pulsante sulla pagina di un run preferito. In ordine:
    1. controlli sullo sviluppo: ≥ 100 trade; limite basso del bootstrap del
       PF > 1; altopiano (i vicini nella griglia del set, cioè le righe che
       differiscono di un passo in un parametro solo, hanno PF > 1); PF senza
       i 3 trade migliori > 1; baseline casuale (C5) sopra il 95° percentile;
    2. solo se passano tutti: lo stesso form sull'holdout, una volta. Net > 0,
       PF ≥ 0.7 × sviluppo, DD ≤ 1.5 × sviluppo;
    3. il verdetto va nella scheda (`holdout.opened = true`). Se passa, la
       scheda riceve il riferimento (C5) ed è pronta per la demo; se no conta
       un tentativo;
    4. al terzo tentativo fallito per strategia + strumento + granularità,
       nuove schede rifiutate: `DEAD`.
  - Il push a un server demo (`servers.push`) rifiuta un form senza una
    scheda che ha passato il gate (`GATE_REQUIRED`, default `1`).
- **File.** Nuovo `performance/gate.py` (controlli puri, senza HTTP);
  `web/service.py` (taglio in `backtest` e `startSweep`, route
  `/api/gate`); `web/cards.py`; `web/servers.py` (rifiuto del push);
  `web/static/sim.js` e `app.js` (taglio disegnato sul grafico e nel form,
  pulsante del gate, dialog con il verdetto riga per riga); `etc/settings.py`
  (`HOLDOUT_SHARE=0.25`, `HOLDOUT_MIN_DAYS=365`, `GATE_MIN_TRADES=100`,
  `GATE_PF_LOW=1.0`, `GATE_HOLDOUT_PF_RATIO=0.7`, `GATE_HOLDOUT_DD_RATIO=1.5`,
  `GATE_MAX_VERSIONS=3`); `i18n/it.json`.
- **Test.** Nuovo `tests/gate_test.py`: ogni controllo che passa e che non
  passa; holdout aperto una volta sola; terzo tentativo = DEAD. In
  `tests/sweep_test.py`: uno sweep oltre il taglio viene tagliato.
- **Fatto quando.** Nessuno sweep legge oltre il taglio; il gate gira una
  volta per versione e il verdetto sta nella scheda; il push in demo lo esige.
- **Dipende da.** C2, C5, D4.

---

## C4. Motore correlazioni + filtri d'ingresso (L)

**Obiettivo.** Trovare condizioni all'ingresso che separano i trade buoni da
quelli cattivi e provarle come parametri dello sweep, per **qualunque**
strategia.

### C4a – filtro d'ingresso generico (M)

Senza questo pezzo un candidato non ha dove andare.

- **Scelte.**
  - Nuova opzione del conto `filters`, come `session` e `news`: una o più
    condizioni `indicatore operatore soglia`, per esempio `rsi14<55`,
    `atrpct14>0.3`, `slope100>0`.
  - Si valuta sull'ultima barra chiusa dello stream della strategia, quando
    arriva il `SIGNAL`. Se non passa, il segnale viene rifiutato con una riga
    nel log (`SIGNAL IGNORED: filter rsi14<55 (61.2)`).
  - Un handler nuovo, `portfolio/filters.py` (`EntryFilter`), legge i
    `CANDLE` e tiene le serie. Il money manager lo interroga come fa con il
    calendario. È lo stesso oggetto in backtest, live e ombra.
  - Nello sweep: `none;rsi14<50;rsi14<55` sono tre valori della griglia.
  - `filters` entra nel `groupKey`: una versione con un filtro è un form
    diverso.
- **File.** `portfolio/filters.py`; `portfolio/moneymanager.py`;
  `backtest/ledger.py` (`run(filters=…)`, handler prima del money manager);
  `web/service.py` (`backtestArgs`, campi dello sweep); `scripts/live.py`
  (lo stesso filtro dal form); `web/static/sim.js` (campo nel form).
- **Test.** Nuovo `tests/filters_test.py`: stessa decisione offline e live;
  la barra letta è quella chiusa (nessuno sguardo al futuro); una soglia
  sbagliata viene rifiutata con un messaggio chiaro.

### C4b – catalogo delle caratteristiche (S)

- **Scelte.** `lib/indicators.py` oggi ha `sma`, `ema`, `atr`, `bollinger`,
  `slope`, `stdev`: niente RSI né ADX. Si aggiungono `rsi` e `adx`, più
  caratteristiche derivate, ognuna con un nome fisso: `rsi14`, `adx14`,
  `atrpct14` (ATR in % del prezzo), `dist_sma100_atr` (distanza dalla media in
  ATR), `range_atr14` (ampiezza della barra in ATR), `slope100`, `hour`,
  `weekday`.
- **Poi (C4b2).** Gli indicatori AI abilitati, calcolati nella sandbox sulle
  candele del run, come già si fa per disegnarli sul grafico.
- **Test.** Valori noti su serie piccole scritte a mano.

### C4c – l'analisi (M)

- **Scelte.** Nuovo `performance/entry.py`, funzioni pure:
  1. i trade del run, solo del periodo di sviluppo;
  2. per ogni trade, le caratteristiche sulla barra del segnale;
  3. per ogni caratteristica, 5 fasce (quintili). Per fascia: n, win rate,
     expectancy in R (C7c), PF, intervallo bootstrap dell'expectancy. La
     funzione `block_ci` di `scripts/entry_excursions.py` si sposta in
     `performance/` e la usano tutti e due;
  4. candidato = una fascia con n ≥ 30 il cui intervallo sta tutto sopra
     l'expectancy complessiva. La soglia è il bordo del quintile;
  5. in testa ai risultati: "N caratteristiche provate → circa N × 5% escono
     buone per caso".
- **Pagina.** Nella pagina del run, un pannello "entry conditions": una riga
  per caratteristica con l'expectancy per fascia, poi i candidati. Il
  pulsante "try in a sweep" apre la pagina di simulazione con i campi del run
  e `filters` già riempito (`none;rsi14<50;rsi14<55;rsi14<60`).
- **API/MCP.** `/api/run/entry?sweep=&n=`; tool MCP `get_entry_analysis` in
  lettura, così un assistente può proporre filtri.
- **Test.** Nuovo `tests/entry_test.py`: trade sintetici in cui RSI < 50
  vince → il candidato esce; trade casuali → nessun candidato.

- **Fatto quando (C4 intero).** Il filtro funziona uguale in sweep, backtest e
  live. L'analisi dà candidati, e il giro sweep → analisi → sweep si fa tutto
  dalle pagine.
- **Dipende da.** C3 (periodo di sviluppo), C7c (R).

---

## C5. Banda Monte Carlo + baseline casuale (M)

**Obiettivo.** Un metro per giudicare demo e live (la banda) e un test contro
il caso (la baseline).

- **Banda.** Nuovo `performance/montecarlo.py`:
  - `band(trades, n=1000, seed=…)`. Ogni trade diventa un rendimento
    `pl / saldo prima del trade`, così vale anche con il sizing a rischio.
  - Si pesca con reinserimento, e per ogni posizione `i` si tengono il 5°, il
    50° e il 95° percentile della curva cumulata.
  - Lunghezza: i trade di sviluppo + holdout, o di più se la demo è più
    lunga.
  - Nello stesso giro: la distribuzione della peggiore serie di perdite.
- **Baseline.** Nuovo `performance/baseline.py`:
  - ingressi casuali con il profilo della strategia: lo stesso numero di
    trade, le ore d'ingresso pescate da quelle della strategia, la stessa
    proporzione long/short, distanze di stop e target pescate dai suoi trade,
    la stessa durata massima;
  - si risolvono sulle stesse candele (le fini, se ci sono) con
    `backtest/resolution.resolve_exit`, che legge già ask e bid;
  - 200 ripetizioni. Il risultato è il percentile del PF della strategia.
  - `ponytail:` ignora "un trade alla volta" e i filtri del money manager;
    se serve più fedeltà, si fa girare una strategia `RandomEntries` dentro
    `ledger.run`.
- **Usata da.** Gate (C3), scheda (C2), `promote` (C1b), protezioni live (C6).
- **Pagina.** Sul grafico del capitale del run, la banda in grigio. Nel dialog
  del gate, il percentile della baseline.
- **Test.** Nuovo `tests/montecarlo_test.py`: risultati uguali con lo stesso
  seed; la banda si allarga man mano che si va avanti coi trade; una
  strategia casuale finisce intorno al 50° percentile della baseline.
- **Fatto quando.** La scheda ha la banda, e il gate il percentile.
- **Dipende da.** C7c (R, per la versione in R).

---

## C6. Live: ramp, protezioni per strategia, monitoraggio, avvisi (M)

**Obiettivo.** Soldi veri a piccoli passi, con una strategia fermata da sola
quando esce da quello che la simulazione permetteva.

- **Ramp (D2).**
  - Sul server reale, `start()` propone nel campo capitale il 25% del
    capitale target della scheda.
  - I metadati della sessione dicono `ramp` o `full`.
  - Dopo `RAMP_TRADES` (30) trade chiusi dentro la banda, la pagina live
    mostra "ready for full size". Il pulsante ferma la sessione e riavvia il
    form al 100%, con la conferma del capitale.
- **Protezioni.** Il giro di `watch()` che ogni minuto controlla il loss limit
  controlla anche ogni sessione che ha una scheda:

  | Condizione | Azione |
  |---|---|
  | DD della sessione > `LIVE_DD_RATIO` (1.5) × `maxDDpct` della scheda | `stop(session)` + scheda `DEAD` |
  | curva sotto il 5° percentile della banda | `stop(session)` + `SUSPENDED` |
  | serie di perdite > `LIVE_STREAK_RATIO` (1.5) × `worstStreak` | `stop(session)` + `SUSPENDED` |
  | seconda sospensione | `DEAD` |

  `stop(session)` annulla già gli ordini della sessione e chiude i suoi trade.
- **SUSPENDED → DEMO.** Il form si rimanda in demo. Per una nuova promozione
  contano solo le sessioni demo dopo la sospensione.
- **Monitoraggio.** Nella pagina live, per ogni form, un pannello "vs card":
  ultimi 50 trade (PF, expectancy, win rate) contro la scheda, la curva con
  la banda, il DD di adesso. Si calcola quando si apre la pagina: nessun cron.
- **Avvisi (D3).** Nuovo `web/notify.py`:
  - `notify(kind, text)` scrive sempre nel log del servizio e in
    `DATA_DIR/alerts.json`, che le pagine live e logs mostrano come banner;
  - email con `smtplib` se `.env` ha `SMTP_HOST`, `SMTP_USER`, `SMTP_TO`;
  - eventi: cambio di stato, protezione scattata, loss limit, allarme di
    parità.
- **File.** `web/livesessions.py` (`watch`, `guard`, `start`, `record`);
  `web/cards.py`; `web/notify.py`; `etc/settings.py` (`RAMP_SHARE=0.25`,
  `RAMP_TRADES=30`, `LIVE_DD_RATIO=1.5`, `LIVE_STREAK_RATIO=1.5`, `SMTP_*`);
  `web/static/live.js`, `live.html`, `logs.js`; `i18n/it.json`.
- **Test.** `tests/real_money_test.py` e `tests/livesessions_test.py`: una
  sessione finta che supera ogni soglia viene fermata con lo stato giusto; la
  seconda sospensione dà DEAD; il ramp propone il 25%. Nuovo
  `tests/notify_test.py`: banner scritto sempre, email solo se configurata
  (SMTP finto).
- **Fatto quando.** Ogni soglia ferma la sessione con lo stato giusto nella
  scheda e un avviso visibile; il ramp propone il 25% e poi il 100%.
- **Dipende da.** C2, C5, D2, D3.

---

## C7. Cantieri piccoli

### C7a – giorno della settimana nello sweep (S)

- Opzione del conto `weekdays` (per esempio `1-5`, oppure i giorni uno per
  uno). Il segnale fuori giorno viene rifiutato come fa `session`. Entra
  nello sweep e nel `groupKey`.
- Nella pagina: sette segmenti nello stile `Y | N` di `web/DESIGN.md`.
- File: `portfolio/moneymanager.py`, `backtest/ledger.py`, `web/service.py`,
  `scripts/live.py`, `web/static/sim.js`. Test: un segnale del sabato
  rifiutato, offline e live.

### C7b – commissioni e financing (S)

- Commissione per broker (`COMMISSION_<PROVIDER>`, per lotto o per trade),
  applicata dal simulatore all'apertura e alla chiusura. Vale anche per
  l'ombra live, quindi la parità resta.
- Financing: tasso overnight fisso per strumento, long e short, da
  `settings`; default 0. `ponytail:` tassi fissi, non storici.
- Il report mostra il totale dei costi.
- File: `backtest/oanda.py`, `etc/settings.py`, `performance/report.py`.
  Test: un trade tenuto tre notti paga tre volte il financing.

### C7c – colonna R ed export CSV (S)

- `Ledger.trades()` aggiunge `r = pl / (|entry − stop iniziale| × |units|)`.
- Il report aggiunge R medio ed expectancy in R; la tabella dei trade, una
  colonna R.
- Pulsante "CSV" nella pagina del run: i trade con data, direzione, entrata,
  uscita, P&L, R, motivo d'uscita.
- File: `backtest/ledger.py`, `performance/report.py`, `web/service.py`,
  `web/static/app.js`. Test: R di un trade chiuso sullo stop = −1.

### C7d – push e verify senza mix (S)

- `sync.py push --run <sweep>/<n>`, e `--favourites` per i preferiti: stessi
  pezzi del push di un mix, senza il mix.
- Sull'archivio, pulsante "verify" nella pagina del run: usa
  `Service.verify`, che esiste già.
- File: `scripts/sync.py`, `web/service.py` (route), `web/static/app.js`.
  Test: in `tests/sync_test.py`, un run spinto da solo arriva con la sua
  strategia e i suoi indicatori.

---

## 3. Cosa la fase 1 non fa

Mix, portafoglio e più strumenti: [ROADMAP.md](ROADMAP.md). La fase 1 è
finita quando una strategia fa tutto il giro con i gate automatici:

1. SIM, con holdout e correlazioni;
2. gate, con la scheda;
3. DEMO, con verify;
4. promozione giudicata sui numeri;
5. LIVE in ramp e poi piena, sotto protezione.
