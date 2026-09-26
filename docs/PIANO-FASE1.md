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
- **Nessuna dipendenza nuova**: stdlib, numpy e pandas che ci sono già, e i
  comandi che l'immagine Docker ha già (`openssl`). Una sola eccezione, piccola:
  la libreria MIT per il QR, copiata in `web/static/qrcode.js` (C8).
- **Test**: `unittest` in `tests/`, uno per cantiere almeno; `tests/app_check.js`
  dopo ogni modifica ad `app.js` o `menu.js`. Screenshot con Chrome headless
  per le pagine toccate.
- **Deploy**: solo con `deploy-prod.sh`, dopo la suite verde su dev.
- **`DEAD` lo decide l'utente.** La piattaforma blocca un passaggio, ferma
  una sessione e propone; non scarta mai una strategia da sola.
- **Gli sweep in SIM sono liberi**: nessun limite al numero di set e nessun
  conteggio che pesi sul giudizio.
- **Ogni azione lascia una voce nel diario** (C2). Chi aggiunge un'azione
  nuova (un pulsante, un controllo, una protezione) aggiunge anche la sua
  voce.

---

## 1. Decisioni

Servono prima di partire con i cantieri indicati. Quelle prese lo dicono;
per le altre c'è la mia proposta.

| # | Domanda | Proposta | Serve a |
|---|---|---|---|
| D1 | Quando nasce una **versione** nuova? | **Deciso il 2026-09-26.** Al gate SIM → DEMO. La versione è l'impronta del codice (hash del sorgente della strategia e degli indicatori che usa, senza commenti e righe vuote) più i parametri congelati (`groupKey` dei campi del form). In SIM i parametri cambiano senza creare versioni. Etichetta leggibile: `M1502 v3`, contata per strategia + strumento + granularità. Nessun limite al numero di versioni. | C2, C3 |
| D2 | Il **ramp**: chi lo sceglie, quanto dura, chi passa al 100%? | **Deciso il 2026-09-26.** Lo sceglie l'utente all'avvio del live, con la casella "ramp" accesa di default: accesa, il capitale proposto è il 25%; spenta, il 100%. Finisce al primo tra `RAMP_TRADES` (30) trade chiusi e `RAMP_DAYS` (60) giorni, tutti e due modificabili all'avvio: su D1 30 trade sarebbero un anno. Il 100% è un clic dell'utente, possibile in qualsiasi momento purché la sessione non abbia trade aperti; prima della fine del ramp chiede conferma e mostra a che punto è. | C6 |
| D3 | Che **canali** usano gli avvisi? | **Deciso il 2026-09-26.** Banner nelle pagine e riga nel log, sempre e per tutti gli eventi. In più, solo per gli urgenti: email (`SMTP_*`), Telegram (`TELEGRAM_*`) e notifiche sul telefono, abbinato con un QR code, con una pagina che mostra i trade in corso. Ognuno si accende se configurato. Vale per il server demo e per il reale. | C8 |
| D4 | L'holdout è **per strumento** o **per strategia**? | **Deciso il 2026-09-26.** Di default un taglio per strumento, uguale per tutte le granularità (il mercato è lo stesso) e per tutte le strategie: nessuno sweep guarda lì. L'utente può dare a una strategia il suo taglio, dalla pagina del diario. | C3 |
| D5 | Un diario per **strategia** o per strategia + strumento? | **Deciso il 2026-09-26.** Per strategia, cioè per nome del codice. Ogni voce dice strumento e granularità e la pagina filtra: "va su FX, non sulle azioni" si legge nello stesso posto. Le versioni restano per strategia + strumento + granularità (D1). | C2 |
| D6 | La **demo** esige il gate? | **Deciso il 2026-09-26.** No: un form va in demo anche senza gate, con l'avviso "no gate" nella pagina live e nel diario. La promozione al server reale esige la scheda, e una scheda nasce solo dal gate: una demo senza gate non arriva al live. | C3, C1b |
| D7 | Il **taglio dell'holdout** si sposta? | **Deciso il 2026-09-26.** Resta fisso finché non lo sposti tu: quello dello strumento dalla pagina settings, quello di una strategia dal suo diario. Avanti o indietro, ma lasciando sempre almeno `HOLDOUT_MIN_DAYS` di holdout. Ogni spostamento va nel diario. Se il nuovo holdout contiene dati già letti da sweep o gate, il gate lo dice: è un'informazione, non un blocco. | C3 |
| D8 | Il minimo di trade in demo sui **timeframe lenti** | **Deciso il 2026-09-26.** Il record basta con `PROMOTE_TRADES` (30) trade chiusi, oppure dopo `PROMOTE_SLOW_DAYS` (90) giorni se ne ha almeno `PROMOTE_MIN_TRADES` (10). `PROMOTE_DAYS` (20) resta. Su D1, con circa 30 trade l'anno, la demo dura circa 4 mesi invece di un anno. | C1a |

---

