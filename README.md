# Dati dell'app (iVIAGGI)

Archivio che pubblica il database dell'app. Guida completa: `GUIDA.md`.

- `source/dati.xlsx`: l'Excel da modificare. `source/schema.sql`: struttura del database (non toccare).
- `tools/build.py`: controlla l'Excel, costruisce il database e `data/manifest.json` (versione, impronta SHA-256). `tools/export_xlsx.py`: ricrea l'Excel dal database.
- `data/`: ultima versione pubblicata (scritta in automatico, non modificare a mano). `data/status.json`: esito dell'ultimo controllo.
- `site/`: pagine pubbliche (assistenza, privacy IT/EN) e `admin/` (pagina di caricamento dell'Excel).
- `.github/workflows/publish.yml`: a ogni nuovo `dati.xlsx` controlla, costruisce, pubblica.

Struttura dei dati, `schema` 1: deve coincidere con `Config.supportedDataSchema` nell'app. Se cambi le tabelle, serve una nuova versione dell'app.
