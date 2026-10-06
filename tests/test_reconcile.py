"""Сверка: возвраты на карту и их исходные покупки."""
from core import reconcile as R


def test_brand_same_shop():  # адрес сайта из выписки и название магазина из письма — один магазин
    assert R.brand("example.nl") == R.brand("Example") == R.brand("http://www.example.nl/") == "example"


def test_refund_of_part_of_order_goes_to_only_order_of_shop(tmp_path, monkeypatch):
    """Вернул часть заказа со скидкой: сумма возврата не равна ни заказу, ни товару — исходная покупка —
    единственный заказ того же магазина за 60 дней; двух заказов — не угадываем."""
    from core import db
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    con = db.connect()
    for pid, date, merchant, total in (("email:shop:1", "2025-09-30T21:00:00", "Example", 438.5),
                                       ("email:other:1", "2025-10-05T10:00:00", "Allegro", 300.0),
                                       ("email:other:2", "2025-10-06T10:00:00", "Allegro", 250.0)):
        db.save_purchase(con, {"id": pid, "source": "email", "date": date, "merchant": merchant, "total": total},
                         [{"name": "Rzecz", "qty": 1, "unit_price": total, "amount": total}])
    got = R.refund_origin(con, {"amount": 241.0, "date": "2025-10-21", "merchant": None}, "example.nl")
    assert got == {"id": "email:shop:1", "item": "часть заказа от 30.09.25"}
    assert R.refund_origin(con, {"amount": 30.0, "date": "2025-10-21", "merchant": None}, "Allegro") is None
