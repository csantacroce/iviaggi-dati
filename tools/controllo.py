#!/usr/bin/env python3
"""Controllo di salute dei dati dell'app (aggiunto il 14/11/2026).

Gira su GitHub alla fine di ogni pubblicazione e verifica che tutto funzioni come impostato:
  1. il database si è costruito senza errori;
  2. i prezzi arrivano dall'API Osservaprezzi del Ministero (non da una fonte di riserva),
     per quasi tutti gli impianti, e sono di oggi o di ieri;
  3. i cambi della BCE non sono troppo vecchi;
  4. i numeri del database sono plausibili (nessun foglio svuotato per errore);
  5. il sito pubblicato (quello che scarica l'app) ha davvero l'ultima versione, e il file si apre.

Scrive in italiano semplice (dal 25/11/2026, richiesta di Carlo: "un linguaggio semplice"):
  controllo_problemi.md   elenco breve dei problemi (vuoto = tutto a posto)
  controllo_titolo.txt    titolo della segnalazione (oggetto dell'email): il problema principale
  controllo_corpo.md      testo dell'email: per ogni problema cosa è successo, cosa vede chi usa l'app,
                          cosa deve fare Carlo; i dettagli tecnici in fondo, chiusi
  controllo_chiusura.md   quando è tutto a posto: commento di chiusura (cosa era successo, quanto è durato,
                          com'è adesso), usato se una segnalazione era aperta
Il flusso di GitHub li usa per aprire, aggiornare o chiudere la segnalazione "allarme-dati".
Variabili d'ambiente lette: RUN_URL, ESITO_BUILD, ESITO_DEPLOY, PROVA_ALLARME, APERTA_DAL, APERTA_TITOLO.

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


MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
        "settembre", "ottobre", "novembre", "dicembre"]


def data_it(data_iso):
    """'2026-11-17' -> '17 novembre'."""
    try:
        d = dt.date.fromisoformat(str(data_iso)[:10])
        return f"{d.day} {MESI[d.month - 1]}"
    except Exception:           # noqa: BLE001
        return str(data_iso)


def ora_it(quando):
    """datetime UTC -> '24 novembre alle 18:35' (ora italiana)."""
    try:
        from zoneinfo import ZoneInfo
        q = quando.astimezone(ZoneInfo("Europe/Rome"))
    except Exception:           # noqa: BLE001
        q = quando
    return f"{q.day} {MESI[q.month - 1]} alle {q:%H:%M}"


def durata_it(secondi):
    ore = secondi / 3600
    if ore < 1:
        return "meno di un'ora"
    if ore < 36:
        n = round(ore)
        return "circa un'ora" if n == 1 else f"circa {n} ore"
    n = round(ore / 24)
    return f"circa {n} giorni"


def problema(problemi, codice, titolo, cosa, app, fare, tecnico=""):
    """Un problema spiegato in parole semplici. `codice` serve a riconoscere lo stesso problema nei giorni dopo."""
    problemi.append({"codice": codice, "titolo": titolo, "cosa": cosa, "app": app, "fare": fare, "tecnico": tecnico})


def controlla_dati(problemi, stato):
    st = leggi_json("status.json")
    if "_errore" in st:
        problema(problemi, "status", "manca l'esito della costruzione del database",
                 "Il file in cui il programma scrive com'è andata la costruzione del database non si legge.",
                 "Probabilmente nessuno: l'app usa l'ultimo database pubblicato.",
                 "Se si ripete domani, chiedi a Claude di guardare.", st["_errore"])
        return
    stato["versione"] = st.get("version")
    if not st.get("ok"):
        err = "; ".join(st.get("errors") or []) or st.get("message", "")
        problema(problemi, "build", "il database di oggi non si è costruito",
                 "Il programma che ogni giorno prepara il database dell'app si è fermato con un errore, "
                 "quindi oggi non è uscita una versione nuova.",
                 "L'app continua a usare l'ultima versione buona del database: funziona, ma senza le novità di oggi.",
                 "Chiedi a Claude di guardare l'errore (è nei dettagli tecnici qui sotto).", err)

    # ultimo tentativo di aggiornamento di prezzi e cambi
    ag = leggi_json("aggiornamento_settimanale.json")
    dettagli_ag = "; ".join(ag.get("problemi") or []) if "_errore" not in ag else ag["_errore"]
    if "_errore" not in ag:
        riga_prezzi = next((x for x in ag.get("ok") or [] if x.startswith("prezzi:")), None)
        scartata = any("più vecchi di quelli già nel database" in x for x in ag.get("problemi") or [])
        ministero_giu = any("Osservaprezzi non raggiungibile" in x for x in ag.get("problemi") or [])
        if (riga_prezzi and "API Osservaprezzi" not in riga_prezzi) or (ministero_giu and not riga_prezzi):
            m = re.search(r"estrazione (\d{4}-\d{2}-\d{2})", riga_prezzi or "")
            if scartata or not riga_prezzi:
                copia = ("Abbiamo provato una copia degli stessi dati pubblicata da un altro sito (OpenDataView), ma era "
                         "più vecchia dei prezzi che avevamo già, quindi è stata scartata." if scartata else
                         "Non è servita nemmeno la copia di riserva pubblicata da un altro sito (OpenDataView).")
            else:
                copia = ("Al suo posto è stata usata una copia degli stessi dati pubblicata da un altro sito (OpenDataView), "
                         f"che però è in ritardo: ha i prezzi del {data_it(m.group(1)) if m else '?'}.")
            problema(problemi, "prezzi_fonte", "il sito dei prezzi del Ministero non risponde",
                     "Ogni giorno i prezzi dei carburanti arrivano dal sito Osservaprezzi del Ministero. Oggi non ha "
                     "risposto, e nemmeno il file di riserva del Ministero. " + copia,
                     "L'app mostra i prezzi dell'ultimo aggiornamento riuscito (vedi più sotto la data). Tutto il resto funziona.",
                     "Niente: di solito il sito del Ministero torna a rispondere entro un giorno, e l'aggiornamento riprova da solo "
                     "ogni giorno. Se dura più di 2-3 giorni, chiedi a Claude di controllare se il Ministero ha cambiato qualcosa.",
                     dettagli_ag)
        elif riga_prezzi:
            m = re.search(r"(\d+) impianti letti, (\d+) non letti", riga_prezzi)
            if m:
                letti, non_letti = int(m.group(1)), int(m.group(2))
                if letti < MIN_QUOTA_IMPIANTI * (letti + non_letti):
                    problema(problemi, "prezzi_parziali", "il sito dei prezzi ha risposto solo in parte",
                             f"Il sito del Ministero ha dato i prezzi solo per {letti} distributori su {letti + non_letti}, "
                             "anche dopo averlo riprovato più volte.",
                             f"Per {non_letti} distributori l'app mostra i prezzi dell'ultimo aggiornamento riuscito.",
                             "Niente se succede una volta sola. Se si ripete per giorni, chiedi a Claude di controllare.",
                             riga_prezzi)
        elif any("prezzi" in x for x in ag.get("problemi") or []):
            problema(problemi, "prezzi_errore", "i prezzi di oggi non si sono aggiornati",
                     "L'aggiornamento dei prezzi dei carburanti si è fermato con un errore.",
                     "L'app mostra i prezzi dell'ultimo aggiornamento riuscito.",
                     "Niente se succede una volta sola. Se si ripete, chiedi a Claude di guardare.", dettagli_ag)

    # contenuto del database pubblicato
    try:
        db = sqlite3.connect(os.path.join(DATA, "iviaggi_database.sqlite"))
        info = dict(db.execute("SELECT chiave, valore FROM info_aggiornamento").fetchall())
    except Exception as e:      # noqa: BLE001
        problema(problemi, "db", "il database pubblicato non si apre",
                 "Il file del database preparato oggi risulta danneggiato o illeggibile.",
                 "Se è già sul sito, l'app lo scarta (controlla il file prima di usarlo) e tiene quello che ha.",
                 "Chiedi a Claude di guardare.", str(e))
        return
    stato["data_prezzi"] = info.get("data_estrazione")
    try:
        g = giorni_fa(info.get("data_estrazione"))
        if g > MAX_GIORNI_PREZZI:
            gia = any(p["codice"].startswith("prezzi_") for p in problemi)
            problema(problemi, "prezzi_vecchi", f"i prezzi nell'app sono di {g} giorni fa",
                     f"Nel database pubblicato i prezzi dei carburanti sono quelli del {data_it(info.get('data_estrazione'))}."
                     + (" È la conseguenza del problema qui sopra." if gia else ""),
                     f"Chi usa l'app vede i prezzi del {data_it(info.get('data_estrazione'))}: di solito cambiano di pochi centesimi.",
                     "Niente: appena il sito del Ministero risponde, i prezzi tornano di oggi." if gia else
                     "Chiedi a Claude di guardare perché l'aggiornamento non è partito.",
                     f"data_estrazione = {info.get('data_estrazione')}")
    except Exception:           # noqa: BLE001
        problema(problemi, "prezzi_data", "manca la data dei prezzi",
                 "Nel database non c'è la data dei prezzi dei carburanti.", "L'app non sa dire di quando sono i prezzi.",
                 "Chiedi a Claude di guardare.")
    try:
        g = giorni_fa(info.get("data_cambi"))
        if g > MAX_GIORNI_CAMBI:
            problema(problemi, "cambi", f"i cambi delle valute sono fermi da {g} giorni",
                     f"I cambi delle valute della Banca centrale europea non si aggiornano dal {data_it(info.get('data_cambi'))}.",
                     "Per chi imposta una valuta diversa dall'euro i costi possono essere un po' imprecisi.",
                     "Niente se dura poco. Se dura più di due settimane, chiedi a Claude di controllare.",
                     f"data_cambi = {info.get('data_cambi')}; {dettagli_ag}")
    except Exception:           # noqa: BLE001
        problema(problemi, "cambi_data", "manca la data dei cambi", "Nel database non c'è la data dei cambi delle valute.",
                 "Nessuno di rilievo.", "Chiedi a Claude di guardare.")
    nomi = {"aree_servizio_attive": "aree di servizio attive", "prezzi": "prezzi dei carburanti",
            "impianti_ministero": "distributori abbinati alle aree", "tratte": "tratte autostradali"}
    conteggi = st.get("counts") or {}
    for nome, minimo in MINIMI.items():
        if nome in conteggi and conteggi[nome] < minimo:
            problema(problemi, f"conteggio_{nome}", f"nel database ci sono poche {nomi.get(nome, nome)}",
                     f"Nel database di oggi ci sono {conteggi[nome]} {nomi.get(nome, nome)}, di solito almeno {minimo}: "
                     "potrebbe essere un errore nei dati di partenza.",
                     "L'app potrebbe mostrare dati incompleti.",
                     "Chiedi a Claude di controllare subito.", f"{nome}: {conteggi[nome]} (minimo {minimo})")


def scarica(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "iVIAGGI-controllo/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def controlla_sito(problemi, tentativi=8, attesa=45):
    """Il sito di GitHub Pages può metterci qualche minuto ad aggiornarsi (e ha una cache di 10 minuti):
    riprova finché vede la stessa versione dell'archivio."""
    locale = leggi_json("manifest.json")
    if "_errore" in locale:
        problema(problemi, "manifest", "manca il file con la versione del database",
                 "Il file che dice all'app qual è l'ultima versione del database non si legge.",
                 "L'app non scarica aggiornamenti e usa i dati che ha.", "Chiedi a Claude di guardare.", locale["_errore"])
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
    problema(problemi, "sito", "il sito da cui l'app scarica i dati non è aggiornato",
             "Il database nuovo è pronto, ma il sito su GitHub da cui l'app lo scarica non lo mostra ancora "
             "(o non risponde).",
             "Gli utenti continuano a usare i dati del giorno prima.",
             "Niente: di solito si sistema da solo al giro successivo. Se dura più di un giorno, chiedi a Claude.", ultimo)


