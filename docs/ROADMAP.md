# parity-deriva – Roadmap

| fase | cosa | stato |
|---|---|---|
| 1 | **singola strategia**: da un'idea a un trade live | in corso · [PROCESSO.md](PROCESSO.md), piano in [PIANO-FASE1.md](PIANO-FASE1.md) |
| 2 | **mix**: più strategie sullo stesso strumento | dopo la fase 1 · qui sotto |
| 3 | **più strumenti**: correlazioni tra strumenti diversi | più avanti · qui sotto |

Legenda: ✅ c'è già nel codice · 🔧 da fare. I numeri sono proposte, da tarare.

La fase 2 parte da una strategia che ha già fatto la fase 1: ogni run di un
mix è una versione che ha passato il gate su holdout e ha la sua scheda di
riferimento.

---

## Fase 2 – Il mix: più strategie sullo stesso strumento

Un mix mette insieme più strategie, timeframe o parametri **sullo stesso
strumento** e le giudica come un conto solo.

### Cosa fa oggi ✅

- Mette insieme run di set diversi: strategie, timeframe, parametri.
- Due viste:
  - **summed**: ogni run gira sul suo capitale, i profitti si sommano
  - **together**: un solo conto condiviso, con la leva del mix; un trade
    che il margine non copre viene rifiutato, come farebbe il broker
- Analisi: quanto rende ogni run e che quota del totale fa, il suo DD da
  solo, la correlazione con gli altri, la matrice di correlazione, quanto DD
  si risparmia tenendoli insieme (diversificazione).
- Push dal PC all'archivio (`sync.py push`, `push_mix`) e **verify**
  sull'archivio, dalla pagina del mix.

### Controlli da aggiungere 🔧

| Controllo | Perché | Proposta di soglia |
|---|---|---|
| **long e short aperti insieme** | alcuni broker compensano le due posizioni (netting), altri le tengono separate e paghi lo spread due volte; oggi il codice non lo gestisce | mostrare quanto tempo succede; simulare il netting se il broker lo fa |
| **posizioni che si sommano** | tre strategie long insieme sono una sola scommessa grande | rischio totale aperto sullo strumento ≤ 3% del capitale |
| **strategie doppione** | se A e B entrano negli stessi momenti nella stessa direzione, il mix non diversifica | trade sovrapposti < 50% |
| **senza questo run** | ogni run deve migliorare il mix | togliendolo, rendimento/DD non migliora |
| **pesi** | oggi ogni run pesa uguale | proposte automatiche: "stesso rischio", "miglior rendimento/DD" |
| **banda Monte Carlo del mix** | serve un riferimento per il mix in demo e live | rimescolando giorni interi, così la correlazione resta |
| **holdout del mix** | anche scegliere i run è una selezione | il mix si sceglie sul periodo di sviluppo, holdout guardato una volta |

### Gate "mix pronto" 🔧

Niente doppioni; rischio sommato sotto il tetto; netting gestito se il broker
lo fa; ogni run migliora il mix; holdout del mix superato. Lo decide la
piattaforma.

### Il mix in demo e in live 🔧

Oggi si manda un form alla volta. Con la fase 2:

- **in demo** parte il mix intero: tutti i form insieme, con il capitale
  diviso come nel mix;
- **record e promozione per mix**: il server reale giudica il mix come
  un'unità, con i gate della fase 1 più i controlli del mix superati;
- **in live**, una protezione in più: se il rischio totale aperto sullo
  strumento supera il tetto, il nuovo trade viene rifiutato. Qui il netting
  conta ancora di più: se il broker compensa le posizioni, i trade veri
  escono diversi da quelli simulati.

### Cantieri della fase 2, in ordine

1. **Long/short insieme e doppioni.** Piccolo-medio. Dicono se il mix è vero
   o solo apparente.
2. **"Senza questo run" e pesi.** Piccolo.
3. **Banda Monte Carlo del mix** (riusa quella della fase 1, a giorni
   interi). Medio.
4. **Holdout del mix.** Piccolo, se c'è già l'holdout della fase 1.
5. **Mix intero in demo e live**, con record e promozione per mix e tetto al
   rischio sommato. Medio.

---

## Fase 3 – Più strumenti

Oggi un mix lavora su un solo strumento. Con più strumenti servono:

- **conversione delle valute**: oggi un mix con EURJPY ed EURUSD somma yen e
  dollari così come sono (c'è solo un avviso nella pagina del mix). Il P&L va
  convertito nella valuta del conto al cambio del giorno;
- **esposizione per valuta**: EURUSD long e USDCHF short sono due volte la
  stessa scommessa contro il dollaro; serve un tetto per valuta;
- **correlazioni tra strumenti**, anche nei giorni peggiori: la correlazione
  su tutti i giorni nasconde che nei crolli cade tutto insieme;
- **tetto al rischio del portafoglio** e controllo della correlazione tra le
  strategie live.

Vale anche per l'azionario: un titolo è uno strumento come gli altri.

---

## Grafici per claude.ai

Stesso stile di [PROCESSO.md](PROCESSO.md).

### GRAFICO R1 – Il mix sullo stesso strumento
render_generated_image
prompt is Diagramma "Mix di strategie sullo stesso strumento" per parity-deriva. Stile tecnico pulito, sfondo bianco.

In alto un grafico a candele stilizzato di un solo strumento (es. EURUSD), lungo tutta la larghezza.

Sotto, tre righe orizzontali, una per strategia: "Strategia A (H4)", "Strategia B (M15)", "Strategia C (H1)". In ogni riga delle barre colorate che indicano quando la posizione è aperta: verde = long, rosso = short.

Tre zone evidenziate con un riquadro tratteggiato e un'etichetta:
1. Punto in cui A è long e B è short nello stesso momento: "long e short insieme – netting o doppio spread?"
2. Punto in cui A, B e C sono tutte long: "posizioni che si sommano – una sola scommessa grande, tetto al rischio totale"
3. Punto in cui B e C aprono quasi sempre insieme nella stessa direzione: "doppione – il mix non diversifica"

A destra un pannello "Controlli del mix": "senza questo run", "pesi per run", "banda Monte Carlo del mix", "holdout del mix", tutti con etichetta "da fare". Sopra, con etichetta "c'è già": "summed / together, margine, correlazioni, diversificazione".
orientation is landscape
layout is block

### GRAFICO R2 – Le tre fasi
render_generated_image
prompt is Roadmap orizzontale "parity-deriva – tre fasi" in stile tecnico pulito, sfondo bianco, font sans-serif.

Tre blocchi da sinistra a destra, uniti da una freccia:
1. "Fase 1 · singola strategia" (verde, etichetta "in corso"): "SIM → DEMO → LIVE, gate su holdout, promozione giudicata dal server reale, protezioni per strategia"
2. "Fase 2 · mix sullo stesso strumento" (arancione, etichetta "dopo"): "netting, doppioni, rischio sommato, pesi, banda Monte Carlo del mix, mix intero in demo e live"
3. "Fase 3 · più strumenti" (grigio, etichetta "più avanti"): "conversione valute, esposizione per valuta, correlazioni tra strumenti, tetto al rischio del portafoglio"

Sotto ogni blocco una nota piccola: fase 1 "una strategia alla volta", fase 2 "più strategie, un solo strumento", fase 3 "più strumenti, anche azioni".
orientation is landscape
layout is block
