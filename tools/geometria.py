#!/usr/bin/env python3
"""Tabella "geometria" del database (16/11/2026): il tracciato reale di ogni carreggiata.

Per ogni autostrada e senso di marcia (id_autostrada, id_casello_direzione) segue sui tracciati
OpenStreetMap (source/osm_autostrade.json.gz, vedi osm.py) la carreggiata giusta dal casello di
partenza, attraverso tutte le aree di servizio in ordine di km, fino al casello di arrivo.
Il percorso è calcolato sulla rete a sensi unici (così prende la carreggiata del senso di marcia),
preferendo le vie con la stessa sigla dell'autostrada.

Una riga per tratto continuo:
  punti   polyline di Google (precisione 1e-5) del tracciato semplificato (~8 m)
  aree    "id_area:metri;…"  posizione di ogni area lungo il tracciato (metri dall'inizio del tratto)
  cippi   "metri:km;…"       km del cartello in alcuni punti (dalle aree), per dire "ti trovi al km …"
L'app trova la carreggiata più vicina e coerente con la direzione di marcia, misura i metri lungo
il tracciato e da lì ricava i km mancanti alle aree successive.

Uso da solo (prova):  python3 tools/geometria.py data/iviaggi_database.sqlite
"""
import heapq
import math
import os
import sqlite3
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from osm import decodifica, codifica, leggi_cache, CACHE  # noqa: E402

KX0 = 111_320.0
KY = 110_540.0
CELLA = 0.01           # griglia dell'indice (gradi)
RAGGIO_AREA = 600.0    # m: carreggiate candidate attorno a un'area
RAGGIO_CASELLO = 3000.0
TOLL_RDP = 8.0         # m: semplificazione
SCHEMA = """CREATE TABLE IF NOT EXISTS geometria (
  id INTEGER PRIMARY KEY,
  id_autostrada INTEGER NOT NULL,
  id_casello_direzione INTEGER NOT NULL,
  parte INTEGER NOT NULL,
  lunghezza_m REAL,
  punti TEXT NOT NULL,
  aree TEXT,
  cippi TEXT
);
CREATE INDEX IF NOT EXISTS idx_geometria_chiave ON geometria(id_autostrada, id_casello_direzione);"""


def dist(a, b):
    kx = KX0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[1] - b[1]) * kx, (a[0] - b[0]) * KY)


def norm_ref(r):
    return [x.replace(" ", "").upper() for x in r.split(";") if x.strip()]


class Rete:
    def __init__(self, vie):
        self.idx = {}
        self.pt = []
        self.out = defaultdict(list)      # u -> [(v, lunghezza, via)]
        self.vie = []
        for vid, hw, ref, oneway, name, junction, poly in vie:
            pts = decodifica(poly)
            link = hw.endswith("_link")
            ow = oneway in ("yes", "true", "1") or (oneway not in ("no", "false", "0", "-1") and
                                                     (hw in ("motorway", "motorway_link") or junction in ("roundabout", "circular")))
            rev = oneway == "-1"
            w = len(self.vie)
            self.vie.append({"id": vid, "hw": hw, "ref": set(norm_ref(ref)), "link": link, "name": name})
            ids = [self._nodo(p) for p in pts]
            for a, b in zip(ids, ids[1:]):
                if a == b:
                    continue
                L = dist(self.pt[a], self.pt[b])
                if rev:
                    self.out[b].append((a, L, w))
                else:
                    self.out[a].append((b, L, w))
                    if not ow:
                        self.out[b].append((a, L, w))
        # indice a griglia dei tratti (u, v, via)
        self.griglia = defaultdict(list)
        for u, lst in self.out.items():
            for v, L, w in lst:
                a, b = self.pt[u], self.pt[v]
                for i in range(int(min(a[0], b[0]) / CELLA), int(max(a[0], b[0]) / CELLA) + 1):
                    for j in range(int(min(a[1], b[1]) / CELLA), int(max(a[1], b[1]) / CELLA) + 1):
                        self.griglia[(i, j)].append((u, v, L, w))

    def _nodo(self, p):
        k = (round(p[0] * 1e6), round(p[1] * 1e6))
        i = self.idx.get(k)
        if i is None:
            i = self.idx[k] = len(self.pt)
            self.pt.append(p)
        return i

    def vicini(self, p, raggio, ammessi=None):
        """Tratti entro raggio: [(d, u, v, t, punto proiettato, via)], il più vicino per ogni tratto."""
        r = raggio / KY / CELLA + 1
        ci, cj = int(p[0] / CELLA), int(p[1] / CELLA)
        n = int(math.ceil(r))
        visti, res = set(), []
        kx = KX0 * math.cos(math.radians(p[0]))
        for i in range(ci - n, ci + n + 1):
            for j in range(cj - 2 * n, cj + 2 * n + 1):
                for u, v, L, w in self.griglia.get((i, j), ()):
                    if (u, v) in visti:
                        continue
                    visti.add((u, v))
                    if ammessi and not ammessi(self.vie[w]):
                        continue
                    a, b = self.pt[u], self.pt[v]
                    ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * KY
                    bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * KY
                    dx, dy = bx - ax, by - ay
                    l2 = dx * dx + dy * dy
                    t = 0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
                    d = math.hypot(ax + t * dx, ay + t * dy)
                    if d <= raggio:
                        q = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
                        res.append((d, u, v, t, q, w))
        res.sort(key=lambda x: x[0])
        return res


