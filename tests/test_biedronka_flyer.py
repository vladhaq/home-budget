"""Газетки Biedronka: распознанные строки склеиваются в карточки, плашки (цена, «KAŻDA Z 2 SZTUK», «Cena przed
obniżką») достаются своему товару, а не соседу; цена — по цене за кг и весу (сверка с крупной ценой), скидка — не
из «3,2%» жирности, «DRUGI 70% TANIEJ» — −35 % за штуку; даты — из шапки страницы."""
import datetime as dt

from core import biedronka_deals as B

FLYER = {"id": "x1", "fslug": "codziennie-niskie-ceny-p-oferta-od-12-10", "name": "Codziennie niskie ceny p — od 12.10",
         "start": "2030-10-12", "end": "2030-10-18"}


def line(x, y, text, w=200, h=18):
    return [x, y, x + w, y + h, text]


def page():
    """Крупный товар: плашка с ценой слева от названия; ниже — обычная карточка."""
    lines = [line(120, 20, "PONIEDZIAŁEK — SOBOTA 12.10-17.10", w=500),
             line(280, 137, "Masło ekstra"), line(280, 163, "Łaciate, 200 g"), line(280, 184, "14,95 zł/kg"),
             line(100, 212, "PRZY ZAKUPIE 3", w=120), line(85, 236, "50% TANIEJ", w=130),
             line(104, 273, "KAŻDA Z 3 SZTUK", w=120), line(111, 298, "299", w=100, h=40),
             line(69, 420, "Limit dzienny 9 szt. na kartę Moja Biedronka.", w=500),
             line(279, 950, "Mleko UHT 3,2% Testowe"), line(279, 975, "1 l"), line(279, 997, "3,98 zł/l")]
    return {"w": 1146, "h": 1800, "blocks": B.cards(lines)}


def grid():
    """Ряд из двух товаров (как на страницах с колбасами): над названием — «DRUGI 70% TANIEJ», «KAŻDA Z 2 SZTUK»
    и крупная «6 49» (распознана как «64?»), под ним — «Cena przed obniżką» и цена; к названию соседа прилип мусор."""
    lines = [line(475, 322, "DRUGI", w=40), line(442, 338, "70% TANIEJ", w=120), line(450, 375, "KAŻDA Z 2 SZTUK", w=110),
             line(445, 396, "64?", w=90, h=56),
             line(416, 476, "Szynka zawędzana"), line(416, 497, "Kraina Wędlin, 250 g"), line(416, 516, "25,96 zł/kg", w=80),
             line(422, 545, "Cena przed obniżką:", w=90), line(520, 550, "9,99", w=40),
             line(764, 455, "ET", w=40), line(764, 476, "Salami pepperoni"), line(764, 497, "Kraina Wędlin, 100 g"),
             line(764, 516, "39,90 zł/kg", w=80), line(932, 540, "Cena za 1 szt./ bez karty MB/ poza limitem:", w=80),
             line(1032, 544, "5,25", w=40),
             line(417, 594, "Limit dzienny 6 szt. na kartę Moja Biedronka", w=210)]
    return {"w": 1146, "h": 1800, "blocks": B.cards(lines)}


def deal(pg, start):
    hit = next(b for b in pg["blocks"] if B.is_product_block(b) and start in b[4])
    return B.build_deal(FLYER, "s", 0, pg, hit)


def test_lines_glued_into_product_cards():
    cards = [b for b in page()["blocks"] if B.is_product_block(b)]
    assert [B.title_of(b[4]) for b in cards] == ["Masło ekstra Łaciate", "Mleko UHT 3,2% Testowe"]
    assert sorted(B.title_of(b[4]) for b in grid()["blocks"] if B.is_product_block(b)) == \
        ["Salami pepperoni Kraina Wędlin", "Szynka zawędzana Kraina Wędlin"]  # «ET» над названием — мусор картинки


def test_price_by_unit_confirmed_by_big_digits_and_conditions():
    d = deal(page(), "Masło")
    assert (d["price"], d["approx"], d["size"], d["unit_price"], d["ocr"]) == (2.99, False, "200 g", 14.95, True)
    assert d["conditions"][0] == "цена за штуку при покупке 3 шт."
    assert d["discount"] == 50  # «przy zakupie 3 — 50% taniej» — на каждую, а не «второй −50%»


def test_fat_percent_is_not_a_discount():
    d = deal(page(), "Mleko")
    assert d["price"] == 3.98 and d["approx"] and d["discount"] is None


def test_second_item_discount_is_halved_and_neighbours_stay_apart():
    pg = grid()
    d = deal(pg, "Szynka")
    assert (d["price"], d["approx"], d["old_price"], d["discount"]) == (6.49, False, 9.99, 35)
    assert d["conditions"][0] == "второй товар −70%, цена — средняя за штуку"
    assert not any("5,25" in c for c in d["conditions"])  # «без карты 5,25» — у салями справа
    s = deal(pg, "Salami")
    assert (s["price"], s["old_price"]) == (3.99, 5.25)


def test_big_digits_and_sizes_from_noisy_ocr():
    assert B.big_pattern("272") == "272" and B.big_pattern("64?") == "64." and B.big_pattern("469,") == "469"
    assert B.big_pattern("2+") is None and B.big_pattern("70%") is None
    assert B.pack("Ser feta Ektos\n49,95 kg") == (None, None, None)  # цена за кг без «zł/», не вес
    assert B.pack("Cytryna 0,60 zł/100 g") == (None, None, None)
    assert B.pack("Kabanosy, 9x50 g") == (0.45, "kg", "9x50 g")


def test_dates_from_page_header_and_slug():
    assert B.page_dates(page(), 2030) == (dt.date(2030, 10, 12), dt.date(2030, 10, 17))
    assert B.start_from_slug("codziennie-niskie-ceny-p-oferta-od-08-10", dt.date(2030, 10, 11)) == dt.date(2030, 10, 8)
    assert B.start_from_slug("codziennie-niskie-ceny-p-oferta-od-29-12", dt.date(2041, 1, 2)) == dt.date(2040, 12, 29)
