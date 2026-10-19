#!/usr/bin/env python3
"""Trasforma source/dati.xlsx nel database dell'app e lo prepara per la pubblicazione.

Passi: legge l'Excel -> controlla tutto -> costruisce il database -> se il contenuto è cambiato
sale di versione e scrive data/iviaggi_database.sqlite + data/manifest.json. In ogni caso scrive
data/status.json con l'esito in italiano (lo legge la pagina di aggiornamento).
Se c'è un errore NON cambia nulla di ciò che l'app scarica.

Uso:  python3 tools/build.py            (dalla cartella principale dell'archivio)
"""
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile

from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dbtools import REQUIRED, content_hash, load_schema, norm  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
XLSX = os.path.join(ROOT, "source", "dati.xlsx")
SCHEMA = os.path.join(ROOT, "source", "schema.sql")
DATA = os.path.join(ROOT, "data")
SCHEMA_VERSION = 1          # deve coincidere con Config.supportedDataSchema nell'app
FIRST_VERSION = 1           # versione della prima pubblicazione (uguale a Config.bundledDataVersion)
MAX_ERRORS = 40

FOREIGN = [  # (tabella, colonna, tabella riferita, colonna riferita, obbligatoria)
    ("aree_servizio", "id_autostrada", "autostrade", "id", False),
    ("aree_servizio", "id_casello_direzione", "caselli", "id", False),
    ("autostrade", "id_casello_inizio", "caselli", "id", False),
    ("autostrade", "id_casello_fine", "caselli", "id", False),
    ("tratte", "id_autostrada", "autostrade", "id", False),
    ("aree_colonnine", "id_area", "aree_servizio", "id", True),
    ("aree_colonnine", "id_gestore", "gestori_ricarica", "id", False),
    ("aree_marche_carburante", "id_area", "aree_servizio", "id", True),
    ("aree_marche_carburante", "id_marca", "marche_carburante", "id", True),
    ("aree_marche_ristorante", "id_area", "aree_servizio", "id", True),
    ("aree_marche_ristorante", "id_marca", "marche_ristorante", "id", True),
    ("aree_servizi", "id_area", "aree_servizio", "id", True),
    ("aree_servizi", "id_servizio", "servizi", "id", True),
    ("prezzi", "id_area", "aree_servizio", "id", True),
    ("tariffe_ricarica", "id_gestore", "gestori_ricarica", "id", True),
]
RANGES = [  # (tabella, colonna, minimo, massimo)
    ("aree_servizio", "lat", 35, 48), ("aree_servizio", "lon", 6, 19), ("aree_servizio", "km", -50, 2000), ("aree_servizio", "km_cippo", -5, 2000),
    ("caselli", "lat", 35, 48), ("caselli", "lon", 6, 19),
    ("aree_colonnine", "numero_colonnine", 1, 100), ("aree_colonnine", "potenza_kw", 1, 1000),
    ("aree_colonnine", "prezzo_kwh", 0.01, 2),
    ("prezzi", "prezzo", 0.3, 5),
    ("tariffe_ricarica", "potenza_min_kw", 0, 1000), ("tariffe_ricarica", "potenza_max_kw", 1, 1000),
    ("tariffe_ricarica", "prezzo_kwh", 0.01, 2), ("tariffe_ricarica", "prezzo_kwh_notte", 0.01, 2),
]
ALLOWED = [
    ("aree_colonnine", "tipo_corrente", {None, "AC", "DC"}),
    ("prezzi", "carburante", {"Benzina", "Gasolio", "GPL", "Metano"}),
    ("prezzi", "modalita", {"self", "servito"}),
    ("aree_servizio", "attiva_2026", {-1, 0, 1}),
    ("aree_servizio", "stato", {None, "aperta", "chiusa (temporaneamente)"}),
    ("autostrade", "tipo", {None, "autostrada", "tangenziale"}),
    ("tariffe_ricarica", "tipo_corrente", {"AC", "DC"}),
    ("tariffe_ricarica", "stato", {"verificato", "da verificare"}),
]
DATES = [("aree_colonnine", "data_aggiornamento"), ("tariffe_ricarica", "data_fonte")]


class Problems:
    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, msg):
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(msg)
        elif len(self.errors) == MAX_ERRORS:
            self.errors.append("… e altri errori (correggi questi e ricarica).")

    def warn(self, msg):
        if len(self.warnings) < 30:
            self.warnings.append(msg)


