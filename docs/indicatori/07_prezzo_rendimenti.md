# Prezzo, rendimenti e statistiche rolling

| Indicatore | Calcolo sintetico | Parametri principali |
|---|---|---|
| Rendimento semplice | P_t/P_{t-1}-1 | lag |
| Log-return | ln(P_t/P_{t-1}) | lag |
| Rendimento cumulato | Prodotto cumulato di (1+r) - 1 | finestra |
| Gap | Open_t/Close_{t-1}-1 | nessuno |
| Distanza dalla media | Prezzo/media - 1 | media, periodo |
| Z-score prezzo | (prezzo-media)/deviazione standard | finestra |
| Percent rank | Percentuale di valori inferiori nella finestra | finestra |
| CLV | ((close-low)-(high-close))/(high-low) | OHLC |
| Beta rolling | Covarianza asset/benchmark divisa per varianza benchmark | finestra |
| Correlazione rolling | Covarianza normalizzata | finestra |
| R-squared | Quadrato della correlazione o fit OLS | finestra |
| Hurst exponent | Stima della persistenza/scalabilità della serie | metodo, finestra |
| Skewness | Momento centrato cubico normalizzato | finestra |
| Kurtosis | Momento centrato quarto normalizzato | finestra |
| Autocorrelazione | Correlazione tra serie e sua versione ritardata | lag, finestra |
| Spread/ratio | Prezzo A - β·Prezzo B oppure A/B | hedge ratio, finestra |
