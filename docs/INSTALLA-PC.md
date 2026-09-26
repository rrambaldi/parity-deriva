# Installare parity-deriva sul PC (Windows)

Questa guida porta parity-deriva sul tuo PC, dentro Docker. Il PC prende tutto
dall'**Archivio**, il server già acceso (oggi
`https://priv.progettazionisoftware.it/parity-dev/`): candele, calendario, set
degli spread, strategie e indicatori. Poi simula per conto suo.

Quasi tutto lo fa uno script. Tu clicchi, rispondi "Sì" a Windows e compili una
pagina alla fine.

**Tempo:** 20-40 minuti la prima volta, quasi tutto attesa.

---

## Prima di iniziare: cosa ti serve

- [ ] Windows 10 (versione 22H2) o Windows 11, a 64 bit.
- [ ] Almeno **20 GB liberi** sul disco C:.
- [ ] Essere **amministratore** del PC: Windows ti chiederà "Sì" qualche volta.
- [ ] Un account **GitHub** che vede il repository `rrambaldi/parity-deriva`.
- [ ] Il browser del PC deve aprire l'Archivio (serve il tuo certificato
      client, lo stesso che usi sempre).

---

## Passo 1 — Prepara il token sull'Archivio (5 minuti)

Il token è la "chiave" con cui il PC si fa riconoscere dall'Archivio.

1. Apri nel browser: `https://priv.progettazionisoftware.it/parity-dev/`
   Se il browser chiede un certificato, scegli il tuo.
2. Clicca **settings** (*impostazioni*), in alto.
3. Clicca la scheda **AI assistants** (*assistenti AI*).
4. Scendi fino alla riga con **program** (*programma*):
   - in **program** scrivi: `PC di casa`
   - in **role** (*ruolo*) scegli: **pc: pushes strategies, sets, mixes; copies the market data**
5. Clicca **new program token** (*nuovo token per il programma*).
6. Nella tabella compare la riga "PC di casa". Clicca **copy** (*copia*) su
   quella riga.
7. Apri **Blocco note**, incolla (Ctrl+V) e salva il file sul Desktop come
   `token-parity.txt`. Ti serve al passo 4.

---

## Passo 2 — Scarica l'installatore

1. Apri questo indirizzo nel browser:
   `https://github.com/rrambaldi/parity-deriva/archive/refs/heads/dev.zip`
   Se GitHub chiede di entrare, entra col tuo account.
2. Nella cartella **Download** arriva il file `parity-deriva-dev.zip`.
3. Clic destro sul file → **Estrai tutto…** → **Estrai**.
4. Si apre la cartella estratta. Entra in `parity-deriva-dev`, poi nella
   cartella `docker`.

---

## Passo 3 — Lancia l'installatore

1. Doppio clic su **`install-pc.cmd`**.
2. Se compare **"Windows ha protetto il PC"**: clicca **Ulteriori informazioni**,
   poi **Esegui comunque**.
3. Si apre una **finestra nera**. Lasciala aperta: lavora da sola e scrive cosa
   sta facendo.

