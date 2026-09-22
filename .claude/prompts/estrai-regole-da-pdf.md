# Prompt: da PDF a documento di regole in Markdown

Stadio a monte della skill `strategia-da-documento`: produce il `.md` che quella
skill legge nella sua fase 1. Sostituisci i due percorsi e passa il testo a Claude.

---

Leggi il PDF `<PERCORSO/DEL/DOCUMENTO.pdf>` e scrivi `<PERCORSO/OUTPUT.md>`: un
documento Markdown che contenga le regole operative del trading system descritto,
in forma utilizzabile da chi dovrà implementarle senza riaprire il PDF.

## Come leggerlo

Leggilo tutto, a blocchi di pagine con il parametro `pages` del tool Read, fino
all'ultima pagina. Non fermarti quando ti sembra di avere il quadro: nei testi di
trading le condizioni di uscita, le eccezioni e i filtri arrivano dopo gli
esempi, e spesso una regola viene corretta o ristretta venti pagine più avanti.
Se una parte è una scansione senza testo estraibile, dillo indicando le pagine
invece di indovinarne il contenuto.

Se il PDF descrive più sistemi (o più varianti dello stesso), trattali come
documenti separati: una sezione per ciascuno, non una fusione.

## Cosa deve contenere il .md

Struttura in questo ordine:

1. **Intestazione** — titolo, fonte (autore, titolo, edizione se ci sono),
   percorso del PDF, intervallo di pagine effettivamente letto, data di
   estrazione.
2. **Sintesi in 5 righe** — che tipo di sistema è (trend following, mean
   reversion, breakout...), su quali mercati e time frame l'autore lo applica.
3. **Regole operative**, una sottosezione per ciascuna di queste voci, e in
   questo ordine: *entry*, *stop loss*, *take profit e uscite*, *filtri e
   vincoli* (sessione, giorni, volatilità, spread, numero di posizioni, scadenza
   dei pendenti), *dimensionamento* se il testo ne parla.
4. **Tabella dei parametri** — nome, valore indicato dal testo, pagina, e una
   colonna "fonte" con `esplicito` / `dedotto` / `assente`.
5. **Definizioni** — ogni termine, indicatore o sigla che il documento usa in
   modo proprio, con la definizione che ne dà lui, non quella generica.
6. **Zone d'ombra** — elenco numerato di ciò che resta ambiguo o non detto, con
   la domanda precisa a cui servirebbe risposta per poter scrivere il codice.
7. **Citazioni letterali** — per ogni regola critica (entry, stop, target) la
   frase originale tra virgolette con la pagina.

## Come scriverlo

- **Ogni affermazione porta la pagina**, nella forma `(p. 42)`. Una riga senza
  pagina è una riga che non sopravvive alla revisione.
- Distingui sempre tre cose, e marcale in modo visibile: ciò che il documento
  **dice**, ciò che il documento **implica** e che tu stai deducendo, ciò che il
  documento **non dice**. Non scrivere mai una deduzione con il tono di una
  citazione.
- Non completare il sistema. Se manca il criterio di uscita, la sezione "take
  profit" dice che manca e finisce lì: non ci metti un default ragionevole preso
  dal tuo mestiere. Le proposte, se le hai, vanno nelle zone d'ombra come
  domande.
- Numeri: riporta valore **e unità** come stanno nel testo (pip, punti, ticks,
  percentuale, ATR), senza convertirli. Se l'unità non è dichiarata, è una zona
  d'ombra, non un dettaglio.
- Formule: trascrivile in blocco di codice, con il significato di ogni simbolo
  accanto.
- Grafici e figure: se una regola sta solo in una figura, descrivi cosa mostra e
  segnala che la sorgente è un'immagine — è il tipo di regola che va riletta a
  occhio prima di fidarsene.
- Contraddizioni interne (il testo dice una cosa a p. 30 e un'altra a p. 80):
  riportale entrambe con le rispettive pagine, senza sceglierne una.
- Markdown semplice: intestazioni, elenchi, tabelle e blocchi di codice. Niente
  HTML, niente decorazioni. Deve restare leggibile come testo grezzo.

## Verifica prima di consegnare

Rileggi il `.md` e controlla tre cose, dichiarando l'esito:

1. ogni regola ha la pagina;
2. entry, stop loss e take profit sono ciascuno o definiti in modo eseguibile
   (un programmatore saprebbe cosa scrivere) o esplicitamente marcati come
   incompleti;
3. nel file non è finito nulla che nel PDF non ci sia.

Poi dimmi in due righe quanto il documento è pronto per essere implementato e
qual è la zona d'ombra che pesa di più.
