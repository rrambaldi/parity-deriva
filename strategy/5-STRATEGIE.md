# Istruzioni per codificare strategie di trading 4H in Python

Queste indicazioni descrivono, a livello di logica e flusso, come implementare strategie di trading sul timeframe 4H in un sistema automatizzato. Non includono codice, librerie o dettagli su come recuperare i dati.

## Concetti comuni a tutte le strategie

- **Timeframe:** tutte le logiche si basano su candele da 4 ore (4H).
- **Frequenza di valutazione:** la logica di segnale viene valutata una volta per ogni nuova candela 4H chiusa.
- **Dati necessari per ogni candela:** apertura, massimo, minimo, chiusura, volume (opzionale).
- **Stato del sistema:** devi mantenere in memoria:
  - Se sei già in posizione (long, short o flat).
  - Prezzo di ingresso della posizione aperta.
  - Livelli di stop loss e target associati alla posizione.
- **Regola di esecuzione:** agisci solo alla chiusura della candela 4H, non durante la sua formazione.

---

## 1. Strategia: Trend Following con pullback su EMA

### Indicatori da calcolare

- EMA veloce (es. 50 periodi) sul prezzo di chiusura 4H.
- EMA lenta (es. 200 periodi) sul prezzo di chiusura 4H.
- ATR (es. 14 periodi) per dimensionare stop loss.

### Logica di trend

- **Trend rialzista:** chiusura corrente > EMA lenta.
- **Trend ribassista:** chiusura corrente < EMA lenta.

### Condizione di ingresso long

Alla chiusura di ogni candela 4H:

1. Verifica che il trend sia rialzista (chiusura > EMA lenta).
2. Controlla che, durante la candela appena chiusa, il minimo abbia toccato o superato verso il basso l'EMA veloce (pullback).
3. Verifica che la candela sia bullish (chiusura > apertura).
4. Se non sei già in posizione long:
   - Calcola lo stop loss come: `prezzo di chiusura - (ATR × fattore)` (es. fattore 1.5–2).
   - Calcola il target come: `prezzo di chiusura + (distanza stop loss × rapporto R:R)` (es. 2–3).
   - Apri una posizione long al prezzo di chiusura.
   - Registra stop loss e target associati.

### Condizione di ingresso short

Speculare:

1. Trend ribassista (chiusura < EMA lenta).
2. Massimo della candela tocca o supera verso l'alto l'EMA veloce.
3. Candela bearish (chiusura < apertura).
4. Se non sei già in posizione short:
   - Stop loss: `chiusura + (ATR × fattore)`.
   - Target: `chiusura - (distanza stop loss × rapporto R:R)`.
   - Apri short alla chiusura.

### Gestione uscita

Per ogni candela 4H successiva:

- Se sei long:
  - Se il massimo della candela ≥ target long → chiudi la posizione in profitto.
  - Se il minimo della candela ≤ stop loss long → chiudi in perdita.
- Se sei short:
  - Se il minimo della candela ≤ target short → chiudi in profitto.
  - Se il massimo della candela ≥ stop loss short → chiudi in perdita.

---

## 2. Strategia: Breakout di consolidamento con conferma

### Rilevamento del pattern

Su un window di N candele 4H (es. 10–30):

1. Identifica massimi e minimi locali.
2. Rileva una fase di consolidamento quando:
   - I massimi sono approssimativamente allineati (resistenza).
   - I minimi sono approssimativamente allineati (supporto).
   - La differenza tra resistenza e supporto è “stretta” rispetto alla volatilità recente (puoi usare ATR come riferimento).

Puoi definire il pattern come:

- **Rettangolo:** massimo del window ≈ resistenza, minimo del window ≈ supporto.
- **Triangolo:** massimi decrescenti e minimi crescenti (o viceversa).

### Livelli di breakout

- **Breakout long:** prezzo di chiusura > resistenza del pattern.
- **Breakout short:** prezzo di chiusura < supporto del pattern.

