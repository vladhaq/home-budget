"""Выписка из файла: CSV из интернет-банка, MT940 или camt.053 (XML) -> операции счёта, как из Enable Banking.

  python budget.py bank import [файлы]   добавить операции; без файлов — всё из bank/inbox/
                                         (разобранные файлы переносятся в bank/inbox/обработано)

Форматы: camt.053 (ISO 20022 — любой банк), MT940 (поле :86: в польском виде «~20…» и немецком «?20…»),
CSV — колонки узнаются по заголовку (PKO, mBank, Millennium, ING, Revolut, немецкие банки…), у файла без
заголовка — по содержимому (даты, суммы, текст). Тип операции переводится в коды PKO (CARD-PAYMENT,
TRANSFER-IN…): на них опираются сверка, наличные и аналитика.
Повторы не добавляются: операция того же счёта на ту же сумму ±3 дня, уже пришедшая из банка или прошлого
файла, — та же самая.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from core.common import BUDGET, fold
from core.db import connect, set_meta

INBOX = BUDGET / "bank" / "inbox"
DONE = INBOX / "обработано"
EXTS = {".csv", ".txt", ".xml", ".sta", ".940", ".mt940", ".camt"}
DUP_DAYS = 3  # та же сумма в пределах стольких дней — та же операция (банк и файл ставят разные даты)


@dataclass
class Tx:
    date: str                     # дата списания (YYYY-MM-DD)
    amount: float                 # минус — списание
    description: str = ""
    counterparty: str | None = None
    type: str | None = None       # код как у PKO: CARD-PAYMENT, TRANSFER-IN…
    balance: float | None = None  # остаток после операции
    raw: dict = field(default_factory=dict)


@dataclass
class Statement:
    format: str
    rows: list[Tx]
    iban: str | None = None
    currency: str | None = None


# ---------------------------------------------------------------- числа, даты, тип операции

def amount_of(s) -> float | None:
    """«-3 283,33 PLN», «1.234,56», «1,234.56», «(12.50)», «−7,99 zł» -> число; не сумма — None."""
    if s is None:
        return None
    s = re.sub(r"[^\d,.()+\-−']", "", str(s).replace(" ", " ").replace(" ", " "))
    neg = s.startswith(("-", "−")) or (s.startswith("(") and s.endswith(")"))
    s = s.strip("+-−()").replace("'", "")
    if not s or not re.fullmatch(r"[\d.,]+", s) or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:  # последний из знаков — дробная часть
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if len(s.rsplit(",", 1)[1]) in (1, 2) and s.count(",") == 1 else s.replace(",", "")
    elif s.count(".") > 1:  # 1.234.567
        s = s.replace(".", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y", "%Y.%m.%d", "%Y/%m/%d", "%d.%m.%y", "%Y%m%d")


def date_of(s) -> str | None:
    s = str(s or "").strip().strip("'\"")
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T]\d", s)
    if m:
        return m.group(1)
    s = s.split()[0] if s else s
    for f in DATE_FORMATS:
        try:
            d = dt.datetime.strptime(s, f).date()
        except ValueError:
            continue
        if 2000 <= d.year <= 2100:
            return d.isoformat()
    return None


# текст типа/описания -> код PKO (при списании, при поступлении); первое совпадение. Тексты — польские, английские,
# немецкие; Revolut пишет «CARD_PAYMENT» — подчёркивания заменяются пробелами. Последние два правила «слабые»:
# по описанию (не по колонке типа) они срабатывают только в начале текста — «Opłata za czynsz» в назначении перевода
# не комиссия банка
TYPE_RULES = [
    (r"zwrot.{0,30}kod\w* mobiln|zwrot.{0,15}blik", "MOBILE-PAYMENT-POS-RETURN", "MOBILE-PAYMENT-POS-RETURN"),
    (r"zwrot.{0,30}kart|card refund|refund.{0,20}card|karten.{0,10}gutschrift", "CARD-PAYMENT-RETURN", "CARD-PAYMENT-RETURN"),
    (r"wplatomat|wplata gotowk|wplata w kasie|wplata wlasna|cash deposit|bareinzahlung|einzahlung", "CASH-IN", "CASH-IN"),
    (r"bankomat|\batm\b|cash withdrawal|wyplata gotowk|geldautomat|bargeldbehebung|\bbehebung", "CARD-ATM", "CASH-IN-ATM"),
    (r"przelew na telefon|blik.{0,20}(telefon|p2p|transfer to mobile)|na numer telefonu", "MOBILE-PAYMENT-C2C",
     "MOBILE-PAYMENT-C2C"),
    (r"(platnosc|zakup).{0,20}(web|internet).{0,20}(kod\w* mobiln|blik)|kod\w* mobiln.{0,20}(web|internet)"
     r"|blik.{0,30}(e-commerce|internet|\bweb\b)",
     "MOBILE-PAYMENT-POS-NO-CARD-TX-CODE", "MOBILE-PAYMENT-POS-RETURN"),
    (r"kod\w* mobiln|\bblik\b", "MOBILE-PAYMENT-POS-TX-CODE", "MOBILE-PAYMENT-POS-RETURN"),
    (r"oplata.{0,30}kart|card fee|kartengebuhr", "CARD-FEE", "CARD-FEE"),
    (r"platnosc kart|transakcja kart|zakup kart|operacja kart|przy uzyciu kart|card payment|kartenzahlung|karteneinsatz"
     r"|\bpos\b|debit card",
     "CARD-PAYMENT", "CARD-PAYMENT-RETURN"),
    (r"urzad skarbow|przelew podatkow|mikrorachun|\bzus\b", "US-TRANSFER", "TRANSFER-IN"),
    (r"express elixir|przelew natychmiast", "TRANSFER-EXPRESS-ELIXIR", "TRANSFER-EXPRESS-ELIXIR-IN"),
    (r"odsetk|kapitalizac|interest|zinsen|habenzins", "INTEREST", "INTEREST"),
    (r"\boplata\b|prowizj|\bfee\b|gebuhr|entgelt|kontofuhrung|commission", "FEE", "FEE"),
]


WEAK = {"INTEREST", "FEE"}


def type_of(text: str, amount: float, strict=False) -> str:
    """strict — текст это описание, а не тип операции: «слабые» правила только в начале текста."""
    f = fold(text or "").replace("_", " ").strip()
    for rx, out, inc in TYPE_RULES:
        if re.match(rx, f) if strict and out in WEAK else re.search(rx, f):
            return out if amount < 0 else inc
    return "TRANSFER" if amount < 0 else "TRANSFER-IN"


def kind_of(type_text: str, desc: str, amount: float) -> str:
    """Тип по колонке/коду типа; если там просто «перевод» или ничего — по описанию (BLIK, карта, банкомат…)."""
    kind = type_of(type_text, amount) if (type_text or "").strip() else None
    if kind in (None, "TRANSFER", "TRANSFER-IN"):
        kind = type_of(desc, amount, strict=True)
    return kind


def clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("˙", " ")).strip(" ;,")


# ---------------------------------------------------------------- camt.053 (ISO 20022 XML)

def _local(e) -> str:
    return e.tag.rsplit("}", 1)[-1]


def _find(e, path: str):
    for part in path.split("/"):
        if e is None:
            return None
        e = next((c for c in e if _local(c) == part), None)
    return e


def _text(e, path: str) -> str | None:
    x = _find(e, path)
    return x.text.strip() if x is not None and x.text and x.text.strip() else None


def _all(e, name: str) -> list:
    return [x for x in e.iter() if _local(x) == name]


# коды ISO «домен/семейство/подсемейство» -> код PKO (при списании, при поступлении)
CAMT_CODES = [("CCRD/CWDL", "CARD-ATM", "CASH-IN-ATM"), ("CNTR/CWDL", "CARD-ATM", "CARD-ATM"),
              ("CNTR/CDPT", "CASH-IN", "CASH-IN"), ("CCRD/", "CARD-PAYMENT", "CARD-PAYMENT-RETURN"),
              ("ICDT/", "TRANSFER", "TRANSFER-IN"), ("RCDT/", "TRANSFER", "TRANSFER-IN"),
              ("/CHRG", "FEE", "FEE"), ("/FEES", "FEE", "FEE"), ("/COMM", "FEE", "FEE"), ("/INTR", "INTEREST", "INTEREST")]


def parse_camt(data: bytes) -> Statement | None:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    stmts = _all(root, "Stmt") or _all(root, "Rpt")
    if not stmts:
        return None
    rows, iban, ccy = [], None, None
    for st in stmts:
        iban = iban or _text(st, "Acct/Id/IBAN") or _text(st, "Acct/Id/Othr/Id")
        ccy = ccy or _text(st, "Acct/Ccy")
        opening = closing = None
        for b in (x for x in st if _local(x) == "Bal"):
            code = _text(b, "Tp/CdOrPrtry/Cd") or _text(b, "Tp/CdOrPrtry/Prtry")
            amt = amount_of(_text(b, "Amt"))
            if amt is None:
                continue
            amt = -amt if _text(b, "CdtDbtInd") == "DBIT" else amt
            if code in ("OPBD", "PRCD") and opening is None:
                opening = amt
            elif code in ("CLBD", "CLAV") and closing is None:
                closing = amt
        out = []
        for n in (x for x in st if _local(x) == "Ntry"):
            status = _text(n, "Sts") or _text(n, "Sts/Cd") or "BOOK"
            amt = amount_of(_text(n, "Amt"))
            if status != "BOOK" or amt is None:
                continue
            amt = -abs(amt) if _text(n, "CdtDbtInd") == "DBIT" else abs(amt)
            day = date_of(_text(n, "BookgDt/Dt") or _text(n, "BookgDt/DtTm") or _text(n, "ValDt/Dt") or "")
            if not day:
                continue
            tx = _find(n, "NtryDtls/TxDtls")
            side = "Cdtr" if amt < 0 else "Dbtr"
            who = None
            if tx is not None:
                who = _text(tx, f"RltdPties/{side}/Nm") or _text(tx, f"RltdPties/{side}/Pty/Nm")
            ustrd = [x.text.strip() for x in (tx if tx is not None else n).iter() if _local(x) == "Ustrd" and x.text]
            desc = clean(" ".join(ustrd) or _text(n, "AddtlNtryInf") or (tx is not None and _text(tx, "AddtlTxInf")) or "")
            code = "/".join(filter(None, [_text(n, "BkTxCd/Domn/Fmly/Cd"), _text(n, "BkTxCd/Domn/Fmly/SubFmlyCd")]))
            prtry = _text(n, "BkTxCd/Prtry/Cd") or ""
            kind = next((o if amt < 0 else i for c, o, i in CAMT_CODES if c in code + "/"), None)
            if kind in (None, "TRANSFER", "TRANSFER-IN"):  # по коду только «перевод» — уточняем по тексту (BLIK, налог…)
                kind = kind_of(f"{prtry} {_text(n, 'AddtlNtryInf') or ''}", desc, amt)
            out.append(Tx(day, amt, desc, clean(who) or None, kind,
                          raw={"code": code or prtry, "ref": _text(n, "AcctSvcrRef"), "value_date": _text(n, "ValDt/Dt")}))
        if opening is not None:  # остаток после каждой операции — от начального, если сходится с конечным
            bal = opening
            for t in out:
                bal = round(bal + t.amount, 2)
                t.balance = bal
            if closing is not None and abs(bal - closing) > 0.01:
                for t in out:
                    t.balance = None
        rows += out
    return Statement("camt.053", rows, iban, ccy)


# ---------------------------------------------------------------- MT940

def _mt940_fields(text: str) -> list[tuple[str, str]]:
    out = []
    for ln in text.splitlines():
        if m := re.match(r"^:(\d{2}[A-Z]?):(.*)$", ln):
            out.append([m.group(1), m.group(2)])
        elif out and ln.strip() not in ("-", "-}") and not ln.startswith("{"):
            out[-1][1] += "\n" + ln
    return [tuple(f) for f in out]


def _mt940_details(s: str) -> dict:
    """:86: -> {code, title, name, account, text}. Польский вид «020~00150~20ZAPŁATA~32NAZWA…» и немецкий
    «166?00GUTSCHRIFT?20…?32NAME»: подполя 20–29 и 60–63 — назначение, 32–33 — имя, 38/31 — счёт."""
    flat = s.replace("\n", "")
    m = re.match(r"^(\w{3})?([~?])\d{2}", flat)
    if not m:  # свободный текст или SWIFT-теги «/NAME/…/REMI/…»
        tags = dict(re.findall(r"/(NAME|REMI|EREF|IBAN|ORDP|BENM)/([^/]*)", flat))
        return {"title": clean(tags.get("REMI") or flat), "name": clean(tags.get("NAME")) or None, "text": flat}
    sep = m.group(2)
    sub = {}
    for k, v in re.findall(re.escape(sep) + r"(\d{2})([^" + re.escape(sep) + r"]*)", flat):
        sub[k] = sub.get(k, "") + v
    def glue(keys):  # подполе до 27 знаков: полное — текст продолжается в следующем, неполное — отдельная часть
        out, prev = "", ""
        for k in keys:
            v = sub.get(f"{k:02d}", "").replace("˙", "")
            if v.strip():
                out += ("" if not out or len(prev) >= 27 else " ") + v
            prev = v
        return clean(out)
    return {"code": m.group(1), "kind": clean(sub.get("00")), "title": glue([*range(20, 30), *range(60, 64)]),
            "name": glue([32, 33]) or None, "account": clean(sub.get("38") or sub.get("31")), "text": flat}


def parse_mt940(text: str) -> Statement | None:
    fields = _mt940_fields(text)
    if not any(k == "61" for k, _ in fields):
        return None
    rows, iban, ccy, bal, last = [], None, None, None, None
    for k, v in fields:
        if k == "25":
            iban = iban or re.sub(r"[^A-Z0-9]", "", v.split("/")[-1].upper()) or None
        elif k in ("60F", "60M"):
            if m := re.match(r"([CD])(\d{6})([A-Z]{3})([\d,]+)", v):
                ccy = ccy or m.group(3)
                bal = amount_of(m.group(4)) * (-1 if m.group(1) == "D" else 1)
        elif k == "61":
            m = re.match(r"(\d{6})(\d{4})?(R?[CD])[A-Z]?([\d,]+)(\w{4})?(.*)", v.split("\n")[0])
            if not m:
                last = None
                continue
            vd = dt.datetime.strptime(m.group(1), "%y%m%d").date()
            day = vd
            if m.group(2):  # дата записи MMDD — год от даты валютирования (переход через Новый год)
                day = dt.date(vd.year, int(m.group(2)[:2]), int(m.group(2)[2:]))
                if (day - vd).days > 180:
                    day = day.replace(year=vd.year - 1)
                elif (vd - day).days > 180:
                    day = day.replace(year=vd.year + 1)
            amt = amount_of(m.group(4))
            amt = -amt if m.group(3) in ("D", "RC") else amt
            if bal is not None:
                bal = round(bal + amt, 2)
            last = Tx(day.isoformat(), amt, "", None, None, bal, raw={"code": m.group(5), "ref": clean(m.group(6))})
            rows.append(last)
        elif k == "86" and last is not None:
            d = _mt940_details(v)
            last.description, last.counterparty = d["title"], d.get("name")
            last.type = kind_of(d.get("kind") or "", d["title"], last.amount)
            last.raw |= {k2: d[k2] for k2 in ("code", "kind", "account") if d.get(k2)}
        elif k in ("62F", "62M") and rows and bal is not None:
            if (m := re.match(r"([CD])\d{6}[A-Z]{3}([\d,]+)", v)) and \
                    abs(amount_of(m.group(2)) * (-1 if m.group(1) == "D" else 1) - bal) > 0.01:
                for t in rows:  # остатки не сошлись с конечным — лучше без них
                    t.balance = None
    for t in rows:
        t.type = t.type or kind_of("", t.description, t.amount)
    return Statement("MT940", rows, iban, ccy)


# ---------------------------------------------------------------- CSV

# роль колонки -> слова в заголовке (после fold, без «#»), по убыванию предпочтения
HEADS = {
    "date": ["data ksiegowania", "data operacji", "data transakcji", "completed date", "booking date", "buchungstag",
             "buchungsdatum", "transaction date", "data", "date", "datum"],
    "amount": ["kwota transakcji (waluta rachunku)", "kwota operacji", "kwota", "amount", "betrag", "summe"],
    "debit": ["obciazenia", "wydatki", "debit", "soll", "ausgang"],
    "credit": ["uznania", "wplywy", "credit", "haben", "eingang"],
    "balance": ["saldo po transakcji", "saldo po operacji", "saldo", "balance", "kontostand"],
    "currency": ["waluta", "currency", "wahrung"],
    "who": ["nadawca / odbiorca", "nadawca/odbiorca", "dane kontrahenta", "odbiorca/zleceniodawca", "nazwa kontrahenta",
            "kontrahent", "odbiorca", "nadawca", "counterparty", "payee", "beneficiary", "empfanger", "auftraggeber",
            "zahlungsempfanger", "name"],
    "desc": ["opis transakcji", "opis operacji", "tytul", "opis", "description", "verwendungszweck", "buchungstext",
             "szczegoly", "details", "reference"],
    "type": ["typ transakcji", "rodzaj transakcji", "rodzaj operacji", "typ operacji", "type", "buchungsart", "umsatzart"],
    "state": ["state", "status"],
    "fee": ["fee", "prowizja"],
    "iban": ["rachunek kontrahenta", "na konto/z konta", "nr rachunku", "iban"],
}


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _rows(text: str) -> list[list[str]]:
    sample = re.sub(r'"[^"]*"', "", "\n".join(text.splitlines()[:40]))
    counts = {d: sample.count(d) for d in (";", ",", "\t", "|")}
    delim = max(counts, key=counts.get)
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]


def _header_roles(row: list[str]) -> dict:
    heads = [fold(c).strip("# ").strip() for c in row]
    roles, used = {}, set()
    for role, words in HEADS.items():
        for exact in (True, False):
            hit = next((i for w in words for i, h in enumerate(heads)
                        if i not in used and h and (h == w if exact else w in h)), None)
            if hit is not None:
                roles[role] = hit
                used.add(hit)
                break
    return roles


def _infer_roles(rows: list[list[str]]) -> dict:
    """CSV без заголовка (Santander): роли по содержимому — даты, суммы со знаком, остаток, самый длинный текст."""
    n = min(len(r) for r in rows)
    sample = rows[:200]
    share = lambda col, fn: sum(1 for r in sample if fn(r[col])) / len(sample)
    dates = [c for c in range(n) if share(c, date_of) > .8]
    nums = [c for c in range(n) if c not in dates and share(c, lambda x: amount_of(x) is not None and re.search(r"[.,]\d{2}\b", x)) > .8]
    if not dates or not nums:
        return {}
    roles = {"date": dates[-1] if len(dates) > 1 else dates[0]}  # две даты — операции и проводки: берём проводку
    signed = [c for c in nums if any((amount_of(r[c]) or 0) < 0 for r in sample)]
    roles["amount"] = (signed or nums)[0]
    for c in nums:  # остаток: соседние строки отличаются ровно на сумму операции
        if c == roles["amount"]:
            continue
        pairs = list(zip(sample, sample[1:]))
        ok = sum(1 for a, b in pairs if abs(abs(amount_of(a[c]) - amount_of(b[c])) - abs(amount_of(a[roles["amount"]]))) < .011
                 or abs(abs(amount_of(a[c]) - amount_of(b[c])) - abs(amount_of(b[roles["amount"]]))) < .011)
        if pairs and ok / len(pairs) > .6:
            roles["balance"] = c
            break
    used = set(roles.values())
    texts = sorted((c for c in range(n) if c not in used and share(c, lambda x: re.search(r"[A-Za-zА-Яа-яąćęłńóśźż]{3}", x))),
                   key=lambda c: -sum(len(r[c]) for r in sample))
    if texts:
        roles["desc"] = texts[0]
    if len(texts) > 1:
        roles["who"] = texts[1]
    if ib := next((c for c in range(n) if c not in used and share(c, lambda x: re.fullmatch(r"'?[A-Z]{0,2}[\d ]{20,34}'?", x)) > .5), None):
        roles["iban"] = ib
    return roles


KV_KEYS = ["tytuł", "tytul", "lokalizacja", "adres odbiorcy", "adres nadawcy", "adres", "miasto", "kraj", "numer telefonu",
           "nazwa odbiorcy", "nazwa nadawcy", "rachunek odbiorcy", "rachunek nadawcy", "numer karty", "numer referencyjny",
           "oryginalna kwota operacji", "operacja", "data i czas operacji", "data wykonania", "odbiorca", "nadawca"]
KV_RX = re.compile(r"(?i)(?<![\w])(" + "|".join(sorted(map(re.escape, KV_KEYS), key=len, reverse=True)) + r")\s*:\s*")


def _kv(cells: list[str]) -> dict:
    """iPKO пишет подробности отдельными ячейками «Ключ: значение»: «Tytuł: …», «Lokalizacja: Adres: … Miasto: …»."""
    out = {}
    for c in cells:
        parts = KV_RX.split(c or "")
        for k, v in zip(parts[1::2], parts[2::2]):
            out.setdefault(fold(k), v.strip(" ,;"))
    return out


def parse_csv(text: str) -> Statement | None:
    rows = _rows(text)
    if len(rows) < 1:
        return None
    head, roles = None, {}
    for i, r in enumerate(rows[:40]):
        rr = _header_roles(r)
        if "date" in rr and ("amount" in rr or "debit" in rr):
            head, roles = i, rr
            break
    body = rows[head + 1:] if head is not None else rows
    fmt = "CSV"
    if head is None:
        body = [r for r in body if any(date_of(c) for c in r[:3])]
        if not body:
            return None
        width = max(set(map(len, body)), key=[len(r) for r in body].count)  # сводка сверху и итог снизу — другой длины
        body = [r for r in body if len(r) == width]
        roles = _infer_roles(body)
        fmt = "CSV без заголовка"
        if not roles:
            return None
    header = rows[head] if head is not None else []
    named = set(roles.values())
    out = []
    for r in body:
        get = lambda role: r[roles[role]] if role in roles and roles[role] < len(r) else ""
        day = date_of(get("date"))
        if "amount" in roles:
            amt = amount_of(get("amount"))
        else:
            d, c = amount_of(get("debit")), amount_of(get("credit"))
            amt = None if d is None and c is None else (c or 0) - abs(d or 0)
        if not day or amt is None or amt == 0 and not get("desc"):
            continue  # итоговые строки, строки-подзаголовки, пустые
        if fold(get("state")) and fold(get("state")) not in ("completed", "zaksiegowana", "wykonana", "booked"):
            continue  # Revolut: отменённые и ожидающие
        if (fee := amount_of(get("fee"))):
            amt = round(amt - abs(fee), 2)
        extra = [c for k, c in enumerate(r) if c and k not in named and head is not None
                 and (k >= len(header) or not header[k].strip())]
        kv = _kv([get("desc"), *extra])
        title = kv.get("tytul") or kv.get("tytul operacji") or kv.get("title")
        place = " ".join(filter(None, [kv.get("adres") or kv.get("lokalizacja"), kv.get("miasto")]))
        who = clean(get("who")) or kv.get("nazwa odbiorcy") or kv.get("nazwa nadawcy") or kv.get("odbiorca") or kv.get("nadawca")
        if kv:  # описание из частей: назначение, место (у карты — магазин и город), телефон (BLIK на телефон)
            desc = clean(" ".join(filter(None, [title, place, kv.get("numer telefonu") and f"tel. {kv['numer telefonu']}"])))
            desc = desc or clean(" ".join([get("desc"), *extra]))
        else:
            desc = clean(" ".join([get("desc"), *extra]))
        kind = kind_of(get("type"), f"{get('desc')} {' '.join(extra)}", amt)
        out.append(Tx(day, round(amt, 2), desc, clean(who) or None, kind, amount_of(get("balance")),
                      raw={"row": r, "type_text": get("type") or None}))
    if not out:
        return None
    ccy = next((r[roles["currency"]] for r in body if "currency" in roles and roles["currency"] < len(r) and r[roles["currency"]]), None)
    return Statement(fmt, out, None, ccy)


def parse(path: Path) -> Statement:
    data = path.read_bytes()
    head = data[:2000].lstrip()
    st = None
    if head.startswith(b"<") or b"BkToCstmrStmt" in head or path.suffix.lower() in (".xml", ".camt"):
        st = parse_camt(data)
    if st is None and (b":20:" in data[:4000] or b":61:" in data or path.suffix.lower() in (".sta", ".940", ".mt940")):
        st = parse_mt940(_decode(data))
    if st is None:
        st = parse_csv(_decode(data))
    if st is None or not st.rows:
        raise ValueError(f"{path.name}: не узнал формат — нужен CSV из интернет-банка, MT940 или camt.053 (XML)")
    # по времени, от старых к новым; файлы «новые сверху» — переворачиваем, чтобы порядок внутри дня сохранился
    if len(st.rows) > 1 and st.rows[0].date > st.rows[-1].date:
        st.rows.reverse()
    st.rows.sort(key=lambda t: t.date)
    return st


# ---------------------------------------------------------------- в базу

def norm_account(s: str | None) -> str | None:
    """Номер счёта для сравнения: только цифры, без кода страны (IBAN PL… и 26 цифр польского номера — один счёт)."""
    d = re.sub(r"\D", "", s or "")
    return d[-26:] if len(d) >= 20 else None


def api_accounts(con) -> dict:
    """Счета из подключения к банку: номер -> префикс id их операций (номер — в ответе банка у списаний)."""
    out = {}
    for r in con.execute("""SELECT substr(id, 1, instr(id, ':') - 1) p, raw FROM bank_tx
                            WHERE amount < 0 AND id NOT LIKE '%:F%' GROUP BY p"""):
        try:
            acc = norm_account(((json.loads(r["raw"]) or {}).get("debtor_account") or {}).get("iban"))
        except (TypeError, ValueError):
            acc = None
        out[acc or f"?{r['p']}"] = r["p"]
    return out


def account_prefix(con, iban: str | None) -> tuple[str, str, bool]:
    """-> (префикс id операций, как назвать счёт в отчёте, счёт из подключения к банку?). Файл без номера
    счёта — тот же счёт, что подключён к банку (обычно это выгрузка истории оттуда же); иначе — отдельный счёт."""
    api, acc = api_accounts(con), norm_account(iban)
    if acc and acc in api:
        return api[acc], "подключённый счёт", True
    if not acc and len(api) == 1:
        return next(iter(api.values())), "подключённый счёт (в файле нет номера — считаю, что тот же)", True
    key = acc or "no-account"
    return "f" + hashlib.sha1(key.encode()).hexdigest()[:7], f"счёт …{acc[-4:]}" if acc else "счёт из файла", False


def fresh(existing: list[tuple[str, float]], rows: list[Tx]) -> tuple[list[Tx], int]:
    """Операции, которых ещё нет: та же сумма ±DUP_DAYS дней — уже есть (каждая имеющаяся — только для одной
    строки файла, ближайшей по дате). -> (новые, повторов)"""
    pool = {}
    for d, a in existing:
        pool.setdefault(round(a, 2), []).append(dt.date.fromisoformat(d))
    new, dup = [], 0
    for t in rows:
        cand = pool.get(round(t.amount, 2), [])
        day = dt.date.fromisoformat(t.date)
        near = sorted((abs((d - day).days), i) for i, d in enumerate(cand) if abs((d - day).days) <= DUP_DAYS)
        if near:
            cand.pop(near[0][1])
            dup += 1
        else:
            new.append(t)
    return new, dup


def import_file(path: Path, con=None) -> dict:
    """Разобрать файл и добавить новые операции. -> отчёт для консоли и интерфейса."""
    con = con or connect()
    st = parse(path)
    prefix, label, linked = account_prefix(con, st.iban)
    existing = [(r["date"], r["amount"]) for r in con.execute("SELECT date, amount FROM bank_tx WHERE id LIKE ?", (prefix + ":%",))]
    new, dup = fresh(existing, st.rows)
    order = {id(t): i for i, t in enumerate(st.rows)}  # номер после «;» — порядок внутри дня для остатка на конец дня
    seen_keys = {}
    rows = []
    for t in new:
        key = f"{t.date}|{t.amount:.2f}|{t.description}|{t.counterparty}"
        seen_keys[key] = seen_keys.get(key, 0) + 1
        h = hashlib.sha1(f"{prefix}|{key}|{seen_keys[key]}".encode()).hexdigest()[:10]
        raw = {"file": path.name, "format": st.format, "currency": st.currency, **t.raw}
        rows.append((f"{prefix}:F{h};{order[id(t)]}", t.date, t.amount, t.balance, t.type, t.counterparty, t.description,
                     json.dumps(raw, ensure_ascii=False)))
    # seen — пусто: когда операция появилась в банке, неизвестно (кошелёк поставит середину дня)
    con.executemany("INSERT OR IGNORE INTO bank_tx (id, date, amount, balance, type, counterparty, description, raw) "
                    "VALUES (?,?,?,?,?,?,?,?)", rows)
    if rows:
        last = con.execute("SELECT max(date) FROM bank_tx").fetchone()[0]
        set_meta(con, "bank_last_date", last)
        if not linked:  # отдельный счёт из файла — его остаток для «всего на счетах» (у подключённого — из банка)
            with_bal = [t for t in st.rows if t.balance is not None]
            if with_bal:
                set_meta(con, f"bank_balance:{prefix}", json.dumps({"amount": with_bal[-1].balance, "date": with_bal[-1].date}))
    con.commit()
    return {"file": path.name, "format": st.format, "account": label, "rows": len(st.rows), "new": len(rows), "dup": dup,
            "from": st.rows[0].date, "to": st.rows[-1].date}


def report(r: dict) -> str:
    return (f"{r['file']}: {r['format']}, {r['account']}, {r['from']} — {r['to']}: операций {r['rows']}, "
            f"новых {r['new']}, уже были {r['dup']}")


def import_inbox(verbose=True) -> list[dict]:
    """Все файлы из bank/inbox/; разобранные — в bank/inbox/обработано (с датой в имени, чтобы не затереть)."""
    INBOX.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(INBOX.iterdir()):
        if not p.is_file() or p.suffix.lower() not in EXTS:
            continue
        try:
            r = import_file(p)
        except ValueError as e:
            out.append({"file": p.name, "error": str(e)})
            if verbose:
                print("  ✗", e)
            continue
        out.append(r)
        if verbose:
            print("  " + report(r))
        DONE.mkdir(exist_ok=True)
        shutil.move(str(p), DONE / f"{dt.datetime.now():%Y%m%d-%H%M%S}_{p.name}")
    return out


def main(argv: list[str]):
    files = [Path(a) for a in argv]
    results = [import_file(p) for p in files] if files else import_inbox(verbose=False)
    if not results:
        print(f"Нет файлов. Положи выписку (CSV, MT940, camt.053) в {INBOX} или укажи файл: python budget.py bank import <файл>")
        return
    for r in results:
        print("✗ " + r["error"] if "error" in r else report(r))
    if any(r.get("new") for r in results):
        from core import reconcile
        reconcile.match(verbose=False)
        print("Сверка с чеками обновлена.")


if __name__ == "__main__":
    main(sys.argv[1:])
