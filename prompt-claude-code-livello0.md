# Prompt per Claude Code — Livello 0: previsione delle prossime M candele

Copia tutto ciò che sta sotto la riga e incollalo in Claude Code, dalla cartella dove vuoi
che nasca il progetto.

Prima di incollarlo, controlla la tabella "Decisioni": le righe **PROPOSTA** sono scelte
da confermare o cambiare; le righe **APERTO** Claude Code te le chiederà quando ci arriva.

---

## Contesto

Progetto di ricerca a livelli su serie di candele. Questo prompt copre solo il **livello 0**:
dato un contesto di N candele, prevedere le M candele successive (M = 1 o 2).

Un **sistema** è una combinazione (strumento, timeframe, N, M, modello). Ogni sistema viene
addestrato e valutato in modo indipendente dagli altri.

Scopo: misurare se esiste un segnale predittivo rispetto a baseline banali. Non è un sistema
di trading: in questa fase non si simulano ordini, spread o P&L.

Regola di ingaggio per tutto il lavoro: le righe **DEC** della tabella sono decisioni di
Roberto; le righe **L0-P** sono proposte contenute in questo documento, valide finché Roberto
non le cambia; le righe **APERTO** vanno chieste quando ci arrivi. Nel codice e nel README ogni
scelta porta il suo codice. Se ti serve una scelta che non è in nessuna di queste righe,
**fermati e chiedila**: non scegliere per me. Le soglie numeriche le decide Roberto.

## Decisioni

| Codice | Voce | Valore | Stato |
|---|---|---|---|
| DEC-1 | Dati di partenza | M5 dal 2015, due CSV per strumento (BID e ASK) | deciso |
| DEC-2 | Rappresentazione | delta rispetto al close dell'ultima candela della finestra, in unità minime di prezzo; nessun'altra normalizzazione | deciso |
| DEC-3 | Unità minima | il più piccolo incremento di prezzo dello strumento, dichiarato per strumento nel config | deciso |
| DEC-4 | N | 21, 37, 42, 108 | deciso |
| DEC-5 | M | 1, 2 | deciso |
| DEC-6 | Split | 75% training, 25% valutazione, frazioni da config | deciso |
| DEC-7 | Soglia minima di campioni | nessuna per ora; `n` riportato comunque in ogni riga dei risultati | deciso |
| L0-P1 | Divisione del 25% | 12,5% validazione + 12,5% test bloccato | PROPOSTA |
| L0-P2 | Serie di prezzo | mid, calcolato campo per campo da BID e ASK; bid e ask selezionabili | PROPOSTA |
| L0-P3 | Chiusura della candela daily | 17:00 America/New_York, con ora legale | PROPOSTA |
| L0-P4 | High e low del mid | media di `bid_high` e `ask_high` (e dei low): approssimazione | PROPOSTA |
| L0-P5 | Timestamp della candela | inizio dell'intervallo, in UTC | PROPOSTA |
| L0-P6 | Finestre che attraversano un buco nei dati | escluse e contate | PROPOSTA |
| L0-P7 | Baseline | ZERO, REPEAT, MEAN (Parte 4) | PROPOSTA |
| L0-P8 | Iperparametri | punti di partenza della Parte 5, non ottimizzati | PROPOSTA |
| L0-P9 | Early stopping | sull'ultimo 10% del training, non sulla validazione | PROPOSTA |
| APERTO-1 | Colonne e formato dei CSV | — | da chiedere |
| APERTO-2 | Fuso orario dei timestamp nei CSV | — | da chiedere |
| APERTO-3 | Candele incomplete (festività, buchi) | — | da chiedere, con le statistiche alla mano |
| APERTO-4 | Strumenti e timeframe | — | da chiedere; si parte da un solo sistema |
| APERTO-5 | Indicatori | — | fuori dalla prima versione; struttura predisposta (Parte 7) |

