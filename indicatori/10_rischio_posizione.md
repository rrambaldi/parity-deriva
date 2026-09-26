# Rischio e gestione della posizione

| Metodo | Calcolo sintetico | Parametri principali |
|---|---|---|
| ATR stop | Entrata ± k·ATR | periodo ATR, k |
| Trailing stop | Stop che segue massimo/minimo favorevole | distanza, attivazione |
| Chandelier Exit | Estremo lookback ∓ k·ATR | lookback, ATR, k |
| Risk/reward | Profit target / distanza stop | target, stop |
| Fixed fractional | Posizione=rischio monetario/(distanza stop·valore punto) | capitale, rischio %, stop |
| Volatility targeting | Peso obiettivo/volatilità stimata | target vol, finestra |
| Kelly | f*= (bp-q)/b | probabilità, payoff, cap |
| MAE/MFE | Escursione avversa/favorevole per trade | finestra trade |
| Drawdown | Picco cumulato meno equity corrente | nessuno |
| VaR | Quantile negativo della perdita | confidenza, orizzonte, metodo |
| CVaR/Expected Shortfall | Media delle perdite oltre VaR | confidenza, orizzonte |
| Portfolio volatility | √(w'Σw) | matrice covarianza, pesi |
| Exposure | Somma valori assoluti delle posizioni/capitale | capitale, posizioni |
| Circuit breaker | Blocca il sistema oltre perdita/errore | soglie, durata |
