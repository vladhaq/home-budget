"""Парсер акций Lidl.pl по газеткам + напоминания в Telegram (Избранное).

  python lidl.py                  меню: постоянные позиции / найти товар
  python lidl.py search <товар>   разовый поиск (без обновления, если индекс свежий)
  python lidl.py update           только скачать/обновить газетки
  python lidl.py watch add <товар> | watch rm <товар> | watch ls   — постоянные позиции
  python lidl.py history <товар>  история цен + график
  python lidl.py report           инфляция и сезонность по постоянным позициям (HTML-отчёт)
  python lidl.py auto           обновить + проверить постоянные позиции + поставить напоминания

Постоянные позиции лежат в файле «постоянные_позиции.txt» рядом со скриптом.
"""
import asyncio
import configparser
import datetime as dt
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

import pymupdf
import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
INDEX = DATA / "index.json"
SENT = DATA / "reminders.json"
HISTORY = DATA / "price_history.json"
WATCH = ROOT / "постоянные_позиции.txt"
CONFIG = ROOT / "config.ini"
SESSION = DATA / "telegram"  # файл входа в Telegram (budget подставляет общий)
TZ = ZoneInfo("Europe/Warsaw")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept-Language": "pl-PL,pl;q=0.9"}
LISTING_URL = "https://www.lidl.pl/c/nasze-gazetki/s10008614"
FLYER_API = "https://endpoints.leaflets.schwarz/v4/flyer"
FLYER_PAGE_URL = "https://www.lidl.pl/l/pl/gazetki/{slug}/view/flyer/page/{page}"

LEGAL_MARKERS = ("artykuły prezentowane", "przy produktach lub do wyczerpania",
                 "znaki firmowe", "więcej na lidl.pl")
PRICE_RE = re.compile(r"^\d{1,4},\d{2}\*?$")
DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?")
# непродуктовые марки Lidl и корм для животных — на случай, если попадутся в продуктовой газетке
NON_FOOD = ("silvercrest", "parkside", "livarno", "esmara", "lupilu", "crivit", "melinera", "auriol",
            "ultimate speed", "powerfix", "florabest", "cien", "sanitas", "nevadent", "zoofari",
            "whiskas", "pedigree", "felix", "coshida", "orlando", "karma dla", "dla kota", "dla psa")
# рекламные/информационные тексты, а не товары
PROMO_TEXT = ("sprawdziliśmy", "porównanie", "nutri-score", "zakupy zrobiliśmy", "regulamin", "akcja trwa")
UNIT_RE = re.compile(r"^\d+([.,]\d+)?\s?(g|kg|l|ml|szt|opak)\b|^\d+\s?x\s?\d+", re.I)


def fold(s: str) -> str:
    """Нижний регистр без польских диакритик: 'Masło' -> 'maslo'."""
    s = s.lower().replace("ł", "l")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- загрузка газеток

def is_food_flyer(slug: str) -> bool:
    """«katalog» у Lidl — непродуктовые товары (Parkside, Silvercrest...), кроме каталогов вина/алкоголя."""
    return "katalog" not in slug or any(w in slug for w in ("alkohol", "win"))


def flyer_slugs() -> list[str]:
    html = requests.get(LISTING_URL, headers=UA, timeout=30).text
    return sorted(s for s in set(re.findall(r"/l/pl/gazetki/([a-z0-9-]+)/ar/", html)) if is_food_flyer(s))


