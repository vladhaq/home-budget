"""Скидки из газеток Lidl (модуль lidl-deals) и Kaufland (core/kaufland_deals.py) — для интерфейса и «обновить всё».

Модуль Lidl лежит в lidl-deals/lidl.py и работает и отдельно (python lidl-deals/lidl.py). Здесь он подключается
как есть; вход в Telegram для напоминаний — общий с фото чеков (python budget.py telegram), если у lidl-deals
нет своего. Постоянные позиции и напоминания — общие для обоих магазинов; store = "lidl" | "kaufland".
Цены по твоим чекам (история, личная инфляция) — core/prices.py.
"""
import configparser
import re
import importlib.util

from core.common import BUDGET, DATA

_mod = None
_summary = {}


def mod():
    """lidl-deals/lidl.py как модуль (в имени папки дефис — обычный import не подходит)."""
    global _mod
    if _mod is None:
        spec = importlib.util.spec_from_file_location("lidl_deals", BUDGET / "lidl-deals" / "lidl.py")
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
        own = configparser.ConfigParser()
        own.read(_mod.CONFIG, encoding="utf-8")
        if not own.get("telegram", "api_id", fallback="").strip():
            _mod.CONFIG = BUDGET / "config.ini"  # ключи Telegram — из общего config.ini бюджета
        if not (_mod.DATA / "telegram.session").exists():
            _mod.SESSION = DATA / "telegram"  # вход — общий с фото чеков
    return _mod


def telegram_state() -> dict:
    L = mod()
    cfg = configparser.ConfigParser()
    cfg.read(L.CONFIG, encoding="utf-8")
    keys = bool(cfg.get("telegram", "api_id", fallback="").strip())
    session = L.SESSION.with_suffix(".session").exists()
    clock = L.remind_clock(cfg["telegram"] if cfg.has_section("telegram") else {})
    return {"keys": keys, "session": session, "ready": keys and session, "remind_at": clock.strftime("%H:%M")}


def set_remind_at(hhmm: str):
    """Время напоминаний накануне акции. Правим одну строку в config.ini — комментарии и ключи не трогаем."""
    import re
    L = mod()
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", hhmm):
        raise ValueError("время в формате ЧЧ:ММ")
    text = L.CONFIG.read_text(encoding="utf-8") if L.CONFIG.exists() else ""
    line = f"remind_at = {hhmm}"
    if re.search(r"(?m)^remind_at\s*=.*$", text):
        text = re.sub(r"(?m)^remind_at\s*=.*$", line, text)
    elif re.search(r"(?m)^\[telegram\]\s*$", text):
        text = re.sub(r"(?m)^\[telegram\]\s*$", lambda m: "[telegram]\n" + line, text, count=1)
    else:
        text = text.rstrip("\n") + f"\n\n[telegram]\n{line}\n"
    L.CONFIG.write_text(text, encoding="utf-8")


def src(store: str = "lidl"):
    """Модуль газеток магазина: у обоих — INDEX, update(), search(), previous_price(), find_history()."""
    if store == "kaufland":
        from core import kaufland_deals
        return kaufland_deals
    return mod()


def index(store: str = "lidl"):
    return mod().load_json(src(store).INDEX, None)


def deal_json(d: dict, sent: set, store: str = "lidl") -> dict:
    L = mod()
    today = L.today().isoformat()
    key = L.deal_key(d)
    out = {k: d.get(k) for k in ("title", "price", "approx", "old_price", "discount", "size", "unit", "unit_price",
                                 "unit_regular", "start", "end", "flyer", "url", "conditions", "supercena", "page")}
    out.update(key=key, status=L.status(d), reminded=key in sent, upcoming=d["start"] > today, store=store,
               no_discount=d["price"] is not None and d["old_price"] is None and not d["discount"])
    if out["no_discount"] and (prev := src(store).previous_price(d)):
        out["prev"] = {"price": prev["price"], "start": prev["start"]}
    return out


