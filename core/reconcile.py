"""Сверка покупок с выпиской банка.

1. Каждая покупка (чек, заказ из почты, фото) ищет свою операцию в банке: та же сумма, дата -3..+5 дней,
   совпадение продавца — приоритет. Для покупок в валюте — допуск 6% (курс и комиссия банка ≠ курс NBP).
2. Операции без чека становятся покупками с источником «банк» (категория — по получателю и описанию).
3. Переводы людям, банкомат, взносы — категории «Переводы» (не расход). Доходы — категории «Доходы».
4. Возвраты на карту — отрицательные покупки в категории исходной покупки.

  python budget.py reconcile
"""
import datetime as dt
import json
import re

from core import categories
from core.common import fold
from core.db import connect, save_purchase, set_meta

CITY_PREFIX = re.compile(r"^(wroclaw|warszawa|poznan|lodz|gdansk|gdynia|krakow|katowice|szczecin|lublin|bydgoszcz|bialystok|rzeszow|opole|dublin|"
                         r"anthropic\.com|luxembourg|london|amsterdam|berlin)", re.I)
MERCHANTS = [  # шаблон в описании операции -> магазин (как в покупках)
    (r"kaufland", "Kaufland"), (r"lidl", "Lidl"), (r"allegro", "Allegro"), (r"biedronka", "Biedronka"),
    (r"zabka", "Żabka"), (r"koleo", "KOLEO"), (r"erecept", "Erecept"), (r"orange flex", "Orange Flex"),
    (r"doz apteka|doz\.pl", "DOZ.pl"), (r"super-pharm", "Super-Pharm"), (r"rossmann", "Rossmann"),
    (r"\baction\b", "Action"), (r"carrefour", "Carrefour"), (r"auchan", "Auchan"), (r"anthropic|claude", "Anthropic"),
    (r"google play", "Google Play"), (r"google", "Google"), (r"vending", "Automat (vending)"),
    (r"city-?nav|jakdojade", "Jakdojade"), (r"media ?expert", "Media Expert"), (r"apteka", "Apteka"),
    (r"\bnetto\b", "Netto"), (r"\bdino\b", "Dino"), (r"pepco", "Pepco"), (r"mcdonald", "McDonald's"), (r"kfc", "KFC"),
]
# категория по операции без чека: шаблон по «тип + получатель + описание» (fold) -> категория
BANK_RULES = [
    (r"eksploatac|media za|prad|gaz ziemny|woda i sciek", "Жильё/Коммунальные"),
    (r"wynajmujacy|czynsz|za mieszkanie|najem lokal", "Жильё/Аренда"),
    (r"czesne|studia|uczelni|legitymac|szkol", "Образование"),
    (r"urzad skarbowy|urzad miasta|us-transfer|oplata skarbowa", "Налоги и сборы"),
    (r"\bfee\b|card-fee|\binterest\b|oplata za prowadzenie|oplata - przelew|odsetek", "Банк и комиссии"),
    (r"card-atm", "Переводы/Снятие наличных"),
    (r"c2c", "Переводы/Людям"),
    (r"biedronka|zabka|carrefour|auchan|\bnetto\b|\bdino\b|stokrotka|lewiatan|polomarket|kaufland|lidl", "Еда"),
    (r"vending", "Еда/Снеки и орехи"),
    (r"super-pharm|rossmann|hebe", "Гигиена и косметика"),
    (r"apteka|doz", "Здоровье/Аптека"),
    (r"erecept|medicover|lekarz|przychodnia", "Здоровье/Врачи"),
    (r"vape|tyton|papieros", "Табак и вейп"),
    (r"restaurac|bistro|pizza|kebab|mcdonald|kfc|burger|kawiarnia|\bcafe\b|sushi|pyszne|glovo|wolt", "Кафе и доставка"),
    (r"koleo|jakdojade|city-?nav|\bmpk\b|bilet", "Транспорт/Общественный транспорт"),
    (r"orlen|\bshell\b|circle k|\bbp\b", "Транспорт/Топливо"),
    (r"\buber\b|\bbolt\b|freenow", "Транспорт/Такси"),
    (r"orange|t-mobile|\bplay\b|plus gsm", "Связь и интернет"),
    (r"google play|netflix|spotify|youtube|anthropic|claude|apple\.com|chatgpt|openai", "Подписки"),
    (r"\baction\b|pepco|jysk|ikea|castorama|leroy", "Дом"),
    (r"kino|cinema|teatr|bilety", "Развлечения"),
]
INCOME_RULES = [
    (r"wynagrodzenie|pensja|premia", "Доходы/Зарплата"),
    (r"glovo|wolt|uber|bolt", "Доходы/Подработка"),
    (r"stypendium", "Доходы/Стипендия"),
    (r"cash-in", "Переводы/Взнос наличных"),
    (r"transfer-in|elixir-in|c2c", "Переводы/От людей"),
]
def ensure_categories(con):
    categories.seed(con)  # дерево (в т.ч. категории банка) — в core/categories.py
    have = {r["name"] for r in con.execute("PRAGMA table_info(bank_tx)")}
    for col, kind in (("purchase_id", "TEXT"), ("category_id", "INTEGER"), ("category_source", "TEXT")):
        if col not in have:
            con.execute(f"ALTER TABLE bank_tx ADD COLUMN {col} {kind}")
    con.commit()