def esiti_github(problemi):
    """Esiti dei passaggi precedenti del flusso di GitHub (passati come variabili d'ambiente)."""
    if os.environ.get("PROVA_ALLARME") == "true":
        problema(problemi, "prova", "prova dell'allarme (nessun problema vero)",
                 "Hai avviato a mano la prova dell'allarme: questa email serve solo a vedere che le segnalazioni arrivano.",
                 "Niente di diverso dal solito.", "Niente: la segnalazione si chiude da sola al prossimo giro.")
    b, d = os.environ.get("ESITO_BUILD", "success"), os.environ.get("ESITO_DEPLOY", "success")
    if b not in ("success", ""):
        problema(problemi, "job_build", "il lavoro di aggiornamento su GitHub non è finito",
                 "Il passaggio che aggiorna prezzi e database si è interrotto prima della fine.",
                 "L'app usa l'ultimo database pubblicato.",
                 "Niente se succede una volta sola. Se si ripete, chiedi a Claude di guardare.", f"esito build: {b}")
    if d not in ("success", ""):
        problema(problemi, "job_deploy", "la pubblicazione del sito su GitHub non è finita",
                 "Il database è pronto ma la pubblicazione sul sito da cui lo scarica l'app si è interrotta.",
                 "Gli utenti continuano a usare i dati del giorno prima.",
                 "Niente se succede una volta sola. Se si ripete, chiedi a Claude di guardare.", f"esito deploy: {d}")


