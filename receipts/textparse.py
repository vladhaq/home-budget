"""Разбор текста польского кассового чека (paragon fiskalny) — после OCR фото/скана или из PDF.

OCR ошибается по-разному на разных вариантах картинки, поэтому здесь два шага:
  parse_text()   — разбор одного текста в кандидаты;
  merge()        — голосование между несколькими разборами + проверка «сумма позиций = итог».
"""
import datetime as dt
import itertools
import re
from collections import Counter
from difflib import SequenceMatcher

from core.common import fold, num

AMOUNT = r"-?\d{1,5}[.,]\d{2}"
# в конце строки товара: сумма и налоговая буква (A-D); OCR часто читает «A» как «4» или «Ą»
LINE_END_RE = re.compile(rf"({AMOUNT})\s*([A-DĄ4]|\d)?\s*[.,]?\s*$")
QTY_RE = re.compile(r"(\d+(?:[.,]\d{1,3})?)\s*(?:szt\.?|kg|op\.?)?\s*[x×*]\s*(\d+(?:[.,]\d{1,2})?)(?!\d)")
CODE_RE = re.compile(r"^(\d{5,14})\s+")
DISCOUNT_RE = re.compile(rf"^(rabat|upust|opust|obni[zż]k|promoc|kupon|lidl\s*plus|taniej)\b.*?(-?{AMOUNT})", re.I)
TOTAL_RE = re.compile(rf"^(suma|do zap[lł]aty|razem)(?!\s*ptu)\W*(pln)?\W*({AMOUNT})", re.I)
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})|(\d{2})[.-](\d{2})[.-]?(\d{4})")
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
POSTAL_RE = re.compile(r"\b\d{2}-\d{3}\b")
SKIP_RE = re.compile(r"^(sprzeda[zż]|kwota|ptu|suma ptu|nip|paragon|niefiskaln|rozliczenie|reszta|wydano|nr |numer"
                     r"|informacja|wymian|dzi[eę]kuj|bdo|sp\.? ?z|do zap)", re.I)
PAYMENT_WORDS = [("blik", "blik"), ("got[oó]wk", "cash"), ("karta|karte|kart[aąy]|visa|mastercard|p[lł]atno[sś][cć] kart", "card"),
                 ("bon|voucher|kupon", "voucher")]
MERCHANTS = ["Action", "Biedronka", "Lidl", "Kaufland", "Żabka", "Rossmann", "Pepco", "Carrefour", "Auchan", "Netto",
             "Dino", "Stokrotka", "Hebe", "Aldi", "Lewiatan", "Polomarket", "Intermarche", "Tesco", "Castorama",
             "Leroy Merlin", "IKEA", "Media Expert", "RTV Euro AGD", "Decathlon", "Sinsay", "Super-Pharm",
             "McDonald's", "KFC", "Orlen", "Circle K", "Shell", "BP", "Jysk", "TEDi", "KiK", "Empik", "Smyk",
             "Mix Markt", "Top Market", "Groszek", "Społem", "Freshmarket", "Delikatesy Centrum", "Aptek"]
# Юрлица -> сеть (когда логотип не распознан)
LEGAL_NAMES = {"jeronimo martins": "Biedronka", "zabka polska": "Żabka", "action poland": "Action",
               "kaufland polska": "Kaufland", "lidl sp": "Lidl", "rossmann supermarkety": "Rossmann"}


