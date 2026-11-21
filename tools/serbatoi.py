#!/usr/bin/env python3
"""Capacità del serbatoio dei modelli (20/11/2026, Carlo).

Nessuna fonte aperta unica ha il serbatoio: si mettono insieme più fonti, ognuna annotata.
  1. source/serbatoi.csv: valori presi a mano dai siti ufficiali delle case (listini, schede, manuali) o da altre
     fonti verificate, con il link. Hanno sempre la precedenza.
  2. Wikipedia (inglese), licenza CC BY-SA: parametro "fuel_capacity" della scheda (infobox) della generazione più
     recente del modello. Cache in source/serbatoi_wikipedia.json, riletta una volta al mese o con
     source/aggiorna_serbatoi.
Nella tabella veicoli: colonne serbatoio (litri), serbatoio_gpl (litri, solo GPL) e fonte_serbatoio.
I dati mancanti restano NULL: l'app chiede il valore all'utente.
"""
import csv
import datetime as dt
import json
import os
import re
import time
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MANUALI = os.path.join(ROOT, "source", "serbatoi.csv")
CACHE = os.path.join(ROOT, "source", "serbatoi_wikipedia.json")
TRIGGER = os.path.join(ROOT, "source", "aggiorna_serbatoi")
GIORNI = 30
AGENTE = "iVIAGGI-dati/1.0 (https://github.com/csantacroce/iviaggi-dati; app iVIAGGI Autostrade Italiane)"
API = "https://en.wikipedia.org/w/api.php"


def leggi(params):
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(req, timeout=40) as r:
        dati = json.load(r)
    time.sleep(0.2)
    return dati


