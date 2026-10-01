"""Разбор писем о покупках: текст письма -> покупка (магазин, номер заказа, позиции, сумма, оплата).

Письма одного заказа (подтверждение, оплата, отправка) склеиваются по номеру заказа.
Исходники — data/mail/raw/*.eml (скачаны командой mail bodies).
"""
import email
import re
from email import policy

from bs4 import BeautifulSoup

from core.common import DATA, num

PLN = r"(-?\d{1,3}(?:[  .]\d{3})*,\d{2})\s*(?:zł|PLN)"
MONTHS = {"stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6, "lipca": 7,
          "sierpnia": 8, "września": 9, "października": 10, "listopada": 11, "grudnia": 12}
PAYMENT_WORDS = [(r"blik", "blik"), (r"google pay|apple pay|karta|card|visa|mastercard|maestro", "card"),
                 (r"allegro pay|pay later|odroczon", "deferred"), (r"przelew|transfer|pay ?by ?link|tpay|przelewy24|payu",
                                                                     "online"), (r"pobranie|za pobraniem|got[oó]wk", "cash")]


def load(path: str) -> email.message.EmailMessage:
    return email.message_from_bytes((DATA / path).read_bytes(), policy=policy.default)


def text(msg) -> str:
    """Текст письма построчно: HTML -> текст, лишние пробелы и невидимые символы убраны."""
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is None:
        return ""
    try:
        raw = body.get_content()
    except (LookupError, UnicodeDecodeError):
        raw = body.get_payload(decode=True).decode("utf-8", "replace")
    if body.get_content_type() == "text/html":
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["style", "script", "head"]):
            tag.decompose()
        raw = soup.get_text("\n")
    lines = []
    for ln in raw.splitlines():
        ln = re.sub(r"[‌​­﻿͏]", "", ln)
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            lines.append(ln)
    # «92,» + «00 zł» (цена разбита на строки вёрсткой) -> «92,00 zł»
    out = []
    for ln in lines:
        if out and re.fullmatch(r"\d{2} ?(zł|PLN)", ln) and re.search(r"\d,$", out[-1]):
            out[-1] = out[-1] + ln.replace(" ", " ")
        else:
            out.append(ln)
    return "\n".join(out)


def pdfs(msg) -> list[tuple[str, bytes]]:
    return [(p.get_filename() or "file.pdf", p.get_payload(decode=True)) for p in msg.walk()
            if p.get_content_type() == "application/pdf"]


def amount(s: str):
    m = re.search(PLN, s or "")
    return num(m.group(1).replace(" ", "").replace(" ", "").replace(".", "")) if m else None


def payment_method(s: str) -> str | None:
    low = (s or "").lower()
    return next((m for rx, m in PAYMENT_WORDS if re.search(rx, low)), None)


def polish_date(s: str) -> str | None:
    """«22 września 2026, 01:39» -> 2026-09-22T01:39:00"""
    m = re.search(r"(\d{1,2}) (\w+) (\d{4})(?:,? (\d{1,2}):(\d{2}))?", s or "")
    if not m or m.group(2) not in MONTHS:
        return None
    d = f"{m.group(3)}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    return d + (f"T{int(m.group(4)):02d}:{m.group(5)}:00" if m.group(4) else "T00:00:00")
