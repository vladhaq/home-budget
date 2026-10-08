"""Сверка покупок с выпиской банка.

1. Каждая покупка (чек, заказ из почты, фото) ищет свою операцию в банке: та же сумма, дата -3..+5 дней,
   совпадение продавца — приоритет. Для покупок в валюте — допуск 6% (курс и комиссия банка ≠ курс NBP).
2. Операции без чека становятся покупками с источником «банк» (категория — по получателю и описанию).
3. Переводы людям, банкомат — категории «Переводы» (не расход). Доходы и взносы наличных — категории «Доходы».
4. Возвраты на карту — отрицательные покупки со ссылкой на исходную покупку (refund_of), её категория.
5. Регистрация платежа без подтверждения, которой нет в выписке, — «под вопросом» (в суммы не идёт).

reconcile() — пересборка источников + сверка; match() — только сверка (повторяемая, суммы из чека хранятся в orig_*).

  python budget.py reconcile
"""
import datetime as dt
import json
import re

from core import categories
from core.common import fold, local_pairs
from core.db import connect, save_purchase, set_meta

# город, склеенный с названием в описании операции карты; свои небольшие города — config.ini [bank] cities
_LOCAL_CITIES = "".join("|" + re.escape(fold(c.strip())) for k, v in local_pairs("bank") if k == "cities"
                        for c in v.split(",") if c.strip())
CITY_PREFIX = re.compile(r"^(wroclaw|warszawa|poznan|lodz|gdansk|gdynia|krakow|katowice|szczecin|lublin|bydgoszcz|bialystok"
                         r"|rzeszow|opole|dublin|kyiv|anthropic\.com|luxembourg|london|amsterdam|berlin" + _LOCAL_CITIES + ")", re.I)
# для распознавания (правила, магазин) — шире: в выписке город склеен с названием («WarsawBOLT.EU», «NicosiaSA *V …»).
# На названия позиций не влияет: по ним построены твои правила
MATCH_PREFIX = re.compile(CITY_PREFIX.pattern[:-1] + r"|warsaw|kobierzyce|nicosia|difc|san jose|cork|paris|minsk|vilnius"
                          r"|mountain view|legnica|walbrzych|torun|kielce|olsztyn|czestochowa|radom|zielona gora)", re.I)