Se L0-P1 non viene accettata, il 25% resta un unico segmento e il blocco del test
(Parte 3) si applica a quello.

## Obiettivo

Una pipeline unica, parametrizzata da config, che per ogni sistema produca: campioni,
baseline, modelli addestrati, metriche sulla validazione, un CSV delle previsioni barra per
barra, e una riga in un riepilogo comune.

Priorità: correttezza e tracciabilità, non velocità.

## Vincoli tecnici

- Python ≥ 3.11 (serve `tomllib` della libreria standard per leggere il config).
- Env conda dedicato: se non esiste, proponimi come crearlo prima di installare qualunque
  cosa. Mai il python di sistema.
- Dipendenze ammesse: `pandas`, `numpy`, `pytest`, `lightgbm`, `torch`. Qualunque altra cosa
  chiedimela prima.
- Niente framework di forecasting o di AutoML esterni: i due modelli sono piccoli e voglio
  poterli leggere riga per riga.
- I test girano senza dati reali: usano serie sintetiche costruite ad hoc.

---

# SPECIFICA

## Parte 1 — Dati

**Adapter.** Input: path dei due CSV (BID, ASK) e simbolo. Non assumere il formato delle
colonne né il fuso dei timestamp: chiedimeli (APERTO-1, APERTO-2) quando arrivi a quel punto,
e fatti mandare le prime righe di un file.

Output: un `DataFrame` M5 indicizzato per timestamp UTC, con colonne
`bid_open, bid_high, bid_low, bid_close, ask_open, ask_high, ask_low, ask_close`.

**Validazione**, su ciascun file e sulla coppia:

- indice monotono crescente, nessun duplicato, nessun NaN;
- `high >= max(open, close)` e `low <= min(open, close)`;
- BID e ASK con gli stessi timestamp;
- `ask >= bid` campo per campo;
- numero di decimali osservato coerente con l'unità minima dichiarata (DEC-3).

Nessuna correzione silenziosa. Le violazioni finiscono in un report (quante, dove, primi
esempi) e il caricamento si ferma: la politica sui dati sporchi la decide Roberto guardando
il report.

**Buchi.** Le barre M5 mancanti non si riempiono. Il report elenca i buchi più lunghi del
normale weekend, con durata e posizione.

## Parte 2 — Serie di prezzo e ricampionamento

**Serie di prezzo** (L0-P2). Parametro `PRICE_SERIES ∈ {mid, bid, ask}`, default `mid`.
Il mid si calcola campo per campo: `mid_open = (bid_open + ask_open) / 2`, e così via.
Per `high` e `low` è un'approssimazione, perché dentro la stessa M5 i massimi di bid e ask
possono cadere in istanti diversi (L0-P4).

Motivo della proposta: al rollover lo spread si allarga, l'ask sale e il bid scende. Nel mid
i due effetti si compensano; nella sola serie bid restano minimi finti.

**Ricampionamento da M5.** `open` = primo open, `high` = massimo, `low` = minimo,
`close` = ultimo close.

- Ancoraggio (L0-P3): il giorno di trading va dalle 17:00 alle 17:00 America/New_York.
  Il cambio dell'ora legale si gestisce con il fuso, non con un offset fisso. Così la
  settimana ha 5 daily e non compare la candela della domenica.
- I timeframe intraday sono allineati alla stessa ancora: H4 alle 17, 21, 01, 05, 09, 13
  ora di New York; H1 ogni ora.
- Weekly: da domenica 17:00 a venerdì 17:00 New York.
- Il timestamp di ogni candela è l'**inizio** del suo intervallo, in UTC (L0-P5).
- Ogni candela porta il numero di barre M5 da cui è costruita e quello atteso. Le candele
  incomplete (festività, buchi) si contano e si segnalano. La politica — tenerle, scartarle,
  o scartare le finestre che le contengono — è APERTO-3: presentami le statistiche e aspetta
  la mia decisione.

## Parte 3 — Campioni e split