def convert(value, typ, where, pb):
    """Valore della cella -> valore da salvare. None = vuoto."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    if typ == "INTEGER":
        try:
            if isinstance(value, str):
                value = float(value.replace(",", "."))
            if isinstance(value, bool):
                return int(value)
            if float(value) != int(float(value)):
                raise ValueError
            return int(float(value))
        except (ValueError, OverflowError):
            pb.error(f"{where}: serve un numero intero, trovato «{value}».")
            return None
    if typ == "REAL":
        try:
            if isinstance(value, str):
                value = value.replace(",", ".")
            return float(value)
        except ValueError:
            pb.error(f"{where}: serve un numero, trovato «{value}».")
            return None
    # testo
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d") if (value.hour, value.minute, value.second) == (0, 0, 0) else value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def read_workbook(schema, pb):
    wb = load_workbook(XLSX, read_only=True, data_only=False)
    tables = {}
    for table, cols in schema.items():
        if table not in wb.sheetnames:
            if table in REQUIRED:
                pb.error(f"Manca il foglio «{table}» nell'Excel (non rinominare né cancellare i fogli).")
            tables[table] = []
            continue
        rows_iter = wb[table].iter_rows(values_only=True)
        header = next(rows_iter, None)
        if not header:
            pb.error(f"Il foglio «{table}» è vuoto: manca la riga delle intestazioni.")
            tables[table] = []
            continue
        names = [None if h is None else str(h).strip() for h in header]
        index = {}
        for i, h in enumerate(names):
            if h and not h.startswith("_"):
                if h in index:
                    pb.error(f"Foglio «{table}»: l'intestazione «{h}» compare due volte.")
                index[h] = i
        wanted = [c[0] for c in cols]
        for c in wanted:
            if c not in index:
                pb.error(f"Foglio «{table}»: manca la colonna «{c}» (non cambiare le intestazioni).")
        for h in index:
            if h not in wanted:
                pb.error(f"Foglio «{table}»: la colonna «{h}» non esiste nel database. Se è solo un appunto, metti _ davanti al nome.")
        out = []
        for rown, row in enumerate(rows_iter, start=2):
            if row is None:
                continue
            vals = [row[index[c]] if c in index and index[c] < len(row) else None for c in wanted]
            if all(v is None or (isinstance(v, str) and v.strip() == "") for v in vals):
                continue
            conv = [convert(v, cols[i][1], f"Foglio «{table}», riga {rown}, colonna «{wanted[i]}»", pb)
                    for i, v in enumerate(vals)]
            out.append((rown, conv))
        tables[table] = out
    wb.close()
    return tables


def assign_ids(schema, tables, pb):
    """Se in una riga nuova manca l'id (prima colonna 'id'), gli si dà il successivo."""
    for table, cols in schema.items():
        if not cols or cols[0][0] != "id":
            continue
        used = [r[1][0] for r in tables[table] if r[1][0] is not None]
        nxt = (max(used) if used else 0) + 1
        for rown, conv in tables[table]:
            if conv[0] is None:
                conv[0] = nxt
                pb.warn(f"Foglio «{table}», riga {rown}: id mancante, assegnato {nxt}.")
                nxt += 1


