# Indicatori di momentum

| Indicatore | Calcolo sintetico | Parametri principali |
|---|---|---|
| RSI | 100 - 100/(1+media guadagni/media perdite) | periodo n |
| Stocastico | 100·(Close-Low_n)/(High_n-Low_n) | %K, %D, smoothing |
| Stochastic RSI | Stocastico applicato all'RSI | periodo RSI, stocastico, smoothing |
| Williams %R | -100·(High_n-Close)/(High_n-Low_n) | periodo n |
| ROC | 100·(P_t/P_{t-n}-1) | periodo n |
| Momentum | P_t-P_{t-n} oppure rapporto dei prezzi | periodo n |
| CCI | (Typical Price-SMA)/(.015·mean deviation) | periodo n |
| Awesome Oscillator | SMA mediana veloce - SMA mediana lenta | veloce, lenta |
| Accelerator Oscillator | AO - SMA(AO) | periodo AO, smoothing |
| Ultimate Oscillator | Media pesata di buying pressure/range | tre periodi, pesi |
| RMI | RSI applicato a prezzi separati da m periodi | periodo RSI, momentum lag |
| RVI | Media del rapporto close-open/range smussato | periodo n |
| Fisher Transform | Trasformazione logaritmica del prezzo normalizzato | periodo n, smoothing |
| STC | MACD trasformato con doppio stocastico | MACD veloce/lenta, ciclo, smoothing |
| CMO | 100·(somma up-somma down)/(somma up+somma down) | periodo n |
| Connors RSI | Media RSI prezzo, RSI streak e percentile ROC | tre periodi |
| Elder Ray | EMA base e differenze high/low dalla EMA | periodo EMA |
| Bull/Bear Power | High/Low - EMA | periodo EMA |
| DPO | Prezzo meno SMA spostata indietro | periodo n, offset |
| PPO | 100·(EMA veloce-EMA lenta)/EMA lenta | veloce, lenta, signal |
| Coppock Curve | WMA della somma di due ROC | periodi ROC, WMA |
