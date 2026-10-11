"""Biedronka: электронный чек в JSON — точные позиции (гроши, скидка под позицией, сторно) вместо распознавания PDF;
распознанная из PDF копия того же чека заменяется точной."""
import json

from core import db
from receipts import biedronka

DOC = {"receipt": {"Id": "1234567890", "DateTime": "2030-03-15T17:56:19+01:00", "TotalValue": 30.97, "Discount": 5.49,
                   "Store": "0001", "Address": "ul. Testowa 1, Wrocław"},
       "eparagon": {"body": [
           {"sellLine": {"name": "Ciastka testowe 500 g        C", "vatId": "C", "price": 1999, "total": 1999,
                         "quantity": "1", "isStorno": False}},
           {"sellLine": {"name": "Napój testowy 0,4l       A", "vatId": "A", "price": 549, "total": 1647,
                         "quantity": "3", "isStorno": False}},
           {"discountLine": {"base": 1647, "value": 549, "isDiscount": True, "isPercent": False, "isStorno": False}},
           {"sumInCurrency": {"fiscalTotal": 3097, "currency": "PLN"}},
           {"payment": {"type": "2", "amount": 3097, "name": "Karta platnicza", "currency": "PLN"}},
           {"fiscalFooter": {"billNumber": 1, "date": "2030-03-15T16:56:37.000Z"}}]}}


def test_parse_json_exact_items_and_discount():
    p, items, pays, ok = biedronka.parse_json(DOC, "1234567890", "biedronka/json/1234567890.json")
    assert ok and p["total"] == 30.97 and p["date"] == "2030-03-15T17:56:19" and p["source"] == "biedronka"
    assert [(i["name"], i["qty"], i["unit_price"], i["amount"], i["discount"]) for i in items] == [
        ("Ciastka testowe 500 g", 1, 19.99, 19.99, None), ("Napój testowy 0,4l", 3, 5.49, 16.47, 5.49)]
    assert pays == [{"method": "card", "amount": 30.97, "card_last4": None}]


def test_storno_line_is_negative():
    doc = json.loads(json.dumps(DOC))
    doc["eparagon"]["body"].insert(2, {"sellLine": {"name": "Ciastka testowe 500 g C", "price": 1999, "total": 1999,
                                                    "quantity": "1", "isStorno": True}})
    doc["eparagon"]["body"][4]["sumInCurrency"]["fiscalTotal"] = 1098
    _, items, _, ok = biedronka.parse_json(doc, "1", "x")
    assert ok and items[2]["amount"] == -19.99


def test_json_replaces_receipt_recognized_from_pdf(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(biedronka, "ACCOUNT", tmp_path)
    monkeypatch.setattr(biedronka, "DATA", tmp_path)
    monkeypatch.setattr(biedronka, "DOWNLOADS", tmp_path / "downloads")
    (tmp_path / "downloads").mkdir()
    (tmp_path / "json").mkdir()
    pdf = tmp_path / "downloads" / "a.pdf"
    pdf.write_bytes(b"%PDF-test")
    (tmp_path / "json" / "1234567890.json").write_text(json.dumps(DOC), encoding="utf-8")
    con = db.connect()
    old = "photo:" + hashlib.sha1(pdf.read_bytes()).hexdigest()[:16]
    db.save_purchase(con, {"id": old, "source": "photo", "date": "2030-03-15T17:56:00", "merchant": "Biedronka", "total": 30.97},
                     [{"name": "Clastka test0we", "qty": 1, "unit_price": 19.99, "amount": 19.99}])
    info = {"filename": "a.pdf", "date": "2030-03-15", "json": "json/1234567890.json"}
    assert biedronka.import_json(con, "1234567890", info) is False  # не новый чек — замена распознанного
    ids = [r[0] for r in con.execute("SELECT id FROM purchases")]
    assert ids == ["biedronka:1234567890"]
    assert con.execute("SELECT path FROM attachments WHERE purchase_id = ?", (ids[0],)).fetchone()[0] == "downloads/a.pdf"