def percorso(rete, sorgenti, destinazioni, refs, limite):
    """A* da più partenze a più arrivi sulla rete orientata.
    sorgenti/destinazioni: [(costo_iniziale, u, v, t, q, w, ...)] tratti candidati (u->v, frazione t).
    Restituisce (costo, sorgente, destinazione, nodi intermedi) oppure None."""
    def peso(w, L):
        via = rete.vie[w]
        f = 1.0 if (via["ref"] & refs) else 3.0
        if via["link"]:
            f = 6.0
        return L * f
    # arrivi: si arriva al nodo u del tratto di destinazione e si percorre t*L
    arrivo = defaultdict(list)
    for k, dst in enumerate(destinazioni):
        c0, u, v, t, q, w = dst[:6]
        L = dist(rete.pt[u], rete.pt[v])
        arrivo[u].append((peso(w, L) * t + c0, k))
    meta = [d[4] for d in destinazioni]

    def h(n):
        p = rete.pt[n]
        return min(dist(p, m) for m in meta)
    best, dove, prec, heap = {}, {}, {}, []
    risultato = None
    # caso particolare: partenza e arrivo sullo stesso tratto, arrivo più avanti
    for si, src in enumerate(sorgenti):
        c0, u, v, t, q, w = src[:6]
        L = dist(rete.pt[u], rete.pt[v])
        for k, dst in enumerate(destinazioni):
            if (dst[1], dst[2]) == (u, v) and dst[3] >= t:
                c = c0 + dst[0] + peso(w, L) * (dst[3] - t)
                if risultato is None or c < risultato[0]:
                    risultato = (c, si, k, [])
        c = c0 + peso(w, L) * (1 - t)
        if c < best.get(v, math.inf):
            best[v] = c
            dove[v] = si
            prec[v] = None
            heapq.heappush(heap, (c + h(v), c, v))
    while heap:
        f, c, n = heapq.heappop(heap)
        if c > best.get(n, math.inf):
            continue
        if risultato is not None and f >= risultato[0]:
            break
        if c > limite:
            break
        for extra, k in arrivo.get(n, ()):
            tot = c + extra
            if risultato is None or tot < risultato[0]:
                nodi = []
                x = n
                while x is not None:
                    nodi.append(x)
                    x = prec[x]
                risultato = (tot, dove[n], k, nodi[::-1])
        for v, L, w in rete.out.get(n, ()):
            nc = c + peso(w, L)
            if nc < best.get(v, math.inf):
                best[v] = nc
                prec[v] = n
                dove[v] = dove[n]
                heapq.heappush(heap, (nc + h(v), nc, v))
    return risultato


def rdp(punti, toll, fissi):
    """Douglas-Peucker che conserva gli indici in 'fissi'. Restituisce gli indici tenuti."""
    n = len(punti)
    if n < 3:
        return list(range(n))
    lat0 = punti[0][0]
    kx = KX0 * math.cos(math.radians(lat0))
    xy = [((p[1]) * kx, p[0] * KY) for p in punti]
    tieni = [False] * n
    tieni[0] = tieni[-1] = True
    for f in fissi:
        tieni[f] = True
    cut = sorted(set([0, n - 1] + list(fissi)))
    stack = list(zip(cut, cut[1:]))
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        ax, ay = xy[i]
        bx, by = xy[j]
        dx, dy = bx - ax, by - ay
        l = math.hypot(dx, dy)
        dmax, kmax = -1, -1
        for k in range(i + 1, j):
            px, py = xy[k]
            d = abs(dy * (px - ax) - dx * (py - ay)) / l if l > 0 else math.hypot(px - ax, py - ay)
            if d > dmax:
                dmax, kmax = d, k
        if dmax > toll:
            tieni[kmax] = True
            stack.append((i, kmax))
            stack.append((kmax, j))
    return [k for k in range(n) if tieni[k]]


