# candle_forecast — livello 0

Dato un contesto di N candele, prevedere le M successive (M = 1, 2) e misurare se c'è segnale
rispetto a baseline banali. Specifica: `../prompt-claude-code-livello0.md`.

## Ambiente

Env conda dedicato su `/mnt` (la root è quasi piena), da `conda-forge` (i canali Anaconda
chiedono l'accettazione dei ToS, non fatta):

```bash
E=/mnt/HC_Volume_37718599/rrambaldi/envs/candle_forecast
CONDA_PKGS_DIRS=/mnt/HC_Volume_37718599/rrambaldi/.conda-pkgs \
  /mnt/HC_Volume_37718599/miniconda3/bin/conda create -p $E --override-channels -c conda-forge python=3.12
$E/bin/pip install pandas numpy pytest lightgbm tables
$E/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
```

Versioni in uso: Python 3.12, pandas 3.0.6, numpy 2.5.3, lightgbm 4.7.0, torch 2.14.0+cpu, pytest 9.1.1,
tables 3.11.1 (PyTables: serve per leggere lo store HDF5, APERTO-1).

Il training non ha bisogno di `parity_deriva`: lo store si legge con `pd.read_hdf` (chiave `/M5`,
come `parity_deriva.data.store.load`) e SMA, EMA e ATR sono copiati da `parity_deriva/lib/indicators.py`.
Due test verificano che restino identici agli originali dove `parity_deriva` è importabile.
Solo `pack-stores` usa `parity_deriva` (per `settings.DATA_DIR`), quindi gira sul server.

## Su Windows: `run.cmd`

Doppio clic su `run.cmd` (o lancio da "Anaconda Prompt"). Chiede subito se alla fine fare commit e
push dei risultati, poi fa tutto da solo: crea l'env conda `candle_forecast` se manca e installa le
librerie, lancia i test (si ferma se falliscono), `prepare`, `run` su tutti i sistemi, e il push se
richiesto. Serve solo conda installato (Miniconda va bene). `num_threads = 0` nel config usa tutti i core.

## Store zippati (dati in git)

I dati di training viaggiano nel repo come `stores/<store>.zip` (per EURUSD `stores/EUR_USD.hd5.zip`,
15,4 MB). Due passi:

- **dove c'è lo store** (il server, `parity_deriva` `settings.DATA_DIR`):
  `$PY -m candle_forecast.cli pack-stores` zippa gli store di `[instruments]` in `stores/`
  (`--instrument EURUSD` per uno solo). Se lo store non è cambiato lo zip non viene riscritto,
  e a parità di contenuto i byte sono identici: git non vede modifiche finte. Poi `git add stores/`.
- **dove si addestra** (il tuo PC): dopo `git pull`, `prepare` scompatta da solo lo zip in
  `stores/` (la copia `.hd5` è in `.gitignore`), verifica lo SHA-256 e la riusa finché lo zip non
  cambia. `prepare` legge **solo** dallo zip: se manca si ferma, non ripiega su `DATA_DIR`.

Compressione LZMA: metà del deflate (15 MB contro 32). Ogni aggiornamento dello store aggiunge
uno zip intero alla storia di git. Lo zip si apre con Python o 7-Zip; il comando `unzip` classico
non conosce LZMA.

## Come si lancia

Dalla cartella `candle_forecast/` del repo, con `PY=$E/bin/python`:

```bash
$PY -m pytest -q                                    # test 1-9, dati sintetici
$PY -m candle_forecast.cli pack-stores              # solo dove c'è lo store: zip in stores/ per git
$PY -m candle_forecast.cli prepare                  # scompatta lo zip, valida, report, candele, indicatori, campioni
$PY -m candle_forecast.cli run --models baselines   # solo baseline, tutti i sistemi
$PY -m candle_forecast.cli run --N 21 --M 1         # un sistema, baseline + lgbm + gru
$PY -m candle_forecast.cli run                      # griglia completa del config
$PY -m candle_forecast.cli run --open-test          # apre il test: loggato in results/open_test.log
```

## Cosa produce

`results/EURUSD_M5_mid/` (da `prepare`):
- `data_report.txt`: validazione, buchi, candele incomplete, segmenti, campioni per sistema;
- `gaps.csv`: tutti i buchi fuori dal weekend;
- `candles.npz`: candele in unità minime (`ts`, `units`, `breaks`, `n_bars`, `n_expected`) e
  indicatori (`extra`, `level`, `warmup`, `names`);
- `samples_N{N}_M{M}.npz`: indici `t` dei campioni per segmento (`fit`, `es`, `train`, `val`, `test`).
  Sono i dati di training e test: `samples.windows(units, t, N, M, extra, level)` li trasforma in X, Y.
  X ha 4 + 3 canali: open, high, low, close, SMA100, ATR14, EMA21.

`results/<sistema>/` (da `run`, es. `EURUSD_M5_mid_N21_M1_sma100-atr14-ema21/`): `predictions_val.csv` (una riga per campione: target veri,
modelli, baseline), `train_info.json` (seed, iterazioni, epoche, tempi, file dei modelli) e
`models/`, dove si tengono **tutti** i modelli addestrati:
- `lgbm_h{h}_{high|low|close}.txt`: un regressore LightGBM per valore, tagliato al `best_iteration`
  (circa 3 KB per albero: 1 MB circa per sistema, 19 MB al massimo con M=2 e 1000 alberi);
- `gru_s{seed}.pt`: pesi, costanti di scala e forma della rete (circa 19 KB per seed).

Per ricaricarli: `models.lgbm.load(dir, M)` e `models.gru.load(path)`, poi `predict(...)` sugli
stessi X costruiti con `samples.windows` (stessi N, M, indicatori).
`results/summary.csv`: una riga per (sistema, modello, segmento).

## Kronos da zero: `run_kronos.cmd`

Confronto con il LightGBM delle escursioni (`parity_deriva/scripts/excursion_lgbm.py`, branch `dev`):
stesso EURUSD M5, stesso taglio (si impara prima del 2020-11-11 meno 288 candele, si prevede dopo),
stesse misure. `candle_forecast/kronos_scratch.py` addestra il tokenizer (config di
Kronos-Tokenizer-base) e il modello (config di Kronos-mini, 4,1M parametri) partendo da pesi casuali,
poi per un punto di test ogni 96 candele genera 8 percorsi di 288 candele e salva, per h = 16, 48, 288,
la media di massimo, minimo e close raggiunti in `results/kronos/scratch-mini/predictions.csv`.
Il codice di Kronos (MIT, commit fissato) si clona da solo in `third_party/`, fuori da git.

Doppio clic su `run_kronos.cmd`: librerie, torch con CUDA se c'è una GPU NVIDIA, prova veloce
(`--smoke`), training, previsioni, push. Con GPU circa un'ora; solo CPU molte ore (lo stima dopo i
primi passi). `run_kronos.cmd --pretrained` usa invece Kronos-small già addestrato, senza training.
Il confronto si fa sul server, dove c'è lo store che legge LightGBM:

```bash
python -m parity_deriva.scripts.excursion_lgbm EUR_USD --compare <repo>/candle_forecast/results/kronos/scratch-mini/predictions.csv
```

## Decisioni

| Codice | Voce | Valore | Stato | Dove |
|---|---|---|---|---|
| DEC-1 | Dati | M5 dal 2015, BID e ASK | deciso | `config` |
| DEC-2 | Rappresentazione | delta dal close di `t`, in unità minime | deciso | `samples.py` |
| DEC-3 | Unità minima | EURUSD 0.00001 | deciso | `config`, `data.check_side` |
| DEC-4 | N | 21, 37, 42, 108 | deciso | `config` |
| DEC-5 | M | 1, 2 | deciso | `config` |
| DEC-6 | Split | 75 / 25, da config | deciso | `split.py` |
| DEC-7 | Soglia campioni | nessuna, `n` in ogni riga | deciso | `summary.csv` |
| L0-P1 | Il 25% | 12,5% validazione + 12,5% test bloccato | PROPOSTA | `config`, `split.py` |
| L0-P2 | Serie | mid campo per campo; bid/ask selezionabili | PROPOSTA | `resample.price_series` |
| L0-P3 | Chiusura daily | 17:00 New York, con ora legale | PROPOSTA | `resample.py` |
| L0-P4 | High/low del mid | media di bid e ask (approssimazione) | PROPOSTA | `resample.price_series` |
| L0-P5 | Timestamp | inizio intervallo, UTC | PROPOSTA | `data.read_csv`, `resample.py` |
| L0-P6 | Finestre su un buco | superata da APERTO-3; resta attivabile con `exclude_gap_windows = true` | sostituita | `samples.valid_refs` |
| L0-P7 | Baseline | ZERO, REPEAT, MEAN | PROPOSTA | `baselines.py` |
| L0-P8 | Iperparametri | punti di partenza, non ottimizzati | PROPOSTA | `config` |
| L0-P9 | Early stopping | ultimo 10% del training | PROPOSTA | `split.bounds` |
| APERTO-1 | Sorgente | lo store HDF5 già caricato, zippato in `stores/EUR_USD.hd5.zip` (da `settings.DATA_DIR`), chiave `/M5`, colonne `bid_o…ask_c`; il `mid_*` dello store non si usa, si ricalcola (L0-P2) | deciso | `data.read_store` |
| APERTO-2 | Fuso | indice in UTC, timestamp = inizio barra (prima barra 22:00 UTC = 17:00 NY) | deciso, per ora | `data.read_store` |
| APERTO-3 | Candele mancanti | la candela mancante non esiste: la sequenza continua per indice, nessuna finestra esclusa; i buchi restano nel report. Nei timeframe ricampionati le incomplete si tengono come sono | deciso | `config` `exclude_gap_windows = false` |
| APERTO-4 | Primo sistema | EURUSD M5, mid, tutti gli N e M | deciso | `config` |
| APERTO-5 | Indicatori | SMA100 sul close, ATR14 (Wilder), EMA21 sul close; riscaldamento: via le prime 99 candele, finché SMA100 non c'è | deciso | `indicators.py`, `config` |