def norm(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def nome_inglese(marca, modello):
    """"Serie 3" → "3 Series", "Classe C" → "C-Class" (titoli di Wikipedia in inglese)."""
    r = re.match(r"^Serie (\w+)$", modello)
    if r:
        return f"{marca} {r.group(1)} Series"
    r = re.match(r"^Classe (\w+)$", modello)
    if r:
        return f"{marca} {r.group(1)}-Class"
    return f"{marca} {modello}"


def litri(testo):
    """Litri da "{{convert|36|L|...}}", "{{cvt|50|L}}", "{{convert|40|-|50|L}}" (il primo), "{{convert|11.9|USgal|L}}",
    "36 L", "45 litres". None se non si capisce o se il valore è fuori misura."""
    t = re.sub(r"<ref[^>]*>.*?</ref>|<ref[^/>]*/>", "", testo, flags=re.S)
    unita = {"l": 1, "litre": 1, "litres": 1, "liter": 1, "liters": 1, "usgal": 3.785, "us gal": 3.785,
             "impgal": 4.546, "imp gal": 4.546}
    r = re.search(r"\{\{\s*(?:convert|cvt)\s*\|\s*([\d.]+)\s*(?:\|\s*(?:-|–|to|and|or)\s*\|\s*[\d.]+\s*)?\|\s*"
                  r"(l|L|litres?|liters?|USgal|US gal|impgal|imp gal)\b", t)
    if not r:
        r = re.search(r"([\d.]+)\s*(?:[-–]\s*[\d.]+\s*)?(l|L|litres?|liters?)\b", t)
    if not r:
        return None
    try:
        v = round(float(r.group(1)) * unita[r.group(2).lower()], 1)
    except (ValueError, KeyError):
        return None
    return v if 15 <= v <= 150 else None


def infobox(wikitext):
    """(capacità, anno di inizio produzione) per ogni scheda della pagina."""
    schede = []
    for blocco in re.split(r"\{\{\s*Infobox automobile", wikitext, flags=re.I)[1:]:
        cap = re.search(r"\|\s*fuel_capacity\s*=\s*([^\n]+)", blocco)
        prod = re.search(r"\|\s*production\s*=\s*([^\n]+)", blocco)
        anni = [int(a) for a in re.findall(r"\b(19[5-9]\d|20[0-3]\d)\b", prod.group(1))] if prod else []
        if cap:
            v = litri(cap.group(1))
            if v:
                schede.append((v, min(anni) if anni else 0, cap.group(1).strip()[:80]))
    return schede


def cerca_wikipedia(marca, modello):
    """Valore della generazione più recente trovata nelle pagine del modello (pagina principale e generazioni)."""
    nome = nome_inglese(marca, modello)
    risultati = leggi({"action": "query", "list": "search", "srsearch": nome, "srlimit": 6, "srnamespace": 0})
    titoli = [r["title"] for r in risultati.get("query", {}).get("search", [])]
    chiave = norm(nome)
    titoli = [t for t in titoli if norm(t).startswith(chiave) or chiave in norm(t)]
    migliori = []
    for t in titoli[:4]:
        p = leggi({"action": "parse", "page": t, "prop": "wikitext", "redirects": 1})
        wt = p.get("parse", {}).get("wikitext", {}).get("*", "")
        for v, anno, grezzo in infobox(wt):
            migliori.append({"litri": v, "anno": anno, "pagina": t, "testo": grezzo})
    if not migliori:
        return None
    migliori.sort(key=lambda m: -m["anno"])
    scelta = migliori[0]
    if scelta["anno"] and scelta["anno"] < 2012:     # solo generazioni vecchie: meglio niente
        return None
    scelta["url"] = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(scelta["pagina"].replace(" ", "_"))
    return scelta


def aggiorna_cache(con):
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    forza = os.path.exists(TRIGGER)
    oggi = dt.date.today()
    modelli = con.execute("SELECT DISTINCT marca, modello FROM veicoli WHERE alimentazione <> 'Elettrica'").fetchall()
    nuovi = errori = 0
    for marca, modello in modelli:
        k = f"{marca}|{modello}"
        v = cache.get(k)
        if v and not forza and (oggi - dt.date.fromisoformat(v.get("provato", "2000-01-01"))).days < GIORNI:
            continue
        try:
            r = cerca_wikipedia(marca, modello)
            cache[k] = {**(r or {}), "provato": oggi.isoformat()}
            nuovi += 1
        except Exception as e:
            errori += 1
            print(f"Serbatoio {k}: {e}")
            if errori >= 15:
                break
    if forza:
        try:
            os.remove(TRIGGER)
        except OSError:
            pass
    with open(CACHE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(CACHE + ".tmp", CACHE)
    return nuovi, errori


def costruisci(con):
    """Colonne serbatoio, serbatoio_gpl, fonte_serbatoio nella tabella veicoli."""
    for col, tipo in (("serbatoio", "REAL"), ("serbatoio_gpl", "REAL"), ("fonte_serbatoio", "TEXT")):
        con.execute(f"ALTER TABLE veicoli ADD COLUMN {col} {tipo}")
    # 2) Wikipedia (per modello, tutte le alimentazioni con motore termico)
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    for k, v in cache.items():
        if v.get("litri"):
            marca, modello = k.split("|", 1)
            con.execute("UPDATE veicoli SET serbatoio = ?, fonte_serbatoio = ? WHERE marca = ? AND modello = ? "
                        "AND alimentazione <> 'Elettrica'", (v["litri"], "Wikipedia: " + v["url"], marca, modello))
    # 1) valori presi a mano (precedenza), dal più generico al più specifico:
    #    riga senza alimentazione = tutto il modello tranne GPL e metano (serbatoi diversi, servono righe proprie);
    #    poi le righe con alimentazione; per ultime quelle limitate per potenza (colonne kw_da / kw_a, estremi inclusi)
    manuali = list(csv.DictReader(open(MANUALI, encoding="utf-8"))) if os.path.exists(MANUALI) else []

    def ordine(r):
        return (bool(r["alimentazione"]), bool((r.get("kw_da") or "") + (r.get("kw_a") or "")))

    for r in sorted(manuali, key=ordine):
        dove, par = "marca = ? AND modello = ? AND alimentazione <> 'Elettrica'", [r["marca"], r["modello"]]
        if r["alimentazione"]:
            dove += " AND alimentazione = ?"
            par.append(r["alimentazione"])
        else:
            dove += " AND alimentazione NOT IN ('GPL', 'Metano')"
        if r.get("kw_da"):
            dove += " AND kw >= ?"
            par.append(float(r["kw_da"]))
        if r.get("kw_a"):
            dove += " AND kw <= ?"
            par.append(float(r["kw_a"]))
        con.execute(f"UPDATE veicoli SET serbatoio = ?, serbatoio_gpl = ?, fonte_serbatoio = ? WHERE {dove}",
                    [float(r["litri"]), float(r["litri_gpl"]) if r["litri_gpl"] else None, r["fonte"]] + par)
    con.commit()
    tot = con.execute("SELECT COUNT(DISTINCT marca||modello) FROM veicoli WHERE alimentazione <> 'Elettrica'").fetchone()[0]
    con_dato = con.execute("SELECT COUNT(DISTINCT marca||modello) FROM veicoli WHERE serbatoio IS NOT NULL").fetchone()[0]
    print(f"Serbatoi: {con_dato} modelli su {tot} ({len(manuali)} valori presi a mano, ognuno con la fonte)")
