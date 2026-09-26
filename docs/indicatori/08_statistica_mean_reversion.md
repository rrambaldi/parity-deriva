# Statistica e mean reversion

| Metodo | Calcolo sintetico | Parametri principali |
|---|---|---|
| Z-score spread | (spread-media)/deviazione standard | finestra |
| Bollinger z-score | (prezzo-SMA)/deviazione standard | finestra |
| Mean deviation | Scostamento relativo dalla media | media, periodo |
| Half-life | Stima OLS della velocità di ritorno | finestra, lag |
| Cointegrazione | Test e residui di regressione tra serie | serie, finestra, significatività |
| ADF | Test di radice unitaria | lag, trend, significatività |
| Engle-Granger | Regressione e test ADF sui residui | specifica regressione |
| Kalman filter | Stima ricorsiva di stato e coefficienti | Q, R, stato iniziale |
| Rolling OLS | Regressione su finestra mobile | finestra, variabile dipendente |
| HP filter | Separazione trend/ciclo tramite penalità | lambda |
| Quantile bands | Quantili mobili della distribuzione | finestra, quantili |
| Residual momentum | Momentum dei residui rispetto a fattori | fattori, finestra |
| Ornstein-Uhlenbeck | dX=κ(θ-X)dt+σdW | κ, θ, σ, finestra |