def search(query: str, idx=None, store: str = "lidl") -> list[dict]:
    L = mod()
    idx = idx if idx is not None else index(store)
    if not idx:
        return []
    sent = set(L.load_json(L.SENT, []))
    return [deal_json(d, sent, store) for d in src(store).search(idx, query)]


def summary() -> dict:
    """Для списка разделов: сколько акций на постоянные позиции в обоих магазинах. Кэш — пока не поменялись газетки и список."""
    L = mod()
    from core import kaufland_deals as K
    stamp = tuple(p.stat().st_mtime if p.exists() else 0 for p in (L.INDEX, K.INDEX, L.WATCH, K.WATCH)) + (L.today().isoformat(),)
    if _summary.get("stamp") != stamp:
        data = {"updated": None, "flyers": 0, "deals": 0, "stores": {}}
        for store in ("lidl", "kaufland"):
            idx = index(store)
            n = sum(len(src(store).search(idx, q)) for q in src(store).watchlist()) if idx else 0
            data["stores"][store] = {"flyers": len(idx["flyers"]) if idx else 0, "deals": n}
            data["flyers"] += data["stores"][store]["flyers"]
            data["deals"] += n
            if idx and idx.get("updated") and (data["updated"] or "") < idx["updated"]:
                data["updated"] = idx["updated"]
        _summary.update(stamp=stamp, data=data)
    return _summary["data"]


def history(query: str, limit: int = 6, store: str = "lidl") -> list[dict]:
    """История цен по газеткам: записи, ряд обычных цен, изменение, лучшая акция, сезонность."""
    L = mod()
    out = []
    for key, rows in list(src(store).find_history(query).items())[:limit]:
        series, unit = L.regular_series(rows)
        ch = L.change(series)
        promos = [r for r in rows if r.get("discount") and r.get("price") is not None]
        best = min(promos, key=lambda r: r["price"]) if promos else None
        out.append({"key": key, "title": rows[-1]["title"], "rows": rows, "unit": unit,
                    "change": {"from": ch[0], "p0": ch[1], "to": ch[2], "p1": ch[3], "pct": round(ch[4], 1)} if ch else None,
                    "best": {"price": best["price"], "discount": best["discount"], "start": best["start"]} if best else None,
                    "season": {str(m): s for m, s in L.seasonality(rows).items()}})
    return out


def inflation(store: str = "lidl") -> dict:
    """Инфляция: по твоим чекам этого магазина (личная) и по газеткам — для постоянных позиций."""
    from core import prices
    L = mod()
    rows, pcts = [], []
    for q in src(store).watchlist():
        for hist in src(store).find_history(q).values():
            series, unit = L.regular_series(hist)
            ch = L.change(series)
            if ch:
                pcts.append(ch[4])
                rows.append({"q": q, "title": hist[-1]["title"], "unit": unit, "from": ch[0], "p0": ch[1],
                             "to": ch[2], "p1": ch[3], "pct": round(ch[4], 1)})
    return {"rows": rows, "avg": round(sum(pcts) / len(pcts), 1) if pcts else None,
            "receipts": prices.inflation(store, src(store).watchlist())}


def remind(query: str, keys: list[str], store: str = "lidl") -> list[dict]:
    """Поставить напоминания в Избранное Telegram для выбранных акций (без вопросов в терминале)."""
    L = mod()
    idx = index(store)
    deals = [d for d in src(store).search(idx, query) if L.deal_key(d) in set(keys)] if idx else []
    return L.schedule_reminders(deals, interactive=False)


def auto() -> dict:
    """Шаг «обновить всё»: свежие газетки обоих магазинов + напоминания на постоянные позиции (если есть вход в Telegram).
    Газетки одного магазина не скачались — второй всё равно обновляется."""
    L = mod()
    today = L.today().isoformat()
    found, flyers, errors = [], 0, []
    for store in ("lidl", "kaufland"):
        try:
            idx = src(store).update(verbose=False)
        except Exception as e:  # noqa: BLE001 — сайт магазина недоступен
            errors.append(f"{store}: {e}")
            idx = index(store)
        if idx:
            flyers += len(idx["flyers"])
            found += [d for q in src(store).watchlist() for d in src(store).search(idx, q)]
    upcoming = [d for d in found if d["start"] >= today]
    tg = telegram_state()
    made = L.schedule_reminders(upcoming, interactive=False) if upcoming and tg["ready"] else []
    return {"flyers": flyers, "deals": len(found), "upcoming": len(upcoming), "errors": errors,
            "reminded": sum(1 for r in made if not r.get("skipped")), "telegram": tg}


