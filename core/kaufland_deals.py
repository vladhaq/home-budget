"""Газетки Kaufland: те же источники и форма данных, что у газеток Lidl (lidl-deals/lidl.py), — для вкладки «Kaufland»
на странице «Скидки».

Список текущих газеток — со страницы sklep.kaufland.pl/gazeta-reklamowa.html (идентификаторы вида
PL_pl_KDZ_1060_PL40-LFT), сами газетки — через тот же сервис Schwarz, что у Lidl (endpoints.leaflets.schwarz),
текст — из PDF. Разметка у Kaufland другая: описание товара отдельным блоком («HERBAPOL / Syrop / 420 ml butelka /
(=1 l 11,88)»), цены — блоками рядом («4,99», «cena przed obniżką 5,99», «cena bez karty 13,29»). Цену ищем рядом
с описанием и сверяем с ценой за кг/л из описания: если не сходится — считаем по ней (≈).
"""
import datetime as dt
import re

import requests

from core.common import DATA
from core.deals import mod as lidl_mod

KDATA = DATA / "kaufland_deals"
INDEX = KDATA / "index.json"
HISTORY = KDATA / "price_history.json"
WATCH = KDATA / "постоянные_позиции.txt"  # свой список Kaufland (у Lidl — lidl-deals/постоянные_позиции.txt)
LIST_URL = "https://sklep.kaufland.pl/gazeta-reklamowa.html"
PAGE_URL = "https://leaflets.kaufland.com/pl-PL/{slug}/ar/{page}"
STORE = "Kaufland"

PRICE_RE = re.compile(r"^\d{1,4},\d{2}\*?$")
SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s?(?:[x×]\s?(\d+(?:[.,]\d+)?)\s?)?(kg|g|ml|l|szt|sztuk|opak)\b", re.I)
UNITP_RE = re.compile(r"\((?:z\s?kartą\s?)?=\s?(\d+)\s?(g|kg|ml|l)\s(\d+,\d{2})", re.I)
PACK_WORDS = r"(?:butelka|puszka|słoik|opakowanie|opak\.|torebka|kubek|karton|tacka|sztuka|szt\.|pęczek|luzem|paczka|wiadro|tuba)"
NOT_PRODUCT = ("uwaga!", "oferta ważna", "sprzedaż alkoholu", "zawiera", "zdjęcia poglądowe", "nie łączymy", "cena wyłącznie",
               "najniższa cena", "cena regularna", "cena przed", "cena bez karty", "przy zakupie", "limit", "teraz zapłacisz",
               "potwierdzone", "złotym laurem", "partner tygodnia", "aplikacj", "kaufland card", "regulamin")


def L():
    return lidl_mod()


def load_json(path, default):
    return L().load_json(path, default)


# ---------------------------------------------------------------- постоянные позиции Kaufland

def ensure_watch():
    """При первом запуске — копия списка Lidl, дальше списки живут отдельно."""
    if not WATCH.exists():
        KDATA.mkdir(parents=True, exist_ok=True)
        start = L().watchlist()
        WATCH.write_text("# Постоянные позиции Kaufland — по одной на строку (товар по-польски, диакритику можно не ставить)\n"
                         + "".join(q + "\n" for q in start), encoding="utf-8")


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


# ---------------------------------------------------------------- загрузка

def flyer_ids() -> list[str]:
    html = requests.get(LIST_URL, headers=L().UA, timeout=30).text
    return sorted(set(re.findall(r"PL_pl_[A-Za-z0-9_\-]+", html)))


