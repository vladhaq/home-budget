"""Цены по твоим чекам Lidl и Kaufland: история цены товара в магазине и личная инфляция.

Берём цену на полке (unit_price — до скидки в чеке): так видно, сколько товар стоит, а не повезло ли с акцией.
У весовых товаров (количество дробное) это уже цена за кг. У фасованных — за штуку, а если фасовка есть
в названии («500g», «0,5l»), ещё и за кг/л: так видно и уменьшение упаковки.
"""
import datetime as dt
import re
import statistics as st
from collections import defaultdict

from core.categories import normalize
from core.common import fold, word_matches
from core.db import connect

SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s?(?:x\s?(\d+(?:[.,]\d+)?)\s?)?(kg|g|ml|l)(?:pl)?\b")  # «300gpl» — граммы, в пластах
STORES = {"lidl": "Lidl", "kaufland": "Kaufland"}


def per_unit(name: str, price: float) -> tuple[float, str] | None:
    """Цена за кг/л по фасовке в названии: «Skyr 150g» 4,49 -> (29,93, 'kg')."""
    m = SIZE.search(normalize(name))
    if not m:
        return None
    a = float(m.group(1).replace(",", ".")) * (float(m.group(2).replace(",", ".")) if m.group(2) else 1)
    u = m.group(3)
    qty = a / 1000 if u in ("g", "ml") else a
    return (round(price / qty, 2), "kg" if u in ("g", "kg") else "l") if qty else None


def rows(con, merchant: str) -> dict[str, list[dict]]:
    """Покупки товаров в магазине: нормализованное название -> записи по дням (цена на полке)."""
    out = defaultdict(dict)
    for r in con.execute("""SELECT i.name, i.qty, i.unit_price, i.amount, i.discount, p.date FROM items i
                            JOIN purchases p ON p.id = i.purchase_id
                            WHERE p.merchant = ? AND p.source IN ('lidl', 'kaufland', 'photo') AND i.amount > 0
                            ORDER BY p.date""", (merchant,)):
        qty = r["qty"] or 1
        price = r["unit_price"] or (r["amount"] / qty)
        if not price or price <= 0:
            continue
        weighed = abs(qty - round(qty)) > 1e-6
        key = normalize(r["name"])
        day = r["date"][:10]
        pu = (round(price, 2), "kg") if weighed else per_unit(r["name"], price)
        out[key][day] = {"date": day, "name": r["name"], "price": round(price, 2), "unit": "kg" if weighed else "шт",
                         "per_unit": pu[0] if pu else None, "pu_unit": pu[1] if pu else None,
                         "paid": round((r["amount"] - (r["discount"] or 0)) / qty, 2)}
    return {k: sorted(v.values(), key=lambda x: x["date"]) for k, v in out.items()}


def change(rs: list[dict], min_n=4, min_days=60) -> dict | None:
    """Типичная цена в начале и в конце (медиана первых и последних трёх покупок) и изменение в %."""
    if len(rs) < min_n:
        return None
    first, last = rs[:3], rs[-3:]
    if (dt.date.fromisoformat(last[-1]["date"]) - dt.date.fromisoformat(first[0]["date"])).days < min_days:
        return None
    key = "per_unit" if all(r["per_unit"] for r in first + last) else "price"
    p0, p1 = st.median(r[key] for r in first), st.median(r[key] for r in last)
    return {"from": first[0]["date"], "to": last[-1]["date"], "p0": round(p0, 2), "p1": round(p1, 2),
            "pct": round((p1 / p0 - 1) * 100, 1) if p0 else 0.0, "n": len(rs),
            "unit": f"zł/{rs[-1]['pu_unit']}" if key == "per_unit" else f"zł/{rs[-1]['unit']}"}


def matches(key: str, query: str) -> bool:
    tokens = [t for t in fold(query).split() if len(t) > 2 or t.isdigit()]
    words = re.findall(r"\w+", key)
    return bool(tokens) and all(any(word_matches(w, t) for w in words) for t in tokens)


def history(query: str, store: str, limit: int = 6) -> list[dict]:
    """Товары магазина под запрос: все покупки (цена на полке, оплачено), изменение цены."""
    con = connect()
    found = [(k, rs) for k, rs in rows(con, STORES[store]).items() if matches(k, query)]
    found.sort(key=lambda x: -len(x[1]))
    return [{"key": k, "title": rs[-1]["name"], "rows": rs, "change": change(rs, min_n=2, min_days=14)}
            for k, rs in found[:limit]]


def inflation(store: str, watch: list[str]) -> dict:
    """Личная инфляция по чекам магазина: товары, купленные 4+ раза за 2+ месяца; среднее — с весом трат."""
    con = connect()
    all_rows = rows(con, STORES[store])
    items, wsum, acc = [], 0.0, 0.0
    for k, rs in all_rows.items():
        ch = change(rs)
        if not ch:
            continue
        weight = sum(r["paid"] for r in rs)
        items.append(ch | {"key": k, "title": rs[-1]["name"], "weight": round(weight, 2)})
        wsum += weight
        acc += weight * ch["pct"]
    items.sort(key=lambda x: -x["pct"])
    positions = []
    for q in watch:
        best = max(((k, rs) for k, rs in all_rows.items() if matches(k, q)), key=lambda x: len(x[1]), default=None)
        positions.append({"q": q, "receipts": (change(best[1], min_n=2, min_days=14) or None) | {"title": best[1][-1]["name"]}
                          if best and change(best[1], min_n=2, min_days=14) else None})
    return {"avg": round(acc / wsum, 1) if wsum else None, "n": len(items),
            "since": min((i["from"] for i in items), default=None), "up": items[:8],
            "down": [i for i in reversed(items) if i["pct"] < 0][:8], "positions": positions}
