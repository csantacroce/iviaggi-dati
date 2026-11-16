#!/usr/bin/env python3
"""Tracciati stradali da OpenStreetMap (16/11/2026) per il riconoscimento della posizione nell'app.

Scarica da Overpass le carreggiate di autostrade, tangenziali e raccordi italiani e le salva in forma
compatta in source/osm_autostrade.json.gz (una riga per via: id, tipo, sigla, senso unico, nome, tracciato).
Si riscarica quando il file manca, quando è più vecchio di 30 giorni o quando in source/ c'è il file
"aggiorna_osm" (che poi viene tolto). Se Overpass non risponde si tiene il file che c'è.

Dati © OpenStreetMap contributors, licenza ODbL (https://www.openstreetmap.org/copyright).
"""
import datetime as dt
import gzip
import json
import os
import time
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CACHE = os.path.join(ROOT, "source", "osm_autostrade.json.gz")
TRIGGER = os.path.join(ROOT, "source", "aggiorna_osm")
GIORNI = 30
SERVER = ["https://overpass-api.de/api/interpreter",
          "https://overpass.kumi.systems/api/interpreter",
          "https://overpass.private.coffee/api/interpreter"]
QUERY = """[out:json][timeout:900][maxsize:2000000000];
area["ISO3166-1"="IT"][admin_level=2]->.it;
(
  way["highway"~"^(motorway|motorway_link)$"](area.it);
  way["highway"~"^(trunk|trunk_link)$"]["ref"~"^RA"](area.it);
  way["highway"="trunk"]["name"~"Tangenziale|Raccordo|Autostrada|Diramazione|Asse|Superstrada",i](area.it);
);
out tags geom;"""


def codifica(punti, prec=6):
    """Polyline di Google (precisione 1e-6) di una lista di (lat, lon)."""
    f = 10 ** prec
    out, plat, plon = [], 0, 0
    for lat, lon in punti:
        ilat, ilon = round(lat * f), round(lon * f)
        for v in (ilat - plat, ilon - plon):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def decodifica(s, prec=6):
    f = 10 ** prec
    punti, i, lat, lon = [], 0, 0, 0
    while i < len(s):
        for k in range(2):
            shift = res = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(res >> 1) if res & 1 else res >> 1
            if k == 0:
                lat += d
            else:
                lon += d
        punti.append((lat / f, lon / f))
    return punti


def scarica():
    dati = urllib.parse.urlencode({"data": QUERY}).encode()
    ultimo = None
    for url in SERVER:
        for tentativo in range(2):
            try:
                req = urllib.request.Request(url, data=dati, headers={"User-Agent": "iVIAGGI-dati (github.com/csantacroce/iviaggi-dati)"})
                with urllib.request.urlopen(req, timeout=1000) as r:
                    js = json.load(r)
                vie = []
                for el in js.get("elements", []):
                    if el.get("type") != "way" or not el.get("geometry"):
                        continue
                    t = el.get("tags", {})
                    pts = [(g["lat"], g["lon"]) for g in el["geometry"] if g]
                    if len(pts) < 2:
                        continue
                    vie.append([el["id"], t.get("highway", ""), t.get("ref", ""), t.get("oneway", ""),
                                t.get("name", ""), t.get("junction", ""), codifica(pts)])
                if len(vie) < 5000:
                    raise ValueError(f"risposta troppo piccola ({len(vie)} vie)")
                return vie
            except Exception as e:  # server occupato, risposta incompleta…
                ultimo = f"{url}: {e}"
                print("Overpass:", ultimo)
                time.sleep(30)
    raise RuntimeError(ultimo)


def aggiorna_cache(forza=False):
    """Restituisce un messaggio (o None se non c'era niente da fare)."""
    eta = None
    if os.path.exists(CACHE):
        try:
            with gzip.open(CACHE, "rt") as f:
                eta = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(json.load(f)["scaricato"])).days
        except Exception:
            eta = None
    if not (forza or os.path.exists(TRIGGER) or eta is None or eta >= GIORNI):
        return None
    vie = scarica()
    vie.sort(key=lambda v: v[0])
    ora = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    with gzip.GzipFile(CACHE, "wb", mtime=0) as f:
        f.write(json.dumps({"scaricato": ora, "licenza": "© OpenStreetMap contributors, ODbL",
                            "campi": ["id", "highway", "ref", "oneway", "name", "junction", "polyline6"],
                            "vie": vie}, ensure_ascii=False, separators=(",", ":")).encode())
    if os.path.exists(TRIGGER):
        os.remove(TRIGGER)
    return f"Tracciati OpenStreetMap aggiornati: {len(vie)} vie."


def leggi_cache():
    with gzip.open(CACHE, "rt") as f:
        return json.load(f)


if __name__ == "__main__":
    print(aggiorna_cache(forza=True))
