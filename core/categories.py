"""Категории: стартовое дерево, словарь ключевых слов, назначение категорий позициям.

Приоритет: ручная правка позиции > правило по коду товара > правило по шаблону названия > словарь.
Всё, что не распознано, остаётся без категории — очередь «Неопознанные» в интерфейсе.
"""
import datetime as dt
import json
import re
from collections import Counter

from core.common import fold

TREE = {
    "Еда": ["Мясо", "Колбасы и нарезка", "Рыба", "Молочное", "Сыр", "Яйца", "Хлеб и выпечка", "Овощи",
            "Фрукты", "Крупы и макароны", "Готовая еда и заморозка", "Сладкое", "Снеки и орехи", "Напитки",
            "Кофе и чай", "Бакалея, соусы, специи"],
    "Алкоголь": [],
    "Бытовая химия и хозтовары": [],
    "Гигиена и косметика": [],
    "Здоровье": ["Витамины и добавки", "Аптека", "Врачи"],
    "Дом": ["Мебель и интерьер", "Инструменты", "Ремонт"],
    "Одежда и обувь": [],
    "Электроника": [],
    "Транспорт": ["Общественный транспорт", "Такси", "Топливо"],
    "Жильё": ["Аренда", "Коммунальные"],
    "Связь и интернет": [],
    "Подписки": [],
    "Развлечения": [],
    "Кафе и доставка": [],
    "Подарки": [],
    "Образование": [],
    "Налоги и сборы": [],
    "Банк и комиссии": [],
    "Табак и вейп": [],
    "Прочее": ["Залог за тару", "Доставка", "Наличные без чека"],
}
INCOME = {"Доходы": ["Зарплата", "Подработка", "Стипендия", "Наличные", "Прочие доходы"]}
TRANSFERS = {"Переводы": ["Между своими счетами", "Людям", "Снятие наличных", "От людей", "Взнос наличных"]}