def parse_text(text: str) -> dict:
    lines = [re.sub(r"\s+", " ", ln).strip(" |") for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    items, totals, dates, payments, pending = [], [], [], [], None
    # товары — между «PARAGON FISKALNY» (если распознан) и «Sprzedaż opodatkowana»/«SUMA»
    start = next((n for n, ln in enumerate(lines) if "paragon" in fold(ln) and "fiskal" in fold(ln)), -1)
    in_items = start < 0
    for n, ln in enumerate(lines):
        low = fold(ln)
        if n == start:
            in_items = True
            continue
        if in_items and re.match(r"(sprzeda[zż]|suma|razem|ptu|kwota ptu)", low):
            in_items = False
        for m in DATE_RE.finditer(ln):
            dates.append(_date(m, ln))
        if m := TOTAL_RE.match(low.replace(" ", " ")):
            totals.append(num(m.group(3)))
            continue
        if m := DISCOUNT_RE.match(low):
            if items:
                items[-1]["discount"] = round((items[-1]["discount"] or 0) + abs(num(m.group(2))), 2)
            continue
        paid = next((method for rx, method in PAYMENT_WORDS if re.match(rx, low)), None)
        if paid:  # «Karta: 16.56» — строка оплаты, не товар
            if re.search(AMOUNT, ln) or ":" in ln:
                payments.append(paid)
            pending = None
            continue
        if SKIP_RE.match(low) or not in_items:
            pending = None
            continue
        item = parse_item_line(ln, pending)
        if item:
            items.append(item)
            pending = None
        elif re.search(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{3}", ln) and not re.search(r"\d[.,]\d{2}", ln):
            pending = ln  # название на одной строке, «1 x 3,99 3,99A» — на следующей
    return {"items": items, "totals": [t for t in totals if t], "dates": [d for d in dates if d],
            "payments": payments, "merchant": detect_merchant(text), "store": detect_store(lines)}


def parse_item_line(ln: str, pending: str | None = None) -> dict | None:
    end = LINE_END_RE.search(ln)
    if not end:
        return None
    amount = num(end.group(1))
    head = ln[:end.start()].strip()
    if not re.search(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{2}", head):
        if pending and (q := QTY_RE.search(head)):   # строка «1 x 3,99 3,99A» после строки-названия
            head = pending + " " + head
        else:
            return None
    code = None
    if m := CODE_RE.match(head):
        code, head = m.group(1), head[m.end():]
    qty = unit = None
    if q := QTY_RE.search(head):
        qty, unit = num(q.group(1)), num(q.group(2))
        name = head[:q.start()].strip()
    else:
        # «1410.77» — OCR прочитал «1*10.77» как «1410.77»: хвост совпадает с суммой
        name = head
        tok = re.search(r"(\d+)[.,]?(\d{2})\s*$", head)
        if tok and amount and (tok.group(1) + tok.group(2)).endswith(f"{amount:.2f}".replace(".", "")):
            name = head[:tok.start()].strip()
            qty, unit = 1.0, amount
    # хвост от испорченного «1*5.79»: «... kolor023 195 79___» -> «... kolor023»
    name = re.sub(r"(\s+[\d.,*x×_/|\\-]+)+$", "", name)
    name = re.sub(r"\s+[A-D]$", "", name)  # налоговая буква перед «2 x3,49»
    if not name or len(name) < 3:
        return None
    return {"name": name.strip(" .,-"), "product_code": code, "qty": qty, "unit_price": unit,
            "amount": amount, "discount": None}


def _date(m, ln) -> str | None:
    g = m.groups()
    y, mo, d = (g[0], g[1], g[2]) if g[0] else (g[5], g[4], g[3])
    try:
        day = dt.date(int(y), int(mo), int(d))
    except ValueError:
        return None
    t = TIME_RE.search(ln[m.end():m.end() + 12])
    return day.isoformat() + (f"T{int(t.group(1)):02d}:{t.group(2)}:00" if t else "")


def detect_merchant(text: str) -> str | None:
    f = fold(text)
    for legal, chain in LEGAL_NAMES.items():
        if legal in f:
            return chain
    for name in MERCHANTS:
        if re.search(r"\b" + re.escape(fold(name)), f):
            return name
    return None


def detect_store(lines: list[str]) -> str | None:
    """Адрес точки: последняя строка с почтовым индексом до NIP (первая обычно — адрес головного офиса)."""
    cand = []
    for ln in lines:
        if fold(ln).startswith("nip"):
            break
        if POSTAL_RE.search(ln):
            cand.append(ln)
    return cand[-1] if cand else None


# ---------------------------------------------------------------- голосование между вариантами OCR

def _key(it: dict) -> str:
    return it["product_code"] or fold(it["name"])[:10]


def merge(runs: list[dict], hint_date: str | None = None) -> dict:
    """Собрать один чек из нескольких разборов. status: ok / derived (одна сумма вычислена из итога) / check."""
    totals = Counter(t for r in runs for t in r["totals"])
    total = totals.most_common(1)[0][0] if totals else None

    base = max(runs, key=lambda r: (len(r["items"]), sum(1 for i in r["items"] if i["product_code"])))
    keys = [_key(i) for i in base["items"]]
    cands = {k: Counter() for k in keys}
    info = {k: [] for k in keys}
    for r in runs:
        for it in r["items"]:
            k = _key(it)
            if k not in cands:  # тот же товар без кода или с искажённым кодом — ищем по названию
                k = next((kk for kk in keys if fold(it["name"])[:8] and fold(it["name"])[:8] in kk + fold(
                    next(i["name"] for i in base["items"] if _key(i) == kk))), None)
                if k is None:
                    continue
            info[k].append(it)
            if it["amount"] is not None:
                cands[k][it["amount"]] += 1
                if it["qty"] and it["unit_price"] and abs(it["qty"] * it["unit_price"] - it["amount"]) < 0.02:
                    cands[k][it["amount"]] += 1  # сумма подтверждена «кол-во × цена»

    items = []
    for k in keys:
        its = info[k] or [next(i for i in base["items"] if _key(i) == k)]
        names = [i["name"] for i in its]
        # «медоида»: вариант, в среднем самый похожий на остальные — случайные искажения OCR гасятся
        name = max(set(names), key=lambda a: sum(SequenceMatcher(None, a, b).ratio() for b in names))
        code = Counter(i["product_code"] for i in its if i["product_code"]).most_common(1)
        disc = Counter(i["discount"] for i in its if i["discount"]).most_common(1)
        items.append({"name": name, "product_code": code[0][0] if code else None, "qty": None, "unit_price": None,
                      "amount": None, "discount": disc[0][0] if disc else None, "_key": k})

    status = "check"
    options = [[a for a, _ in cands[i["_key"]].most_common(4)] or [None] for i in items]
    best = None
    if total is not None and len(items) <= 12:
        for combo in itertools.product(*options):
            if None in combo:
                continue
            s = sum(combo) - sum(i["discount"] or 0 for i in items)
            if abs(s - total) < 0.015:
                score = sum(cands[i["_key"]][a] for i, a in zip(items, combo))
                if best is None or score > best[0]:
                    best = (score, combo)
    if best:
        chosen, status = list(best[1]), "ok"
    else:
        chosen = [o[0] for o in options]
        missing = [n for n, a in enumerate(chosen) if a is None]
        if total is not None and len(missing) <= 1:
            # одна позиция не прочиталась (или прочиталась неверно) — вычисляем из итога
            fix = missing[0] if missing else min(range(len(items)), key=lambda n: sum(cands[items[n]["_key"]].values()))
            rest = sum(a for n, a in enumerate(chosen) if n != fix and a is not None)
            chosen[fix] = round(total + sum(i["discount"] or 0 for i in items) - rest, 2)
            items[fix]["approx"] = True
            status = "derived" if chosen[fix] > 0 else "check"
    for it, a in zip(items, chosen):
        it["amount"] = a
        same = [i for i in info[it["_key"]] if a is not None and i["amount"] == a and i["qty"] and i["unit_price"]
                and abs(i["qty"] * i["unit_price"] - a) < 0.02]
        if same:
            it["qty"], it["unit_price"] = same[0]["qty"], same[0]["unit_price"]
        elif a is not None:
            it["qty"], it["unit_price"] = 1.0, a
        it.pop("_key")

    return {"items": items, "total": total, "status": status,
            "date": vote_date([d for r in runs for d in r["dates"]], hint_date),
            "payment": (Counter(p for r in runs for p in r["payments"]).most_common(1) or [(None,)])[0][0],
            "merchant": (Counter(r["merchant"] for r in runs if r["merchant"]).most_common(1) or [(None,)])[0][0],
            "store": (Counter(r["store"] for r in runs if r["store"]).most_common(1) or [(None,)])[0][0]}


def vote_date(dates: list[str], hint: str | None = None) -> str | None:
    """Самая частая правдоподобная дата; hint (дата сообщения в Telegram) отсекает невозможные: чек не из будущего
    и не старше 120 дней от момента отправки."""
    ok = []
    for d in dates:
        day = d[:10]
        if hint and not (dt.date.fromisoformat(hint[:10]) - dt.timedelta(days=120) <= dt.date.fromisoformat(day)
                         <= dt.date.fromisoformat(hint[:10])):
            continue
        if not hint and not (2020 <= int(day[:4]) <= dt.date.today().year):
            continue
        ok.append(d)
    if not ok:
        return hint[:19] if hint else None
    day = Counter(d[:10] for d in ok).most_common(1)[0][0]
    with_time = [d for d in ok if d.startswith(day) and len(d) > 10]
    return Counter(with_time).most_common(1)[0][0] if with_time else day
