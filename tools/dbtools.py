"""Funzioni comuni: lettura dello schema, confronto del contenuto."""
import hashlib
import sqlite3

# Tabelle che l'app legge: devono esserci sempre nel foglio Excel.
REQUIRED = ["aree_servizio", "autostrade", "caselli", "tratte", "marche_carburante", "marche_ristorante",
            "servizi", "aree_marche_carburante", "aree_marche_ristorante", "aree_servizi",
            "gestori_ricarica", "aree_colonnine", "prezzi", "tariffe_ricarica"]


def load_schema(schema_path):
    """Crea un database vuoto in memoria dallo schema e ne legge tabelle e colonne.
    Restituisce {tabella: [(nome, tipo, è_chiave)]}."""
    con = sqlite3.connect(":memory:")
    con.executescript(open(schema_path, encoding="utf-8").read())
    tables = {}
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        tables[name] = [(r[1], (r[2] or "").upper(), bool(r[5])) for r in con.execute(f'PRAGMA table_info("{name}")')]
    con.close()
    return tables


def norm(v):
    """Excel non distingue testo vuoto e vuoto: li trattiamo uguali. I numeri interi e reali con lo stesso valore sono uguali."""
    if v is None or v == "":
        return None
    if isinstance(v, float):
        v = round(v, 9)  # Excel conserva circa 15 cifre: differenze sotto il miliardesimo sono irrilevanti
        if v == int(v):
            return int(v)
    return v


def content_hash(path, tables):
    """Impronta del contenuto logico (non del file): se due database hanno gli stessi dati, è uguale."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    h = hashlib.sha256()
    for t in sorted(tables):
        cols = [c[0] for c in tables[t]]
        rows = con.execute(f'SELECT {",".join(chr(34)+c+chr(34) for c in cols)} FROM "{t}"').fetchall()
        rows = sorted((tuple(norm(v) for v in r) for r in rows), key=lambda r: tuple((x is None, str(x)) for x in r))
        h.update(t.encode())
        for r in rows:
            h.update(repr(r).encode())
    con.close()
    return h.hexdigest()
