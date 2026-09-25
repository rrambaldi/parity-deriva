# Strategie di trading M15: regole operative

> Documento didattico. Le regole descritte non garantiscono risultati e devono essere validate con backtest, forward test e conto demo. Il trading comporta il rischio di perdita del capitale.

## Convenzioni comuni

- **Timeframe operativo:** M15; ogni candela rappresenta 15 minuti.
- **Timeframe di contesto:** H1, salvo diversa indicazione.
- **R:** rischio iniziale definito dallo stop loss. Un target a 2R equivale a un profitto potenziale doppio rispetto alla perdita iniziale.
- **Chiusura valida:** usare solo candele completate; non entrare sulla base di una candela ancora in formazione.
- **Spread e slippage:** devono essere inclusi nel backtest e verificati prima dell'operatività reale.
- **News:** evitare nuove posizioni poco prima di eventi macroeconomici ad alto impatto, salvo strategia specificamente progettata per gestirli.

---

## 1. MTP — Moving-average Trend Pullback

### Breve descrizione

Strategia trend-following che usa EMA 20 ed EMA 50 per identificare la direzione su M15 e cerca un ritracciamento verso le medie prima dell'ingresso. Il timeframe H1 viene usato come filtro direzionale.

### Indicatori e impostazioni

- EMA 20 su M15.
- EMA 50 su M15.
- EMA 20 e EMA 50 su H1.
- ATR 14 su M15 per stimare la volatilità e dimensionare lo stop.

### Ingresso long

1. Su H1, EMA 20 deve essere sopra EMA 50 oppure il prezzo deve mostrare massimi e minimi crescenti.
2. Su M15, EMA 20 deve essere sopra EMA 50.
3. Il prezzo deve ritracciare verso EMA 20 o verso la zona compresa tra EMA 20 ed EMA 50.
4. Attendere una candela di conferma rialzista, per esempio una chiusura sopra il massimo della candela precedente o un engulfing rialzista.
5. Entrare all'apertura della candela successiva oppure con ordine stop poco sopra il massimo della candela di conferma.
6. Non entrare se il movimento di conferma è già eccessivamente esteso rispetto all'ATR.

### Ingresso short

Applicare condizioni speculari:

1. Su H1, EMA 20 sotto EMA 50 oppure struttura di massimi e minimi decrescenti.
2. Su M15, EMA 20 sotto EMA 50.
3. Ritracciamento verso EMA 20 o la zona EMA 20–EMA 50.
4. Candela di conferma ribassista.
5. Entrata sotto il minimo della candela di conferma.

### Uscita

- **Stop long:** sotto il minimo del pullback o a una distanza minima di 1,0–1,5 ATR, scegliendo il livello più coerente con la struttura.
- **Stop short:** sopra il massimo del pullback o a 1,0–1,5 ATR.
- **Target principale:** 2R oppure il precedente massimo/minimo significativo.
- **Uscita anticipata:** chiusura M15 dalla parte opposta di EMA 50, rottura strutturale contro la posizione o perdita del contesto H1.
- **Trailing opzionale:** dopo il raggiungimento di 1R, spostare lo stop a pareggio solo se previsto dal test; in alternativa seguire EMA 20 o gli swing successivi.

### Money management

- Rischio suggerito per test iniziale: 0,25–0,5% del capitale per operazione.
- Rischio/rendimento minimo: 1:1,5; preferibile 1:2.
- Massimo due tentativi sullo stesso movimento.
- Stop giornaliero: interrompere dopo una perdita cumulata dell'1–2%.
- Evitare posizioni simultanee fortemente correlate, per esempio più trade long sul dollaro contro valute diverse.

---

## 2. SBR — Session Breakout Range

### Breve descrizione

Strategia che identifica il range iniziale di una sessione e opera la rottura del massimo o del minimo. È adatta a strumenti liquidi durante le fasi di maggiore attività, ma può subire falsi breakout.

### Preparazione