def extract_pdf(url: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "f.pdf"
        with requests.get(url, headers=UA, timeout=120, stream=True) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        pages = []
        with pymupdf.open(path) as doc:
            for pg in doc:
                blocks = [[round(b[0]), round(b[1]), round(b[2]), round(b[3]), b[4].strip()]
                          for b in pg.get_text("blocks") if b[6] == 0 and b[4].strip()]
                pages.append({"w": round(pg.rect.width), "h": round(pg.rect.height), "blocks": blocks})
        return pages


def flyer_products(f: dict) -> list[list]:
    """Товары, которые API газетки отдаёт структурно (обычно только непродуктовые каталоги)."""
    prods = f.get("products") or []
    prods = prods.values() if isinstance(prods, dict) else prods
    out = []
    for p in prods:
        try:
            out.append([p["title"], float(p["price"])])
        except (KeyError, TypeError, ValueError):
            pass
    return out


def update(verbose=True) -> dict:
    index = load_json(INDEX, {"flyers": {}})
    old = index["flyers"]
    fresh = {}
    for slug in flyer_slugs():
        try:
            f = requests.get(FLYER_API, params={"flyer_identifier": slug, "region_id": 0, "region_code": 0},
                             headers=UA, timeout=30).json()["flyer"]
        except Exception as e:  # noqa: BLE001
            print(f"  ! {slug}: {e}")
            continue
        end = dt.date.fromisoformat(f.get("offerEndDate") or f["endDate"])
        if end < today():
            continue
        if slug in old and old[slug]["id"] == f["id"]:
            fresh[slug] = old[slug]
            continue
        if verbose:
            print(f"  скачиваю: {f['name']} ({slug})")
        try:
            pages = extract_pdf(f["pdfUrl"])
        except Exception as e:  # noqa: BLE001
            print(f"  ! не удалось скачать PDF {slug}: {e}")
            continue
        fresh[slug] = {"id": f["id"], "name": f["name"],
                       "start": f.get("offerStartDate") or f["startDate"],
                       "end": f.get("offerEndDate") or f["endDate"], "pages": pages,
                       "products": flyer_products(f)}
        record_history(slug, fresh[slug])
    index = {"updated": dt.datetime.now(TZ).isoformat(timespec="minutes"), "flyers": fresh}
    save_json(INDEX, index)
    if verbose:
        print(f"Газеток в индексе: {len(fresh)}")
    return index


def history_row(d: dict) -> dict:
    discounted = d["old_price"] is not None or bool(d["discount"])
    regular = d["old_price"] if d["old_price"] is not None else (
        d["price"] if not discounted and not d["approx"] else None)
    return {"slug": d["slug"], "start": d["start"], "end": d["end"], "title": d["title"],
            "price": d["price"], "approx": d["approx"], "regular": regular,
            "discount": d["discount"], "size": d["size"], "unit": d["unit"],
            "unit_price": d["unit_price"], "unit_regular": d["unit_regular"]}


def record_history(slug: str, flyer: dict):
    """Каждое появление товара в газетке: обычная цена (для инфляции) и акция (для сезонности)."""
    hist = load_json(HISTORY, {})
    for pno, page in enumerate(flyer["pages"]):
        for b in page["blocks"]:
            if not is_product_block(b) or not looks_like_food_offer(b[4]):
                continue
            d = build_deal(flyer, slug, pno, page, b)
            if d["price"] is None and not d["discount"]:
                continue
            rows = hist.setdefault(fold(d["title"]), [])
            rows[:] = [r for r in rows if r["slug"] != slug] + [history_row(d)]
    save_json(HISTORY, hist)


def previous_price(d: dict):
    """Последняя цена этого товара в более ранней газетке (из нашей истории)."""
    rows = [r for r in load_json(HISTORY, {}).get(fold(d["title"]), [])
            if r["start"] < d["start"] and r["slug"] != d["slug"] and r.get("price") is not None]
    return max(rows, key=lambda r: r["start"]) if rows else None


def ensure_index(max_age_days=3) -> dict:
    index = load_json(INDEX, None)
    if index and index.get("updated"):
        age = dt.datetime.now(TZ) - dt.datetime.fromisoformat(index["updated"])
        if age.days < max_age_days:
            return index
    print("Обновляю газетки...")
    return update()


# ---------------------------------------------------------------- разбор карточки товара

def gap(a, b) -> float:
    """Расстояние между прямоугольниками (0, если пересекаются)."""
    dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
    return (dx * dx + dy * dy) ** 0.5


def is_legal(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in LEGAL_MARKERS) or re.fullmatch(r"r \d+/\d+", t) is not None


def is_date_block(text: str) -> bool:
    t = text.lower()
    return len(text) < 80 and DATE_RE.search(text) is not None and re.search(
        r"\b(od|do|tylko|oferta|ważn|wazn|poniedzia|wtor|środ|czwart|piąt|sobot|niedziel)", t) is not None


def parse_dates(text: str, flyer: dict):
    year = int(flyer["start"][:4])
    found = []
    for d, m, y in DATE_RE.findall(text):
        d, m = int(d), int(m)
        if not (1 <= d <= 31 and 1 <= m <= 12):
            continue
        yy = int(y) + (2000 if y and len(y) == 2 else 0) if y else year
        if not y and m < int(flyer["start"][5:7]) - 6:  # газетка на стыке декабря и января
            yy += 1
        try:
            found.append(dt.date(yy, m, d))
        except ValueError:
            pass
    if not found:
        return None
    low = text.lower()
    if len(found) == 1:
        if low.startswith("od") or " od " in low:
            return found[0], dt.date.fromisoformat(flyer["end"])
        return found[0], found[0]
    return min(found), max(found)


def title_of(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = []
    for ln in lines:
        if out and (UNIT_RE.match(ln) or "=" in ln or ln.startswith("*") or ln.lower().startswith("limit")):
            break
        out.append(ln)
        if len(out) == 3:
            break
    return " ".join(out)


def to_float(s: str):
    return float(s.replace("*", "").replace(",", ".")) if s else None


def is_product_block(b) -> bool:
    """Блок-карточка товара: несколько строк, вес/объём или цена за кг."""
    lines = [ln.strip() for ln in b[4].splitlines() if ln.strip()]
    return len(lines) >= 3 and any(UNIT_RE.match(ln) or "=" in ln for ln in lines)


def grow_cell(blocks: list, hit: list, h: int) -> list:
    """Блоки карточки: соседи товара, плюс цепочкой через «чужие» блоки без товаров
    (плашки «-30%», «Taniej drugi...»), но не дальше ~трети страницы вниз."""
    near = 0.045 * h
    cell = [b for b in blocks if gap(b, hit) <= near]
    frontier = [b for b in cell if not is_product_block(b) or b is hit]
    while frontier:
        cur = frontier.pop()
        for o in blocks:
            if o in cell or is_product_block(o) or gap(o, cur) > near:
                continue
            if o[1] - hit[3] > 0.3 * h or hit[1] - o[3] > 0.1 * h:
                continue
            cell.append(o)
            frontier.append(o)
    return cell


UNIT_PRICE_RE = re.compile(r"(1\s?kg|1\s?l|100\s?g|100\s?ml)\s*=\s*(\d+,\d{2})", re.I)
REGULAR_MARKERS = ("przed obniżką", "poza promocją", "najniższa cena z")


def parse_units(hit_text: str, cell_low: str, discounted: bool):
    """Цена за кг/л: акционная (из карточки) и обычная (из фразы «cena przed obniżką...»)."""
    def norm(m):
        base, val = m.group(1).lower().replace(" ", ""), to_float(m.group(2))
        unit = "kg" if base in ("1kg", "100g") else "l"
        return unit, round(val * (10 if base.startswith("100") else 1), 2)

    low = hit_text.lower()
    cut = min((low.find(k) for k in REGULAR_MARKERS if k in low), default=len(low))
    promo_m = UNIT_PRICE_RE.search(low[:cut])
    pos = min((cell_low.find(k) for k in REGULAR_MARKERS if k in cell_low), default=-1)
    reg_m = UNIT_PRICE_RE.search(cell_low[pos:]) if pos >= 0 else None
    promo_u = norm(promo_m) if promo_m else None
    reg_u = norm(reg_m) if reg_m else None
    if not discounted and promo_u and not reg_u:
        reg_u = promo_u  # скидки нет — цена за кг в карточке и есть обычная
    unit = (promo_u or reg_u or (None,))[0]
    return unit, promo_u[1] if promo_u else None, reg_u[1] if reg_u and reg_u[0] == unit else None


def build_deal(flyer: dict, slug: str, pno: int, page: dict, hit: list) -> dict:
    h = page["h"]
    blocks = [b for b in page["blocks"] if not is_legal(b[4])]
    cell = grow_cell([b for b in blocks if not is_date_block(b[4])], hit, h)
    text = "\n".join(b[4] for b in sorted(cell, key=lambda b: (b[1], b[0])))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    low = text.lower()

    old = None
    m = re.search(r"(?:przed obniżką|poza promocją|najniższa cena z 30 dni)[^:]*:\s*(?:1 kg = |1 l = )?(\d+,\d{2})", low)
    if m:
        old = to_float(m.group(1))
    prices = sorted({to_float(ln) for ln in lines if PRICE_RE.match(ln)})
    promo = next((p for p in prices if p != old), None)
    if old is None and len(prices) >= 2:
        old = prices[-1]
    discounts = [int(x) for x in re.findall(r"(?:-\s?|taniej o\s*)(\d{1,2})\s?%", text, re.I)]
    approx = False
    if promo is None and flyer.get("products"):
        tw = set(re.findall(r"\w{3,}", fold(title_of(hit[4]))))
        best, score = None, 0.0
        for ptitle, pprice in flyer["products"]:
            pw = set(re.findall(r"\w{3,}", fold(ptitle)))
            s = len(tw & pw) / max(1, len(tw | pw))
            if s > score:
                best, score = pprice, s
        if score >= 0.5:
            promo = best
    # «6 + 6 gratis», «2 + 1 za grosz»: рядом стоит обычная цена за штуку — считаем реальную
    bundle = None
    m = re.search(r"(\d{1,2})\s*\+\s*(\d{1,2})\s*(gratis|za grosz)", re.sub(r"\s+", " ", low))
    if m:
        n, extra, kind = int(m.group(1)), int(m.group(2)), m.group(3)
        base = old or (prices[-1] if prices else None)
        bundle = {"n": n, "extra": extra, "kind": kind, "base": base}
        if base:
            eff = round(base * n / (n + extra) + (0.01 * extra / (n + extra) if kind == "za grosz" else 0), 2)
            if promo is None or promo >= base or abs(promo - eff) / eff > 0.1:
                promo, approx = eff, True
            old = base
        if not discounts:
            discounts = [round(extra / (n + extra) * 100)]
    if old and discounts and not bundle:
        # цена часто нарисована картинкой или рядом стоит цена соседнего товара — сверяемся со скидкой
        expected = round(old * (1 - max(discounts) / 100), 2)
        if promo is None or abs(promo - expected) / expected > 0.1:
            promo, approx = expected, True

    conditions = []
    flat = re.sub(r"\s+", " ", low)
    if "aktywuj kupon" in low or "lidl plus" in low:
        conditions.append("нужен купон Lidl Plus (активировать в приложении)")
    if "drugi, tańszy" in flat or "drugi tańszy" in flat:
        conditions.append("скидка на второй, более дешёвый товар")
    if "miksuj dowolnie" in flat:
        conditions.append("можно комбинировать разные товары акции")
    if bundle:
        n, extra, total = bundle["n"], bundle["extra"], bundle["n"] + bundle["extra"]
        what = "бесплатно" if bundle["kind"] == "gratis" else "за 1 grosz"
        per = f", выходит ≈{money(promo)}/шт." if bundle["base"] else ""
        conditions.append(f"купи {n} — ещё {extra} {what} (бери {total}{per})")
    for ln in lines:
        l2 = re.sub(r"\s+", " ", ln.lower())
        if l2 in ("taniej", "drugi, tańszy", "produkt", "miksuj", "dowolnie", "gratis", "za grosz*"):
            continue
        if bundle and re.fullmatch(r"\d+ \+ \d+( gratis\*?| za grosz\*?)?", l2):
            continue
        if any(k in l2 for k in ("przy zakupie", "cena za 1", "gratis", "limit:", "+1", "za 1 zł")):
            conditions.append(ln.lstrip("* "))

    # даты: ближайший к товару блок-«шапка» с датами, иначе даты газетки
    dates = None
    date_blocks = [b for b in blocks if is_date_block(b[4])]
    if date_blocks:
        above = [b for b in date_blocks if b[1] <= hit[1]] or date_blocks
        best = min(above, key=lambda b: gap(b, hit))
        dates = parse_dates(best[4], flyer)
    if not dates:
        dates = dt.date.fromisoformat(flyer["start"]), dt.date.fromisoformat(flyer["end"])

    discount = max(discounts) if discounts else None
    if discount is None and promo and old and old > promo:
        discount = round((1 - promo / old) * 100)

    size = next((ln.strip() for ln in hit[4].splitlines()[1:] if UNIT_RE.match(ln.strip())), None)
    unit, unit_price, unit_regular = parse_units(hit[4], low, discounted=old is not None)
    one = re.fullmatch(r"1\s?(kg|l)\b.*", (size or "").lower())
    if one:  # «1 kg», «1 L» — цена за упаковку и есть цена за кг/л
        unit = one.group(1)
        unit_price = unit_price or promo
        unit_regular = unit_regular or old or (promo if not discounts else None)

    return {"title": title_of(hit[4]), "price": promo, "approx": approx, "old_price": old,
            "size": size, "unit": unit, "unit_price": unit_price, "unit_regular": unit_regular,
            "discount": discount, "supercena": "supercena" in low,
            "conditions": list(dict.fromkeys(conditions))[:4],
            "start": dates[0].isoformat(), "end": dates[1].isoformat(),
            "flyer": flyer["name"], "slug": slug, "page": pno + 1,
            "url": FLYER_PAGE_URL.format(slug=slug, page=pno + 1), "snippet": text}


def richness(d: dict) -> int:
    """Сколько полезного распознано — из дублей оставляем самый полный."""
    return (d["price"] is not None and not d["approx"]) * 4 + (d["old_price"] is not None) * 2 \
        + bool(d["discount"]) + len(d["conditions"])


def word_matches(word: str, token: str) -> bool:
    """Польские окончания: 'ser' ~ serem/serek, 'kawa' ~ kawy, но не serdelki/kawałkami."""
    stem = token[:-1] if len(token) >= 4 and token[-1] in "aeiouy" else token
    return word.startswith(stem) and len(word) - len(stem) <= 3


def looks_like_food_offer(text: str) -> bool:
    low = text.lower()
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    if len(first) > 70 or len(text.split()) > 45:  # длинный абзац — рекламный или юридический текст
        return False
    if any(p in low for p in PROMO_TEXT):
        return False
    folded = fold(text)
    return not any(fold(w) in folded for w in NON_FOOD)


def search(index: dict, query: str, include_past=False) -> list[dict]:
    tokens = [t for t in fold(query).split() if len(t) > 2 or t.isdigit()]  # 'z', 'w', 'i' — предлоги
    if not tokens:
        return []
    seen: dict = {}
    for slug, flyer in index["flyers"].items():
        for pno, page in enumerate(flyer["pages"]):
            for b in page["blocks"]:
                if is_legal(b[4]) or is_date_block(b[4]):
                    continue
                words = re.findall(r"\w+", fold(b[4]))
                if not all(any(word_matches(w, t) for w in words) for t in tokens):
                    continue
                if not looks_like_food_offer(b[4]):
                    continue
                d = build_deal(flyer, slug, pno, page, b)
                if not include_past and d["end"] < today().isoformat():
                    continue
                if d["price"] is None and not d["discount"] and not d["conditions"]:
                    continue  # ни цены, ни скидки — это не акция, а просто упоминание
                key = (fold(d["title"]), d["start"], d["end"])
                prev = seen.get(key)
                if prev is None:
                    seen[key] = d
                elif richness(d) > richness(prev):
                    seen[key] = d
    deals = list(seen.values())
    deals.sort(key=lambda d: (d["start"], d["title"]))
    return deals


# ---------------------------------------------------------------- вывод

def fmt_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return d.strftime("%d.%m") + " " + ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][d.weekday()]


def money(x) -> str:
    return f"{x:.2f}".replace(".", ",") + " zł"


def describe(d: dict, html=False) -> str:
    parts = []
    if d["price"] is not None:
        parts.append(("≈" if d.get("approx") else "") + money(d["price"]))
    if d["old_price"] is not None and d["old_price"] != d["price"]:
        parts.append(f"(было {money(d['old_price'])})")
    if d["discount"]:
        parts.append(f"−{d['discount']}%")
    if d["price"] is None and d["discount"]:
        parts = [f"скидка −{d['discount']}% (цена в газетке не указана)"]
    no_discount = d["price"] is not None and d["old_price"] is None and not d["discount"]
    if no_discount:
        prev = previous_price(d)
        if prev and prev["price"] != d["price"]:
            diff = round((d["price"] / prev["price"] - 1) * 100)
            parts.append(f"(в газетке от {fmt_date(prev['start'])}: {money(prev['price'])}, {diff:+d}%)")
        else:
            label = "плашка «Supercena», но " if d.get("supercena") else ""
            parts.append(f"— {label}скидки нет, это обычная цена из газетки")
    price = " ".join(parts) or "цена не распознана — см. газетку"
    period = fmt_date(d["start"]) if d["start"] == d["end"] else f"{fmt_date(d['start'])} – {fmt_date(d['end'])}"
    lines = [d["title"], f"  {price}", f"  когда: {period}"]
    lines += [f"  • {c}" for c in d["conditions"]]
    lines.append(f"  {d['url']}" if not html else f"  газетка: {d['url']}")
    return "\n".join(lines)


def status(d: dict) -> str:
    t = today()
    start, end = dt.date.fromisoformat(d["start"]), dt.date.fromisoformat(d["end"])
    if start > t:
        days = (start - t).days
        return f"начнётся {'завтра, ' if days == 1 else ''}{fmt_date(d['start'])}"
    if end == t:
        return "идёт — последний день сегодня!"
    if end == t + dt.timedelta(days=1):
        return f"идёт, заканчивается завтра ({fmt_date(d['end'])})"
    return f"идёт, до {fmt_date(d['end'])}"


def print_deals(query: str, deals: list[dict], start_no=1):
    if not deals:
        print(f"«{query}»: скидок в текущих газетках не нашёл.\n")
        return
    print(f"«{query}»: найдено {len(deals)}\n")
    for i, d in enumerate(deals, start_no):
        print(f"[{i}] {status(d).upper()}\n    " + describe(d).replace("\n", "\n  "))
        print()


# ---------------------------------------------------------------- история цен, инфляция, сезонность

MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
REPORT = DATA / "report.html"


def find_history(query: str) -> dict[str, list[dict]]:
    tokens = [t for t in fold(query).split() if len(t) > 2 or t.isdigit()]
    out = {}
    for key, rows in load_json(HISTORY, {}).items():
        words = re.findall(r"\w+", key)
        if tokens and all(any(word_matches(w, t) for w in words) for t in tokens):
            out[key] = sorted(rows, key=lambda r: r["start"])
    return out


def regular_series(rows: list[dict]):
    """Ряд обычных цен. Если у большинства записей есть цена за кг/л — берём её (видна «шринкфляция»)."""
    unit_rows = [r for r in rows if r.get("unit_regular")]
    if unit_rows and len(unit_rows) >= len(rows) / 2:
        return [(r["start"], r["unit_regular"]) for r in unit_rows], f"zł/{unit_rows[0]['unit']}"
    return [(r["start"], r["regular"]) for r in rows if r.get("regular")], "zł/шт."


def change(series):
    """Изменение от первой обычной цены к последней (нужно ≥ 2 разных даты)."""
    if len(series) < 2 or series[0][0] == series[-1][0]:
        return None
    (d0, p0), (d1, p1) = series[0], series[-1]
    return d0, p0, d1, p1, (p1 / p0 - 1) * 100


def seasonality(rows: list[dict]) -> dict[int, dict]:
    months = {}
    for r in rows:
        if not r.get("discount"):
            continue
        s = months.setdefault(int(r["start"][5:7]), {"n": 0, "best": 0})
        s["n"] += 1
        s["best"] = max(s["best"], r["discount"])
    return months


def short_date(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[2:4]}"


def opt_money(x) -> str:
    return money(x) if x is not None else "—"


def print_history(rows: list[dict]):
    print(f"\n{rows[-1]['title']}  ({len(rows)} записей, с {rows[0]['start']})")
    print(f"  {'дата':<10} {'цена':>11} {'обычная':>10} {'скидка':>7} {'за кг/л':>14}  упаковка")
    for r in rows:
        unit = f"{opt_money(r.get('unit_price') or r.get('unit_regular'))}/{r['unit']}" if r.get("unit") else "—"
        disc = f"−{r['discount']}%" if r.get("discount") else ""
        price = ("≈" if r.get("approx") else "") + opt_money(r.get("price"))
        print(f"  {r['start']:<10} {price:>11} {opt_money(r.get('regular')):>10} {disc:>7} {unit:>14}  {r.get('size') or ''}")
    series, unit = regular_series(rows)
    ch = change(series)
    if ch:
        print(f"  Обычная цена: {money(ch[1])} → {money(ch[3])} ({unit}), {ch[4]:+.1f}% с {ch[0]}")
    promos = [r for r in rows if r.get("discount") and r.get("price") is not None]
    if promos:
        best = min(promos, key=lambda r: r["price"])
        print(f"  Лучшая цена по акции: {money(best['price'])} (−{best['discount']}%, {best['start']})")
    seas = seasonality(rows)
    if seas:
        print("  Акции по месяцам: " + ", ".join(
            f"{MONTHS[m - 1]} ×{s['n']} (до −{s['best']}%)" for m, s in sorted(seas.items())))
    if len({r["start"][:7] for r in rows}) < 6:
        print("  (данных пока мало — сезонность станет видна после нескольких месяцев записи)")


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def chart_block(cid: str, rows: list[dict]) -> str:
    series, unit = regular_series(rows)
    labels = sorted({r["start"] for r in rows})
    reg = dict(series)
    by_unit = unit != "zł/шт."
    promo = {r["start"]: (r.get("unit_price") if by_unit else r.get("price")) for r in rows if r.get("discount")}
    data = {"labels": labels, "datasets": [
        {"label": f"обычная цена, {unit}", "data": [reg.get(d) for d in labels],
         "borderColor": "#2563eb", "backgroundColor": "#2563eb", "spanGaps": True, "tension": 0.2},
        {"label": f"цена по акции, {unit}", "data": [promo.get(d) for d in labels],
         "borderColor": "#dc2626", "backgroundColor": "#dc2626", "showLine": False, "pointRadius": 5},
    ]}
    return (f'<canvas id="{cid}" height="110"></canvas>'
            f'<script>charts.push(["{cid}", {json.dumps(data, ensure_ascii=False)}]);</script>')


def season_table(rows: list[dict]) -> str:
    seas = seasonality(rows)
    cells = []
    for m in range(1, 13):
        s = seas.get(m)
        body = f"×{s['n']}<br>до −{s['best']}%" if s else "—"
        cells.append(f"<td class='{'hot' if s else ''}'>{MONTHS[m - 1]}<br>{body}</td>")
    return "<table class='season'><tr>" + "".join(cells) + "</tr></table>"


REPORT_HEAD = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Lidl — история цен</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#111;background:#fff}
h3{margin-top:28px} .muted{color:#666;font-weight:normal;font-size:.9em}
table{border-collapse:collapse;margin:8px 0} td,th{border:1px solid #ddd;padding:4px 8px;text-align:center;font-size:.9em}
.season td{width:8%} .season td.hot{background:#fde2e2}</style></head><body>
<h1>Lidl — история цен</h1><script>const charts=[];</script>
"""
REPORT_TAIL = """<script>for (const [id, data] of charts) new Chart(document.getElementById(id), {type: "line", data,
 options: {plugins: {legend: {position: "bottom"}}}});</script></body></html>"""


def write_report(sections: list, summary: list[list[str]]) -> Path:
    parts = [f"<p class='muted'>Сформировано {dt.datetime.now(TZ):%d.%m.%Y %H:%M}. "
             "Синяя линия — обычная цена (инфляция), красные точки — цены по акциям. "
             "Таблица под графиком — в какие месяцы были акции (сезонность).</p>"]
    if summary:
        parts.append("<h2>Инфляция по постоянным позициям</h2><table><tr><th>товар</th><th>было</th>"
                     "<th>стало</th><th>изменение</th></tr>"
                     + "".join("<tr>" + "".join(f"<td>{html_escape(c)}</td>" for c in r) + "</tr>" for r in summary)
                     + "</table>")
    n = 0
    for query, items in sections:
        parts.append(f"<h2>{html_escape(query)}</h2>")
        if not items:
            parts.append("<p class='muted'>в истории пока нет</p>")
        for rows in items:
            n += 1
            last = rows[-1]
            parts.append(f"<h3>{html_escape(last['title'])} <span class='muted'>"
                         f"{html_escape(last.get('size') or '')} · записей: {len(rows)}</span></h3>")
            parts.append(chart_block(f"c{n}", rows) + season_table(rows))
    DATA.mkdir(exist_ok=True)
    REPORT.write_text(REPORT_HEAD + "\n".join(parts) + REPORT_TAIL, encoding="utf-8")
    return REPORT


def open_file(path: Path):
    try:
        os.startfile(path)
    except (AttributeError, OSError):
        print(f"Открой вручную: {path}")


def show_history(query: str, open_it=True):
    found = find_history(query)
    if not found:
        print(f"«{query}»: в истории цен пока нет.")
        return
    for rows in found.values():
        print_history(rows)
    path = write_report([(query, list(found.values()))], [])
    print(f"\nГрафик: {path}")
    if open_it:
        open_file(path)


def inflation_report(open_it=True):
    items = watchlist()
    if not items:
        print(f"Список постоянных позиций пуст: {WATCH}")
        return
    sections, summary, changes = [], [], []
    print(f"\n{'товар':<45} {'было':>20} {'стало':>20} {'изм.':>8}")
    for q in items:
        found = list(find_history(q).values())
        sections.append((q, found))
        for rows in found:
            series, unit = regular_series(rows)
            ch = change(series)
            if not ch:
                continue
            d0, p0, d1, p1, pct = ch
            changes.append(pct)
            title = rows[-1]["title"]
            print(f"{title[:44]:<45} {money(p0) + ' ' + short_date(d0):>20} {money(p1) + ' ' + short_date(d1):>20} {pct:>+7.1f}%")
            summary.append([f"{title} ({unit})", f"{money(p0)} ({d0})", f"{money(p1)} ({d1})", f"{pct:+.1f}%"])
    if changes:
        avg = sum(changes) / len(changes)
        print(f"\nВ среднем по корзине: {avg:+.1f}% ({len(changes)} товаров)")
        summary.append(["В среднем по корзине", "", "", f"{avg:+.1f}%"])
    else:
        print("Пока не с чем сравнивать: у товаров меньше двух записей в разные даты — нужно подождать пару недель.")
    path = write_report(sections, summary)
    print(f"Отчёт с графиками и сезонностью: {path}")
    if open_it:
        open_file(path)


# ---------------------------------------------------------------- Telegram

def remind_clock(tg) -> dt.time:
    """Во сколько напоминать накануне: remind_at = ЧЧ:ММ (или старое remind_hour = Ч), по умолчанию 19:00."""
    raw = (tg.get("remind_at") or "").strip() or f"{(tg.get('remind_hour') or '19').strip()}:00"
    h, m = (int(x) for x in raw.split(":")[:2])
    return dt.time(h, m)


def remind_time(d: dict, clock: dt.time) -> dt.datetime | None:
    """Вечер накануне начала акции. None — отправить сразу."""
    start = dt.date.fromisoformat(d["start"])
    at = dt.datetime.combine(start - dt.timedelta(days=1), clock, TZ)
    return at if at > dt.datetime.now(TZ) + dt.timedelta(minutes=1) else None


def reminder_text(d: dict, at) -> str:
    if at is None and d["start"] <= today().isoformat():
        head = f"🛒 Lidl: уже на скидке — {status(d)}"
    else:
        head = f"🛒 Lidl: завтра ({fmt_date(d['start'])}) на скидке — стоит зайти в магазин"
    return head + "\n\n" + describe(d, html=True)


def deal_key(d: dict) -> str:
    return f"{d['slug']}|{d['page']}|{fold(d['title'])}|{d['start']}"


def schedule_reminders(deals: list[dict], interactive: bool = True) -> list[dict]:
    """interactive=False (интерфейс бюджета, автообновление): без входа не спрашиваем телефон, а сообщаем об ошибке."""
    if not deals:
        return []
    cfg = configparser.ConfigParser()
    if not cfg.read(CONFIG, encoding="utf-8") or not cfg.get("telegram", "api_id", fallback="").strip():
        if not interactive:
            raise RuntimeError("Нет ключей Telegram (api_id/api_hash) в config.ini")
        print("! Нет config.ini с api_id/api_hash — см. README.md. Напоминания не поставлены.")
        return []
    from telethon import TelegramClient

    tg = cfg["telegram"]
    clock = remind_clock(tg)
    sent = set(load_json(SENT, []))
    results = []

    async def run():
        client = TelegramClient(str(SESSION), int(tg["api_id"]), tg["api_hash"])
        if not interactive:
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                raise RuntimeError("Нужен вход в Telegram: python budget.py telegram")
        # при первом запуске Telethon спросит в терминале телефон и код из Telegram
        async with client:
            for d in deals:
                key = deal_key(d)
                if key in sent:
                    print(f"  уже напоминал: {d['title']}")
                    results.append({"title": d["title"], "skipped": True})
                    continue
                at = remind_time(d, clock)
                await client.send_message("me", reminder_text(d, at), schedule=at, link_preview=False)
                sent.add(key)
                when = at.strftime("%d.%m %H:%M") if at else "сразу"
                results.append({"title": d["title"], "when": when})
                print(f"  ✓ {d['title']} — напоминание в Избранном: {when}")

    DATA.mkdir(exist_ok=True)
    try:
        asyncio.run(run())
    finally:
        save_json(SENT, sorted(sent))
    return results


# ---------------------------------------------------------------- команды

def pick(deals: list[dict]) -> list[dict]:
    try:
        ans = input("Напомнить в Telegram? Номера через запятую, 'all' или Enter — нет: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return []
    if not ans:
        return []
    if ans in ("all", "все", "a"):
        return [d for d in deals if d["start"] >= today().isoformat()] or deals
    nums = {int(x) for x in re.findall(r"\d+", ans)}
    return [d for i, d in enumerate(deals, 1) if i in nums]


def ask(prompt: str) -> str | None:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return None


def check_positions(index: dict):
    items = watchlist()
    if not items:
        print(f"Список пуст. Впиши товары в файл: {WATCH}\n")
        return
    found = []
    for q in items:
        deals = search(index, q)
        print_deals(q, deals, start_no=len(found) + 1)
        found += deals
    if found:
        schedule_reminders(pick(found))


def search_loop(index: dict):
    print("Вводи товар по-польски (диакритику можно не ставить: maslo, kawa, piers z kurczaka).")
    print("«+товар» — добавить в постоянные позиции. Пусто — назад в меню.\n")
    while True:
        q = ask("товар> ")
        if not q:
            return
        if q.startswith("+"):
            add_position(q[1:].strip())
            continue
        deals = search(index, q)
        print_deals(q, deals)
        if deals:
            schedule_reminders(pick(deals))


def interactive():
    index = ensure_index(max_age_days=1)
    print(f"Индекс от {index['updated']}, газеток: {len(index['flyers'])}.")
    while True:
        items = watchlist()
        print("\n1 — постоянные позиции" + (f" ({len(items)}: {', '.join(items)})" if items else " (пусто)"))
        print("2 — найти товар")
        print("3 — редактировать список постоянных позиций")
        print("4 — история цен товара (таблица + график)")
        print("5 — инфляция и сезонность по постоянным позициям")
        print("Enter — выход")
        choice = ask("> ")
        if not choice:
            return
        if choice == "1":
            check_positions(index)
        elif choice == "2":
            search_loop(index)
        elif choice == "3":
            ensure_positions_file()
            os.startfile(WATCH)  # откроется в Блокноте; после сохранения снова выбери 1
            print(f"Открыл {WATCH.name}. Сохрани файл и выбери 1.")
        elif choice == "4":
            q = ask("товар> ")
            if q:
                show_history(q)
        elif choice == "5":
            inflation_report()


POSITIONS_HEADER = """\
# Постоянные позиции: по одному товару на строку, по-польски, диакритику можно не ставить.
# Строки, начинающиеся с #, игнорируются. Примеры:
# maslo
# kawa ziarnista
# piers z kurczaka
"""


def ensure_positions_file():
    if not WATCH.exists():
        WATCH.write_text(POSITIONS_HEADER, encoding="utf-8")


def watchlist() -> list[str]:
    try:
        return [ln.strip() for ln in WATCH.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
    except FileNotFoundError:
        return []


def add_position(q: str):
    if not q:
        return
    ensure_positions_file()
    if fold(q) in {fold(i) for i in watchlist()}:
        print(f"«{q}» уже в постоянных позициях.")
        return
    text = WATCH.read_text(encoding="utf-8")
    WATCH.write_text(text + ("" if text.endswith("\n") else "\n") + q + "\n", encoding="utf-8")
    print(f"Добавил «{q}» в постоянные позиции.")


def remove_position(q: str):
    lines = WATCH.read_text(encoding="utf-8").splitlines() if WATCH.exists() else []
    keep = [ln for ln in lines if ln.lstrip().startswith("#") or fold(ln.strip()) != fold(q)]
    WATCH.write_text("\n".join(keep) + "\n", encoding="utf-8")


def main(argv: list[str]):
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = argv[0] if argv else ""
    if cmd == "update":
        update()
    elif cmd == "search":
        q = " ".join(argv[1:])
        deals = search(ensure_index(), q)
        print_deals(q, deals)
        if deals and sys.stdin.isatty():
            schedule_reminders(pick(deals))
    elif cmd == "watch":
        sub, q = (argv[1] if len(argv) > 1 else "ls"), " ".join(argv[2:]).strip()
        if sub == "add":
            add_position(q)
        elif sub == "rm":
            remove_position(q)
        print("Постоянные позиции:", ", ".join(watchlist()) or "(пусто)")
    elif cmd == "history":
        show_history(" ".join(argv[1:]))
    elif cmd == "report":
        inflation_report()
    elif cmd == "rebuild-history":
        for slug, flyer in ensure_index()["flyers"].items():
            record_history(slug, flyer)
        print("История пересобрана из текущих газеток.")
    elif cmd == "auto":
        index = update(verbose=False)
        todo = []
        for q in watchlist():
            deals = [d for d in search(index, q) if d["start"] >= today().isoformat()]
            print_deals(q, deals)
            todo += deals
        schedule_reminders(todo)
    elif cmd in ("", "run"):
        interactive()
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
