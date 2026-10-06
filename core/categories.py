"""Категории: дерево, словари ключевых слов, назначение категорий позициям.

У системных категорий постоянный ключ (food.meat, transfer.atm, ...): код и словари обращаются к категориям
по ключу, поэтому переименование и перенос в интерфейсе ничего не ломают. Твои новые категории — без ключа.

Приоритет: ручная правка позиции > комментарий > правило по коду товара > правило по названию > словарь и подсказки.
Всё, что не распознано, остаётся без категории — очередь «Неопознанные» в интерфейсе.
"""
import datetime as dt
import json
import re
from collections import Counter

from core.common import fold, local_pairs

# Структура «категория › подкатегория». (ключ группы, название, вид, [(ключ, название), ...])
TREE = [
    ("food", "Еда", "expense", [
        ("food.meat", "Мясо и рыба"), ("food.sausage", "Колбасы и нарезка"), ("food.dairy", "Молочное и яйца"),
        ("food.cheese", "Сыр"), ("food.bread", "Хлеб"), ("food.pastry", "Сладкая выпечка"),
        ("food.produce", "Овощи и фрукты"), ("food.grains", "Крупы и макароны"), ("food.ready", "Готовая еда и заморозка"),
        ("food.sweets", "Сладкое"), ("food.snacks", "Снеки и орехи"), ("food.drinks", "Напитки"),
        ("food.coffee", "Кофе и чай"), ("food.grocery", "Бакалея, соусы, специи"), ("food.nocheck", "Продукты без чека")]),
    ("vice", "Алкоголь и табак", "expense", [("vice.alcohol", "Алкоголь"), ("vice.tobacco", "Табак и вейп")]),
    ("home", "Дом и быт", "expense", [
        ("home.chem", "Бытовая химия и хозтовары"), ("home.furniture", "Мебель, посуда, интерьер"),
        ("home.repair", "Ремонт и инструменты")]),
    ("health", "Здоровье и красота", "expense", [
        ("health.pharmacy", "Аптека"), ("health.vitamins", "Витамины и добавки"), ("health.doctors", "Врачи и анализы"),
        ("health.hygiene", "Гигиена и косметика")]),
    ("goods", "Вещи и техника", "expense", [
        ("goods.clothes", "Одежда и обувь"), ("goods.electronics", "Электроника"), ("goods.appliances", "Бытовая техника")]),
    ("transport", "Транспорт", "expense", [
        ("transport.city", "Городской транспорт"), ("transport.intercity", "Поезда и междугородние"),
        ("transport.taxi", "Такси"), ("transport.fuel", "Топливо"), ("transport.bike", "Велосипед и самокат")]),
    ("housing", "Жильё", "expense", [("housing.rent", "Аренда"), ("housing.utilities", "Коммунальные")]),
    ("comms", "Связь и подписки", "expense", [("comms.mobile", "Связь и интернет"), ("comms.subscriptions", "Подписки")]),
    ("leisure", "Досуг", "expense", [
        ("leisure.cafe", "Кафе и доставка еды"), ("leisure.fun", "Развлечения"), ("leisure.gifts", "Подарки и помощь")]),
    ("education", "Образование", "expense", []),
    ("finance", "Финансы", "expense", [("finance.bank", "Банк и комиссии"), ("finance.taxes", "Налоги и сборы")]),
    ("other", "Прочее", "expense", [
        ("other.deposit", "Залог за тару"), ("other.delivery", "Доставка и почта"), ("other.cash", "Наличные без чека")]),
    ("income", "Доходы", "income", [
        ("income.salary", "Зарплата"), ("income.side", "Подработка"), ("income.scholarship", "Стипендия"),
        ("income.cash", "Наличные"), ("income.other", "Прочие доходы")]),
    ("transfer", "Переводы", "transfer", [
        ("transfer.own", "Между своими счетами"), ("transfer.family", "Семья"), ("transfer.out", "Людям"),
        ("transfer.in", "От людей"), ("transfer.debt", "Долги и возвраты"), ("transfer.atm", "Снятие наличных")]),
]

