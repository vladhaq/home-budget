"""Газетки Biedronka: те же форма данных и функции, что у газеток Lidl и Kaufland, — для вкладки «Biedronka»
на странице «Скидки», напоминаний и «Где выгоднее».

Текста у газеток Biedronka нет нигде: сайт (biedronka.pl/pl/gazetki) показывает страницы картинками через
leaflet-api.prod.biedronka.cloud, агрегаторы — тоже картинки или подборку из нескольких десятков «хитов».
Поэтому страницы распознаются (Tesseract, разреженный текст, зелёный канал картинки ×2, несколько страниц сразу) —
каждая один раз, результат хранится в data/biedronka_deals/ocr/ (VER — версия распознавания: сменилась — газетки
распознаются заново). Берём только продуктовые газетки («Codziennie niskie ceny»). Строки распознанного текста
склеиваются в карточки товаров (название, вес, «14,95 zł/kg»); плашки — цена, «KAŻDA Z 2 SZTUK», «DRUGI 70% TANIEJ»,
«Cena przed obniżką», «Limit dzienny…» — раздаются товарам по колонкам (owners), чтобы не брать цену соседа.
Крупная цена нарисована графикой и читается с ошибками («64?» = 6,49) — основная цена считается по цене за кг/л
и весу и сверяется с ней (не сошлось — ≈). Все акции помечены ocr=True: в интерфейсе — предупреждение, что данные
могут быть неточными.
"""
import datetime as dt
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

import requests

from core.common import DATA
from core.deals import mod as lidl_mod

BDATA = DATA / "biedronka_deals"
INDEX = BDATA / "index.json"
HISTORY = BDATA / "price_history.json"
WATCH = BDATA / "постоянные_позиции.txt"
OCR_DIR = BDATA / "ocr"
LIST_URL = "https://www.biedronka.pl/pl/gazetki"
LEAFLET_API = "https://leaflet-api.prod.biedronka.cloud/api/leaflets/{}?ctx=web"
PAGE_URL = "https://www.biedronka.pl/pl/press,id,{id},title,{slug}#page={page}"
STORE = "Biedronka"
FOOD = ("codziennie-niskie-ceny-p",)  # продуктовые газетки; «-l» — то же для магазинов с ладой, «home», «znicze» — не еда
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
      "Accept-Language": "pl-PL,pl;q=0.9"}
NON_FOOD = ("znicz", "przysmak dla", "szampon", "lakier", "dezodorant", "pieluch", "chusteczki", "pędzl", "kosmetyczn",
            "do prania", "do zmywar", "do mycia", "be beauty", "niuqi")  # и NON_FOOD газеток Lidl (корм, «dla psa»)
SCALE = 2  # картинка страницы ×2: мелкий текст (цена за кг, «przed obniżką») иначе теряется
VER = 2  # 2: зелёный канал (белый текст на красных плашках, крупные цены); другая версия — газетка распознаётся заново
WORKERS = 4  # страниц распознаётся одновременно

SIZE_RE = re.compile(r"(?:(\d+)\s?[x×]\s?)?(\d+(?:[.,]\d+)?)\s?(kg|g|ml|l)\b", re.I)
UNIT_RE = re.compile(r"(\d{1,4}[,.]\d{2})\s*zł\s*/\s*(100\s?g|kg|l|szt)", re.I)
NOT_NAME = ("każda", "kazda", "limit", "cena", "bez karty", "bezkarty", "poza limitem", "mega paka", "supercena", "przy zakupie",
            "drugi", "kup ", "oferta", "poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela",
            "tylko w", "codziennie", "niskie", "ceny", "rabat", "taniej", "gratis", "produkt", "dowolnie", "mieszaj",
            "zł/kg", "zł/l", "* ", "obniżką", "różne", "rozne", "obnizka", "z kartą", "z karta", "aplikacj", "najtańsz", "moja biedronka")


def L():
    return lidl_mod()


def load_json(path, default):
    return L().load_json(path, default)


# ---------------------------------------------------------------- постоянные позиции Biedronka

def ensure_watch():
    """При первом запуске — копия списка Lidl, дальше списки живут отдельно."""
    if not WATCH.exists():
        BDATA.mkdir(parents=True, exist_ok=True)
        WATCH.write_text("# Постоянные позиции Biedronka — по одной на строку (товар по-польски, диакритику можно не ставить)\n"
                         + "".join(q + "\n" for q in L().watchlist()), encoding="utf-8")