# Порядок важен: первое совпадение побеждает. Шаблоны — по названию без диакритик (fold).
KEYWORDS = [
    ("Прочее/Залог за тару", r"zwrotn|kaucj|opakowania zwrot"),
    ("Прочее/Доставка", r"^dostawa|^kurier w nast|^przesylka kurier"),
    ("Финансы/Банк и комиссии", r"^koszt (obslugi )?platnosci|^oplata za (platnosc|pobranie)|^koszt pobrania"),
    ("Подписки", r"allegro smart|claude pro|subscription|abonament|premium plan"),
    ("Еда/Кофе и чай", r"tassimo|kapsulki do kaw|dolce gusto|nespresso"),
    # исключения: слово из «чужой» категории внутри названия (пончик с ликёром, приправа для курицы...)
    ("Еда/Хлеб и выпечка", r"\bpaczek|\bpaczki|racuch"),
    ("Еда/Бакалея, соусы, специи", r"stevia|proszek do piec|\bprzyp\b|\bfix\b"),
    # чипсы (и «Chipsy Tortilla»), сухофрукты — но не «Redds Żurawina piwo» и не «Rozmaryn suszony»
    ("Еда/Снеки и орехи", r"chips|zuraw(?!.*piw)|rodzynk|liofil|suszone (owoc|sliwk|morel)|daktyl"),
    ("Еда/Готовая еда и заморозка", r"\bdanie\b|gulasz|po chinsku|\bwok\b|jemy ?jemy|goracy kubek"),
    ("Еда/Снеки и орехи", r"wiejskie ziemn|miesz\w* egzot|orze\w* piec"),
    ("Еда/Кофе и чай", r"earl ?grey"),
    ("Еда/Напитки", r"sparkling|ice ?tea"),
    ("Алкоголь", r"\blomza"),
    ("Алкоголь", r"\bpiw|piwo|\brum\b|likier|\bwino\b|wodka|whisk|\bgin\b|okocim|zywiec|tyskie|\blech\b|somersby"
                 r"|jager|warka|carlsberg|heineken|desperados|kozel|radler|cydr"),
    ("Электроника", r"\betui\b|szklo (na|ochron|hartow)|szklo ochronne|folia ochron|ochrona na ekran|ladowark|supervooc"
                    r"|power ?bank|smart ?band|smart plug|inteligentn\w* gniazd|czujnik temperatur|uchwyt (do|na) (monitor|telefon)"
                    r"|uchwyt stolowy do monitor|adapter vesa|sluchawk|kabel usb|przewod usb"),
    ("Дом/Ремонт", r"uszczelk|wentylator|regulator obrot|wago|zlaczk|szybkozlaczk|tester napiec|przewod przylacz"
                   r"|tasma dwustron|izopropyl|filtr .*antyalerg|podkladki filc"),
    ("Дом/Мебель и интерьер", r"biurko|\bmeble\b|polka|wieszak|suszarka (wiszac|do naczyn)|ociekacz|lina jutow"),
    ("Бытовая химия и хозтовары", r"rekawice nitryl"),
    ("Гигиена и косметика", r"opaska do wlosow|opaska do wlos"),
    ("Транспорт", r"uchwyt rowerow|na telefon skuter"),
    ("Дом/Инструменты", r"przedluz|listwa zasil|zarowk|\bled\b|kabel|ladowark|wkretak|srubokret|mlotek|tasma (izol|klej|pakow|mier)"
                        r"|klej\b"),
    ("Дом/Мебель и интерьер", r"podgrzewacz|poduszk|koc\b|zaslon|firan|ramk|swiec|wazon|doniczk|dywan|posciel|recznik kapiel"
                              r"|lampk|pojemnik|kosz\b|organizer"),
    ("Бытовая химия и хозтовары", r"\btorba|szorowan|gabki do|bateri|folia|worki|reklamowk|\brolek|plyn|\bwc\b|zel do wc|udrazn|udrozn|czarus|\bcif\b"
                                  r"|\bclin\b|sciereczk|scierecz|zmywak|recznik|papier toalet|papier do piecz"
                                  r"|proszek|kapsulki do pr|domestos|ajax|mop\b|miotl|szczotka do"),
    ("Образование", r"zeszyt|dlugopis|olowek|flamast"),
    ("Гигиена и косметика", r"patycz|szampon|prysz|aquafresh|pasta do z|szczot|gillette|krem do rak|gabka|maszynk|garnier"
                            r"|dezodor|mydlo|balsam|odzywk|krem do|krem na|krem nawil|tusz do rz|pomadk|lakier do paz|chusteczk|wacik"
                            r"|patyczk|podpask|tampon|pieluch|nivea|dove\b|colgate|oral-b"),
    ("Одежда и обувь", r"skarpet|koszul|spodni|bluz|kurtk|czapk|rekawicz|majtk|biustonosz|buty|klapk|legginsy|t-shirt"),
    ("Электроника", r"sluchawk|glosnik|mysz\b|klawiatur|pendrive|powerbank|etui na tel"),
    ("Здоровье/Витамины и добавки", r"multiwit|witamin|magnez|elektrolit|tabl\.|whey|protein\b|kreatyn"),
    ("Здоровье/Аптека", r"strepsils|ibuprom|\bapap\b|rutinoscorbin|na kaszel|na gardlo|plaster(?! miod)"),
    ("Еда/Кофе и чай", r"\bkawa\b|\bkawy\b|kawow|\bherb\.|cappuccino|herbat(a|ka|y)\b|herbatk|lipton|\blip\.herb|\binka\b"),
    ("Еда/Бакалея, соусы, специи", r"tabasco|jalap|ketchup|majonez"),
    ("Еда/Готовая еда и заморозка", r"noodle|nissin|oyakata|lunch ?box|\bsoba\b|ramen|kubek zup"),
    ("Еда/Хлеб и выпечка", r"chleb|\bbul(\.|k|ecz)|bagiet|tortill|croissant|rogal|drozdzow|paczek|jagodzian|cebularz"
                           r"|strucla|donut|brioche|\btost"),
    ("Еда/Готовая еда и заморозка", r"pierog|\bpyzy|pielmien|nalesnik|\bzup|nudle|ramen|rosol|pizza|lasagn|mroz"),
    ("Еда/Сладкое", r"czekol|czek\.|ciast|cias\.|wafel|wafl|herbatnik|baton|zelki|pralin|piernik|\blody|marmolad|galaretk"
                    r"|deser|prince polo|kinder|schogetten|tabliczka|konafetto|rurki|rolada|tartaletk|inspiracje"
                    r"|sandwich|7 ?days|rozek|cukierk|koral|magnum|haribo|orbit|guma do|czeko|jezyk|lovita|\bcake\b"
                    r"|\btarta\b|duet|\bbat\b"),
    ("Еда/Снеки и орехи", r"cheetos|krakers|nic ?nacs|popcorn|chips|orzech|orzesz|migdal|prazynk|chrupk|slonecz(?!nikow)|przysnack|\blay"),
    ("Еда/Сыр", r"\bser(\b|\.|ek)|gouda|cheddar|camemb|\bbrie|mozz|\bedam|tylzyck|parmez"),
    ("Еда/Молочное", r"muller|yo ?pro|jog|kefir|twarog|skyr|napoj mlecz|danio|smietan|mleko|maslo|fantazja|maslank"),
    ("Еда/Бакалея, соусы, специи", r"przypr|przyp\."),  # «Przypr.d.kurcz.» — приправа, а не курица
    ("Еда/Мясо", r"mies\w* miel|miel\."),  # фарш, даже «Mięso mielone,szynka»
    ("Еда/Колбасы и нарезка", r"parow|szynk|salami|kabanos|pasztet|mielonk|boczek|pieczen|poledw|serdel|kielbas"),
    ("Еда/Мясо", r"filet|mies|kurcz|podudz|medalion|indyk|wolow|wieprz|schab|karkow|udko|skrzydel"),
    ("Еда/Рыба", r"\bryb|losos|tunczyk|sledz|makrel|dorsz|krewet"),
    ("Еда/Крупы и макароны", r"kasz|\bryz\b|makar|\bmaka\b|platki|spaghetti\b|penne|fusilli|kuskus|musli"),
    ("Еда/Яйца", r"\bjaj|jajk"),
    ("Еда/Бакалея, соусы, специи", r"\bsos|musztard|przypr|\bsol\b|cukier|\bolej|oregano|rozmaryn|lisc|papryka slod"
                                   r"|soda ocz|dzem|konserw|groszek|korniszon|kukurydz|aromat|winiary|kamis|wanilin"
                                   r"|ketchup|majonez|ocet|drozdze"),
    ("Еда/Напитки", r"\bwoda|napoj|\bnap\.|\bnap\b|cola|pepsi|\bsok\b|oranzad|lemoniad|izotonik|energy|monster|oshee"
                    r"|syrop|\bdoze\b|4move|coloss"),
    ("Еда/Овощи", r"papryk|marchew|cebul|pomid|ogork|kapust|ziemn|czosnek|rzodkiew|salat|brokul|pieczark|cukini"
                  r"|burak|seler|por\b"),
    ("Еда/Фрукты", r"banan|jablk|mandaryn|winogr|\bkiwi|\bkaki|cytryn|mango|grusz|pomarancz|truskaw|malin|borowk"
                   r"|arbuz|brzoskw|sliwk|awokado|granat"),
]
_COMPILED = [(path, re.compile(rx)) for path, rx in KEYWORDS]