def update(verbose=True) -> dict:
    M = L()
    index = load_json(INDEX, {"flyers": {}})
    old, fresh = index["flyers"], {}
    for slug in flyer_ids():
        try:
            f = requests.get(M.FLYER_API, params={"flyer_identifier": slug, "region_id": 0, "region_code": 0},
                             headers=M.UA, timeout=30).json()["flyer"]
        except Exception as e:  # noqa: BLE001
            print(f"  ! {slug}: {e}")
            continue
        end = dt.date.fromisoformat(f.get("offerEndDate") or f["endDate"])
        if end < M.today():
            continue
        if slug in old and old[slug]["id"] == f["id"]:
            fresh[slug] = old[slug]
            continue
        if verbose:
            print(f"  скачиваю: {f['name']} ({slug})")
        try:
            pages = M.extract_pdf(f["pdfUrl"])
        except Exception as e:  # noqa: BLE001
            print(f"  ! не удалось скачать PDF {slug}: {e}")
            continue
        fresh[slug] = {"id": f["id"], "name": f["name"], "start": f.get("offerStartDate") or f["startDate"],
                       "end": f.get("offerEndDate") or f["endDate"], "pages": pages, "products": []}
        record_history(slug, fresh[slug])
    index = {"updated": dt.datetime.now(M.TZ).isoformat(timespec="minutes"), "flyers": fresh}
    M.save_json(INDEX, index)
    if verbose:
        print(f"Газеток Kaufland в индексе: {len(fresh)}")
    return index


# ---------------------------------------------------------------- разбор карточки

