"""Газетка Lidl: цена нарисована картинкой, в ячейке — «−50%» и цены соседнего товара. Обычная цена берётся из блока
самого товара, цена по акции — из его цены за кг, скидка пересчитывается."""
from core import deals

L = deals.mod()
FLYER = {"name": "Gazetka", "start": "2030-10-12", "end": "2030-10-14", "products": []}


def block(x0, y0, x1, y1, text):
    return [x0, y0, x1, y1, text]


def test_neighbour_discount_does_not_leak():
    neighbour = block(100, 900, 400, 1000, "PILOS\nSer gouda w kawałku\n1 kg\n* cena przed obniżką/\ncena poza promocją:\n29,99/opak.")
    hit = block(450, 900, 750, 1000, "PILOS\nSer gouda tarty, XXL\n500 g\n1 kg = 27,98\n* cena przed obniżką/\n"
                                     "cena poza promocją:\n15,99/opak., 1 kg = 31,98")
    badges = [block(100, 1010, 250, 1040, "-50%"), block(260, 1010, 380, 1040, "29,99*"),
              block(450, 1010, 600, 1040, "-12%"), block(610, 1010, 700, 1040, "15,99*")]
    page = {"h": 1400, "blocks": [neighbour, hit] + badges}
    d = L.build_deal(FLYER, "gazetka-test", 0, page, hit)
    assert (d["price"], d["old_price"], d["discount"]) == (13.99, 15.99, 12)


def test_pack_qty():
    assert L.pack_qty("500 g", "kg") == 0.5 and L.pack_qty("4 x 150 g", "kg") == 0.6
    assert L.pack_qty("165 g, 175 g", "kg") == 0.165  # варианты веса — цена за кг в газетке по первому
    assert L.pack_qty("33-45 g", "kg") is None and L.pack_qty("100 g = 4,61-4,47", "kg") is None
    assert L.pack_qty("500 ml", "kg") is None  # другая единица