**Campione** con riferimento alla candela `t`, per un sistema con parametri N e M:

- `ref = close[t]`
- input: candele `t−N+1 … t`, ciascuna come `(open, high, low, close) − ref`, diviso l'unità
  minima dello strumento → matrice `N × 4`; l'ultimo close vale 0;
- target: candele `t+1 … t+M`, ciascuna come `(high, low, close) − ref`, diviso l'unità
  minima → matrice `M × 3`.

La sequenza è per indice di candela: il weekend non è un buco. Le finestre che attraversano
un buco segnalato nella Parte 1 vengono escluse e contate (L0-P6).

**Split** cronologico, frazioni da config (DEC-6, L0-P1). Un campione appartiene a un
segmento solo se **tutte** le sue N+M candele stanno dentro quel segmento. Così nessuna
candela compare in due segmenti e non serve altro distacco.

**Test bloccato.** Le metriche sul segmento di test non vengono calcolate né stampate, salvo
flag esplicito `--open-test`, che viene loggato con data e ora. Di default si lavora solo su
training e validazione.

## Parte 4 — Baseline

Tre previsioni banali, calcolate per ogni sistema sugli stessi campioni del modello (L0-P7):

- `ZERO`: close = 0 su ogni orizzonte; high e low = media dei rispettivi target nel training.
- `REPEAT`: la prossima candela ripete la forma dell'ultima (high, low e close misurati dal
  suo open), con open posto a `ref`. Con M = 2 la stessa forma si applica di nuovo a partire
  dal close previsto.
- `MEAN`: media di ciascun valore di target nel training.

## Parte 5 — Modelli

Iperparametri di partenza, tutti nel config con etichetta L0-P8. Nessuna ottimizzazione
finché Roberto non la chiede.

**LightGBM.** Input: la matrice `N × 4` appiattita. Un regressore per ogni valore di target
(3 con M = 1, 6 con M = 2). Punto di partenza: `n_estimators` 1000 con early stopping,
`learning_rate` 0.05, `num_leaves` 31, seed fisso.

**GRU.** 1 strato da 32 unità, testa lineare su `3 × M` uscite, loss MSE, Adam con `lr`
1e-3, batch 256, early stopping. Input e target divisi per una costante per colonna,
calcolata sul training (deviazione standard); le previsioni si riportano in unità minime
prima di valutarle. È un'esigenza tecnica della rete e non cambia la rappresentazione di
DEC-2. Tre seed per sistema: si riportano media e intervallo, perché la variabilità fra seed
di una rete piccola può superare il segnale.

**Early stopping** (L0-P9) sull'ultimo 10% del segmento di training, con la stessa regola
dei segmenti (finestre interamente dentro). La validazione resta pulita per il confronto.

## Parte 6 — Metriche e output

Per ogni sistema, sulla validazione, per ogni orizzonte `1 … M`:

- percentuale di direzione indovinata sul close; i campioni con close vero = 0 o previsto = 0
  si escludono dal calcolo e si contano a parte;
- errore standard della percentuale, `0.5 / sqrt(n)`;
- errore medio assoluto su high, low, close, in unità minime;
- le stesse metriche per le tre baseline, e la differenza modello − baseline;
- `n` di training e di validazione.

**CSV per sistema**, una riga per campione: timestamp della candela `t`, target veri,
previsione del modello, previsioni delle baseline. Serve a controllare i casi sul grafico:
è un deliverable, non debug.

**Riepilogo** `results/summary.csv`: una riga per sistema con strumento, timeframe, N, M,
modello, serie di prezzo, `n_train`, `n_val`, metriche e differenze dalle baseline.

## Parte 7 — Indicatori (predisposizione)

Il livello 0 prevede "un paio di indicatori", ma la prima versione gira sulle sole candele
(APERTO-5). Predisponi la costruzione dei campioni perché accetti canali aggiuntivi, ciascuno
espresso come le candele: delta da `ref` in unità minime. Quali indicatori, e con quali
parametri, lo decide Roberto dopo che la pipeline sulle candele funziona.

