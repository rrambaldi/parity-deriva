# Order flow e microstruttura

| Indicatore | Calcolo sintetico | Parametri principali |
|---|---|---|
| Bid-ask spread | Ask - Bid, assoluto o percentuale | prezzi bid/ask |
| Mid-price | (Bid+Ask)/2 | bid, ask |
| Book imbalance | (bid volume-ask volume)/(somma volumi) | livelli order book |
| CVD | Volume market buy - market sell cumulato | classificazione trade |
| Delta | Buy volume - sell volume per barra | finestra/barra |
| Footprint | Aggregazione trade per livello prezzo | tick size, intervallo |
| Depth imbalance | Squilibrio dei volumi entro profondità | profondità |
| Microprice | Prezzo ponderato per quantità bid/ask | best bid/ask, quantità |
| Queue imbalance | Squilibrio delle code al best bid/ask | livelli |
| Amihud | |return|/volume monetario | finestra |
| Kyle lambda | Regressione variazione prezzo su signed volume | finestra |
| VPIN | Probabilità di volume tossico da bucket buy/sell | bucket, finestra |
| Funding rate | Tasso periodico pagato tra long e short | intervallo |
| Basis | Futures - spot, spesso annualizzato | scadenza, giorni |
| Liquidations | Somma liquidazioni long/short | finestra |
