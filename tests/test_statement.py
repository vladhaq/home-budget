"""Выписки из файлов: CSV разных банков, MT940, camt.053 — по образцам из открытых описаний форматов, данные выдуманы."""
import json

import pytest

from bank import statement as S


def write(tmp_path, name, text, enc="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(enc))
    return p


def rows(st):
    return [(t.date, t.amount, t.type, t.counterparty, t.balance) for t in st.rows]


IPKO = '''"Data operacji","Data waluty","Typ transakcji","Kwota","Waluta","Saldo po transakcji","Opis transakcji","","",""
"2026-10-02","2026-10-02","Płatność kartą","-38.17","PLN","+2849.33","Tytuł: 000000001 00000002","Lokalizacja: Adres: SKLEP PRZYKLAD Miasto: WROCLAW Kraj: POLSKA","Numer karty: 400000******0000",""
"2026-10-01","2026-10-01","Przelew na rachunek","+2000.00","PLN","+2887.50","Rachunek nadawcy: 61 1090 1014 0000 0712 1981 2874","Nazwa nadawcy: FIRMA PRZYKLAD SP Z O O","Tytuł: WYNAGRODZENIE 09.2026",""
"2026-09-30","2026-09-30","Płatność web - kod mobilny","-12.50","PLN","+887.50","Tytuł: 00000000000000001","Numer telefonu: 48500000000","Lokalizacja: Adres: www.example.pl",""
"2026-09-30","2026-09-30","Wypłata z bankomatu","-100.00","PLN","+900.00","Tytuł: 0000123","Lokalizacja: Adres: BANKOMAT Miasto: LODZ","",""
'''


def test_ipko_csv(tmp_path):  # windows-1250, «новые сверху», подробности — в безымянных ячейках «Ключ: значение»
    st = S.parse(write(tmp_path, "historia.csv", IPKO, "cp1250"))
    assert rows(st) == [
        ("2026-09-30", -100.0, "CARD-ATM", None, 900.0),
        ("2026-09-30", -12.5, "MOBILE-PAYMENT-POS-NO-CARD-TX-CODE", None, 887.5),
        ("2026-10-01", 2000.0, "TRANSFER-IN", "FIRMA PRZYKLAD SP Z O O", 2887.5),
        ("2026-10-02", -38.17, "CARD-PAYMENT", None, 2849.33)]
    assert "SKLEP PRZYKLAD WROCLAW" in st.rows[3].description
    assert st.rows[2].description == "WYNAGRODZENIE 09.2026" and "tel. 48500000000" in st.rows[1].description


MBANK = '''mBank S.A. Bankowość Detaliczna;
#Za okres:;
01.09.2026;30.09.2026;

#Data operacji;#Opis operacji;#Rachunek;#Kategoria;#Kwota;#Saldo po operacji;
2026-09-29;"SKLEP PRZYKLAD  ZAKUP PRZY UŻYCIU KARTY";"eKonto 1111";"Jedzenie";-45,90 PLN;1 954,10 PLN;
2026-09-28;"BLIK ZAKUP E-COMMERCE  SKLEP.PL";"eKonto 1111";"Zakupy";-54,00 PLN;2 000,00 PLN;
#Saldo końcowe;;;;1 954,10 PLN;
'''


def test_mbank_csv_preamble_and_polish_amounts(tmp_path):
    st = S.parse(write(tmp_path, "mbank.csv", MBANK, "cp1250"))
    assert rows(st) == [("2026-09-28", -54.0, "MOBILE-PAYMENT-POS-NO-CARD-TX-CODE", None, 2000.0),
                        ("2026-09-29", -45.9, "CARD-PAYMENT", None, 1954.1)]


MILLENNIUM = '''Numer rachunku/karty,Data transakcji,Data rozliczenia,Rodzaj transakcji,Na konto/Z konta,Odbiorca/Zleceniodawca,Opis,Obciążenia,Uznania,Saldo,Waluta
PL00 1160 0000 0000,2026-09-15,2026-09-15,PŁATNOŚĆ KARTĄ,,,SKLEP TEST WARSZAWA,-23.40,,976.60,PLN
PL00 1160 0000 0000,2026-09-14,2026-09-14,PRZELEW PRZYCHODZĄCY,PL61109010140000071219812874,Anna Testowa,ZWROT ZA BILETY,,100.00,1000.00,PLN
'''


def test_millennium_debit_credit_columns(tmp_path):
    st = S.parse(write(tmp_path, "mill.csv", MILLENNIUM))
    assert rows(st) == [("2026-09-14", 100.0, "TRANSFER-IN", "Anna Testowa", 1000.0),
                        ("2026-09-15", -23.4, "CARD-PAYMENT", None, 976.6)]


REVOLUT = '''Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance
CARD_PAYMENT,Current,2026-09-10 12:00:00,2026-09-11 09:00:00,Shop Example,-10.00,0.50,PLN,COMPLETED,89.50
TOPUP,Current,2026-09-09 10:00:00,2026-09-09 10:00:01,Top-Up by *1234,100.00,0.00,PLN,COMPLETED,100.00
CARD_PAYMENT,Current,2026-09-12 12:00:00,,Pending Shop,-5.00,0.00,PLN,PENDING,
'''


def test_revolut_fee_and_pending(tmp_path):  # комиссия входит в списание, неподтверждённые — пропускаются
    st = S.parse(write(tmp_path, "revolut.csv", REVOLUT))
    assert rows(st) == [("2026-09-09", 100.0, "TRANSFER-IN", None, 100.0),
                        ("2026-09-11", -10.5, "CARD-PAYMENT", None, 89.5)]


HEADERLESS = '''2026-09-01,2026-09-30,'PL00109000000000000000000000',WYCIAG
30-09-2026,30-09-2026,PRZELEW ZA CZYNSZ,Anna Testowa,'PL61109010140000071219812874',-1900.00,100.00,1
29-09-2026,29-09-2026,ZAKUP KARTA SKLEP,SKLEP TEST,,-50.00,2000.00,2
28-09-2026,28-09-2026,WYNAGRODZENIE,FIRMA TEST,'PL61109010140000071219812874',2000.00,2050.00,3
'''


def test_csv_without_header_by_content(tmp_path):  # колонки — по содержимому: даты, сумма со знаком, остаток, текст
    st = S.parse(write(tmp_path, "santander.csv", HEADERLESS))
    assert st.format == "CSV без заголовка"
    assert [(t.date, t.amount, t.balance, t.counterparty) for t in st.rows] == [
        ("2026-09-28", 2000.0, 2050.0, "FIRMA TEST"), ("2026-09-29", -50.0, 2000.0, "SKLEP TEST"),
        ("2026-09-30", -1900.0, 100.0, "Anna Testowa")]
    assert st.rows[2].description == "PRZELEW ZA CZYNSZ"


MT940_PL = ''':20:MT940
:25:/PL61109010140000071219812874
:28C:1
:60F:C260930PLN1000,00
:61:2610011001D38,17N073NONREF//1234
073 0
:86:073~00073
~20PŁATNOŚĆ KARTĄ 38,17 PLN
~21SKLEP TEST WROCLAW
~22˙
~3010205561
~310000000000000000
~32SKLEP TEST
~33˙
~38PL61109010140000071219812874
~60˙
~63˙
:61:2610021002C2000,00N051NONREF//5678
051 0
:86:051~00051
~20WYNAGRODZENIE
~21˙
~32FIRMA TESTOWA SP Z O O 01-456 WAR
~33SZAWA UL. TESTOWA 1
:62F:C261002PLN2961,83
-
'''


def test_mt940_polish_subfields(tmp_path):
    st = S.parse(write(tmp_path, "wyciag.sta", MT940_PL, "cp1250"))
    assert st.iban == "PL61109010140000071219812874"
    assert rows(st) == [("2026-10-01", -38.17, "CARD-PAYMENT", "SKLEP TEST", 961.83),
                        ("2026-10-02", 2000.0, "TRANSFER-IN", "FIRMA TESTOWA SP Z O O 01-456 WARSZAWA UL. TESTOWA 1", 2961.83)]
    assert st.rows[0].description == "PŁATNOŚĆ KARTĄ 38,17 PLN SKLEP TEST WROCLAW"


MT940_DE = ''':20:STARTUMSE
:25:20041111/1234567890
:28C:00001/001
:60F:C261001EUR500,00
:61:2610021002DR12,34NMSCNONREF
:86:106?00KARTENZAHLUNG?20SVWZ+Supermarkt Test?32SUPERMARKT TEST
:62F:C261002EUR487,66
-
'''


def test_mt940_german_subfields(tmp_path):
    st = S.parse(write(tmp_path, "umsatz.sta", MT940_DE))
    assert rows(st) == [("2026-10-02", -12.34, "CARD-PAYMENT", "SUPERMARKT TEST", 487.66)]


CAMT = '''<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt>
 <GrpHdr><MsgId>1</MsgId><CreDtTm>2026-10-03T08:00:00</CreDtTm></GrpHdr>
 <Stmt><Id>1</Id><Acct><Id><IBAN>AT611904300234573201</IBAN></Id><Ccy>EUR</Ccy></Acct>
  <Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt Ccy="EUR">100.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2026-10-01</Dt></Dt></Bal>
  <Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="EUR">135.50</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2026-10-02</Dt></Dt></Bal>
  <Ntry><Amt Ccy="EUR">14.50</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>BOOK</Sts><BookgDt><Dt>2026-10-01</Dt></BookgDt>
   <BkTxCd><Domn><Cd>PMNT</Cd><Fmly><Cd>CCRD</Cd><SubFmlyCd>POSD</SubFmlyCd></Fmly></Domn></BkTxCd>
   <NtryDtls><TxDtls><RltdPties><Cdtr><Nm>Supermarkt Test</Nm></Cdtr></RltdPties>
    <RmtInf><Ustrd>POS 1234 Supermarkt Test Wien</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
  <Ntry><Amt Ccy="EUR">50.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts><BookgDt><Dt>2026-10-02</Dt></BookgDt>
   <BkTxCd><Domn><Cd>PMNT</Cd><Fmly><Cd>RCDT</Cd><SubFmlyCd>ESCT</SubFmlyCd></Fmly></Domn></BkTxCd>
   <NtryDtls><TxDtls><RltdPties><Dbtr><Pty><Nm>Anna Testowa</Nm></Pty></Dbtr></RltdPties>
    <RmtInf><Ustrd>Rueckzahlung</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
  <Ntry><Amt Ccy="EUR">9.99</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>PDNG</Sts><BookgDt><Dt>2026-10-03</Dt></BookgDt></Ntry>
 </Stmt></BkToCstmrStmt></Document>
'''


def test_camt053(tmp_path):  # коды ISO -> коды PKO, остаток от начального, ожидающие — пропускаются
    st = S.parse(write(tmp_path, "auszug.xml", CAMT))
    assert st.iban == "AT611904300234573201" and st.currency == "EUR"
    assert rows(st) == [("2026-10-01", -14.5, "CARD-PAYMENT", "Supermarkt Test", 85.5),
                        ("2026-10-02", 50.0, "TRANSFER-IN", "Anna Testowa", 135.5)]


@pytest.mark.parametrize("s,v", [("-3 283,33 PLN", -3283.33), ("1.234,56", 1234.56), ("1,234.56", 1234.56),
                                 ("+2000.00", 2000.0), ("(12.50)", -12.5), ("−7,99 zł", -7.99), ("abc", None), ("", None)])
def test_amounts(s, v):
    assert S.amount_of(s) == v


def test_fresh_dedups_same_amount_nearby():  # та же сумма ±3 дня — уже есть; каждая имеющаяся — только для одной строки
    existing = [("2026-10-01", -38.17), ("2026-10-03", -12.5)]
    new, dup = S.fresh(existing, [S.Tx("2026-10-02", -38.17), S.Tx("2026-10-07", -12.5), S.Tx("2026-10-02", -38.17)])
    assert dup == 1 and [(t.date, t.amount) for t in new] == [("2026-10-07", -12.5), ("2026-10-02", -38.17)]


def test_import_into_linked_account(tmp_path, monkeypatch):
    """Файл без номера счёта при одном подключённом счёте — тот же счёт: операции, уже пришедшие из банка,
    не дублируются (дата в банке на день позже), новые получают его префикс и порядок для остатка."""
    from core import db
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    con = db.connect()
    con.execute("INSERT INTO bank_tx (id, date, amount, balance, type, description, raw) VALUES (?,?,?,?,?,?,?)",
                ("abcd1234:O;1", "2026-10-03", -38.17, 2849.33, "CARD-PAYMENT", "WROCLAWSKLEP PRZYKLADPL",
                 json.dumps({"debtor_account": {"iban": "PL61109010140000071219812874"}})))
    r = S.import_file(write(tmp_path, "historia.csv", IPKO, "cp1250"), con)
    assert (r["new"], r["dup"], r["account"]) == (3, 1, "подключённый счёт (в файле нет номера — считаю, что тот же)")
    ids = [x[0] for x in con.execute("SELECT id FROM bank_tx WHERE id LIKE 'abcd1234:F%' ORDER BY date")]
    assert len(ids) == 3 and all(";" in i for i in ids)
    again = S.import_file(write(tmp_path, "historia2.csv", IPKO, "cp1250"), con)
    assert again["new"] == 0 and again["dup"] == 4  # повторный файл — ничего нового
