#!/usr/bin/env python3
"""Catalogo dei veicoli e consumi (20/11/2026) per le Impostazioni veicolo e le soste consigliate dell'app.

Fonti (aperte, gratuite, interrogate via API; nessuna pagina copiata):
  - ADEME Car Labelling (Francia), Licence Ouverte / Open Licence 2.0
    https://data.ademe.fr/datasets/ademe-car-labelling
    Versioni in vendita in Francia: marca, modello, descrizione commerciale, energia, cilindrata, potenza,
    cambio, consumi WLTP per fase (bassa, media, alta, extra alta, mista; min e max, l/100 km),
    consumo elettrico (Wh/km) e autonomia elettrica.
  - EEA, CO2 emissions from new passenger cars (UE), CC BY 4.0
    https://www.eea.europa.eu/en/datahub/datahubitem-view/fa8b1229-3db6-495d-b18e-9c9b3267c02b
    Auto immatricolate per Stato membro: serve a sapere quali versioni sono vendute in Italia (MS = IT).

Regole (requisiti di Carlo, documento «veicoli-catalogo-e-consumi»):
  - dati mancanti = NULL, mai inventati; ogni riga ha la sua fonte;
  - WLTP Extra High NON è una misura a 130 km/h: nell'app è "stima per l'autostrada";
  - per le ibride plug-in il consumo misto (ponderato) non vale per l'autostrada: non si salva;
  - ADEME ed EEA si uniscono solo con chiavi affidabili (marca, modello, alimentazione, potenza ±2 kW).

Cache in source/veicoli_ademe.json.gz e source/veicoli_eea.json.gz: si riscaricano quando mancano, quando
sono più vecchie di 30 giorni o quando in source/ c'è il file "aggiorna_veicoli" (che poi viene tolto).
Se una fonte non risponde si tiene il file che c'è. L'esito di ogni richiesta è stampato nel registro.
"""
import datetime as dt
import gzip
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CACHE_ADEME = os.path.join(ROOT, "source", "veicoli_ademe.json.gz")
CACHE_EEA = os.path.join(ROOT, "source", "veicoli_eea.json.gz")
TRIGGER = os.path.join(ROOT, "source", "aggiorna_veicoli")
GIORNI = 30
ADEME = "https://data.ademe.fr/data-fair/api/v1/datasets/ademe-car-labelling/lines"
EEA = "https://discodata.eea.europa.eu/sql"
AGENTE = "iVIAGGI-dati/1.0 (+https://csantacroce.github.io/iviaggi-dati/)"

# Campi ADEME che servono (nomi verificati dal vivo il 20/11/2026)
CAMPI_ADEME = ["Marque", "Libellé_modèle", "Modèle", "Description_Commerciale", "Energie", "Cylindrée",
               "Puissance_maximale", "Type_de_boite", "Carrosserie",
               "Conso_vitesse_mixte_Min", "Conso_vitesse_mixte_Max",
               "Conso_T-haute_vitesse_Min", "Conso_T-haute_vitesse_Max",
               "Conso_haute_vitesse_Min", "Conso_haute_vitesse_Max",
               "Conso_elec_Min", "Conso_elec_Max", "Autonomie_elec_Min", "Autonomie_elec_Max"]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", s).split())


def leggi(url, params=None, tentativi=3):
    """GET JSON con tre tentativi. Solleva l'ultima eccezione con il codice HTTP e l'inizio della risposta."""
    if params:
        url += "?" + urllib.parse.urlencode(params)
    ultimo = None
    for i in range(tentativi):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": AGENTE})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            corpo = e.read()[:300].decode("utf-8", "replace")
            ultimo = RuntimeError(f"HTTP {e.code}: {corpo}")
            if e.code in (400, 403, 404):
                break
        except Exception as e:  # rete, JSON, timeout
            ultimo = e
        time.sleep(2 ** i * 3)
    raise ultimo


