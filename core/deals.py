"""Скидки Lidl из газеток (модуль lidl-deals) — для интерфейса и «обновить всё».

Сам модуль лежит в lidl-deals/lidl.py и работает и отдельно (python lidl-deals/lidl.py). Здесь он подключается
как есть; вход в Telegram для напоминаний — общий с фото чеков (python budget.py telegram), если у lidl-deals
нет своего.
"""
import configparser
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


def index():
    L = mod()
    return L.load_json(L.INDEX, None)


def deal_json(d: dict, sent: set) -> dict:
    L = mod()
    today = L.today().isoformat()
    key = L.deal_key(d)
    out = {k: d.get(k) for k in ("title", "price", "approx", "old_price", "discount", "size", "unit", "unit_price",
                                 "unit_regular", "start", "end", "flyer", "url", "conditions", "supercena", "page")}
    out.update(key=key, status=L.status(d), reminded=key in sent, upcoming=d["start"] > today,
               no_discount=d["price"] is not None and d["old_price"] is None and not d["discount"])
    if out["no_discount"] and (prev := L.previous_price(d)):
        out["prev"] = {"price": prev["price"], "start": prev["start"]}
    return out


def search(query: str, idx=None) -> list[dict]:
    L = mod()
    idx = idx if idx is not None else index()
    if not idx:
        return []
    sent = set(L.load_json(L.SENT, []))
    return [deal_json(d, sent) for d in L.search(idx, query)]


def summary() -> dict:
    """Для списка разделов: сколько акций на постоянные позиции. Кэш — пока не поменялись газетки и список."""
    L = mod()
    stamp = tuple(p.stat().st_mtime if p.exists() else 0 for p in (L.INDEX, L.WATCH)) + (L.today().isoformat(),)
    if _summary.get("stamp") != stamp:
        idx = index()
        n = sum(len(L.search(idx, q)) for q in L.watchlist()) if idx else 0
        _summary.update(stamp=stamp, data={"updated": idx.get("updated") if idx else None,
                                           "flyers": len(idx["flyers"]) if idx else 0, "deals": n})
    return _summary["data"]


def history(query: str, limit: int = 6) -> list[dict]:
    """История цен по товарам, подходящим под запрос: записи, ряд обычных цен, изменение, лучшая акция, сезонность."""
    L = mod()
    out = []
    for key, rows in list(L.find_history(query).items())[:limit]:
        series, unit = L.regular_series(rows)
        ch = L.change(series)
        promos = [r for r in rows if r.get("discount") and r.get("price") is not None]
        best = min(promos, key=lambda r: r["price"]) if promos else None
        out.append({"key": key, "title": rows[-1]["title"], "rows": rows, "unit": unit,
                    "change": {"from": ch[0], "p0": ch[1], "to": ch[2], "p1": ch[3], "pct": round(ch[4], 1)} if ch else None,
                    "best": {"price": best["price"], "discount": best["discount"], "start": best["start"]} if best else None,
                    "season": {str(m): s for m, s in L.seasonality(rows).items()}})
    return out


def inflation() -> dict:
    """Инфляция по постоянным позициям: обычная цена (за кг/л, если есть) — первая и последняя запись."""
    L = mod()
    rows, pcts = [], []
    for q in L.watchlist():
        for hist in L.find_history(q).values():
            series, unit = L.regular_series(hist)
            ch = L.change(series)
            if ch:
                pcts.append(ch[4])
                rows.append({"q": q, "title": hist[-1]["title"], "unit": unit, "from": ch[0], "p0": ch[1],
                             "to": ch[2], "p1": ch[3], "pct": round(ch[4], 1)})
    return {"rows": rows, "avg": round(sum(pcts) / len(pcts), 1) if pcts else None}


def remind(query: str, keys: list[str]) -> list[dict]:
    """Поставить напоминания в Избранное Telegram для выбранных акций (без вопросов в терминале)."""
    L = mod()
    idx = index()
    deals = [d for d in L.search(idx, query) if L.deal_key(d) in set(keys)] if idx else []
    return L.schedule_reminders(deals, interactive=False)


def auto() -> dict:
    """Шаг «обновить всё»: свежие газетки + напоминания на постоянные позиции (если вход в Telegram есть)."""
    L = mod()
    idx = L.update(verbose=False)
    today = L.today().isoformat()
    found = [d for q in L.watchlist() for d in L.search(idx, q)]
    upcoming = [d for d in found if d["start"] >= today]
    tg = telegram_state()
    made = L.schedule_reminders(upcoming, interactive=False) if upcoming and tg["ready"] else []
    return {"flyers": len(idx["flyers"]), "deals": len(found), "upcoming": len(upcoming),
            "reminded": sum(1 for r in made if not r.get("skipped")), "telegram": tg}
