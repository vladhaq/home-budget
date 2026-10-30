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


def test_photo_receipt_without_shop_takes_it_from_paired_bank_payment(tmp_path, monkeypatch):
    """Магазин не прочитался с фото — сверка берёт его из операции, с которой сошлась покупка, и по нему работает
    твоё правило магазина (в тесте — встроенный шаблон «vapebox»)."""
    from core import db
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    con = db.connect()
    db.save_purchase(con, {"id": "photo:x", "source": "photo", "date": "2030-10-09T14:56:00", "merchant": None,
                           "total": 29.0, "payment_method": "card"},
                     [{"name": "LIQUID TEST 20MG 10ML", "qty": 1, "unit_price": 29.0, "amount": 29.0}])
    con.execute("INSERT INTO bank_tx (id, date, amount, type, description, raw) VALUES (?,?,?,?,?,?)",
                ("t1", "2030-10-11", -29.0, "CARD-PAYMENT", "WROCLAWVAPEBOX SP Z O OPL", "{}"))
    con.commit()
    R.match(verbose=False)
    con = db.connect()
    assert dict(con.execute("SELECT merchant, bank_tx_id FROM purchases WHERE id = 'photo:x'").fetchone()) == \
        {"merchant": "Vapebox", "bank_tx_id": "t1"}



def test_two_different_registrations_in_a_row_are_not_a_retry(tmp_path, monkeypatch):
    """Регистрация платежа той же суммы тому же магазину через полторы минуты — повтор, только если описание то же:
    два разных курса на одну сумму — два платежа, оба в суммах."""
    from core import db
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    con = db.connect()
    note = "регистрация платежа без подтверждения — проверить по банку"
    for pid, date, name in (("email:p24:1", "2030-03-05T12:01:10", "Kurs A - lab."),
                            ("email:p24:2", "2030-03-05T12:02:40", "Kurs A - wykł."),
                            ("email:p24:3", "2030-03-05T12:03:50", "Kurs A - wykł.")):  # а это — повтор второго
        db.save_purchase(con, {"id": pid, "source": "email", "date": date, "merchant": "Uczelnia", "total": 320.0,
                               "payment_method": "online"}, [{"name": name, "qty": 1, "unit_price": 320.0, "amount": 320.0}])
        con.execute("UPDATE purchases SET note = ? WHERE id = ?", (note, pid))
    con.commit()
    R.match(verbose=False)
    con = db.connect()
    status = dict(con.execute("SELECT id, status FROM purchases").fetchall())
    assert status == {"email:p24:1": None, "email:p24:2": None, "email:p24:3": "doubt"}
