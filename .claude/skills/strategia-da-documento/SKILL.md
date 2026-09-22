---
name: strategia-da-documento
description: Estrae le regole operative di un trading system da un documento (Markdown, PDF, testo - appunti, capitolo di un libro, paper, manuale) e le implementa come strategia parity-deriva, con criterio di entry, stop loss e take profit espliciti, registrazione nei tre registri, test unitari e un backtest sui dati locali. Usala quando l'utente indica un documento e chiede di ricavarne una strategia, un algoritmo o un segnale operativo, o di codificare regole di trading lette da un testo.
---

# Da documento a strategia parity-deriva

Trasforma le regole descritte a parole in un documento in una strategia che gira
sullo stack di questo repo. L'input e' il percorso di un file — di norma un `.md`,
ma vale lo stesso per un PDF o un testo semplice; l'output e' una classe in
`strategy/`, registrata, testata e passata da un backtest.

Se l'utente non l'ha detto, chiedi **una sola volta** e in un colpo solo: sigla
della strategia (es. `MR01`), strumento e granularita' di riferimento. Tutto il
resto si ricava dal documento o si dichiara come assunzione.

## Fase 1 — leggere il documento, non interpretarlo

Leggi il file per intero: un `.md` con Read (o `cat -n`, che da' i numeri di
riga da citare dopo), un PDF con Read usando `pages` e procedendo a blocchi fino
alla fine. Leggilo tutto: le regole di uscita stanno quasi sempre dopo quelle di
ingresso, e fermarsi a meta' produce una strategia che entra e non esce.

Produci una tabella delle regole operative, una riga per regola, con il
riferimento da cui viene (`documento.md:riga`, o la sezione, o la pagina per un
PDF), che copra almeno:

| voce | cosa deve risultare |
| --- | --- |
| entry | condizione esatta (barre, indicatori con i loro periodi, soglie), direzione, tipo di ordine (MARKET/STOP/LIMIT), su quale prezzo cade il livello |
| stop loss | come si calcola: estremo di N barre, ATR x k, distanza fissa, livello tecnico |
| take profit | come si calcola: multiplo di R, livello tecnico, target fisso; piu' eventuale trailing o uscita a tempo |
| filtri | sessione/orario, giorni, volatilita' minima, spread massimo, posizioni contemporanee, scadenza dei pendenti |

Chiudi la fase con due elenchi **separati e dichiarati come tali**:

1. cio' che il documento definisce senza ambiguita';
2. cio' che il documento non dice e che verra' assunto, con il valore proposto e
   il perche'.

Regola ferma: non inventare regole assenti dal testo. Un parametro mancante va
nell'elenco 2 come default configurabile, mai spacciato per regola del
documento. Se manca un pezzo essenziale — tipicamente il criterio di uscita —
chiedi invece di indovinare.

Mostra l'esito della fase 1 all'utente **prima** di scrivere codice.

## Fase 2 — implementare

Leggi prima `strategy/AG01.py`, `strategy/AG02.py`, `portfolio/moneymanager.py`,
`event/event.py` e `tests/strategy_ag_test.py`: il nuovo file deve somigliare a
quelli, non a codice generico. Indentazione con TAB, come tutto `strategy/`.

### Il contratto della classe

- File `strategy/<SIGLA>.py`, classe che estende
  `parity_deriva.trading.handler.ExecutionHandler` e implementa
  `execute_event(event)`.
- `__init__(self, **args)` legge i parametri con `self._set(args, 'nome',
  default)`; logger `logging.getLogger('parity_deriva.trading.trading')`. Ogni
  numero del documento (periodi, moltiplicatori, soglie, orari) e' un parametro
  con default, non una costante sepolta nel codice.
- Prima riga utile di `execute_event`: `if str(event) != 'CANDLE': return`, poi
  il filtro sullo strumento. Lo stato che serve (buffer delle barre precedenti,
  indicatori incrementali) sta in un dizionario per strumento, come `self.prev`
  in AG01.
- La strategia **non piazza ordini**: pubblica `SignalEvent` con
  `self.queue_event(...)`. E' `MoneyManager` che li trasforma in ordini, tiene
  una sola operazione per volta e cancella la gamba perdente quando l'altra
  riempie.

### Il contratto del segnale

Campi del `SignalEvent` (vedi `event/event.py` e `AG01.execute_event`):

