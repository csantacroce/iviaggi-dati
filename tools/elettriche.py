#!/usr/bin/env python3
"""Dati delle auto elettriche (21/11/2026, Carlo): autonomia WLTP, consumo, batteria utile e potenza di ricarica.

Fonti, in ordine:
  - ADEME e EEA (in veicoli.py): consumo WLTP (kWh/100 km, dalla presa) e autonomia WLTP; per l'EEA i campi
    z (Wh/km) e Zr (km), che alcune case non dichiarano (Volkswagen, Audi, Cupra, Leapmotor) o dichiarano dopo.
  - source/elettriche.csv: valori presi a mano dai siti e dai comunicati delle case (o da prove), ognuno con il link.
    Autonomia e consumo riempiono solo i valori mancanti; batteria utile (kWh) e ricarica massima in corrente
    continua (kW) vengono solo da qui. Righe per modello, eventualmente limitate per potenza (kw_da / kw_a).
Nella tabella veicoli: colonne batteria, ricarica_dc_kw e fonte_elettrica (se i valori vengono dal file).
"""
import csv
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MANUALI = os.path.join(ROOT, "source", "elettriche.csv")


def _num(v):
    try:
        x = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def costruisci(con):
    for col, tipo in (("batteria", "REAL"), ("ricarica_dc_kw", "REAL"), ("fonte_elettrica", "TEXT")):
        con.execute(f"ALTER TABLE veicoli ADD COLUMN {col} {tipo}")
    righe = list(csv.DictReader(open(MANUALI, encoding="utf-8"))) if os.path.exists(MANUALI) else []
    # prima le righe per tutto il modello, poi quelle limitate per potenza
    for r in sorted(righe, key=lambda r: bool((r.get("kw_da") or "") + (r.get("kw_a") or ""))):
        dove, par = "marca = ? AND modello = ? AND alimentazione = 'Elettrica'", [r["marca"], r["modello"]]
        if _num(r.get("kw_da")):
            dove += " AND kw >= ?"
            par.append(_num(r["kw_da"]))
        if _num(r.get("kw_a")):
            dove += " AND kw <= ?"
            par.append(_num(r["kw_a"]))
        aut, cons = _num(r.get("autonomia_km")), _num(r.get("consumo_kwh100"))
        batt, dc = _num(r.get("batteria_kwh")), _num(r.get("ricarica_dc_kw"))
        if aut:
            con.execute(f"UPDATE veicoli SET autonomia_elettrica = ? WHERE {dove} AND autonomia_elettrica IS NULL",
                        [int(aut)] + par)
        if cons:
            con.execute(f"UPDATE veicoli SET consumo_elettrico = ? WHERE {dove} AND consumo_elettrico IS NULL",
                        [cons] + par)
        con.execute(f"UPDATE veicoli SET batteria = COALESCE(?, batteria), ricarica_dc_kw = COALESCE(?, ricarica_dc_kw), "
                    f"fonte_elettrica = ? WHERE {dove}", [batt, dc, r["fonte"]] + par)
    con.commit()
    q = lambda sql: con.execute(sql).fetchone()[0]
    tot = q("SELECT COUNT(DISTINCT marca||modello) FROM veicoli WHERE alimentazione = 'Elettrica'")
    cons = q("SELECT COUNT(DISTINCT marca||modello) FROM veicoli WHERE alimentazione = 'Elettrica' AND consumo_elettrico > 0")
    aut = q("SELECT COUNT(DISTINCT marca||modello) FROM veicoli WHERE alimentazione = 'Elettrica' AND autonomia_elettrica > 0")
    batt = q("SELECT COUNT(DISTINCT marca||modello) FROM veicoli WHERE alimentazione = 'Elettrica' AND batteria > 0")
    print(f"Elettriche: {tot} modelli; consumo {cons}, autonomia WLTP {aut}, batteria {batt} ({len(righe)} righe a mano)")