# Перестройка дерева v3: из каких прежних категорий (пути до и после перегруппировки v2) получается категория с ключом.
# Первая найденная — основная (переименовывается), остальные сливаются в неё: позиции, правила, операции переходят.
V3_FROM = {
    "food": ["Еда"], "food.meat": ["Еда/Мясо", "Еда/Рыба"], "food.sausage": ["Еда/Колбасы и нарезка"],
    "food.dairy": ["Еда/Молочное", "Еда/Яйца"], "food.cheese": ["Еда/Сыр"], "food.bread": ["Еда/Хлеб и выпечка"],
    "food.produce": ["Еда/Фрукты", "Еда/Овощи"], "food.grains": ["Еда/Крупы и макароны"],
    "food.ready": ["Еда/Готовая еда и заморозка"], "food.sweets": ["Еда/Сладкое"], "food.snacks": ["Еда/Снеки и орехи"],
    "food.drinks": ["Еда/Напитки"], "food.coffee": ["Еда/Кофе и чай"], "food.grocery": ["Еда/Бакалея, соусы, специи"],
    "vice": ["Алкоголь и табак"], "vice.alcohol": ["Алкоголь"], "vice.tobacco": ["Табак и вейп"],
    "home": ["Дом и быт", "Дом"], "home.chem": ["Бытовая химия и хозтовары"],
    "home.furniture": ["Дом/Мебель и интерьер"], "home.repair": ["Дом/Ремонт", "Дом/Инструменты"],
    "health": ["Здоровье и красота", "Здоровье"], "health.pharmacy": ["Здоровье/Аптека"],
    "health.vitamins": ["Здоровье/Витамины и добавки"], "health.doctors": ["Здоровье/Врачи"],
    "health.hygiene": ["Гигиена и косметика"],
    "goods": ["Вещи и техника"], "goods.clothes": ["Одежда и обувь"], "goods.electronics": ["Электроника"],
    "transport": ["Транспорт"], "transport.city": ["Транспорт/Общественный транспорт"],
    "transport.taxi": ["Транспорт/Такси"], "transport.fuel": ["Транспорт/Топливо"],
    "housing": ["Жильё"], "housing.rent": ["Жильё/Аренда"], "housing.utilities": ["Жильё/Коммунальные"],
    "comms": ["Связь и подписки"], "comms.mobile": ["Связь и интернет"], "comms.subscriptions": ["Подписки"],
    "leisure": ["Досуг"], "leisure.cafe": ["Кафе и доставка"], "leisure.fun": ["Развлечения"], "leisure.gifts": ["Подарки"],
    "education": ["Образование"], "finance": ["Финансы"], "finance.bank": ["Банк и комиссии"],
    "finance.taxes": ["Налоги и сборы"],
    "other": ["Прочее"], "other.deposit": ["Прочее/Залог за тару"], "other.delivery": ["Прочее/Доставка"],
    "other.cash": ["Прочее/Наличные без чека"],
    "income": ["Доходы"], "income.salary": ["Доходы/Зарплата"], "income.side": ["Доходы/Подработка"],
    "income.scholarship": ["Доходы/Стипендия"], "income.cash": ["Доходы/Наличные", "Переводы/Взнос наличных"],
    "income.other": ["Доходы/Прочие доходы"],
    "transfer": ["Переводы"], "transfer.own": ["Переводы/Между своими счетами"], "transfer.out": ["Переводы/Людям"],
    "transfer.in": ["Переводы/От людей"], "transfer.atm": ["Переводы/Снятие наличных"],
}