def is_product_block(b) -> bool:
    """Описание товара: 2–9 строк, начинается с названия (марка ЗАГЛАВНЫМИ или «Polska marchew»),
    есть фасовка («420 ml butelka», «1 kg», «4 x 0,5 l») или цена за кг/л."""
    text = b[4]
    low = text.lower()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not 2 <= len(lines) <= 9 or len(text) > 260 or any(w in low for w in NOT_PRODUCT):
        return False
    first = lines[0]
    if PRICE_RE.match(first) or re.match(r"^[-(\d]", first) or not first[0].isupper():
        return False
    has_size = SIZE_RE.search(low) or re.search(PACK_WORDS, low)
    return bool(has_size and re.search(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{3}", first))


def title_of(text: str) -> str:
    out = []
    for ln in (x.strip() for x in text.splitlines() if x.strip()):
        low = ln.lower()
        if out and (SIZE_RE.search(low) or low.startswith(("(", "różne", "rozne", "marki"))):
            break
        out.append(ln)
        if len(out) == 3:
            break
    return " ".join(out)


def size_amount(text: str):
    """«420 ml» -> (0.42, 'l'); «4 x 0,5 l» -> (2.0, 'l'); «500 g» -> (0.5, 'kg'); «10 szt» -> (10, 'szt')."""
    for m in SIZE_RE.finditer(text.lower()):
        a, b, u = m.group(1), m.group(2), m.group(3).lower()
        v = float(a.replace(",", ".")) * (float(b.replace(",", ".")) if b else 1)
        if u in ("g", "ml"):
            return v / 1000, "kg" if u == "g" else "l"
        if u in ("kg", "l"):
            return v, u
        return v, "szt"
    return None


def unit_price(text: str):
    """«(=1 l 11,88)», «(=100 g 0,76/0,81)», «(z kartą =1 l 5,58)» -> (цена за 1 кг/л, 'kg'|'l')."""
    m = UNITP_RE.search(text)
    if not m:
        return None
    base, u, p = float(m.group(1)), m.group(2).lower(), float(m.group(3).replace(",", "."))
    if u in ("g", "ml"):
        return round(p * 1000 / base, 2), "kg" if u == "g" else "l"
    return round(p / base, 2), u


def price_info(b) -> dict:
    """Блок рядом с товаром: отдельные цены (с «размером шрифта»), старая цена, цена без карты, скидка."""
    text, low = b[4], b[4].lower()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    info = {"prices": [], "text": low}
    for k, ln in enumerate(lines):
        prev = lines[k - 1].lower() if k else ""
        if PRICE_RE.match(ln):
            v = float(ln.rstrip("*").replace(",", "."))
            label = prev + " " + low[:0]
            if any(w in prev for w in ("przed", "obniżką", "regularna", "30 dni", "bez karty", "karty")):
                if "bez karty" in prev or "karty" in prev:
                    info["nocard"] = v
                else:
                    info["old"] = v
            else:
                info["prices"].append((v, (b[3] - b[1]) / len(lines)))
        elif m := re.search(r"(?:bez karty|przed obniżką|regularna)\D{0,30}?(\d{1,4},\d{2})", ln.lower()):
            v = float(m.group(1).replace(",", "."))
            info["nocard" if "karty" in ln.lower() else "old"] = v
    if any(w in low for w in ("przed obniżką", "najniższa cena", "cena regularna")) and "old" not in info:
        nums = re.findall(r"(\d{1,4},\d{2})", text)
        if nums:
            info["old"] = float(nums[-1].replace(",", "."))
    if m := re.search(r"-\s?(\d{1,2})\s?%|(\d{1,2})\s?%\s*\n?\s*taniej", low):
        info["discount"] = int(m.group(1) or m.group(2))
    return info


def near(a, b) -> float:
    return L().gap(a, b)


_OWN = {}


def owners(page: dict) -> dict:
    """Каждый блок с ценой — ближайшему к нему описанию товара (иначе одна цена достаётся двум соседям).
    Кэш — по самой странице (тот же объект), а не по номеру: газетку перечитали из файла — считаем заново."""
    hit = _OWN.get(id(page))
    if hit and hit[0] is page:
        return hit[1]
    prods = [b for b in page["blocks"] if is_product_block(b)]
    out = {id(p): [] for p in prods}
    for b in page["blocks"]:
        if b in prods or not prods or len(b[4]) > 200:
            continue
        if not (re.search(r"\d,\d{2}|%|z kartą|gratis|taniej|tańszy|limit|drugi", b[4].lower())):
            continue
        d, owner = min(((near(b, p), p) for p in prods), key=lambda x: x[0])
        if d < 140:
            out[id(owner)].append((d, b))
    if len(_OWN) > 400:
        _OWN.clear()
    _OWN[id(page)] = (page, out)
    return out


def build_deal(flyer: dict, slug: str, pno: int, page: dict, hit: list) -> dict:
    M = L()
    own = owners(page).get(id(hit), [])
    infos = [(d, b, price_info(b)) for d, b in sorted(own, key=lambda x: x[0])]
    low_all = " ".join(i["text"] for _, _, i in infos)
    up, size = unit_price(hit[4]), size_amount(hit[4])
    cands = [(v, h, d) for d, _, i in infos for v, h in i["prices"]]
    old = next((i["old"] for _, _, i in infos if i.get("old")), None)
    nocard = next((i["nocard"] for _, _, i in infos if i.get("nocard")), None)
    cands = [c for c in cands if c[0] not in (old, nocard)] or cands
    price, approx = None, False
    if up and size and size[1] == up[1] and size[0]:
        expected = up[0] * size[0]
        ok = sorted((c for c in cands if abs(c[0] - expected) / expected <= 0.12), key=lambda c: abs(c[0] - expected))
        if not ok:  # ценник стоит ближе к описанию соседа — ищем по странице цену, которая сходится с ценой за кг/л
            ok = sorted(((v, h, near(b, hit)) for b in page["blocks"] if near(b, hit) < 170 and b is not hit
                         for v, h in price_info(b)["prices"] if abs(v - expected) / expected <= 0.12),
                        key=lambda c: (abs(c[0] - expected), c[2]))
        price, approx = (ok[0][0], False) if ok else (round(expected, 2), True)
    elif cands:
        price = max(cands, key=lambda c: (c[1], -c[2]))[0]  # самый крупный шрифт, при равенстве — ближе
    if old is None and nocard and price and nocard > price:
        old = nocard
    if old and price and old <= price:
        old = None
    flat = re.sub(r"\s+", " ", low_all)
    second = "drugi produkt" in flat or "drugi tańszy" in flat or "przy zakupie 2" in flat or "trzeci" in flat
    discount = round((1 - price / old) * 100) if old and price else None
    if discount is None and not second:
        discount = next((i["discount"] for _, _, i in infos if i.get("discount")), None)
    conditions = []
    if "z kartą" in flat or "z karta" in flat:
        conditions.append(f"цена с картой Kaufland Card{f' (без карты {M.money(nocard)})' if nocard else ''}")
    if m := re.search(r"(\d)\s*\+\s*(\d)", flat) if ("gratis" in flat or "tańszy" in flat) else None:
        free = "gratis" in flat
        conditions.append(f"купи {m.group(1)} + {m.group(2)} {'бесплатно' if free else 'со скидкой'}")
    if second:
        if m := re.search(r"(\d{1,3})\s?%", flat):
            conditions.append(f"на второй (третий) товар −{m.group(1)}%, цена — средняя за штуку")
        else:
            conditions.append("скидка на второй товар")
    if m := re.search(r"limit[^.()]{0,40}", re.sub(r"\s+", " ", hit[4].lower() + " " + flat)):
        conditions.append(m.group(0).strip())
    dates = None
    date_blocks = [b for b in page["blocks"] if M.is_date_block(b[4])]
    if date_blocks:
        above = [b for b in date_blocks if b[1] <= hit[1]] or date_blocks
        dates = M.parse_dates(min(above, key=lambda b: near(b, hit))[4], flyer)
    if not dates:
        dates = dt.date.fromisoformat(flyer["start"]), dt.date.fromisoformat(flyer["end"])
    size_line = next((ln.strip() for ln in hit[4].splitlines()
                      if SIZE_RE.search(ln.lower()) and not ln.strip().startswith("(")), None)
    unit = up[1] if up else (size[1] if size and size[1] != "szt" else None)
    uprice = up[0] if up and not approx else (round(price / size[0], 2) if price and size and size[1] != "szt" and size[0] else None)
    uregular = round(uprice * old / price, 2) if uprice and old and price else (uprice if not discount else None)
    return {"title": title_of(hit[4]), "price": price, "approx": approx, "old_price": old, "size": size_line,
            "unit": unit, "unit_price": uprice, "unit_regular": uregular, "discount": discount,
            "supercena": "supercena" in flat, "conditions": list(dict.fromkeys(conditions))[:4],
            "start": dates[0].isoformat(), "end": dates[1].isoformat(), "flyer": flyer["name"], "slug": slug,
            "page": pno + 1, "url": PAGE_URL.format(slug=slug, page=pno + 1), "snippet": hit[4], "store": STORE}


def deals_of(flyer: dict, slug: str):
    for pno, page in enumerate(flyer["pages"]):
        for b in page["blocks"]:
            if is_product_block(b):
                yield build_deal(flyer, slug, pno, page, b)


# ---------------------------------------------------------------- поиск и история (как у Lidl)

def search(index: dict, query: str, include_past=False) -> list[dict]:
    M = L()
    tokens = [t for t in M.fold(query).split() if len(t) > 2 or t.isdigit()]
    if not tokens or not index:
        return []
    seen = {}
    for slug, flyer in index["flyers"].items():
        for pno, page in enumerate(flyer["pages"]):
            for b in page["blocks"]:
                if not is_product_block(b):
                    continue
                words = re.findall(r"\w+", M.fold(b[4]))
                if not all(any(M.word_matches(w, t) for w in words) for t in tokens):
                    continue
                d = build_deal(flyer, slug, pno, page, b)
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
    hist = load_json(HISTORY, {})
    for d in deals_of(flyer, slug):
        if d["price"] is None and not d["discount"]:
            continue
        rows = hist.setdefault(M.fold(d["title"]), [])
        rows[:] = [r for r in rows if r["slug"] != slug] + [M.history_row(d)]
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
