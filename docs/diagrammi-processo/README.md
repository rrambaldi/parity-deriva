# Diagrammi del processo parity-deriva

Otto tavole SVG tratte da `PROCESSO.md` (sezione "Grafici per claude.ai"), stesso stile
dei diagrammi di architettura: font incorporati, icone Tabler, logo definitivo.

| file | cosa |
|---|---|
| `…_1-processo-completo.svg` | il processo in quattro corsie: Test, Archivio, Trade DEMO, Trade REAL |
| `…_2-loop-simulazione.svg` | SIM: sweep, risultati, correlazioni, filtro candidato, gate holdout |
| `…_3-sviluppo-holdout.svg` | come si usa lo storico, versioni bocciate |
| `…_4-mix-stesso-strumento.svg` | posizioni di tre strategie, zone critiche, controlli del mix |
| `…_5-prima-di-andare-live.svg` | sequenza DEMO → Archivio → REAL e checklist della promozione |
| `…_6-stati-e-protezioni-live.svg` | stati: DEMO, ramp, 100%, SUSPENDED, DEAD |
| `…_7-banda-monte-carlo.svg` | banda 5°–95° percentile, curva in linea e curva fuori banda |
| `…_8-cosa-ce-e-cosa-manca.svg` | roadmap per fase e ordine dei cantieri |

Convenzioni: linea piena = c'è già; linea tratteggiata ed etichetta "da fare" = da fare.
Stati: SIM arancione, DEMO verde chiaro, LIVE ramp verde, LIVE 100% verde pieno,
SUSPENDED ambra, DEAD rosso; gate = rombo giallo. ▲ long verde, ▼ short rosso.

Rigenerare, dalla cartella `sorgenti/` (servono anche `lib.py` e `render_png.py` di
`../diagrammi-architettura/sorgenti/`, da copiare accanto o mettere nel PYTHONPATH):

```
pip install fonttools brotli
PYTHONPATH=../../diagrammi-architettura/sorgenti python processo.py ..
```

Le curve della tavola 7 sono d'esempio (300 trade simulati, 45% vinti a +1.6R, persi a −1R,
2000 rimescolamenti, seme fisso).
