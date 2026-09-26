# parity-deriva – Da un'idea a un trade live · fase 1: singola strategia

Come **una** strategia nasce, viene simulata, provata in demo e alla fine va a
soldi veri. Con il dettaglio di tutto quello che succede **prima di andare
live**.

Il piano ha due fasi:

1. **singola strategia**: questo documento, ed è quella su cui si lavora ora;
2. **mix**, più strategie sullo stesso strumento: in [ROADMAP.md](ROADMAP.md),
   con quello che viene dopo.

Legenda usata in tutto il documento:

- ✅ **c'è già** nel codice
- 🔧 **da fare**

I numeri dei gate (100 trade, 25%, 1.5×, …) sono proposte: si tarano nel
tempo, stanno in un posto solo (`etc/settings.py`).

---

## 1. Il quadro in una pagina

```text
IDEA → STRATEGIA (codice + parametri)
     → SIM   : sweep ↔ correlazioni, in loop, sul periodo di sviluppo
     → GATE  : holdout, aperto una volta
     → DEMO  : stesso broker del live, parametri congelati
     → GATE  : promozione, giudicata dal server reale
     → LIVE  : ramp al 25% se lo scegli, poi 100% con un tuo clic
     → sempre sotto protezione: SUSPENDED se qualcosa va storto
     → DEAD solo quando lo decidi tu
```

Niente paper trading: la demo gira già sul broker vero.

### Gli stati di una strategia

| Stato | Vuol dire | Parametri |
|---|---|---|
| `SIM` | in simulazione: sweep, correlazioni, loop | liberi |
| `DEMO` | gira su un account demo | congelati |
| `LIVE` | soldi veri, in ramp (25%) se lo scegli, poi piena | congelati |
| `SUSPENDED` | fermata da una protezione, da riverificare | congelati |
| `DEAD` | scartata, sempre da te: la piattaforma non scarta mai da sola | – |

ALIVE = `LIVE`.

**Versioni.** Ogni strategia ha nome e versione (es. `M1502 v3`). Da `DEMO`
in poi cambiare codice o parametri crea una nuova versione, che riparte da
`SIM`. Le versioni si contano (`v1`, `v2`, …), senza limite.