def validate(schema, tables, pb, prev_counts):
    pos = {t: {c[0]: i for i, c in enumerate(cols)} for t, cols in schema.items()}
    # id duplicati
    for table, cols in schema.items():
        if cols and cols[0][0] == "id":
            seen = {}
            for rown, conv in tables[table]:
                if conv[0] in seen:
                    pb.error(f"Foglio «{table}», riga {rown}: l'id {conv[0]} è già usato alla riga {seen[conv[0]]}.")
                seen[conv[0]] = rown
    # campi obbligatori
    for table, cols in schema.items():
        pass
    # riferimenti tra fogli
    ids = {t: {r[1][pos[t]["id"]] for r in tables[t]} for t in schema if "id" in pos[t]}
    for t, col, ref, refcol, required in FOREIGN:
        for rown, conv in tables[t]:
            v = conv[pos[t][col]]
            if v is None:
                if required:
                    pb.error(f"Foglio «{t}», riga {rown}: manca «{col}».")
            elif v not in ids[ref]:
                pb.error(f"Foglio «{t}», riga {rown}: «{col}» = {v} non esiste nel foglio «{ref}».")
    for t, col, lo, hi in RANGES:
        for rown, conv in tables[t]:
            v = conv[pos[t][col]]
            if v is not None and not (lo <= v <= hi):
                pb.error(f"Foglio «{t}», riga {rown}: «{col}» = {v} fuori dai valori attesi ({lo}–{hi}).")
    for t, col, allowed in ALLOWED:
        for rown, conv in tables[t]:
            v = conv[pos[t][col]]
            if v not in allowed:
                pb.error(f"Foglio «{t}», riga {rown}: «{col}» = «{v}» non è ammesso. Valori ammessi: {', '.join(str(a) for a in sorted(allowed, key=str) if a is not None)}.")
    for t, col in DATES:
        for rown, conv in tables[t]:
            v = conv[pos[t][col]]
            if v is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                pb.error(f"Foglio «{t}», riga {rown}: «{col}» deve essere una data AAAA-MM-GG, trovato «{v}».")
    # listino: campi obbligatori e fasce di potenza coerenti
    for rown, conv in tables.get("tariffe_ricarica", []):
        p = pos["tariffe_ricarica"]
        for col in ("potenza_min_kw", "potenza_max_kw", "prezzo_kwh", "fonte", "data_fonte"):
            if conv[p[col]] is None:
                pb.error(f"Foglio «tariffe_ricarica», riga {rown}: manca «{col}».")
        lo, hi = conv[p["potenza_min_kw"]], conv[p["potenza_max_kw"]]
        if lo is not None and hi is not None and lo > hi:
            pb.error(f"Foglio «tariffe_ricarica», riga {rown}: potenza_min_kw ({lo}) è maggiore di potenza_max_kw ({hi}).")
    # km ufficiale da mostrare (avviso): serve per "Ti trovi al km" e per il km delle aree
    for rown, conv in tables["aree_servizio"]:
        p = pos["aree_servizio"]
        if conv[p["attiva_2026"]] == 1 and conv[p["km"]] is not None and conv[p["km_cippo"]] is None:
            pb.warn(f"Foglio «aree_servizio», riga {rown}: manca «km_cippo» (il km del cartello). L'app mostrerà il valore di «km».")
    # colonnine doppie (avviso)
    seen = {}
    for rown, conv in tables["aree_colonnine"]:
        key = tuple(conv[pos["aree_colonnine"][c]] for c in ("id_area", "id_gestore", "tipo_corrente", "potenza_kw"))
        if key in seen:
            pb.warn(f"Foglio «aree_colonnine», righe {seen[key]} e {rown}: stessa area, gestore, corrente e potenza (forse doppia).")
        seen[key] = rown
    # conteggi minimi (stessi controlli dell'app) e calo improvviso
    active = sum(1 for _, c in tables["aree_servizio"] if c[pos["aree_servizio"]["attiva_2026"]] == 1)
    minimi = [("aree_servizio attive", active, 100), ("autostrade", len(tables["autostrade"]), 10),
              ("caselli", len(tables["caselli"]), 10)]
    for name, n, lo in minimi:
        if n < lo:
            pb.error(f"Troppo pochi dati: {name} = {n} (minimo {lo}). L'Excel è incompleto?")
    allow = os.environ.get("ALLOW_SHRINK") == "1"
    for name, now in (("aree_servizio", active), ("aree_colonnine", len(tables["aree_colonnine"])), ("prezzi", len(tables["prezzi"]))):
        before = prev_counts.get(name)
        if before and now < before * 0.9 and not allow:
            pb.error(f"Il foglio «{name}» è sceso da {before} a {now} righe (oltre il 10%). Se è voluto, avvia la pubblicazione a mano da GitHub con «allow_shrink».")
    return active


def build_db(schema, tables, path):
    con = sqlite3.connect(path)
    con.executescript(open(SCHEMA, encoding="utf-8").read())
    for table, cols in schema.items():
        names = [c[0] for c in cols]
        sql = f'INSERT INTO "{table}" ({",".join(chr(34)+n+chr(34) for n in names)}) VALUES ({",".join("?" for _ in names)})'
        for rown, conv in tables[table]:
            try:
                con.execute(sql, conv)
            except sqlite3.IntegrityError as e:
                con.close()
                raise ValueError(f"Foglio «{table}», riga {rown}: {e}") from e
    con.commit()
    con.close()


def table_counts(path, tables):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    out["aree_servizio_attive"] = con.execute("SELECT COUNT(*) FROM aree_servizio WHERE attiva_2026 = 1").fetchone()[0]
    con.close()
    return out