1. Definire una finestra oraria fissa e convertirla correttamente nell'orario del broker.
2. Calcolare il massimo e il minimo del range iniziale, per esempio delle prime 2–4 candele M15 della sessione scelta.
3. Non modificare la finestra dopo aver visto il risultato storico.
4. Verificare che il range non sia troppo ampio rispetto all'ATR M15.

### Ingresso long

1. Attendere una candela M15 che chiuda sopra il massimo del range.
2. La chiusura deve superare il livello di almeno un piccolo buffer, definito in punti o come frazione dell'ATR.
3. Preferire una candela con corpo consistente, non soltanto uno spike.
4. Entrare alla candela successiva oppure attendere un retest del massimo del range.
5. Annullare il segnale se il prezzo rientra nel range prima dell'esecuzione.

### Ingresso short

1. Attendere una chiusura M15 sotto il minimo del range.
2. Applicare il buffer predefinito.
3. Verificare che la candela non sia già troppo estesa.
4. Entrare direttamente o sul retest del minimo.
5. Annullare il segnale se il prezzo rientra nel range.

### Uscita

- **Stop:** appena dentro il range dopo la rottura; per un ingresso su retest, oltre il livello e oltre il massimo/minimo del retest.
- **Target 1:** ampiezza del range proiettata dal punto di rottura.
- **Target 2:** 2R o livello tecnico H1 successivo.
- **Gestione parziale:** possibile chiusura del 50% a 1R e trailing sulla parte restante, ma solo se questa regola è inclusa nel backtest.
- **Time stop:** chiudere la posizione se dopo 4–8 candele M15 non si sviluppa un movimento favorevole.

### Money management

- Rischio per trade: 0,25–0,5%.
- Un solo breakout valido per direzione e sessione.
- Se il range è troppo ampio e produce uno stop sproporzionato, non operare.
- Non aumentare la size per compensare un trade perso.
- Ridurre il rischio durante giornate con spread anomalo o liquidità ridotta.

---

## 3. S/RP — Support and Resistance Price Action

### Breve descrizione

Metodo discrezionale ma codificabile che cerca rimbalzi o false rotture su supporti e resistenze. I livelli principali vengono preferibilmente tracciati su H1/H4 e utilizzati per sincronizzare l'entrata su M15.

### Preparazione dei livelli

1. Tracciare zone, non linee eccessivamente precise.
2. Privilegiare livelli con più reazioni storiche, massimi/minimi evidenti e precedenti breakout.
3. Annotare la distanza dal prossimo livello opposto.
4. Scartare il trade se lo spazio disponibile non consente almeno 1,5R.

### Ingresso long su rimbalzo

1. Il prezzo raggiunge una zona di supporto.
2. Attendere una falsa rottura, una candela di rifiuto o un engulfing rialzista.
3. La candela deve chiudere nuovamente sopra il supporto o dentro la zona.
4. Entrare sopra il massimo della candela di conferma.
5. Se il prezzo chiude nettamente sotto il supporto, annullare il setup long.

### Ingresso short su rimbalzo

1. Il prezzo raggiunge una resistenza.
2. Attendere rifiuto, falsa rottura o engulfing ribassista.
3. La candela deve chiudere sotto la resistenza o rientrare nella zona.
4. Entrare sotto il minimo della candela di conferma.
5. Annullare il setup se il prezzo chiude nettamente sopra la resistenza.

### Ingresso su breakout e retest

- Long: chiusura sopra la resistenza, ritorno sulla zona, tenuta del livello e nuova candela rialzista.
- Short: chiusura sotto il supporto, ritorno sulla zona, rifiuto del livello e nuova candela ribassista.

### Uscita

- **Stop:** oltre il limite della zona e oltre il massimo/minimo della falsa rottura.
- **Target 1:** livello tecnico successivo.
- **Target 2:** 2R, se raggiungibile prima del livello opposto.
- **Uscita anticipata:** chiusura decisa oltre il livello che avrebbe dovuto contenere il prezzo.
- **Nessun trade:** se il rapporto tra distanza dello stop e spazio fino al target è sfavorevole.

### Money management