def _meta(con, key, default):
    from core.db import get_meta
    return json.loads(get_meta(con, key) or json.dumps(default))


def _set_meta(con, key, value):
    from core.db import set_meta
    set_meta(con, key, json.dumps(value, ensure_ascii=False))


def ensure_tree(con, tree: dict, kind: str):
    """Добавить категории из кода, которых ещё не было. Что однажды создано, повторно не создаётся:
    если ты категорию удалил, переименовал или перенёс — код её обратно не вернёт."""
    seeded = set(_meta(con, "seeded_paths", []))
    if not seeded:  # первый запуск этой логики: всё, что уже есть, считается созданным
        seeded = set(paths(con).values())
    ids = ids_by_path(con)
    for parent, children in tree.items():
        if parent not in seeded:
            pid = ids.get(parent) or con.execute(
                "INSERT INTO categories (parent_id, name, kind) VALUES (NULL, ?, ?)", (parent, kind)).lastrowid
            seeded.add(parent)
        pid = ids_by_path(con).get(parent)
        for child in children:
            path = f"{parent}/{child}"
            if path in seeded or pid is None:
                continue
            if path not in ids_by_path(con):
                con.execute("INSERT INTO categories (parent_id, name, kind) VALUES (?, ?, ?)", (pid, child, kind))
            seeded.add(path)
    _set_meta(con, "seeded_paths", sorted(seeded))
    con.commit()