## 2. Ordine di lavoro

L'ordine segue le dipendenze, non il numero del cantiere:

```text
C1a  net ≥ 0 e trade minimi in promote()  fatto (7d93728)
C7c  colonna R nei trade                  fatto (0ca8c06)
C8   avvisi + pagina per il telefono      fatto (21cc77b)
C2   diario + scheda                      fatto (e21e911)
C5   banda Monte Carlo + baseline         fatto (0cc433b)
C3   holdout + gate SIM → DEMO            fatto (f3208a0)
C1b  banda e serie di perdite in promote  fatto (6e69885)
C7d  push e verify senza mix              fatto (96668e1)
C4   motore correlazioni + filtri         fatto (352bec2)
C6   live: ramp, protezioni               fatto (564fc96)
C7a  giorno della settimana               fatto (cc0b5d9)
C7b  commissioni e financing              fatto
```

---

## C1. Gate sulla performance in `promote()`

**Obiettivo.** Il server reale non promuove una strategia in perdita in demo,
né una che in demo è andata peggio di quanto la simulazione permetteva.

### C1a – net ≥ 0 e trade minimi sui timeframe lenti (S)

- **Scelte.**
  - `judge()` in `web/livesessions.py` calcola già `net` sui trade chiusi del
    record: `promote()` lo aggiunge ai controlli.
  - Trade minimi (D8): `trades ≥ PROMOTE_TRADES`, oppure
    `days ≥ PROMOTE_SLOW_DAYS` e `trades ≥ PROMOTE_MIN_TRADES`. `judge()`
    dà già `days` e `trades`.
- **File.** `web/livesessions.py` (`promote`), `etc/settings.py`
  (`PROMOTE_MIN_NET=0`, `PROMOTE_SLOW_DAYS=90`, `PROMOTE_MIN_TRADES=10`).
- **Dati.** Nessun formato nuovo. Il motivo del rifiuto va in `need`, come gli
  altri: `"net on demo ≥ 0, it has -123.40"`,
  `"30 closed trades, or 10 after 90 days: it has 8"`.
- **Pagina.** Niente di nuovo: la pagina live dell'archivio mostra già il
  verdetto e `need`.
- **Test.** `tests/real_money_test.py`: record con net negativo rifiutato, con
  net positivo accettato; 12 trade in 95 giorni accettato, 8 trade in 95
  giorni rifiutato, 12 trade in 40 giorni rifiutato; gli altri controlli
  invariati.
- **Fatto quando.** Un record in perdita torna con `ok: false` e il motivo; una
  strategia D1 passa dopo 90 giorni con 10 trade.
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
- **Com'è stato fatto.** Una scheda senza riferimento (gate non passato)
  non basta. La scheda cambia stato da sola dove il passaggio è un fatto:
  SIM → DEMO quando la form parte su un conto demo o va a un server demo,
  DEMO → LIVE quando parte sui soldi veri. `DEAD` resta solo dell'utente.
- **Dipende da.** C2, C5.

---

## C2. Diario della strategia + scheda della versione (L)

**Obiettivo.** Ogni strategia ha un **diario tenuto da parity-deriva**: ogni
cosa fatta su quella strategia vi resta scritta, con data, numeri e link,
dall'idea fino al live o allo scarto. L'utente può annotare le voci, se
vuole. Ogni versione che passa il gate ha una **scheda**: lo stato e i numeri
con cui va giudicata.

