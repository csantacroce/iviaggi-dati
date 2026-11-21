"""Sonda temporanea (21/11/2026): quali campi ha l'EEA per le elettriche immatricolate in Italia (autonomia, consumo)."""
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from veicoli import leggi, EEA_API

def q(d):
    return leggi(EEA_API, {"source": json.dumps(d, separators=(",", ":"))})

for anno in (2025, 2024):
    r = q({"size": 2, "query": {"bool": {"must": [{"term": {"year": anno}}, {"term": {"MS": "IT"}},
                                                  {"match": {"Ft": "electric"}}]}}})
    hits = r.get("hits", {}).get("hits", [])
    print("ANNO", anno, "totale", r.get("hits", {}).get("total"))
    for h in hits:
        print(json.dumps(h.get("_source"), ensure_ascii=False))
# valori distinti di Ft
r = q({"size": 0, "query": {"bool": {"must": [{"term": {"year": 2024}}, {"term": {"MS": "IT"}}]}},
       "aggs": {"ft": {"terms": {"field": "Ft", "size": 30}}}})
print("FT", json.dumps(r.get("aggregations"), ensure_ascii=False))
