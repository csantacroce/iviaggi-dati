#!/usr/bin/env python3
"""Controllo di salute dei dati dell'app (aggiunto il 14/11/2026).

Gira su GitHub alla fine di ogni pubblicazione e verifica che tutto funzioni come impostato:
  1. il database si è costruito senza errori;
  2. i prezzi arrivano dall'API Osservaprezzi del Ministero (non da una fonte di riserva),
     per quasi tutti gli impianti, e sono di oggi o di ieri;
  3. i cambi della BCE non sono troppo vecchi;
  4. i numeri del database sono plausibili (nessun foglio svuotato per errore);
  5. il sito pubblicato (quello che scarica l'app) ha davvero l'ultima versione, e il file si apre.

Scrive l'elenco dei problemi in italiano in controllo_problemi.md (vuoto = tutto a posto):
il flusso di GitHub lo usa per aprire, aggiornare o chiudere la segnalazione "allarme-dati".

Uso:  python3 tools/controllo.py [--senza-sito]
"""
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")
SITO = "https://csantacroce.github.io/iviaggi-dati/data/"
USCITA = os.path.join(ROOT, "controllo_problemi.md")

MIN_QUOTA_IMPIANTI = 0.90      # impianti letti dall'API e impianti con prezzi: almeno il 90 %
MAX_GIORNI_PREZZI = 1          # prezzi di oggi o di ieri
MAX_GIORNI_CAMBI = 10          # la BCE si aggiorna una volta alla settimana
MINIMI = {"aree_servizio_attive": 450, "prezzi": 1800, "impianti_ministero": 500, "tratte": 40}


def leggi_json(nome):
    try:
        with open(os.path.join(DATA, nome), encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:      # noqa: BLE001
        return {"_errore": str(e)}


def giorni_fa(data_iso):
    return (dt.datetime.now(dt.timezone.utc).date() - dt.date.fromisoformat(str(data_iso)[:10])).days


def controlla_dati(problemi):
    st = leggi_json("status.json")
    if "_errore" in st:
        problemi.append(f"Non riesco a leggere data/status.json ({st['_errore']}).")
        return
    if not st.get("ok"):
        err = "; ".join(st.get("errors") or []) or st.get("message", "")
        problemi.append(f"**La costruzione del database è fallita**: {err}. I dati pubblicati restano quelli della versione precedente.")

    # ultimo tentativo di aggiornamento di prezzi e cambi
    ag = leggi_json("aggiornamento_settimanale.json")
    if "_errore" not in ag:
        riga_prezzi = next((x for x in ag.get("ok") or [] if x.startswith("prezzi:")), None)
        if riga_prezzi:
            if "API Osservaprezzi" not in riga_prezzi:
                fonte = re.search(r"fonte: ([^)]*)", riga_prezzi)
                problemi.append("**I prezzi non arrivano più dall'API Osservaprezzi** del Ministero ma da una fonte di riserva "
                                f"({fonte.group(1) if fonte else 'sconosciuta'}): l'API potrebbe essere cambiata o bloccata. "
                                "Dettagli del tentativo: " + "; ".join(ag.get("problemi") or ["nessuno"]))
            m = re.search(r"(\d+) impianti letti, (\d+) non letti", riga_prezzi)
            if m:
                letti, non_letti = int(m.group(1)), int(m.group(2))
                if letti < MIN_QUOTA_IMPIANTI * (letti + non_letti):
                    problemi.append(f"L'API Osservaprezzi ha risposto solo per {letti} impianti su {letti + non_letti}: "
                                    "forse limita le richieste o qualche codice impianto è cambiato.")
            m = re.search(r"da (\d+)/(\d+) impianti", riga_prezzi)
            if m and int(m.group(1)) < MIN_QUOTA_IMPIANTI * int(m.group(2)):
                problemi.append(f"Prezzi trovati solo per {m.group(1)} impianti su {m.group(2)} abbinati alle aree.")
        elif any("prezzi" in x for x in ag.get("problemi") or []):
            problemi.append("**I prezzi non si sono aggiornati** all'ultimo tentativo: " + "; ".join(ag.get("problemi")))

    # contenuto del database pubblicato
    try:
        db = sqlite3.connect(os.path.join(DATA, "iviaggi_database.sqlite"))
        info = dict(db.execute("SELECT chiave, valore FROM info_aggiornamento").fetchall())
    except Exception as e:      # noqa: BLE001
        problemi.append(f"Il database data/iviaggi_database.sqlite non si apre ({e}).")
        return
    try:
        g = giorni_fa(info.get("data_estrazione"))
        if g > MAX_GIORNI_PREZZI:
            problemi.append(f"**I prezzi nel database sono vecchi di {g} giorni** (estrazione del {info.get('data_estrazione')}).")
    except Exception:           # noqa: BLE001
        problemi.append("Nel database manca la data dei prezzi (data_estrazione).")
    try:
        g = giorni_fa(info.get("data_cambi"))
        if g > MAX_GIORNI_CAMBI:
            problemi.append(f"I cambi delle valute sono vecchi di {g} giorni (BCE del {info.get('data_cambi')}).")
    except Exception:           # noqa: BLE001
        problemi.append("Nel database manca la data dei cambi (data_cambi).")
    conteggi = st.get("counts") or {}
    for nome, minimo in MINIMI.items():
        if nome in conteggi and conteggi[nome] < minimo:
            problemi.append(f"Numero sospetto di righe in «{nome}»: {conteggi[nome]} (di solito almeno {minimo}).")


def scarica(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "iVIAGGI-controllo/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def controlla_sito(problemi, tentativi=8, attesa=45):
    """Il sito di GitHub Pages può metterci qualche minuto ad aggiornarsi (e ha una cache di 10 minuti):
    riprova finché vede la stessa versione dell'archivio."""
    locale = leggi_json("manifest.json")
    if "_errore" in locale:
        problemi.append(f"Non riesco a leggere data/manifest.json ({locale['_errore']}).")
        return
    ultimo = None
    for n in range(tentativi):
        try:
            online = json.loads(scarica(f"{SITO}manifest.json?c={int(time.time())}"))
            if online.get("version") == locale.get("version") and online.get("sha256") == locale.get("sha256"):
                raw = scarica(f"{SITO}{online.get('file', 'iviaggi_database.sqlite')}?c={int(time.time())}", timeout=120)
                if hashlib.sha256(raw).hexdigest() != online.get("sha256"):
                    ultimo = "il file del database scaricato dal sito non corrisponde al manifest"
                else:
                    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
                        f.write(raw)
                    ok = sqlite3.connect(f.name).execute("PRAGMA integrity_check").fetchone()[0]
                    os.unlink(f.name)
                    if ok == "ok":
                        return
                    ultimo = f"il database scaricato dal sito è danneggiato ({ok})"
            else:
                ultimo = f"il sito mostra la versione {online.get('version')}, l'archivio ha la {locale.get('version')}"
        except Exception as e:  # noqa: BLE001
            ultimo = f"il sito non risponde ({e})"
        time.sleep(attesa)
    problemi.append(f"**Il sito da cui l'app scarica i dati non è allineato**: {ultimo}. "
                    "Gli utenti continuano a usare i dati che hanno già.")


def main():
    problemi = []
    controlla_dati(problemi)
    if "--senza-sito" not in sys.argv:
        controlla_sito(problemi)
    with open(USCITA, "w", encoding="utf-8") as f:
        f.write("\n".join(f"- {p}" for p in problemi))
    if problemi:
        print("PROBLEMI:")
        for p in problemi:
            print(" -", p)
    else:
        print("Tutto a posto.")


if __name__ == "__main__":
    main()