# ---------------------------------------------------------------- ADEME

def scarica_ademe():
    righe = []
    url, params = ADEME, {"size": 1000, "select": ",".join(CAMPI_ADEME)}
    try:
        leggi(url, {**params, "size": 1})
    except RuntimeError as e:
        print(f"ADEME: elenco dei campi non accettato ({e}); scarico tutti i campi")
        params = {"size": 1000}
    pagina = {}
    for _ in range(50):
        pagina = leggi(url, params)
        righe += pagina.get("results", [])
        prossima = pagina.get("next")
        if not prossima:
            break
        url, params = prossima, None
    totale = pagina.get("total")
    if not righe:
        raise RuntimeError("ADEME: nessuna riga")
    print(f"ADEME: {len(righe)} versioni scaricate (totale dichiarato {totale})")
    return righe


# ---------------------------------------------------------------- EEA

def interroga_eea(sql, pagina_righe=10000, pagine=60):
    righe = []
    for p in range(1, pagine + 1):
        r = leggi(EEA, {"query": sql, "p": p, "nrOfHits": pagina_righe})
        if isinstance(r, dict) and r.get("errors"):
            raise RuntimeError(f"EEA: {json.dumps(r['errors'])[:300]}")
        blocco = r.get("results", []) if isinstance(r, dict) else []
        righe += blocco
        if len(blocco) < pagina_righe:
            break
    return righe


def scarica_eea():
    """Versioni immatricolate in Italia dal 2021 (somma delle immatricolazioni R). Il filtro di sicurezza del
    sito EEA respinge alcune interrogazioni (403): si provano forme diverse, dalla più precisa alla più semplice,
    e nel registro resta l'esito di ognuna."""
    colonne = ("Mk AS marca, Cn AS modello, Ft AS carburante, Fm AS modo, [Ec (cm3)] AS cc, [Ep (KW)] AS kw, "
               "SUM(R) AS auto, AVG(Fc) AS fc, AVG([Z (Wh/km)]) AS z, MAX([Year]) AS anno")
    gruppo = "GROUP BY Mk, Cn, Ft, Fm, [Ec (cm3)], [Ep (KW)]"
    tabella = "[CO2Emission].[latest].[co2cars]"
    prove = [
        ("solo Italia", f"SELECT {colonne} FROM {tabella} WHERE MS = 'IT' AND [Year] >= 2021 {gruppo}", False),
        ("tutti gli Stati, filtro dopo", f"SELECT MS AS stato, {colonne} FROM {tabella} WHERE [Year] >= 2021 GROUP BY MS, "
                                         + gruppo[len("GROUP BY "):], True),
    ]
    for nome, sql, filtra in prove:
        try:
            righe = interroga_eea(sql)
        except Exception as e:
            print(f"EEA ({nome}): non riuscita: {e}")
            continue
        if filtra:
            righe = [r for r in righe if str(r.get("stato", "")).upper() == "IT"]
        if righe:
            print(f"EEA ({nome}): {len(righe)} versioni immatricolate in Italia")
            return righe
        print(f"EEA ({nome}): risposta vuota")
    raise RuntimeError("EEA: nessuna interrogazione riuscita")


# ---------------------------------------------------------------- cache

def _vecchio(path):
    if not os.path.exists(path):
        return True
    eta = dt.datetime.now().timestamp() - os.path.getmtime(path)
    return eta > GIORNI * 86400


def _salva(path, fonte, licenza, righe):
    dati = {"fonte": fonte, "licenza": licenza,
            "scaricato": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "righe": righe}
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


