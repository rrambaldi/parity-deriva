# Indicatori di volatilità

| Indicatore | Calcolo sintetico | Parametri principali |
|---|---|---|
| ATR | Media smussata del True Range | periodo n |
| Bande di Bollinger | SMA ± k·deviazione standard | periodo n, k |
| Bollinger Band Width | (banda alta-banda bassa)/banda centrale | n, k |
| Keltner Channels | EMA ± k·ATR | EMA n, ATR n, k |
| Donchian Channels | Massimo/minimo su n barre | periodo n |
| Deviazione standard | Radice della varianza mobile | periodo n |
| Historical Volatility | Deviazione standard dei log-return annualizzata | finestra, giorni annui |
| Chaikin Volatility | ROC della differenza EMA(high-low) | EMA n, ROC n |
| ADR | Media dei range giornalieri | periodo n |
| True Range | max(high-low, |high-prev close|, |low-prev close|) | nessuno |
| Normalized ATR | ATR/prezzo | periodo ATR |
| Ulcer Index | Radice della media dei drawdown percentuali quadrati | periodo n |
| Chandelier Exit | Estremo -/+ k·ATR | ATR n, k, lookback |
| Volatility Stop | Stop dinamico basato su ATR dal prezzo | ATR n, k |
| Percentile volatilità | Posizione della volatilità nella sua distribuzione storica | finestra, lookback |
| GARCH | Volatilità condizionata stimata da rendimenti e varianza passata | p, q, distribuzione |