MERCHANTS = local_pairs("merchants") + [  # шаблон в описании операции -> магазин (как в покупках); свои — config.ini
    (r"kaufland", "Kaufland"), (r"lidl", "Lidl"), (r"allegro", "Allegro"), (r"biedronka", "Biedronka"),
    (r"zabka", "Żabka"), (r"koleo", "KOLEO"), (r"erecept", "Erecept"), (r"orange flex", "Orange Flex"),
    (r"doz apteka|doz\.pl", "DOZ.pl"), (r"super-pharm", "Super-Pharm"), (r"rossmann", "Rossmann"),
    (r"\baction\b", "Action"), (r"carrefour", "Carrefour"), (r"\baldi\b", "ALDI"), (r"ikea", "IKEA"), (r"\bagata\b", "Agata"),
    (r"\bshell\b", "Shell"), (r"\borlen\b", "Orlen"), (r"bolt\.eu|\bbolt\b", "Bolt"), (r"\buber\b", "Uber"),
    (r"poczta polska", "Poczta Polska"), (r"vapebox", "Vapebox"), (r"urbancard", "Urbancard"), (r"auchan", "Auchan"), (r"anthropic|claude", "Anthropic"),
    (r"google play", "Google Play"), (r"google", "Google"), (r"vending", "Automat (vending)"),
    (r"city-?nav|jakdojade", "Jakdojade"), (r"media ?expert", "Media Expert"), (r"apteka", "Apteka"),
    (r"\bnetto\b", "Netto"), (r"\bdino\b", "Dino"), (r"pepco", "Pepco"), (r"mcdonald", "McDonald's"), (r"kfc", "KFC"),
]
# категория по операции без чека: шаблон по «тип + получатель + описание» (fold) -> ключ категории.
# Свои правила (config.ini [bank_rules]) — первыми
BANK_RULES = local_pairs("bank_rules") + [
    (r"eksploatac|media za|prad|gaz ziemny|woda i sciek", "housing.utilities"),
    (r"wynajmujacy|czynsz|za mieszkanie|najem lokal", "housing.rent"),
    (r"czesne|studia|uczelni|legitymac|szkol", "education"),
    (r"urzad skarbowy|urzad miasta|us-transfer|oplata skarbowa", "finance.taxes"),
    (r"\bfee\b|card-fee|\binterest\b|oplata za prowadzenie|oplata - przelew|oplata miesieczna za karte|odsetek"
     r"|oproc debetu", "finance.bank"),
    (r"card-atm", "transfer.atm"),
    # перевод на телефон: назначение — в заголовке («KINO», «DLA DŁUGU»), иначе — перевод человеку (не расход)
    (r"c2c.*\b(kino|cinema)\b", "leisure.fun"),
    (r"c2c.*\b(dlug\w*|zwrot\w*|pozycz\w*|oddaj\w*)\b", "transfer.debt"),
    (r"c2c", "transfer.out"),
    (r"biedronka|zabka|carrefour|auchan|\bnetto\b|\bdino\b|stokrotka|lewiatan|polomarket|kaufland|lidl|\baldi\b"
     r"|best market|\bspar\b|intermarche", "food.nocheck"),
    (r"vending", "food.snacks"),
    (r"super-pharm|apteka|\bdoz\b|gdziepolek|ziko", "health.pharmacy"),  # Super-Pharm — по твоим покупкам это лекарства
    (r"rossmann|\bhebe\b|drogeria natura", "health.hygiene"),
    (r"erecept|medicover|lekarz|przychodnia|synevo|diagnostyka|luxmed|enel-med", "health.doctors"),
    (r"vape|tyton|papieros", "vice.tobacco"),
    (r"restaurac|bistro|pizza|kebab|mcdonald|kfc|burger|kawiarnia|\bcafe\b|sushi|pyszne|glovo|wolt", "leisure.cafe"),
    (r"koleo|\bpkp\b|intercity|polregio|koleje|flixbus", "transport.intercity"),
    (r"jakdojade|city-?nav|\bmpk\b|urbancard|bilet", "transport.city"),
    (r"orlen|\bshell\b|circle k|\bbp\b|\bmol\b|amic", "transport.fuel"),
    (r"\buber\b|\bbolt\b|freenow", "transport.taxi"),
    # подписки раньше операторов связи: «Google Play» — не оператор Play
    (r"google play|netflix|spotify|youtube|anthropic|claude|apple\.com|chatgpt|openai", "comms.subscriptions"),
    (r"orange|t-mobile|\bplay\b|plus gsm", "comms.mobile"),
    (r"ikea|agata|jysk|\bjula\b", "home.furniture"),
    (r"castorama|leroy|obi\b|bricomarche|psb", "home.repair"),
    (r"\baction\b|pepco|tedi|kik\b", "home"),
    (r"kino|cinema|teatr|bilety", "leisure.fun"),
    (r"poczta polska|inpost|\bdpd\b|\bdhl\b|orlen paczka", "other.delivery"),
    (r"fundacja|donateo|zrzutka|pomagam|siepomaga", "leisure.gifts"),
]
INCOME_RULES = local_pairs("income_rules") + [  # свои (работодатель, подработка) — config.ini [income_rules]
    (r"wynagrodzenie|pensja|premia", "income.salary"),
    (r"glovo|wolt|uber|bolt", "income.side"),
    (r"stypendium", "income.scholarship"),
    (r"cash-in", "income.cash"),
    (r"\b(dlug\w*|zwrot\w*|pozycz\w*|oddaj\w*)\b", "transfer.debt"),
    (r"transfer-in|elixir-in|c2c", "transfer.in"),
]
def ensure_categories(con):
    categories.seed(con)  # дерево (в т.ч. категории банка) — в core/categories.py; колонки bank_tx — в core/db.py


def card_labels() -> dict:
    """config.ini [cards]: последние 4 цифры = подпись («1234 = карта другого банка»)."""
    import configparser
    from core.common import BUDGET
    cfg = configparser.ConfigParser()
    cfg.read(BUDGET / "config.ini", encoding="utf-8")
    return dict(cfg["cards"]) if cfg.has_section("cards") else {}


def merchant_of(desc: str) -> str | None:
    f = fold(MATCH_PREFIX.sub("", desc or ""))
    return next((name for rx, name in MERCHANTS if re.search(rx, f)), None)


