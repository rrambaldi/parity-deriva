# Indicatori di volume

| Indicatore | Calcolo sintetico | Parametri principali |
|---|---|---|
| Volume medio | Media mobile dei volumi | periodo n |
| OBV | Volume sommato se close sale, sottratto se scende | nessuno |
| VWAP | Σ(prezzo tipico·volume)/Σ(volume) | sessione o ancoraggio |
| Anchored VWAP | VWAP calcolato da un punto iniziale scelto | timestamp iniziale |
| MFI | RSI del money flow positivo/negativo | periodo n, prezzo tipico |
| CMF | Somma money flow volume / somma volume | periodo n |
| Accumulation/Distribution | Somma CLV·volume | nessuno |
| Chaikin Oscillator | EMA veloce A/D - EMA lenta A/D | periodi veloce/lenta |
| Force Index | (close-prev close)·volume, spesso smussato | periodo smoothing |
| EMV | Variazione prezzo normalizzata per volume e range | periodo n, divisione volume |
| VPT | Somma volume·variazione percentuale prezzo | nessuno |
| Volume ROC | Variazione percentuale del volume | periodo n |
| NVI/PVI | Indice aggiornato quando il volume diminuisce/aumenta | valore iniziale |
| Klinger Oscillator | EMA veloce/lenta del volume force | periodi, signal |
| Volume Oscillator | EMA volume veloce - EMA volume lenta | periodi |
| Volume Profile | Volume aggregato per livello di prezzo | bin size, finestra |
| RVOL | Volume corrente/media volume | periodo n |
| CVD | Volume aggressivo buy - volume aggressivo sell cumulato | fonte bid/ask |
| Open Interest | Contratti futures aperti aggregati | nessuno |
