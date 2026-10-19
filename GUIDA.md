# Guida: mettere online l'aggiornamento dei dati (GitHub, gratis)

Serve una volta sola, circa 30 minuti, da computer. Dopo, aggiornare i dati è: modificare l'Excel, aprire una pagina, premere un tasto.

## A. Preparare l'archivio su GitHub
1. Account GitHub: **csantacroce** (già creato). Entra su github.com con questo account.
2. Clicca **+ > New repository**. Nome: esattamente `iviaggi-dati` (l'app cerca i dati a `https://csantacroce.github.io/iviaggi-dati/`). Visibilità: **Public** (serve per ospitare gratis il sito). Spunta "Add a README"? No. Clicca **Create repository**.
3. Nella pagina del nuovo archivio clicca **uploading an existing file** e trascina dentro **tutto il contenuto** della cartella `iVIAGGI_archivio_github` (cartelle `site`, `source`, `tools`, `data`, file `README.md`, `GUIDA.md`). Premi **Commit changes**.
4. La cartella nascosta `.github` (contiene l'automatismo) spesso non si trascina. Fai così: **Add file > Create new file**, nel nome scrivi esattamente `.github/workflows/publish.yml` (scrivendo le barre `/` GitHub crea le cartelle), incolla il contenuto del file `publish.yml` che trovi in `iVIAGGI_archivio_github/.github/workflows/` (su Mac: Cmd+Maiusc+punto in Finder per vedere le cartelle nascoste; nello zip c'è anche una copia visibile `publish.yml`). Premi **Commit changes**.

## B. Attivare le impostazioni (Settings dell'archivio)
5. **Settings > Pages > Build and deployment > Source: GitHub Actions.**
6. **Settings > Actions > General > Workflow permissions: Read and write permissions** > Save.
7. Vai nella scheda **Actions**: dovresti vedere l'esecuzione "Pubblica i dati dell'app" (parte da sola dopo il caricamento; se non c'è, **Run workflow**). Quando è verde, il sito è online all'indirizzo `https://csantacroce.github.io/iviaggi-dati/`.
8. Prova: apri `https://csantacroce.github.io/iviaggi-dati/data/manifest.json` (deve mostrare `"version": 2`) e `.../privacy.html`.

## C. Il codice di accesso per la pagina di aggiornamento
9. GitHub > tua foto in alto a destra > **Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token.**
   - Nome: `pagina aggiornamento dati`. Scadenza: 1 anno (annotati la data: alla scadenza la pagina dirà "codice non valido" e se ne crea uno nuovo).
   - Repository access: **Only select repositories** > scegli `iviaggi-dati`.
   - Permissions > Repository permissions: **Contents: Read and write**, **Actions: Read-only**. Nient'altro.
   - Generate token e **copialo subito** (si vede una volta sola): inizia con `github_pat_`. Conservalo in un gestore di password.
10. Apri `https://csantacroce.github.io/iviaggi-dati/admin/` (da telefono o computer), incolla il codice, premi **Salva**. Sul telefono aggiungi la pagina alla schermata Home per averla a portata di mano.

## D. Collegare l'app
11. Già fatto nel progetto Xcode: in `Config.swift` `static let dataManifestURL: String? = "https://csantacroce.github.io/iviaggi-dati/data/manifest.json"`.
12. Il database dentro l'app e quello pubblicato (versione 2, con il km del cartello `km_cippo`) sono identici: `bundledDataVersion = 2` resta com'è. Quando pubblichi una nuova versione dell'app con un database più recente, aumenta `bundledDataVersion` fino all'ultima versione online (la vedi nella pagina di aggiornamento).

## E. Aggiornare i dati (ogni volta)
1. Apri la pagina di aggiornamento, premi **Scarica dati.xlsx** (parti sempre dall'ultima versione).
2. Modifica l'Excel (foglio LEGGIMI dentro il file spiega come).
3. Carica il file e premi **Controlla e pubblica**. Dopo circa 1 minuto vedi l'esito: pubblicato (con il riepilogo delle modifiche) oppure l'elenco degli errori da correggere. In caso di errore l'app continua a usare i dati vecchi.
4. Gli utenti ricevono i dati nuovi alla prossima apertura dell'app. Il sito può impiegare fino a circa 10 minuti per propagarsi.

## Se qualcosa va storto
- **Tornare a una versione precedente:** su GitHub, scheda del file `source/dati.xlsx` > History > scegli la versione > Raw/Download > ricaricala dalla pagina di aggiornamento.
- **"Il codice di accesso non è valido":** il token è scaduto o è stato incollato male: creane uno nuovo (punto 9).
- **Le Actions non partono:** controlla il punto 6 e che il file `.github/workflows/publish.yml` esista.
- **Calo di righe voluto** (cancelli oltre il 10% delle righe di una tabella): Actions > Pubblica i dati > Run workflow > spunta "allow_shrink".

## Cose da sapere
- L'archivio è pubblico, quindi anche l'Excel lo è: contiene gli stessi dati che l'app mostra, più due fogli di appunti interni (correzioni_log, da_verificare). Se vuoi toglierli, cancella i due fogli: sono facoltativi.
- Dominio tuo: quando lo avrai, **Settings > Pages > Custom domain** e un record DNS (`CNAME` verso `csantacroce.github.io`). Il vecchio indirizzo `github.io` continua a reindirizzare: lascialo attivo per le app già installate.
- Le condizioni di GitHub Pages escludono i siti con finalità commerciali. Per un'app gratuita che scarica due file va bene; se la parte a pagamento cresce, si sposta su un altro servizio (cambia solo l'indirizzo in Config.swift).
- Sicurezza: il codice di accesso dà accesso solo a questo archivio; chi lo ha può cambiare i dati dell'app. Non condividerlo; se lo perdi, revocalo in GitHub > Developer settings.

## Colonne dei km (foglio aree_servizio)
- `km`: progressivo **nel senso di marcia** della carreggiata (nella carreggiata verso l'inizio dell'autostrada è contato dall'altro capo). Serve all'app per calcolare le distanze: non va "corretto".
- `km_cippo`: il km **del cartello** (contato dall'inizio dell'autostrada), quello che l'app mostra ("Ti trovi al km …", km delle aree). Per una nuova area scrivi il km che leggi sul cartello. Se manca, la pubblicazione dà un avviso e l'app mostra `km`.

## Aggiornamento automatico settimanale (prezzi e cambi)
- Ogni mattina GitHub controlla e, **una volta alla settimana** (quando i dati hanno almeno 6 giorni), scarica da solo i prezzi dei carburanti del Ministero (Osservaprezzi, open data IODL 2.0) e i cambi della BCE, aggiorna i fogli `prezzi` e `tassi_cambio` dell'Excel e pubblica una nuova versione dei dati se qualcosa è cambiato. Non devi fare nulla.
- Si aggiornano solo i prezzi degli impianti già abbinati alle aree nel foglio `impianti_ministero`: le tue correzioni negli altri fogli non vengono toccate. Per abbinare un impianto nuovo aggiungi una riga in `impianti_ministero` (id_area e id_impianto del Ministero).
- Se il Ministero o la BCE non rispondono, quella parte resta com'era e GitHub riprova la mattina dopo. L'esito dell'ultimo tentativo è in `data/aggiornamento_settimanale.json`.
- **Se il Ministero non risponde a GitHub** (succede: blocca alcuni server esteri), dalla pagina di aggiornamento, sezione 4: scarica `prezzo_alle_8.csv` con il telefono e caricalo con «Carica i prezzi». GitHub lo usa, aggiorna e pubblica in 2–3 minuti.
- Per lanciarlo subito: scheda **Actions → Pubblica i dati dell'app → Run workflow**, spunta «Scarica adesso prezzi…».
- Prima di modificare l'Excel scarica sempre l'ultima versione dalla pagina di aggiornamento: il lunedì GitHub la aggiorna.
