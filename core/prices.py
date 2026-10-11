"""Цены по твоим чекам Lidl и Kaufland: история цены товара в магазине и личная инфляция.

Два взгляда на цену (переключатель на странице «Скидки»):
  регулярная — цена без акций. Берём самую высокую цену на полке за первые и последние ~3 месяца покупок:
               акции цену только снижают, а в Kaufland акционная цена бывает прямо в строке чека, без скидки.
  выгодная   — самая низкая цена, которую ты реально заплатил за те же периоды (со скидками и акциями).
У весовых товаров (количество дробное) цена — за кг. У фасованных — за штуку, а если фасовка есть
в названии («500g», «0,5l»), за кг/л: так видно и уменьшение упаковки.
Овощи и фрукты — сезонные: в списках помечены, в среднее не входят. Товары, которые ты давно не покупал
(последняя покупка больше STALE дней назад), в инфляцию не идут — их «стало» давно устарело.
Разные варианты под одним названием (вкусы, у которых свой код товара) не смешиваются: сравнивается основной.
"""
import datetime as dt
import re
from collections import defaultdict

from core.categories import normalize
from core.common import fold, word_matches
from core.db import connect

SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s?(?:x\s?(\d+(?:[.,]\d+)?)\s?)?(kg|g|ml|l)(?:pl)?\b")  # «300gpl» — граммы, в пластах
STORES = {"lidl": "Lidl", "kaufland": "Kaufland"}
MODES = ("regular", "best")
WINDOW = 90  # дней: «было» — первые покупки за этот срок, «стало» — последние
STALE = 180  # дней: последняя покупка давнее — товар не в инфляции (цена «сейчас» неизвестна)
SURE_DAYS = 7  # нижняя из цен «было/стало» подтверждена, если держалась хотя бы столько дней


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
    """Покупки товаров в магазине: нормализованное название -> записи по дням (цена на полке и оплаченная)."""
    out = defaultdict(dict)
    for r in con.execute("""SELECT i.name, i.qty, i.unit_price, i.amount, i.discount, i.product_code, p.date, c.key cat
                            FROM items i
                            JOIN purchases p ON p.id = i.purchase_id LEFT JOIN categories c ON c.id = i.category_id
                            WHERE p.merchant = ? AND p.source IN ('lidl', 'kaufland', 'biedronka', 'photo') AND i.amount > 0
                            ORDER BY p.date""", (merchant,)):
        qty = r["qty"] or 1
        price = r["unit_price"] or (r["amount"] / qty)
        if not price or price <= 0:
            continue
        weighed = abs(qty - round(qty)) > 1e-6
        key = normalize(r["name"])
        day = r["date"][:10]
        pu = (round(price, 2), "kg") if weighed else per_unit(r["name"], price)
        paid = round((r["amount"] - (r["discount"] or 0)) / qty, 2)
        code = r["product_code"] or ""
        # код варианта: у весовых и у штрихкодов с весом внутри («2…», 13 цифр) он свой у каждой покупки — не вариант
        code = None if weighed or (len(code) == 13 and code.startswith("2")) else code or None
        out[key][(day, code)] = {"date": day, "name": r["name"], "code": code, "price": round(price, 2), "unit": "kg" if weighed else "шт",
                         "per_unit": pu[0] if pu else None, "pu_unit": pu[1] if pu else None, "paid": paid,
                         "paid_pu": round(paid * pu[0] / price, 2) if pu else None,
                         "season": (r["cat"] or "").startswith("food.produce")}
    return {k: sorted(v.values(), key=lambda x: x["date"]) for k, v in out.items()}


def _day(r: dict) -> dt.date:
    return dt.date.fromisoformat(r["date"])