Cosa fa la finestra, in ordine (salta da sola quello che c'è già):

| La finestra scrive | Cosa sta facendo | Cosa fai tu |
|---|---|---|
| `Installing Git...` | installa Git | se Windows chiede "Vuoi consentire…?" → **Sì** |
| `Turning on WSL 2...` | attiva WSL 2, che serve a Docker | **Sì**; poi la finestra dice `Now restart the PC` → **riavvia il PC e rifai il passo 3** |
| `Installing Docker Desktop...` | installa Docker Desktop | **Sì** |
| `Starting Docker Desktop...` | avvia Docker Desktop | la prima volta Docker mostra le condizioni → **Accept**; se chiede di entrare o creare un account → **Skip** / **Continue without signing in** |
| `Taking the code from GitHub...` | scarica il programma | se si apre "Connect to GitHub" → **Sign in with your browser** → autorizza |
| `Building and starting parity-deriva...` | costruisce e avvia | niente: la prima volta ci mette qualche minuto |
| `Setup open in the browser` | ha finito | vai al passo 4 |

Alla fine il **browser si apre da solo** sulla pagina **setup**. Il codice è già
inserito.

---

## Passo 4 — Rispondi al setup (le risposte giuste per un PC)

La pagina ha delle sezioni numerate. Compila così.

**1 · setup code** — già fatto. Se la pagina chiede il codice: è nella finestra
nera, riga `Setup open in the browser (code XXXX-XXXX)`. Scrivilo e clicca
**continue**.

**start from a profile** — lascia **none: start empty**.

**2 · who may open the pages** — scegli **no sign-in: only on this PC**.
Va bene così: parity-deriva sul PC si apre solo dal PC stesso.

**3 · what this server does**
- **this server's name**: `PC di casa`
- spunta **solo** **test: simulates, and takes the AI assistants' drafts**
  (niente archive, niente trade).

**4 · the other servers**
1. **the Archive's MCP address**: `https://priv.progettazionisoftware.it/parity-dev/mcp`
2. **a "pc" token made on the Archive**: incolla il token del passo 1
   (dal file `token-parity.txt`).
3. Clicca **connect**. Deve comparire una riga così:
   `Connected. The Archive gives: 3 instruments · 80 calendar events · … strategies and … indicators`
   Se compare un errore rosso, vedi "Se qualcosa va storto".
4. **candles** e **calendar**: lascia **from the Archive**.

**5 · finish** — clicca **finish setup**.
- Compare **Taken from the Archive:** con l'elenco di cosa ha preso: spread set,
  strategie, indicatori.
- Poi `Restarting…`, e dopo poco **Ready. open the app**. Clicca **open the app**.

---

## Passo 5 — Abilita le strategie

Le strategie arrivano come **bozze**: non girano finché non le abiliti. È una
regola di sicurezza.

1. Nell'app clicca **settings** → scheda **AI assistants**.
2. Nell'elenco delle strategie, per ognuna: **enable** → **yes**.

Fatto. Le candele arrivano dall'Archivio in automatico: le prime subito, poi
ogni ora.

---

## Tutti i giorni

- **Aprire parity-deriva:** nel browser, `http://localhost:8731`
  (mettilo nei preferiti).
- **Accensione:** parte da solo quando parte Docker Desktop. Docker Desktop
  parte con Windows (è l'impostazione normale).
- **Aggiornare:** doppio clic su
  `C:\Users\<il tuo nome>\parity-deriva\app\docker\install-pc.cmd`.
  Scarica la versione nuova e riparte sugli stessi dati.
  La cartella estratta dallo zip del passo 2 ora puoi cancellarla.
- **Spegnere:** apri Docker Desktop → **Containers** → sulla riga `parity`
  clicca ⏹ (stop). Per riaccendere: ▶.
- **Dove sono le cose:** tutto in `C:\Users\<il tuo nome>\parity-deriva`
  - `app` — il programma (si riscarica quando vuoi)
  - `data` — **i tuoi dati**: simulazioni, impostazioni, token. È la cartella
    da copiare per il backup. Copiala a parity-deriva spento.

---

## Se qualcosa va storto

| Vedi questo | Cosa fai |
|---|---|
| `winget is missing` / `winget` non trovato | Apri **Microsoft Store**, cerca **Programma di installazione app** (*App Installer*), installalo o aggiornalo. Poi rifai il passo 3. |
| Errore su WSL con "virtualizzazione" / "virtualization" | Va accesa la virtualizzazione nel BIOS del PC (Intel VT-x o AMD SVM). Cerca in internet "attivare virtualizzazione BIOS" + il modello del tuo PC. Poi rifai il passo 3. |
| `Docker Desktop does not answer` | Esci da Windows e rientra (o riavvia). Apri **Docker Desktop** e aspetta che in basso a sinistra dica **Engine running**. Poi rifai il passo 3. |
| `git clone failed` | Il login a GitHub non è andato, o il tuo account non vede il repository. Chiedi l'accesso a `rrambaldi/parity-deriva`, poi rifai il passo 3. |
| `git pull failed` (quando aggiorni) | Qualcuno ha modificato i file in `parity-deriva\app`. Non toccarli: chiedi aiuto. |
| `parity-deriva did not answer on http://localhost:8731` | Apri Docker Desktop → **Containers** → `parity` → **Logs**: l'errore è lì. Mandalo a chi ti aiuta. |
| La pagina setup dice che il codice non è giusto | Il codice cambia a ogni avvio. Rifai il passo 3: riapre la pagina col codice nuovo. |
| **connect** dà errore rosso | L'indirizzo deve finire con `/mcp`. Il token deve essere quello col ruolo **pc**. Se non sei sicuro, rifai il passo 1 (un token nuovo con lo stesso nome sostituisce il vecchio). |
| La finestra nera si chiude subito | Apri il menu Start, scrivi `powershell`, apri **Windows PowerShell** e incolla: `powershell -ExecutionPolicy Bypass -File "$HOME\Downloads\parity-deriva-dev\parity-deriva-dev\docker\pc.ps1"`. Così l'errore resta a video. |

---

## Togliere tutto

1. Docker Desktop → **Containers** → riga `parity` → 🗑 (delete).
2. Docker Desktop → **Images** → `parity-deriva` → 🗑.
3. Cancella la cartella `C:\Users\<il tuo nome>\parity-deriva`.
   **Attenzione:** dentro `data` ci sono i tuoi dati. Se ti servono, copiali
   prima.
4. Sull'Archivio: **settings** → **AI assistants** → sulla riga "PC di casa"
   togli il token.

---

*Per chi sviluppa: lo script è `docker/pc.ps1` (lo lancia `docker/install-pc.cmd`).
Accetta `-Root <cartella>` e `-Branch <branch>`. Scrive un
`docker-compose.override.yml` che monta `data` come `/data` del container.*