def scrivi_segnalazione(problemi, stato, run_url):
    """Titolo e testo della email (segnalazione GitHub), in parole semplici."""
    titolo = "⚠️ iVIAGGI: " + problemi[0]["titolo"]
    if len(problemi) > 1:
        titolo += f" (e altri {len(problemi) - 1} problemi)" if len(problemi) > 2 else " (e un altro problema)"
    r = ["**In breve:** " + "; ".join(p["titolo"] for p in problemi) + ".", ""]
    for n, p in enumerate(problemi, 1):
        r += [f"### {n}. {p['titolo'][0].upper()}{p['titolo'][1:]}", "",
              f"**Cosa è successo.** {p['cosa']}", "",
              f"**Cosa vede chi usa l'app.** {p['app']}", "",
              f"**Cosa devi fare.** {p['fare']}", ""]
    if stato.get("data_prezzi"):
        r += [f"Oggi l'app ha i prezzi del **{data_it(stato['data_prezzi'])}**"
              + (f" (database versione {stato['versione']})." if stato.get("versione") else "."), ""]
    r += ["Ti arriva un'altra email quando tutto torna a posto: la segnalazione si chiude da sola.", ""]
    tecnici = [f"- {p['titolo']}: `{p['tecnico']}`" for p in problemi if p.get("tecnico")]
    r += ["<details><summary>Dettagli tecnici (per Claude)</summary>", ""] + tecnici + \
         ["", f"Esito completo: {run_url}", "File: data/aggiornamento_settimanale.json, data/status.json", "</details>"]
    return titolo, "\n".join(r)