def _regular(rs: list[dict], key: str, latest: bool) -> dict:
    """Покупка с регулярной ценой в окне. Цена на полке на 10%+ ниже самой высокой в окне — акционная прямо в чеке
    (Kaufland), её пропускаем; из остальных — первая покупка окна («было») или последняя («стало»): разовый скачок
    цены не становится «регулярной». Одиночный выброс вверх (ошибка распознавания фото — в полтора раза дороже
    следующей) не считается."""
    top = sorted(rs, key=lambda r: -r[key])
    if len(top) >= 2 and top[0][key] > top[1][key] * 1.5:
        top = top[1:]
    ok = [r for r in top if r[key] >= top[0][key] * 0.9]
    return max(ok, key=lambda r: r["date"]) if latest else min(ok, key=lambda r: r["date"])


def _like_promo(rs: list[dict], key: str, price: float) -> bool:
    """Цена похожа на акцию: за всю историю держалась меньше SURE_DAYS дней (купил раз или запасся за пару дней)
    или была коротким провалом — и до, и после (в пределах WINDOW дней) товар стоил на 10%+ дороже."""
    at = [r for r in rs if abs(r[key] - price) <= price * 0.01]
    if not at:
        return False
    if (_day(at[-1]) - _day(at[0])).days < SURE_DAYS:
        return True
    near = lambda r, sign: [x for x in rs if 0 < sign * (_day(x) - _day(r)).days <= WINDOW]
    return any(any(x[key] >= price * 1.1 for x in near(r, -1)) and any(x[key] >= price * 1.1 for x in near(r, 1))
               for r in at)


