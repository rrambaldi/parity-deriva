# Diagrammi di architettura parity-deriva

SVG autonomi (font incorporati, icone Tabler outline da `icone-per diagrammi/`,
logo da `loghi/definitivo/`), colori e font di `loghi/definitivo/tokens.css`.

| cartella | cosa |
|---|---|
| `parity-deriva_1..4-*.svg` | tavole di dettaglio: platform, simple, medium, cloud + home lab |
| `overview/` | le stesse quattro a livello alto: un nodo per server, legenda su una riga |
| `originali/` | le tavole di partenza (PNG) |
| `sorgenti/` | script che rigenerano tutto |

## Rigenerare

Dalla cartella `sorgenti/`, con l'env conda del progetto:

```
pip install fonttools brotli
python diagrams.py ../      # tavole di dettaglio
python overview.py ../overview
# anteprime PNG (facoltative): pip install playwright && playwright install chromium
python render_png.py "../*.svg"
```

Le icone (`icone-per diagrammi/tabler-outline/`) non sono nel repository: per
rigenerare servono lì o in `PD_ICONS`. I percorsi di icone e logo sono relativi alla radice del progetto; si cambiano con
`PD_ICONS` e `PD_LOGHI`. `lib.py` contiene colori, componenti (server, moduli,
frecce, legenda) e l'incorporamento dei font ridotti ai soli glifi usati.

## Convenzioni

- push strategie (inchiostro, freccia piena): solo archive → demo / real
- MCP (blu): risultati e trade status verso l'archive, strategie e indicatori con il test
- you + AI → MCP (ocra bronzo, evidenziato): archive = strategie e indicatori,
  test = affinare parametri e algoritmo, demo / real = dati dei trade in sola lettura
- backup (tratteggio grigio): rclone verso S3, Google Drive, OneDrive o qualsiasi remote; scrive solo l'archive
- badge: `practice` blu, `live · real money` rosso pieno, `no orders` neutro
- nessun fornitore cloud nominato: "cloud · virtual machines · any provider"
