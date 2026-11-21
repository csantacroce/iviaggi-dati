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


EEA_API = "https://co2cars.apps.eea.europa.eu/tools/api"


def interroga_api_eea(anno):
    """Riassunto delle auto immatricolate in Italia in un anno (20/11/2026): l'interfaccia pubblica del
    visualizzatore co2cars dell'EEA accetta le aggregazioni; una riga per marca, modello, carburante, modo,
    cilindrata e potenza, con le immatricolazioni e il consumo medio. Pagine da 1000 (aggregazione "composite")."""
    righe, dopo = [], None
    for _ in range(100):
        comp = {"size": 1000, "sources": [
            {"marca": {"terms": {"field": "Mk"}}}, {"modello": {"terms": {"field": "Cn"}}},
            {"carburante": {"terms": {"field": "Ft"}}}, {"modo": {"terms": {"field": "Fm", "missing_bucket": True}}},
            {"cc": {"terms": {"field": "ec__cm3_", "missing_bucket": True}}},
            {"kw": {"terms": {"field": "ep__KW_", "missing_bucket": True}}}]}
        if dopo:
            comp["after"] = dopo
        q = {"size": 0, "query": {"bool": {"must": [{"term": {"year": anno}}, {"term": {"MS": "IT"}}]}},
             "aggs": {"v": {"composite": comp, "aggs": {"auto": {"sum": {"field": "r"}}, "fc": {"avg": {"field": "Fc"}},
                                                         "z": {"avg": {"field": "z__Wh_km_"}},
                                                         "zr": {"avg": {"field": "Zr"}}}}}}
        r = leggi(EEA_API, {"source": json.dumps(q, separators=(",", ":"))})
        agg = r.get("aggregations", {}).get("v", {})
        for b in agg.get("buckets", []):
            k = b["key"]
            if not k.get("marca") or not k.get("modello"):
                continue
            righe.append({**k, "auto": b["auto"]["value"], "fc": b["fc"]["value"], "z": b["z"]["value"],
                          "zr": b.get("zr", {}).get("value"), "anno": anno})   # Zr: autonomia elettrica WLTP (km)
        dopo = agg.get("after_key")
        if not dopo or not agg.get("buckets"):
            break
    return righe


def scarica_eea():
    """Versioni immatricolate in Italia negli ultimi anni (EEA, CC BY 4.0). Prima l'interfaccia del visualizzatore
    co2cars (funziona dal 20/11/2026); se non risponde, le vecchie prove su DISCODATA (respinte dal filtro di sicurezza)."""
    anno = dt.date.today().year
    righe = []
    for a in range(anno, anno - 4, -1):          # es. 2026 (se c'è), 2025, 2024, 2023
        try:
            nuove = interroga_api_eea(a)
            print(f"EEA {a} (co2cars): {len(nuove)} versioni immatricolate in Italia")
            righe += nuove
        except Exception as e:
            print(f"EEA {a} (co2cars): non riuscita: {e}")
        if len({r["anno"] for r in righe}) >= 2:  # bastano i due anni più recenti pubblicati
            break
    if righe:
        # stessa versione in più anni: si sommano le immatricolazioni e si tiene il consumo più recente
        unite = {}
        for r in sorted(righe, key=lambda r: r["anno"]):
            k = (r["marca"], r["modello"], r["carburante"], r.get("modo"), r.get("cc"), r.get("kw"))
            if k in unite:
                v = unite[k]
                v["auto"] = (v["auto"] or 0) + (r["auto"] or 0)
                v["fc"] = r["fc"] if r["fc"] is not None else v["fc"]
                v["z"] = r["z"] if r["z"] is not None else v["z"]
                v["zr"] = r.get("zr") if r.get("zr") is not None else v.get("zr")
                v["anno"] = r["anno"]
            else:
                unite[k] = dict(r)
        return list(unite.values())
    return scarica_eea_discodata()


