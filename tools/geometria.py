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
import re
import os
import sqlite3
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from osm import decodifica, codifica, leggi_cache, CACHE  # noqa: E402

KX0 = 111_320.0
KY = 110_540.0
CELLA = 0.01           # griglia dell'indice (gradi)
RAGGIO_AREA = 1000.0   # m: carreggiate candidate attorno a un'area (alcune coordinate sono imprecise)
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
  cippi TEXT,
  anello INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_geometria_chiave ON geometria(id_autostrada, id_casello_direzione);"""


def dist(a, b):
    kx = KX0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[1] - b[1]) * kx, (a[0] - b[0]) * KY)


def norm_ref(r):
    return [x.replace(" ", "").upper() for x in r.split(";") if x.strip()]


def combacia(ref_via, refs):
    """vero se una sigla della via è una di refs, anche con un suffisso (A1VAR, A1DIR) ma non A1 con A14."""
    for r in ref_via:
        for x in refs:
            if r == x or (r.startswith(x) and not r[len(x)].isdigit()):
                return True
    return False


def rotta(a, b):
    kx = KX0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.degrees(math.atan2((b[1] - a[1]) * kx, (b[0] - a[0]) * KY)) % 360


def svolta(r1, r2):
    d = abs(r1 - r2) % 360
    return 360 - d if d > 180 else d


class Rete:
    def __init__(self, vie):
        self.idx = {}
        self.pt = []
        self.out = defaultdict(list)      # u -> [(v, lunghezza, via)]
        self.inn = defaultdict(list)      # v -> [(u, lunghezza, via)]
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
                    self.arco(b, a, L, w)
                else:
                    self.arco(a, b, L, w)
                    if not ow:
                        self.arco(b, a, L, w)
        # indice a griglia dei tratti (u, v, via)
        self.griglia = defaultdict(list)
        for u, lst in self.out.items():
            for v, L, w in lst:
                a, b = self.pt[u], self.pt[v]
                for i in range(int(min(a[0], b[0]) / CELLA), int(max(a[0], b[0]) / CELLA) + 1):
                    for j in range(int(min(a[1], b[1]) / CELLA), int(max(a[1], b[1]) / CELLA) + 1):
                        self.griglia[(i, j)].append((u, v, L, w))

    def arco(self, a, b, L, w):
        self.out[a].append((b, L, w))
        self.inn[b].append((a, L, w))

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


def percorso(rete, sorgenti, destinazioni, refs, limite, evita=None):
    """A* da più partenze a più arrivi sulla rete orientata.
    sorgenti/destinazioni: [(costo_iniziale, u, v, t, q, w, ...)] tratti candidati (u->v, frazione t).
    Restituisce (costo, sorgente, destinazione, nodi intermedi) oppure None."""
    def peso(w, L):
        via = rete.vie[w]
        f = 1.0 if combacia(via["ref"], refs) else 3.0
        if via["link"]:
            f = 6.0
        return L * f
    evita = evita or ()
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
            nc = c + peso(w, L) * (4.0 if n in evita else 1.0)
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




def inversione(pts, passo=40.0):
    """vero se il percorso torna indietro (inversione a un'uscita): svolta di oltre 120° su ~40 m."""
    ricamp, acc = [pts[0]], 0.0
    for x, y in zip(pts, pts[1:]):
        acc += dist(x, y)
        if acc >= passo:
            ricamp.append(y)
            acc = 0.0
    r = [rotta(x, y) for x, y in zip(ricamp, ricamp[1:])]
    return any(svolta(a, b) > 120 for a, b in zip(r, r[1:]))


def estendi(rete, nodo, rotta0, refs, cap, avanti, stop=None):
    """Segue la stessa autostrada all'indietro (avanti=False) o in avanti dal nodo, per al massimo cap metri.
    Si ferma quando la sigla si perde per più di 2 km, a una svolta brusca o dove finisce la strada.
    Restituisce i nodi visitati (escluso quello di partenza), nell'ordine di allontanamento."""
    nodi, visti, tot, senza = [], {nodo}, 0.0, 0.0
    cur, r_cur = nodo, rotta0
    while tot < cap:
        scelte = []
        for x, L, w in (rete.out if avanti else rete.inn).get(cur, ()):
            via = rete.vie[w]
            if via["link"] or x in visti:
                continue
            r = rotta(rete.pt[cur], rete.pt[x]) if avanti else rotta(rete.pt[x], rete.pt[cur])
            sv = svolta(r, r_cur)
            ok = combacia(via["ref"], refs)
            if sv > (60 if ok else 25):
                continue
            scelte.append((0 if ok else 1, sv, x, L, r, ok))
        if not scelte:
            break
        scelte.sort()
        _, _, x, L, r, ok = scelte[0]
        senza = 0.0 if ok else senza + L
        if senza > 2000:
            break
        nodi.append(x)
        visti.add(x)
        tot += L
        cur, r_cur = x, r
        if stop is not None and stop(rete.pt[x]):
            break
    # niente coda senza sigla
    while nodi and senza > 0:
        x = nodi.pop()
        senza -= dist(rete.pt[x], rete.pt[nodi[-1]] if nodi else rete.pt[nodo])
    return nodi


def costruisci(con, log=print):
    """Crea/riempie la tabella geometria nel database aperto 'con'. Restituisce un riepilogo."""
    if not os.path.exists(CACHE):
        log("Geometria: manca source/osm_autostrade.json.gz, salto.")
        return None
    cache = leggi_cache()
    rete = Rete(cache["vie"])
    log(f"Geometria: rete OSM con {len(cache['vie'])} vie, {len(rete.pt)} nodi.")
    autostrade = {r[0]: r[1:] for r in con.execute("SELECT id, sigla, nome, tipo FROM autostrade")}
    nomi_caselli = {r[0]: r[1] for r in con.execute("SELECT id, nome FROM caselli")}
    aree = defaultdict(list)
    for r in con.execute("""SELECT id, nome, lat, lon, id_autostrada, id_casello_direzione, km, km_cippo FROM aree_servizio
                            WHERE lat IS NOT NULL AND lon IS NOT NULL AND id_autostrada IS NOT NULL
                              AND id_casello_direzione IS NOT NULL AND km IS NOT NULL AND attiva_2026 >= 0"""):
        aree[(r[4], r[5])].append({"id": r[0], "nome": r[1], "p": (r[2], r[3]), "km": r[6], "cippo": r[7]})

    # sigle OSM di ogni autostrada: la sua e quelle della carreggiata più vicina alle sue aree
    sigle = {}
    for a in {k[0] for k in aree}:
        cnt, n = Counter(), 0
        for (a2, d), lista in aree.items():
            if a2 != a:
                continue
            for ar in lista:
                v = rete.vicini(ar["p"], 250, lambda w: not w["link"])
                if v:
                    n += 1
                    for r in rete.vie[v[0][5]]["ref"]:
                        cnt[r] += 1
        s = {r for r, c in cnt.items() if c >= max(1, 0.25 * n)}
        sig = autostrade[a][0].replace(" ", "").upper()
        if re.match(r"^R?A\d", sig):
            s.add(sig)
        sigle[a] = s

    def candidati(p, refs):
        """carreggiate vicine; quelle senza la sigla giusta valgono 150 m in più (a volte la sigla manca)."""
        tutti = rete.vicini(p, RAGGIO_AREA, lambda w: not w["link"])
        res = [(c[0] * 2.0 + (0 if combacia(rete.vie[c[5]]["ref"], refs) else 150),) + c[1:] for c in tutti]
        res.sort(key=lambda c: c[0])
        return res[:40]

    def tratta(t0, t1, refs, src=None, evita=None):
        """percorso tra due aree: (punti, sorgente, arrivo, lunghezza, quota con la sigla giusta, nodi) oppure None"""
        src = src or candidati(t0["p"], refs)
        dst = candidati(t1["p"], refs)
        if not src or not dst:
            return None
        retta = dist(t0["p"], t1["p"])
        ok = percorso(rete, src, dst, refs, 4.0 * retta + 40000, evita)
        if ok is None:
            return None
        _, si, di, nodi = ok
        s, e = src[si], dst[di]
        pts = [s[4]] + [rete.pt[n] for n in nodi] + [e[4]]
        giusta = 0.0
        for x, y in zip(nodi, nodi[1:]):
            for v, L, w in rete.out[x]:
                if v == y:
                    if combacia(rete.vie[w]["ref"], refs) and not rete.vie[w]["link"]:
                        giusta += L
                    break
        L = sum(dist(x, y) for x, y in zip(pts, pts[1:]))
        return pts, s, e, L, giusta / L if L else 1.0, nodi

    def valida(t0, t1, tr):
        L, quota = tr[3], tr[4]
        retta = dist(t0["p"], t1["p"])
        if L > 1.7 * retta + 5000:
            return f"percorso tortuoso {L / 1000:.1f} km (in linea d'aria {retta / 1000:.1f})"
        attesi = [abs(t1[k] - t0[k]) * 1000 for k in ("cippo", "km") if t0.get(k) is not None and t1.get(k) is not None]
        if attesi and not any(0.8 * x - 1500 <= L <= 1.2 * x + 1500 for x in attesi):
            # i km del database a volte sono sbagliati: vale il tracciato se resta sulla stessa autostrada e non gira attorno
            if quota >= 0.95 and L <= 1.6 * retta + 3000:
                return ""
            return f"lunghezza {L / 1000:.1f} km, attesi {' / '.join(f'{x / 1000:.1f}' for x in attesi)} km"
        return ""

    con.executescript("DROP TABLE IF EXISTS geometria;" + SCHEMA)
    report, righe = [], []
    tot_m, collocate_tot, orfane_tot = 0, 0, 0
    for (a, d) in sorted(aree):
        refs = sigle[a]
        nome_k = f"{autostrade[a][0]} → {nomi_caselli.get(d, d)}"
        lista = sorted(aree[(a, d)], key=lambda r: r["km"])
        # 1) tratte tra aree consecutive; un'area che non si collega (ramo parallelo, coordinate sbagliate)
        #    si salta e si colloca dopo con i km del cartello
        parti, cur, saltate = [], None, []
        i = 0
        while i < len(lista):
            if cur is None:
                cur = {"aree": [lista[i]], "tratte": []}
                i += 1
                continue
            ultimo = cur["aree"][-1]
            fatto = False
            for j in range(i, min(i + 3, len(lista))):
                # si riparte dal punto dove è arrivata la tratta precedente (stessa carreggiata), altrimenti da capo
                tr, motivo = None, "nessun percorso"
                for src in ([[(0.0,) + cur["tratte"][-1][2][1:]]] if cur["tratte"] else []) + [None]:
                    tr = tratta(ultimo, lista[j], refs, src)
                    motivo = "nessun percorso" if tr is None else valida(ultimo, lista[j], tr)
                    if not motivo:
                        break
                if not motivo:
                    saltate += lista[i:j]
                    for x in lista[i:j]:
                        report.append(f"  {nome_k}: {x['nome']} non è sul tracciato tra {ultimo['nome']} e {lista[j]['nome']}: collocata con i km")
                    cur["tratte"].append(tr)
                    cur["aree"].append(lista[j])
                    i = j + 1
                    fatto = True
                    break
                if j == i:
                    report.append(f"  {nome_k}: {ultimo['nome']} → {lista[i]['nome']}: {motivo}")
            if not fatto:
                parti.append(cur)
                cur = None
        if cur:
            parti.append(cur)
        # 2) anello (G.R.A.): l'ultima area si ricollega alla prima con un percorso corto
        anello = False
        if len(parti) == 1 and len(parti[0]["aree"]) >= 3:
            p0 = parti[0]
            tr = tratta(p0["aree"][-1], p0["aree"][0], refs)
            if tr and tr[3] < 0.5 * sum(t[3] for t in p0["tratte"]) and tr[3] < 30000:
                anello = True
                p0["tratte"].append(tr)
                p0["aree"].append(p0["aree"][0])
        # 2b) rami paralleli della stessa autostrada (es. A1 Variante di Valico e Panoramica): un percorso
        #     diverso tra due aree, quasi tutto nuovo, con la stessa sigla e lungo come quello principale
        rami = []
        if not anello:
            for parte in parti:
                principali = set()
                for tr in parte["tratte"]:
                    principali.update(tr[5])
                vicino = set()
                for n in principali:
                    p = rete.pt[n]
                    for di in (-1, 0, 1):
                        for dj in (-1, 0, 1):
                            vicino.add((round(p[0] * 2500) + di, round(p[1] * 2000) + dj))   # celle di ~40 m
                ar = parte["aree"]
                for i in range(len(ar) - 1):
                    for j in (i + 1, i + 2):
                        if j >= len(ar):
                            continue
                        tr = tratta(ar[i], ar[j], refs, evita=principali)
                        if tr is None or tr[4] < 0.95 or inversione(tr[0]):
                            continue
                        L_princ = sum(t[3] for t in parte["tratte"][i:j])
                        nuovo = sum(dist(x, y) for x, y in zip(tr[0], tr[0][1:])
                                    if (round(x[0] * 2500), round(x[1] * 2000)) not in vicino)
                        if nuovo < 5000 or tr[3] > 1.15 * L_princ + 1000:
                            continue
                        # già trovato?
                        if any(sum(1 for q in tr[0] if (round(q[0] * 2500), round(q[1] * 2000)) in r["celle"]) > 0.5 * len(tr[0]) for r in rami):
                            continue
                        celle = {(round(q[0] * 2500), round(q[1] * 2000)) for q in tr[0]}
                        rami.append({"aree": [ar[i], ar[j]], "tratte": [tr], "ramo": True, "celle": celle})
                        principali.update(tr[5])
                        report.append(f"  {nome_k}: ramo parallelo tra {ar[i]['nome']} e {ar[j]['nome']} ({tr[3] / 1000:.1f} km, nuovi {nuovo / 1000:.1f})")
        # 3) punti di ogni parte, con estensioni prima della prima area e dopo l'ultima
        for k, parte in enumerate(parti):
            pts, segni = [], []
            if parte["tratte"]:
                s0 = parte["tratte"][0][1]
                u, v = s0[1], s0[2]
            else:   # area sola: la carreggiata più vicina
                c = candidati(parte["aree"][0]["p"], refs)
                if not c:
                    report.append(f"  {nome_k}: {parte['aree'][0]['nome']} lontana da ogni carreggiata")
                    continue
                s0 = c[0]
                u, v = s0[1], s0[2]
            if not anello and not parte.get("ramo"):
                indietro = estendi(rete, u, rotta(rete.pt[u], rete.pt[v]), refs, 80000 if k == 0 else 40000, False)
                pts += [rete.pt[n] for n in reversed(indietro)] + [rete.pt[u]]
            pts.append(s0[4])
            segni.append((len(pts) - 1, parte["aree"][0]))
            for tr, area in zip(parte["tratte"], parte["aree"][1:]):
                tp = tr[0]
                if dist(pts[-1], tp[0]) > 150:   # le due tratte non si toccano: si va avanti lo stesso, il salto è piccolo
                    report.append(f"  {nome_k}: salto di {dist(pts[-1], tp[0]):.0f} m presso {area['nome']}")
                pts += tp[1:] if dist(pts[-1], tp[0]) < 1 else tp
                segni.append((len(pts) - 1, area))
            if not anello and not parte.get("ramo") and k < len(parti) - 1:
                e = parte["tratte"][-1][2] if parte["tratte"] else s0
                avanti = estendi(rete, e[2], rotta(rete.pt[e[1]], rete.pt[e[2]]), refs, 40000, True)
                pts += [rete.pt[e[2]]] + [rete.pt[n] for n in avanti]
            parte["pt"], parte["segni"] = pts, segni
        parti = [p for p in parti if "pt" in p]
        for r in rami:
            tp = r["tratte"][0][0]
            r["pt"], r["segni"] = list(tp), [(0, r["aree"][0]), (len(tp) - 1, r["aree"][1])]
        parti += rami
        # 4) aree saltate: collocate sulla parte giusta in proporzione ai km del cartello
        orfane = []
        for x in saltate:
            messa = False
            # prima sui rami paralleli, se ci passano vicino
            for r in rami:
                dmin, jmin = min((dist(x["p"], q), j) for j, q in enumerate(r["pt"]))
                if dmin < 800 and 0 < jmin < len(r["pt"]) - 1:
                    r["segni"].append((jmin, x))
                    messa = True
                    break
            for parte in ([] if messa else parti):
                ancore = [(j, t) for j, t in parte["segni"] if t.get("cippo") is not None]
                if x.get("cippo") is None:
                    break
                for (j0, t0), (j1, t1) in zip(ancore, ancore[1:]):
                    lo, hi = sorted((t0["cippo"], t1["cippo"]))
                    if lo <= x["cippo"] <= hi and hi > lo:
                        parte.setdefault("collocate", []).append((j0, j1, (x["cippo"] - t0["cippo"]) / (t1["cippo"] - t0["cippo"]), x))
                        messa = True
                        break
                if messa:
                    break
            if messa:
                collocate_tot += 1
            else:
                orfane.append(x)
        orfane_tot += len(orfane)
        for x in orfane:
            report.append(f"  {nome_k}: {x['nome']} non collocata (resta senza tracciato)")
        # 5) semplificazione e righe della tabella
        for k, parte in enumerate(parti):
            pts = parte["pt"]
            pulito, mappa = [], {}
            for j, p in enumerate(pts):
                if not pulito or dist(pulito[-1], p) > 0.3:
                    pulito.append(p)
                mappa[j] = len(pulito) - 1
            parte["segni"].sort(key=lambda z: z[0])
            fissi = sorted({mappa[j] for j, _ in parte["segni"]})
            tenuti = rdp(pulito, TOLL_RDP, fissi)
            pos = {j: n for n, j in enumerate(tenuti)}
            semp = [pulito[j] for j in tenuti]
            cum = [0.0]
            for x, y in zip(semp, semp[1:]):
                cum.append(cum[-1] + dist(x, y))
            posizioni = []
            for j, t in parte["segni"]:
                posizioni.append((cum[pos[mappa[j]]], t))
            if anello:   # l'ultima "area" è la prima ripetuta in fondo all'anello: non va scritta due volte
                posizioni = posizioni[:-1]
            for j0, j1, f, x in parte.get("collocate", []):
                m0, m1 = cum[pos[mappa[j0]]], cum[pos[mappa[j1]]]
                posizioni.append((m0 + f * (m1 - m0), x))
            posizioni.sort(key=lambda z: z[0])
            ar = ";".join(f"{t['id']}:{round(m)}" for m, t in posizioni)
            cp = ";".join(f"{round(m)}:{t['cippo']:.3f}" for m, t in posizioni if t.get("cippo") is not None)
            righe.append((a, d, k, round(cum[-1], 1), codifica(semp, 5), ar, cp, 1 if anello else 0))
            tot_m += cum[-1]
    con.executemany("""INSERT INTO geometria (id_autostrada, id_casello_direzione, parte, lunghezza_m, punti, aree, cippi, anello)
                       VALUES (?,?,?,?,?,?,?,?)""", righe)
    con.commit()
    coperte = len({(r[0], r[1]) for r in righe})
    n_aree = sum(len(v) for v in aree.values())
    msg = (f"Geometria: {len(righe)} tratti su {coperte} carreggiate di {len(aree)}, {tot_m / 1000:.0f} km; "
           f"aree sul tracciato {n_aree - collocate_tot - orfane_tot}, collocate con i km {collocate_tot}, senza tracciato {orfane_tot}.")
    log(msg)
    for r in report:
        log(r)
    return {"messaggio": msg, "avvisi": report}


if __name__ == "__main__":
    con = sqlite3.connect(sys.argv[1])
    costruisci(con)