# Порядок важен: первое совпадение побеждает. Шаблоны — по названию без диакритик (fold).
KEYWORDS = [
    ("other.deposit", r"zwrotn|kaucj|opakowania zwrot"),
    ("other.delivery", r"^dostawa|^kurier w nast|^przesylka kurier"),
    ("finance.bank", r"^koszt (obslugi )?platnosci|^oplata za (platnosc|pobranie)|^koszt pobrania"),
    ("comms.subscriptions", r"allegro smart|claude pro|subscription|abonament|premium plan"),
    # исключения: слово из «чужой» категории внутри названия (пончик с ликёром, приправа для курицы...)
    ("home.chem", r"odkamieniacz"),  # «Odkamieniacz do ekspresów Tassimo» — не кофе
    ("health.vitamins", r"\btran\b|omega ?3|olej z watroby"),  # рыбий жир «…cytrynowy płyn» — не фрукты и не химия
    ("health.pharmacy", r"woda utlenion|apteczk|bandaz|opatrun"),  # перекись водорода — не напиток
    ("food.dairy", r"muller|\bskyr\b|kefir"),  # «Müllermilch … ciasteczko», «Protein Skyr» — молочное
    ("food.coffee", r"tassimo|kapsulki do kaw|dolce gusto|nespresso"),
    ("food.pastry", r"\bpaczek|\bpaczki|racuch"),
    ("food.grocery", r"stevia|proszek do piec|\bprzyp\b|\bfix\b"),
    # чипсы (и «Chipsy Tortilla», «Crispers … cheddar»), сухофрукты — но не «Redds Żurawina piwo» и не «Rozmaryn suszony»
    ("food.snacks", r"chips|crispers|zuraw(?!.*piw)|rodzynk|liofil|suszone (owoc|sliwk|morel)|daktyl"),
    ("food.ready", r"\bdanie\b|gulasz|po chinsku|\bwok\b|jemy ?jemy|goracy kubek"),
    ("food.snacks", r"wiejskie ziemn|miesz\w* egzot|orze\w* piec"),
    ("food.coffee", r"earl ?grey"),
    ("food.drinks", r"sparkling|ice ?tea"),
    ("vice.alcohol", r"\blomza"),
    ("vice.alcohol", r"\bpiw|piwo|\brum\b|likier|\bwino\b|wodka|whisk|\bgin\b|okocim|zywiec|tyskie|\blech\b|somersby"
                 r"|jager|warka|carlsberg|heineken|desperados|kozel|radler|cydr"),
    ("goods.electronics", r"\betui\b|szklo (na|ochron|hartow)|szklo ochronne|folia ochron|ochrona na ekran|ladowark|supervooc"
                    r"|power ?bank|smart ?band|smart plug|inteligentn\w* gniazd|czujnik temperatur|uchwyt (do|na) (monitor|telefon)"
                    r"|uchwyt stolowy do monitor|adapter vesa|sluchawk|kabel|przewod usb"
                    r"|smartfon|telefon komork|oneplus|motorola|redmi|iphone|\b\d+ ?gb ?/? ?\d+ ?gb\b|\b\d+/\d+ ?gb\b"),
    ("goods.appliances", r"odkurzacz|oczyszczacz powietrz|pralk|lodowk|zmywark|mikrofal|czajnik|toster|blender|zelazk"
                         r"|frytkownic|ekspres do kaw|robot (kuchen|sprzat)|suszarka do wlos|nawilzacz|grzejnik"),
    ("home.repair", r"uszczelk|wentylator|nawiewnik|deska sedesow|regulator obrot|wago|zlaczk|szybkozlaczk|tester napiec|przewod przylacz"
                   r"|tasma dwustron|izopropyl|filtr .*antyalerg|podkladki filc"),
    ("home.furniture", r"biurko|\bmeble\b|polka|wieszak|suszarka (wiszac|do naczyn)|ociekacz|lina jutow"),
    ("home.chem", r"rekawice nitryl"),
    ("health.hygiene", r"opaska do wlosow|opaska do wlos"),
    ("transport.bike", r"rower|hamulc|hulajnog|uchwyt rowerow|na telefon skuter|kask (rowerow|na hulajnog)"),
    ("home.repair", r"przedluz|listwa zasil|zarowk|\bled\b|wkretak|srubokret|\bwkret|\bwkr\b|ostrza|mlotek"
                    r"|tasma (izol|klej|pakow|mier)|klej\b"),
    ("home.furniture", r"podgrzewacz|poduszk|koc\b|zaslon|firan|ramk|swiec|wazon|doniczk|dywan|posciel|recznik kapiel"
                              r"|lampk|pojemnik|kosz\b|organizer"),
    ("home.chem", r"\btorba|szorowan|gabki do|bateri|folia|worki|reklamowk|\brolek|plyn|\bwc\b|zel do wc|udrazn|udrozn|czarus|\bcif\b"
                                  r"|\bclin\b|sciereczk|scierecz|zmywak|recznik|papier toalet|papier do piecz"
                                  r"|proszek|kapsulki do pr|domestos|ajax|mop\b|miotl|szczotka do|wykalacz"),
    ("education", r"zeszyt|dlugopis|olowek|flamast"),
    ("health.hygiene", r"patycz|szampon|prysz|aquafresh|pasta do z|szczot|gillette|krem do rak|gabka|maszynk|garnier"
                            r"|dezodor|mydlo|balsam|odzywk|krem do|krem na|krem nawil|tusz do rz|pomadk|lakier do paz|chusteczk|wacik"
                            r"|patyczk|podpask|tampon|pieluch|nivea|dove\b|colgate|oral-b"),
    ("goods.clothes", r"skarpet|koszul|spodni|bluz|kurtk|czapk|rekawicz|majtk|biustonosz|buty|klapk|legginsy|t-shirt"
                      r"|szort|shorts?\b|spodenk|jeans|denim|hoody|hoodie|sweatshirt"),
    ("goods.electronics", r"sluchawk|glosnik|mysz\b|klawiatur|pendrive|powerbank|etui na tel"),
    ("health.vitamins", r"multiwit|witamin|magnez|elektrolit|tabl\.|whey|protein\b|kreatyn"),
    ("health.pharmacy", r"strepsils|ibuprom|\bapap\b|rutinoscorbin|na kaszel|na gardlo|plaster(?! miod)"),
    ("food.coffee", r"\bkawa\b|\bkawy\b|kawow|\bherb\.|cappuccino|herbat(a|ka|y)\b|herbatk|lipton|\blip\.herb|\binka\b"),
    ("food.grocery", r"tabasco|jalap|ketchup|majonez"),
    ("food.ready", r"noodle|nissin|oyakata|lunch ?box|\bsoba\b|ramen|kubek zup"),
    ("food.bread", r"chleb"),  # «Chleb brioche» — хлеб
    ("food.pastry", r"donut|drozdzow|croissant|rogal|jagodzian|brioche|strucla|babka|cynamonk|makowiec|warkocz"
                    r"|gniazdk|kostka wisn|przekaska mak|paczek|\bbul\w*\.? .*czek"),
    ("food.bread", r"\bbul(\.|k|ecz)|bagiet|tortill|cebularz|\btost"),
    ("food.ready", r"pierog|\bpyzy|pielmien|nalesnik|\bzup|nudle|ramen|rosol|pizza|lasagn|mroz"),
    ("food.sweets", r"czekol|czek\.|\bczek\b|ciast|cias\.|wafel|wafl|herbatnik|baton|zelki|pralin|piernik|\blody|marmolad|galaretk"
                    r"|deser|prince polo|kinder|schogetten|tabliczka|konafetto|rurki|rolada|tartaletk|inspiracje"
                    r"|sandwich|7 ?days|rozek|cukierk|koral|magnum|haribo|orbit|guma do|czeko|jezyk|lovita|\bcake\b"
                    r"|\btarta\b|duet|\bbat\b"),
    ("food.snacks", r"cheetos|krakers|nic ?nacs|popcorn|chips|orzech|orzesz|migdal|prazynk|chrupk|slonecz(?!nikow)|przysnack|\blay"),
    ("food.cheese", r"\bser(\b|\.|ek)|gouda|cheddar|camemb|\bbrie|mozz|\bedam|tylzyck|parmez"),
    ("food.dairy", r"muller|yo ?pro|jog|kefir|twarog|skyr|napoj mlecz|danio|smietan|mleko|maslo|fantazja|maslank"),
    ("food.grocery", r"przypr|przyp\."),  # «Przypr.d.kurcz.» — приправа, а не курица
    ("food.meat", r"mies\w* miel|miel\."),  # фарш, даже «Mięso mielone,szynka»
    ("food.sausage", r"parow|szynk|salami|kabanos|pasztet|mielonk|boczek|pieczen|poledw|serdel|kielbas"),
    ("food.meat", r"filet|mies|kurcz|podudz|medalion|indyk|wolow|wieprz|schab|karkow|udko|skrzydel"),
    ("food.meat", r"\bryb|losos|tunczyk|sledz|makrel|dorsz|krewet"),
    ("food.grains", r"kasz|\bryz\b|makar|\bmaka\b|platki|spaghetti\b|penne|fusilli|kuskus|musli"),
    ("food.dairy", r"\bjaj|jajk"),
    ("food.grocery", r"\bsos|musztard|przypr|\bsol\b|cukier|\bolej|oregano|rozmaryn|lisc|papryka slod"
                                   r"|soda ocz|dzem|konserw|groszek|korniszon|kukurydz|aromat|winiary|kamis|wanilin"
                                   r"|ketchup|majonez|ocet|drozdze"),
    ("food.drinks", r"\bwoda|napoj|\bnap\.|\bnap\b|cola|pepsi|\bsok\b|oranzad|lemoniad|izotonik|energy|monster|oshee"
                    r"|syrop|\bdoze\b|4move|coloss"),
    ("food.produce", r"papryk|marchew|cebul|pomid|ogork|kapust|ziemn|czosnek|rzodkiew|salat|brokul|pieczark|cukini"
                  r"|burak|seler|por\b"),
    ("food.produce", r"banan|jablk|mandaryn|winogr|\bkiwi|\bkaki|cytryn|mango|grusz|pomarancz|truskaw|malin|borowk"
                   r"|arbuz|brzoskw|sliwk|awokado|granat"),
]
_COMPILED = [(key, re.compile(rx)) for key, rx in KEYWORDS]