def scarica_eea_discodata():
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
          "ASTON MARTIN": "Aston Martin", "SSANGYONG": "KG Mobility", "KGM": "KG Mobility", "KG MOBILITY": "KG Mobility",
          "DS": "DS", "DS AUTOMOBILES": "DS", "AUTOMOBILI LAMBORGHINI S P A": "Lamborghini", "LAMBORGHINI": "Lamborghini",
          "MERCEDES AMG": "Mercedes-Benz", "FORD CNG TECHNIK": "Ford", "LYNK CO": "Lynk & Co",
          "SHINERAY SWM JINBEI SRM": "SWM", "DR AUTOMOBILES": "DR", "INEOS GRENADIER": "Ineos", "INEOS": "Ineos"}


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
    if "HYDROGEN" in ft:
        return "Idrogeno"
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


CODA = re.compile(r"^(\d+\s?kw|kw|hybrid\+?|e-?hybrid\d*|ehybrid|phev|plug-?in|electric|elettrica|ev|gpl|lpg|bi-?fuel|h|4matic\+?|xdrive|"
                  r"sdrive|edrive\d*|4x4|awd|fwd|pro|pure|performance|competition|tourer|lr|\+)$", re.I)


def famiglia(marca, modello):
    """Nome della famiglia del modello, uguale per ADEME ed EEA (20/11/2026): l'EEA chiama "modello" anche la versione
    ("320D Xdrive", "Glc 220 D 4MATIC", "ID.3 Pro 150 KW"). Esempi: BMW 320D → Serie 3, IX1 EDRIVE20 → iX1;
    Mercedes C 220 D → Classe C, Glc 300 → GLC, AMG A 35 → Classe A; VW ID.3 Pro → ID.3; Toyota Yaris → Yaris."""
    m = " ".join(str(modello or "").split())
    m = re.split(r"\s*[,;]\s*", m)[0]                      # "Kona, Kauai" → "Kona"; "TUCSON,IX35" → "TUCSON"
    if norm(m).startswith(norm(marca) + " "):
        m = m[len(marca):].strip()
    sigla = re.sub(r"[^A-Z]", "", marca.upper())
    if sigla and re.match(rf"^{sigla}\d", m.upper()):           # "DS4" → "4", "DR3.0" → "3.0"
        m = m[len(sigla):].strip()
    m = re.sub(r"^AMG\s+", "", m, flags=re.I)
    t = m.split()
    # dati tecnici dentro il nome: si taglia dal primo ("Born 170 KW 60/63 Kwh", "Friday 1.5T Dct", "Leaf 40kWh")
    for i in range(1, len(t)):
        if re.match(r"^(\d+\s?kwh?|kwh?|\d\.\dt?|\d+/\d+|e-?hybrid\d*|dct|dual-?.*)$", t[i], re.I):
            t = t[:i]
            break
    if len(t) > 1 and re.match(r"^\d+$", t[-1]) and re.match(r"^\d+\s?kw", " ".join(m.split()[len(t) - 1:len(t) + 1]), re.I):
        t = t[:-1]                                              # "Born 170 KW" → "Born"
    if norm(marca) == "LEXUS" and t and re.match(r"^[A-Z]{2,3}\d", t[0].upper()):
        return re.match(r"^[A-Z]{2,3}", t[0].upper()).group(0)   # "RX450H+" → "RX"
    m = " ".join(t)
    if m.isupper() and len(m) > 3:
        m = marca_leggibile(m)                                  # "TUCSON" → "Tucson"
        t = m.split()
    if len(sigla) <= 2 and re.match(r"^\d", m):
        m = f"{sigla} {m}"                                      # DS 4, DR 5.0 (il nome del modello comprende la marca)
        t = m.split()
    if not t:
        return modello
    b = norm(marca)
    p0 = t[0].upper()
    if b == "BMW":
        if re.match(r"^\d{3}", p0):
            return "Serie " + p0[0]
        r = re.match(r"^M(\d)(\d\d)", p0)
        if r:
            return "Serie " + r.group(1)
        if re.match(r"^M\d$", p0):
            return p0
        if re.match(r"^(X\d|Z\d|XM)$", p0):
            return p0
        if re.match(r"^I(X\d?|\d)$", p0):
            return "i" + p0[1:]
        return m
    if b == "MERCEDES BENZ":
        if norm(m).startswith("CLASSE "):
            resto = m.split(None, 1)[1]
            return resto.upper() if len(resto) > 1 else "Classe " + resto.upper()
        if p0 in ("V-KLASSE", "V-CLASS"):
            return "Classe V"
        if "T-CLASS" in m.upper():
            return "Classe T"
        if p0 in ("A", "B", "C", "E", "S", "G", "V"):
            return "Classe " + p0
        if re.match(r"^(CLA|CLE|CLS|GLA|GLB|GLC|GLE|GLS|EQA|EQB|EQC|EQE|EQS|EQV|SL|GT)\+?$", p0.rstrip("+")):
            return p0.rstrip("+")
        if re.match(r"^(EQA|EQB|EQE|EQS)\d", p0):
            return p0[:3]
        return t[0] if len(t) > 1 and t[0].lower() in ("sprinter", "vito", "citan") else m
    if b == "VOLKSWAGEN":
        r = re.match(r"^ID\.?\s?(\d|BUZZ)", m.upper().replace("ID. ", "ID."))
        if r:
            return "ID." + (r.group(1) if r.group(1).isdigit() else " Buzz")
    if b == "AUDI":
        r = re.match(r"^((?:RS|SQ|S|A|Q)\s?\d)(?:\s+(E-?TRON))?", m, re.I)
        if r:
            return r.group(1).upper().replace(" ", "") + (" e-tron" if r.group(2) else "")
    if b == "HYUNDAI":
        m2 = re.sub(r"IX35|KAUAI", "", " ".join(t), flags=re.I).strip()
        m2 = re.sub(r"^I\s?(\d+)", lambda r: "i" + r.group(1), m2, flags=re.I)        # I 10, I10 → i10
        m2 = re.sub(r"^IONIQ\s?(\d)", lambda r: "Ioniq " + r.group(1), m2, flags=re.I)
        m2 = re.sub(r"^TUCSON", "Tucson", m2, flags=re.I).replace("Santa FE", "Santa Fe")
        t = m2.split() or t
    if b == "DR":
        m2 = re.sub(r"^DR (\d)H?$", lambda r: f"DR {r.group(1)}.0", " ".join(t))      # DR 6, DR 7H → DR 6.0, DR 7.0
        t = m2.split()
    if b == "MINI":
        testo = " ".join(t).lower()
        for fam in ("countryman", "aceman", "clubman", "paceman"):
            if fam in testo:
                return fam.capitalize()
        if "jcw" in testo or "john cooper works" in testo:
            return "John Cooper Works"
        return "Cooper"                              # Mini, Cooper C, Cooper SE… = la Mini 3/5 porte
    if b == "SKODA" and len(t) >= 2 and re.match(r"^\d{2}X?$", t[-1], re.I):
        t = t[:-1]                                   # Elroq 85, Enyaq 85X → Elroq, Enyaq
    if b == "PORSCHE" and t:
        return " ".join(t[:2]) if t[0] == "718" and len(t) > 1 else t[0]   # 911, 718 Cayman, Cayenne, Macan…
    if b == "MASERATI" and t:
        return t[0]                                  # Grecale, Levante, Ghibli (le versioni sono allestimenti)
    for i in range(1, len(t)):                       # "SX4 Suzuki SX4 S-C" → "SX4"
        if norm(t[i]) == b:
            t = t[:i]
            break
    if b == "RENAULT" and len(t) >= 2 and norm(t[1]) == "E TECH" and t[0].lower() in (
            "clio", "captur", "arkana", "austral", "espace", "rafale", "symbioz"):
        t = t[:1]                                   # Clio E-Tech = Clio ibrida (Megane, Scenic, 4, 5 E-Tech restano modelli)
    # tutte le altre: tolte le parole finali di versione (Hybrid, Phev, Electric, 150 KW…)
    while len(t) > 1 and CODA.match(t[-1]):
        t.pop()
    while len(t) > 2 and re.match(r"^\d+$", t[-1]) and t[-2].upper() == "KW":
        t = t[:-2]
    return " ".join(t)