def _leggi_cache(path):
    if not os.path.exists(path):
        return None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def aggiorna_cache():
    """Riscarica le fonti quando serve. Restituisce un messaggio per il registro."""
    forza = os.path.exists(TRIGGER)
    msg = []
    if forza or _vecchio(CACHE_ADEME):
        try:
            _salva(CACHE_ADEME, "ADEME Car Labelling", "Licence Ouverte 2.0", scarica_ademe())
            msg.append("ADEME aggiornato")
        except Exception as e:
            msg.append(f"ADEME non aggiornato ({e})")
    if forza or _vecchio(CACHE_EEA):
        try:
            _salva(CACHE_EEA, "EEA CO2 emissions from new passenger cars", "CC BY 4.0", scarica_eea())
            msg.append("EEA aggiornato")
        except Exception as e:
            msg.append(f"EEA non aggiornato ({e})")
    if forza:
        try:
            os.remove(TRIGGER)
        except OSError:
            pass
    return "Veicoli: " + "; ".join(msg) if msg else ""


# ---------------------------------------------------------------- catalogo

def _num(v):
    try:
        x = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def _media(a, b):
    a, b = _num(a), _num(b)
    if a is None and b is None:
        return None
    if a is None or b is None:
        return round(a or b, 2)
    return round((a + b) / 2, 2)


def alimentazione_ademe(e):
    """Energie ADEME (es. ESSENCE, GAZOLE, ELECTRIC, ESS+ELEC HNR, GAZ+ELEC HR, GPL, GAZ NAT.VEH, SUPERETHANOL,
    oppure i vecchi codici ES, GO, EL, EH, GH, EE, GL). HR = ricaricabile (plug-in), HNR = non ricaricabile."""
    e = norm(e)
    t = set(e.split())
    codici = {"ES": "Benzina", "GO": "Gasolio", "EL": "Elettrica", "EH": "Ibrida benzina", "GH": "Ibrida gasolio",
              "EE": "Plug-in benzina", "GL": "Plug-in gasolio", "GP": "GPL", "GN": "Metano", "FE": "Bioetanolo"}
    if e in codici:
        return codici[e]
    if e in ("ELECTRIC", "ELECTRIQUE", "ELEC"):
        return "Elettrica"
    if "ELEC" in t or "ELECTRIC" in t or "ELECTRIQUE" in t:
        base = "gasolio" if ("GAZOLE" in t or "GAZ" in t or "GO" in t or "DIESEL" in t) else "benzina"
        return ("Plug-in " if "HR" in t else "Ibrida ") + base
    if "GPL" in t or "G P L" in e:
        return "GPL"
    if "GNV" in t or "NAT" in t or "GN" in t:
        return "Metano"
    if "GAZOLE" in t or "DIESEL" in t:
        return "Gasolio"
    if "SUPERETHANOL" in t or "E85" in t:
        return "Bioetanolo"
    return "Benzina"


SIGLE = {"BMW", "MG", "DS", "BYD", "GWM", "BAIC", "JAC", "SWM", "DR", "EVO", "NIO", "XEV", "KGM", "VW", "AMG", "GMC"}


# ADEME tronca le marche a 10 caratteri e usa i punti nelle sigle
MARCHE = {"B M W": "BMW", "M G": "MG", "LAMBORGHIN": "Lamborghini", "ROLLS ROYC": "Rolls-Royce",
          "CITROEN": "Citroën", "MERCEDES": "Mercedes-Benz", "MERCEDES BENZ": "Mercedes-Benz",
          "LAND ROVER": "Land Rover", "ALFA ROMEO": "Alfa Romeo", "ASTON MART": "Aston Martin",
          "ASTON MARTIN": "Aston Martin", "SSANGYONG": "SsangYong", "KGM": "KGM", "DS": "DS"}


def ripara(t):
    """Testo UTF-8 letto due volte come Latin-1 nella fonte ("Ã " → "à")."""
    t = str(t or "")
    if "Ã" in t or "Â" in t:
        try:
            t = t.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return " ".join(t.split())


def nome_marca(m):
    m = ripara(m)
    return MARCHE.get(norm(m)) or marca_leggibile(m)


