#!/usr/bin/env python3
"""Capacità del serbatoio dei modelli (20/11/2026, Carlo).

Nessuna fonte aperta unica ha il serbatoio: si mettono insieme più fonti, ognuna annotata.
  1. source/serbatoi.csv: valori presi a mano dai siti ufficiali delle case (listini, schede, manuali) o da prove e
     schede di singoli modelli, ognuno con il link. Hanno sempre la precedenza.
  2. OwnerSpecs (21/11/2026, Carlo: ok), https://ownerspecs.com, licenza CC BY-SA 4.0 (citazione obbligatoria, i dati
     derivati restano CC BY-SA): campo fuel_tank_l per generazione, controllato dal sito su libretti, certificati di
     conformità e registri. Si usa solo la generazione ancora in produzione e, se ce ne sono più
     di una (carrozzerie diverse), il valore più basso. Controllo a campione del 21/11/2026: Octavia IV 40 l (scheda
     Škoda: 45) e Classe A 51 l (43 di serie, 51 a richiesta) sbagliati, corretti con righe a mano. Cache in source/serbatoi_ownerspecs.json, riletta una volta
     al mese o con source/aggiorna_serbatoi.
  (Wikipedia in inglese, provata il 20/11/2026: nelle schede il serbatoio non c'è più, 0 valori su 364 modelli.)
Nella tabella veicoli: colonne serbatoio (litri), serbatoio_gpl (litri, solo GPL) e fonte_serbatoio.
I dati mancanti restano NULL: l'app chiede il valore all'utente.
"""
import csv
import datetime as dt
import json
import os
import re
import unicodedata
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MANUALI = os.path.join(ROOT, "source", "serbatoi.csv")
CACHE = os.path.join(ROOT, "source", "serbatoi_ownerspecs.json")
TRIGGER = os.path.join(ROOT, "source", "aggiorna_serbatoi")
GIORNI = 30
AGENTE = "iVIAGGI-dati/1.0 (https://github.com/csantacroce/iviaggi-dati; app iVIAGGI Autostrade Italiane)"
OWNERSPECS = "https://ownerspecs.com/data/dataset.json"
ANNO_MINIMO = 2024      # (filtro di riserva) generazioni finite prima: fuori dal catalogo


def norm(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def nome_inglese(marca, modello):
    """"Serie 3" → "3 Series", "Classe C" → "C-Class" (nomi in inglese di OwnerSpecs)."""
    r = re.match(r"^Serie (\w+)$", modello)
    if r:
        return f"{marca} {r.group(1)} Series"
    r = re.match(r"^Classe (\w+)$", modello)
    if r:
        return f"{marca} {r.group(1)}-Class"
    return f"{marca} {modello}"


def aggiorna_cache(con=None):
    """Scarica il dataset di OwnerSpecs (una volta al mese) e ne tiene solo le colonne che servono."""
    if os.path.exists(CACHE) and not os.path.exists(TRIGGER):
        vecchio = json.load(open(CACHE, encoding="utf-8"))
        letto = dt.date.fromisoformat(vecchio.get("letto", "2000-01-01"))
        if (dt.date.today() - letto).days < GIORNI:
            return f"cache del {letto}, {len(vecchio.get('generazioni', []))} generazioni"
    req = urllib.request.Request(OWNERSPECS, headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(req, timeout=60) as r:
        dati = json.load(r)
    generazioni = []
    for g in dati.get("generations", []):
        litri = g.get("fuel_tank_l")
        if not isinstance(litri, (int, float)) or not 15 <= litri <= 150:
            continue
        generazioni.append({k: g.get(k) for k in ("brand", "model", "generation", "body_type", "year_start",
                                                  "year_end", "url")} | {"litri": float(litri)})
    uscita = {"letto": dt.date.today().isoformat(), "licenza": dati.get("license"),
              "citazione": dati.get("attribution"), "generazioni": generazioni}
    with open(CACHE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(uscita, f, ensure_ascii=False, indent=1)
    os.replace(CACHE + ".tmp", CACHE)
    try:
        os.remove(TRIGGER)
    except OSError:
        pass
    return f"{len(generazioni)} generazioni con il serbatoio"


def scelta_ownerspecs(generazioni, marca, modello):
    """Valore (litri, url) della generazione in produzione del modello; None se non c'è o non è sicuro."""
    chiave = norm(nome_inglese(marca, modello))
    attuali = [g for g in generazioni
               if norm(f"{g['brand']} {g['model']}") == chiave and (g.get("year_end") or 9999) >= ANNO_MINIMO]
    if not attuali:
        return None
    attuali = [g for g in attuali if not g.get("year_end")]   # solo la generazione in produzione (21/11/2026)
    if not attuali:                                # quella finita può essere già stata sostituita (BMW X3, Serie 5)
        return None
    g = min(attuali, key=lambda g: g["litri"])     # più carrozzerie: il valore più basso (autonomia prudente)
    return g["litri"], g.get("url") or "https://ownerspecs.com"


def costruisci(con):
    """Colonne serbatoio, serbatoio_gpl, fonte_serbatoio nella tabella veicoli."""
    for col, tipo in (("serbatoio", "REAL"), ("serbatoio_gpl", "REAL"), ("fonte_serbatoio", "TEXT")):
        con.execute(f"ALTER TABLE veicoli ADD COLUMN {col} {tipo}")
    # 2) OwnerSpecs (per modello; non GPL, metano e plug-in, che hanno serbatoi diversi dal valore della generazione)
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    generazioni = cache.get("generazioni", [])
    da_ownerspecs = 0
    for marca, modello in con.execute("SELECT DISTINCT marca, modello FROM veicoli").fetchall():
        v = scelta_ownerspecs(generazioni, marca, modello)
        if v:
            da_ownerspecs += 1
            con.execute("UPDATE veicoli SET serbatoio = ?, fonte_serbatoio = ? WHERE marca = ? AND modello = ? "
                        "AND alimentazione NOT IN ('Elettrica', 'GPL', 'Metano', 'Plug-in benzina', 'Plug-in gasolio')",
                        (v[0], "OwnerSpecs (CC BY-SA 4.0): " + v[1], marca, modello))
    # 1) valori presi a mano (precedenza), dal più generico al più specifico:
    #    riga senza alimentazione = tutto il modello tranne GPL, metano e plug-in (serbatoi diversi, servono righe proprie);
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
            dove += " AND alimentazione NOT IN ('GPL', 'Metano', 'Plug-in benzina', 'Plug-in gasolio')"
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
    print(f"Serbatoi: {con_dato} modelli su {tot} ({len(manuali)} valori presi a mano, {da_ownerspecs} modelli da OwnerSpecs)")