# ---------------------------------------------------------------- где выгоднее: одинаковое в обоих магазинах

# мясо сравниваем по виду и цене за кг (сырое: колбасы, ветчина, полуфабрикаты — мимо)
# порядок важен: товар относится к первому подходящему виду («Mięso mielone z łopatki» — фарш, а не лопатка)
MEAT = [("Фарш", r"mies\w* miel|\bmielon"), ("Филе куриной грудки", r"(filet|piers)\w*.*kurcz|kurcz\w*.*(filet|piers)"),
        ("Голень, бёдра курицы", r"(podudz|\budk|udz\w*).*kurcz|kurcz\w*.*(podudz|\budk|udz)"), ("Крылья куриные", r"skrzyd"),
        ("Индейка", r"indyk"), ("Свиная корейка (schab)", r"\bschab"), ("Свиная шея (karkówka)", r"karkow"),
        ("Свиная лопатка", r"lopatk"), ("Грудинка (boczek)", r"\bboczek"), ("Рёбрышки", r"zeberk"), ("Говядина", r"wolow")]
PROCESSED = (r"szynk|parow|kabanos|pasztet|salami|kielbas|nugget|strips|panier|pierog|pyzy|burger|kebab|gyros|wedzon|pieczen"
             r"|konserw|kotlet|gotowan|sous|grill\w* gotow|karma|dla ps|dla kot|sliwk|faszer|marynow|klops|pulpet|rolad")
NOT_BRAND = {"wszystkie", "polska", "polski", "polskie", "k-stoisko", "k-classic", "nowosc", "hit", "super", "mega", "extra", "xxl"}
STOP = {"rozne", "rodzaje", "opakowanie", "butelka", "puszka", "sloik", "sztuka", "kartonik", "torebka", "luzem", "smaki", "rodzaju"}
_ALL = {}


def all_deals(store: str) -> list[dict]:
    """Все текущие и будущие акции магазина (без запроса). Кэш — пока не обновились газетки."""
    L, S = mod(), src(store)
    stamp = S.INDEX.stat().st_mtime if S.INDEX.exists() else 0
    if _ALL.get(store, (None,))[0] == stamp:
        return _ALL[store][1]
    idx, today, seen = index(store), L.today().isoformat(), {}
    if idx:
        if store == "kaufland":
            found = (d for slug, f in idx["flyers"].items() for d in S.deals_of(f, slug))
        else:
            found = (L.build_deal(f, slug, pno, page, b) for slug, f in idx["flyers"].items()
                     for pno, page in enumerate(f["pages"]) for b in page["blocks"]
                     if L.is_product_block(b) and not L.is_legal(b[4]) and not L.is_date_block(b[4]) and L.looks_like_food_offer(b[4]))
        for d in found:
            if d["end"] < today or (d["price"] is None and not d["unit_price"]):
                continue
            key = (L.fold(d["title"]), d["start"], d["end"])
            if key not in seen or L.richness(d) > L.richness(seen[key]):
                seen[key] = d
    _ALL[store] = (stamp, list(seen.values()))
    return _ALL[store][1]


def _value(d: dict):
    """(цена для сравнения, основа): за кг/л, если есть, иначе за упаковку с её размером."""
    if d.get("unit_price") and d.get("unit") in ("kg", "l"):
        return d["unit_price"], f"за {d['unit']}"
    if d.get("price") is not None and d.get("size"):
        return d["price"], "за " + re.sub(r"\s+", " ", d["size"].lower())
    return None, None