- `signalNumber` da `signalNumber(self.__class__.__name__, instrument,
  self.granularity, event.time)` di `lib/utils.py`. Deve dipendere **solo dai
  dati**: con `datetime.today()` il replay non si aggancia mai al live e le
  chiavi collidono.
- `clientExtension` = `{'id': signalNumber, 'tag': nome della classe, 'comment': granularita'}`.
- `signalType` = `'EXCLUSIVE'` quando emetti due gambe opposte da tenere in OCO.
- `orderType` e `type` (STOP / LIMIT / MARKET), `instrument`, `time`,
  `price` (l'entry), `stopLoss`, `takeProfit`, `units` (il segno e' la
  direzione), `gtdTime` per i pendenti.
- Ogni prezzo derivato passa da `roundPrice(instrument, value)`: arrotondare a
  mano rompe gli strumenti con precisione diversa dal DAX (un TP di 1.22380
  collassato a 1.2 fa scattare sempre lo stop).

### Il lato del prezzo

La regola di riempimento legge **ask high** e **bid low**. Un long entra
sull'ask e viene colpito su stop e target sul bid; per uno short vale il
contrario. Usa `event.ask['h']`, `event.bid['l']` di conseguenza e tieni conto
dello spread (`event.ask['c'] - event.bid['c']`) dove il documento parla di
distanze nette. Se la strategia legge solo il prezzo medio (`event.mid`),
dichiaralo: la capability `bid_ask_candles` in `scripts/live.py` va richiesta
solo se bid e ask servono davvero.

### Registrazione — tutti e tre i posti

Saltarne uno da' una strategia che esiste e non gira:

- `backtest/ledger.py`, dizionario `STRATEGIES`: serve al backtest e al viewer;
- `scripts/live.py`, `STRATEGIES`: tupla `(modulo, classe, capability, chiave)`
  dove la chiave e' `'pairs'` se la classe riceve una lista, `'pair'` se riceve
  un solo strumento. Sbagliarla non produce errori: `_set()` non trova la chiave
  e la strategia guarda in silenzio il suo EUR_USD di default;
- `scripts/divergence_band.py` se ha senso misurare su questa strategia le barre
  che toccano entrambi i livelli.

### Test

Nuovo `tests/<sigla>_test.py` sullo stampo di `tests/strategy_ag_test.py`:
`unittest` puro, nessuna rete, helper `T0`, `bull_candle`, `bear_candle`,
`candle_series`, `Recorder`, `candle_dict` da `tests/helpers.py`. Copertura
minima: la prima barra riempie solo il buffer e non segnala; la condizione di
entry che scatta, verificando i valori esatti di `price`, `stopLoss`,
`takeProfit` e `units`; il caso simmetrico che non deve scattare; i filtri di
orario o sessione; nessun segnale per gli strumenti non seguiti.

## Fase 3 — verificare

- Suite completa, dalla directory che contiene il package:
  `python -m unittest discover -s parity_deriva -p '*_test.py'`.
  Riporta l'output reale, anche quando fallisce.
- Backtest offline con `backtest/ledger.py` (`run(strumento, granularita',
  '<SIGLA>', dtfrom, dtto)`) su un intervallo davvero presente nello store HDF5;
  `scripts/check.py` dice cosa contiene. Riporta: operazioni aperte, chiuse a
  target, chiuse a stop, rimaste `STILL_OPEN`.
- Se non apre nessuna operazione, dillo e spiega perche'. **Non ritoccare le
  regole per far uscire qualche trade**: una strategia che sul documento non
  scatta mai e' un risultato, non un bug da aggirare.
- Come si leggono quei numeri: il P&L e' `(uscita - entrata) x units`, prezzo
  per unita' e non valuta; una barra che tocca sia stop sia target non e'
  risolvibile (`backtest/resolution.py`) e non va contata come vincita; un trade
  ancora aperto a fine dati e' `STILL_OPEN`, fuori da ogni rapporto.

## Consegna

1. tabella regola del documento (con il riferimento: riga, sezione o pagina) ->
   punto del codice che la implementa (`file:riga`);
2. elenco delle assunzioni della fase 1 con il valore effettivamente usato;
3. esito di test e backtest, in chiaro.

Tutto il lavoro gira offline sul warehouse locale: nessuna chiamata di rete,
nessun contatto con i broker, nessun ordine inviato.
