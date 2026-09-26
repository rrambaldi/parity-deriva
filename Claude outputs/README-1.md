# Sorgenti del logo

Script Python che generano tutti gli SVG del logo: la geometria del marchio è
scritta in codice e il testo è convertito in tracciati con fontTools, quindi gli
SVG non dipendono dai font installati. Eseguiti da questa cartella riproducono
byte per byte i file di `../definitivo/` (verificato).

| file | cosa genera |
|---|---|
| `textpath.py` | conversione testo → tracciati SVG (fontTools) |
| `build.py` | le quattro proposte iniziali A–D e le funzioni comuni (`build`, `wordmark`, `svg`) → `out/` |
| `build_d.py` | sviluppo di D · Orizzonte (D1 classico, D2 incisa, D3 alba) → `out_d/` |
| `build_mix.py` | mix Alba × Classico (M1 **scelto**, M2 monocolore, M3) + favicon → `out_mix/` |
| `build_ui.py` | testata senza tagline e grafici d'esempio per le tavole → `out_ui/` |

## Come eseguirli

Con l'env conda del progetto, non con il python di sistema:

```
cd loghi/sorgenti
pip install fonttools brotli
npm i @fontsource/marcellus @fontsource/jost @fontsource/cormorant-garamond @fontsource/josefin-sans
python build_mix.py     # logo scelto e monocolore
python build_ui.py      # testata (usa ../definitivo/font/IBMPlexMono-Regular.woff2)
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
  le dimensioni di nome e tagline.
