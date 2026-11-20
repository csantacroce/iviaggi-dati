#!/usr/bin/env python3
"""Foto dei modelli per l'elenco Modello delle Impostazioni veicolo (20/11/2026, richiesta di Carlo).

Fonte: Wikimedia Commons, solo file con licenza libera (CC0, pubblico dominio, CC BY, CC BY-SA).
Per ogni marca e modello del catalogo (tabella veicoli) si cerca la pagina di Wikipedia (prima italiana, poi
inglese) e se ne prende l'immagine principale; poi si chiede a Commons licenza e autore. Le foto non libere
(es. "fair use" caricate solo su Wikipedia) o senza licenza leggibile si scartano: il modello resta senza foto.

- Foto in site/foto_modelli/<marca>__<modello>.<ext> (larghezza 480 px), pubblicate con il sito su GitHub Pages:
  l'app le scarica quando apre l'elenco e le tiene in memoria sul telefono.
- Registro in source/foto_modelli.json (pagina, file Commons, autore, licenza, data). Le pagine dei crediti
  (site/foto_modelli/crediti.html) si rigenerano a ogni giro: la licenza CC BY-SA chiede di citare l'autore.
- Si cercano solo i modelli nuovi; quelli senza foto si riprovano dopo 30 giorni. Con il file
  source/aggiorna_foto si riprova tutto.
- Nella tabella veicoli la colonna "foto" dice all'app quale file c'è (NULL = nessuna foto).
"""
import datetime as dt
import html
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CARTELLA = os.path.join(ROOT, "site", "foto_modelli")
REGISTRO = os.path.join(ROOT, "source", "foto_modelli.json")
TRIGGER = os.path.join(ROOT, "source", "aggiorna_foto")
GIORNI_RIPROVA = 30
LARGHEZZA = 480
AGENTE = "iVIAGGI-dati/1.0 (https://github.com/csantacroce/iviaggi-dati; app iVIAGGI Autostrade Italiane)"
LIBERE = re.compile(r"^(cc0|public domain|pd|cc[ -]by(-sa)?([ -]\d(\.\d)?)?.*)$", re.I)