def _meta(con, key, default):
    from core.db import get_meta
    return json.loads(get_meta(con, key) or json.dumps(default))


def _set_meta(con, key, value):
    from core.db import set_meta
    set_meta(con, key, json.dumps(value, ensure_ascii=False))


def key_ids(con) -> dict[str, int]:
    """ключ -> id системной категории"""
    return {r["key"]: r["id"] for r in con.execute("SELECT id, key FROM categories WHERE key IS NOT NULL")}


def seed(con):
    """Дерево категорий: разовая перестройка v3 (старые базы), затем — недостающие категории из TREE.
    Что однажды создано, повторно не создаётся: если ты категорию удалил — код её обратно не вернёт."""
    migrate_tree_v3(con)
    seeded = set(_meta(con, "seeded_keys", []))
    have = key_ids(con)
    added = False
    for gkey, gname, kind, children in TREE:
        if gkey not in seeded and gkey not in have:
            have[gkey] = _insert(con, None, gname, kind, gkey)
        for ckey, cname in children:
            if ckey not in seeded and ckey not in have and gkey in have:
                have[ckey] = _insert(con, have[gkey], cname, kind, ckey)
    new = seeded | {k for k in have}
    if new != seeded:
        _set_meta(con, "seeded_keys", sorted(new))
        added = True
    if added:
        con.commit()


def _insert(con, parent_id, name, kind, key) -> int:
    return con.execute("INSERT INTO categories (parent_id, name, kind, key) VALUES (?, ?, ?, ?)",
                       (parent_id, name, kind, key)).lastrowid


def migrate_tree_v3(con):
    """Разовая перестройка: ключи системным категориям, новые названия, слияния (Мясо + Рыба, Овощи + Фрукты...).
    Старые пути остаются псевдонимами — правила, записанные путём, продолжают работать."""
    from core.db import get_meta, set_meta
    if get_meta(con, "tree_v3"):
        return
    for gkey, gname, kind, children in TREE:
        gid = _adopt(con, gkey, gname, None, kind)
        for ckey, cname in children:
            _adopt(con, ckey, cname, gid, kind)
    _set_meta(con, "seeded_keys", sorted(key_ids(con)))
    set_meta(con, "tree_v3", "1")
    con.commit()


def _adopt(con, key, name, parent_id, kind) -> int:
    have = key_ids(con)
    if key in have:
        return have[key]
    ids, found = ids_by_path(con), []
    for path in V3_FROM.get(key, []):
        cid = ids.get(path)
        if cid and cid not in found and cid not in have.values():
            found.append(cid)
    if not found:
        return _insert(con, parent_id, name, kind, key)
    primary, *rest = found
    _remember_paths(con, primary)
    for cid in rest:
        merge(con, cid, primary)
    con.execute("UPDATE categories SET name = ?, parent_id = ?, kind = ?, key = ? WHERE id = ?",
                (name, parent_id, kind, key, primary))
    return primary


def merge(con, src: int, dst: int):
    """Слить категорию src в dst: позиции, правила, операции банка и подкатегории переходят, src удаляется."""
    _remember_paths(con, src)
    aliases = {k: (dst if v == src else v) for k, v in _meta(con, "category_aliases", {}).items()}
    _set_meta(con, "category_aliases", aliases)
    con.execute("UPDATE categories SET parent_id = ? WHERE parent_id = ?", (dst, src))
    for table in ("items", "rules", "bank_tx"):
        con.execute(f"UPDATE {table} SET category_id = ? WHERE category_id = ?", (dst, src))
    con.execute("DELETE FROM categories WHERE id = ?", (src,))


