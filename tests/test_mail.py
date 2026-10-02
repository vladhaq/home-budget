"""Разбор писем магазинов: товары из блока заказа (выдуманные письма в формате настоящих)."""
from receipts.mail_orders import parse_generic


def lines(text):
    return [ln.strip() for ln in text.strip().splitlines() if ln.strip()]


def test_idosell_qty_next_line():  # IdoSell: «Ilość:» / «1 szt.»
    p = parse_generic(lines("""
        zamówienie numer 123456 zostało już spakowane i oczekuje na kuriera.
        ZAMÓWIONE PRODUKTY
        Telefon Przykładowy 8/256GB
        256 GB 8 GB Szary
        Ilość:
        1 szt.
        Rozmiar:
        uniwersalny
        999,00 zł
        Kurier odbiera przesyłki około godziny 15.
    """), "Przygotowaliśmy zamówienie do wysłania")
    assert p["order"] == "123456"
    assert [(i["name"], i["qty"], i["amount"]) for i in p["items"]] == [("Telefon Przykładowy 8/256GB", 1.0, 999.0)]


def test_idosell_stops_at_totals():  # DOZ: после товаров — итоги, это не товары
    p = parse_generic(lines("""
        Zamówione produkty
        Lek A, 25 mg, 25 szt.
        1
        x 16.49 zł
        16.49 zł
        Lek B, 150 mg, 30 szt
        2
        x 50.00 zł
        100.00 zł
        Sposób płatności
        Płatność przy odbiorze
        Wartość produktów
        116.49 zł
        Łącznie
        116.49 zł
        Numer zamówienia
        Z12345678
    """), "Zarejestrowaliśmy Twoje zamówienie")
    assert [(i["name"], i["qty"], i["amount"]) for i in p["items"]] == [("Lek A, 25 mg, 25 szt.", 1.0, 16.49),
                                                                       ("Lek B, 150 mg, 30 szt", 2.0, 100.0)]
    assert p["total"] == 116.49


def test_order_info_qty_same_line_and_delivery():
    p = parse_generic(lines("""
        INFORMACJE O ZAMÓWIENIU NR - 525000
        Buty sportowe Przykład białe
        Rozmiar: 44,5
        Ilość: 2 szt.
        259,98 zł
        Dostawa:
        Kurier
        Koszt dostawy:
        10,99 zł
        Razem do zapłaty:
        270,97 zł brutto
    """), "Złożyłeś zamówienie nr 525000")
    assert [(i["name"], i["qty"], i["amount"]) for i in p["items"]] == [("Buty sportowe Przykład białe", 2.0, 259.98),
                                                                       ("Dostawa", 1, 10.99)]
    assert p["total"] == 270.97


def test_brand_name_qty_blocks():  # Zalando: «марка» / «название» / «Size: S» / «Quantity: 1» / цена
    p = parse_generic(lines("""
        Order number
        10900000000001
        Standard Delivery
        Fri, 12 Jun 2026 - Mon, 15 Jun 2026
        Marka
        Szorty - niebieskie
        Size: S
        Quantity: 1
        126,00 zł
        Complete the look
        Standard Delivery
        Mon, 15 Jun 2026 - Tue, 16 Jun 2026
        Marka
        Szorty - białe
        Size: S
        Quantity: 2
        298,00 zł
        Payment method
        BLIK
        Total
        vat incl.
        424,00 zł
    """), "Thanks for your order")
    assert p["order"] == "10900000000001" and p["total"] == 424.0 and p["payment"] == "blik"
    assert [(i["name"], i["qty"], i["amount"]) for i in p["items"]] == [("Marka Szorty - niebieskie", 1.0, 126.0),
                                                                       ("Marka Szorty - białe", 2.0, 298.0)]


def test_brand_qty_next_line_and_shipping():  # Modivo: код товара, «Rozmiar odzieży:» / «S», «Ilość:» / «1», «Przesyłka»
    p = parse_generic(lines("""
        Numer zamówienia: ABC000000001
        Metoda płatości: BLIK
        Marka
        0000300000000
        Rozmiar odzieży:
        S
        Ilość:
        1
        69,99 zł
        Suma
        69,99 zł
        Przesyłka
        9,99 zł
        Łącznie (z VAT)
        79,98 zł
    """), "Nowe zamówienie nr ABC000000001")
    assert [(i["name"], i["amount"]) for i in p["items"]] == [("Marka", 69.99), ("Dostawa", 9.99)]
    assert p["total"] == 79.98 and p["payment"] == "blik"


def test_payment_description_and_order_ref():
    from receipts.mail_orders import parse_payment
    olx = parse_payment(["zarejestrowaliśmy zlecenie płatności dla GRUPA OLX SP. Z O.O.", "Opis płatności:",
                         "Telefon Przykład 8gb 128gb", "Kwota:", "1 007,63 PLN"], "Sprawdź status swojej płatności (5500000000)")
    assert olx["merchant"] == "OLX" and olx["items"][0]["name"] == "Telefon Przykład 8gb 128gb" and olx["refs"] == []
    p24 = parse_payment(["Odbiorca wpłaty:", "Zalando Payments GmbH", "Kwota transakcji:", "135,95 PLN", "Opis:",
                         "10900000000001"], "Nowa transakcja płatnicza (P24-AAA-BBB-CCC)")
    assert p24["order"] == "P24-AAA-BBB-CCC" and p24["refs"] == ["10900000000001"]
    assert p24["items"][0]["name"] == "Оплата: Zalando"  # номер заказа — не название


