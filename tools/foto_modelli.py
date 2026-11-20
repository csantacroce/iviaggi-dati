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
LARGHEZZA = 1280        # originale scaricato da Commons (per un ritaglio pulito); la foto pubblicata è 480x300
# Scelte a mano quando la ricerca automatica prende una foto sbagliata: "Marca|Modello": "Nome del file su Commons"
SCELTE = {}
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
    return [("en", t) for t in en] + [("it", t) for t in it]


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


# Nomi da cercare su Commons quando quello del catalogo non basta (gruppi ADEME, nomi francesi).
NOMI_COMMONS = {
    "Mini|Mini": "Mini Cooper", "BMW|Serie X": "BMW X3", "BMW|Serie Z": "BMW Z4", "MG|MG ZS": "MG ZS EV",
    "Mercedes-Benz|Classe Eqa": "Mercedes-Benz EQA", "Mercedes-Benz|Classe Eqb": "Mercedes-Benz EQB",
    "Hyundai|I 10": "Hyundai i10", "Hyundai|I 30": "Hyundai i30", "Nissan|LEAF 40kWh": "Nissan Leaf",
    "Nissan|LEAF 62kWh": "Nissan Leaf", "Honda|Honda E": "Honda e", "Audi|A3 40 Tfsie": "Audi A3 Sportback",
    "Audi|A3 45 Tfsie": "Audi A3 Sportback", "Citroën|Jumpy Space Tourer": "Citroën SpaceTourer",
    "Peugeot|Expert Traveller": "Peugeot Traveller", "DS|DS 9": "DS 9",
}
SCARTA = re.compile(r"interior|innenraum|innen|interieur|cockpit|dashboard|armaturen|engine|motor\b|motorraum|"
                    r"rear|heck|back\b|badge|logo|emblem|wheel|felge|rad\b|seat|sitz|lenkrad|steering|detail|"
                    r"light|leuchte|scheinwerfer|trunk|kofferraum|grill|tail|collage|montage|mosaic|"
                    r"\s[-–]\s?r\b|\(r\)|_r_|\bri\b|mirror|spiegel|key|schlüssel|display|screen|charging port", re.I)
FRONTE = re.compile(r"front|vorn|\s[-–]\s?f\b|\(f\)|frontansicht|vorderansicht|\bfl\b", re.I)


def nomi_da_cercare(marca, modello):
    """"Mazda|Mazda CX-5" → "Mazda CX-5"; "BMW|Serie 3" → "BMW 3 Series"; "Porsche|911 Carrera 4 Gts" → … "Porsche 911"."""
    chiave = f"{marca}|{modello}"
    if chiave in NOMI_COMMONS:
        return [NOMI_COMMONS[chiave]]
    m = modello
    if norm(m).startswith(norm(marca) + " "):
        m = m[len(marca):].strip()
    nomi = []
    r = re.match(r"^Serie (\w+)$", m, re.I)
    if r:
        nomi.append(f"{marca} {r.group(1)} Series")
    r = re.match(r"^Classe (\w+)$", m, re.I)
    if r:
        nomi.append(f"{marca} {r.group(1).upper()}-Class")
    parole = m.split()
    for n in range(len(parole), 0, -1):          # 911 Carrera 4 Gts → 911 Carrera 4 → 911 Carrera → 911
        if n < len(parole) and parole[n - 1].lower() in ("serie", "classe", "e-tech", "plug-in", "space"):
            continue                             # mai "BMW Serie" o "Renault Megane E-Tech" da solo
        nomi.append(f"{marca} {' '.join(parole[:n])}")
    return list(dict.fromkeys(nomi))


def anno(meta, titolo):
    for testo in ((meta.get("DateTimeOriginal", {}).get("value") or ""), titolo):
        r = re.search(r"(19[89]\d|20[0-3]\d)", re.sub(r"<[^>]+>", "", testo))
        if r:
            return int(r.group(1))
    return None


def cerca_commons(nome):
    """Foto più recente e frontale di Commons con "nome" nel titolo del file; None se non ce n'è una adatta."""
    url = ("https://commons.wikimedia.org/w/api.php?action=query&format=json&generator=search&gsrnamespace=6"
           "&gsrlimit=50&gsrsearch=" + urllib.parse.quote(f'intitle:"{nome}" filetype:bitmap')
           + f"&prop=imageinfo&iiprop=url|extmetadata|mime|size&iiurlwidth={LARGHEZZA}"
           + "&iiextmetadatafilter=LicenseShortName|LicenseUrl|Artist|DateTimeOriginal|NonFree")
    r = leggi(url)
    migliori = []
    for p in r.get("query", {}).get("pages", {}).values():
        if not p.get("imageinfo"):
            continue
        info = p["imageinfo"][0]
        titolo = p.get("title", "")[5:]
        meta = info.get("extmetadata", {})
        lic = (meta.get("LicenseShortName", {}).get("value") or "").strip()
        w, h = info.get("width", 0), info.get("height", 1)
        if (info.get("mime") not in ("image/jpeg", "image/png") or meta.get("NonFree", {}).get("value")
                or not LIBERE.match(lic) or w < 1000 or not 1.25 <= w / h <= 2.1 or SCARTA.search(titolo)):
            continue
        if norm(nome) not in norm(titolo):
            continue
        a = anno(meta, titolo)
        punti = (a or 2005) + (3 if FRONTE.search(titolo) else 0)
        autore = re.sub(r"<[^>]+>", "", html.unescape(meta.get("Artist", {}).get("value") or "")).strip()
        migliori.append((punti, {"licenza": lic, "url_licenza": meta.get("LicenseUrl", {}).get("value"),
                                 "autore": autore or "sconosciuto", "file_commons": "File:" + titolo,
                                 "pagina_commons": info.get("descriptionurl"), "anno": a,
                                 "miniatura": info.get("thumburl") or info.get("url"), "mime": info.get("mime")}))
    if not migliori:
        return None
    migliori.sort(key=lambda t: -t[0])
    punti, scelta = migliori[0]
    return scelta if (scelta["anno"] or 0) >= 2015 else None


