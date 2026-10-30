"""Английский интерфейс: словарь (app/static/js/i18n-en.js) читается и сервером — для напоминаний в Telegram;
правила те же, что у страницы: числа и даты — {n}, имена — {s} (и они тоже переводятся), значки вокруг текста,
составное «Еда › Сладкое». В каждом переводе — те же подстановки, что в ключе."""
import re

import pytest

from core import i18n


@pytest.mark.parametrize("ru, en", [
    ("Обзор", "Overview"),
    ("операций: 914", "transactions: 914"),
    ("−23% к прошлому месяцу", "−23% vs previous month"),
    ("ошибки: Чеки Biedronka", "errors: Biedronka receipts"),
    ("Еда › Сладкое", "Food › Sweets"),
    ("Доходы/Зарплата", "Income/Salary"),
    ("▸ Взнос наличных", "▸ Cash deposit"),
    ("идёт, до 18.10 сб", "on, until 18.10 Sat"),
    ("  когда: 20.10 вс – 22.10 вт", "  when: 20.10 Sun – 22.10 Tue"),
    ("PILOS Ser gouda tarty, XXL", "PILOS Ser gouda tarty, XXL"),  # польское название товара — как есть
])
def test_translate(ru, en):
    assert i18n.translate(ru) == en


def test_placeholders_match():
    """{n} и {s} в переводе — столько же, сколько в ключе (или явные {1}, {2}…): иначе число потеряется."""
    d = i18n._load()
    bad = []
    keys = list(d["plain"].items()) + [(p[0].pattern, p[1]) for p in d["patterns"]]
    for k, v in d["plain"].items():
        if re.search(r"\{\d\}", v):
            continue
        if k.count("{n}") != v.count("{n}") or k.count("{s}") != v.count("{s}"):
            bad.append((k, v))
    for _, v, _, kinds in d["patterns"]:
        if re.search(r"\{\d\}", v):
            continue
        if v.count("{n}") + v.count("{s}") != len(kinds):
            bad.append((kinds, v))
    assert not bad and keys


def test_reminder_language_follows_setting(tmp_path, monkeypatch):
    from core import db
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    con = db.connect()
    text = "🛒 Lidl: завтра (20.10 вс) на скидке — стоит зайти в магазин\n\nSer gouda\n  газетка: https://x"
    assert i18n.localize(text, con) == text  # язык не выбран — по-русски
    db.set_meta(con, "lang", "en")
    assert i18n.localize(text, con).splitlines()[0] == "🛒 Lidl: tomorrow (20.10 Sun) on sale — worth a visit"