- **Scelte.**
  - **Un server solo.** Il diario registra quello che succede sul server
    dove gira. Le azioni verso un altro server (push in demo, push al server
    reale con il verdetto che torna) sono voci di questo server; quello che
    succede sull'altro server resta nel diario di quello. Unire i diari di
    più server è nella [roadmap](ROADMAP.md#il-diario-su-più-server).
  - **Un diario per strategia** (D5), cioè per nome del codice: `M1502`. Ogni
    voce dice strumento e granularità; la pagina filtra. Le versioni di una
    strategia caricata da un assistente (`MY-EMA 1`, `MY-EMA 2`) scrivono
    nello stesso diario, `MY-EMA`, e ogni voce dice quale versione era.
  - **Lo scrive parity-deriva.** L'utente non deve scrivere niente. Le note
    sono facoltative: attaccate a una voce o libere, con un segno facoltativo
    👍 / 👎 / ➖.
  - **Si aggiunge e basta**: una voce non si modifica e non si cancella; una
    nota corretta è una nota nuova. L'id è casuale (`uuid4`), così quando si
    uniranno i diari di più server non ci saranno collisioni.
    `ponytail:` un file per strategia; se diventa troppo grande, uno per
    anno.
  - **Nasce da solo** con la prima voce di una strategia. La prima voce è
    l'idea: il `DESCRIPTION` della classe.
  - Una voce porta i suoi numeri principali: si legge anche quando il set è
    cancellato o nella cantina. Il link lo riprende dalla cantina.
  - Una voce salva `kind` e numeri, non una frase: la frase la compone la
    pagina, in inglese o tradotta con `it.json`.
  - Il live non scrive una voce per trade: la pagina fa il riassunto per
    settimana dai trade delle sessioni, quando si apre. Nessun cron.
- **Le voci e chi le scrive.**

  | voce | livello | scritta da |
  |---|---|---|
  | idea (`DESCRIPTION`) | tappa | prima voce della strategia |
  | bozza da un assistente, bozza abilitata | tappa | `submit_strategy` in `web/mcp.py`; abilitazione dalla pagina settings |
  | codice cambiato, con il sorgente | esperimento | backtest e sweep, quando `codeHash` è diverso da quello dell'ultima voce |
  | set finito o fermato: nome, griglia, combinazioni, miglior score e PF | esperimento | `saveSweep` |
  | set cancellato | esperimento | `deleteSweep` |
  | run salvato | esperimento | `saveRun` |
  | preferito messo, tolto, nota del preferito | esperimento | `addFavourite`, `dropFavourite`, `noteFavourite` |
  | entrata in un mix, mix cancellato | tappa | `saveMix`, `dropMix` |
  | verify: uguale, o il primo trade diverso | tappa | `verify` |
  | gate: verdetto riga per riga | tappa | gate (C3) |
  | nuova versione, cambio di stato | tappa | `cards.move` |
  | taglio dell'holdout spostato, o taglio proprio della strategia | tappa | pagina settings, pagina del diario (D4, D7) |
  | push a un server di trade, verdetto della promozione | tappa | `servers.push`, `servers.record` |
  | sessione avviata o fermata, con account e capitale | tappa | `livesessions.start`, `stop`, `stopAll` |
  | protezione scattata, loss limit, allarme di parità | tappa | `guard`, `watch`, monitor di parità, protezioni (C6) |
  | nota dell'utente | – | pagina o MCP |

- **La scheda della versione.**
  - Nasce al gate (D1). Identità: `id = sha1(codeHash + groupKey(fields))[:16]`.
    `codeHash` è lo sha1 del sorgente della strategia, dei moduli di
    `parity_deriva.strategy` e degli indicatori che importa (trovati con
    `ast`), letti come albero sintattico (`ast.dump`, senza docstring): un
    commento, una riga vuota o una docstring non creano una versione. L'etichetta `M1502 v3` è contata per
    strategia + strumento + granularità.
  - Un modulo solo cambia lo stato: `cards.move(id, to, why, by)`. Rifiuta i
    passaggi che non esistono (la tabella di PROCESSO.md § 8), accetta `DEAD`
    solo da un utente (pulsante "discard"), mai da un controllo automatico,
    e scrive ogni cambio nel diario. Lo storico sta nel diario, non nella
    scheda.
  - La scheda va con il record al server reale (`push_record`), come
    riferimento: `promote()` ne tiene una copia (`cards.receive`), e C1b e C6
    la leggono lì. Un server demo la scheda non la riceve: la demo non la
    usa. Ogni server tiene lo stato della sua copia; allinearli è nella
    roadmap, insieme al diario.
- **Dati.**
  - `DATA_DIR/journal/<strategia>.jsonl`, una riga per voce:

    ```json
    {"id": "9c1e…", "at": 1759000000000, "kind": "sweep", "level": "experiment",
     "instrument": "EUR_USD", "granularity": "M15", "version": null,
     "data": {"name": "atr exit", "combos": 48, "bestScore": 71, "pf": 1.31},
     "link": {"kind": "sweep", "id": "20260921-…"}, "by": "parity-deriva"}
    ```

    Una nota: `{"kind": "note", "about": "9c1e…", "mark": "up", "text": "…", "by": "<utente>"}`.
    Codice cambiato: `data.source` tiene i sorgenti, così il diff è tra due
    voci e non serve altro.
  - `DATA_DIR/cards/<id>.json`:

    ```json
    {
      "id": "3f2a…", "label": "M1502 v3",
      "strategy": "M1502", "codeHash": "…", "fields": {"instrument": "EUR_USD", "granularity": "M15", "…": "…"},
      "state": "DEMO",
      "source": {"sweep": "20260926-…", "n": 41},
      "reference": {
        "trades": 214, "pf": 1.38, "expectancy": 12.1, "expectancyR": 0.18,
        "winRate": 0.46, "maxDD": 812.0, "maxDDpct": 7.9, "worstStreak": 7,
        "band": {"p5": [], "p50": [], "p95": []},
        "sample": {"from": "2015-01-01", "to": "2024-03-01"}
      },
      "holdout": {"cut": "2024-03-01", "opened": true, "verdict": {}, "openings": 2}
    }
    ```

- **Pagine.** Nuove `journal.html` e `journal.js`, voce "Journal" nel menu.
  - La lista dei diari: strategia, ultima attività, righe "in corso".
  - Un diario:
    - in cima le righe **in corso**: le versioni con il loro stato, le
      sessioni aperte, i mix che la contengono;
    - sotto la **storia**, con i filtri tappe / esperimenti / tutto e per
      strumento;
    - una nota su qualsiasi voce;
    - il diff del codice tra due voci "codice cambiato";
    - il riassunto settimanale del live.
  - La ricerca su tutti i diari, anche quelli delle strategie `DEAD`.
  - Un link "journal" dalle pagine di un set, di un run e live. Il mix ha più
    strategie: è il diario che rimanda al mix, con la voce "in the mix".
- **API.** `/api/journals`, `/api/journal?strategy=`, `/api/journal/note`
  (POST), `/api/journals/search?q=`; `/api/cards`, `/api/cards/<id>`,
  `/api/cards/move` (POST: i pulsanti "back to SIM", "back to demo",
  "discard" della pagina).
- **MCP.** In lettura `list_journals`, `get_journal`, `search_journals`,
  `list_cards`, `get_card`. `add_note` su tutti i server tranne quello reale.
- **Dati che ci sono già.** `scripts/journal_backfill.py`, da lanciare una
  volta: ricostruisce i diari da set, run salvati, preferiti, mix e sessioni
  già su disco, con le loro date. Gli id vengono dalla sorgente (sha1 del
  file e della riga), così rilanciarlo non crea doppioni.
- **File.** Nuovi `web/journal.py` (`add`, `read`, `search`, `note`),
  `web/cards.py`, `scripts/journal_backfill.py`, `web/static/journal.html`,
  `web/static/journal.js`. Da toccare: `web/service.py` (`journal.add` nelle
  funzioni della tabella, le route), `web/livesessions.py`, `web/servers.py`,
  `web/mcp.py`, `web/static/menu.js`, `run.html`, `app.js`, `live.js`,
  `i18n/it.json`.
- **Test.**
  - Nuovo `tests/journal_test.py`:
    - ogni funzione della tabella lascia la sua voce, senza che l'utente
      scriva niente;
    - "codice cambiato" solo se l'hash cambia: un commento non conta;
    - una nota si attacca a una voce;
    - la ricerca trova voci e note;
    - il backfill rilanciato non crea doppioni;
    - una voce si legge anche dopo aver cancellato il suo set.
  - Nuovo `tests/cards_test.py`: id stabile; passaggi validi e non validi;
    `DEAD` solo da un utente; ogni cambio di stato scrive nel diario; la
    scheda arriva al server di trade con il push (con i server finti di
    `servers_test.py`).
  - `tests/app_check.js` per `menu.js`.
- **Fatto quando.** Parti da una strategia nuova e fai set, preferiti, un
  mix, il gate, la demo: il diario ha tutto, in ordine, senza che tu abbia
  scritto una riga. Puoi annotare qualsiasi voce.
- **Dipende da.** D1, D5.

---

## C3. Holdout nello sweep + gate SIM → DEMO (M)

**Obiettivo.** Nessuna simulazione vede l'ultimo pezzo di storico, tranne una
volta per versione, al gate. Il gate è un pulsante che fa tutti i controlli
e scrive il verdetto nella scheda.

- **Scelte.**
  - Registro `DATA_DIR/holdout.json` (D4): per strumento il taglio e le sue
    aperture, più i tagli propri delle strategie che ne hanno uno:

    ```json
    {"EUR_USD": {"cut": "2023-09-01", "openings": 3,
                 "strategies": {"M1502": {"cut": "2024-06-01", "openings": 1}}}}
    ```

  - Taglio di default: `min(fine − 25% della durata, fine − 365 giorni)`,
    calcolato sulla granularità del primo backtest o sweep sullo strumento
    e fissato in quel momento, prima che uno sweep legga qualcosa. Da lì
    vale per tutte le granularità. I dati nuovi che arrivano dopo allungano
    l'holdout, non lo spostano.
  - Vale il taglio della strategia, se ne ha uno; se no quello dello
    strumento.
  - I tagli li sposta solo l'utente (D7): quello dello strumento dalla pagina
    settings, quello di una strategia dal suo diario. Avanti o indietro,
    lasciando sempre almeno `HOLDOUT_MIN_DAYS` di holdout. Un taglio nuovo
    parte con zero aperture.
  - **Holdout già letto.** Il gate guarda i set e i run salvati su quello
    strumento, di tutte le strategie: se qualcuno ha letto oltre il taglio
    (set fatti prima di C3, un taglio spostato indietro, il taglio proprio di
    un'altra strategia), il dialog lo dice: `holdout read by 4 sets, up to
    2025-01-10`. È un'informazione, non un blocco.
  - Ogni backtest e ogni sweep con `to` oltre il taglio viene **tagliato**, con
    un avviso sulla pagina: `holdout starts 2024-03-01: the run stops there`.
    Il taglio sta in `_backtest`, da cui passano pagina, sweep e MCP; un run
    tutto nell'holdout è rifiutato, e un set lo è prima di partire. Leggono
    oltre solo il gate e `verify`, che rifà un run spinto da un altro server
    così com'era.
  - Gli sweep sul periodo di sviluppo non hanno limiti e non si contano.
  - **Il gate**, pulsante sulla pagina di un run preferito. In ordine:
    1. controlli sullo sviluppo: ≥ 100 trade; limite basso del bootstrap del
       PF > 1; altopiano (i vicini nella griglia del set, cioè le righe che
       differiscono di un passo in un parametro solo, hanno PF > 1); PF senza
       i 3 trade migliori > 1; baseline casuale (C5) sopra il 95° percentile;
    2. solo se passano tutti: lo stesso form sull'holdout, una volta. Net > 0,
       PF ≥ 0.7 × sviluppo, DD ≤ 1.5 × sviluppo;
    3. il verdetto va nella scheda (`holdout.opened = true`) e il registro
       conta un'apertura in più. Se passa, la scheda riceve il riferimento
       (C5) ed è pronta per la demo; se no torna a `SIM`;
    4. il dialog del verdetto mostra quante volte quel taglio è già stato
       aperto e se l'holdout era già stato letto. Nessun limite: riprovare o
       scartare (`DEAD`) lo decide l'utente.
  - Il push a un server demo (`servers.push`) accetta anche un form senza
    gate (D6): la pagina live mostra "no gate" e il diario lo scrive. Al
    server reale arriva solo con una scheda (C1b).
- **File.** Nuovo `performance/gate.py` (controlli puri, senza HTTP);
  `web/service.py` (taglio in `backtest` e `startSweep`, route
  `/api/gate`); `web/cards.py`; `web/servers.py` ("no gate" nel push);
  `web/static/sim.js` e `app.js` (taglio disegnato sul grafico e nel form,
  pulsante del gate, dialog con il verdetto riga per riga); `settings.js`
  (il taglio dello strumento), `journal.js` (il taglio della strategia);
  `etc/settings.py`
  (`HOLDOUT_SHARE=0.25`, `HOLDOUT_MIN_DAYS=365`, `GATE_MIN_TRADES=100`,
  `GATE_PF_LOW=1.0`, `GATE_HOLDOUT_PF_RATIO=0.7`, `GATE_HOLDOUT_DD_RATIO=1.5`);
  `i18n/it.json`.
- **Test.** Nuovo `tests/gate_test.py`: ogni controllo che passa e che non
  passa; holdout aperto una volta sola per versione; il contatore delle aperture
  cresce; nessun `DEAD` automatico. In
  `tests/sweep_test.py`: uno sweep oltre il taglio viene tagliato, M15 e H4
  dello stesso strumento con lo stesso taglio. Il taglio proprio di una
  strategia vale solo per lei; nessun taglio lascia meno di
  `HOLDOUT_MIN_DAYS`; un set salvato che ha letto oltre il taglio compare
  nel dialog del gate; un push in demo senza gate passa con l'avviso.
- **Fatto quando.** Nessuno sweep legge oltre il taglio; il gate gira una
  volta per versione e il verdetto sta nella scheda; una demo senza gate si
  può fare, ma senza scheda non arriva al live.
- **Dipende da.** C2, C5, D4, D6, D7.

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
  - Nello sweep: `none, rsi14<50, rsi14<55` sono tre valori della griglia
    (virgole, come gli altri campi); più condizioni in un valore si uniscono
    con `&`: `rsi14<55&hour>=7`.
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
- **Com'è stato fatto.** Le caratteristiche stanno in `portfolio/features.py`,
  lette una barra chiusa alla volta da `lib/streaming.Series` (che ha già RSI,
  ATR e medie, e dal 2026-09-26 l'ADX di Wilder): le usano il filtro e
  l'analisi, stesso codice.
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
     l'expectancy complessiva. La soglia è il bordo del quintile. Su trade
     vinti a caso, 20 prove danno in media meno candidati di quanti ne
     annuncia il punto 5 (il test lo controlla);
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
  - si risolvono sulle stesse candele del run con la regola di
    `backtest/resolution.py` (un livello è toccato se sta nel range della
    barra, dal lato che chiude la posizione), scritta con numpy: con
    `resolve_exit`, che scorre le barre con `iterrows`, 200 ripetizioni
    durerebbero minuti. Una barra che tocca stop e target insieme è uno stop;
  - 200 ripetizioni. Il risultato è il percentile del PF della strategia.
  - `ponytail:` ignora "un trade alla volta" e i filtri del money manager;
    se serve più fedeltà, si fa girare una strategia `RandomEntries` dentro
    `ledger.run`.
- **Usata da.** Gate (C3), scheda (C2), `promote` (C1b), protezioni live (C6).
- **Pagina.** Sul grafico del capitale del run, la banda in grigio
  (`/api/run/band`, un punto a ogni chiusura). Nel dialog del gate, il
  percentile della baseline (C3).
- **Test.** Nuovo `tests/montecarlo_test.py`: risultati uguali con lo stesso
  seed; la banda si allarga man mano che si va avanti coi trade; una
  strategia casuale finisce intorno al 50° percentile della baseline.
- **Fatto quando.** La scheda ha la banda, e il gate il percentile.
- **Dipende da.** C7c (R, per la versione in R).

---

## C6. Live: ramp, protezioni per strategia, monitoraggio (M)

**Obiettivo.** Soldi veri a piccoli passi, con una strategia fermata da sola
quando esce da quello che la simulazione permetteva.

- **Ramp (D2).**
  - All'avvio sul server reale, la casella "ramp" (accesa di default)
    decide il capitale proposto: il 25% (`RAMP_SHARE`) del capitale della
    scheda, oppure il 100%.
  - Accanto, la durata: `RAMP_TRADES` (30) trade o `RAMP_DAYS` (60) giorni,
    il primo dei due, modificabili. La pagina stima quando finirà dai trade
    al mese della scheda: "2.5 trades a month: the ramp ends by days, after
    about 5 trades".
  - I metadati della sessione dicono `ramp` (con i due limiti) o `full`. La
    scelta va nel diario.
  - A ramp finito la pagina live mostra "ready for full size". Il pulsante
    "full size" si può premere in qualsiasi momento, purché la sessione non
    abbia trade aperti: ferma la sessione e riavvia il form al 100%, con la
    conferma del capitale. Prima della fine del ramp chiede conferma e
    mostra a che punto è: trade, giorni, banda.
- **Protezioni.** Il giro di `watch()` che ogni minuto controlla il loss limit
  controlla anche ogni sessione che ha una scheda:

  | Condizione | Azione |
  |---|---|
  | DD della sessione > `LIVE_DD_RATIO` (1.5) × `maxDDpct` della scheda | `stop(session)` + `SUSPENDED`, con la proposta di scartarla |
  | curva sotto il 5° percentile della banda | `stop(session)` + `SUSPENDED` |
  | serie di perdite > `LIVE_STREAK_RATIO` (1.5) × `worstStreak` | `stop(session)` + `SUSPENDED` |

  `stop(session)` annulla già gli ordini della sessione e chiude i suoi trade.
- **Da SUSPENDED decide l'utente**, con tre pulsanti sulla pagina live:
  rimanda in demo (per una nuova promozione contano solo le sessioni demo
  dopo la sospensione), torna a SIM, scarta (`DEAD`). Dopo un DD oltre
  `LIVE_DD_RATIO` o alla seconda sospensione la pagina propone di scartarla,
  senza farlo.
- **Monitoraggio.** Nella pagina live, per ogni form, un pannello "vs card":
  ultimi 50 trade (PF, expectancy, win rate) contro la scheda, la curva con
  la banda, il DD di adesso. Si calcola quando si apre la pagina: nessun cron.
- **Avvisi.** Ogni protezione scattata e ogni cambio di stato chiamano
  `notify()` (C8).
- **File.** `web/livesessions.py` (`watch`, `guard`, `start`, `record`);
  `web/cards.py`; `etc/settings.py` (`RAMP_SHARE=0.25`, `RAMP_TRADES=30`,
  `RAMP_DAYS=60`, `LIVE_DD_RATIO=1.5`, `LIVE_STREAK_RATIO=1.5`);
  `web/static/live.js`, `live.html`; `i18n/it.json`.
- **Test.** `tests/real_money_test.py` e `tests/livesessions_test.py`: una
  sessione finta che supera ogni soglia viene fermata e va in `SUSPENDED`;
  nessuna soglia mette `DEAD` da sola; il ramp propone il 25%, o il 100% se
  è spento; finisce al primo tra trade e giorni; "full size" non si preme
  con trade aperti; ogni soglia chiama `notify()`.
- **Fatto quando.** Ogni soglia ferma la sessione con lo stato giusto nella
  scheda e un avviso visibile; il ramp propone il 25% e poi il 100%.
- **Com'è stato fatto.** Il ramp è un campo della form, `ramp:
  TRADE/GIORNI/CAPITALE PIENO`, fuori dal `groupKey`: la sessione in ramp e
  quella piena sono la stessa form per il record e la promozione. La stima di
  quando finisce sta nel dettaglio della sessione, dai trade al mese della
  scheda. Le protezioni girano sul server dei soldi veri, sulle sessioni
  la cui scheda è `LIVE`. Resta da fare: "per una nuova promozione contano
  solo le sessioni demo dopo la sospensione" chiede che l'archivio sappia
  della sospensione, che è sul server reale - va con il diario su più server
  ([ROADMAP.md](ROADMAP.md#il-diario-su-più-server)).
- **Dipende da.** C2, C5, C8, D2.

---

## C7. Cantieri piccoli

### C7a – giorno della settimana nello sweep (S)

- Opzione del conto `weekdays`: le cifre ISO dei giorni (1 lunedì … 7
  domenica), `12345` da lunedì a venerdì - senza trattini né virgole, che
  nella griglia separano i valori. Il segnale fuori giorno viene rifiutato
  come fa `session`. Entra nello sweep e nel `groupKey`.
- Nella pagina: sette segmenti nello stile `Y | N` di `docs/web/DESIGN.md`.
- File: `portfolio/moneymanager.py`, `backtest/ledger.py`, `web/service.py`,
  `scripts/live.py`, `web/static/sim.js`. Test: un segnale del sabato
  rifiutato, offline e live.

### C7b – commissioni e financing (S)

- Commissione per broker (`COMMISSION_<PROVIDER>`, per lotto o per trade),
  applicata dal simulatore all'apertura e alla chiusura. Vale anche per
  l'ombra live, quindi la parità resta.
- Financing: tasso overnight fisso per strumento, long e short, da
  `settings`; default 0. `ponytail:` tassi fissi, non storici.
- Il report mostra il totale dei costi. Il P&L di un trade è già netto: nel
  backtest e nell'ombra del live, così la parità resta. Una notte è un
  passaggio delle 17:00 di New York; il mercoledì non conta triplo.
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

## C8. Avvisi e pagina per il telefono (M)

**Obiettivo.** Sapere subito, anche lontano dal computer, quando una sessione
demo o live ha un problema, e vedere dal telefono i trade in corso. Vale per
il server demo e per quello reale.

- **Eventi e livelli.**

  | evento | livello | da dove |
  |---|---|---|
  | sessione fermata da sola: il processo è morto e non l'ha fermata l'utente | urgente | `summary()` lo sa già (`exited`) |
  | nessuna candela nuova da 3 intervalli, a mercato aperto | urgente | `lastBar` della sessione |
  | ordini rifiutati, errori del broker | urgente | `errors` e `ORDER_REJECT` negli eventi della sessione |
  | loss limit del giorno | urgente | `guard` |
  | allarme di parità: `halt` urgente, `warn` informativo | – | monitor di parità |
  | protezione scattata (C6) | urgente | il giro di ogni minuto |
  | cambio di stato, "ready for full size", verdetto della promozione | informativo | `cards.move`, ramp (C6), `servers.record` |

- **Scelte.**
  - Nuovo `web/notify.py`, una funzione sola: `notify(level, kind, key, data)`.
    - Scrive sempre nel log del servizio e in `DATA_DIR/alerts.json`: le
      pagine live e logs lo mostrano come banner.
    - Gli urgenti vanno anche su email, Telegram e telefono, quelli
      configurati.
  - **Una volta sola per evento.** La chiave `(sessione, tipo)` resta aperta
    finché il problema c'è: il giro di ogni minuto non rimanda niente. Quando
    si risolve (candele ripartite, sessione riavviata) parte un "resolved".
  - Il giro di ogni minuto oggi parte solo sul server reale, per il loss
    limit (`scripts/web.py` → `watch`). Parte su ogni server con sessioni
    live e controlla anche gli eventi della tabella. Il loss limit resta solo
    sul reale.
  - `ponytail:` "mercato aperto" = non sabato e domenica UTC; gli orari di
    borsa delle azioni quando servono.
  - Il testo porta etichetta, evento e numeri
    (`M1502 v3 · EUR_USD M15 · stopped: DD 12% > 1.5× card`), mai account,
    token o chiavi.
- **Email.** `smtplib` (stdlib), con `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
  `SMTP_PASSWORD`, `SMTP_TO` in `.env`.
- **Telegram.** Un POST alla Bot API con `urllib` (stdlib), con
  `TELEGRAM_TOKEN` (il bot si crea con BotFather) e `TELEGRAM_CHAT` in `.env`.
- **Il telefono.**
  - **Abbinamento con un QR.** Pagina settings, tab "alerts", "pair a
    phone": il server crea un codice monouso valido 10 minuti e mostra il QR
    di `<PUBLIC_URL>/phone?pair=<codice>`. Il codice si può anche scrivere a
    mano nella pagina del telefono: su iPhone l'app nella schermata Home non
    ha i cookie di Safari, quindi si abbina da lì. Il telefono lo apre e il codice
    diventa un token del dispositivo, in un cookie `HttpOnly`, `Secure`,
    `SameSite=Strict`. Sul server se ne tiene solo lo sha256, in
    `DATA_DIR/phones.json`, con nome, data e ultimo accesso. Dalla stessa tab
    si vedono i telefoni abbinati e si revocano.
  - **Notifiche.** La pagina del telefono ha il pulsante "Enable
    notifications": il browser chiede il permesso, e vuole un clic. Si usa
    Web Push, lo standard dei browser, con le chiavi VAPID del server.
    - Il push parte **vuoto**: sveglia il telefono e basta. Il service worker
      legge l'avviso da `/api/phone/alerts` con il suo cookie e mostra la
      notifica. I servizi push di Google e Apple, da cui il messaggio passa,
      non vedono né trade né numeri, e il contenuto non va cifrato.
    - La firma VAPID (ES256) la fa il comando `openssl`, che l'immagine
      Docker ha già (il setup lo usa per i certificati): nessuna dipendenza
      Python nuova. Le chiavi nascono al primo abbinamento, in
      `DATA_DIR/vapid.pem`. `ponytail:` un processo `openssl` per push; se i
      push diventano tanti, una libreria.
  - **La pagina `/phone`.** In cima "demo", o "REAL MONEY" in rosso come le
    altre pagine. Sotto: le sessioni aperte con il P&L di oggi e quello
    totale, i trade aperti (strumento, direzione, entrata, stop), gli ultimi
    avvisi. Si aggiorna ogni 30 secondi mentre è aperta. Solo lettura:
    nessun pulsante che avvia o ferma.
  - **Come un'app.** Un manifest e un'icona: sul telefono si aggiunge alla
    schermata home, con il nome del ruolo del server ("parity demo",
    "parity REAL"). Un telefono si abbina a ogni server che vuole: demo e
    reale sono due icone.
  - **Accesso.** `/phone`, `/phone-sw.js` (alla radice, così il suo scope è
    tutto il servizio), `/phone-manifest.json` e `/api/phone/*` passano prima
    del cancello di `web/access.py`, come `/mcp`, perché il telefono non ha il
    certificato client; `/static/` è già pubblico. Tutte tranne
    l'abbinamento vogliono il token del dispositivo. Dove nginx chiede il
    certificato, serve la stessa eccezione di `/mcp` (README, "Alerts, and a
    phone paired with a trade server").
  - **Limiti.**
    - Il telefono parla con il server di trade al suo indirizzo pubblico
      (`PARITY_DERIVA_PUBLIC_URL`). Un server di trade ha sempre un
      indirizzo pubblico in https: è un requisito di parity-deriva
      ([ARCHITECTURE.md](ARCHITECTURE.md)). La pagina settings lo dice in
      rosso se un server con il ruolo trade non ce l'ha.
    - iPhone: le notifiche web funzionano da iOS 16.4, e solo dopo
      "Aggiungi a schermata Home". Android: da Chrome, anche senza.
- **QR.** Lo disegna il browser, con una piccola libreria MIT copiata in
  `web/static/qrcode.js` (qrcode-generator 1.4.4 di Kazuhiko Arase, 56 KB;
  `sendFile` serve solo file piatti in `web/static`): nessuna CDN, le pagine
  funzionano anche offline.
- **File.** Nuovi `web/notify.py`, `web/phone.py` (abbinamento, token, VAPID,
  push, il manifest), `web/static/phone.html`, `phone.js`, `phone-sw.js`,
  `alerts.js` (i banner), `qrcode.js`. Toccati: `web/livesessions.py` (il
  giro di ogni minuto, gli ordini rifiutati contati), `scripts/web.py` (lo fa
  partire su ogni server), `web/service.py` (route), `settings.html` e
  `settings.js` (tab "alerts", "send a test alert"), `live.html` e
  `logs.html` (banner), `app.css`, `etc/settings.py` (`SMTP_*`,
  `TELEGRAM_*`, `ALERT_STALE_BARS=3`), `i18n/it.json`, README.
- **Test.**
  - Nuovo `tests/notify_test.py`: un evento parte una volta sola, e
    "resolved" una volta; banner sempre; email e Telegram solo se
    configurati (SMTP e HTTP finti); il testo non contiene account né token;
    una sessione finta con il processo morto e una con le candele ferme
    fanno partire l'urgente giusto.
  - Nuovo `tests/phone_test.py`: il codice è monouso e scade; il token è
    salvato solo come hash; la revoca funziona; senza token le rotte del
    telefono rispondono 401; la firma VAPID si verifica con `openssl`; un
    server trade senza `PARITY_DERIVA_PUBLIC_URL` viene segnalato.
  - Il pulsante "send a test alert" nella tab, per provarlo davvero sul
    telefono.
- **Fatto quando.** Abbini il telefono con il QR e accetti le notifiche; una
  sessione demo che muore ti arriva sul telefono entro un minuto; la pagina
  mostra i trade aperti. Lo stesso sul server reale.
- **Dipende da.** Niente, per gli eventi che ci sono già: sessione morta,
  candele ferme, ordini rifiutati, loss limit, parità. Protezioni e cambi di
  stato si aggiungono con C6 e C2.

---

## 3. Cosa la fase 1 non fa

Mix, portafoglio, più strumenti e il diario su più server:
[ROADMAP.md](ROADMAP.md). La fase 1 è
finita quando una strategia fa tutto il giro con i gate automatici:

1. SIM, con holdout e correlazioni;
2. gate, con la scheda;
3. DEMO, con verify;
4. promozione giudicata sui numeri;
5. LIVE in ramp e poi piena, sotto protezione;
6. e il diario racconta tutto il giro, senza che nessuno l'abbia scritto.