def test_table_rows_and_vat_is_not_total():  # «Produkt / Ilość / Kwota»; «Kwota VAT» — не итог
    p = parse_generic(lines("""
        Produkt
        Ilość
        Kwota
        Lek A 60 mg, 28 kapsułek
        1
        28,78 zł
        Lek B 25 mg, 25 tabletek
        2
        23,98 zł
        Kwota VAT:
        3,91 zł
        Łącznie
        52,76 zł
    """), "Potwierdzenie złożenia zamówienia")
    assert [(i["name"], i["qty"], i["amount"]) for i in p["items"]] == [("Lek A 60 mg, 28 kapsułek", 1.0, 28.78),
                                                                       ("Lek B 25 mg, 25 tabletek", 2.0, 23.98)]
    assert p["total"] == 52.76


def test_rows_with_details_and_old_price():  # «название» / «Cena jednostkowa» / «Numer modelu» / «1» / цена / старая цена
    p = parse_generic(lines("""
        Podsumowanie zamówienia
        Zestaw noży - 3 elementy
        Cena jednostkowa 69,00 zł
        Numer modelu: K000
        1
        34,50 zł
        69,00 zł
        Patelnia 28cm
        Cena jednostkowa 99,00 zł
        Numer modelu: C000
        1
        99,00 zł
        Kwota VAT:
        24,96 zł
        Suma (razem z VAT)
        133,50 zł
    """), "Potwierdzenie zamówienia")
    assert [(i["name"], i["amount"]) for i in p["items"]] == [("Zestaw noży - 3 elementy", 34.5), ("Patelnia 28cm", 99.0)]
    assert p["total"] == 133.5


def test_qty_then_availability_and_delivery_with_payment():
    p = parse_generic(lines("""
        Produkt
        Cena
        Korek klik-klak okrągły, chrom
        Ilość: 1
        Dostępność:
        W magazynie
        34,99 zł
        Wartość koszyka:
        34,99 zł
        Dostawa i płatność:
        12,99 zł
        Rabat:
        0,00 zł
        Razem do zapłaty:
        47,98 zł
    """), "Potwierdzenie zamówienia")
    assert [(i["name"], i["amount"]) for i in p["items"]] == [("Korek klik-klak okrągły, chrom", 34.99), ("Dostawa", 12.99)]


def test_visit_service_is_item():
    p = parse_generic(lines("""
        Twoja rezerwacja wizyty została potwierdzona oraz opłacona.
        Usługa:
        USG jamy brzusznej
        Data:
        2026-01-01
        Kwota:
        250.00 PLN
    """), "Potwierdzenie wizyty")
    assert [(i["name"], i["amount"]) for i in p["items"]] == [("USG jamy brzusznej", 250.0)]


def test_order_discount_spread_over_items():  # «Zniżka -X zł» на весь заказ — делится на позиции, итог сходится
    p = parse_generic(lines("""
        Pozycje
        Ilość
        Cena
        Badanie A
        ul. Przykładowa 1
        1
        60,00 zł
        Badanie B
        ul. Przykładowa 1
        1
        40,00 zł
        Suma częściowa
        100,00 zł
        Zniżka (KOD)
        -25,00 zł
        Suma łączna (z podatkiem)
        75,00 zł
    """), "Potwierdzenie opłaty zamówienia")
    assert [(i["name"], i["amount"], i["discount"]) for i in p["items"]] == [("Badanie A", 60.0, 15.0), ("Badanie B", 40.0, 10.0)]
    assert p["total"] == 75.0


def test_payment_refs_blik_and_footer_krs():
    from receipts.mail_orders import parse_payment
    tpay = parse_payment(["Kwota", "2,00 PLN"], "Transaction confirmation TR-AAA-BBBBBBX/88000000001 for jakdojade.pl - "
                                                 "UM Wrocław - 30-minutowy at City-nav Sp. z o.o.")
    assert tpay["order"] == "TR-AAA-BBBBBBX" and tpay["pay_ref"] == "88000000001" and tpay["strong_ref"]
    # номер транзакции — из строки «Identyfikator transakcji», а не KRS из подвала (он у всех платежей один)
    autopay = parse_payment(["Przekazaliśmy Twoją płatność", "Odbiorca:", "Uczelnia", "Identyfikator transakcji:", "ABC123XYZ",
                             "Kwota transakcji:", "450,00 PLN", "Łączna kwota:", "451,00 PLN", "KRS pod nr 0000300000"], "Przekazaliśmy")
    assert autopay["order"] == "ABC123XYZ" and autopay["total"] == 451.0 and not autopay["strong_ref"] and autopay["confirmed"]
    ekspres = parse_payment(["Transakcja została przekazana do wypłaty", "ID transakcji:", "170000000", "Kwota transakcji:",
                             "239.00 PLN", "Tytuł:", "FV1 Czesne", "Nr KRS", "0000300000"],
                            "Ekspres Przelewy24 - Powiadomienie o przekazaniu transakcji do realizacji")
    assert ekspres["order"] == "170000000" and ekspres["items"][0]["name"] == "FV1 Czesne"
    assert not ekspres["confirmed"]  # платить мог другой человек — подтверждает только списание в выписке