def change(rs: list[dict], mode: str = "regular", min_n=4, min_days=60) -> dict | None:
    """Цена в начале и сейчас (по окнам первых и последних покупок) и изменение в %."""
    if len(rs) < min_n:
        return None
    d0, d1 = _day(rs[0]), _day(rs[-1])
    span = (d1 - d0).days
    if span < min_days:
        return None
    w = min(WINDOW, span // 3)  # окна «было» и «стало» не пересекаются
    first = [r for r in rs if (_day(r) - d0).days <= w]
    last = [r for r in rs if (d1 - _day(r)).days <= w]
    # варианты под одним названием (вкусы со своим кодом): сравниваем основной — тот, что куплен и тогда, и сейчас
    codes = {r["code"] for r in first + last if r["code"]}
    both = [c for c in codes if any(r["code"] == c for r in first) and any(r["code"] == c for r in last)]
    if len(codes) > 1 and both:
        main = max(both, key=lambda c: sum(r["code"] == c for r in rs))
        first, last = [r for r in first if r["code"] == main], [r for r in last if r["code"] == main]
        rs = [r for r in rs if r["code"] == main]
    pu = all(r["per_unit"] for r in first + last)
    key = ("per_unit" if pu else "price") if mode == "regular" else ("paid_pu" if pu else "paid")
    if mode == "regular":
        a, b = _regular(first, key, latest=False), _regular(last, key, latest=True)
    else:
        a, b = min(first, key=lambda r: r[key]), min(last, key=lambda r: r[key])
    p0, p1 = a[key], b[key]
    # заметное изменение, у которого нижняя цена похожа на акцию, — под вопросом: в среднее не идёт
    sure = mode != "regular" or abs(p1 / p0 - 1) < 0.1 or not _like_promo(rs, key, min(p0, p1))
    return {"from": a["date"], "to": b["date"], "p0": round(p0, 2), "p1": round(p1, 2), "sure": sure,
            "pct": round((p1 / p0 - 1) * 100, 1) if p0 else 0.0, "n": len(rs),
            "unit": f"zł/{rs[-1]['pu_unit']}" if pu else f"zł/{rs[-1]['unit']}"}


def matches(key: str, query: str, loose: bool = False) -> bool:
    """Запрос — как угодно: «ser gouda», ключ «k. gouda plastry 500g» или название из чека «K.GoudaPlastry500g».
    loose — одно слово запроса может не найтись: в чеке часто нет общего слова («Gouda w plastr. XXL» без «ser»),
    но хотя бы одно найденное — не короче 4 букв."""
    q = normalize(query)
    if q == key:
        return True
    tokens = [t for t in re.findall(r"\w+", q) if len(t) > 2 or t.isdigit()]
    words = re.findall(r"\w+", key)
    hit = [t for t in tokens if any(word_matches(w, t) for w in words)]
    if loose and len(tokens) >= 2:
        return len(hit) >= len(tokens) - 1 and any(len(t) >= 4 for t in hit)
    return bool(tokens) and len(hit) == len(tokens)


def find(all_rows: dict, query: str) -> list[tuple[str, list[dict]]]:
    """Товары под запрос: точное название из чека, затем все слова запроса, затем «без одного слова»;
    внутри — чаще покупаемые первыми."""
    exact = normalize(query)
    strict = sorted(((k, rs) for k, rs in all_rows.items() if matches(k, query)),
                    key=lambda x: (x[0] != exact, -len(x[1])))
    seen = {k for k, _ in strict}
    loose = sorted(((k, rs) for k, rs in all_rows.items() if k not in seen and matches(k, query, loose=True)),
                   key=lambda x: -len(x[1]))
    return strict + loose


def history(query: str, store: str, limit: int = 6) -> list[dict]:
    """Товары магазина под запрос: все покупки (цена на полке, оплачено), регулярная и выгодная цена."""
    con = connect()
    found = find(rows(con, STORES[store]), query)
    return [{"key": k, "title": rs[-1]["name"], "rows": rs, "change": change(rs, "regular", min_n=2, min_days=14),
             "best": change(rs, "best", min_n=2, min_days=14)} for k, rs in found[:limit]]


def inflation(store: str, watch: list[str]) -> dict:
    """Личная инфляция по чекам магазина: товары, купленные 4+ раза за 2+ месяца; среднее — с весом трат,
    без сезонных овощей и фруктов. Для обоих взглядов на цену (MODES)."""
    con = connect()
    all_rows = rows(con, STORES[store])
    out = {"modes": {}, "n": 0, "since": None, "stale": 0, "stale_days": STALE}
    cutoff = (dt.date.today() - dt.timedelta(days=STALE)).isoformat()
    for mode in MODES:
        items, wsum, acc, stale = [], 0.0, 0.0, 0
        for k, rs in all_rows.items():
            ch = change(rs, mode)
            if not ch:
                continue
            if rs[-1]["date"] < cutoff:  # давно не покупал: «стало» — цена годичной давности
                stale += 1
                continue
            weight = sum(r["paid"] for r in rs)
            season = any(r["season"] for r in rs[-3:])
            items.append(ch | {"key": k, "title": rs[-1]["name"], "weight": round(weight, 2), "season": season})
            if not season and ch["sure"]:
                wsum += weight
                acc += weight * ch["pct"]
        items.sort(key=lambda x: -x["pct"])
        out["modes"][mode] = {"avg": round(acc / wsum, 1) if wsum else None, "n": len(items),
                              "n_avg": sum(not i["season"] and i["sure"] for i in items),
                              "up": [i for i in items if i["pct"] > 0][:8],
                              "down": [i for i in reversed(items) if i["pct"] < 0][:8]}
        out["n"] = max(out["n"], len(items))
        out["stale"] = stale
        out["since"] = min((i["from"] for i in items), default=out["since"])
    out["positions"] = []
    for q in watch:
        # товар позиции — первый под запрос, у которого есть с чем сравнить (по одной покупке цену не сравнить)
        found = find(all_rows, q)
        best = next(((k, rs) for k, rs in found if change(rs, "regular", min_n=2, min_days=14)), found[0] if found else None)
        out["positions"].append({"q": q, "title": best[1][-1]["name"] if best else None,
                                 **{m: change(best[1], m, min_n=2, min_days=14) if best else None for m in MODES}})
    return out
