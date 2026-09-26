# Logo parity-deriva — file definitivi

Logo scelto: **Alba con deriva** (M1). Un sole ocra a metà sull'orizzonte con il
riflesso a bande sotto; dall'orizzonte parte un cammino con deriva che attraversa il
sole ed esce in alto a destra. L'orizzonte è la parità, il cammino la deriva.
Versione monocolore: **Alba incisa** (M2), stessa geometria con il cammino
ritagliato nel sole.

Regole d'uso in `docs/web/DESIGN.md` (§ 7). "fondo-chiaro" = da usare su fondo chiaro
(inchiostro petrolio); "fondo-scuro" = su fondo scuro (avorio).

## SVG (testo convertito in tracciati, nessun font richiesto)

| file | cosa |
|---|---|
| `parity-deriva_verticale_fondo-chiaro.svg` / `_fondo-scuro.svg` | marchio sopra, nome, tagline |
| `parity-deriva_orizzontale_fondo-chiaro.svg` / `_fondo-scuro.svg` | marchio a sinistra, nome e tagline a destra |
| `parity-deriva_testata_fondo-chiaro.svg` / `_fondo-scuro.svg` | marchio + nome senza tagline, per la barra dell'applicativo |
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

## Font

- Nome: **Marcellus** (400), tagline: **Jost** (400), entrambi SIL OFL, già
  convertiti in tracciati negli SVG.
- Applicativo: **IBM Plex Sans** e **IBM Plex Mono** in `font/`, file ufficiali IBM
  non modificati, con `OFL.txt` e `fonts.css`.

## Token

`tokens.css`: tutti i colori, i font e le misure dell'applicativo nei due temi.

## Rigenerare

Gli script che hanno prodotto questi file sono in `../sorgenti/` e li
riproducono identici (vedi il README della cartella).