def paths(con) -> dict[int, str]:
    """id -> «Еда/Сладкое» (любая глубина)"""
    rows = {r["id"]: r for r in con.execute("SELECT id, parent_id, name FROM categories")}

    def path(cid, seen=()):
        r = rows[cid]
        if r["parent_id"] is None or r["parent_id"] not in rows or cid in seen:
            return r["name"]
        return path(r["parent_id"], seen + (cid,)) + "/" + r["name"]
    return {cid: path(cid) for cid in rows}


def ids_by_path(con) -> dict[str, int]:
    """путь -> id, включая старые пути после переименований/переносов (псевдонимы)."""
    ids = {p: cid for cid, p in paths(con).items()}
    for old, cid in _meta(con, "category_aliases", {}).items():
        if old not in ids and con.execute("SELECT 1 FROM categories WHERE id = ?", (cid,)).fetchone():
            ids[old] = cid
    return ids


def cat_id(con, path: str) -> int:
    """Категория по пути («Еда/Сладкое», в т.ч. старому) или по ключу («food.sweets»)."""
    if (cid := key_ids(con).get(path.strip())) is not None:
        return cid
    by_path = {p.lower(): cid for p, cid in ids_by_path(con).items()}
    cid = by_path.get(path.strip().lower())
    if cid is None:
        raise ValueError(f"Нет категории «{path}». Есть: {', '.join(sorted(paths(con).values()))}")
    return cid


def subtree(con, cid: int) -> list[int]:
    out, todo = [], [cid]
    while todo:
        c = todo.pop()
        out.append(c)
        todo += [r["id"] for r in con.execute("SELECT id FROM categories WHERE parent_id = ?", (c,))]
    return out


def _remember_paths(con, cid: int):
    """Перед переименованием/переносом запоминаем текущие пути ветки как псевдонимы."""
    aliases = _meta(con, "category_aliases", {})
    cur = paths(con)
    for c in subtree(con, cid):
        aliases[cur[c]] = c
    _set_meta(con, "category_aliases", aliases)


def rename(con, cid: int, name: str):
    name = name.strip()
    if not name:
        raise ValueError("пустое название")
    _remember_paths(con, cid)
    con.execute("UPDATE categories SET name = ? WHERE id = ?", (name, cid))
    con.commit()


def move(con, cid: int, parent_id: int | None):
    if parent_id is not None and parent_id in subtree(con, cid):
        raise ValueError("нельзя перенести категорию внутрь самой себя")
    _remember_paths(con, cid)
    con.execute("UPDATE categories SET parent_id = ? WHERE id = ?", (parent_id, cid))
    if parent_id is not None:  # вид (расход/доход/перевод) — как у новой родительской категории
        kind = con.execute("SELECT kind FROM categories WHERE id = ?", (parent_id,)).fetchone()["kind"]
        con.executemany("UPDATE categories SET kind = ? WHERE id = ?", [(kind, c) for c in subtree(con, cid)])
    con.commit()


def delete(con, cid: int):
    """Удалить категорию: её позиции, правила и подкатегории переходят к родительской (или в «Неопознанные»)."""
    row = con.execute("SELECT parent_id FROM categories WHERE id = ?", (cid,)).fetchone()
    parent = row["parent_id"] if row else None
    aliases = _meta(con, "category_aliases", {})
    for old, c in list(aliases.items()):
        if c == cid:
            aliases[old] = parent
    if parent is not None:
        aliases[paths(con)[cid]] = parent
    _set_meta(con, "category_aliases", {k: v for k, v in aliases.items() if v is not None})
    con.execute("UPDATE categories SET parent_id = ? WHERE parent_id = ?", (parent, cid))
    con.execute("UPDATE items SET category_id = ? WHERE category_id = ?", (parent, cid))
    con.execute("UPDATE rules SET category_id = ? WHERE category_id = ?", (parent, cid))
    con.execute("DELETE FROM rules WHERE category_id IS NULL")
    con.execute("UPDATE bank_tx SET category_id = ? WHERE category_id = ?", (parent, cid))
    con.execute("DELETE FROM categories WHERE id = ?", (cid,))
    con.commit()


def normalize(name: str) -> str:
    """«TymbarkNapójJabArb2L» / «K.MakaronKolanko500» -> «tymbark napoj jab arb 2l» / «k. makaron kolanko 500»:
    слитные названия Kaufland разбиваем на слова, иначе словарь их не видит."""
    s = re.sub(r"(?<=[A-ZĄĆĘŁŃÓŚŹŻ]{2})(?=[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż])", " ", name or "")  # «BKTarta», но не «7DAys»
    s = re.sub(r"(?<=[a-ząćęłńóśźż])(?=[A-ZĄĆĘŁŃÓŚŹŻ])", " ", s)
    s = re.sub(r"(?<=[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż])(?=\d)|(?<=\d)(?=[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{2})", " ", s)
    s = re.sub(r"\.(?=\S)", ". ", s)
    return fold(s)