- Rischio iniziale: 0,25–0,5% per setup.
- Ridurre a 0,25% quando il livello è recente o poco testato.
- Non sommare più posizioni nella stessa zona.
- Definire il livello prima dell'apertura del trade per evitare stop scelti a posteriori.

---

## 4. BMR — Bollinger Mean Reversion

### Breve descrizione

Strategia di ritorno verso la media per mercati laterali. Usa Bollinger Bands 20,2 e RSI 14. Non deve essere applicata automaticamente contro trend H1 forti.

### Filtro di contesto

Operare soltanto quando:

- EMA 50 H1 è relativamente piatta;
- non ci sono massimi e minimi H1 chiaramente direzionali;
- le bande M15 non stanno espandendosi violentemente;
- non sono imminenti notizie ad alto impatto.

### Ingresso long

1. Il prezzo tocca o attraversa la banda inferiore.
2. RSI 14 è sotto 30 oppure mostra una divergenza rialzista, se questa regola è stata testata.
3. Una candela M15 chiude nuovamente dentro le bande.
4. Entrare sopra il massimo della candela di rientro.
5. Non comprare una semplice candela che continua a chiudere fuori dalla banda in un trend forte.

### Ingresso short

1. Il prezzo tocca o attraversa la banda superiore.
2. RSI è sopra 70 oppure mostra divergenza ribassista.
3. Candela M15 di rientro dentro le bande.
4. Entrare sotto il minimo della candela di rientro.

### Uscita

- **Stop long:** sotto il minimo della falsa rottura più un buffer.
- **Stop short:** sopra il massimo della falsa rottura più un buffer.
- **Target primario:** banda centrale, generalmente la media mobile a 20 periodi.
- **Target esteso:** banda opposta solo se il mercato resta laterale e il rapporto rischio/rendimento è adeguato.
- **Uscita immediata:** nuova chiusura fuori dalla banda nella direzione avversa o forte espansione della volatilità.

### Money management

- Rischio ridotto: 0,25% per trade, perché si opera potenzialmente contro movimenti improvvisi.
- Massimo un tentativo per banda e per zona.
- Non usare martingala né aumentare la size dopo una rottura perdente.
- Sospendere il metodo quando l'ATR cresce oltre la soglia stabilita nel test.

---

## 5. BBO — Bollinger Breakout

### Breve descrizione

Strategia di continuazione che cerca una compressione delle Bollinger Bands seguita da una rottura accompagnata da espansione della volatilità.

### Preparazione

- Bollinger Bands: periodo 20, deviazione standard 2.
- ATR 14.
- Facoltativo: volume reale o tick volume, se affidabile per lo strumento.
- Definire una soglia di compressione, per esempio bandwidth sotto il percentile 20 degli ultimi 100 periodi. La soglia deve essere fissata prima del test.

### Ingresso long

1. Le bande restano compresse per almeno 3–5 candele M15.
2. Una candela chiude sopra la banda superiore o sopra il massimo della congestione.
3. Il corpo della candela supera un filtro minimo, per esempio 0,5 ATR.
4. Il volume o il momentum non devono essere nettamente inferiori alla media, se tali dati sono utilizzati come filtro.
5. Entrare sulla candela successiva o sul retest del bordo superiore della congestione.

### Ingresso short

Condizioni speculari:

1. Compressione delle bande.
2. Chiusura sotto la banda inferiore o sotto il minimo della congestione.
3. Corpo minimo definito in ATR.
4. Conferma di momentum.
5. Entrata diretta o sul retest.

### Uscita

- **Stop:** dentro la congestione o oltre il minimo/massimo del retest.
- **Target:** 2R, proiezione dell'ampiezza della congestione oppure livello H1 successivo.
- **Uscita anticipata:** il prezzo rientra rapidamente nella congestione e chiude al suo interno.
- **Trailing:** usare swing M15 o ATR trailing soltanto dopo una progressione di almeno 1R.
- **Time stop:** uscire se il breakout non produce follow-through entro 4–6 candele.

### Money management

