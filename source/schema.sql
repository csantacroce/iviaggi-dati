-- Struttura del database dell'app (versione dello schema: vedi SCHEMA in tools/build.py).
-- NON modificare a mano: se cambi le tabelle va aggiornata anche l'app.
-- 18/10/2026: id come chiave primaria nelle tabelle anagrafiche; aggiunta tabella tariffe_ricarica (listino per gestore).

CREATE TABLE aree_colonnine (
  id INTEGER PRIMARY KEY,
  id_area INTEGER NOT NULL REFERENCES aree_servizio(id),
  id_gestore INTEGER REFERENCES gestori_ricarica(id),
  numero_colonnine INTEGER,
  tipo_corrente TEXT,        -- AC / DC
  potenza_kw REAL,
  prezzo_kwh REAL,
  fonte TEXT,
  data_aggiornamento TEXT    -- AAAA-MM-GG
);

CREATE TABLE "aree_marche_carburante" (
"id_area" INTEGER,
  "id_marca" INTEGER
);

CREATE TABLE "aree_marche_ristorante" (
"id_area" INTEGER,
  "id_marca" INTEGER
);

CREATE TABLE "aree_servizi" (
"id_area" INTEGER,
  "id_servizio" INTEGER
);

CREATE TABLE "aree_servizio" (
"id" INTEGER PRIMARY KEY,
  "nome" TEXT,
  "lat" REAL,
  "lon" REAL,
  "id_autostrada" INTEGER,
  "id_casello_direzione" INTEGER,
  "km" REAL,
  "lato" TEXT
, attiva_2026 INTEGER DEFAULT 0, fonte_coordinate TEXT DEFAULT 'originale', stato TEXT default 'aperta', km_cippo REAL);

CREATE TABLE "autostrade" (
"id" INTEGER PRIMARY KEY,
  "sigla" TEXT,
  "tratta_europea" TEXT,
  "nome" TEXT,
  "id_casello_inizio" INTEGER,
  "id_casello_fine" INTEGER
, tipo TEXT default 'autostrada');

CREATE TABLE "caselli" (
"id" INTEGER PRIMARY KEY,
  "nome" TEXT,
  "lat" REAL,
  "lon" REAL
);

CREATE TABLE correzioni_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT DEFAULT (date('now')),
  tabella TEXT, id_riga INTEGER, campo TEXT, vecchio TEXT, nuovo TEXT, motivo TEXT);

CREATE TABLE "da_verificare" (
"tabella" TEXT,
  "id" INTEGER,
  "campo" TEXT,
  "problema" TEXT
);

CREATE TABLE gestori_ricarica (
  id INTEGER PRIMARY KEY,
  nome TEXT NOT NULL UNIQUE
);

CREATE TABLE "impianti_ministero" (
"id_area" INTEGER,
  "id_impianto" INTEGER,
  "bandiera_attuale" TEXT,
  "criterio" TEXT,
  "affidabilita" TEXT,
  "distanza_coordinate_km" REAL
);

CREATE TABLE info_aggiornamento (chiave TEXT, valore TEXT);

CREATE TABLE "marche_carburante" (
"id" INTEGER PRIMARY KEY,
  "nome" TEXT
);

CREATE TABLE "marche_ristorante" (
"id" INTEGER PRIMARY KEY,
  "nome" TEXT
);

CREATE TABLE "prezzi" (
"id_area" INTEGER,
  "id_impianto" INTEGER,
  "carburante" TEXT,
  "modalita" TEXT,
  "prezzo" REAL,
  "data_comunicazione" TEXT,
  "data_estrazione" TEXT
);

CREATE TABLE "servizi" (
"id" INTEGER PRIMARY KEY,
  "nome" TEXT
);

CREATE TABLE "tratte" (
"id" INTEGER PRIMARY KEY,
  "id_autostrada" INTEGER,
  "nome" TEXT
);

CREATE INDEX idx_colonnine_area ON aree_colonnine(id_area);

CREATE TABLE tariffe_ricarica (
  id INTEGER PRIMARY KEY,
  id_gestore INTEGER NOT NULL REFERENCES gestori_ricarica(id),
  tipo_corrente TEXT NOT NULL CHECK (tipo_corrente IN ('AC','DC')),
  potenza_min_kw REAL NOT NULL, potenza_max_kw REAL NOT NULL,
  prezzo_kwh REAL NOT NULL, prezzo_kwh_notte REAL,
  stato TEXT NOT NULL CHECK (stato IN ('verificato','da verificare')),
  fonte TEXT NOT NULL, data_fonte TEXT NOT NULL, note TEXT);

CREATE INDEX idx_marche_carb_area ON aree_marche_carburante(id_area);
CREATE INDEX idx_marche_rist_area ON aree_marche_ristorante(id_area);
CREATE INDEX idx_servizi_area ON aree_servizi(id_area);
CREATE INDEX idx_prezzi_area ON prezzi(id_area);
CREATE INDEX idx_tariffe_gestore ON tariffe_ricarica(id_gestore);

CREATE TABLE tassi_cambio (
  valuta TEXT,
  per_euro REAL,
  data TEXT,
  fonte TEXT
);
