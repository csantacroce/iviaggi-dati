"""Foto dei modelli in stile studio (20/11/2026, scelto da Carlo): l'auto ritagliata dalla foto di Commons
(rembg, modello isnet-general-use) su uno studio grigio scuro, con l'ombra presa dalla sagoma dell'auto
(niente "auto volanti"). Uscita 480x300 JPEG. Le licenze CC BY / CC BY-SA permettono le modifiche:
nei crediti è scritto che le foto sono ritagliate e messe su sfondo uniforme."""
from PIL import Image, ImageChops, ImageDraw, ImageFilter
W, H = 480, 300
STILE = "studio-scuro-1"
_sess = None


def _sessione():
    global _sess
    if _sess is None:
        from rembg import new_session
        _sess = new_session("isnet-general-use")
    return _sess

def fondo():
    """Studio grigio scuro (20/11/2026, Carlo): parete antracite, pavimento appena più chiaro, luce morbida dall'alto a destra."""
    im = Image.new("RGB", (W, H)); d = ImageDraw.Draw(im)
    oriz = int(H * 0.60)
    def parete(y):
        t = min(1, y / oriz); return (34 + 22*t, 36 + 23*t, 40 + 24*t)
    def pavimento(y):
        t = max(0, (y - oriz) / (H - oriz)); return (64 - 18*t, 66 - 18*t, 70 - 18*t)
    banda = H * 0.10    # passaggio morbido tra parete e pavimento (niente linea netta)
    for y in range(H):
        k = min(1, max(0, (y - (oriz - banda / 2)) / banda))
        k = k * k * (3 - 2 * k)
        a, b = parete(y), pavimento(y)
        d.line([(0, y), (W, y)], fill=tuple(int(a[i] * (1 - k) + b[i] * k) for i in range(3)))
    luce = Image.new("L", (W, H), 0); dl = ImageDraw.Draw(luce)
    dl.ellipse([W*0.18, H*0.30, W*0.82, H*0.95], fill=60)              # pozza di luce dove sta l'auto
    luce = luce.filter(ImageFilter.GaussianBlur(45))
    im = Image.composite(Image.new("RGB", (W, H), (120, 124, 130)), im, luce)
    return im

def studio(src, dst):
    orig = Image.open(src).convert("RGB")
    from rembg import remove
    rit = remove(orig, session=_sessione(), post_process_mask=True)
    # bordo pulito: maschera leggermente ristretta e ammorbidita (niente aloni dello sfondo di partenza)
    alfa = rit.getchannel("A").point(lambda v: 255 if v > 140 else 0).filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(0.9))
    rit.putalpha(alfa)
    a = alfa.point(lambda v: 255 if v > 128 else 0)
    bbox = a.getbbox()
    if not bbox: return False
    auto = rit.crop(bbox)
    # dimensione: larghezza 84% o altezza 70% del riquadro
    s = min(W * 0.84 / auto.width, H * 0.70 / auto.height)
    auto = auto.resize((max(1, int(auto.width * s)), max(1, int(auto.height * s))), Image.LANCZOS)
    x = (W - auto.width) // 2; y = int(H * 0.88) - auto.height
    im = fondo()
    # ombra: ellisse sfocata sotto le ruote + ombra di contatto
    # ombra presa dalla sagoma dell'auto (20/11/2026, "auto volanti"): per ogni colonna il punto più basso;
    # la linea di terra unisce i punti di appoggio delle ruote (inviluppo inferiore).
    m = auto.getchannel("A").point(lambda v: 255 if v > 128 else 0)
    mw, mh = m.size
    px = m.load()
    punti = []
    for cx in range(mw):
        for cy in range(mh - 1, -1, -1):
            if px[cx, cy]:
                punti.append((cx, cy)); break
    if len(punti) < 10: return False
    # inviluppo inferiore (y verso il basso): catena monotona tenendo i punti più bassi
    def croce(o, p, q): return (p[0]-o[0])*(q[1]-o[1]) - (p[1]-o[1])*(q[0]-o[0])
    inv = []
    for p in punti:
        while len(inv) >= 2 and croce(inv[-2], inv[-1], p) >= 0:
            inv.pop()
        inv.append(p)
    def terra(cx):
        for (x0, y0), (x1, y1) in zip(inv, inv[1:]):
            if x0 <= cx <= x1:
                return y0 + (y1 - y0) * (cx - x0) / max(1, x1 - x0)
        return inv[-1][1]
    sotto = Image.new("L", (W, H), 0); ds = ImageDraw.Draw(sotto)
    alone = Image.new("L", (W, H), 0); da = ImageDraw.Draw(alone)
    contatto = Image.new("L", (W, H), 0); dc = ImageDraw.Draw(contatto)
    for cx, fy in punti:
        t = terra(cx); X = x + cx
        if t - fy < mh * 0.22:                                            # solo sotto la scocca, non ai lati
            ds.line([(X, y + fy), (X, y + t + 2)], fill=210)
        da.line([(X, y + t - mh * 0.02), (X, y + t + mh * 0.07)], fill=150) # alone sul pavimento
        if t - fy < mh * 0.015:                                           # la gomma tocca terra
            dc.line([(X, y + t - 2), (X, y + t + 3)], fill=255)
    om = ImageChops.lighter(sotto.filter(ImageFilter.GaussianBlur(3)),
                            alone.filter(ImageFilter.GaussianBlur(11)).point(lambda v: int(v * 0.7)))
    om = ImageChops.lighter(om, contatto.filter(ImageFilter.GaussianBlur(2)))
    im = Image.composite(Image.new("RGB", (W, H), (6, 6, 8)), im, om.point(lambda v: int(v * 0.88)))
    im.paste(auto, (x, y), auto)
    im.save(dst, quality=90)
    return True