**Il diario.** 🔧 Ogni strategia ha un diario tenuto da parity-deriva: set,
preferiti, codice cambiato, mix, gate, versioni, demo, live, protezioni,
ognuno con data, numeri e link. L'utente può annotare qualsiasi voce, se
vuole. Per ora il diario sta sul server dove succedono le cose; su più
server è nella [roadmap](ROADMAP.md#il-diario-su-più-server).

### Chi fa cosa (ruoli dei server)

| Ruolo | Dove di solito | Nel processo |
|---|---|---|
| **Test** | PC di casa o cloud | SIM, sweep, correlazioni |
| **Archivio** | cloud | tiene strategie, simulazioni, dati; rifà i run (verify); manda i form ai server di trade; legge i loro trade ogni 5 minuti |
| **Trade demo** | cloud, sempre con indirizzo pubblico | DEMO |
| **Trade real** | cloud, server separato, sempre con indirizzo pubblico | LIVE |

Un server di trade ha **sempre un indirizzo pubblico in https**: l'archivio
ci manda i form e lo legge ogni 5 minuti, il telefono ci prende avvisi e
trade aperti. È un requisito di parity-deriva, non un'opzione: un PC di casa
senza indirizzo pubblico archivia e simula, non fa trading.

Demo o soldi veri non si sceglie da una pagina: sta in `.env`
(`PARITY_DERIVA_ACCOUNTS`). Un server demo non può diventare reale con un
click. ✅

---

## 2. Dall'idea alla strategia

Una strategia è **codice + parametri**.

- Scritta a mano in `strategy/`, oppure proposta da un assistente AI via MCP
  (`submit_strategy`). Quella dell'assistente arriva come bozza e si
  abilita a mano dalla pagina settings. ✅
- Gli indicatori degli assistenti (`submit_indicator`) vengono controllati
  in sandbox e abilitati dalla pagina settings. ✅
- Nel `DESCRIPTION` della classe stanno le regole. ✅ In più servono due
  righe: 🔧
  - **Ipotesi**: perché dovrebbe funzionare.
  - **Non opera quando**: in quali condizioni deve stare ferma.
- Alla prima simulazione nasce il **diario** della strategia, con l'idea
  come prima voce. 🔧

Dati su cui gira: candele ask/bid vere dalla cartella di mercato, calendario
economico, archivio delle barre servite dai broker. ✅

Vale anche per l'azionario: lo strumento può essere un titolo, il processo
non cambia.

---

## 3. SIM – il loop di simulazione

```text
strategia → sweep → correlazioni → nuovo filtro = nuovo parametro → sweep → …
                                          ↓ niente di nuovo
                                   gate su holdout
```

### 3.1 Sviluppo e holdout 🔧

Lo storico si taglia in due:

- **sviluppo**: la parte vecchia. Sweep e correlazioni vedono solo questa.
- **holdout**: l'ultimo ~25%, almeno 1 anno. Nessuno lo guarda durante il
  loop.

Di default il taglio è **uno per strumento**: la stessa data per tutte le
granularità e tutte le strategie, così nessuno sweep guarda lì. Lo sposti tu
quando vuoi, e a una strategia puoi dare il suo taglio; resta sempre almeno
1 anno di holdout, e ogni spostamento va nel diario. Se il nuovo holdout
contiene dati già letti da qualche sweep, il gate lo dice. 🔧

L'holdout si apre **una volta per versione**. Se la versione non passa,
torna a `SIM`: riprovare con una versione nuova o scartare la strategia
(`DEAD`) lo decidi tu. La piattaforma conta quante volte l'holdout di quello
strumento è stato aperto e lo mostra nel gate: più aperture, meno l'holdout
è un dato mai visto. È un'informazione, non un blocco.

Gli sweep sul periodo di sviluppo sono **liberi**: quanti ne servono, anche
tanti quando i parametri spostano molto i risultati. Non si contano e non
pesano sul giudizio. Dall'overfitting proteggono l'holdout, che nessuno sweep
vede, e l'altopiano al gate: il punto scelto deve avere vicini buoni anche
loro.

### 3.2 Sweep ✅

Una lista in un campo = un backtest per valore. Fino a 500 combinazioni per
set (`MAX_COMBOS`).

Cosa si può variare:

- i parametri della strategia
- le opzioni del conto:
  - `session`: ore UTC in cui accetta segnali
  - `news`: finestra attorno agli eventi del calendario
  - `intraday`, `maxBars`: chiusura a fine giornata, durata massima
  - `slScale`, `tpScale`, `maxStopPips`: stop e target
  - `trailing`, `trailProfit`, `trailPips`: trailing stop
  - `inverse`: ordini girati
- lo strumento

Ore e giorni **non sono una fase**: sono opzioni dello sweep. Le ore ci sono
già (`session`); il giorno della settimana è da aggiungere. 🔧

### 3.3 Leggere i risultati ✅

- **Tabella del set**: score 0–100, trade, ROI, PF, max DD, Sharpe.
- **`paramEffects`**: quanto pesa ogni parametro, se l'effetto è costante
  nel tempo, quali valori vincono nelle diverse fette di storico.
- **Pagina del run**: grafico, trade uno per uno con zoom, drawdown, heatmap
  giorno × ora, eventi del calendario vicini ai trade.
- **Preferiti**: si mette la stella a un run, con una nota.

Regola: scegliere un **altopiano** (parametri vicini buoni anche loro), non
il picco isolato.

### 3.4 Correlazioni indicatori ↔ profit/loss 🔧

Il motore che manca. Trova valori di indicatori all'ingresso che separano i
trade buoni da quelli cattivi.

1. Prende i trade di un run, solo del periodo di sviluppo.
2. Per ogni trade calcola gli indicatori sulla **barra chiusa prima del
   segnale**, così non guarda nel futuro. Catalogo: `lib/indicators.py` più
   gli indicatori AI abilitati. Il regime di mercato (slope, ATR,
   volatilità) è un indicatore come gli altri.
3. Divide i trade in 5 fasce per indicatore. Per ogni fascia: numero di
   trade, win rate, expectancy in R, profit factor.
4. Intervallo di confidenza con bootstrap (si riusa `block_ci` di
   `scripts/entry_excursions.py`).
5. Esce una classifica di **filtri candidati**, es.
   `RSI14 < 55 → expectancy +0.3R, 240 trade, CI [+0.1, +0.5]`.

Le fasce con meno di 30 trade si ignorano. Un candidato **non è mai una
regola**: diventa un nuovo parametro dello sweep e deve superare l'holdout.
Con 20 indicatori provati, circa uno esce "buono" per puro caso.

### 3.5 Il loop

Candidato → parametro dello sweep → nuovo sweep → di nuovo correlazioni.
Ci si ferma quando niente migliora in modo chiaro. Meno filtri = meno
overfitting.

### 3.6 Gate SIM → DEMO

Sul **periodo di sviluppo**:

| Controllo | Soglia | Stato |
|---|---|---|
| numero di trade | ≥ 100 | 🔧 |
| PF, limite basso del bootstrap al 90% | > 1 | 🔧 |
| altopiano: i vicini nella griglia | PF > 1 | 🔧 (oggi si guarda a occhio) |
| PF togliendo i 3 trade migliori | > 1 | 🔧 |
| baseline casuale: stessi exit e sizing, ingressi a caso alla stessa ora | PF sopra il 95° percentile dei casuali | 🔧 |

Sull'**holdout**, aperto una volta:

| Controllo | Soglia | Stato |
|---|---|---|
| net | > 0 | 🔧 |
| PF holdout | ≥ 0.7 × PF sviluppo | 🔧 |
| max DD holdout | ≤ 1.5 × max DD sviluppo | 🔧 |

Costi: lo spread c'è già, perché i fill sono sui veri ask/bid. ✅
Commissioni e financing oggi sono a zero. 🔧

### 3.7 La scheda di riferimento 🔧

Esce dal gate. È il metro con cui si giudicano la demo e il live:

- PF, expectancy, win rate, max DD, peggiore serie di perdite
- **banda Monte Carlo**: rimescolando l'ordine dei trade si ottengono il 5°
  e il 95° percentile della curva di equity, trade per trade
- quante volte l'holdout di quello strumento era già stato aperto (informazione)
Un JSON per versione in `DATA_DIR`, scritto dalla piattaforma. Lo storico
dei cambi di stato (data, da, a, perché) va nel diario.

---

## 4. Dal PC all'archivio ✅

1. Il Test (PC) manda all'archivio il run scelto, con il suo set, la
   strategia e gli indicatori: `python scripts/sync.py push`.
2. Le strategie e gli indicatori arrivano come bozze: si abilitano a mano.
3. **Verify**: sull'archivio il run viene rifatto con il codice e le candele
   dell'archivio. Si confrontano i trade: "uguali" oppure il primo trade in
   cui divergono.

Quello che va a tradare è esattamente quello che è stato simulato.

Oggi push e verify passano dal mix: `sync.py push` manda i mix, e verify sta
nella pagina del mix. Per una strategia sola si fa un **mix con un solo run**,
e funziona. ✅ Push e verify di un run senza mix: piccolo cantiere. 🔧

---

## 5. DEMO

1. Dalla pagina live dell'archivio si manda un **form** (un preferito) al
   server demo: codice della strategia, indicatori, run del set da cui
   parte. ✅
2. Sul server demo si abilita il codice e si avvia la sessione. ✅
3. Il **monitor di parità** confronta la demo con un simulatore ombra sulle
   stesse candele. ✅ Se divergono troppo scatta l'allarme: `warn` o `halt`.
   Un allarme di parità è un problema di **esecuzione**, non di edge: si
   sistema l'esecuzione e si resta in DEMO.
4. L'archivio legge le sessioni del server demo ogni 5 minuti e le mostra
   sulla sua pagina live. ✅
5. Confronto della demo con la scheda di riferimento (banda, serie di
   perdite). 🔧

Parametri congelati: se cambiano è una nuova versione, e si torna a SIM.

In demo si può andare anche **senza gate**, per provare in fretta: la
pagina lo dice ("no gate"). Senza gate però non c'è la scheda, e senza
scheda il server reale non promuove. 🔧

---

## 6. Prima di andare live – il dettaglio

Questa è la parte che protegge i soldi. La promozione **la giudica il server
reale**, non chi la chiede.

### 6.1 La sequenza

| # | Chi | Cosa succede | Stato |
|---|---|---|---|
| 1 | Server demo | la sessione accumula giorni e trade chiusi | ✅ |
| 2 | Archivio | raccoglie il **record demo**: le sessioni del form sull'archivio e su tutti i server demo | ✅ |
| 3 | Archivio | dalla pagina live, push del form al server reale, con il record (`push_record`), usando il token `promote` di quel server | ✅ |
| 4 | Server reale | giudica il record da solo, con le sue soglie (`promote()`) | ✅ in parte, vedi 6.2 |
| 5 | Server reale | salva il giudizio come prova (`promotions/*.json`), con la lista di cosa manca se è bocciato | ✅ |
| 6 | Server reale | il codice della strategia arriva come bozza: si abilita a mano | ✅ |
| 7 | Server reale | avvio: rifiuta un form non promosso e chiede di confermare il capitale a rischio | ✅ |
| 8 | Server reale | propone il ramp: 25% del capitale, se lo lasci acceso | 🔧 |

### 6.2 La checklist del gate DEMO → LIVE

| Controllo | Soglia | Stato | Se non passa |
|---|---|---|---|
| giorni in demo | ≥ 20 (`PROMOTE_DAYS`) | ✅ | resta in DEMO |
| trade chiusi in demo | ≥ 30 (`PROMOTE_TRADES`); oppure ≥ 10 dopo 90 giorni, per i timeframe lenti | ✅ 30 · 🔧 10 in 90 giorni | resta in DEMO |
| allarmi di parità | 0 | ✅ | sistemare l'esecuzione, resta in DEMO |
| scheda: gate SIM → DEMO passato | sì | 🔧 | rifiutato: prima il gate |
| solo account demo nel record | sì | ✅ | rifiutato |
| net in demo | ≥ 0 | 🔧 | non promossa; poi decidi tu: più demo, SIM o DEAD |
| curva demo dentro la banda Monte Carlo | mai sotto il 5° percentile | 🔧 | non promossa; poi decidi tu |
| serie di perdite | ≤ la peggiore della scheda | 🔧 | non promossa; poi decidi tu |

**Il buco più importante oggi:** `promote()` controlla giorni, trade e
allarmi, ma non il risultato. Il net viene calcolato e nessuno lo guarda.
Una strategia in perdita in demo può passare. Primo cantiere.

### 6.3 Le protezioni già accese sul server reale ✅

- Il server reale accetta via MCP solo quello che l'archivio promuove:
  nessun assistente ci scrive strategie o lancia backtest.
- **Loss limit giornaliero**: al 3% di perdita nel giorno
  (`DAILY_LOSS_PCT`) ferma tutte le sessioni e non ne riavvia nessuna prima
  del giorno UTC dopo.
- **Stop all**: un pulsante ferma tutto a mano.
- **Allarme di parità** con azione `halt`: ferma la sessione se il live si
  stacca dalla simulazione.
- L'intestazione di ogni pagina dice "real money server" in rosso.

---

## 7. LIVE

### 7.1 Ramp 🔧

- Lo scegli tu all'avvio del live. È acceso di default.
- Acceso: **25% del capitale** fino al primo tra 30 trade e 60 giorni. Su D1
  30 trade sarebbero un anno.
- Il 100% è un tuo clic, quando la sessione non ha trade aperti. Se lo premi
  prima della fine del ramp, la pagina chiede conferma e mostra a che punto
  sei.
- Il ramp controlla l'esecuzione con soldi veri (slippage, fill, requote),
  non l'edge: quello è già giudicato in SIM e in demo. La banda continua a
  controllare anche a size piena.

### 7.2 Protezioni

| Livello | Evento | Azione | Stato |
|---|---|---|---|
| server | perdita del giorno ≥ 3% | stop di tutte le sessioni fino al giorno dopo | ✅ |
| server | a mano | stop all | ✅ |
| sessione | live diverso dalla simulazione | allarme di parità (warn / halt) | ✅ |
| strategia | DD > 1.5 × max DD della scheda | `SUSPENDED`, con la proposta di scartarla | 🔧 |
| strategia | curva sotto il 5° percentile della banda | `SUSPENDED` | 🔧 |
| strategia | serie di perdite > 1.5 × la peggiore della scheda | `SUSPENDED` | 🔧 |

### 7.3 Monitoraggio

- Curva del capitale per sessione sulla pagina live. ✅
- Ogni settimana: ultimi 50 trade contro la scheda. 🔧
- Avvisi 🔧, oggi non c'è nessun canale di notifica:
  - banner nelle pagine e nel log, sempre;
  - per gli urgenti (sessione morta, candele ferme, ordini rifiutati,
    protezioni, loss limit, parità in halt) anche email, Telegram e
    notifiche sul telefono;
  - il telefono si abbina con un QR code dalla pagina settings; la sua
    pagina mostra i trade in corso. Vale per demo e live.

### 7.4 SUSPENDED

La sessione si ferma subito: ordini annullati, trade chiusi. Poi decidi tu:
riverificare in DEMO, tornare a SIM con una versione nuova, o `DEAD`. La
pagina propone `DEAD` dopo un DD oltre 1.5× quello della scheda o alla
seconda sospensione, ma non lo applica.

---

## 8. Tutti i gate in una tabella

| Passaggio | Controlli | Chi decide |
|---|---|---|
| idea → SIM | regole scritte in modo che il codice le esegua senza dubbi; ipotesi; "non opera quando" | chi scrive la strategia |
| SIM → DEMO | ≥ 100 trade; PF bootstrap basso > 1; altopiano; PF senza i 3 migliori > 1; batte la baseline casuale; holdout: net > 0, PF ≥ 0.7×, DD ≤ 1.5× | la piattaforma 🔧 (oggi a occhio); la demo senza gate si può, il live no |
| DEMO → LIVE | ≥ 20 giorni; ≥ 30 trade (o ≥ 10 dopo 90 giorni); 0 allarmi; solo demo ✅; net ≥ 0; dentro la banda; serie di perdite ok 🔧 | il server reale |
| ramp → 100% | fine del ramp (30 trade o 60 giorni), o prima con conferma; nessun trade aperto | tu 🔧 |
| LIVE → SUSPENDED | sotto la banda; serie di perdite oltre 1.5×; DD oltre 1.5× | la piattaforma 🔧 |
| qualunque stato → DEAD | quando vuoi; la pagina lo propone dopo un DD oltre 1.5× o una seconda sospensione | tu |

---

## 9. Cosa manca, in ordine

1. **Gate performance in `promote()`** + scheda di riferimento. Piccolo.
2. **Diario della strategia + scheda della versione**: tutto quello che si
   fa, scritto da parity-deriva; stato e numeri di ogni versione. Medio-grande.
3. **Holdout nello sweep**, aperto una volta per versione. Medio.
4. **Motore correlazioni** indicatori ↔ P/L. Medio-grande.
5. **Banda Monte Carlo + baseline casuale**. Medio.
6. **Live**: ramp al 25%, protezioni per strategia, ultimi 50 trade.
   Medio.
7. Piccoli: giorno della settimana nello sweep, commissioni e financing,
   colonna R nei trade, push e verify di un run senza mix.
8. **Avvisi e telefono**: banner, email, Telegram, notifiche sul telefono
   abbinato con un QR, pagina con i trade in corso; demo e live. Medio.

Come si fa ogni cantiere, con file, dati, test e ordine di lavoro:
[PIANO-FASE1.md](PIANO-FASE1.md). Il mix e quello che viene dopo:
[ROADMAP.md](ROADMAP.md).

---

## Grafici per claude.ai

Stile comune: tecnico, pulito, sfondo bianco, font sans-serif.
Colori degli stati: SIM arancione, DEMO verde chiaro, LIVE verde pieno,
SUSPENDED ambra, DEAD rosso. Quello che c'è già: linea piena; quello da
fare: linea tratteggiata con etichetta "da fare".

### GRAFICO 1 – Il processo completo
render_generated_image
prompt is Diagramma di flusso del processo "parity-deriva – Da un'idea a un trade live (una strategia)". Stile tecnico professionale, sfondo bianco, font sans-serif chiaro.

Quattro corsie orizzontali (swimlane), una per server, dall'alto in basso: "Test (PC)", "Archivio (cloud)", "Trade DEMO", "Trade REAL".

Nella corsia Test, da sinistra a destra:
1. "Idea" (cerchio grigio)
2. "Strategia = codice + parametri" (rettangolo)
3. Riquadro arancione "SIM" che contiene un ciclo con due blocchi e frecce circolari: "Sweep (periodo di sviluppo)" ↔ "Correlazioni indicatori ↔ P/L (da fare)"
4. Rombo giallo "Gate holdout (una volta per versione)"

Freccia verso la corsia Archivio etichettata "sync.py push":
5. Rettangolo "Verify: rifà il run con codice e candele dell'archivio"

Freccia verso la corsia DEMO etichettata "push form":
6. Riquadro verde chiaro "DEMO – parametri congelati, monitor di parità"

Freccia verso l'Archivio etichettata "record demo", poi verso la corsia REAL etichettata "push_record":
7. Rombo giallo "Gate promozione (giudicato dal server reale)"
8. Riquadro verde "LIVE ramp 25%" → freccia "30 trade o 60 giorni, poi un tuo clic" → riquadro verde scuro "LIVE 100%"

In basso a destra due riquadri: "SUSPENDED" (ambra) e "DEAD" (rosso). Verso SUSPENDED frecce tratteggiate dai riquadri LIVE, etichettate "fuori banda / serie di perdite / DD oltre 1.5×". Verso DEAD frecce tratteggiate dal Gate holdout e da SUSPENDED, con l'icona di una persona e l'etichetta "decidi tu".

Una freccia di ritorno dal Gate holdout alla Strategia etichettata "bocciata: nuova versione, se vuoi". Una freccia da SUSPENDED a DEMO etichettata "riverifica, se vuoi". Una freccia sottile tratteggiata dal riquadro SIM direttamente a DEMO, etichettata "senza gate: demo sì, live no".

Legenda in basso: linea piena = c'è già, linea tratteggiata = da fare.
orientation is landscape
layout is block

### GRAFICO 2 – Il loop di simulazione
render_generated_image
prompt is Diagramma circolare "SIM – il loop di simulazione" di parity-deriva. Stile tecnico pulito, sfondo bianco.

Al centro un grande cerchio con quattro tappe in senso orario, frecce arancioni:
1. "Sweep" – sotto in piccolo: "parametri + ore (session) + news + stop/target + strumento; fino a 500 combinazioni per set, set quanti ne servono"
2. "Leggi i risultati" – sotto: "paramEffects, score, altopiano non picco"
3. "Correlazioni indicatori ↔ profit/loss" (bordo tratteggiato, etichetta "da fare") – sotto: "indicatori sulla barra prima del segnale, 5 fasce, bootstrap"
4. "Filtro candidato → nuovo parametro dello sweep"

Dal cerchio esce una freccia verso destra etichettata "niente migliora più" che porta a un rombo giallo "Gate holdout" con sotto in piccolo: "≥100 trade, PF bootstrap >1, batte la baseline casuale, holdout: net >0, PF ≥0.7×, DD ≤1.5×".

Dal rombo: freccia verde "passa → scheda di riferimento → DEMO"; freccia rossa "bocciata → nuova versione o DEAD, decidi tu".

Nota a lato: "un filtro candidato non è mai una regola: deve superare l'holdout".
orientation is landscape
layout is block

### GRAFICO 3 – Sviluppo e holdout
render_generated_image
prompt is Linea del tempo orizzontale "Come si usa lo storico" per parity-deriva. Stile tecnico pulito, sfondo bianco.

Una barra lunga che rappresenta tutto lo storico di uno strumento, da sinistra (anni vecchi) a destra (oggi).

I primi tre quarti della barra in azzurro, etichetta "SVILUPPO – sweep e correlazioni vedono solo questo".
L'ultimo quarto in grigio scuro con un lucchetto, etichetta "HOLDOUT – almeno 1 anno, aperto una volta per versione".

Sopra la parte azzurra tante piccole frecce che vanno e vengono, etichetta "loop: prova, correggi, riprova – sweep liberi, quanti ne servono".
Sopra la parte grigia una sola freccia con un occhio, etichetta "si guarda una volta".

A destra della barra, fuori dallo storico, un riquadro verde "DEMO – dati nuovi, mai visti".

Sotto la parte grigia un contatore "holdout aperto: 2 volte", con la scritta "si vede nel gate: più aperture, meno è un dato mai visto".
orientation is landscape
layout is block

### GRAFICO 4 – Prima di andare live
render_generated_image
prompt is Diagramma di sequenza "Prima di andare live" per parity-deriva. Stile tecnico pulito, sfondo bianco.

Tre colonne verticali (linee di vita) con in cima un'icona server: "Trade DEMO", "Archivio", "Trade REAL" (la colonna REAL con bordo rosso).

Messaggi dall'alto in basso, frecce orizzontali numerate:
1. Nella colonna DEMO: "sessione demo: giorni e trade chiusi" (freccia che torna su se stessa)
2. DEMO → Archivio: "sessioni lette ogni 5 minuti"
3. Archivio: "raccoglie il record demo"
4. Archivio → REAL: "push form + record (push_record, token promote)"
5. Nella colonna REAL un riquadro grande "Giudizio della promozione" con una checklist:
   ✓ "≥ 20 giorni in demo"
   ✓ "≥ 30 trade chiusi" (☐ "o ≥ 10 dopo 90 giorni", da fare)
   ✓ "0 allarmi di parità"
   ✓ "solo account demo"
   ☐ "net ≥ 0" (da fare)
   ☐ "curva dentro la banda Monte Carlo" (da fare)
   ☐ "serie di perdite ≤ scheda" (da fare)
6. REAL: "salva il giudizio come prova"
7. REAL → Archivio: "verdetto: promosso / cosa manca"
8. REAL: "abilitazione del codice a mano"
9. REAL: "avvio: conferma del capitale a rischio" → "25% del target (da fare)"

In basso nella colonna REAL una fascia rossa "Protezioni sempre accese: loss limit 3% al giorno, stop all, allarme di parità halt".

Legenda: ✓ c'è già, ☐ da fare.
orientation is portrait
layout is block

### GRAFICO 5 – Stati e protezioni in live
render_generated_image
prompt is Diagramma a stati "LIVE: ramp, protezioni, sospensione" per parity-deriva. Stile tecnico pulito, sfondo bianco.

Cinque stati come rettangoli arrotondati:
- "DEMO" (verde chiaro) a sinistra
- "LIVE ramp 25%" (verde) al centro sinistra
- "LIVE 100%" (verde scuro) al centro destra
- "SUSPENDED" (ambra) in basso al centro
- "DEAD" (rosso) in basso a destra

Transizioni come frecce etichettate:
- DEMO → LIVE ramp: "promozione"
- LIVE ramp → LIVE 100%: "30 trade o 60 giorni, poi un tuo clic"
- DEMO → LIVE 100%: "senza ramp, se lo spegni"
- LIVE ramp → SUSPENDED e LIVE 100% → SUSPENDED: "sotto il 5° percentile della banda" oppure "serie di perdite > 1.5× la peggiore"
- LIVE ramp → SUSPENDED e LIVE 100% → SUSPENDED anche per "drawdown > 1.5× max DD della scheda"
- SUSPENDED → DEMO: "decidi tu: si riverifica"
- SUSPENDED → DEAD: "decidi tu", con l'icona di una persona; accanto in piccolo "proposto dopo DD > 1.5× o seconda sospensione"

In alto una fascia orizzontale sopra tutti gli stati live: "livello server (c'è già): loss limit 3% al giorno · stop all · allarme di parità".

Tutte le frecce verso SUSPENDED e DEAD tratteggiate con etichetta "da fare". Nessuna freccia porta a DEAD senza la persona.
orientation is landscape
layout is block

### GRAFICO 6 – La banda Monte Carlo
render_generated_image
prompt is Grafico concettuale "La banda Monte Carlo come metro della demo e del live" per parity-deriva. Stile pulito, sfondo bianco.

Asse orizzontale: "numero di trade" da 0 a 60. Asse verticale: "capitale".

Una fascia grigia chiara che si allarga verso destra, etichetta "banda 5°–95° percentile (trade della simulazione rimescolati)". Al centro della fascia una linea tratteggiata grigia "mediana della simulazione".

Due curve sopra:
- una curva verde che resta dentro la fascia, etichetta "demo / live in linea → avanti"
- una curva rossa che a circa trade 35 scende sotto il bordo basso della fascia, con un punto rosso e l'etichetta "sotto il 5° percentile → SUSPENDED"

Una linea verticale a trade 30 etichettata "fine ramp (o 60 giorni): 25% → 100% con un clic".
orientation is landscape
layout is block

### GRAFICO 7 – Cosa c'è e cosa manca
render_generated_image
prompt is Tabella visuale "parity-deriva: cosa c'è e cosa manca nel processo di una strategia" in stile roadmap. Sfondo bianco, font sans-serif.

Quattro colonne, una per fase: "Strategia", "SIM", "DEMO", "LIVE".

In ogni colonna una pila di tessere. Tessere verdi piene = "c'è già", tessere arancioni tratteggiate = "da fare".

Strategia: verde "codice + DESCRIPTION", verde "bozze degli assistenti AI via MCP"; arancione "ipotesi e 'non opera quando'", arancione "diario della strategia", arancione "scheda con stato e versione".
SIM: verde "sweep fino a 500 combinazioni", verde "paramEffects e score", verde "pagina del run con heatmap"; arancione "holdout", arancione "motore correlazioni", arancione "baseline casuale", arancione "banda Monte Carlo".
DEMO: verde "stesso broker del live", verde "verify sull'archivio", verde "monitor di parità", verde "record demo"; arancione "confronto con la scheda".
LIVE: verde "promozione giudicata dal server reale", verde "loss limit 3%", verde "stop all"; arancione "gate su net e banda", arancione "ramp 25%", arancione "protezioni per strategia", arancione "avvisi".

In basso una freccia con il numero d'ordine dei cantieri: "1 gate in promote() → 2 scheda → 3 holdout → 4 correlazioni → 5 Monte Carlo → 6 protezioni live".
orientation is landscape
layout is block