def diff_summary(old_path, new_path, schema):
    """Quante righe aggiunte, tolte, modificate per tabella rispetto alla versione pubblicata."""
    if not os.path.exists(old_path):
        return {}
    a, b = sqlite3.connect(f"file:{old_path}?mode=ro", uri=True), sqlite3.connect(f"file:{new_path}?mode=ro", uri=True)
    res = {}
    for t, cols in schema.items():
        names = ",".join(chr(34) + c[0] + chr(34) for c in cols)
        try:
            ra = [tuple(norm(v) for v in r) for r in a.execute(f'SELECT {names} FROM "{t}"')]
        except sqlite3.Error:
            ra = []
        rb = [tuple(norm(v) for v in r) for r in b.execute(f'SELECT {names} FROM "{t}"')]
        if cols and cols[0][0] == "id":
            da, db = {r[0]: r for r in ra}, {r[0]: r for r in rb}
            add, rem = len(set(db) - set(da)), len(set(da) - set(db))
            mod = sum(1 for k in set(da) & set(db) if da[k] != db[k])
        else:
            sa, sb = {}, {}
            for r in ra:
                sa[r] = sa.get(r, 0) + 1
            for r in rb:
                sb[r] = sb.get(r, 0) + 1
            add = sum(max(0, n - sa.get(r, 0)) for r, n in sb.items())
            rem = sum(max(0, n - sb.get(r, 0)) for r, n in sa.items())
            mod = 0
        if add or rem or mod:
            res[t] = {"aggiunte": add, "tolte": rem, "modificate": mod}
    return res


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    os.makedirs(DATA, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status = {"ok": False, "when": now, "changed": False, "message": "", "errors": [], "warnings": []}
    state_path = os.path.join(DATA, "state.json")
    manifest_path = os.path.join(DATA, "manifest.json")
    db_path = os.path.join(DATA, "iviaggi_database.sqlite")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    status["version"] = state.get("version", 0)
    pb = Problems()
    try:
        schema = load_schema(SCHEMA)
        if not os.path.exists(XLSX):
            pb.error("Non trovo source/dati.xlsx.")
            raise SystemExit
        tables = read_workbook(schema, pb)
        if not pb.errors:
            assign_ids(schema, tables, pb)
            validate(schema, tables, pb, state.get("counts", {}))
        tmp = None
        if not pb.errors:
            tmp = os.path.join(tempfile.mkdtemp(), "db.sqlite")
            build_db(schema, tables, tmp)
            check = sqlite3.connect(tmp)
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                pb.error("Il database costruito non supera il controllo di integrità.")
            check.close()
    except SystemExit:
        pass
    except ValueError as e:
        pb.error(str(e))
    except Exception as e:  # file Excel illeggibile, ecc.
        pb.error(f"Non riesco a leggere l'Excel: {e}")

    status["warnings"] = pb.warnings
    if pb.errors:
        status["errors"] = pb.errors
        status["message"] = f"Aggiornamento NON pubblicato: {len(pb.errors)} problemi da correggere. L'app continua a usare i dati precedenti."
        write_json(os.path.join(DATA, "status.json"), status)
        print(status["message"])
        for e in pb.errors:
            print(" -", e)
        sys.exit(1)

    # copia compatta e deterministica
    final = os.path.join(os.path.dirname(tmp), "final.sqlite")
    src = sqlite3.connect(tmp)
    src.execute("VACUUM INTO ?", (final,))
    src.close()
    new_hash = content_hash(final, schema)
    counts = table_counts(final, schema)
    if state.get("content_hash") == new_hash and os.path.exists(db_path):
        status.update(ok=True, changed=False, version=state["version"], counts=counts,
                      message=f"Nessuna modifica rispetto alla versione {state['version']}: non c'è nulla da pubblicare.")
        write_json(os.path.join(DATA, "status.json"), status)
        print(status["message"])
        return
    changes = diff_summary(db_path, final, schema)
    version = state["version"] + 1 if state.get("version") else FIRST_VERSION
    shutil.copyfile(final, db_path)
    data = open(db_path, "rb").read()
    manifest = {"version": version, "schema": SCHEMA_VERSION, "file": "iviaggi_database.sqlite",
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    write_json(manifest_path, manifest)
    write_json(state_path, {"version": version, "content_hash": new_hash, "counts": {
        "aree_servizio": counts["aree_servizio_attive"], "aree_colonnine": counts["aree_colonnine"], "prezzi": counts["prezzi"]},
        "published": now})
    status.update(ok=True, changed=True, version=version, counts=counts, changes=changes,
                  message=f"Pubblicata la versione {version}. Gli utenti la ricevono alla prossima apertura dell'app.")
    write_json(os.path.join(DATA, "status.json"), status)
    print(status["message"], changes)


if __name__ == "__main__":
    main()