def _pair(kind: str, label: str, a: dict, b: dict, sent: set):
    va, ba = _value(a)
    vb, bb = _value(b)
    if va is None or vb is None or ba != bb:
        return None
    cheaper = "lidl" if va < vb else "kaufland" if vb < va else None
    pct = round(abs(va - vb) / max(va, vb) * 100) if cheaper else 0
    return {"kind": kind, "label": label, "basis": ba, "cheaper": cheaper, "pct": pct,
            "lidl": deal_json(a, sent, "lidl") | {"value": va}, "kaufland": deal_json(b, sent, "kaufland") | {"value": vb}}


def compare() -> dict:
    """Одинаковое в обоих магазинах сейчас: мясо по видам, одна и та же марка, твои постоянные позиции."""
    L = mod()
    sent = set(L.load_json(L.SENT, []))
    lidl, kauf = all_deals("lidl"), all_deals("kaufland")
    pairs, used = [], set()

    def cheapest(deals):
        vals = [(v, d) for d in deals for v, _ in [_value(d)] if v is not None]
        return min(vals, key=lambda x: x[0])[1] if vals else None

    def meat_kind(d):  # вид мяса (первый подходящий), только сырое и с правдоподобной ценой за кг
        f = L.fold(d["title"])
        if d.get("unit") != "kg" or not d.get("unit_price") or not 6 <= d["unit_price"] <= 150 or re.search(PROCESSED, f):
            return None
        return next((label for label, rx in MEAT if re.search(rx, f)), None)

    for label, _ in MEAT:  # мясо: самое дешёвое за кг в каждом магазине
        a = cheapest([d for d in lidl if meat_kind(d) == label])
        b = cheapest([d for d in kauf if meat_kind(d) == label])
        if a and b and (p := _pair("meat", label, a, b, sent)):
            pairs.append(p)

    def brand(d):
        first = (d["title"].split() or [""])[0]
        f = L.fold(first).strip(".,")
        return f if len(f) >= 3 and first.upper() == first and any(c.isalpha() for c in first) and f not in NOT_BRAND else None

    words = lambda d: {w for w in re.findall(r"[a-z]{4,}", L.fold(d["title"])) if w not in STOP}  # noqa: E731
    by_brand = {}
    for d in lidl:
        if (br := brand(d)):
            by_brand.setdefault(br, []).append(d)
    def similar(a, b, br):  # тот же товар марки: совпадает хотя бы половина слов названия (без марки)
        wa, wb = words(a) - {br}, words(b) - {br}
        return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.5

    for b in kauf:  # одна марка, тот же товар («ZOTT Primo Skyr» и «ZOTT Primo Skyr naturalny»)
        br = brand(b)
        if not br or br not in by_brand:
            continue
        cands = [a for a in by_brand[br] if similar(a, b, br)]
        a = cheapest(cands)
        if a and (L.deal_key(a), L.deal_key(b)) not in used and (p := _pair("brand", br.upper(), a, b, sent)):
            used.add((L.deal_key(a), L.deal_key(b)))
            pairs.append(p)

    from core import kaufland_deals as K
    def titled(q, ds):  # акция, где запрос есть в самом названии (а не в соседнем тексте газетки)
        toks = [x for x in L.fold(q).split() if len(x) > 2]
        return [d for d in ds if all(any(L.word_matches(w, x) for w in re.findall(r"\w+", L.fold(d["title"]))) for x in toks)]

    for q in dict.fromkeys(L.watchlist() + K.watchlist()):  # постоянные позиции из обоих списков
        a = cheapest(titled(q, src("lidl").search(index("lidl"), q))) if index("lidl") else None
        b = cheapest(titled(q, src("kaufland").search(index("kaufland"), q))) if index("kaufland") else None
        if a and b and (p := _pair("position", q, a, b, sent)):
            pairs.append(p)
    order = {"position": 0, "meat": 1, "brand": 2}
    pairs.sort(key=lambda p: (order[p["kind"]], -p["pct"]))
    return {"pairs": pairs, "lidl": len(lidl), "kaufland": len(kauf)}