### Conferme opzionali

- RSI(14) > 60 per breakout long, < 40 per breakout short.
- Volume della candela di breakout superiore alla media degli ultimi N periodi.

### Condizione di ingresso long

Alla chiusura di ogni candela 4H:

1. Se è presente un pattern di consolidamento valido.
2. Se la chiusura supera la resistenza del pattern.
3. Se le conferme (RSI, volume) sono soddisfatte (se le usi).
4. Se non sei già in posizione:
   - Stop loss: sotto il supporto del pattern o `chiusura - (ATR × fattore)`.
   - Target: misura del pattern (resistenza − supporto) proiettata dal punto di breakout, o multiplo fisso del rischio (2–3R).
   - Apri long alla chiusura.

### Condizione di ingresso short

Speculare:

1. Pattern di consolidamento presente.
2. Chiusura sotto il supporto.
3. Conferme RSI/volume (se usate).
4. Stop loss sopra la resistenza o `chiusura + (ATR × fattore)`.
5. Target calcolato come per il long, ma verso il basso.

### Gestione uscita

Stessa logica della strategia 1, confrontando massimi/minimi delle candele successive con stop loss e target.

---

## 3. Strategia: Mean Reversion con Bande di Bollinger

### Indicatori

- SMA(20) sul prezzo di chiusura 4H.
- Deviazione standard su 20 periodi.
- Bande di Bollinger:
  - Superiore = SMA + (k × deviazione standard), k = 2.
  - Inferiore = SMA − (k × deviazione standard).
- RSI(14) opzionale per filtrare ipercomprato/ipervenduto.

### Condizione di ingresso long

Alla chiusura di ogni candela 4H:

1. Verifica che il minimo della candela abbia toccato o superato la banda inferiore.
2. Opzionale: RSI(14) < 30.
3. Verifica che la chiusura sia tornata all’interno delle bande (chiusura > banda inferiore).
4. Se non sei già in posizione:
   - Stop loss: sotto il minimo recente o sotto la banda inferiore di una quantità fissa (es. 0.5–1× ATR).
   - Target: SMA centrale o banda superiore.
   - Apri long alla chiusura.

### Condizione di ingresso short

Speculare:

1. Massimo della candela tocca o supera la banda superiore.
2. Opzionale: RSI(14) > 70.
3. Chiusura rientra dentro le bande (chiusura < banda superiore).
4. Stop loss sopra la banda superiore.
5. Target: SMA o banda inferiore.
6. Apri short alla chiusura.

### Gestione uscita

- Long: chiudi quando il prezzo tocca il target (SMA o banda superiore) o lo stop loss.
- Short: chiudi quando il prezzo tocca il target (SMA o banda inferiore) o lo stop loss.

---

## 4. Strategia: Swing su livelli chiave Daily + conferma 4H

### Livelli daily (pre-calcolati)

Prima di eseguire la logica 4H, devi avere una lista di livelli daily rilevanti:

- Supporti e resistenze daily (massimi/minimi recenti, zone di congestione, ecc.).
- Trend daily (es. prezzo sopra/sotto una media mobile daily).

Questi livelli sono fissi per la sessione e non cambiano durante le candele 4H.

### Logica di bias daily

- **Bias long daily:** trend daily rialzista e prezzo vicino a un supporto daily.
- **Bias short daily:** trend daily ribassista e prezzo vicino a una resistenza daily.

### Condizione di ingresso long (4H)

Alla chiusura di ogni candela 4H:

1. Verifica che il prezzo 4H sia vicino a un livello di supporto daily (entro una tolleranza, es. 0.5–1× ATR 4H).
2. Cerca un pattern di inversione 4H sul livello:
   - Pin bar bullish, engulfing bullish, inside bar con rottura al rialzo, ecc.
3. Se il bias daily è long e il pattern è valido:
   - Stop loss: sotto il minimo del pattern o sotto il livello daily.
   - Target: prossimo livello daily di resistenza o multiplo fisso del rischio (2–3R).
   - Apri long alla chiusura della candela 4H di conferma.

