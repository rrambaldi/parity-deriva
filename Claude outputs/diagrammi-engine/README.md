# Diagramma del motore di simulazione

`parity-deriva_engine.svg`, da `docs/ENGINE.md`: un bus di eventi, gli stessi handler,
due cablaggi (backtest con ReplayEngine e SimulatedBroker; live con execution, broker
reale, simulatore come ombra e parity monitor). Sotto: ordine degli handler, regole di
fill del simulatore, niente look-ahead e cosa una barra non può dire.

Rigenerare, dalla cartella `sorgenti/` (usa `lib.py` e `flowlib.py` delle altre serie):

```
pip install fonttools brotli
PYTHONPATH=../../diagrammi-architettura/sorgenti:../../diagrammi-processo/sorgenti python engine.py ..
```
