"""Sonda temporanea (21/11/2026): copertura di consumo (z) e autonomia (Zr) per le elettriche in Italia."""
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from veicoli import leggi, EEA_API

def q(d):
    return leggi(EEA_API, {"source": json.dumps(d, separators=(",", ":"))})

for anno in (2025, 2024):
    r = q({"size": 0, "query": {"bool": {"must": [{"term": {"year": anno}}, {"term": {"MS": "IT"}}, {"term": {"Ft": "electric"}}]}},
           "aggs": {"m": {"terms": {"field": "Cn", "size": 40, "order": {"n": "desc"}},
                          "aggs": {"n": {"sum": {"field": "r"}}, "mk": {"terms": {"field": "Mk", "size": 1}},
                                   "z": {"avg": {"field": "z__Wh_km_"}}, "zr": {"avg": {"field": "Zr"}},
                                   "zc": {"value_count": {"field": "z__Wh_km_"}}, "zrc": {"value_count": {"field": "Zr"}},
                                   "tot": {"value_count": {"field": "r"}}}}}})
    print("ANNO", anno)
    for b in r.get("aggregations", {}).get("m", {}).get("buckets", []):
        mk = b["mk"]["buckets"][0]["key"] if b["mk"]["buckets"] else ""
        print(f'{mk:12} {b["key"]:28} n={int(b["n"]["value"]):6} z={b["z"]["value"]} zr={b["zr"]["value"]} righe={b["tot"]["value"]} conz={b["zc"]["value"]} conzr={b["zrc"]["value"]}')