def costruisci(con, log=print):
    """Crea/riempie la tabella geometria nel database aperto 'con'. Restituisce un riepilogo."""
    if not os.path.exists(CACHE):
        log("Geometria: manca source/osm_autostrade.json.gz, salto.")
        return None
    cache = leggi_cache()
    rete = Rete(cache["vie"])
    log(f"Geometria: rete OSM con {len(cache['vie'])} vie, {len(rete.pt)} nodi.")
    caselli = {r[0]: (r[1], r[2], r[3]) for r in con.execute("SELECT id, lat, lon, nome FROM caselli WHERE lat IS NOT NULL")}
    autostrade = {r[0]: r[1:] for r in con.execute("SELECT id, sigla, nome, id_casello_inizio, id_casello_fine FROM autostrade")}
    aree = defaultdict(list)
    for r in con.execute("""SELECT id, nome, lat, lon, id_autostrada, id_casello_direzione, km, km_cippo FROM aree_servizio
                            WHERE lat IS NOT NULL AND lon IS NOT NULL AND id_autostrada IS NOT NULL
                              AND id_casello_direzione IS NOT NULL AND attiva_2026 >= 0""") :
        aree[(r[4], r[5])].append({"id": r[0], "nome": r[1], "p": (r[2], r[3]), "km": r[6], "cippo": r[7]})
    direzioni = defaultdict(set)
    for (a, d) in aree:
        direzioni[a].add(d)

    # sigle OSM di ogni autostrada: quelle della carreggiata più vicina alle sue aree
    sigle = {}
    for a in direzioni:
        cnt = Counter()
        n = 0
        for d in direzioni[a]:
            for ar in aree[(a, d)]:
                v = rete.vicini(ar["p"], 250, lambda w: not w["link"])
                if v:
                    n += 1
                    for r in rete.vie[v[0][5]]["ref"]:
                        cnt[r] += 1
        s = {r for r, c in cnt.items() if c >= max(1, 0.25 * n)}
        sig = autostrade[a][0].replace(" ", "").upper()
        if sig.startswith("A") or sig.startswith("RA"):
            s.add(sig)
        sigle[a] = s

    def candidati(p, raggio, refs, peso_dist=2.0):
        tutti = rete.vicini(p, raggio, lambda w: not w["link"])
        buoni = [c for c in tutti if rete.vie[c[5]]["ref"] & refs]
        scelti = buoni if buoni else tutti
        return [(c[0] * peso_dist,) + c[1:] for c in scelti[:40]]

    def origine(a, d):
        ini, fin = autostrade[a][2], autostrade[a][3]
        if d == fin and ini in caselli:
            return ini
        if d == ini and fin in caselli:
            return fin
        altre = [x for x in direzioni[a] if x != d and x in caselli]
        if len(altre) == 1:
            return altre[0]
        # più rami (A55): il casello delle altre direzioni più vicino alla prima area
        prima = min(aree[(a, d)], key=lambda r: r["km"])
        return min(altre, key=lambda x: dist(caselli[x][:2], prima["p"])) if altre else None

    con.executescript("DROP TABLE IF EXISTS geometria;" + SCHEMA)
    report, righe = [], []
    tot_m = 0
    for (a, d) in sorted(aree):
        refs = sigle[a]
        lista = sorted(aree[(a, d)], key=lambda r: (r["km"] if r["km"] is not None else 0))
        tappe = []
        o = origine(a, d)
        if o is not None:
            tappe.append({"tipo": "casello", "id": o, "p": caselli[o][:2], "nome": caselli[o][2]})
        tappe += [{"tipo": "area", **r} for r in lista]
        if d in caselli:
            tappe.append({"tipo": "casello", "id": d, "p": caselli[d][:2], "nome": caselli[d][2]})
        parti = []
        cur = None        # tratto in costruzione: {"pt": [...], "segni": [(indice, tappa)]}
        prec_cand = None  # candidato scelto per la tappa precedente
        for i in range(len(tappe) - 1):
            t0, t1 = tappe[i], tappe[i + 1]
            r0 = RAGGIO_CASELLO if t0["tipo"] == "casello" else RAGGIO_AREA
            r1 = RAGGIO_CASELLO if t1["tipo"] == "casello" else RAGGIO_AREA
            src = [prec_cand] if prec_cand is not None else candidati(t0["p"], r0, refs, 0.5 if t0["tipo"] == "casello" else 2.0)
            dst = candidati(t1["p"], r1, refs, 0.5 if t1["tipo"] == "casello" else 2.0)
            ok = None
            if src and dst:
                retta = dist(t0["p"], t1["p"])
                ok = percorso(rete, src, dst, refs, 3.0 * retta + 30000)
            if ok is None:
                report.append(f"  {autostrade[a][0]} → {caselli.get(d, ('', '', d))[2]}: nessun percorso {t0['nome']} → {t1['nome']}")
                if cur:
                    parti.append(cur)
                cur, prec_cand = None, None
                continue
            costo, si, di, nodi = ok
            s, e = src[si], dst[di]
            tratto = [s[4]] + [rete.pt[n] for n in nodi] + [e[4]]
            L = sum(dist(x, y) for x, y in zip(tratto, tratto[1:]))
            # controllo: lunghezza del percorso rispetto ai km del cartello (o ai km progressivi)
            dubbio = ""
            if t0["tipo"] == "area" and t1["tipo"] == "area":
                attesi = [abs(t1[k] - t0[k]) * 1000 for k in ("cippo", "km") if t0.get(k) is not None and t1.get(k) is not None]
                if attesi and all(not (0.8 * x - 1500 <= L <= 1.2 * x + 1500) for x in attesi):
                    dubbio = f"lunghezza {L / 1000:.1f} km, attesi {' / '.join(f'{x / 1000:.1f}' for x in attesi)} km"
            elif L > 2.5 * dist(t0["p"], t1["p"]) + 5000:
                dubbio = f"percorso tortuoso {L / 1000:.1f} km (in linea d'aria {dist(t0['p'], t1['p']) / 1000:.1f})"
            if dubbio:
                report.append(f"  {autostrade[a][0]} → {caselli.get(d, ('', '', d))[2]}: {t0['nome']} → {t1['nome']}: {dubbio}")
            if cur is None:
                cur = {"pt": [], "segni": []}
                cur["segni"].append((0, t0))
                cur["pt"].append(tratto[0])
            cur["pt"].extend(tratto[1:])
            cur["segni"].append((len(cur["pt"]) - 1, t1))
            prec_cand = (0.0,) + e[1:]
        if cur:
            parti.append(cur)
        for k, parte in enumerate(parti):
            pts = parte["pt"]
            # punti doppi consecutivi
            pulito, mappa = [], {}
            for j, p in enumerate(pts):
                if not pulito or dist(pulito[-1], p) > 0.3:
                    pulito.append(p)
                mappa[j] = len(pulito) - 1
            fissi = sorted({mappa[j] for j, _ in parte["segni"]})
            tenuti = rdp(pulito, TOLL_RDP, fissi)
            pos = {j: n for n, j in enumerate(tenuti)}
            semp = [pulito[j] for j in tenuti]
            cum = [0.0]
            for x, y in zip(semp, semp[1:]):
                cum.append(cum[-1] + dist(x, y))
            ar, cp = [], []
            for j, t in parte["segni"]:
                m = cum[pos[mappa[j]]]
                if t["tipo"] == "area":
                    ar.append(f"{t['id']}:{round(m)}")
                    if t.get("cippo") is not None:
                        cp.append(f"{round(m)}:{t['cippo']:.3f}")
            if not ar:
                continue
            righe.append((a, d, k, round(cum[-1], 1), codifica(semp, 5), ";".join(ar), ";".join(cp)))
            tot_m += cum[-1]
    con.executemany("INSERT INTO geometria (id_autostrada, id_casello_direzione, parte, lunghezza_m, punti, aree, cippi) VALUES (?,?,?,?,?,?,?)", righe)
    con.commit()
    coperte = len({(r[0], r[1]) for r in righe})
    msg = f"Geometria: {len(righe)} tratti su {coperte} carreggiate di {len(aree)}, {tot_m / 1000:.0f} km, {len(report)} avvisi."
    log(msg)
    for r in report:
        log(r)
    return {"messaggio": msg, "avvisi": report}


if __name__ == "__main__":
    db = sys.argv[1]
    con = sqlite3.connect(db)
    costruisci(con)
