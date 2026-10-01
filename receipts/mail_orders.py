"""Письма -> покупки. Allegro и Koleo — свои разборщики, остальные магазины — общий, платёжные посредники
(Tpay/PayU/Przelewy24/Autopay) — только если у магазина нет своего письма с той же суммой.

  python budget.py mail parse      разобрать скачанные письма в покупки (повторный запуск пересобирает)
"""
import datetime as dt
import json
import re
from collections import defaultdict
from itertools import combinations

import requests

from core.common import DATA, money, num
from core.db import connect, save_purchase
from receipts import mail_parse as MP
from receipts.mail import db as mail_db

AMT = r"(-?\d{1,3}(?:[ \u00a0]\d{3})*(?:[.,]\d{2})|-?\d+[.,]\d{2})"
PLN_RX = re.compile(AMT + r"\s*(?:zł|PLN|zl)\b", re.I)
EUR_RX = re.compile(r"€\s?(\d+[.,]\d{2})|(\d+[.,]\d{2})\s?(?:€|EUR)")
NBP_CACHE = DATA / "mail" / "nbp_rates.json"


def amt(s: str):
    m = PLN_RX.search(s or "")
    if not m:
        return None
    v = m.group(1).replace(" ", "").replace("\u00a0", "")
    return num(v)


def find_after(lines, label_rx, span=4):
    """Первая сумма в span строках после строки, совпавшей с label_rx (или в ней самой)."""
    rx = re.compile(label_rx, re.I)
    for i, ln in enumerate(lines):
        if rx.search(ln):
            for j in range(i, min(i + span + 1, len(lines))):
                if (a := amt(lines[j])) is not None:
                    return a
    return None


# ---------------------------------------------------------------- Allegro

def parse_allegro(lines: list[str]) -> dict | None:
    """«Kupiłeś i zapłaciłeś»: блоки «od <продавец>» -> товары «название / (номер оферты) / цена» -> доставка."""
    if not any(ln.startswith("Numer płatności") for ln in lines):
        return None
    items, n = [], 0
    while n < len(lines):
        ln = lines[n]
        if re.fullmatch(r"\(\d{9,}\)", ln) and n >= 1:      # строка номера оферты: перед ней название, после — цена
            name = lines[n - 1]
            qty, price = 1.0, None
            for k in range(n + 1, min(n + 4, len(lines))):
                if m := re.match(r"(\d+)\s*(?:szt\.?|x|×)", lines[k], re.I):
                    qty = float(m.group(1))
                if (a := amt(lines[k])) is not None:
                    price = a
                    break
            items.append({"name": name, "product_code": ln.strip("()"), "qty": qty,
                          "unit_price": round(price / qty, 2) if price else None, "amount": price, "discount": None})
        elif re.match(r"^(Usługa|Opłata|Ubezpieczenie|Pakiet)\b", ln) and n + 1 < len(lines) and amt(lines[n + 1]):
            fee = amt(lines[n + 1])  # «Usługa Allegro Smart! 12 miesięcy 39,90 zł» — куплено вместе с заказом
            items.append({"name": ln, "product_code": None, "qty": 1, "unit_price": fee, "amount": fee, "discount": None})
        elif re.match(r"^(Kupon|Rabat|Obniżka|Monety)", ln) and n + 1 < len(lines) and (amt(lines[n + 1]) or 0) < 0:
            disc = abs(amt(lines[n + 1]))  # «Kupon za Smart! Monety ... -4,00 zł» — скидка на товары продавца выше
            target = next((i for i in reversed(items) if not i["name"].startswith("Dostawa")), None)
            if target:
                target["discount"] = round((target["discount"] or 0) + disc, 2)
        elif ln == "Metoda dostawy":
            cost = next((amt(lines[k]) for k in range(n + 1, min(n + 4, len(lines))) if amt(lines[k]) is not None), None)
            method = lines[n + 1].rstrip(",") if n + 1 < len(lines) else ""
            if cost:
                items.append({"name": f"Dostawa: {method}", "product_code": None, "qty": 1, "unit_price": cost,
                              "amount": cost, "discount": None})
        n += 1
    total = find_after(lines, r"^Płatność$", 2)
    method_line = next((lines[i + 1] for i, ln in enumerate(lines) if ln == "Metoda płatności" and i + 1 < len(lines)), "")
    paid_at = next((MP.polish_date(ln) for ln in lines if ln.startswith("przekazana")), None)
    order = next((lines[i + 1] for i, ln in enumerate(lines) if ln == "Numer płatności" and i + 1 < len(lines)), None)
    sellers = [lines[i][3:] for i, ln in enumerate(lines) if ln.startswith("od ") and len(ln) < 40]
    return {"order": order, "items": items, "total": total, "payment": MP.payment_method(method_line),
            "payment_label": method_line, "date": paid_at, "store": ", ".join(dict.fromkeys(sellers))}


