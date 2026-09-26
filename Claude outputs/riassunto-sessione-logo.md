# Riassunto della sessione: logo e identità dell'applicativo

Sessione del 24 settembre 2026 (Roberto con Claude). Da 15 loghi di riferimento al
logo definitivo, ai font e alle regole del layout del viewer di backtest.

- Tela con tutte le tavole: <https://claude.ai/artifact/YQwK1TbB1ZfcSY7sZxxkqN>
  (privata; pagine Proposte, D · Orizzonte, Alba × Classico, Font e componenti)
- Linee guida operative: `docs/web/DESIGN.md`
- File definitivi: `loghi/definitivo/` · script: `loghi/sorgenti/`
- Proposte scartate: `loghi/proposte/` (A–D, `orizzonte/`, `alba-classico/`)

## 1. Analisi dei riferimenti (`loghi/download*.png`, 15 immagini)

| segnale | frequenza |
|---|---|
| petrolio / navy / verde scuro come colore base | 15 su 15 (5 come fondo) |
| accento caldo (oro, ocra, pesca) | 6 |
| maiuscole con molto spazio fra le lettere | 11 |
| germoglio sulle monete | 6 |
| cornice | 5 |
| monogramma P | 3 |
| corsivo | 4 (secondario) |

Tono: sobrio, da private banking più che da fintech; composizione centrata.

## 2. Percorso sul logo

1. **Quattro proposte**: A Parità in deriva (segno "=" che si stacca), B Monogramma
   P a bande, C Germoglio sulle monete, D Orizzonte (moneta ocra, linea di parità,
   cammino con deriva).
2. **Scelta D.** Sviluppata in D1 Classico (cammino che esce dalla moneta),
   D2 Incisa (cammino ritagliato, monocolore), D3 Alba (sole a metà
   sull'orizzonte con riflesso a bande).
3. **Mix Alba × Classico** chiesto da Roberto: M1 Alba con deriva, M2 Alba incisa,
   M3 Alba inscritta.
4. **Scelta finale: M1 · Alba con deriva.** M2 resta come versione monocolore;
   favicon semplificata per 16–32 px.

Dettagli costruttivi: il cammino è un ponte browniano (seme 129) fra il punto in
cui l'orizzonte tocca il sole e un punto in alto a destra fuori dal sole; la
stessa forma è riusata in tutte le varianti. Nome in Marcellus con spaziatura
0,18em, tagline "STRATEGIE DI VALORE" in Jost, trattino del nome in ocra.

## 3. Font dell'applicativo

Misure fatte sui file completi (non sui sottoinsiemi):

| font | occhio medio | cifre | zero barrato | note |
|---|---|---|---|---|
| Marcellus | 0,50 | proporzionali, niente tnum | no | un solo peso → solo logo |
| Jost | 0,46 | tnum | no | I/l/1 ambigui, debole a 12–13 px |
| **IBM Plex Sans** | 0,52 | **tabulari di default** | **sì** | pesi 100–700, larghezza variabile |
| IBM Plex Mono | 0,52 | tabulari | sì | — |
| Inter | 0,55 | tnum | sì | ottimo in piccolo, anonimo |
| JetBrains Mono | 0,55 | tabulari | sì | — |

**Decisioni di Roberto:**
- IBM Plex Sans per l'interfaccia, **IBM Plex Mono per prezzi, orari e importi**
  in tabelle e assi.
- Marcellus solo dentro il logo.
- Font serviti dal repo (file ufficiali IBM, OFL), niente CDN come da README.

## 4. Componenti e colori

Punto di partenza: il viewer esistente (`web/static`: index.html, app.css, app.js),
di cui sono stati mantenuti campi, grafico, indicatori, tabella e interazioni.

**Decisioni:**
- **Tema: segue il sistema**, con interruttore che lo forza (scelta di Roberto).
- **Long verde, short rosso**, con i verdi e i rossi del tema (scelta di Roberto).
  Sostituisce blu/ambra di oggi.
- Ocra solo per marchio, pulsante `Run` (unico elemento pieno), focus e link.
- Nei dati solo tre tinte: blu (entry, selezione, info), verde, rosso.
- **Exit e "stop moved to" neutri** (continua e punteggiata) al posto di viola e
  arancio. *Proposta di Claude applicata insieme alla scelta su long/short, non
  confermata esplicitamente: da rivedere se non convince.*
- Testata con logo senza tagline e badge d'ambiente sempre visibile
  (offline / practice / LIVE pieno in rosso).
- Riga selezionata blu: conserva il legame riga ↔ trade zoomato di oggi.

**Misure che hanno guidato le scelte** (distanza di colore OKLab ×100; sotto 15
due colori si confondono a vista normale; per i daltonici la soglia è 8):

| coppia | distanza |
|---|---|
| ocra – stop rosso (oggi / nuovo) | 13,2 / 10,6 |
| ocra – ambra `short` di oggi | 8,7 |
| ocra – arancio `stop moved to` di oggi | 11,0 |
| ambra – arancio (oggi) | 4,0 |
| blu entry – viola exit (oggi) | 13,6 vista normale, 2,6 protanopia |
| terna nuova blu/verde/rosso, peggior coppia | 17,6 vista normale; verde/rosso 7,0–7,8 per daltonici → sempre con simboli |

Contrasti del testo calcolati: ≥ 4,5:1 su fondo e pannelli in entrambi i temi.

## 5. Punti aperti

- Confermare exit e "stop moved to" neutri (vedi § 4).
- Applicare le linee guida al codice di `web/static` (piano in `docs/web/DESIGN.md` § 9):
  per ora nessun file dell'applicativo è stato modificato.
- IBM Plex Sans Condensed (pacchetto `@ibm/plex-sans-condensed`) se la tabella dei
  trade diventa troppo larga.
- ▲ ▼ ■ non sono nei font Plex: vanno disegnati come SVG in linea.

## 6. Mappa dei file salvati

```
loghi/
  download*.png                 riferimenti di partenza (di Roberto)
  proposte/                     A–D, orizzonte/ (D1–D3), alba-classico/ (M1–M3)
  definitivo/                   logo M1 + monocolore M2, favicon, PNG, font, tokens.css
  sorgenti/                     script che rigenerano tutto
  riassunto-sessione-logo.md    questo file
web/
  DESIGN.md                     linee guida per il layout
```