def rule_text(t: dict) -> str:
    """Текст операции для правил: тип + получатель + описание без склеенного города и без «OD: … DO: …»."""
    desc = MATCH_PREFIX.sub("", t.get("description") or "").strip()
    desc = re.sub(r"(PL|IE|UA|LU|GB|DE|NL|US|CY|AE)$", "", desc)
    if "C2C" in (t.get("type") or ""):  # перевод на телефон: «KINOOD: 48500000000 DO: 485*****000» -> «KINO»
        desc = re.sub(r"\s*OD: ?[\d*]+.*$", "", desc)
    return " ".join(filter(None, [t.get("type"), t.get("counterparty"), desc]))


def blik_ref(desc: str) -> str:
    """Номер операции BLIK — в начале описания (дальше бывает адрес из выписки-файла); без ведущих нулей."""
    m = re.match(r"\s*0*(\d{8,})", desc or "")
    return m.group(1) if m else ""


def blik_place(desc: str) -> str:
    """Где платил BLIK — адрес из выписки-файла: «00000000000000001 http://www.example.nl/» -> «example.nl»."""
    s = re.sub(r"^\s*\d{8,}\s*", "", desc or "")
    return re.sub(r"^(https?://)?(www\.)?", "", s.strip(), flags=re.I).rstrip("/ ")


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
    """Полная пересборка: покупки из исходников (чеки Lidl/Kaufland, письма) и сверка с банком."""
    from receipts import kaufland, lidl, mail_orders
    lidl.reparse()
    kaufland.reparse()
    mail_orders.parse_all(verbose=False)
    return match(verbose)