---

## Cosa consegnare

```
candle_forecast/
  config/systems.toml     # strumenti, timeframe, unità minima, N, M, modelli, frazioni di
                          # split, serie di prezzo, iperparametri — ognuno con il suo codice
  candle_forecast/
    config.py             # caricamento e validazione del config
    data.py               # adapter BID/ASK, validazione, report
    resample.py           # M5 → timeframe, ancora 17:00 New York, mid
    samples.py            # finestre N+M, delta in unità minime
    split.py              # segmenti cronologici, blocco del test
    baselines.py
    models/lgbm.py
    models/gru.py
    evaluate.py           # metriche, CSV per sistema, riepilogo
    cli.py
  tests/
  results/
  README.md               # come si lancia; tabella di tutte le etichette DEC / L0-P / APERTO
```

Requisiti sul codice: type hints, funzioni pure dove possibile, nessuna variabile globale
mutabile, seed registrati nei risultati.

## Test obbligatori

Scrivili prima o insieme al codice, non dopo.

1. **Validazione dati**: file sintetici con ciascuna violazione della Parte 1 → report e
   arresto.
2. **Ricampionamento**: M5 sintetico → D1 con chiusura alle 17:00 New York, verificato a
   cavallo dei cambi d'ora legale di marzo e novembre (il confine in UTC deve spostarsi di
   un'ora); H4 allineate all'ancora; weekly senza candela della domenica.
3. **Mid**: calcolo campo per campo su valori noti.
4. **Campioni**: esempio costruito a mano; ultimo close dell'input = 0; target corretti in
   unità minime.
5. **Anti-look-ahead**: i campioni costruiti sulla serie intera e su quella troncata sono
   identici nella parte comune; modificare le candele dopo `t` non cambia l'input del
   campione `t`; modificare quelle dopo `t+M` non cambia né input né target.
6. **Split**: nessuna candela in due segmenti; senza `--open-test` nessuna metrica di test
   viene calcolata.
7. **Baseline**: valori attesi su una serie sintetica.
8. **Il modello impara quando c'è qualcosa da imparare**: su una serie sintetica con
   rendimenti AR(1), coefficiente 0.3, LightGBM e GRU battono le baseline.
9. **Il modello non impara quando non c'è niente**: su un random walk sintetico di almeno
   50.000 candele, la percentuale di direzione di ogni modello resta entro tre errori standard
   dal 50%. Se esce da quella banda c'è leakage: è il test più importante di tutti.

## Ordine di lavoro

1. Scaffolding, config, e i test 5 e 9 in forma di scheletro su dati sintetici.
2. Adapter e validazione. Chiedimi formato e fuso dei CSV (APERTO-1, APERTO-2), poi fai
   girare il report su dati veri e mostramelo.
3. Ricampionamento e mid. Mostrami le statistiche delle candele incomplete e aspetta la
   decisione (APERTO-3).
4. Campioni e split.
5. Baseline e metriche. Chiedimi il primo sistema (APERTO-4) e fai girare le sole baseline:
   il report delle baseline da solo è già un risultato.
6. LightGBM.
7. GRU.
8. Griglia su tutti i sistemi del config e riepilogo.

Fermati alla fine di ogni punto e dimmi cosa hai fatto in due righe. Non passare al punto
successivo se i test del punto corrente non passano.

## Cosa non fare

- Nessuna normalizzazione oltre al delta in unità minime (DEC-2), a parte la scalatura
  tecnica della GRU descritta nella Parte 5.
- Nessun indicatore, filtro o feature che qui non c'è.
- Nessuna ottimizzazione degli iperparametri finché i test 5 e 9 non passano, e comunque non
  senza che Roberto la chieda.
- Non aprire il test.
- Non sostituire una proposta L0-P con un'altra perché ti sembra migliore: dimmela e aspetta.