def famiglia_ademe(marca, modello, versione):
    """ADEME raggruppa alcune famiglie ("BMW Serie X" = X1…X7): in quel caso la famiglia viene dalla versione."""
    if norm(marca) == "BMW" and norm(modello) in ("SERIE X", "SERIE Z", "SERIE I") and versione:
        fam = famiglia(marca, versione)
        if fam != versione:
            return fam
    return famiglia(marca, modello)


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
        mc = nome_marca(r.get("marca"))
        k = (norm(mc), norm(famiglia(mc, marca_leggibile(ripara(r.get("modello"))))),
             alimentazione_eea(r.get("carburante"), r.get("modo")))
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
        z_it, zr_it = [], []      # consumo e autonomia elettrica EEA delle versioni abbinate (21/11/2026)
        if italia:
            for kw_it, n, rif in italia.get((norm(nome_marca(marca)), norm(modello), alim), []):
                if kw and abs(kw_it - kw) <= 2:
                    auto_it = (auto_it or 0) + n
                    usate.add(id(rif))
                    if _num(rif.get("z")):
                        z_it.append(_num(rif.get("z")))
                    if _num(rif.get("zr")):
                        zr_it.append(_num(rif.get("zr")))
        plug = alim.startswith("Plug-in")
        elettr = _media(r.get("Conso_elec_Min"), r.get("Conso_elec_Max"))
        righe.append(dict(
            marca=nome_marca(marca),
            modello=famiglia_ademe(nome_marca(marca), marca_leggibile(ripara(modello)),
                                   pulisci_versione(r.get("Description_Commerciale"))),
            versione=pulisci_versione(r.get("Description_Commerciale")),
            alimentazione=alim, cilindrata=int(cc) if cc else None, kw=int(kw) if kw else None,
            cambio={"AUTOMATIQUE": "Automatico", "MECANIQUE": "Manuale"}.get(norm(r.get("Type_de_boite"))),
            consumo_misto=None if plug or alim == "Elettrica" else _media(r.get("Conso_vitesse_mixte_Min"), r.get("Conso_vitesse_mixte_Max")),
            consumo_extra_alto=None if alim == "Elettrica" else _media(r.get("Conso_T-haute_vitesse_Min"), r.get("Conso_T-haute_vitesse_Max")),
            consumo_elettrico=round(elettr / 10, 1) if elettr else (round(min(z_it) / 10, 1) if z_it else None),
            autonomia_elettrica=int(_num(r.get("Autonomie_elec_Max")) or 0) or (int(min(zr_it)) if zr_it else None),
            immatricolate_it=auto_it, fonte="ADEME"))
    # versioni vendute in Italia che ADEME non ha (solo dati EEA: consumo WLTP misto, niente fasi né descrizione
    # commerciale; il nome commerciale EEA, se dice più della famiglia, diventa l'allestimento: "320D Xdrive")
    for chiave, elenco in italia.items():
        for kw_it, n, rif in elenco:
            if id(rif) in usate or n < 100:
                continue
            alim = chiave[2]
            fc = _num(rif.get("fc"))
            z = _num(rif.get("z"))
            cc = _num(rif.get("cc"))
            mc = nome_marca(rif.get("marca"))
            nome = marca_leggibile(ripara(rif.get("modello")))
            if norm(nome).startswith(norm(mc) + " "):
                nome = nome[len(mc):].strip()
            fam = famiglia(mc, nome)
            righe.append(dict(
                marca=mc, modello=fam, versione=None if norm(nome) == norm(fam) else nome,
                alimentazione=alim, cilindrata=int(cc) if cc else None, kw=int(kw_it),
                cambio=None,
                consumo_misto=None if alim.startswith("Plug-in") or alim == "Elettrica" else (round(fc, 2) if fc else None),
                consumo_extra_alto=None,
                consumo_elettrico=round(z / 10, 1) if z else None,
                autonomia_elettrica=int(_num(rif.get("zr"))) if _num(rif.get("zr")) else None,   # Zr, dal 21/11/2026
                immatricolate_it=n, fonte="EEA"))
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