# Комментарий к позиции пишется как удобно — по-русски или по-польски. Русские слова — здесь,
# польские — общий словарь товаров. Шаблоны проходят через fold(): «й» -> «и», «ё» -> «е».
NOTE_KEYWORDS = [
    ("food.coffee", r"кофе в зерн|молот(ый|ого) кофе|растворим|\bчай\b"),
    ("leisure.cafe", r"кафе|кофе|капучино|латте|ресторан|обед|ужин|завтрак|бургер|пицц|шаурм|шаверм|кебаб|суши|роллы|"
                     r"макдон|kfc|доставк[аи] еды|\bбар\b|паб|столов|фастфуд|glovo|wolt|pyszne"),
    ("transport.taxi", r"такси|\buber\b|\bbolt\b|free ?now"),
    ("transport.intercity", r"электричк|поезд|билет на (поезд|автобус)|междугород|koleo|pkp|intercity|flixbus"),
    ("transport.city", r"автобус|трамва|проезд|метро|проездн|jakdojade|mpk|самокат"),
    ("transport.fuel", r"бензин|топлив|заправк|дизел|\bгаз\b для машин"),
    ("transport.bike", r"велосипед|велик|тормозн\w* колодк"),
    ("health.pharmacy", r"аптек|лекарств|таблетк|сироп|капли|пластыр|бинт|обезбол|от боли|ибупро|парацетам|нурофен"
                        r"|рецепт"),
    ("health.vitamins", r"витамин|добавк|магний|омега|протеин"),
    ("health.doctors", r"врач|доктор|стоматолог|зубн(ой|ого) врач|анализ|клиник|прием у|осмотр|узи|рентген"),
    ("health.hygiene", r"шампун|гель для душа|крем|зубн(ая|ую) паст|щетк|мыло|дезодорант|косметик|бритв|"
                       r"прокладк|салфетк|парикмахер|стрижк|маникюр"),
    ("vice.tobacco", r"сигарет|табак|вейп|\bvape\b|жидкост[ьи] для|стики|iqos|снюс"),
    ("vice.alcohol", r"пиво|вино|водк|виск|коньяк|алкогол|сидр|шампанск"),
    ("goods.clothes", r"одежд|обув|куртк|кроссовк|ботинк|футболк|джинс|штаны|брюк|носк|рубашк|свитер|худи|шапк"),
    ("comms.mobile", r"пополнени|связь|мобильн|интернет|симк"),
    ("goods.electronics", r"наушник|зарядк|кабел|ноутбук|смартфон|телефон|планшет|мышк|клавиатур|флешк|батарейк"),
    ("comms.subscriptions", r"подписк|netflix|spotify|youtube|chatgpt|claude|icloud|google one"),
    ("leisure.fun", r"кино|концерт|театр|музе|выставк|боулинг|бильярд|игр[аыу]\b|steam|квест|аквапарк"),
    ("leisure.gifts", r"подар|цвет(ы|ов)|букет|открытк|помощь|помог|донат|пожертв|благотвор"),
    ("goods.appliances", r"пылесос|холодильник|стиральн|посудомо|микроволн|чайник|утюг|\bфен\b|блендер|бытов(ая|ую) техник"),
    ("home.chem", r"бытов|порош|моющ|средство для|губк|мешки для мусора|туалетн(ая|ой) бумаг|бумажн"),
    ("home.furniture", r"мебел|стол|стул|полк|лампа|штор|подушк|одеял|постельн|посуд|кастрюл|сковород|тарелк|чашк"),
    ("home.repair", r"инструмент|отвертк|дрел|молоток|шуруп|гвозд|ремонт|краск|обои"),
    ("education", r"учеб|курс(ы|ов)?\b|универ|колледж|экзамен|учебник|тетрад|канцеляр"),
    ("housing.rent", r"аренд|квартплат|за квартиру|за комнату"),
    ("housing.utilities", r"коммунал|электричеств|свет за|за газ|вода за"),
    ("finance.taxes", r"налог|пошлин|штраф|сбор за"),
    ("other.delivery", r"курьер|посылк|почта|inpost|пачкомат|доставка заказа"),
    ("food.snacks", r"автомат|снек|чипс|орех"),
    ("food.sweets", r"шоколад|конфет|печень|морожен|торт|пирожн|десерт"),
    ("food.pastry", r"пончик|круассан|выпечк|пирожк|булочк"),
    ("food.bread", r"хлеб|багет|лаваш|тортиль"),
    ("food.produce", r"фрукт|яблок|банан|апельсин|ягод"),
    ("food.produce", r"овощ|картош|помидор|огур|лук\b"),
    ("food.meat", r"мясо|курин|куриц|говядин|свинин|фарш|рыб|лосос|сельд"),
    ("food.dairy", r"молок|кефир|йогурт|творог|сметан|яйц"),
    ("food.drinks", r"вода\b|сок\b|напит|энергетик|кола"),
    ("food", r"продукт|еда|рынок|магазин у дома|бакале"),
]
_NOTE_COMPILED = [(key, re.compile(fold(rx))) for key, rx in NOTE_KEYWORDS]


def note_category(note: str) -> str | None:
    """Ключ категории по твоему комментарию к позиции: сначала русские слова, потом польский словарь товаров."""
    f = fold(note or "")
    return next((key for key, rx in _NOTE_COMPILED if rx.search(f)), None) or keyword_category(note or "")


def keyword_category(name: str) -> str | None:
    f = normalize(name)
    return next((key for key, rx in _COMPILED if rx.search(f)), None)