def pulisci_versione(v):
    """Descrizione commerciale ADEME più leggibile: niente gamma di pneumatici, "ch" (chevaux) → "CV"."""
    v = ripara(v)
    if not v:
        return None
    v = v.replace("Ã ", "à ")   # in alcune righe della fonte il secondo byte della "à" è già perso
    v = re.sub(r"\s*\bpn\s+de\s+\d+\s*''?\s*(à|a)\s*\d+\s*''?", "", v, flags=re.I)
    v = re.sub(r"\((\d+)\s*ch\)", r"(\1 CV)", v)
    v = re.sub(r"\b(\d+)\s*ch\b", r"\1 CV", v)
    return " ".join(v.split()) or None


def marca_leggibile(m):
    """FIAT → Fiat, ALFA ROMEO → Alfa Romeo, MERCEDES-BENZ → Mercedes-Benz, BMW resta BMW."""
    m = " ".join(str(m or "").split())
    if not m.isupper():
        return m
    def parola(w):
        # sigle e nomi con cifre restano come sono (BMW, X3, ID.3, 500E)
        return "-".join(x if x in SIGLE or len(x) <= 2 or any(c.isdigit() for c in x) else x.capitalize()
                        for x in w.split("-"))
    return " ".join(parola(w) for w in m.split())


def alimentazione_eea(ft, fm):
    ft, fm = norm(ft), norm(fm)
    if ft == "ELECTRIC":
        return "Elettrica"
    if "ELECTRIC" in ft:
        base = "gasolio" if "DIESEL" in ft else "benzina"
        return ("Plug-in " if fm == "P" else "Ibrida ") + base
    if ft == "DIESEL":
        return "Gasolio" if fm != "H" else "Ibrida gasolio"
    if ft == "LPG":
        return "GPL"
    if ft in ("NG", "NG BIOMETHANE"):
        return "Metano"
    if ft in ("E85",):
        return "Bioetanolo"
    return "Ibrida benzina" if fm == "H" else "Benzina"


def motore_testo(cc, kw, alimentazione, versione=None):
    """"1.2 Ibrida benzina 136 CV", "Elettrica 156 CV". Cilindrata e cavalli come li scrive la casa quando la
    descrizione commerciale li riporta (2143 cm³ = "2.2 Diesel", 148 kW = "200 CV"; Yaris Hybrid 116 CV e non
    i 92 del solo motore termico), altrimenti calcolati."""
    parti = []
    if cc and cc > 0 and alimentazione != "Elettrica":
        litri = cc / 1000
        m = re.search(r"(?<![\d.])(\d\.\d)(?![\d.])", versione or "")
        parti.append(m.group(1) if m and abs(float(m.group(1)) - litri) <= 0.15 else f"{litri:.1f}")
    parti.append(alimentazione)
    if kw:
        cv = round(kw * 1.35962)
        m = re.search(r"\((\d+) CV\)", versione or "")
        if m and 20 <= int(m.group(1)) <= 2000:
            cv = int(m.group(1))   # per le ibride è la potenza di sistema, quella che conosce chi guida
        parti.append(f"{cv} CV")
    return " ".join(parti)


def unisci_doppioni(righe):
    """Versioni con lo stesso nome (in ADEME cambiano solo pneumatici o massa): una riga sola, consumi medi."""
    gruppi = {}
    for r in righe:
        k = (r["marca"], r["modello"], r["motore"], r["versione"], r["alimentazione"], r["cambio"], r["fonte"])
        gruppi.setdefault(k, []).append(r)
    unite = []
    for elenco in gruppi.values():
        r = dict(elenco[0])
        for c in ("consumo_misto", "consumo_extra_alto", "consumo_elettrico"):
            valori = [x[c] for x in elenco if x[c] is not None]
            r[c] = round(sum(valori) / len(valori), 2) if valori else None
        autonomie = [x["autonomia_elettrica"] for x in elenco if x["autonomia_elettrica"]]
        r["autonomia_elettrica"] = min(autonomie) if autonomie else None   # la più prudente
        immatricolate = [x["immatricolate_it"] for x in elenco if x["immatricolate_it"]]
        r["immatricolate_it"] = max(immatricolate) if immatricolate else None
        unite.append(r)
    return unite


