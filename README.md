# Dati dell'app (iVIAGGI)

Archivio che pubblica il database dell'app. Guida completa: `GUIDA.md`.

- `source/dati.xlsx`: l'Excel da modificare. `source/schema.sql`: struttura del database (non toccare).
- `tools/build.py`: controlla l'Excel, costruisce il database e `data/manifest.json` (versione, impronta SHA-256). `tools/export_xlsx.py`: ricrea l'Excel dal database.
- `data/`: ultima versione pubblicata (scritta in automatico, non modificare a mano). `data/status.json`: esito dell'ultimo controllo.
- `site/`: pagine pubbliche (assistenza, privacy IT/EN) e `admin/` (pagina di caricamento dell'Excel).
- `.github/workflows/publish.yml`: a ogni nuovo `dati.xlsx` controlla, costruisce, pubblica.

Struttura dei dati, `schema` 1: deve coincidere con `Config.supportedDataSchema` nell'app. Se cambi le tabelle, serve una nuova versione dell'app.

## Serbatoi (tabella `veicoli`, colonne `serbatoio`, `serbatoio_gpl`, `fonte_serbatoio`)

- `source/serbatoi.csv`: valori presi a mano, uno per riga, ognuno con il link alla fonte (schede e listini delle case, prove e schede di singoli modelli). Hanno la precedenza.
- [OwnerSpecs](https://ownerspecs.com), licenza [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/): capacità per generazione, dove manca un valore preso a mano. I valori del serbatoio che ne derivano restano sotto la stessa licenza CC BY-SA 4.0 (fonte in `fonte_serbatoio`). Cache mensile in `source/serbatoi_ownerspecs.json`; per rileggerla subito crea il file `source/aggiorna_serbatoi`.
- Non si copiano in blocco banche dati di terzi che non lo permettono (es. Quattroruote, cars-data.com).