def brand(merchant: str | None) -> str:
    """Магазин без адреса сайта и юрлица: «example.nl» и «Example» — один магазин."""
    s = fold(merchant or "")
    s = re.sub(r"^(https?://)?(www\.)?", "", s)
    s = re.sub(r"\.(pl|nl|com|de|eu|net|org|co\.uk)\b.*$", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def refund_origin(con, t, merchant: str | None = None) -> dict | None:
    """Исходная покупка возврата на карту: за 90 дней до него — покупка той же суммы, иначе покупка с позицией
    такой суммы (вернул один товар из заказа). Свой магазин, полное совпадение и найденные в банке — в приоритете.
    По сумме не нашлось (вернул часть заказа со скидкой) — единственный заказ того же магазина за 60 дней."""
    amount = round(t["amount"], 2)
    cands = []
    for r in con.execute("""SELECT p.id, p.merchant, p.date, p.total, p.bank_tx_id, i.name,
                                   (SELECT count(DISTINCT name) FROM items WHERE purchase_id = p.id
                                    AND name NOT IN ('Dostawa', 'Koszt płatności')) n,
                                   round(i.amount - coalesce(i.discount, 0), 2) v
                            FROM purchases p JOIN items i ON i.purchase_id = p.id
                            WHERE p.total > 0 AND date(p.date) BETWEEN date(?, '-90 day') AND date(?)
                            ORDER BY i.amount DESC""", (t["date"], t["date"])):
        whole = abs(r["total"] - amount) < 0.005
        if whole or (amount >= 5 and abs(r["v"] - amount) < 0.005):
            same = t["merchant"] is not None and r["merchant"] == t["merchant"]
            cands.append(((same, whole, r["bank_tx_id"] is not None, r["date"]), r))
    if not cands:
        b = brand(merchant or t.get("merchant"))
        shop = [r for r in con.execute("""SELECT p.id, p.merchant, p.date, p.total FROM purchases p
                                          WHERE p.total > ? AND date(p.date) BETWEEN date(?, '-60 day') AND date(?)""",
                                       (amount - 0.01, t["date"], t["date"])) if b and brand(r["merchant"]) == b]
        if len(shop) == 1:  # заказов этого магазина несколько — не угадываем
            r = shop[0]
            return {"id": r["id"], "item": f"часть заказа от {r['date'][8:10]}.{r['date'][5:7]}.{r['date'][2:4]}"}
        return None
    (_, whole, *_), r = max(cands, key=lambda c: c[0])
    more = f" и ещё {r['n'] - 1}" if whole and r["n"] > 1 else ""  # вернул весь заказ из нескольких товаров
    return {"id": r["id"], "item": r["name"] + more}


def migrate_rules_v2(con):
    """Разово: правила «по названию», созданные для операций банка (названия уникальны: номер BLIK, заказа, точки),
    становятся правилом по магазину или — если магазина нет или правила спорят — ручной категорией этих операций."""
    from core.db import get_meta, set_meta
    if get_meta(con, "rules_v2"):
        return
    K = categories.key_ids(con)
    food = K.get("food")
    plan = {}
    for r in con.execute("SELECT id, pattern, category_id FROM rules WHERE target = 'item' AND pattern IS NOT NULL").fetchall():
        rx = re.compile(r["pattern"])
        hits = [x for x in con.execute("SELECT i.purchase_id, i.line, i.name, p.merchant, p.source FROM items i "
                                       "JOIN purchases p ON p.id = i.purchase_id").fetchall() if rx.search(fold(x["name"]))]
        if hits and all(x["source"] == "bank" for x in hits):
            plan[r["id"]] = (r["category_id"], hits)
    # правило на весь магазин — только если там больше одной покупки, категории не спорят и у магазина нет чеков
    # (иначе «Kaufland → хозтовары» задело бы нераспознанные позиции чеков Kaufland)
    with_receipts = {categories.shop_key(r["merchant"]) for r in con.execute(
        "SELECT DISTINCT merchant FROM purchases WHERE source != 'bank'")}
    by_shop, buys = {}, {}
    for rid, (cat, hits) in plan.items():
        for x in hits:
            if x["merchant"] not in categories.PLACEHOLDER_SHOPS and not re.search(r"\d{6,}", x["name"]):
                key = categories.shop_key(x["merchant"])
                by_shop.setdefault(key, set()).add(cat)
                buys.setdefault(key, set()).add(x["purchase_id"])
    shop_ok = {k for k, cats in by_shop.items() if len(cats) == 1 and len(buys[k]) > 1 and k not in with_receipts}
    for rid, (cat, hits) in plan.items():
        for x in hits:
            key = categories.shop_key(x["merchant"])
            if x["merchant"] in categories.PLACEHOLDER_SHOPS or re.search(r"\d{6,}", x["name"]) or key not in shop_ok:
                con.execute("UPDATE items SET category_id = ?, category_source = 'manual' WHERE purchase_id = ? AND line = ?",
                            (cat, x["purchase_id"], x["line"]))
            else:  # «Еда» целиком у магазина без чека — теперь «Продукты без чека»
                categories.add_shop_rule(con, x["merchant"], K.get("food.nocheck", cat) if cat == food else cat)
        con.execute("DELETE FROM rules WHERE id = ?", (rid,))
    set_meta(con, "rules_v2", "1")
    con.commit()


def match(verbose=True):
    """Сверка с банком на уже собранных покупках. Можно запускать сколько угодно раз: суммы, подогнанные
    под банк в прошлый раз (мягкая сверка, валюта), сначала возвращаются к суммам из чека."""
    con = connect()
    from receipts import photos
    if merged := photos.merge_duplicates(con):
        print(f"Фото чеков, совпавших с электронными чеками, прикреплено: {merged}")
    ensure_categories(con)
    K = categories.key_ids(con)
    con.execute("UPDATE items SET amount = orig_amount, unit_price = orig_unit_price, discount = orig_discount, "
                "orig_amount = NULL, orig_unit_price = NULL, orig_discount = NULL WHERE orig_amount IS NOT NULL")
    con.execute("UPDATE purchases SET total = orig_total, orig_total = NULL WHERE orig_total IS NOT NULL")
    con.execute("UPDATE purchases SET status = NULL, refund_of = NULL")
    # пометки прошлой сверки убираем (примечания разбора — курс валюты, «без подтверждения» — остаются)
    marks = ("проверить: в банке", "не со счёта PKO", "оплачено: ", "оплачено вместе", "оплачено по банку", "→ по банку")
    for r in con.execute("SELECT id, note FROM purchases WHERE note IS NOT NULL").fetchall():
        parts = [s for s in r["note"].split("; ") if not any(m in s for m in marks)]
        parts = [s.split(" → по банку")[0] for s in parts]
        con.execute("UPDATE purchases SET note = ? WHERE id = ?", ("; ".join(parts) or None, r["id"]))
    con.execute("UPDATE purchases SET bank_tx_id = NULL WHERE source != 'bank'")
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
        "SELECT id, date, merchant, total, payment_method, note, card_last4, pay_ref FROM purchases "
        "WHERE total IS NOT NULL AND total > 0")]

    def scale(pid, total):
        """Сумма покупки — как списал банк; сумма из чека запоминается (orig_*) для следующей сверки."""
        was = con.execute("SELECT total FROM purchases WHERE id = ?", (pid,)).fetchone()["total"]
        k = total / was
        con.execute("UPDATE items SET orig_amount = coalesce(orig_amount, amount), "
                    "orig_unit_price = coalesce(orig_unit_price, unit_price), "
                    "orig_discount = CASE WHEN orig_amount IS NULL THEN discount ELSE orig_discount END WHERE purchase_id = ?",
                    (pid,))
        con.execute("UPDATE items SET amount = round(amount * ?, 2), unit_price = round(unit_price * ?, 2), "
                    "discount = round(discount * ?, 2) WHERE purchase_id = ?", (k, k, k, pid))
        con.execute("UPDATE purchases SET orig_total = coalesce(orig_total, total), total = ? WHERE id = ?", (total, pid))

    # 1. пары «покупка ↔ операция»: сначала по номеру BLIK, затем с совпадением продавца, затем по ближайшей дате
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
            ref, tref = (p["pay_ref"] or "").lstrip("0"), blik_ref(t["description"])
            exact = bool(ref) and tref == ref
            if ref and tref and not exact:
                continue  # у операции BLIK свой номер, и он не этой покупки
            pairs.append(((exact, same_shop, -abs(delta), -diff), p, t))
    pairs.sort(key=lambda x: x[0], reverse=True)
    used_p, used_t, matched = set(), set(), 0
    for _, p, t in pairs:
        if p["id"] in used_p or t["id"] in used_t:
            continue
        used_p.add(p["id"])
        used_t.add(t["id"])
        con.execute("UPDATE purchases SET bank_tx_id = ? WHERE id = ?", (t["id"], p["id"]))
        if "по курсу" in (p["note"] or "") and abs(-t["amount"] - p["total"]) >= 0.01:
            # валютная покупка: фактическая сумма — из банка
            scale(p["id"], -t["amount"])
            con.execute("UPDATE purchases SET note = note || ' → по банку ' || ? WHERE id = ?",
                        (f"{-t['amount']:.2f} zł", p["id"]))
        matched += 1

    def link(p, t, note=None):
        used_p.add(p["id"])
        used_t.add(t["id"])
        con.execute("UPDATE purchases SET bank_tx_id = ? WHERE id = ?", (t["id"], p["id"]))
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
        scale(p["id"], -t["amount"])
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
            scale(p["id"], total)
            link(p, t, f"заказы на {was:.2f} zł оплачены вместе одной операцией {paid:.2f} zł")
        matched += len(c)

    # способ оплаты из найденной операции в выписке: в письмах он часто не указан или указан общо («przelew»),
    # а выписка точно знает — карта, BLIK или перевод. У чеков Lidl/Kaufland/фото остаётся способ из чека
    con.execute("""UPDATE purchases SET payment_method = (
                       SELECT CASE WHEN b.type LIKE 'CARD%' THEN 'card' WHEN b.type LIKE 'MOBILE-PAYMENT%' THEN 'blik'
                                   ELSE 'transfer' END FROM bank_tx b WHERE b.id = purchases.bank_tx_id)
                   WHERE bank_tx_id IS NOT NULL AND source != 'bank' AND coalesce(payment_method, '') NOT IN ('deferred', 'cash')
                     AND (source = 'email' OR coalesce(payment_method, '') = '')""")

    # «под вопросом»: регистрация платежа (Przelewy24/PayU) без подтверждения, которой нет в выписке, хотя выписка
    # этот день уже покрывает (+5 дней на проводку), или повтор той же суммы тому же магазину в пределах 10 минут.
    # Неоплаченную регистрацию платёжная система отменяет сама. В суммы не идёт, пока ты не подтвердишь
    # («оплачено» — другой картой) или не удалишь.
    confirmed = {r["id"] for r in con.execute("SELECT id FROM confirmed_purchases")}
    first_day = day(txs[0]["date"]) if txs else None
    last_day = day(max(t["date"] for t in txs)) if txs else None
    regs = [p for p in purchases if "без подтверждения" in (p["note"] or "")]
    doubts = []
    for p in regs:
        if p["id"] in used_p or p["id"] in confirmed:
            continue
        covered = bool(txs) and first_day <= day(p["date"]) <= last_day - dt.timedelta(days=5)
        twin = any(q is not p and q["merchant"] == p["merchant"] and abs(q["total"] - p["total"]) < 0.005
                   and 0 < (dt.datetime.fromisoformat(p["date"]) - dt.datetime.fromisoformat(q["date"])).total_seconds() <= 600
                   for q in regs)
        if covered or twin:
            doubts.append(p["id"])
    con.executemany("UPDATE purchases SET status = 'doubt' WHERE id = ?", [(i,) for i in doubts])

    # 2. операции без чека -> покупки «банк»; доходы и переводы — категории в bank_tx
    shops = categories.shop_rules(con)
    made, refunds = 0, []
    for t in txs:
        if t["id"] in used_t:
            continue
        text = rule_text(t)
        if t["amount"] > 0 and "RETURN" not in (t["type"] or ""):
            if t.get("category_source") == "manual":
                continue  # твоя правка категории поступления сохраняется
            cid = shops.get(categories.shop_key(t["counterparty"]))  # твоё правило «все переводы от этого человека»
            if cid is None:
                cid = K.get(rule_category(text, INCOME_RULES))
            con.execute("UPDATE bank_tx SET category_id = ?, category_source = 'rule' WHERE id = ?", (cid, t["id"]))
            continue
        name = t["counterparty"] or clean_desc(t["description"] or "")
        place = blik_place(t["description"]) if (t["type"] or "").startswith("MOBILE-PAYMENT-POS") else ""
        if t["type"] and t["type"].startswith("MOBILE-PAYMENT-POS"):
            name = f"BLIK: {place or t['description']}"
        if t["type"] in ("CARD-PAYMENT", "CARD-ATM"):
            name = clean_desc(t["description"] or "")
            if re.fullmatch(r"[\d\s]+", name):  # терминал без названия: «WROCLAW028PL» -> «028»
                name = f"Карта: {name}"
        if t["counterparty"] and t["description"] and t["type"] and "C2C" not in t["type"]:
            name = f"{t['counterparty']}: {t['description']}"
        cid = K.get(rule_category(text, BANK_RULES))
        amount = -t["amount"]
        if t["amount"] > 0:  # возврат на карту: исходную покупку ищем, когда созданы все операции (может быть в тот же день)
            name = f"Возврат: {name}"
        method = {"CARD-PAYMENT": "card", "CARD-ATM": "card", "CARD-PAYMENT-RETURN": "card"}.get(
            t["type"], "blik" if (t["type"] or "").startswith("MOBILE-PAYMENT") else "transfer")
        pid = f"bank:{t['id']}"
        merchant = t["merchant"] or (t["counterparty"] or place or clean_desc(t["description"] or ""))[:40]
        if re.fullmatch(r"[\d\s]*", merchant):  # у BLIK и части терминалов вместо магазина — номер операции
            merchant = "BLIK без названия" if (t["type"] or "").startswith("MOBILE-PAYMENT") else "Карта без названия"
        save_purchase(con, {"id": pid, "source": "bank", "date": t["date"] + "T12:00:00",
                            "merchant": merchant,
                            "store": t["description"], "total": amount, "payment_method": method, "discount": 0},
                      [{"name": name[:120], "product_code": None, "qty": 1, "unit_price": amount, "amount": amount,
                        "discount": None}])
        src = "bank" if cid else None
        if pid in manual_bank:
            cid, src = manual_bank[pid], "manual"
        con.execute("UPDATE items SET category_id = ?, category_source = ? WHERE purchase_id = ?", (cid, src, pid))
        con.execute("UPDATE purchases SET bank_tx_id = ? WHERE id = ?", (t["id"], pid))
        if t["amount"] > 0:
            refunds.append((pid, t))
        made += 1
    for pid, t in refunds:  # категорию даст исходная покупка (см. categorize)
        if orig := refund_origin(con, t, con.execute("SELECT merchant FROM purchases WHERE id = ?", (pid,)).fetchone()[0]):
            con.execute("UPDATE items SET name = ? WHERE purchase_id = ?", (f"Возврат: {orig['item']}"[:120], pid))
            # магазин — как у исходной покупки: фильтр по магазину показывает сумму за вычетом возврата
            con.execute("UPDATE purchases SET refund_of = ?, merchant = (SELECT merchant FROM purchases WHERE id = ?) "
                        "WHERE id = ?", (orig["id"], orig["id"], pid))

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

    migrate_rules_v2(con)
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
        print(f"Операций банка без чека: {made} (из них возвратов: {len(refunds)})")
        if doubts:
            print(f"Под вопросом (регистрация платежа без оплаты в банке): {len(doubts)}")
    return matched, made
