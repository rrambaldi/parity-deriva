# Sorgenti del logo

Script Python che generano tutti gli SVG del logo: la geometria del marchio è
scritta in codice e il testo è convertito in tracciati con fontTools, quindi gli
SVG non dipendono dai font installati. Eseguiti da questa cartella riproducono
i file di `../definitivo/` identici nel contenuto (verificato); l'unica differenza
è il blocco `<metadata>` di provenienza (C2PA) che i file consegnati hanno in più.

| file | cosa genera |
|---|---|
| `textpath.py` | conversione testo → tracciati SVG (fontTools) |
| `build.py` | le quattro proposte iniziali A–D e le funzioni comuni (`build`, `wordmark`, `svg`) → `out/` |
| `build_d.py` | sviluppo di D · Orizzonte (D1 classico, D2 incisa, D3 alba) → `out_d/` |
| `build_mix.py` | mix Alba × Classico (M1 **scelto**, M2 monocolore, M3) + favicon → `out_mix/` |
| `build_ui.py` | grafici d'esempio per le tavole e la testata di prima (0,16 em) → `out_ui/` |
| `build_testata.py` | **testata dell'applicativo**: nome a 0,07 em con crenatura → `out_testata/` |
| `allarga_instrument.py` | font dei titoli PD Instrument da Instrument Serif (×1,08) → woff2 |

## Come eseguirli

Con l'env conda del progetto, non con il python di sistema:

```
cd loghi/sorgenti
pip install fonttools brotli
npm i @fontsource/marcellus @fontsource/jost @fontsource/cormorant-garamond @fontsource/josefin-sans
python build_mix.py     # logo scelto e monocolore
python build_testata.py # testata dell'applicativo (0,07 em, crenata)
python build_ui.py      # grafici d'esempio (usa ../definitivo/font/IBMPlexMono-Regular.woff2)

# font dei titoli: scaricare InstrumentSerif-Regular.ttf da
# https://github.com/google/fonts/tree/main/ofl/instrumentserif
python allarga_instrument.py InstrumentSerif-Regular.ttf 1.08 ../definitivo/font/PDInstrument-Regular.woff2
```

`build.py` e `build_d.py` servono solo se si vogliono rivedere le proposte scartate.
I font di Fontsource servono solo alla conversione del testo; `node_modules/` e le
cartelle `out*/` non vanno committate.

## Parametri che contano

- `build_mix.py`: `CX, CY, R, HY` (sole e orizzonte), `WALK_M1` (il cammino:
  ponte browniano con seme 129, stessa forma in tutte le varianti), le bande del
  riflesso in `bands()`.
- `build_d.py`: `bridge()` genera il cammino con deriva fra due punti fissi;
  `modes()` contiene i colori per fondo chiaro, scuro e monocolore.
- `build.py`: `build()` impagina verticale, orizzontale e icona; `NS`/`TS` sono
  le dimensioni di nome e tagline. `TAG` può avere più righe separate da `\n`
  (oggi `QUANTITATIVE STRATEGIES\nEDGE BY DESIGN`); con una riga sola gli SVG sono
  identici a quelli di prima.
- `build_mix.py`: `tag2` (colore dalla seconda riga della tagline, ocra) e `tlead`
  (passo fra le righe, 2,3 altezze delle maiuscole).
- `build_testata.py`: `TRACK` (spaziatura del nome, em), `GAP` (spazio fra marchio e
  nome), crenatura sì/no; da riga di comando `python build_testata.py 0.16 18 0`
  rigenera la testata di prima, identica.
- `allarga_instrument.py`: fattore di larghezza (secondo argomento); scala in x
  contorni, avanzamenti, crenatura e ancore; l'hinting (solo verticale) resta.
