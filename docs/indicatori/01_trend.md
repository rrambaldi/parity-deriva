# Indicatori di trend

| Indicatore | Calcolo sintetico | Parametri principali |
|---|---|---|
| SMA | Media aritmetica degli ultimi n prezzi | periodo n, serie prezzo |
| EMA | EMA_t = αP_t + (1-α)EMA_{t-1}, α=2/(n+1) | periodo n, serie prezzo |
| WMA | Media con pesi lineari crescenti | periodo n |
| Wilder MA | Media smussata con α=1/n | periodo n |
| DEMA | 2·EMA(n) - EMA(EMA(n)) | periodo n |
| TEMA | 3·EMA - 3·EMA(EMA) + EMA(EMA(EMA)) | periodo n |
| HMA | WMA(2·WMA(n/2)-WMA(n), √n) | periodo n |
| KAMA | Media adattiva basata sull'efficiency ratio | periodo ER, fast, slow |
| ZLEMA | EMA applicata al prezzo compensato del lag | periodo n, lag |
| VWMA | Σ(prezzo·volume)/Σ(volume) | periodo n, volume |
| MACD | EMA veloce - EMA lenta; signal=EMA(MACD) | veloce, lenta, signal |
| ADX | Media dell'indice DX derivato da +DI/-DI | periodo n |
| +DI/-DI | DM direzionale normalizzato per ATR | periodo n |
| Aroon | 100·(n-periodi dall'ultimo massimo/minimo)/n | periodo n |
| Parabolic SAR | SAR aggiornato verso il punto estremo | step AF, max AF |
| Supertrend | Banda basata su prezzo medio ± moltiplicatore·ATR | ATR period, moltiplicatore |
| Ichimoku | Tenkan, Kijun, Senkou A/B, Chikou | 9, 26, 52, spostamento |
| Linear Regression Line | Retta OLS su una finestra mobile | periodo n |
| Regression Channel | Retta OLS ± k deviazioni standard | n, moltiplicatore k |
| Moving Average Ribbon | Insieme di medie con periodi diversi | periodi, tipo media |
| Vortex | Rapporti tra movimento VM e true range | periodo n |
| KST | Somma pesata di ROC smussati | periodi ROC, periodi SMA, pesi |
| Mass Index | Somma del rapporto EMA range/EMA del rapporto | periodo EMA, finestra |
| Qstick | Media delle differenze close-open | periodo n |
| TRIX | ROC della tripla EMA | periodo n |