def scrivi_chiusura(stato, run_url):
    """Commento di chiusura: cosa era successo, quanto è durato, com'è la situazione adesso."""
    titolo_aperto = os.environ.get("APERTA_TITOLO", "").replace("⚠️ iVIAGGI: ", "").strip()
    r = ["✅ **Risolto" + (f": {titolo_aperto}" if titolo_aperto else "") + ".**", ""]
    aperta_dal = os.environ.get("APERTA_DAL")
    if aperta_dal:
        try:
            inizio = dt.datetime.fromisoformat(aperta_dal.replace("Z", "+00:00"))
            adesso = dt.datetime.now(dt.timezone.utc)
            r += [f"Il problema è durato {durata_it((adesso - inizio).total_seconds())} "
                  f"(dal {ora_it(inizio)} a oggi, {ora_it(adesso)}).", ""]
        except Exception:       # noqa: BLE001
            pass
    ag = leggi_json("aggiornamento_settimanale.json")
    riga = next((x for x in ag.get("ok") or [] if x.startswith("prezzi:")), "") if "_errore" not in ag else ""
    m = re.search(r"da (\d+)/(\d+) impianti", riga)
    adesso = []
    if stato.get("data_prezzi"):
        adesso.append(f"i prezzi nell'app sono del **{data_it(stato['data_prezzi'])}**"
                      + (f", per {m.group(1)} distributori su {m.group(2)}" if m else ""))
    if stato.get("versione"):
        adesso.append(f"il database versione {stato['versione']} è pubblicato e l'app lo scarica")
    if adesso:
        r += ["Adesso " + " e ".join(adesso) + ".", ""]
    r += ["Non devi fare niente.", "", f"<sub>Esito completo: {run_url}</sub>"]
    return "\n".join(r)


def main():
    problemi, stato = [], {}
    esiti_github(problemi)
    controlla_dati(problemi, stato)
    if "--senza-sito" not in sys.argv:
        controlla_sito(problemi)
    run_url = os.environ.get("RUN_URL", "")
    # firma: solo i codici dei problemi, per riconoscere lo stesso problema nei giorni dopo (niente email uguali)
    firma = hashlib.sha256(",".join(sorted(p["codice"] for p in problemi)).encode()).hexdigest()[:12]
    with open(USCITA, "w", encoding="utf-8") as f:
        f.write("\n".join(f"- {p['titolo']}" for p in problemi))
    if problemi:
        titolo, corpo = scrivi_segnalazione(problemi, stato, run_url)
        with open(os.path.join(ROOT, "controllo_titolo.txt"), "w", encoding="utf-8") as f:
            f.write(titolo)
        with open(os.path.join(ROOT, "controllo_corpo.md"), "w", encoding="utf-8") as f:
            f.write(corpo + f"\n\n<!-- firma: {firma} -->\n")
        print("PROBLEMI:")
        for p in problemi:
            print(" -", p["titolo"], "|", p.get("tecnico", ""))
    else:
        with open(os.path.join(ROOT, "controllo_chiusura.md"), "w", encoding="utf-8") as f:
            f.write(scrivi_chiusura(stato, run_url) + "\n")
        print("Tutto a posto.")


if __name__ == "__main__":
    main()