# ---------------------------------------------------------------- Koleo

def parse_koleo(lines, msg) -> dict | None:
    head = next((ln for ln in lines if ln.startswith("Twój bilet ze stacji")), None)
    if not head:
        return None
    m = re.search(r"ze stacji (.+?) do stacji (.+?) w dniu (\d{2}-\d{2}-\d{4})", head)
    route = f"Bilet {m.group(1)} – {m.group(2)} ({m.group(3)})" if m else head
    price = payment = None
    for _, pdf in MP.pdfs(msg):
        import pymupdf
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            t = "\n".join(p.get_text() for p in doc)
        price = find_after(t.splitlines(), r"(razem|cena|do zapłaty|suma|należność|opłata)", 3) or amt(t)
        # в билете: «Płatność kartą» / «Płatność przelewem» (BLIK и PayU у Koleo — «przelew»)
        payment = "card" if re.search(r"P[lł]atno[sś][cć] kart", t) else "online" if re.search(r"P[lł]atno[sś][cć] przelew", t) else None
        if price:
            break
    order = re.search(r"(GO\d+|\d{6,})", " ".join(n for n, _ in MP.pdfs(msg)) or "")
    return {"order": order.group(1) if order else route, "items": [
        {"name": route, "product_code": None, "qty": 1, "unit_price": price, "amount": price, "discount": None}],
        "total": price, "payment": payment, "store": "KOLEO"}


# ---------------------------------------------------------------- квитанции в стиле Stripe (Anthropic и т.п.)

def parse_stripe(lines) -> dict | None:
    if not any(re.match(r"Receipt (from|#)", ln) for ln in lines):
        return None
    total_line = next((lines[i + 1] for i, ln in enumerate(lines) if ln in ("Amount paid", "Total") and i + 1 < len(lines)), "")
    m = EUR_RX.search(total_line) or EUR_RX.search(" ".join(lines[:6]))
    if not m:
        return None
    total = num(m.group(1) or m.group(2))
    product = next((lines[i - 1] for i, ln in enumerate(lines) if re.match(r"Qty \d", ln)), "Subscription")
    order = next((lines[i + 1] for i, ln in enumerate(lines) if ln == "Receipt number" and i + 1 < len(lines)), None)
    card = next((re.sub(r"\D", "", lines[i + 1]) for i, ln in enumerate(lines) if ln == "Payment method" and i + 1 < len(lines)), None)
    paid = next((ln for ln in lines if ln.startswith("Paid ")), "")
    try:
        date = dt.datetime.strptime(paid[5:].strip(), "%B %d, %Y").date().isoformat() + "T12:00:00"
    except ValueError:
        date = None
    return {"order": order, "items": [{"name": product, "product_code": None, "qty": 1, "unit_price": total,
                                       "amount": total, "discount": None}],
            "total": total, "currency": "EUR", "payment": "card", "card": card or None, "date": date}


# ---------------------------------------------------------------- общий разбор магазинов

ORDER_RX = re.compile(r"(?:zam[oó]wieni[aeu]|order|nr|numer|numerze)\s*(?:nr\.?|no\.?|#|:)?\s*:?\s*([A-Z]{0,5}[-#]?\d[\w\-/]{3,})", re.I)
TOTAL_LABELS = r"^(łącznie|do zapłaty|razem|suma zamówienia|wartość zamówienia|kwota zamówienia|total|amount paid|kwota)\b"
FINAL_LABELS = r"^(łącznie|do zapłaty|razem do zapłaty|amount paid)\b"  # итог к оплате важнее «стоимости заказа»
FEE_LABELS = r"^(koszt (obsługi )?płatności|opłata za (płatność|pobranie)|koszt pobrania)\s*:?$"
DELIVERY_LABELS = r"(koszt[y]? (dostawy|transportu|wysyłki)|dostawa|wysyłka)\s*:?$"