- Rischio per trade: 0,25–0,5%.
- Non entrare dopo una candela già estesa oltre 1,5–2 ATR.
- Un solo nuovo ingresso dopo un falso breakout; se fallisce anche il secondo tentativo, attendere una nuova compressione.
- Considerare slippage maggiore durante aperture e pubblicazioni macroeconomiche.

---

## 6. BRT — Breakout Retest

### Breve descrizione

Metodo selettivo che evita l'ingresso immediato sul breakout e aspetta il ritorno del prezzo sul livello rotto. L'obiettivo è ottenere uno stop più corto e una conferma strutturale migliore.

### Ingresso long

1. Identificare una resistenza H1/H4 o un massimo M15 ben definito.
2. Attendere una chiusura M15 sopra la resistenza.
3. Attendere il ritorno del prezzo sulla zona, senza inseguire il movimento.
4. Il retest deve mostrare rifiuto del livello, per esempio una candela con ombra inferiore e chiusura rialzista.
5. Entrare sopra il massimo della candela di conferma.
6. Se il prezzo chiude nuovamente sotto il livello con decisione, annullare il trade.

### Ingresso short

1. Identificare un supporto.
2. Attendere una chiusura M15 sotto il supporto.
3. Attendere il retest dal basso.
4. Cercare rifiuto ribassista e chiusura sotto il livello.
5. Entrare sotto il minimo della candela di conferma.
6. Annullare il trade se il prezzo recupera stabilmente il supporto.

### Uscita

- **Stop long:** sotto il minimo del retest e sotto il livello rotto.
- **Stop short:** sopra il massimo del retest e sopra il livello rotto.
- **Target:** primo livello tecnico successivo oppure 2R.
- **Break-even:** non automatico; spostare lo stop solo secondo una regola testata.
- **Uscita anticipata:** chiusura M15 contro il livello, fallimento del breakout o mancato follow-through entro un numero prefissato di candele.

### Money management

- Rischio tipico: 0,25–0,5%.
- Se il retest è molto profondo o lo stop diventa ampio, ricalcolare la size e non aumentare il rischio.
- Se non avviene il retest, non inseguire il prezzo.
- Evitare di aprire simultaneamente il breakout diretto e il retest dello stesso livello.

---

## Calcolo della posizione

Definire prima di entrare:

- capitale disponibile: `C`;
- rischio percentuale: `r`;
- distanza dello stop in punti/pip: `SL`;
- valore monetario di un punto per una unità dello strumento: `V`.

Formula:

```text
Rischio monetario = C × r
Dimensione posizione = Rischio monetario / (SL × V)
```

Esempio puramente illustrativo:

```text
Capitale: 10.000 €
Rischio: 0,5%
Rischio monetario: 50 €
Stop: 25 pip
Valore per pip per 1 lotto: 10 €
Size: 50 / (25 × 10) = 0,20 lotti
```

La formula deve essere adattata alle specifiche del broker, alla valuta del conto, al valore del contratto e allo strumento negoziato.

## Protocollo di validazione

1. Definire in modo numerico ogni regola, evitando termini vaghi come “trend forte” o “candela significativa”.
2. Usare dati con spread, commissioni e slippage realistici.
3. Separare periodo di sviluppo, periodo di validazione e periodo out-of-sample.
4. Registrare almeno: numero di trade, profit factor, expectancy, drawdown massimo, percentuale di successo, durata media e risultati per sessione.
5. Ripetere il test su strumenti e periodi diversi senza ottimizzare eccessivamente i parametri.
6. Eseguire forward test su conto demo prima di valutare l'uso con capitale reale.
7. Interrompere o rivalutare il metodo quando il drawdown supera quello osservato nel test o cambiano volatilità e costi di esecuzione.

## Checklist prima dell'ordine

- Il metodo utilizzato è identificato con il relativo codice?
- Il contesto H1 è coerente con il setup?
- Il livello di ingresso è definito su una candela chiusa?
- Stop e target sono già determinati?
- Il rapporto rischio/rendimento è sufficiente?
- La size deriva dallo stop e dal rischio monetario?
- Sono presenti news, spread anomalo o bassa liquidità?
- Il trade rispetta il limite giornaliero e il numero massimo di operazioni?