def card_labels() -> dict:
    """config.ini [cards]: последние 4 цифры = подпись («1234 = карта другого банка»)."""
    import configparser
    from core.common import BUDGET
    cfg = configparser.ConfigParser()
    cfg.read(BUDGET / "config.ini", encoding="utf-8")
    return dict(cfg["cards"]) if cfg.has_section("cards") else {}


def merchant_of(desc: str) -> str | None:
    f = fold(desc)
    return next((name for rx, name in MERCHANTS if re.search(rx, f)), None)


def clean_desc(desc: str) -> str:
    """«WARSZAWASKLEP LIDL 1234PL» -> «SKLEP LIDL 1234»"""
    s = CITY_PREFIX.sub("", desc or "").strip()
    s = re.sub(r"(PL|IE|UA|LU|GB|DE|NL|US)$", "", s).strip()
    return s or desc


def rule_category(text: str, rules) -> str | None:
    f = fold(text)
    return next((path for rx, path in rules if re.search(rx, f)), None)


def day(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def reconcile(verbose=True):
    """Каждый запуск начинается с чистых сумм: мягкая сверка пересчитывает позиции под банк,
    поэтому сначала пересобираем покупки из исходников (чеки, письма) — это быстро."""
    from receipts import kaufland, lidl, mail_orders
    lidl.reparse()
    kaufland.reparse()
    mail_orders.parse_all(verbose=False)
    con = connect()
    from receipts import photos
    if merged := photos.merge_duplicates(con):
        print(f"Фото чеков, совпавших с электронными чеками, прикреплено: {merged}")
    ensure_categories(con)
    ids = categories.ids_by_path(con)
    # пометки прошлой сверки убираем (примечания разбора — курс валюты, «без подтверждения» — остаются)
    marks = ("проверить: в банке", "не со счёта PKO", "оплачено: ", "оплачено вместе", "оплачено по банку", "→ по банку")
    for r in con.execute("SELECT id, note FROM purchases WHERE note IS NOT NULL").fetchall():
        parts = [s for s in r["note"].split("; ") if not any(m in s for m in marks)]
        parts = [s.split(" → по банку")[0] for s in parts]
        con.execute("UPDATE purchases SET note = ? WHERE id = ?", ("; ".join(parts) or None, r["id"]))
    con.execute("UPDATE purchases SET bank_tx_id = NULL WHERE source != 'bank'")
    con.execute("UPDATE bank_tx SET purchase_id = NULL")
    # ручные правки категорий у операций банка переживают пересборку
    manual_bank = {r["purchase_id"]: r["category_id"] for r in con.execute(
        "SELECT purchase_id, category_id FROM items WHERE purchase_id LIKE 'bank:%' AND category_source = 'manual'")}
    con.execute("DELETE FROM items WHERE purchase_id LIKE 'bank:%'")
    con.execute("DELETE FROM payments WHERE purchase_id LIKE 'bank:%'")
    con.execute("DELETE FROM purchases WHERE source = 'bank'")

    txs = [dict(r) for r in con.execute("SELECT * FROM bank_tx ORDER BY date")]
    for t in txs:
        t["merchant"] = merchant_of(t["description"] or "")
    debits = [t for t in txs if t["amount"] < 0]
    purchases = [dict(r) for r in con.execute(
        "SELECT id, date, merchant, total, payment_method, note, card_last4 FROM purchases "
        "WHERE total IS NOT NULL AND total > 0")]

    # 1. пары «покупка ↔ операция»: сначала с совпадением продавца, затем по ближайшей дате
    pairs = []
    for p in purchases:
        if p["payment_method"] == "cash":
            continue
        foreign = "по курсу" in (p["note"] or "")
        pd = day(p["date"])
        for t in debits:
            delta = (day(t["date"]) - pd).days
            if not -3 <= delta <= 5:
                continue
            diff = abs(-t["amount"] - p["total"])
            if diff > (p["total"] * 0.06 if foreign else 0.005):
                continue
            same_shop = t["merchant"] is not None and t["merchant"] == p["merchant"]
            if t["merchant"] and p["merchant"] and t["merchant"] != p["merchant"] and t["type"] == "CARD-PAYMENT":
                continue  # оплата картой в другом магазине — точно не эта покупка
            pairs.append(((same_shop, -abs(delta), -diff), p, t))
    pairs.sort(key=lambda x: x[0], reverse=True)
    used_p, used_t, matched = set(), set(), 0
    for _, p, t in pairs:
        if p["id"] in used_p or t["id"] in used_t:
            continue
        used_p.add(p["id"])
        used_t.add(t["id"])
        con.execute("UPDATE purchases SET bank_tx_id = ? WHERE id = ?", (t["id"], p["id"]))
        con.execute("UPDATE bank_tx SET purchase_id = ? WHERE id = ?", (p["id"], t["id"]))
        if "по курсу" in (p["note"] or "") and abs(-t["amount"] - p["total"]) >= 0.01:
            # валютная покупка: фактическая сумма — из банка
            k = -t["amount"] / p["total"]
            con.execute("UPDATE items SET amount = round(amount * ?, 2), unit_price = round(unit_price * ?, 2) "
                        "WHERE purchase_id = ?", (k, k, p["id"]))
            con.execute("UPDATE purchases SET total = ?, note = note || ' → по банку ' || ? WHERE id = ?",
                        (-t["amount"], f"{-t['amount']:.2f} zł", p["id"]))
        matched += 1

    def link(p, t, note=None):
        used_p.add(p["id"])
        used_t.add(t["id"])
        con.execute("UPDATE purchases SET bank_tx_id = ? WHERE id = ?", (t["id"], p["id"]))
        con.execute("UPDATE bank_tx SET purchase_id = coalesce(purchase_id || ',', '') || ? WHERE id = ?", (p["id"], t["id"]))
        if note:
            con.execute("UPDATE purchases SET note = coalesce(note || '; ', '') || ? WHERE id = ?", (note, p["id"]))

    free_p = [p for p in purchases if p["id"] not in used_p and p["payment_method"] != "cash" and p["merchant"]]
    free_t = [t for t in debits if t["id"] not in used_t and t["merchant"]]

    # 1б. несколько заказов одного магазина оплачены одной операцией (аптека: два заказа — один чек)
    from itertools import combinations
    for t in free_t:
        if t["id"] in used_t:
            continue
        cands = [p for p in free_p if p["id"] not in used_p and p["merchant"] == t["merchant"]
                 and 0 <= (day(t["date"]) - day(p["date"])).days <= 14]
        for k in (2, 3):
            hit = next((c for c in combinations(cands, k) if abs(sum(p["total"] for p in c) + t["amount"]) < 0.01), None)
            if hit:
                for p in hit:
                    link(p, t, f"оплачено вместе с другими заказами одной операцией {-t['amount']:.2f} zł")
                matched += len(hit)
                break

    # 1в. мягкое совпадение: тот же магазин, оплата до 14 дней после заказа, сумма отличается до 35%
    #     (цена в аптеке с рецептом, доплата за доставку...) — фактическая сумма берётся из банка
    soft = []
    for t in free_t:
        for p in free_p:
            delta = (day(t["date"]) - day(p["date"])).days
            if t["id"] in used_t or p["id"] in used_p or p["merchant"] != t["merchant"] or not -3 <= delta <= 14:
                continue
            ratio = -t["amount"] / p["total"]
            if 0.65 <= ratio <= 1.35:
                soft.append((abs(1 - ratio), abs(delta), p, t))
    for _, _, p, t in sorted(soft, key=lambda x: (x[0], x[1])):
        if p["id"] in used_p or t["id"] in used_t:
            continue
        k = -t["amount"] / p["total"]
        con.execute("UPDATE items SET amount = round(amount * ?, 2), unit_price = round(unit_price * ?, 2), "
                    "discount = round(discount * ?, 2) WHERE purchase_id = ?", (k, k, k, p["id"]))
        con.execute("UPDATE purchases SET total = ? WHERE id = ?", (-t["amount"], p["id"]))
        link(p, t, None if abs(k - 1) < 0.001 else f"в заказе {p['total']:.2f} zł, оплачено по банку {-t['amount']:.2f} zł")
        matched += 1

    # 1г. несколько заказов одного магазина забраны и оплачены вместе, а в кассе сумма другая
    #     (DOZ: два заказа «оплата при получении» — одна оплата картой в аптеке по цене с рецептом)
    soft_multi = []
    for t in free_t:
        if t["id"] in used_t:
            continue
        cands = [p for p in free_p if p["id"] not in used_p and p["merchant"] == t["merchant"]
                 and 0 <= (day(t["date"]) - day(p["date"])).days <= 14]
        for n in (2, 3):
            for c in combinations(cands, n):
                if (max(day(p["date"]) for p in c) - min(day(p["date"]) for p in c)).days > 2:
                    continue  # заказы сделаны в разное время — вряд ли оплачены одной операцией
                ratio = -t["amount"] / sum(p["total"] for p in c)
                if 0.65 <= ratio <= 1.35:
                    soft_multi.append((abs(1 - ratio), t, c))
    for _, t, c in sorted(soft_multi, key=lambda x: x[0]):
        if t["id"] in used_t or any(p["id"] in used_p for p in c):
            continue
        was = sum(p["total"] for p in c)
        paid = -t["amount"]
        new = [round(p["total"] * paid / was, 2) for p in c]
        new[-1] = round(paid - sum(new[:-1]), 2)  # копейки округления — в последний заказ, чтобы сумма сошлась с банком
        for p, total in zip(c, new):
            k = total / p["total"]
            con.execute("UPDATE items SET amount = round(amount * ?, 2), unit_price = round(unit_price * ?, 2), "
                        "discount = round(discount * ?, 2) WHERE purchase_id = ?", (k, k, k, p["id"]))
            con.execute("UPDATE purchases SET total = ? WHERE id = ?", (total, p["id"]))
            link(p, t, f"заказы на {was:.2f} zł оплачены вместе одной операцией {paid:.2f} zł")
        matched += len(c)

    # способ оплаты из найденной операции в выписке: в письмах он часто не указан или указан общо («przelew»),
    # а выписка точно знает — карта, BLIK или перевод. У чеков Lidl/Kaufland/фото остаётся способ из чека
    con.execute("""UPDATE purchases SET payment_method = (
                       SELECT CASE WHEN b.type LIKE 'CARD%' THEN 'card' WHEN b.type LIKE 'MOBILE-PAYMENT%' THEN 'blik'
                                   ELSE 'transfer' END FROM bank_tx b WHERE b.id = purchases.bank_tx_id)
                   WHERE bank_tx_id IS NOT NULL AND source != 'bank' AND coalesce(payment_method, '') NOT IN ('deferred', 'cash')
                     AND (source = 'email' OR coalesce(payment_method, '') = '')""")

    # 2. операции без чека -> покупки «банк»; доходы и переводы — категории в bank_tx
    by_total = {}
    for p in purchases:
        by_total.setdefault(round(p["total"], 2), []).append(p)
    made = refunds = 0
    for t in txs:
        if t["id"] in used_t:
            continue
        text = " ".join(filter(None, [t["type"], t["counterparty"], t["description"]]))
        if t["amount"] > 0 and "RETURN" not in (t["type"] or ""):
            if t.get("category_source") == "manual":
                continue  # твоя правка категории поступления сохраняется
            path = rule_category(text, INCOME_RULES)
            con.execute("UPDATE bank_tx SET category_id = ?, category_source = 'rule' WHERE id = ?",
                        (ids.get(path), t["id"]))
            continue
        name = t["counterparty"] or clean_desc(t["description"] or "")
        if t["type"] and t["type"].startswith("MOBILE-PAYMENT-POS"):
            name = f"BLIK: {t['description']}"
        if t["type"] in ("CARD-PAYMENT", "CARD-ATM"):
            name = clean_desc(t["description"] or "")
            if re.fullmatch(r"[\d\s]+", name):  # терминал без названия: «WROCLAW028PL» -> «028»
                name = f"Карта: {name}"
        if t["counterparty"] and t["description"] and t["type"] and "C2C" not in t["type"]:
            name = f"{t['counterparty']}: {t['description']}"
        path = rule_category(text, BANK_RULES)
        amount = -t["amount"]
        if t["amount"] > 0:  # возврат на карту: ищем исходную покупку той же суммы за 90 дней
            refunds += 1
            orig = next((p for p in by_total.get(round(t["amount"], 2), [])
                         if 0 <= (day(t["date"]) - day(p["date"])).days <= 90), None)
            if orig:
                row = con.execute("SELECT i.name, i.category_id FROM items i WHERE i.purchase_id = ? ORDER BY i.amount DESC",
                                  (orig["id"],)).fetchone()
                name = f"Возврат: {row['name']}" if row else f"Возврат: {orig['merchant']}"
                path = categories.paths(con).get(row["category_id"]) if row and row["category_id"] else path
            else:
                name = f"Возврат: {name}"
        method = {"CARD-PAYMENT": "card", "CARD-ATM": "card", "CARD-PAYMENT-RETURN": "card"}.get(
            t["type"], "blik" if (t["type"] or "").startswith("MOBILE-PAYMENT") else "transfer")
        pid = f"bank:{t['id']}"
        merchant = t["merchant"] or (t["counterparty"] or clean_desc(t["description"] or ""))[:40]
        if re.fullmatch(r"[\d\s]*", merchant):  # у BLIK и части терминалов вместо магазина — номер операции
            merchant = "BLIK без названия" if (t["type"] or "").startswith("MOBILE-PAYMENT") else "Карта без названия"
        save_purchase(con, {"id": pid, "source": "bank", "date": t["date"] + "T12:00:00",
                            "merchant": merchant,
                            "store": t["description"], "total": amount, "payment_method": method, "discount": 0},
                      [{"name": name[:120], "product_code": None, "qty": 1, "unit_price": amount, "amount": amount,
                        "discount": None}])
        cid = ids.get(path) if path else None
        src = "bank" if cid else None
        if pid in manual_bank:
            cid, src = manual_bank[pid], "manual"
        con.execute("UPDATE items SET category_id = ?, category_source = ? WHERE purchase_id = ?", (cid, src, pid))
        con.execute("UPDATE purchases SET bank_tx_id = ? WHERE id = ?", (t["id"], pid))
        made += 1

    # карты другого счёта: по карте ни одного совпадения при 5+ покупках в период выписки
    first = txs[0]["date"] if txs else ""
    per_card = {}
    for p in purchases:
        if p["card_last4"] and p["date"][:10] >= first:
            s = per_card.setdefault(p["card_last4"], [0, 0])
            s[0] += 1
            s[1] += p["id"] in used_p
    other_cards = {c for c, (n, m) in per_card.items() if n >= 5 and m == 0}
    labels = card_labels()
    for c in other_cards:
        label = f"{labels[c]} •{c}" if c in labels else f"картой •{c} — не со счёта PKO"
        con.execute("UPDATE purchases SET note = coalesce(note || '; ', '') || ? WHERE card_last4 = ? AND bank_tx_id IS NULL",
                    (f"оплачено: {label}", c))

    # покупка без пары, а рядом у того же магазина есть операция без чека — вероятно, это она (другая сумма)
    for p in purchases:
        if p["id"] in used_p or p["payment_method"] == "cash" or not p["merchant"] or p["card_last4"] in other_cards:
            continue
        near = [t for t in debits if t["id"] not in used_t and t["merchant"] == p["merchant"]
                and -3 <= (day(t["date"]) - day(p["date"])).days <= 14]
        if near:
            t = near[0]
            con.execute("UPDATE purchases SET note = coalesce(note || '; ', '') || ? WHERE id = ?",
                        (f"проверить: в банке {t['date']} операция {p['merchant']} на {-t['amount']:.2f} zł без чека "
                         f"— возможно, это эта покупка (тогда трата посчитана дважды)", p["id"]))

    from core import wallet
    wallet.rebuild(con)  # взносы наличных (доход/перенос), траты наличными без чека

    last = con.execute("SELECT max(date) FROM bank_tx").fetchone()[0]
    set_meta(con, "bank_last_date", last)
    con.commit()
    categories.categorize(con)  # ручные правила по названию применяются и к операциям банка
    if verbose:
        if other_cards:
            labels = card_labels()
            print("Карты другого счёта: " + ", ".join(f"•{c}" + (f" ({labels[c]})" if c in labels else "") for c in sorted(other_cards)))
        own = [p for p in purchases if p["payment_method"] != "cash"]
        after = [p for p in own if p["date"][:10] <= (last or "")]
        print(f"Покупок (не наличные) до {last}: {len(after)}, найдено в банке: {matched}")
        miss = [p for p in after if p["id"] not in used_p and p["date"][:10] >= (txs[0]["date"] if txs else "")]
        print(f"Не найдено в банке: {len(miss)} (оплата другой картой/счётом, наличными или сумма отличается)")
        print(f"Операций банка без чека: {made} (из них возвратов: {refunds})")
    return matched, made