def watchlist() -> list[str]:
    ensure_watch()
    return [ln.strip() for ln in WATCH.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def add_position(q: str):
    fold = L().fold
    if q and fold(q) not in {fold(i) for i in watchlist()}:
        text = WATCH.read_text(encoding="utf-8")
        WATCH.write_text(text + ("" if text.endswith("\n") else "\n") + q + "\n", encoding="utf-8")


def remove_position(q: str):
    fold = L().fold
    ensure_watch()
    keep = [ln for ln in WATCH.read_text(encoding="utf-8").splitlines() if ln.lstrip().startswith("#") or fold(ln.strip()) != fold(q)]
    WATCH.write_text("\n".join(keep) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- список газеток и распознавание страниц

def flyer_list() -> list[dict]:
    """Текущие продуктовые газетки: [{"id", "slug"}] со страницы biedronka.pl/pl/gazetki."""
    html = requests.get(LIST_URL, headers=UA, timeout=30).text
    out = {}
    for fid, slug in re.findall(r"/pl/press,id,(\w+),title,([\w\-]+)", html):
        if slug.startswith(FOOD):
            out[fid] = {"id": fid, "slug": slug}
    return list(out.values())


def leaflet_uuid(fid: str, slug: str) -> str | None:
    html = requests.get(f"https://www.biedronka.pl/pl/press,id,{fid},title,{slug}", headers=UA, timeout=30).text
    m = re.search(r'galleryLeaflet\.init\("([0-9a-f\-]{36})"\)', html)
    return m.group(1) if m else None


def start_from_slug(slug: str, today: dt.date) -> dt.date | None:
    """«…-oferta-od-12-10» → 12.10 этого года (в январе газетка от 29.12 — прошлого)."""
    m = re.search(r"od-(\d{1,2})-(\d{1,2})", slug)
    if not m:
        return None
    d, mo = int(m.group(1)), int(m.group(2))
    year = today.year - (1 if mo - today.month > 6 else 0)
    try:
        return dt.date(year, mo, d)
    except ValueError:
        return None


def tesseract_lines(img) -> list[list]:
    """Строки текста страницы: [x0, y0, x1, y1, текст] в координатах исходной картинки."""
    from PIL import Image
    from receipts import photos
    exe = photos.tesseract_exe()
    # зелёный канал: красная плашка — тёмная, белый и жёлтый текст на ней — светлый, жёлтая плашка — светлая
    # (в обычном сером красный и жёлтый сливаются, крупные цены и «KAŻDA Z 2 SZTUK» не читались)
    green = img.convert("RGB").split()[1]
    big = green.resize((img.width * SCALE, img.height * SCALE), Image.LANCZOS)
    fd, name = tempfile.mkstemp(prefix="_bflyer_", suffix=".png", dir=DATA)
    os.close(fd)
    try:
        big.save(name)
        out = subprocess.run([exe, name, "stdout", "-l", "pol", "--psm", "11", "--tessdata-dir", str(photos.TESSDATA),
                              "-c", "tessedit_create_tsv=1"], capture_output=True, text=True, encoding="utf-8",
                             env={**os.environ, "OMP_THREAD_LIMIT": "1"},  # страницы и так идут параллельно
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    finally:
        os.unlink(name)
    lines = {}
    for row in out.splitlines()[1:]:
        c = row.split("\t")
        if len(c) < 12 or c[0] != "5" or not c[11].strip() or float(c[10]) < 30:
            continue
        key = (int(c[2]), int(c[3]), int(c[4]))
        x, y, w, h = (round(int(v) / SCALE) for v in c[6:10])
        ln = lines.setdefault(key, [x, y, x + w, y + h, []])
        ln[0], ln[1], ln[2], ln[3] = min(ln[0], x), min(ln[1], y), max(ln[2], x + w), max(ln[3], y + h)
        ln[4].append(c[11])
    return [[a, b, c2, d, " ".join(ws)] for a, b, c2, d, ws in lines.values()]


def cards(lines: list[list]) -> list[list]:
    """Строки одной колонки, идущие подряд (левый край ±18 px, зазор ≤ 30 px, рамки строк могут чуть заходить
    друг на друга), — одна карточка:
    «Masło ekstra / Łaciate, 200 g / 14,95 zł/kg». Возвращает блоки [x0, y0, x1, y1, текст]."""
    rest = sorted(lines, key=lambda b: (b[1], b[0]))
    out = []
    while rest:
        cur = rest.pop(0)
        card = [cur]
        grown = True
        while grown:
            grown = False
            last = card[-1]
            for b in rest:
                if abs(b[0] - card[0][0]) <= 18 and -8 <= b[1] - last[3] <= 30 and len(card) < 6:
                    card.append(b)
                    rest.remove(b)
                    grown = True
                    break
        out.append([min(b[0] for b in card), min(b[1] for b in card), max(b[2] for b in card), max(b[3] for b in card),
                    "\n".join(b[4] for b in card)])
    return out


def cache_file(url: str):
    return OCR_DIR / f"v{VER}-{hashlib.sha1(url.encode()).hexdigest()[:20]}.json"


def ocr_page(url: str) -> dict:
    """Страница газетки → {"w", "h", "blocks"} (блоки-карточки). Распознаётся один раз: кэш по адресу картинки."""
    from PIL import Image
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    cache = cache_file(url)
    if cache.exists():
        lines = json.loads(cache.read_text(encoding="utf-8"))
    else:
        img = Image.open(io.BytesIO(requests.get(url, headers=UA, timeout=60).content)).convert("RGB")
        lines = {"w": img.width, "h": img.height, "lines": tesseract_lines(img)}
        cache.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    return {"w": lines["w"], "h": lines["h"], "blocks": cards(lines["lines"])}


DATE_RE = re.compile(r"(\d{1,2})(?:\.(\d{1,2}))?\s*[-–—]\s*(\d{1,2})\.(\d{1,2})")


def page_dates(page: dict, year: int):
    """Даты из шапки страницы: «PONIEDZIAŁEK — SOBOTA 12.10-17.10», «OFERTA WAŻNA 12-14.10», «TYLKO W PONIEDZIAŁEK 12.10»."""
    for b in sorted(page["blocks"], key=lambda b: b[1]):
        if b[1] > page["h"] * 0.12:
            break
        low = b[4].lower()
        if m := DATE_RE.search(low):
            d0, m0, d1, m1 = int(m.group(1)), int(m.group(2) or m.group(4)), int(m.group(3)), int(m.group(4))
            try:
                return dt.date(year, m0, d0), dt.date(year, m1, d1)
            except ValueError:
                continue
        if (m := re.search(r"tylko w \w+\s+(\d{1,2})\.(\d{1,2})", low)):
            try:
                day = dt.date(year, int(m.group(2)), int(m.group(1)))
                return day, day
            except ValueError:
                continue
    return None


def update(verbose=True) -> dict:
    """Текущие продуктовые газетки: новые — скачать и распознать (по странице, с кэшем), закончившиеся — убрать."""
    M = L()
    today = M.today()
    index = load_json(INDEX, {"flyers": {}})
    old, fresh = index["flyers"], {}
    for f in flyer_list():
        slug = f"{f['slug']}-{f['id']}"
        if slug in old and old[slug]["end"] >= today.isoformat() and old[slug].get("ver") == VER:
            fresh[slug] = old[slug]
            continue
        start = start_from_slug(f["slug"], today)
        uuid = leaflet_uuid(f["id"], f["slug"])
        if not uuid or not start:
            print(f"  ! {f['slug']}: не нашёл газетку на странице")
            continue
        data = requests.get(LEAFLET_API.format(uuid), headers=UA, timeout=30).json()
        imgs = sorted(data.get("images_mobile") or [], key=lambda p: p["page"])
        if verbose:
            print(f"  распознаю: {f['slug']} — страниц {len(imgs)}")
        with ThreadPoolExecutor(WORKERS) as pool:
            pages = []
            for n, pg in enumerate(pool.map(ocr_page, [p["image"] for p in imgs]), 1):
                pages.append(pg)
                if verbose and n % 10 == 0:
                    print(f"    {n}/{len(imgs)}")
        spans, cur = [], None
        for pg in pages:  # даты шапки действуют до следующей шапки
            cur = page_dates(pg, start.year) or cur
            if cur:
                pg["dates"] = [cur[0].isoformat(), cur[1].isoformat()]
                spans.append(cur)
        end = max([e for _, e in spans] + [start + dt.timedelta(days=6)]) if spans else start + dt.timedelta(days=6)
        if end < today:
            continue
        # «codziennie-niskie-ceny-p-oferta-od-12-10» → «Codziennie niskie ceny» («-p»/«-l» — вид магазина)
        name = re.sub(r"(-[pl])?-oferta-od-\d+-\d+$", "", f["slug"]).replace("-", " ").capitalize()
        fresh[slug] = {"id": f["id"], "uuid": uuid, "fslug": f["slug"], "name": f"{name} — od {start:%d.%m}",
                       "start": start.isoformat(), "end": end.isoformat(), "pages": pages, "products": [], "ver": VER,
                       "images": [p["image"] for p in imgs]}
        record_history(slug, fresh[slug])
    index = {"updated": dt.datetime.now(M.TZ).isoformat(timespec="minutes"), "flyers": fresh}
    M.save_json(INDEX, index)
    # кэш закончившихся газеток и прошлой версии распознавания больше не нужен
    keep = {cache_file(u).name for f in fresh.values() for u in f["images"]}
    for p in OCR_DIR.glob("*.json"):
        if p.name not in keep:
            p.unlink()
    if verbose:
        print(f"Газеток Biedronka в индексе: {len(fresh)}")
    return index


# ---------------------------------------------------------------- разбор карточки

WORD = r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]"


def text_lines(text: str) -> list[str]:
    """Строки карточки без мусора сверху: к названию приклеиваются обрывки картинки товара («ET», «a»),
    крупная цена («34») и надписи плашки («SUPERCENA»)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    while lines and (not re.search(WORD + "{4}", lines[0]) or any(w in lines[0].lower() for w in NOT_NAME)):
        lines.pop(0)
    return lines


def is_product_block(b) -> bool:
    """Карточка товара: название (первая строка — слова, не служебная надпись) и вес/объём или цена за кг/л."""
    lines = text_lines(b[4])
    if not 2 <= len(lines) <= 6:
        return False
    if not re.match(WORD + "{3}", lines[0]):
        return False
    low = b[4].lower()
    # «49,95» отдельной строкой — цена за кг, у которой распознавание потеряло «zł/kg»
    return bool(UNIT_RE.search(low) or SIZE_RE.search(low) or "różne rodzaje" in low
                or any(re.fullmatch(r"\d{1,3},\d{2}", ln) for ln in lines[1:]))


def title_of(text: str) -> str:
    out = []
    for ln in text_lines(text):
        if UNIT_RE.search(ln.lower()) or ln.lower().startswith(("różne", "rozne")) or SIZE_RE.fullmatch(ln.strip()):
            break  # цена за кг, «różne rodzaje» или строка-вес («1 l») — название кончилось
        part = re.split(r",\s*(?=(?:\d+\s?[x×]\s?)?\d+(?:[.,]\d+)?\s?(?:kg|g|ml|l)\b)", ln)[0]  # «Łaciate, 200 g» → «Łaciate»
        out.append(part.strip(" ,"))
        if part != ln or len(out) == 3:
            break
    return " ".join(x for x in out if x)


def pack(text: str):
    """«200 g» → (0.2, 'kg'), «10x 100 g» → (1.0, 'kg'), «1 l» → (1, 'l'). Не вес: «zł/100 g» и цена за кг,
    у которой распознавание потеряло «zł/» («49,95 kg»)."""
    for m in SIZE_RE.finditer(text):
        if text[:m.start()].rstrip().endswith("/") or re.fullmatch(r"\d{2,},\d{2}", m.group(2)):
            continue
        n, v, u = int(m.group(1) or 1), float(m.group(2).replace(",", ".")), m.group(3).lower()
        return n * (v / 1000 if u in ("g", "ml") else v), ("kg" if u in ("g", "kg") else "l"), m.group(0)
    return None, None, None


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _cents(v: float) -> str:
    return f"{int(round(v * 100)) // 100}{int(round(v * 100)) % 100:02d}"


def big_pattern(token: str):
    """Крупная цена нарисована «2» и маленькими «72» — распознаётся «272», «64?» (6,49), «469,» (4,69).
    Возвращает шаблон цифр («64.») или None; неразобранная цифра — не больше одной."""
    t = re.sub(r"[,.*]+$", "", token.strip().replace(" ", ""))
    t = re.sub(r"(?<=\d)[,.](?=\d{2}$)", "", t)
    t = re.sub(r"[?'’”\"]", ".", t)
    if not re.fullmatch(r"\d[\d.]{2,3}", t) or t.count(".") > 1:
        return None
    return t


def _column_right(page: dict, p: list, prods: list) -> float:
    h = page["h"]
    return min([q[0] for q in prods if q[0] > p[0] + 40 and q[1] < p[3] + 0.05 * h and q[3] > p[1] - 0.05 * h],
               default=page["w"])


def _distance(page: dict, p: list, right: float, b: list):
    """Насколько блок b — плашка товара p: та же колонка; над названием — цена и условия (до 16 % высоты),
    под ним — «Cena przed obniżką», «Cena za 1 szt.», лимит (до 4,5 %: ниже — уже «DRUGI 28% TANIEJ» следующего
    товара колонки). Над названием расстояние ×1,5:
    плашки под названием прижаты к своему товару, а до следующего в колонке ещё его собственная цена.
    У крупных товаров плашка с ценой — слева от названия (is_left)."""
    h = page["h"]
    if not p[0] - 30 <= b[0] < right - 10:
        if is_left(page, p, b):
            return max(0, p[0] - b[2]) * 1.5 + 20
        return None
    if b[1] >= p[3] - 5:
        d = max(0, b[1] - p[3])
        return d if d <= 0.045 * h else None
    if b[3] <= p[1] + 5:
        d = max(0, p[1] - b[3])
        return d * 1.5 if d <= 0.16 * h else None
    return 0


def is_left(page: dict, p: list, b: list) -> bool:
    return b[2] <= p[0] + 15 and p[0] - b[2] <= 0.12 * page["w"] and b[1] < p[3] + 0.15 * page["h"] \
        and b[3] > p[1] - 0.03 * page["h"]


def owners(page: dict) -> dict:
    """{id(карточки товара): [её плашки, ближние первыми]} — каждая плашка достаётся одному товару: цены и условия
    соседей (другая колонка, товар выше или ниже) не подмешиваются."""
    prods = [b for b in page["blocks"] if is_product_block(b)]
    rights = {id(p): _column_right(page, p, prods) for p in prods}
    own = {id(p): [] for p in prods}
    for b in page["blocks"]:
        if id(b) in own or UNIT_RE.search(b[4].lower()):
            continue  # «… zł/kg» — строка названия товара, которую не удалось разобрать, а не плашка
        best = None
        for p in prods:
            d = _distance(page, p, rights[id(p)], b)
            if d is not None and (best is None or d < best[0]):
                best = (d, p)
        if best:
            own[id(best[1])].append((best[0], b))
    return {k: [b for _, b in sorted(v, key=lambda x: x[0])] for k, v in own.items()}


def build_deal(flyer: dict, slug: str, pno: int, page: dict, hit: list, own: list | None = None) -> dict:
    M = L()
    if own is None:
        own = owners(page).get(id(hit), [])
    low = hit[4].lower()
    text = "\n".join(b[4] for b in own).lower()
    flat = re.sub(r"\s+", " ", low + "\n" + text)
    up = UNIT_RE.search(low)
    qty, unit, size_txt = pack(hit[4])
    unit_price = up_unit = None
    if up:
        unit_price, up_unit = _num(up.group(1)), up.group(2).lower().replace(" ", "")
        if up_unit == "100g":
            unit_price, up_unit = round(unit_price * 10, 2), "kg"
    # обычная цена: подписанная («Cena przed obniżką», «Cena poza promocją») или число в плашках под названием
    old = None
    if m := re.search(r"(?:przed\s*obni\w*|poza\s*promocj\w*)\W{0,8}(\d{1,4}[,.]\d{2})", flat):
        old = _num(m.group(1))
    left = [b for b in own if b[2] <= hit[0] + 15]
    below = [b for b in own if b not in left and (b[1] >= hit[3] - 5 or b[0] > hit[2])]
    regular = [_num(x) for b in below for x in re.findall(r"(?<![\d,.])(\d{1,4}[,.]\d{2})(?!\d|\s*(?:zł)?\s*/)", b[4])]
    nocard = None
    if m := re.search(r"(?:bez\s*karty|poza\s*limitem)\D{0,30}?(\d{1,4}[,.]\d{2})", flat):
        nocard = _num(m.group(1))
    bigs = [pat for b in own if b[3] <= hit[1] + 5 or b in left for ln in b[4].splitlines() if (pat := big_pattern(ln))]
    price, approx = None, True
    if unit_price and qty and up_unit == unit:
        by_unit = round(unit_price * qty, 2)
        # цена за кг стоит под названием и считается от цены по акции (или от обычной, если акция «2+1»)
        if any(re.fullmatch(p, _cents(by_unit)) for p in bigs) or any(abs(v - by_unit) <= 0.011 for v in regular):
            approx = False
        price = by_unit
    elif unit_price and up_unit == "kg" and not qty and \
            any(re.fullmatch(p, _cents(unit_price / 10)) for p in bigs):  # «3,99 /100 g» — колбаса на вес
        price, approx, qty, unit, size_txt = round(unit_price / 10, 2), False, 0.1, "kg", "100 g"
    elif unit_price and up_unit in ("kg", "l") and not qty and \
            any(re.fullmatch(p, _cents(unit_price)) for p in bigs):  # «9,99 /kg» — мясо на вес
        price, approx, qty, unit, size_txt = unit_price, False, 1, up_unit, f"1 {up_unit}"
    elif exact := [p for p in bigs if "." not in p]:
        price = int(exact[0][:-2]) + int(exact[0][-2:]) / 100  # ближайшая крупная цена над названием
    if nocard and price and nocard <= price:
        nocard = None  # распознавание взяло чужое число
    if old is None and price:
        old = max((v for v in regular + ([nocard] if nocard else []) if price < v <= price * 3), default=None)
    if old and price and old <= price:
        old = None
    conditions, discount = [], None
    each = re.search(r"ka[żz]d[ae]\s*z(?:\s*(\d+)\s*sztuk|\b)", flat)  # «KAŻDA Z 2 SZTUK», часто — только «KAŻDA Z»
    # «DRUGI 70% TANIEJ, KAŻDA Z 2 SZTUK 2,72» — −70 % на второй, цена — средняя за штуку (−35 %);
    # «DRUGI» распознаётся отдельной строкой или теряется, но «każda z 2 sztuk» рядом с «% taniej» — то же самое.
    # «PRZY ZAKUPIE 3 50% TANIEJ, KAŻDA Z 3 SZTUK» — −50 % на каждую (и «każda z 3» без «drugi» — тоже)
    many = "przy zakupie" in flat or (each and each.group(1) and int(each.group(1)) > 2)
    second = re.search(r"(?<![\d,])([1-9]\d?)\s?%\s*tani", flat) if "drugi" in flat or (each and not many) else None
    if m := re.search(r"przy\s*zakupie\s*(\d+)", flat):
        conditions.append(f"цена за штуку при покупке {m.group(1)} шт.")
    bundle = re.search(r"(?<![\d,])(\d)\s?\+\s?(\d)\W{0,3}gratis", flat)
    if bundle and price:
        n, extra = int(bundle.group(1)), int(bundle.group(2))
        base = old or price  # без цены по акции цена за кг — обычная
        price, approx, old = round(base * n / (n + extra), 2), True, base
        discount = round(extra / (n + extra) * 100)
        conditions.append(f"купи {n} — ещё {extra} бесплатно, цена — средняя за штуку")
    elif bundle:
        conditions.append(f"купи {bundle.group(1)} — ещё {bundle.group(2)} бесплатно")
    if discount is None and old and price:
        discount = round((1 - price / old) * 100)
    if second:
        conditions.append(f"второй товар −{second.group(1)}%" + (", цена — средняя за штуку" if each else ""))
        if discount is None and each:
            discount = round(int(second.group(1)) / 2)
    elif each:
        conditions.append(f"цена за штуку при покупке {each.group(1) or 2} шт.")
    # «−30%», «30% taniej» — но не «3,2%» жирности молока и не «drugi 40% taniej»
    if discount is None and not second and \
            (m := re.search(r"(?<![\d,])-\s?([1-9]\d?)\s?%|(?<![\d,])([1-9]\d?)\s?%\s*taniej", text)):
        discount = int(m.group(1) or m.group(2))
    if "drugi tańszy" in flat or "drugitańszy" in flat or "drugi tanszy" in flat:
        conditions.append("скидка на второй, более дешёвый товар")
    if "moja biedronka" in flat or re.search(r"z\s?kart[ąa]|bez\s?karty", flat):
        conditions.append(f"с картой или приложением Moja Biedronka{f' (без карты {M.money(nocard)})' if nocard else ''}")
    if m := re.search(r"limit dzienny[^.]{0,60}", flat):
        conditions.append(m.group(0).strip().capitalize())
    dates = page.get("dates")  # шапка с датами — не на каждой странице: update переносит её на следующие
    dates = tuple(map(dt.date.fromisoformat, dates)) if dates else \
        (dt.date.fromisoformat(flyer["start"]), dt.date.fromisoformat(flyer["end"]))
    unit_out = up_unit if up_unit in ("kg", "l") else unit
    if unit_price and up_unit in ("kg", "l") and price and qty and abs(unit_price * qty - price) <= 0.02:
        uprice = unit_price  # как в газетке: 4,99 за 400 g — «12,48 zł/kg», а не 12,47
    else:
        uprice = round(price / qty, 2) if price and qty else (unit_price if up_unit in ("kg", "l") else None)
    uregular = round(uprice * old / price, 2) if uprice and old and price else None
    return {"title": title_of(hit[4]) or hit[4].splitlines()[0], "price": price, "approx": approx, "old_price": old,
            "size": size_txt, "unit": unit_out, "unit_price": uprice, "unit_regular": uregular, "discount": discount,
            "supercena": "supercena" in flat, "conditions": list(dict.fromkeys(conditions))[:4],
            "start": dates[0].isoformat(), "end": dates[1].isoformat(), "flyer": flyer["name"], "slug": slug,
            "page": pno + 1, "url": PAGE_URL.format(id=flyer["id"], slug=flyer["fslug"], page=pno + 1),
            "snippet": hit[4], "store": STORE, "ocr": True}


def is_food(b) -> bool:
    M = L()
    return M.looks_like_food_offer(b[4]) and not any(M.fold(w) in M.fold(b[4]) for w in NON_FOOD)


def page_deals(flyer: dict, slug: str, pno: int, page: dict, keep=lambda b: True):
    own = None
    for b in page["blocks"]:
        if is_product_block(b) and is_food(b) and keep(b):
            own = own if own is not None else owners(page)
            yield build_deal(flyer, slug, pno, page, b, own[id(b)])


def deals_of(flyer: dict, slug: str):
    for pno, page in enumerate(flyer["pages"]):
        yield from page_deals(flyer, slug, pno, page)


# ---------------------------------------------------------------- поиск и история (как у Lidl и Kaufland)

def search(index: dict, query: str, include_past=False) -> list[dict]:
    M = L()
    tokens = [t for t in M.fold(query).split() if len(t) > 2 or t.isdigit()]
    if not tokens or not index:
        return []

    def matches(text):
        words = re.findall(r"\w+", M.fold(text))
        return all(any(M.word_matches(w, t) for w in words) for t in tokens)

    seen = {}
    for slug, flyer in index["flyers"].items():
        for pno, page in enumerate(flyer["pages"]):
            for d in page_deals(flyer, slug, pno, page, keep=lambda b: matches(b[4])):
                if not include_past and d["end"] < M.today().isoformat():
                    continue
                if d["price"] is None and not d["discount"]:
                    continue
                key = (M.fold(d["title"]), d["start"], d["end"])
                if key not in seen or M.richness(d) > M.richness(seen[key]):
                    seen[key] = d
    return sorted(seen.values(), key=lambda d: (d["start"], d["title"]))


def record_history(slug: str, flyer: dict):
    M = L()
    # газетку распознали заново — её прежние строки (и названия, прочитанные иначе) больше не нужны
    hist = {k: kept for k, rows in load_json(HISTORY, {}).items() if (kept := [r for r in rows if r["slug"] != slug])}
    for d in deals_of(flyer, slug):
        if d["price"] is None and not d["discount"]:
            continue
        rows = hist.setdefault(M.fold(d["title"]), [])
        rows[:] = [r for r in rows if r["slug"] != slug] + [M.history_row(d)]
    BDATA.mkdir(parents=True, exist_ok=True)
    M.save_json(HISTORY, hist)


def previous_price(d: dict):
    rows = [r for r in load_json(HISTORY, {}).get(L().fold(d["title"]), [])
            if r["start"] < d["start"] and r["slug"] != d["slug"] and r.get("price") is not None]
    return max(rows, key=lambda r: r["start"]) if rows else None


def find_history(query: str) -> dict:
    M = L()
    tokens = [t for t in M.fold(query).split() if len(t) > 2 or t.isdigit()]
    out = {}
    for key, rows in load_json(HISTORY, {}).items():
        words = re.findall(r"\w+", key)
        if tokens and all(any(M.word_matches(w, t) for w in words) for t in tokens):
            out[key] = sorted(rows, key=lambda r: r["start"])
    return out
