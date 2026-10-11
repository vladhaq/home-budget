"""Личная инфляция: регулярная цена не путается с акциями, выгодная — что реально заплатил; поиск по названию из чека."""
from core import prices
from core.categories import normalize


def row(date, price, paid=None, code=None):
    return {"date": date, "name": "Ser testowy 500g", "code": code, "price": price, "unit": "шт", "per_unit": None, "pu_unit": None,
            "paid": paid if paid is not None else price, "paid_pu": None, "season": False}


def test_promo_price_in_receipt_is_not_a_price_cut():
    """Kaufland: акционная цена прямо в строке чека (9,99 вместо 14,89) — регулярная цена та же."""
    rs = [row("2036-01-10", 14.89), row("2036-02-10", 14.89), row("2036-03-10", 14.89),
          row("2036-06-01", 14.89), row("2036-06-20", 9.99), row("2036-07-01", 9.99)]
    assert prices.change(rs, "regular")["pct"] == 0.0
    assert prices.change(rs, "best")["pct"] == -32.9


def test_coupon_discount_counts_only_in_best_price():
    """Lidl: цена на полке выросла, но со скидкой в приложении платишь меньше, чем раньше."""
    rs = [row("2034-10-01", 2.79), row("2034-10-20", 2.79), row("2035-03-01", 3.99, 1.99), row("2035-03-15", 3.99, 1.99)]
    assert prices.change(rs, "regular")["pct"] == 43.0
    assert prices.change(rs, "best")["pct"] == -28.7


def test_photo_misread_outlier_ignored():
    rs = [row("2035-01-01", 5.0), row("2035-01-20", 50.0), row("2035-06-01", 5.5), row("2035-06-10", 5.5)]
    assert prices.change(rs, "regular")["p0"] == 5.0


def test_search_by_receipt_name():
    key = normalize("K.GoudaPlastry500g")
    assert prices.matches(key, "K.GoudaPlastry500g") and prices.matches(key, "gouda plastry")
    assert prices.matches(normalize("Gouda w plastr. XXL"), "Gouda w plastr. XXL")
    assert not prices.matches(key, "maslo")


def test_one_off_higher_price_is_not_regular():
    """Разовый скачок (1,79 между 1,71) — не новая регулярная цена: «стало» — последняя покупка."""
    rs = [row("2035-03-28", 1.71), row("2035-03-31", 1.71), row("2035-06-07", 1.71), row("2035-08-04", 1.79),
          row("2035-08-13", 1.71)]
    assert prices.change(rs, "regular")["pct"] == 0.0


def test_variants_under_one_name_not_mixed():
    """Два вкуса со своими кодами (6,39 и 7,99) — сравнивается основной, а не дорогой вкус с дешёвым."""
    rs = [row("2034-11-15", 6.39, code="780"), row("2034-11-21", 6.39, code="780"), row("2034-12-13", 7.99, code="766"),
          row("2035-03-04", 6.99, code="766"), row("2035-03-17", 6.99, code="780")]
    assert prices.change(rs, "regular")["pct"] == 9.4


def test_low_price_that_looks_like_promo_is_unsure():
    """Уксус: «было» 2,49 — одна покупка, и та же цена потом — короткий провал между 3,59: похоже на акцию."""
    rs = [row("2035-06-16", 2.49), row("2035-09-30", 3.59), row("2036-06-17", 3.59), row("2036-08-18", 2.49),
          row("2036-09-07", 3.59)]
    assert prices.change(rs, "regular")["sure"] is False


def test_steady_price_change_is_sure():
    rs = [row("2034-09-28", 19.99), row("2034-11-15", 19.99), row("2034-12-05", 19.99), row("2036-05-23", 29.89),
          row("2036-07-23", 29.89, 14.99), row("2036-08-11", 29.89, 14.99)]
    ch = prices.change(rs, "regular")
    assert ch["sure"] and ch["pct"] == 49.5


def test_position_word_missing_in_receipt():
    """«ser gouda»: в чеке Lidl — «Gouda w plastr. XXL» без «ser»; одно общее слово может не найтись."""
    key = normalize("Gouda w plastr. XXL")
    assert not prices.matches(key, "ser gouda") and prices.matches(key, "ser gouda", loose=True)
    assert not prices.matches(normalize("Ser Carski XXL 500g"), "ser gouda", loose=True)  # «ser» короче 4 букв