def nomi_brevi(righe):
    """Allestimento: la versione senza la parte uguale per tutte le versioni dello stesso motore
    ("Yaris Cross 1.5L (116 CV) Hybrid 2WD Trail MY21" → "2WD Trail MY21"). Con una sola versione resta intera."""
    gruppi = {}
    for r in righe:
        gruppi.setdefault((r["marca"], r["modello"], r["motore"]), []).append(r)
    for elenco in gruppi.values():
        for r in elenco:
            r["allestimento"] = r["versione"]
        parole = [(r["versione"] or "").split() for r in elenco]
        if len(elenco) < 2 or any(not p for p in parole):
            continue
        comuni = 0
        while all(len(p) > comuni + 1 and p[comuni] == parole[0][comuni] for p in parole):
            comuni += 1
        if comuni == 0:
            continue
        brevi = [" ".join(p[comuni:]).strip(" -/·,") for p in parole]
        if all(brevi) and len(set(brevi)) == len(brevi):
            for r, b in zip(elenco, brevi):
                r["allestimento"] = b
    # stesso nome con cambio diverso (Mini One manuale e automatica): si aggiunge il cambio
    for elenco in gruppi.values():
        conta = {}
        for r in elenco:
            conta[r["allestimento"]] = conta.get(r["allestimento"], 0) + 1
        for r in elenco:
            if conta[r["allestimento"]] > 1 and r["cambio"] and r["allestimento"]:
                r["allestimento"] += " · " + r["cambio"]