def find_order(lines, subject) -> str | None:
    # номер бывает на следующей строке: «…zamówienie o numerze» / «0123456789»
    pairs = [f"{a} {b}" for a, b in zip(lines[:60], lines[1:61])]
    for src in [subject] + lines[:60] + pairs:
        if m := ORDER_RX.search(src):
            return m.group(1).strip("#")
    return None


def pay_after_label(lines) -> tuple[str | None, str]:
    """«Forma płatności:» и способ — в той же строке или в нескольких следующих (в счетах-фактурах — таблица)."""
    for i, ln in enumerate(lines):
        if re.match(r"^(sposób|metoda|forma) (płatności|zapłaty)", ln, re.I):
            for cand in [ln] + lines[i + 1:i + 6]:
                if m := MP.payment_method(cand):
                    return m, cand
    return None, ""


def pdf_payment(msg) -> str | None:
    """Способ оплаты из приложенного счёта/чека (PDF): «Forma płatności … Gotówka»."""
    import pymupdf
    for name, data in MP.pdfs(msg):
        if re.search(r"regulamin|warunki|instrukcj", name or "", re.I):
            continue
        try:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                text = "\n".join(p.get_text() for p in doc)
        except Exception:  # noqa: BLE001 — битый или зашифрованный PDF: просто без подсказки
            continue
        pay, _ = pay_after_label([ln.strip() for ln in text.splitlines() if ln.strip()])
        if pay:
            return pay
    return None


def parse_generic(lines, subject) -> dict | None:
    order = find_order(lines, subject)
    items, n = [], 0
    while n < len(lines):  # «Название» + «1 x 15.49 zł»  /  «Ilość : 1x» + «Cena za sztukę : 1199.00 zł»
        ln = lines[n]
        m = re.match(r"^(\d+(?:[.,]\d+)?)\s*x\s*" + AMT + r"\s*(?:zł|PLN)", ln, re.I)
        if m and n >= 1:
            qty, price = num(m.group(1)), num(m.group(2).replace(" ", ""))
            items.append({"name": lines[n - 1], "product_code": None, "qty": qty, "unit_price": price,
                          "amount": round(qty * price, 2), "discount": None})
        elif re.match(r"^Ilość\s*:\s*(\d+)", ln) and n >= 3:
            qty = num(re.match(r"^Ilość\s*:\s*(\d+)", ln).group(1))
            price = find_after(lines[n:n + 4], r"Cena za sztukę", 2)
            name = next((lines[k] for k in range(n - 1, max(n - 6, -1), -1)
                         if not re.match(r"^(Kod produktu|Cena|Ilość|\d+)\b", lines[k]) and len(lines[k]) > 6), lines[n - 3])
            if price:
                items.append({"name": name, "product_code": None, "qty": qty, "unit_price": price,
                              "amount": round(qty * price, 2), "discount": None})
        n += 1
    total = find_after(lines, FINAL_LABELS, 2) or find_after(lines, TOTAL_LABELS, 2)
    # доставка и плата за способ оплаты (наложенный платёж и т.п.) — отдельными позициями, если с ними сходится итог
    extras = [(name, v) for name, rx in (("Dostawa", DELIVERY_LABELS), ("Koszt płatności", FEE_LABELS))
              if (v := find_after(lines, rx, 2))]
    if items and total:
        base = sum(i["amount"] for i in items)
        fit = next((c for k in range(len(extras), 0, -1) for c in combinations(extras, k)
                    if abs(base + sum(v for _, v in c) - total) < 0.05), ())
        items += [{"name": n, "product_code": None, "qty": 1, "unit_price": v, "amount": v, "discount": None} for n, v in fit]
    payment, pay_line = pay_after_label(lines)
    if total is None and not items:
        return None
    return {"order": order, "items": items, "total": total, "payment": payment, "payment_label": pay_line}


# ---------------------------------------------------------------- платёжные посредники

PAYEES = [(r"city-?nav|jakdojade", "Jakdojade"), (r"astarium|koleo", "KOLEO"), (r"erecept", "Erecept"),
          (r"grupa olx|\bolx\b", "OLX"), (r"apo-discounter", "Apo-Discounter"),
          (r"allegro", "Allegro"), (r"doz\b|doz\.pl", "DOZ.pl"), (r"modivo", "Modivo"), (r"zalando", "Zalando"),
          (r"medicover", "Medicover")]