### Scelte tecniche non coperte dal prompt (DA-CONFERMARE)

| Voce | Valore messo | Perché |
|---|---|---|
| Definizione di buco | qualunque barra M5 attesa che manca fuori da venerdì 17:00 → domenica 17:00 NY | serve solo al report (APERTO-3 tiene le finestre); è la definizione senza soglia |
| `early_stopping_rounds` LightGBM | 50 | il prompt dice "con early stopping" senza numero |
| GRU `max_epochs`, `patience` | 30, 3 | idem |
| `num_threads` | 2 | la macchina ha 2 core |
| Arrotondamento a mezzo tick in `to_units` | sì | toglie il rumore della virgola mobile; i prezzi validati sono multipli esatti del tick |
| Direzione per ZERO | non calcolabile (previsto = 0 sempre, tutti esclusi) | conseguenza della regola della Parte 6 |
| Test 8 e 9 | iperparametri ridotti (300 alberi, 8 epoche), stessi indicatori del config | tempo del test; la pipeline è la stessa |
| Espressione dell'ATR | in unità minime, **senza** togliere il close di riferimento | è un'ampiezza, non un livello: "delta da ref" (Parte 7) non ha senso. SMA ed EMA invece sono delta da ref |
| Indicatori | funzioni di `parity_deriva.lib.indicators` (EMA con seme = media dei primi 21 close, ATR di Wilder con seme = media dei primi 14 TR) | sono le curve che leggono strategie e grafici |
| Indicatori e confini dei segmenti | calcolati sulla serie intera, quindi il primo campione di validazione usa candele di training per la SMA | solo passato: nessun look-ahead (test in `test_indicators.py`). Se vuoi il riscaldamento ripetuto in ogni segmento, va cambiato |