def seed(con):
    """Стартовое дерево + разовая перегруппировка (меньше категорий верхнего уровня)."""
    for tree, kind in ((TREE, "expense"), (INCOME, "income"), (TRANSFERS, "transfer")):
        ensure_tree(con, tree, kind)
    migrate_tree_v2(con)


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
    """путь -> id, включая старые пути после переименований/переносов: правила в коде продолжают работать."""
    ids = {p: cid for cid, p in paths(con).items()}
    for old, cid in _meta(con, "category_aliases", {}).items():
        if old not in ids and con.execute("SELECT 1 FROM categories WHERE id = ?", (cid,)).fetchone():
            ids[old] = cid
    return ids


def cat_id(con, path: str) -> int:
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
    have = {r["name"] for r in con.execute("PRAGMA table_info(bank_tx)")}
    if "category_id" in have:
        con.execute("UPDATE bank_tx SET category_id = ? WHERE category_id = ?", (parent, cid))
    con.execute("DELETE FROM categories WHERE id = ?", (cid,))
    con.commit()


# Разовая перегруппировка: 25 категорий верхнего уровня -> ~12 групп. Старые пути остаются псевдонимами.
TREE_V2 = [  # (новая группа, переименовать из, [что перенести внутрь])
    ("Дом и быт", "Дом", ["Бытовая химия и хозтовары"]),
    ("Здоровье и красота", "Здоровье", ["Гигиена и косметика"]),
    ("Алкоголь и табак", None, ["Алкоголь", "Табак и вейп"]),
    ("Связь и подписки", None, ["Связь и интернет", "Подписки"]),
    ("Досуг", None, ["Кафе и доставка", "Развлечения", "Подарки"]),
    ("Вещи и техника", None, ["Одежда и обувь", "Электроника"]),
    ("Финансы", None, ["Налоги и сборы", "Банк и комиссии"]),
]


def migrate_tree_v2(con):
    from core.db import get_meta, set_meta
    if get_meta(con, "tree_v2"):
        return
    for group, rename_from, members in TREE_V2:
        ids = ids_by_path(con)
        if rename_from and rename_from in ids:
            rename(con, ids[rename_from], group)
        ids = ids_by_path(con)
        gid = ids.get(group) or con.execute("INSERT INTO categories (parent_id, name, kind) VALUES (NULL, ?, 'expense')",
                                            (group,)).lastrowid
        for m in members:
            mid = ids_by_path(con).get(m)
            if mid and mid != gid:
                move(con, mid, gid)
    seeded = set(_meta(con, "seeded_paths", [])) | {g for g, _, _ in TREE_V2}
    _set_meta(con, "seeded_paths", sorted(seeded))
    set_meta(con, "tree_v2", "1")
    con.commit()