# Отделы Kaufland (из текста чека) — запасной вариант, когда словарь товар не узнал
SECTIONS = {
    "slodycze": "food.sweets",
    "nabial": "food.dairy",
    "podstawowe artykuly spozywcze": "food.grocery",
    "wypieki pakowane": "food.bread",
    "piekarnia": "food.bread",
    "owoce/warzywa": "food.produce",
    "wedliny": "food.sausage",
    "napoje bezalkoholowe": "food.drinks",
    "napoje gorace": "food.coffee",
    "drogeria": "home.chem",
    "mrozonki": "food.ready",
    "beauty": "health.hygiene",
    "delikatesy/dania gotowe": "food.ready",
    "alkohole": "vice.alcohol",
    "lada z obsluga": "food.sausage",
    "mieso / ryby": "food.meat",
    "moj dom": "home",
    "moda": "goods.clothes",
    "czas wolny": "leisure.fun",
    "dzialalnosc dodatkowa": "other.deposit",
    "pozostaly nonfood": "other",
}


# Категория по магазину. STRICT — всегда (у билетов и визитов названия ни о чём не говорят),
# FALLBACK — только если словарь не узнал товар (в аптеке бывают и витамины, и косметика).
# Свои магазины — config.ini [shops] (всегда) и [shops_hint] (если словарь не узнал).
MERCHANT_STRICT = {"Jakdojade": "transport.city", "KOLEO": "transport.intercity",
                   "Erecept": "health.doctors", "Medicover": "health.doctors", "Synevo": "health.doctors",
                   "Anthropic": "comms.subscriptions", "Orange Flex": "comms.mobile"} | dict(local_pairs("shops"))
MERCHANT_FALLBACK = {"DOZ.pl": "health.pharmacy", "Apo-Discounter": "health.pharmacy", "Apteline": "health.pharmacy",
                     "GdziePoLek": "health.pharmacy", "Super-Pharm": "health.pharmacy", "Media Expert": "goods.electronics",
                     "Zalando": "goods.clothes", "Modivo": "goods.clothes", "eobuwie": "goods.clothes"} | dict(local_pairs("shops_hint"))


# Правило по магазину / получателю (rules.target = 'merchant', pattern = shop_key):
# операция банка — главное после твоих правок; позиция чека — только если словарь её не узнал (отвёртка из Lidl — не еда).
PLACEHOLDER_SHOPS = {"BLIK без названия", "Карта без названия"}  # у таких операций магазина нет — правило не создаём
LEGAL = re.compile(r"\b(sp\.? ?z ?o\.? ?o\.?|spolka z ograniczona odpowiedzialnoscia|spolka akcyjna|s\.a\.|sp\.? ?j\.?"
                   r"|sp\.? ?k\.?|gmbh|ltd|inc)(?=\s|$)")


def shop_key(name: str | None) -> str:
    """«ALDI Sp. z o.o. 001» -> «aldi»; «JAN KOWALSKI UL. DŁUGA 3» -> «jan kowalski»."""
    s = fold(name or "")
    s = re.sub(r"\s+ul\.?\s.*$", "", s)  # адрес отправителя перевода
    s = LEGAL.sub(" ", s)
    s = re.sub(r"[^\w\s&.'-]", " ", s).strip()
    for _ in range(3):  # номер точки в конце: «001», «Z1234 K.1», «A100»
        s = re.sub(r"\s+[a-z]{0,2}\.?\d[\w.-]*$", "", s).strip()
    return re.sub(r"\s+", " ", s).strip(" .-")


def shop_rules(con) -> dict[str, int]:
    return {r["pattern"]: r["category_id"] for r in con.execute(
        "SELECT pattern, category_id FROM rules WHERE target = 'merchant' AND pattern IS NOT NULL ORDER BY id")}


def add_shop_rule(con, merchant: str, category_id: int | None) -> str | None:
    """Правило «все покупки в магазине / переводы этому человеку». None — удалить правило."""
    key = shop_key(merchant)
    if not key or merchant in PLACEHOLDER_SHOPS:
        return None
    con.execute("DELETE FROM rules WHERE target = 'merchant' AND pattern = ?", (key,))
    if category_id is not None:
        con.execute("INSERT INTO rules (target, pattern, category_id, source, created) VALUES ('merchant', ?, ?, 'manual', ?)",
                    (key, category_id, dt.date.today().isoformat()))
    return key


def section_category(section: str | None) -> str | None:
    f = fold(section or "")
    return next((key for k, key in SECTIONS.items() if f.startswith(k)), None)


