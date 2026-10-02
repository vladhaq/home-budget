"""Эталон словаря товаров: названия из реальных чеков (проверены вручную) -> ключ категории.

Любая правка KEYWORDS должна сохранять эти ответы. Новое исправление — сначала строка сюда, потом правило.
  python -m pytest tests
"""
import pytest

from core.categories import TREE, keyword_category, normalize, note_category

GOLDEN = [
    # алкоголь
    ("SomersbyMandarin0,4l", "vice.alcohol"), ("Książęce IPA Piwo", "vice.alcohol"), ("Desper.Orig.piwo", "vice.alcohol"),
    ("ŁomżaBrzoskwiniaMorela0,5l", "vice.alcohol"), ("Redds Żurawina piwo", "vice.alcohol"),
    # электроника и техника
    ("Inteligentne gniazdko Xiaomi Smart Plug 2 WiFi", "goods.electronics"),
    ("Powerbank UGREEN PB720 20000 mAh 100W Szary", "goods.electronics"),
    ("Szkło ochronne z ramką Bizon do Redmi Note 15 Pro 5G, szybka na ekran 2 szt", "goods.electronics"),
    ("Opaska Xiaomi Smart Band 10 Inteligentna Bransoletka Czarna Midnight Black", "goods.electronics"),
    ("MAGNETYCZNY KABEL ONEPLUS 10A USB TYP C - USB TYP C 100W CZERWONY SZYBKI", "goods.electronics"),
    ("Motorola g42 6GB/128GB", "goods.electronics"),
    ("OnePlus 11 8gb 128gb black", "goods.electronics"),
    ("Jack & Jones Szorty jeansowe - blue denim", "goods.clothes"),
    ("Blend Denim shorts - denim white", "goods.clothes"),
    ("Odkurzacz piorący THOMAS Vestfalia XT", "goods.appliances"),
    ("Oczyszczacz powietrza XIAOMI Air Purifier 6", "goods.appliances"),
    # дом
    ("Zmywaki kuchenneMAXI", "home.chem"), ("Ścierecz.z mikrof.", "home.chem"), ("K.PłynWCWhite750ml", "home.chem"),
    ("Worki 25l z uchwyt.", "home.chem"), ("Odkamieniacz do ekspresów Bosch Tassimo (2 cykle)", "home.chem"),
    ("Wykałaczki 6,5 cm", "home.chem"),
    ("Suszarka Wisząca do Naczyń 50 x 22 Czarna Ociekacz Kuchenny Wysuwana # V2", "home.furniture"),
    ("Meble biurko komputerowe stolik 96cm N-35 ANTRACYT", "home.furniture"), ("Podgrzewacze30", "home.furniture"),
    ("Cichy wentylator kanałowy Berger Ventilatoren fi 250/1450m3/h, solidny", "home.repair"),
    ("Uszczelka do drzwi zewnętrznych DEVENTER S6512a czarna 1m", "home.repair"),
    ("TAŚMA PAKOWA 3SZT", "home.repair"), ("Deska sedesowa do Arbo Primo wolnoopadająca twarda", "home.repair"),
    ("Nowy Nawiewnik Ścienny Brookvent Regulator Aquwall fi 125mm", "home.repair"), ("WkretPlas1x4,5x100mm", "home.repair"), ("ZesNozy5mZapasOstrza", "home.repair"),
    # еда
    ("KetchupPikantny650G", "food.grocery"), ("Cukier KryształBiały", "food.grocery"), ("OSoleSólMorska1kg", "food.grocery"),
    ("Przypr.d.kurcz. 80g", "food.grocery"), ("KnorrPDMRozmaryn23g", "food.grocery"),
    ("Pyzy z mięsem 1kg", "food.ready"), ("STD naleśniki 400 g", "food.ready"), ("RoltonZupaPikKur60g", "food.ready"),
    ("Parówki z szynki XXL", "food.sausage"), ("STD.SalamiOstródzkie", "food.sausage"), ("Duda pasztetowa", "food.sausage"),
    ("Mokate Napój kawowy", "food.coffee"), ("Kapsułki do Tassimo Jacobs Cafe au Lait 16 szt.", "food.coffee"),
    ("Inka Kawa zboż.czek.", "food.coffee"),
    ("SonkoRyżBiałyKarton", "food.grains"), ("K.MakaronKolanko500", "food.grains"), ("KLC.KaszaGry4x100g", "food.grains"),
    ("MüllermilZeroMalina", "food.dairy"), ("MlekpolKefir1l", "food.dairy"), ("Jogurt owocowy 9%", "food.dairy"),
    ("MüllermZeroCiasteczk", "food.dairy"), ("MENProteinSkyr pista", "food.dairy"),
    ("Jajka ściółkowe L", "food.dairy"), ("JajaŚciółkoweL10", "food.dairy"),
    ("Mięso miel.z sz.XXL", "food.meat"), ("Mięso mielone,szynka", "food.meat"), ("K-SMFiletZPiersiKurc", "food.meat"),
    ("Pasta rybna", "food.meat"),
    ("Nap.energ.Monster", "food.drinks"), ("FreewayColaZERO 2l", "food.drinks"), ("HerbapolSyropPoma420", "food.drinks"),
    ("Pomidory kiścio. luz", "food.produce"), ("Cebula czerwona", "food.produce"), ("Banany Premium", "food.produce"),
    ("Mandarynki Select 750g", "food.produce"),
    ("K.CzekolMleczna100g", "food.sweets"), ("HariboSquidgies160g", "food.sweets"), ("Konafetto wafle", "food.sweets"),
    ("K.CzekOrzech100g", "food.sweets"), ("BKTarta cytrynowa 900ml", "food.sweets"), ("7DAysWaniliaWiśn110g", "food.sweets"),
    ("Kfav.Migdały200g", "food.snacks"), ("L.Chipsy fromage130g", "food.snacks"), ("CrispersOrzZiemnCheddar125g", "food.snacks"),
    ("KrestoMangoLiofilizowane15", "food.snacks"),
    ("Gouda kawałek", "food.cheese"), ("Ser Camemb. z zioł.", "food.cheese"), ("K.GoudaPlastry500g", "food.cheese"),
    ("Chleb pszenno-żytni", "food.bread"), ("Bagietka", "food.bread"), ("Tortilla pszenna 6 szt", "food.bread"),
    ("Pączek wieloowocowy", "food.pastry"), ("Pączek likier jajeczny 80g", "food.pastry"), ("DonutCzekolada55g", "food.pastry"),
    ("7DaysRogaliki185G", "food.pastry"), ("Drożdżówka z mak120g", "food.pastry"), ("BułeczkiZCzekol400G", "food.pastry"),
    ("Gifflar Cynamonki 260g", "food.pastry"), ("Babka jagodowa 500g", "food.pastry"),
    # здоровье
    ("StrepsilsHerbal16tab", "health.pharmacy"), ("Apteczka ABC Woda Utleniona 3%, 100 ml", "health.pharmacy"),
    ("Magnez tabletki", "health.vitamins"), ("Multiwitamina tabl.", "health.vitamins"),
    ("Moller's Tran norweski cytrynowy płyn 250 ml", "health.vitamins"),
    ("Moller's tran norweski o aromacie cytrynowym 250", "health.vitamins"),
    ("Pantene Szampon", "health.hygiene"), ("Garnier Krem do rąk", "health.hygiene"), ("CleanicPatyczki160sz", "health.hygiene"),
    # прочее
    ("Zeszyt A4", "education"), ("Dostawa: Allegro One Box", "other.delivery"), ("Kurier w następny dzień roboczy", "other.delivery"),
    ("Puszka zwrotna ALU", "other.deposit"), ("Usługa Allegro Smart! 12 miesięcy", "comms.subscriptions"),
    ("Koszt płatności", "finance.bank"),
]