CONFIRMED_RX = re.compile(r"potwierdzeni|confirmation|zaksięgowan|zaksiegowan|przekazaliśmy|completed|zrealizowan", re.I)


def parse_payment(lines, subject) -> dict | None:
    text = "\n".join(lines)
    total = find_after(lines, r"(kwota transakcji|transaction amount|kwota|amount|wartość)", 2)
    if total is None:
        return None
    # что оплачено: «for jakdojade.pl - UM <город> - 30-minutowy at City-nav» / «za KOLEO bilety kolejowe PID:.. w ASTARIUM»
    what = re.search(r"(?:\bfor\b|\bza\b)\s+(.+?)\s+(?:\bat\b|\bw\b:?)\s+(.+?)$", subject)
    payee = (what.group(2) if what else None) or next(
        (lines[i + 2] if lines[i + 1] == ":" else lines[i + 1] for i, ln in enumerate(lines[:-2])
         if re.match(r"^(recipient|odbiorca|sprzedawca|merchant)\b", ln, re.I)), None)
    if not payee and (m := re.search(r"\bdla\s+(.+?)(?:\s+\(|\.|$)", text, re.M)):
        payee = m.group(1)
    desc = re.sub(r"\s*PID:\d+", "", what.group(1)) if what else None
    if not desc and (m := re.search(r"Opis zamówienia.*\n:?\n?(.+)", text)):
        desc = m.group(1)
    payee_l = (payee or "") + " " + text[:3000]
    merchant = next((name for rx, name in PAYEES if re.search(rx, payee_l, re.I)), (payee or "").strip(" .")[:60] or None)
    ref = re.search(r"(TR-[\w-]+|P24-[\w-]+|\(\d{8,}\)|\d{10,})", subject + " " + text[:2000])
    return {"order": ref.group(1).split("/")[0].strip("()") if ref else None, "total": total, "payment": "online",
            "merchant": merchant, "confirmed": bool(CONFIRMED_RX.search(subject + " " + text[:600])),
            "items": [{"name": (desc or f"Оплата: {merchant}")[:120], "product_code": None, "qty": 1,
                       "unit_price": total, "amount": total, "discount": None}]}


# ---------------------------------------------------------------- курсы NBP для покупок в валюте

def nbp_rate(currency: str, date: str) -> float | None:
    cache = json.loads(NBP_CACHE.read_text()) if NBP_CACHE.exists() else {}
    key = f"{currency}:{date[:10]}"
    if key in cache:
        return cache[key]
    day = dt.date.fromisoformat(date[:10])
    for back in range(1, 8):  # курс с предыдущего рабочего дня (как для налогов)
        d = (day - dt.timedelta(days=back)).isoformat()
        r = requests.get(f"https://api.nbp.pl/api/exchangerates/rates/a/{currency.lower()}/{d}/?format=json", timeout=20)
        if r.ok:
            cache[key] = r.json()["rates"][0]["mid"]
            NBP_CACHE.parent.mkdir(parents=True, exist_ok=True)
            NBP_CACHE.write_text(json.dumps(cache))
            return cache[key]
    return None


# ---------------------------------------------------------------- сборка

MERCHANT_NAMES = {"allegro.pl": "Allegro", "allegromail.pl": "Allegro", "allegrolokalnie.pl": "Allegro Lokalnie",
                  "koleo.pl": "KOLEO", "doz.pl": "DOZ.pl", "mediaexpert.pl": "Media Expert", "apo-discounter.pl": "Apo-Discounter",
                  "zalando.pl": "Zalando", "modivo.pl": "Modivo", "eobuwie.pl": "eobuwie", "gdziepolek.pl": "GdziePoLek",
                  "olx.pl": "OLX", "ebay.com": "eBay", "synevo.pl": "Synevo", "apteline.pl": "Apteline", "temu.com": "Temu",
                  "erecept.pl": "Erecept", "anthropic.com": "Anthropic", "orange.com": "Orange Flex",
                  "amazon.pl": "Amazon", "empik.com": "Empik", "euro.com.pl": "RTV Euro AGD", "x-kom.pl": "x-kom",
                  "aliexpress.com": "AliExpress", "pyszne.pl": "Pyszne.pl"}