def normalize(name: str) -> str:
    """«TymbarkNapójJabArb2L» / «K.MakaronKolanko500» -> «tymbark napoj jab arb 2l» / «k. makaron kolanko 500»:
    слитные названия Kaufland разбиваем на слова, иначе словарь их не видит."""
    s = re.sub(r"(?<=[a-ząćęłńóśźż])(?=[A-ZĄĆĘŁŃÓŚŹŻ])", " ", name or "")
    s = re.sub(r"(?<=[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż])(?=\d)|(?<=\d)(?=[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{2})", " ", s)
    s = re.sub(r"\.(?=\S)", ". ", s)
    return fold(s)


# Комментарий к позиции пишется как удобно — по-русски или по-польски. Русские слова — здесь,
# польские — общий словарь товаров. Шаблоны проходят через fold(): «й» -> «и», «ё» -> «е».
NOTE_KEYWORDS = [
    ("Еда/Кофе и чай", r"кофе в зерн|молот(ый|ого) кофе|растворим|\bчай\b"),
    ("Досуг/Кафе и доставка", r"кафе|кофе|капучино|латте|ресторан|обед|ужин|завтрак|бургер|пицц|шаурм|шаверм|кебаб|суши|роллы|"
                              r"макдон|kfc|доставк[аи] еды|\bбар\b|паб|столов|фастфуд|glovo|wolt|pyszne"),
    ("Транспорт/Такси", r"такси|\buber\b|\bbolt\b|free ?now"),
    ("Транспорт/Общественный транспорт", r"автобус|трамва|проезд|метро|электричк|поезд|билет на (поезд|автобус)|jakdojade|koleo|mpk"),
    ("Транспорт/Топливо", r"бензин|топлив|заправк|дизел|\bгаз\b для машин"),
    ("Здоровье и красота/Аптека", r"аптек|лекарств|таблетк|сироп|капли|пластыр|бинт"),
    ("Здоровье и красота/Витамины и добавки", r"витамин|добавк|магний|омега|протеин"),
    ("Здоровье и красота/Врачи", r"врач|доктор|стоматолог|зубн(ой|ого) врач|анализ|клиник|прием у|осмотр|узи|рентген"),
    ("Здоровье и красота/Гигиена и косметика", r"шампун|гель для душа|крем|зубн(ая|ую) паст|щетк|мыло|дезодорант|косметик|бритв|"
                                                r"прокладк|салфетк|парикмахер|стрижк|маникюр"),
    ("Алкоголь и табак/Табак и вейп", r"сигарет|табак|вейп|\bvape\b|жидкост[ьи] для|стики|iqos|снюс"),
    ("Алкоголь и табак/Алкоголь", r"пиво|вино|водк|виск|коньяк|алкогол|сидр|шампанск"),
    ("Вещи и техника/Одежда и обувь", r"одежд|обув|куртк|кроссовк|ботинк|футболк|джинс|штаны|брюк|носк|рубашк|свитер|худи|шапк"),
    ("Связь и подписки/Связь и интернет", r"пополнени|связь|мобильн|интернет|симк"),
    ("Вещи и техника/Электроника", r"наушник|зарядк|кабел|ноутбук|смартфон|телефон|планшет|мышк|клавиатур|флешк|батарейк"),
    ("Связь и подписки/Подписки", r"подписк|netflix|spotify|youtube|chatgpt|claude|icloud|google one"),
    ("Досуг/Развлечения", r"кино|концерт|театр|музе|выставк|боулинг|бильярд|игр[аыу]\b|steam|квест|аквапарк"),
    ("Досуг/Подарки", r"подар|цвет(ы|ов)|букет|открытк"),
    ("Дом и быт/Бытовая химия и хозтовары", r"бытов|порош|моющ|средство для|губк|мешки для мусора|туалетн(ая|ой) бумаг|бумажн"),
    ("Дом и быт/Мебель и интерьер", r"мебел|стол|стул|полк|лампа|штор|подушк|одеял|постельн|посуд|кастрюл|сковород|тарелк|чашк"),
    ("Дом и быт/Инструменты", r"инструмент|отвертк|дрел|молоток|шуруп|гвозд"),
    ("Образование", r"учеб|курс(ы|ов)?\b|универ|колледж|экзамен|учебник|тетрад|канцеляр"),
    ("Жильё/Аренда", r"аренд|квартплат|за квартиру|за комнату"),
    ("Жильё/Коммунальные", r"коммунал|электричеств|свет за|за газ|вода за"),
    ("Финансы/Налоги и сборы", r"налог|пошлин|штраф|сбор за"),
    ("Прочее/Доставка", r"курьер|посылк|почта|inpost|пачкомат|доставка заказа"),
    ("Еда/Снеки и орехи", r"автомат|снек|чипс|орех"),
    ("Еда/Сладкое", r"шоколад|конфет|печень|морожен|торт|пирожн|десерт"),
    ("Еда/Хлеб и выпечка", r"хлеб|булк|багет|круассан|выпечк|пирожк"),
    ("Еда/Фрукты", r"фрукт|яблок|банан|апельсин|ягод"),
    ("Еда/Овощи", r"овощ|картош|помидор|огур|лук\b"),
    ("Еда/Мясо", r"мясо|курин|куриц|говядин|свинин|фарш"),
    ("Еда/Молочное", r"молок|кефир|йогурт|творог|сметан"),
    ("Еда/Напитки", r"вода\b|сок\b|напит|энергетик|кола"),
    ("Еда", r"продукт|еда|рынок|магазин у дома|бакале"),
]
_NOTE_COMPILED = [(path, re.compile(fold(rx))) for path, rx in NOTE_KEYWORDS]