@pytest.mark.parametrize("name,key", GOLDEN)
def test_golden(name, key):
    assert keyword_category(name) == key, normalize(name)


def test_keys_exist():
    keys = {g for g, *_ in TREE} | {c for *_, children in TREE for c, _ in children}
    from core import categories as C
    used = {k for k, _ in C.KEYWORDS} | {k for k, _ in C.NOTE_KEYWORDS} | set(C.SECTIONS.values()) \
        | set(C.MERCHANT_STRICT.values()) | set(C.MERCHANT_FALLBACK.values())
    from core import reconcile as R
    used |= {k for _, k in R.BANK_RULES} | {k for _, k in R.INCOME_RULES}
    assert used <= keys, used - keys


@pytest.mark.parametrize("note,key", [
    ("обезболивающее", "health.pharmacy"), ("отвертка", "home.repair"), ("кофе с собой", "leisure.cafe"),
    ("билет на поезд в Брест", "transport.intercity"), ("проездной", "transport.city"), ("пылесос", "goods.appliances"),
    ("помощь отцу", "leisure.gifts"), ("пончики", "food.pastry"), ("хлеб", "food.bread"), ("śrubokręt", "home.repair"),
])
def test_notes(note, key):
    assert note_category(note) == key


@pytest.mark.parametrize("name,key", [
    ("ALDI Sp. z o.o. 001", "aldi"), ("ALDI Sp. z o.o. 002", "aldi"), ("Action A100", "action"), ("PIZZERIA ROMA SP Z O O", "pizzeria roma"),
    ("PIZZERIA ROMA 02", "pizzeria roma"), ("JAN KOWALSKI UL. DŁUGA 3/4", "jan kowalski"), ("ZABKA Z1234 K.1", "zabka"),
])
def test_shop_key(name, key):
    from core.categories import shop_key
    assert shop_key(name) == key