def slug(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return "_".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def norm(t):
    return " ".join(slug(t).split("_"))


def leggi(url, binario=False):
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Accept": "*/*" if binario else "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        dati = r.read()
    time.sleep(0.25)   # gentile con i server di Wikimedia
    return dati if binario else json.loads(dati)


def titoli(marca, modello):
    """Titoli da provare: "Toyota Yaris Cross"; per l'inglese anche "BMW 4 Series", "Mercedes-Benz A-Class"."""
    base = f"{marca} {modello}"
    it = [base]
    en = [base]
    m = re.match(r"^Serie (\w+)$", modello, re.I)
    if m:
        en.insert(0, f"{marca} {m.group(1)} Series")
    m = re.match(r"^Classe (\w+)$", modello, re.I)
    if m:
        en.insert(0, f"{marca} {m.group(1)}-Class")
    return [("it", t) for t in it] + [("en", t) for t in en]


def pagina(lingua, titolo, marca):
    """Riassunto della pagina (REST di Wikipedia); None se non c'è, è una disambigua o non parla della marca."""
    url = f"https://{lingua}.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(titolo.replace(" ", "_"))
    try:
        p = leggi(url)
    except urllib.error.HTTPError:
        return None
    if p.get("type") != "standard" or not p.get("originalimage"):
        return None
    testo = norm(p.get("title", "") + " " + p.get("description", "") + " " + p.get("extract", "")[:300])
    if norm(marca).split()[0] not in testo:
        return None
    return p


def cerca(lingua, testo, marca):
    """Ricerca libera (opensearch) quando il titolo esatto non esiste."""
    url = (f"https://{lingua}.wikipedia.org/w/api.php?action=opensearch&format=json&limit=3&namespace=0&search="
           + urllib.parse.quote(testo))
    try:
        r = leggi(url)
    except Exception:
        return None
    for t in (r[1] if len(r) > 1 else []):
        p = pagina(lingua, t, marca)
        if p:
            return p
    return None


def licenza_commons(nome_file):
    """Licenza, autore e miniatura da Commons; None se il file non è su Commons o non è libero."""
    url = ("https://commons.wikimedia.org/w/api.php?action=query&format=json&prop=imageinfo"
           f"&iiprop=url|extmetadata|mime&iiurlwidth={LARGHEZZA}&titles=" + urllib.parse.quote("File:" + nome_file))
    r = leggi(url)
    for p in r.get("query", {}).get("pages", {}).values():
        if "missing" in p or not p.get("imageinfo"):
            return None
        info = p["imageinfo"][0]
        meta = info.get("extmetadata", {})
        if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            return None
        lic = (meta.get("LicenseShortName", {}).get("value") or "").strip()
        if meta.get("NonFree", {}).get("value") or not LIBERE.match(lic):
            return None
        autore = re.sub(r"<[^>]+>", "", html.unescape(meta.get("Artist", {}).get("value") or "")).strip()
        return {"licenza": lic, "url_licenza": meta.get("LicenseUrl", {}).get("value"), "autore": autore or "sconosciuto",
                "file_commons": "File:" + nome_file, "pagina_commons": info.get("descriptionurl"),
                "miniatura": info.get("thumburl") or info.get("url"), "mime": info.get("mime")}
    return None


def nome_da_url(url):
    """https://upload.wikimedia.org/wikipedia/commons/a/ab/Nome.jpg → Nome.jpg (anche dalle miniature)."""
    parti = urllib.parse.unquote(urllib.parse.urlparse(url).path).split("/")
    if "thumb" in parti:
        return parti[parti.index("thumb") + 3]
    return parti[-1]


def trova(marca, modello):
    provati = []
    for lingua, titolo in titoli(marca, modello):
        p = pagina(lingua, titolo, marca)
        provati.append(f"{lingua}:{titolo}")
        if p:
            break
    else:
        p = cerca("it", f"{marca} {modello}", marca) or cerca("en", f"{marca} {modello}", marca)
        if not p:
            return None, "nessuna pagina (" + ", ".join(provati) + ")"
    nome = nome_da_url(p["originalimage"]["source"])
    lic = licenza_commons(nome)
    if not lic:
        return None, f"immagine non libera o non su Commons ({nome})"
    lic["pagina_wikipedia"] = p.get("content_urls", {}).get("desktop", {}).get("page")
    return lic, None


def aggiorna(con):
    """Cerca le foto mancanti, aggiorna il registro e la colonna veicoli.foto. Restituisce un riassunto."""
    os.makedirs(CARTELLA, exist_ok=True)
    registro = {}
    if os.path.exists(REGISTRO):
        registro = json.load(open(REGISTRO, encoding="utf-8"))
    forza = os.path.exists(TRIGGER)
    oggi = dt.date.today()
    modelli = con.execute("SELECT DISTINCT marca, modello FROM veicoli ORDER BY marca, modello").fetchall()
    nuove = errori = 0
    for marca, modello in modelli:
        chiave = f"{marca}|{modello}"
        voce = registro.get(chiave)
        if voce and not forza:
            if voce.get("file") and os.path.exists(os.path.join(CARTELLA, voce["file"])):
                continue
            if not voce.get("file"):
                provato = dt.date.fromisoformat(voce.get("provato", "2000-01-01"))
                if (oggi - provato).days < GIORNI_RIPROVA:
                    continue
        try:
            lic, motivo = trova(marca, modello)
            if lic:
                est = {"image/png": "png", "image/webp": "webp"}.get(lic.pop("mime"), "jpg")
                nome_file = f"{slug(marca)}__{slug(modello)}.{est}"
                dati = leggi(lic.pop("miniatura"), binario=True)
                with open(os.path.join(CARTELLA, nome_file), "wb") as f:
                    f.write(dati)
                registro[chiave] = {"file": nome_file, "provato": oggi.isoformat(), **lic}
                nuove += 1
            else:
                registro[chiave] = {"file": None, "provato": oggi.isoformat(), "motivo": motivo}
        except Exception as e:   # rete, Wikimedia lento: si riprova al giro dopo
            errori += 1
            print(f"Foto {marca} {modello}: {e}")
            if errori >= 15:
                print("Foto: troppi errori di rete, mi fermo e riprovo al prossimo giro")
                break
    if forza:
        try:
            os.remove(TRIGGER)
        except OSError:
            pass
    tmp = REGISTRO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registro, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, REGISTRO)
    # colonna foto nella tabella veicoli
    con.execute("ALTER TABLE veicoli ADD COLUMN foto TEXT")
    for chiave, voce in registro.items():
        if voce.get("file") and os.path.exists(os.path.join(CARTELLA, voce["file"])):
            marca, modello = chiave.split("|", 1)
            con.execute("UPDATE veicoli SET foto = ? WHERE marca = ? AND modello = ?", (voce["file"], marca, modello))
    con.commit()
    scrivi_crediti(registro)
    con_foto = con.execute("SELECT COUNT(DISTINCT marca || modello) FROM veicoli WHERE foto IS NOT NULL").fetchone()[0]
    riassunto = {"modelli": len(modelli), "con_foto": con_foto, "nuove": nuove, "errori": errori}
    print("Foto dei modelli:", json.dumps(riassunto))
    return riassunto


def scrivi_crediti(registro):
    """Pagina pubblica con autore e licenza di ogni foto (attribuzione chiesta da CC BY / CC BY-SA)."""
    righe = []
    for chiave in sorted(registro):
        v = registro[chiave]
        if not v.get("file"):
            continue
        marca, modello = chiave.split("|", 1)
        lic = html.escape(v["licenza"])
        if v.get("url_licenza"):
            lic = f'<a href="{html.escape(v["url_licenza"])}">{lic}</a>'
        righe.append(f'<tr><td>{html.escape(marca)} {html.escape(modello)}</td>'
                     f'<td><a href="{html.escape(v.get("pagina_commons") or "")}">{html.escape(v["file_commons"][5:])}</a></td>'
                     f'<td>{html.escape(v["autore"])}</td><td>{lic}</td></tr>')
    pagina_html = f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>iVIAGGI – crediti delle foto dei modelli</title>
<style>body{{font:15px -apple-system,Helvetica,Arial,sans-serif;margin:16px;color:#222;background:#fff}}
table{{border-collapse:collapse;width:100%}}td{{border-bottom:1px solid #ddd;padding:6px 8px;vertical-align:top}}
@media (prefers-color-scheme:dark){{body{{background:#111;color:#eee}}td{{border-color:#333}}a{{color:#6af}}}}</style></head><body>
<h1>Foto dei modelli</h1>
<p>Le foto delle automobili mostrate nell'app iVIAGGI Autostrade Italiane vengono da Wikimedia Commons, con le licenze
indicate qui sotto. Sono state ridotte di dimensione. I marchi appartengono ai rispettivi proprietari.</p>
<table><tr><td><b>Modello</b></td><td><b>File</b></td><td><b>Autore</b></td><td><b>Licenza</b></td></tr>
{chr(10).join(righe)}
</table></body></html>
"""
    with open(os.path.join(CARTELLA, "crediti.html"), "w", encoding="utf-8") as f:
        f.write(pagina_html)