def trova(marca, modello):
    # 0) scelta a mano
    scelta = SCELTE.get(f"{marca}|{modello}")
    if scelta:
        lic = licenza_commons(scelta.replace("File:", ""))
        if lic:
            lic["cercato"] = "scelta a mano"
            return lic, None
    # 1) Commons: la foto più recente con il nome del modello
    for nome in nomi_da_cercare(marca, modello):
        lic = cerca_commons(nome)
        if lic:
            lic["cercato"] = nome
            return lic, None
    # 2) riserva: immagine principale della pagina Wikipedia
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


def prepara(url, nome_file):
    """Scarica l'originale e lo mette in stile studio (studio_foto.py). Se il ritaglio non è disponibile
    (rembg non installato) o non riesce, pubblica la foto ridotta così com'è. Restituisce lo stile usato."""
    import tempfile
    dati = leggi(url, binario=True)
    dest = os.path.join(CARTELLA, nome_file)
    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as t:
        t.write(dati)
        sorgente = t.name
    try:
        try:
            import studio_foto
            if studio_foto.studio(sorgente, dest):
                return studio_foto.STILE
        except Exception as e:
            print(f"Stile studio non riuscito per {nome_file}: {e}")
        try:
            from PIL import Image
            im = Image.open(sorgente).convert("RGB")
            im.thumbnail((480, 300))
            im.save(dest, quality=88)
        except Exception:
            with open(dest, "wb") as f:
                f.write(dati)
        return "originale"
    finally:
        os.remove(sorgente)


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
    try:
        import studio_foto
        stile_attuale = studio_foto.STILE
        import rembg  # noqa: F401  (solo per sapere se il ritaglio è disponibile)
    except Exception:
        stile_attuale = None
    attuali = {f"{m}|{n}" for m, n in modelli}
    for chiave in [k for k in registro if k not in attuali]:   # modelli non più nel catalogo
        del registro[chiave]
    rifatte = 0
    for marca, modello in modelli:
        chiave = f"{marca}|{modello}"
        voce = registro.get(chiave)
        if voce and not forza and voce.get("file") and os.path.exists(os.path.join(CARTELLA, voce["file"])):
            if stile_attuale and voce.get("stile") != stile_attuale and not SCELTE.get(chiave):
                # stessa foto di Commons, rifatta nello stile attuale dall'originale grande
                try:
                    lic = licenza_commons(voce["file_commons"].replace("File:", ""))
                    if lic:
                        nuovo = f"{slug(marca)}__{slug(modello)}.jpg"
                        stile = prepara(lic.pop("miniatura"), nuovo)
                        lic.pop("mime", None)
                        if nuovo != voce["file"] and os.path.exists(os.path.join(CARTELLA, voce["file"])):
                            os.remove(os.path.join(CARTELLA, voce["file"]))
                        registro[chiave] = {**voce, **lic, "file": nuovo, "stile": stile}
                        rifatte += 1
                except Exception as e:
                    errori += 1
                    print(f"Foto {marca} {modello} (stile): {e}")
            if not SCELTE.get(chiave) or voce.get("cercato") == "scelta a mano":
                continue
        if voce and not forza and not SCELTE.get(chiave):
            if not voce.get("file"):
                provato = dt.date.fromisoformat(voce.get("provato", "2000-01-01"))
                if (oggi - provato).days < GIORNI_RIPROVA:
                    continue
        try:
            lic, motivo = trova(marca, modello)
            if lic:
                lic.pop("mime", None)
                nome_file = f"{slug(marca)}__{slug(modello)}.jpg"
                stile = prepara(lic.pop("miniatura"), nome_file)
                registro[chiave] = {"file": nome_file, "provato": oggi.isoformat(), "stile": stile, **lic}
                nuove += 1
            else:
                registro[chiave] = {"file": None, "provato": oggi.isoformat(), "motivo": motivo}
        except Exception as e:   # rete, Wikimedia lento: si riprova al giro dopo
            errori += 1
            print(f"Foto {marca} {modello}: {e}")
            if errori >= 15:
                print("Foto: troppi errori di rete, mi fermo e riprovo al prossimo giro")
                break
    usati = {v.get("file") for v in registro.values()}
    for f in os.listdir(CARTELLA):
        if f not in usati and f not in ("crediti.html", ".gitkeep"):
            os.remove(os.path.join(CARTELLA, f))
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
    riassunto = {"modelli": len(modelli), "con_foto": con_foto, "nuove": nuove, "rifatte_in_stile": rifatte,
                 "stile": stile_attuale or "originale (rembg non disponibile)", "errori": errori}
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
indicate qui sotto. Sono state modificate: l'auto è ritagliata e messa su uno sfondo uniforme con l'ombra, e la foto è ridotta di dimensione. I marchi appartengono ai rispettivi proprietari.</p>
<table><tr><td><b>Modello</b></td><td><b>File</b></td><td><b>Autore</b></td><td><b>Licenza</b></td></tr>
{chr(10).join(righe)}
</table></body></html>
"""
    with open(os.path.join(CARTELLA, "crediti.html"), "w", encoding="utf-8") as f:
        f.write(pagina_html)