REFUND_RX = re.compile(r"zwrot|refund|anulowan|cancel", re.I)


def parse_all(verbose=True):
    store = mail_db()
    rows = store.execute("""SELECT e.uid, e.date, e.domain, e.subject, e.path, s.status FROM emails e
                            JOIN mail_senders s ON s.domain = e.domain
                            WHERE e.path IS NOT NULL AND s.status IN ('shop', 'sub', 'pay') ORDER BY e.date""").fetchall()
    orders: dict[tuple, dict] = {}
    pay_hints: dict[tuple, str] = {}  # (магазин, заказ) -> способ оплаты из любого письма или счёта-фактуры этого заказа
    payments = []
    stats = defaultdict(lambda: [0, 0])
    for r in rows:
        msg = MP.load(r["path"])
        lines = MP.text(msg).splitlines()
        subject = r["subject"] or ""
        dom = r["domain"]
        if r["status"] == "pay":
            if (p := parse_payment(lines, subject)):
                payments.append(p | {"date": r["date"], "path": r["path"], "domain": dom})
            continue
        if REFUND_RX.search(subject) and dom != "allegro.pl":
            continue  # письма о возвратах — отдельная задача (учёт отрицательных сумм), пока пропускаем
        p = None
        if dom == "allegro.pl" and subject.startswith("Kupiłeś i zapłaciłeś"):
            p = parse_allegro(lines)
        elif dom == "allegro.pl":
            continue  # уведомления о доставке, оценках, ценах
        elif dom == "koleo.pl":
            p = parse_koleo(lines, msg)
        else:
            p = parse_stripe(lines) or parse_generic(lines, subject)
            # письма без итога тоже полезны: «Dokument sprzedaży» с фактурой, «Twoje zamówienie» с формой оплаты
            if (order := (p or {}).get("order") or find_order(lines, subject)) and \
                    (pay := (p or {}).get("payment") or pay_after_label(lines)[0] or pdf_payment(msg)):
                pay_hints.setdefault((dom, order), pay)
        stats[dom][0] += 1
        if not p or p.get("total") is None and not p.get("items"):
            continue
        stats[dom][1] += 1
        key = (dom, p.get("order") or r["uid"])
        prev = orders.get(key)
        p |= {"domain": dom, "path": r["path"], "first_date": (prev or {}).get("first_date", r["date"]),
              "date": p.get("date") or (prev or {}).get("date") or r["date"]}
        # из нескольких писем одного заказа оставляем самое полное (с позициями и итогом)
        score = lambda o: (o.get("total") is not None) * 2 + len(o.get("items") or [])
        if prev is None or score(p) >= score(prev):
            orders[key] = p | {"payment": p.get("payment") or (prev or {}).get("payment")}
        elif not prev.get("payment") and p.get("payment"):
            prev["payment"] = p["payment"]

    # одни и те же заказы в письмах «принят / собран / выдан» с разными номерами (DOZ: Z…/R…):
    # один магазин + одна сумма + даты в пределах недели -> один заказ (оставляем самое полное письмо)
    score = lambda o: (o.get("total") is not None) * 2 + sum(1 for i in o.get("items") or []
                                                            if not i["name"].startswith("Заказ")) + (o.get("payment") is not None)
    merged: list[tuple] = []
    for key, o in sorted(orders.items(), key=lambda kv: kv[1]["date"]):
        total = o.get("total")
        reliable_ids = key[0] in ("koleo.pl", "allegro.pl", "anthropic.com")  # одинаковые билеты каждый день — не дубли
        twin = None if reliable_ids else next((k for k in merged if k[0][0] == key[0] and total is not None
                                               and k[1].get("total") == total
                     and (dt.datetime.fromisoformat(o["date"][:19]) - dt.datetime.fromisoformat(k[1]["date"][:19])).days <= 7),
                    None)
        if twin is None:
            merged.append((key, o))
        elif score(o) > score(twin[1]):
            merged[merged.index(twin)] = (twin[0], o | {"date": twin[1]["date"]})
    orders = dict(merged)

    for key, o in orders.items():
        o["payment"] = o.get("payment") or pay_hints.get(key)

    con = connect()
    con.execute("DELETE FROM items WHERE purchase_id LIKE 'email:%'")
    con.execute("DELETE FROM payments WHERE purchase_id LIKE 'email:%'")
    con.execute("DELETE FROM purchases WHERE source = 'email'")
    saved, warn = 0, 0
    for (dom, order), o in orders.items():
        items = o.get("items") or []
        total = o.get("total")
        if total is None and items:
            total = round(sum(i["amount"] or 0 for i in items), 2)
        if not items and total is not None:
            items = [{"name": f"Заказ {MERCHANT_NAMES.get(dom, dom)} {order}", "product_code": None, "qty": 1,
                      "unit_price": total, "amount": total, "discount": None}]
        note = None
        if o.get("currency") and o["currency"] != "PLN":
            rate = nbp_rate(o["currency"], o["date"])
            if rate:
                note = f"{o['currency']} {total:.2f} по курсу NBP {rate}"
                for i in items:
                    i["amount"] = round(i["amount"] * rate, 2)
                    i["unit_price"] = round((i["unit_price"] or 0) * rate, 2)
                total = round(total * rate, 2)
        s = sum((i["amount"] or 0) - (i["discount"] or 0) for i in items)
        if total is not None and abs(s - total) > 0.05:
            warn += 1
            note = (note + "; " if note else "") + f"позиции {s:.2f} ≠ итог {total:.2f}"
        pid = f"email:{dom}:{order}"
        save_purchase(con, {"id": pid, "source": "email", "date": o["date"][:19], "merchant": MERCHANT_NAMES.get(dom, dom),
                            "store": o.get("store"), "total": total, "payment_method": o.get("payment"),
                            "card_last4": o.get("card"), "raw_path": o["path"]}, items,
                      [{"method": o.get("payment"), "amount": total, "card_last4": o.get("card")}] if o.get("payment") else [])
        con.execute("UPDATE purchases SET note = ? WHERE id = ?", (note, pid))
        saved += 1

    # платёжные посредники: письма одной транзакции склеиваем (подтверждение важнее регистрации),
    # и берём только то, чего нет у магазинов (сумма до копейки, ±1 день)
    by_ref = {}
    for p in payments:
        key = (p["domain"], p.get("order") or p["date"])
        prev = by_ref.get(key)
        if prev is None or (p["confirmed"], len(p["items"][0]["name"])) > (prev["confirmed"], len(prev["items"][0]["name"])):
            by_ref[key] = p | {"confirmed": p["confirmed"] or (prev or {}).get("confirmed", False)}
    have = [(r["total"], r["date"], r["merchant"]) for r in
            con.execute("SELECT total, date, merchant FROM purchases WHERE total IS NOT NULL")]
    extra = 0
    for (dom, ref), p in sorted(by_ref.items(), key=lambda kv: kv[1]["date"]):
        d = dt.datetime.fromisoformat(p["date"][:19])
        # та же сумма ±1 день — или тот же магазин ±10 дней (заказ оплачен переводом позже)
        if any(abs((t or 0) - p["total"]) < 0.01 and
               abs((dt.datetime.fromisoformat(dd[:19]) - d).days) <= (10 if m == p.get("merchant") else 1)
               for t, dd, m in have if dd):
            continue
        pid = f"email:{dom}:{ref}"
        save_purchase(con, {"id": pid, "source": "email", "date": p["date"][:19], "merchant": p.get("merchant") or dom,
                            "store": f"через {dom}", "total": p["total"], "payment_method": "online",
                            "raw_path": p["path"]}, p["items"], [{"method": "online", "amount": p["total"], "card_last4": None}])
        if not p["confirmed"]:
            con.execute("UPDATE purchases SET note = ? WHERE id = ?",
                        ("регистрация платежа без подтверждения — проверить по банку", pid))
        # оплаты посредников между собой не сравниваем: два билета по 1,60 zł — две покупки (они уже склеены по номеру)
        extra += 1
    con.commit()
    if verbose:
        print(f"{'отправитель':<22} {'писем':>6} {'разобрано':>9}")
        for dom, (n, ok) in sorted(stats.items(), key=lambda x: -x[1][0]):
            print(f"{dom:<22} {n:>6} {ok:>9}")
        print(f"\nПокупок из писем: {saved} (с расхождением позиций и итога: {warn}); "
              f"из платёжных посредников, которых нет у магазинов: {extra}")
