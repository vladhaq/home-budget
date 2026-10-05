"""Разбор текста кассового чека после OCR и голосование вариантов (тексты — в формате настоящих чеков)."""
from receipts.textparse import merge, parse_text

LIDL = """PARAGON FISKALNY
Filet z kurczak.św. F 1,052 x24,80 26,09C
OPUST Filet z kurczak.św, F -10,32
15,77C
Filet z kurczak.św. F 0,928 x24,80 23,01C
OPUST Filet z kurczak.sw. °F -9,10
13,91C
Pyzy z mięsem mroż. F 1 x8,49 8,49C
Podsuma: o 38,17
SPRZEDAŻ OPODATKOWANA C 38,17
SUMA PLN 38,17
"""

KAUFLAND = """PARAGON FISKALNY
Syrop malinowy 0,7l i 1SZT x7,99 7,99B
Jabłka Jonagold kg t 1,05KG x4,99 5,24C
Pieczywo żytnie 260g t
25ZT x7,99 15,98C
OPUST do -25% -4,00C
Chleb tostowy 500g t 152T x4,99 4,99C
OPUSTY ŁĄCZNIE -4,00
SPRZEDAŻ OPODATKOWANA C 30,20
SUMA PLN 30,20
"""


def rows(r):
    return [(i["name"], i["amount"], i["discount"]) for i in r["items"]]


def test_same_name_twice_are_two_items():  # два филе — две позиции, «Podsuma» — не товар
    r = merge([parse_text(LIDL)] * 3)
    assert r["status"] == "ok" and r["total"] == 38.17
    assert rows(r) == [("Filet z kurczak.św", 26.09, 10.32), ("Filet z kurczak.św", 23.01, 9.1),
                       ("Pyzy z mięsem mroż", 8.49, None)]


def test_kaufland_qty_next_line_and_discount_summary():
    # количество на отдельной строке («2SZT» прочитано как «25ZT»), «KG» заглавными, «OPUSTY ŁĄCZNIE» — итог скидок
    r = merge([parse_text(KAUFLAND)])
    assert r["status"] == "ok"
    assert rows(r) == [("Syrop malinowy 0,7l", 7.99, None), ("Jabłka Jonagold kg", 5.24, None),
                       ("Pieczywo żytnie 260g", 15.98, 4.0), ("Chleb tostowy 500g", 4.99, None)]
    assert r["items"][2]["qty"] == 2.0 and r["items"][1]["qty"] == 1.05


def test_vote_prefers_agreeing_variant_and_drops_stray_line():
    bad = KAUFLAND.replace("Chleb tostowy 500g t 152T x4,99 4,99C", "Chlsh tostdwy 500g | T 152] x4,99 4.980")
    stray = KAUFLAND.replace("OPUSTY ŁĄCZNIE -4,00", "Mus owocowy x1 2,50C")  # мусорная строка одного варианта
    r = merge([parse_text(t) for t in (bad, bad, KAUFLAND, KAUFLAND, KAUFLAND, stray)])
    assert r["status"] == "ok" and [i["name"] for i in r["items"]][-1] == "Chleb tostowy 500g"
    assert round(sum(i["amount"] - (i["discount"] or 0) for i in r["items"]), 2) == 30.20


def test_easyocr_spacing_and_letter_o():  # «15 ,98C», «~4,Ooc», «SUHA PLN 30 20»
    easy = KAUFLAND.replace("15,98C", "15 ,98C").replace("-4,00C", "~4,Ooc").replace("SUMA PLN 30,20", "SUHA PLN 30 20")
    r = merge([parse_text(easy)])
    assert r["total"] == 30.2 and r["status"] == "ok" and rows(r)[2] == ("Pieczywo żytnie 260g", 15.98, 4.0)