def note_category(note: str) -> str | None:
    """Категория по твоему комментарию к позиции: сначала русские слова, потом польский словарь товаров."""
    f = fold(note or "")
    return next((path for path, rx in _NOTE_COMPILED if rx.search(f)), None) or keyword_category(note or "")


def keyword_category(name: str) -> str | None:
    f = normalize(name)
    return next((path for path, rx in _COMPILED if rx.search(f)), None)


# Отделы Kaufland (из текста чека) — запасной вариант, когда словарь товар не узнал
SECTIONS = {
    "slodycze": "Еда/Сладкое",
    "nabial": "Еда/Молочное",
    "podstawowe artykuly spozywcze": "Еда/Бакалея, соусы, специи",
    "wypieki pakowane": "Еда/Хлеб и выпечка",
    "piekarnia": "Еда/Хлеб и выпечка",
    "owoce/warzywa": "Еда/Овощи",
    "wedliny": "Еда/Колбасы и нарезка",
    "napoje bezalkoholowe": "Еда/Напитки",
    "napoje gorace": "Еда/Кофе и чай",
    "drogeria": "Бытовая химия и хозтовары",
    "mrozonki": "Еда/Готовая еда и заморозка",
    "beauty": "Гигиена и косметика",
    "delikatesy/dania gotowe": "Еда/Готовая еда и заморозка",
    "alkohole": "Алкоголь",
    "lada z obsluga": "Еда/Колбасы и нарезка",
    "mieso / ryby": "Еда/Мясо",
    "moj dom": "Дом",
    "moda": "Одежда и обувь",
    "czas wolny": "Развлечения",
    "dzialalnosc dodatkowa": "Прочее/Залог за тару",
    "pozostaly nonfood": "Прочее",
}