def costruisci(con):
    """Tabella veicoli nel database dell'app. Restituisce un riassunto (o None se mancano i dati)."""
    ademe = _leggi_cache(CACHE_ADEME)
    eea = _leggi_cache(CACHE_EEA)
    if not ademe and not eea:
        return None
    # immatricolazioni in Italia per (marca, modello, alimentazione, kW)
    italia = {}
    for r in (eea or {}).get("righe", []):
        kw = _num(r.get("kw"))
        if not kw:
            continue
        k = (norm(nome_marca(r.get("marca"))), norm(r.get("modello")), alimentazione_eea(r.get("carburante"), r.get("modo")))
        italia.setdefault(k, []).append((kw, int(_num(r.get("auto")) or 0), r))
    righe = []
    usate = set()
    for r in (ademe or {}).get("righe", []):
        marca, modello = r.get("Marque"), r.get("Libellé_modèle") or r.get("Modèle")
        if not marca or not modello:
            continue
        alim = alimentazione_ademe(r.get("Energie"))
        kw = _num(r.get("Puissance_maximale"))
        cc = _num(r.get("Cylindrée"))
        auto_it = None
        if italia:
            for kw_it, n, rif in italia.get((norm(nome_marca(marca)), norm(modello), alim), []):
                if kw and abs(kw_it - kw) <= 2:
                    auto_it = (auto_it or 0) + n
                    usate.add(id(rif))
        plug = alim.startswith("Plug-in")
        elettr = _media(r.get("Conso_elec_Min"), r.get("Conso_elec_Max"))
        righe.append(dict(
            marca=nome_marca(marca),
            modello=marca_leggibile(ripara(modello)), versione=pulisci_versione(r.get("Description_Commerciale")),
            alimentazione=alim, cilindrata=int(cc) if cc else None, kw=int(kw) if kw else None,
            cambio={"AUTOMATIQUE": "Automatico", "MECANIQUE": "Manuale"}.get(norm(r.get("Type_de_boite"))),
            consumo_misto=None if plug or alim == "Elettrica" else _media(r.get("Conso_vitesse_mixte_Min"), r.get("Conso_vitesse_mixte_Max")),
            consumo_extra_alto=None if alim == "Elettrica" else _media(r.get("Conso_T-haute_vitesse_Min"), r.get("Conso_T-haute_vitesse_Max")),
            consumo_elettrico=round(elettr / 10, 1) if elettr else None,     # Wh/km → kWh/100 km
            autonomia_elettrica=int(_num(r.get("Autonomie_elec_Max")) or 0) or None,
            immatricolate_it=auto_it, fonte="ADEME"))
    # versioni vendute in Italia che ADEME non ha (solo dati EEA: niente fasi WLTP, niente versione commerciale)
    modelli_ademe = {(norm(r["marca"]), norm(r["modello"])) for r in righe}
    for chiave, elenco in italia.items():
        if chiave[:2] in modelli_ademe:
            continue
        for kw_it, n, rif in elenco:
            if n < 100:
                continue
            alim = chiave[2]
            fc = _num(rif.get("fc"))
            z = _num(rif.get("z"))
            cc = _num(rif.get("cc"))
            righe.append(dict(
                marca=nome_marca(rif.get("marca")), modello=marca_leggibile(ripara(rif.get("modello"))),
                versione=None, alimentazione=alim, cilindrata=int(cc) if cc else None, kw=int(kw_it),
                cambio=None,
                consumo_misto=None if alim.startswith("Plug-in") or alim == "Elettrica" else (round(fc, 2) if fc else None),
                consumo_extra_alto=None,
                consumo_elettrico=round(z / 10, 1) if z else None,
                autonomia_elettrica=None, immatricolate_it=n, fonte="EEA"))
    for r in righe:
        r["motore"] = motore_testo(r["cilindrata"], r["kw"], r["alimentazione"], r["versione"])
    righe = unisci_doppioni(righe)
    nomi_brevi(righe)
    righe.sort(key=lambda r: (norm(r["marca"]), norm(r["modello"]), r["alimentazione"], r["kw"] or 0, r["versione"] or ""))
    con.execute("DROP TABLE IF EXISTS veicoli")
    con.execute("""CREATE TABLE veicoli (
        id INTEGER PRIMARY KEY, marca TEXT NOT NULL, modello TEXT NOT NULL, motore TEXT NOT NULL,
        versione TEXT, allestimento TEXT, alimentazione TEXT NOT NULL, cilindrata INTEGER, kw INTEGER, cambio TEXT,
        consumo_misto REAL, consumo_extra_alto REAL, consumo_elettrico REAL, autonomia_elettrica INTEGER,
        immatricolate_it INTEGER, fonte TEXT NOT NULL)""")
    campi = ["marca", "modello", "motore", "versione", "allestimento", "alimentazione", "cilindrata", "kw", "cambio", "consumo_misto",
             "consumo_extra_alto", "consumo_elettrico", "autonomia_elettrica", "immatricolate_it", "fonte"]
    con.executemany(f"INSERT INTO veicoli ({','.join(campi)}) VALUES ({','.join('?' * len(campi))})",
                    [tuple(r[c] for c in campi) for r in righe])
    con.execute("CREATE INDEX ix_veicoli ON veicoli(marca, modello)")
    con.commit()
    riassunto = {"righe": len(righe), "ademe": sum(r["fonte"] == "ADEME" for r in righe),
                 "solo_eea": sum(r["fonte"] == "EEA" for r in righe),
                 "venduti_in_italia": sum(1 for r in righe if r["immatricolate_it"]),
                 "con_extra_alto": sum(1 for r in righe if r["consumo_extra_alto"]),
                 "ademe_scaricato": (ademe or {}).get("scaricato"), "eea_scaricato": (eea or {}).get("scaricato")}
    print("Veicoli:", json.dumps(riassunto, ensure_ascii=False))
    return riassunto


if __name__ == "__main__":
    print(aggiorna_cache())
