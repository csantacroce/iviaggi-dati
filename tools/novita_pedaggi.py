#!/usr/bin/env python3
"""Sorveglianza delle nuove fonti aperte sui pedaggi (aggiunta il 15/11/2026).

La delibera ART 132/2024 (Misura 3.2) prevede che i pedaggi aggiornati per ogni percorso
tra ingresso e uscita, per classe di veicolo, siano resi disponibili in formato aperto
nel Portale dati dell'Autorità di regolazione dei trasporti. Al 14/11/2026 il catalogo
non li contiene ancora.

Questo script legge il catalogo open data dell'Autorità (DCAT, catalog.rdf) e cerca dataset
sui pedaggi o sulle tariffe autostradali. Scrive in novita_pedaggi.md una riga per ogni
dataset nuovo, con un identificativo stabile ("novita: <firma>"): il flusso di GitHub apre una
segnalazione (etichetta "novita-dati") e, grazie alla firma, non segnala due volte lo stesso dataset
(GIA_SEGNALATI = file con il testo delle segnalazioni già aperte).

Non fa mai fallire la pubblicazione: se il catalogo non risponde scrive solo un avviso nel log.

Uso:  python3 tools/novita_pedaggi.py            (ricerca vera)
      NOVITA_PROVA=1 python3 tools/novita_pedaggi.py   (prova: segnala anche il dataset già esistente
                                                        "La rete autostradale in concessione", per provare
                                                        la segnalazione; Actions → Run workflow → "Prova la
                                                        sorveglianza dei pedaggi aperti ART")
"""
import hashlib
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
USCITA = os.path.join(ROOT, "novita_pedaggi.md")
CATALOGO = "https://bdt.autorita-trasporti.it/catalog.rdf"
PORTALE = "https://bdt.autorita-trasporti.it/rapporto/autostrade/"

# "pedaggi" al plurale: "a pedaggio" (singolare) compare già nelle descrizioni della rete e non va segnalato
CERCA = re.compile(r"\bpedaggi\b|tariff[ae]\s+(di\s+pedaggio|autostrad\w*|unitari\w*|chilometric\w*)|"
                   r"matric\w*\s+(origine|o/?d)\b|origine.{0,5}destinazione|\btolls?\b", re.I)
PROVA = os.environ.get("NOVITA_PROVA") == "1"
CERCA_PROVA = re.compile(r"rete autostradale in concessione", re.I)

RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"


def testo(el, nome):
    return " ".join((x.text or "").strip() for x in el.iter() if x.tag.endswith("}" + nome) and x.text)


def main():
    open(USCITA, "w").close()
    try:
        req = urllib.request.Request(CATALOGO, headers={"User-Agent": "iVIAGGI-controllo/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            radice = ET.fromstring(r.read())
    except Exception as e:  # il catalogo ogni tanto non risponde: non è un problema dell'app
        print(f"Avviso: catalogo ART non raggiungibile o non leggibile ({e}). Riprovo al prossimo giro.")
        return 0

    insiemi = [el for el in radice.iter() if el.tag.endswith("}Dataset")]
    print(f"Catalogo ART: {len(insiemi)} dataset")
    gia = ""
    if os.environ.get("GIA_SEGNALATI") and os.path.exists(os.environ["GIA_SEGNALATI"]):
        gia = open(os.environ["GIA_SEGNALATI"], encoding="utf-8").read()
    trovati, vecchi = [], 0
    for d in insiemi:
        titolo = testo(d, "title")
        descr = testo(d, "description")
        vero = CERCA.search(titolo + " " + descr)
        if not vero and not (PROVA and CERCA_PROVA.search(titolo)):
            continue
        if vero:
            print(f"Corrisponde ({vero.group(0)!r}): {titolo}")
        ident = d.get(RDF + "about") or titolo
        link = sorted({v for x in d.iter() for k, v in x.attrib.items()
                       if k == RDF + "resource" and re.search(r"\.(csv|json|xlsx?|zip|xml)$|download|dataset", v, re.I)})
        firma = hashlib.sha256(ident.encode()).hexdigest()[:12]
        if f"novita: {firma}" in gia:
            vecchi += 1
            continue
        trovati.append(f"- **{titolo.strip() or ident}** <!-- novita: {firma} -->\n"
                       f"  {descr.strip()[:400]}\n"
                       + "".join(f"  - {u}\n" for u in link[:6]))
    with open(USCITA, "w") as f:
        f.write("\n".join(trovati))
    print(f"Dataset sui pedaggi: {len(trovati)} nuovi, {vecchi} già segnalati in passato")
    for t in trovati:
        print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