### Condizione di ingresso short

Speculare:

1. Prezzo vicino a resistenza daily.
2. Pattern di inversione bearish 4H.
3. Bias daily ribassista.
4. Stop loss sopra il massimo del pattern o sopra la resistenza daily.
5. Target: prossimo supporto daily o 2–3R.
6. Apri short.

### Gestione uscita

Come nelle strategie precedenti: confronto tra massimi/minimi delle candele successive e livelli di stop/target.

---

## 5. Strategia: Momentum con RSI + rottura di swing recenti

### Indicatori

- RSI(14) sul prezzo di chiusura 4H.
- SMA(50) opzionale come filtro di trend.
- Swing high/low recenti (es. ultimi 20–50 periodi).

### Definizione di swing

- **Swing high:** una candela il cui massimo è maggiore dei massimi delle N candele precedenti e successive (es. N = 5).
- **Swing low:** analogo per i minimi.

Mantieni una lista degli ultimi swing high e swing low significativi.

### Condizione di ingresso long

Alla chiusura di ogni candela 4H:

1. Verifica che RSI(14) > 60 (momentum positivo).
2. Opzionale: chiusura > SMA(50).
3. Identifica l’ultimo swing high significativo sopra la SMA (se usi il filtro).
4. Se la chiusura supera l’ultimo swing high:
   - Stop loss: sotto la SMA o sotto l’ultimo swing low recente.
   - Target: distanza tra SMA e swing high moltiplicata per 2, oppure 2–3R.
   - Apri long alla chiusura.

### Condizione di ingresso short

Speculare:

1. RSI(14) < 40.
2. Opzionale: chiusura < SMA(50).
3. Ultimo swing low significativo sotto la SMA.
4. Rottura al ribasso dello swing low.
5. Stop loss sopra la SMA o sopra l’ultimo swing high.
6. Target calcolato come per il long, ma verso il basso.
7. Apri short.

### Gestione uscita

Stessa logica: chiudi quando il prezzo tocca target o stop loss.

---

## Gestione del rischio e regole generali

- **Rischio per trade:** definisci una percentuale fissa del capitale da rischiare per ogni operazione (es. 0.5–1%).
- **Dimensionamento posizione:** calcola la quantità da negoziare in modo che, se lo stop loss viene colpito, la perdita corrisponda alla percentuale di rischio definita.
- **Stop loss obbligatorio:** ogni posizione deve avere uno stop loss definito al momento dell’ingresso.
- **Target minimo:** preferisci target ≥ 2× la distanza dello stop loss (2R).
- **Una posizione per strategia:** per semplicità, puoi permettere al massimo una posizione aperta per strategia per strumento.
- **Filtro di volatilità:** evita di entrare se l’ATR è troppo basso (mercato piatto) o troppo alto (eccessiva volatilità), secondo soglie definite.

---

## Flusso generale dell'algoritmo

Per ogni nuovo tick o nuova candela (a seconda di come ricevi i dati):

1. Aggiorna la serie storica con l’ultima candela 4H chiusa.
2. Ricalcola indicatori (EMA, ATR, RSI, Bande di Bollinger, swing, ecc.).
3. Per ogni strategia:
   - Se non ci sono posizioni aperte:
     - Valuta le condizioni di ingresso long/short.
     - Se soddisfatte, apri posizione e registra stop loss e target.
   - Se c’è una posizione aperta:
     - Controlla se massimi/minimi della candela corrente hanno toccato stop loss o target.
     - In caso affermativo, chiudi la posizione.
4. Aggiorna lo stato (posizione aperta, prezzo di ingresso, stop, target).
5. Ripeti alla prossima candela 4H.

Queste istruzioni sono sufficienti per tradurre ciascuna strategia in codice Python (o in qualsiasi altro linguaggio) una volta deciso come rappresentare dati, indicatori e stato del sistema.