# Категория по магазину. STRICT — всегда (у билетов и визитов названия ни о чём не говорят),
# FALLBACK — только если словарь не узнал товар (в аптеке бывают и витамины, и косметика).
MERCHANT_STRICT = {"Jakdojade": "Транспорт/Общественный транспорт", "KOLEO": "Транспорт/Общественный транспорт",
                   "Erecept": "Здоровье/Врачи", "Medicover": "Здоровье/Врачи", "Synevo": "Здоровье/Врачи",
                   "Anthropic": "Подписки",
                   "Orange Flex": "Связь и интернет"}
MERCHANT_FALLBACK = {"DOZ.pl": "Здоровье/Аптека", "Apo-Discounter": "Здоровье/Аптека", "Apteline": "Здоровье/Аптека",
                     "GdziePoLek": "Здоровье/Аптека", "Media Expert": "Электроника",
                     "Zalando": "Одежда и обувь", "Modivo": "Одежда и обувь", "eobuwie": "Одежда и обувь"}


def section_category(section: str | None) -> str | None:
    f = fold(section or "")
    return next((path for key, path in SECTIONS.items() if f.startswith(key)), None)


def categorize(con) -> dict:
    """Назначить категории всем позициям, кроме размеченных вручную. Возвращает статистику."""
    seed(con)
    ids = ids_by_path(con)
    by_code = {r["product_code"]: r["category_id"] for r in con.execute(
        "SELECT product_code, category_id FROM rules WHERE target = 'item' AND product_code IS NOT NULL ORDER BY id")}
    patterns = [(re.compile(r["pattern"]), r["category_id"]) for r in con.execute(
        "SELECT pattern, category_id FROM rules WHERE target = 'item' AND pattern IS NOT NULL ORDER BY id DESC")]
    stats = {"manual": 0, "note": 0, "code": 0, "rule": 0, "keyword": 0, "group": 0, "section": 0, "merchant": 0, "unknown": 0}
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
        if note and (p := note_category(note)) and p in ids:
            cat, src = ids[p], "note"  # твой комментарий важнее правил и словаря (но не ручной категории)
        elif it["product_code"] and it["product_code"] in by_code:
            cat, src = by_code[it["product_code"]], "code"
        elif m := next((c for rx, c in patterns if rx.search(f)), None):
            cat, src = m, "rule"
        elif it["source"] == "bank":
            # операции банка: категорию дала сверка (по получателю/описанию); словарь товаров к ним не применяем
            stats["bank"] = stats.get("bank", 0) + 1
            if it["category_source"] == "note":  # комментарий удалён — категорию по нему тоже снимаем (сверка вернёт свою)
                updates.append((None, None, it["purchase_id"], it["line"]))
                stats["bank"] -= 1
                stats["unknown"] += 1
                continue
            if it["category_source"] is None:
                stats["unknown"] += 1
                stats["bank"] -= 1
            continue
        elif (p := MERCHANT_STRICT.get(it["merchant"])) and p in ids:
            cat, src = ids[p], "merchant"
        elif (p := keyword_category(it["name"])) and p in ids:
            cat, src = ids[p], "keyword"
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
        if cat is None and (p := section_category(it["section"])) and p in ids:
            cat, src = ids[p], "section"
        if cat is None and (p := MERCHANT_FALLBACK.get(it["merchant"])) and p in ids:
            cat, src = ids[p], "merchant"
        stats[src or "unknown"] += 1
        updates.append((cat, src, it["purchase_id"], it["line"]))
    con.executemany("UPDATE items SET category_id = ?, category_source = ? WHERE purchase_id = ? AND line = ?", updates)
    con.commit()
    return stats


def add_rule(con, category_path: str, pattern: str | None = None, product_code: str | None = None,
             source: str = "manual") -> int:
    cid = cat_id(con, category_path)
    con.execute("INSERT INTO rules (target, pattern, product_code, category_id, source, created) VALUES "
                "('item', ?, ?, ?, ?, ?)", (pattern, product_code, cid, source, dt.date.today().isoformat()))
    con.commit()
    return cid