def categorize(con) -> dict:
    """Назначить категории всем позициям, кроме размеченных вручную. Возвращает статистику."""
    seed(con)
    K = key_ids(con)
    by_code = {r["product_code"]: r["category_id"] for r in con.execute(
        "SELECT product_code, category_id FROM rules WHERE target = 'item' AND product_code IS NOT NULL ORDER BY id")}
    patterns = [(re.compile(r["pattern"]), r["category_id"]) for r in con.execute(
        "SELECT pattern, category_id FROM rules WHERE target = 'item' AND pattern IS NOT NULL ORDER BY id DESC")]
    shops = shop_rules(con)
    stats = {"manual": 0, "note": 0, "code": 0, "rule": 0, "keyword": 0, "group": 0, "section": 0, "merchant": 0,
             "shop": 0, "unknown": 0}
    notes = {(r["purchase_id"], r["line"]): r["note"] for r in con.execute("SELECT purchase_id, line, note FROM item_notes")}
    updates, pending = [], []
    for it in con.execute("SELECT i.purchase_id, i.line, i.name, i.product_code, i.category_id, i.category_source, "
                          "i.section, i.group_code, p.merchant, p.source FROM items i JOIN purchases p ON p.id = i.purchase_id"):
        if it["category_source"] == "manual":
            stats["manual" if it["category_id"] is not None else "unknown"] += 1
            continue
        cat, src = None, None
        f = fold(it["name"])  # ручные правила по названию строятся от fold(name)
        note = notes.get((it["purchase_id"], it["line"]))
        if note and (k := note_category(note)) and k in K:
            cat, src = K[k], "note"  # твой комментарий важнее правил и словаря (но не ручной категории)
        elif it["product_code"] and it["product_code"] in by_code:
            cat, src = by_code[it["product_code"]], "code"
        elif m := next((c for rx, c in patterns if rx.search(f)), None):
            cat, src = m, "rule"
        elif it["source"] == "bank" and (c := shops.get(shop_key(it["merchant"]))) is not None:
            cat, src = c, "shop"
        elif it["source"] == "bank":
            # операции банка: категорию дала сверка (по получателю/описанию); словарь товаров к ним не применяем
            stats["bank"] = stats.get("bank", 0) + 1
            if it["category_source"] in ("note", "shop"):  # комментарий или правило удалены — снимаем (сверка вернёт свою)
                updates.append((None, None, it["purchase_id"], it["line"]))
                stats["bank"] -= 1
                stats["unknown"] += 1
                continue
            if it["category_source"] is None:
                stats["unknown"] += 1
                stats["bank"] -= 1
            continue
        elif (k := MERCHANT_STRICT.get(it["merchant"])) and k in K:
            cat, src = K[k], "merchant"
        elif (k := keyword_category(it["name"])) and k in K:
            cat, src = K[k], "keyword"
            # в аптеке «съедобные» слова словаря — лекарства: сироп, чай, леденцы от горла — «Аптека», а не «Напитки»
            fb = MERCHANT_FALLBACK.get(it["merchant"]) or ""
            if fb.startswith("health.") and k.startswith("food.") and fb in K:
                cat, src = K[fb], "merchant"
        if cat is None:
            pending.append(it)  # попробуем товарную группу и отдел магазина — после первого прохода
            continue
        stats[src] += 1
        updates.append((cat, src, it["purchase_id"], it["line"]))
    # товарная группа магазина: если большинство её товаров уже в одной категории — туда же и остальные
    votes = {}
    known = {(pid, line): cat for cat, src, pid, line in updates}
    for r in con.execute("SELECT purchase_id, line, group_code FROM items WHERE group_code IS NOT NULL"):
        if (c := known.get((r["purchase_id"], r["line"]))) is not None:
            votes.setdefault(r["group_code"], Counter())[c] += 1
    for it in pending:
        cat, src = None, None
        v = votes.get(it["group_code"])
        if v:
            top, n = v.most_common(1)[0]
            if n >= 2 and n / sum(v.values()) >= 0.7:
                cat, src = top, "group"
        if cat is None and (k := section_category(it["section"])) and k in K:
            cat, src = K[k], "section"
        if cat is None and (c := shops.get(shop_key(it["merchant"]))) is not None:
            cat, src = c, "shop"
        if cat is None and (k := MERCHANT_FALLBACK.get(it["merchant"])) and k in K:
            cat, src = K[k], "merchant"
        stats[src or "unknown"] += 1
        updates.append((cat, src, it["purchase_id"], it["line"]))
    # возврат: категория той позиции исходной покупки, по которой он сделан (сумма совпала), иначе — самой крупной;
    # твоя ручная категория или комментарий у возврата важнее
    final = {(r["purchase_id"], r["line"]): (r["category_id"], r["category_source"])
             for r in con.execute("SELECT purchase_id, line, category_id, category_source FROM items")}
    final.update({(pid, line): (cat, src) for cat, src, pid, line in updates})
    for r in con.execute("SELECT id, refund_of, total FROM purchases WHERE refund_of IS NOT NULL").fetchall():
        orig = con.execute("SELECT line, round(amount - coalesce(discount, 0), 2) v FROM items WHERE purchase_id = ? "
                           "ORDER BY amount DESC", (r["refund_of"],)).fetchall()
        if not orig:
            continue
        line = next((o["line"] for o in orig if abs(o["v"] + r["total"]) < 0.005), orig[0]["line"])
        cat = final.get((r["refund_of"], line), (None, None))[0]
        for (pid, ln), (_, src) in [(k, v) for k, v in final.items() if k[0] == r["id"]]:
            if cat is not None and src not in ("manual", "note"):
                updates.append((cat, "refund", pid, ln))
                stats["refund"] = stats.get("refund", 0) + 1
    con.executemany("UPDATE items SET category_id = ?, category_source = ? WHERE purchase_id = ? AND line = ?", updates)
    con.commit()
    return stats


def add_rule(con, category: str, pattern: str | None = None, product_code: str | None = None,
             source: str = "manual") -> int:
    """category — путь («Еда/Сладкое») или ключ («food.sweets»)."""
    cid = cat_id(con, category)
    con.execute("INSERT INTO rules (target, pattern, product_code, category_id, source, created) VALUES "
                "('item', ?, ?, ?, ?, ?)", (pattern, product_code, cid, source, dt.date.today().isoformat()))
    con.commit()
    return cid
