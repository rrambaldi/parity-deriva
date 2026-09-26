# Logo parity-deriva — file definitivi

Logo scelto: **Alba con deriva** (M1). Un sole ocra a metà sull'orizzonte con il
riflesso a bande sotto; dall'orizzonte parte un cammino con deriva che attraversa il
sole ed esce in alto a destra. L'orizzonte è la parità, il cammino la deriva.
Versione monocolore: **Alba incisa** (M2), stessa geometria con il cammino
ritagliato nel sole.

Regole d'uso in `web/DESIGN.md` (§ 7). "fondo-chiaro" = da usare su fondo chiaro
(inchiostro petrolio); "fondo-scuro" = su fondo scuro (avorio).

## SVG (testo convertito in tracciati, nessun font richiesto)

| file | cosa |
|---|---|
| `parity-deriva_verticale_fondo-chiaro.svg` / `_fondo-scuro.svg` | marchio sopra, nome, tagline |
| `parity-deriva_orizzontale_fondo-chiaro.svg` / `_fondo-scuro.svg` | marchio a sinistra, nome e tagline a destra |
| `parity-deriva_testata_fondo-chiaro.svg` / `_fondo-scuro.svg` | marchio + nome senza tagline, per la barra dell'applicativo; nome più stretto (0,07 em) e crenato, vedi sotto |
| `parity-deriva_marchio_fondo-chiaro.svg` / `_fondo-scuro.svg` | solo il marchio |
| `parity-deriva_icona_chiara.svg` / `_scura.svg` | marchio in quadrato arrotondato |
| `parity-deriva_icona_quadrata_scura.svg` | a tutto quadro, per apple-touch-icon e avatar |
| `parity-deriva_monocolore_{verticale,orizzontale,marchio}_{inchiostro,avorio}.svg` | un colore solo (M2) |
| `favicon.svg` / `favicon_chiara.svg` | favicon semplificata per 16–32 px |

## PNG (`png/`)

| file | misura |
|---|---|
| `favicon-16.png`, `favicon-32.png`, `favicon-48.png` | favicon |
| `apple-touch-icon.png` | 180 × 180, a tutto quadro |
| `icon-192.png`, `icon-512.png` | icone per web manifest |
| `avatar-1024.png` | avatar e social |
| `verticale_fondo-*.png` | 1200 px di larghezza, fondo trasparente |
| `orizzontale_fondo-*.png` | 1600 px di larghezza, fondo trasparente |
| `testata_fondo-*.png` | 128 px di altezza (testata a 64 px su schermi 2×) |

## Colori del logo

| nome | valore |
|---|---|
| Ocra (sole, riflesso, trattino) | `#C98A45` |
| Inchiostro petrolio (fondo chiaro) | `#152C3A` |
| Avorio (fondo scuro) | `#F4EFE4` |
| Notte (fondo dell'icona) | `#0B1A26` |
| Tagline su chiaro / su scuro | `#6B7A83` / `#C7AA9B` |
| Seconda riga della tagline (EDGE BY DESIGN) | ocra `#C98A45`, su chiaro e su scuro; nelle monocolore dello stesso colore del resto |

## Font

- Nome: **Marcellus** (400), tagline: **Jost** (400), entrambi SIL OFL, già
  convertiti in tracciati negli SVG.
- Applicativo: **IBM Plex Sans** e **IBM Plex Mono** in `font/`, file ufficiali IBM
  non modificati, con `OFL.txt` e `fonts.css`.
- Titoli dell'applicativo (nome della pagina, dialog, pannelli): **PD Instrument**,
  `font/PDInstrument-Regular.woff2`: Instrument Serif allargato ×1,08 in orizzontale,
  sottoinsieme latino, rinominato (SIL OFL 1.1 senza nomi riservati, licenza in
  `font/OFL-Instrument-Serif.txt`). Nel CSS con spaziatura +0,01 em.

## Tagline

Dal 26/09/2026 la tagline è in inglese, su due righe sotto il nome:
**QUANTITATIVE STRATEGIES** (grigio) / **EDGE BY DESIGN** (ocra). Jost 400, spaziatura
0,46 em come prima, passo fra le righe 2,3 volte l'altezza delle maiuscole. La testata
non ha tagline e non è cambiata. I file con la tagline di prima ("STRATEGIE DI VALORE")
sono in `_precedente_strategie-di-valore/`.

## Spaziatura del nome

Il nome PARITY-DERIVA ha spaziatura 0,18 em nelle versioni con tagline (verticale,
orizzontale) e **0,07 em nella testata**, con la crenatura del font (PA, VA, Y-):
dal 25/09/2026, per stare accanto ai titoli dell'applicativo senza staccare troppo.
La testata di prima (0,16 em, senza crenatura) si rigenera con
`python build_testata.py 0.16 18 0`.

## Token

`tokens.css`: tutti i colori, i font e le misure dell'applicativo nei due temi.

## Rigenerare

Gli script che hanno prodotto questi file sono in `../sorgenti/` e ne
riproducono esattamente i tracciati (vedi il README della cartella). I file
consegnati qui contengono in più un blocco `<metadata>` di provenienza (C2PA),
aggiunto alla consegna: non cambia il disegno.
