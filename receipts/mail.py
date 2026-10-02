"""Почта (Gmail по IMAP): заголовки -> статистика отправителей -> полные письма только от отмеченных.

  python budget.py mail login                 сохранить пароль приложения Google (в диспетчер учётных данных Windows)
  python budget.py mail headers               скачать заголовки всех писем с 01.01.2025 (дозагрузка новых)
  python budget.py mail senders [N]           отправители: сколько писем, примеры тем, похоже ли на покупки
  python budget.py mail mark shop|sub|ignore <домен> [...]   отметить отправителей
  python budget.py mail bodies                скачать полные письма от отмеченных магазинов/подписок

Почта открывается только на чтение (EXAMINE), письма не помечаются прочитанными (BODY.PEEK).
"""
import configparser
import email
import email.header
import email.utils
import getpass
import imaplib
import re
from collections import Counter, defaultdict

from core.common import BUDGET, DATA, fold
from core.db import connect, get_meta, set_meta

CONFIG = BUDGET / "config.ini"
RAW = DATA / "mail" / "raw"
SINCE = "01-Jan-2025"
KEYRING_SERVICE = "budget-gmail-imap"

# признаки писем о покупках/платежах в теме
SHOP_WORDS = re.compile(
    r"zam[oó]wieni|zamowien|order|potwierdz|faktur|paragon|p[lł]atno[sś]|zakup|receipt|invoice|rachun|dostaw"
    r"|przesy[lł]k|wys[lł]an|zwrot|refund|subskrypc|subscription|op[lł]at|bilet|rezerwac|booking|payment|"
    r"zap[lł]a|kupi|purchase|your order|dzi[eę]kujemy za zakup|e-paragon", re.I)
STATUS = {"shop": "магазин", "sub": "подписка/услуга", "pay": "оплата (посредник)", "ignore": "не покупки"}


# ---------------------------------------------------------------- подключение

def config():
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG, encoding="utf-8")
    if not cfg.has_section("mail"):
        cfg["mail"] = {"imap_host": "imap.gmail.com", "user": ""}
        with open(CONFIG, "w", encoding="utf-8") as f:
            cfg.write(f)
    return cfg["mail"]


def login():
    import keyring

    m = config()
    user = m.get("user", "").strip() or input("Адрес Gmail: ").strip()
    print("Пароль приложения Google (16 символов): Аккаунт Google -> Безопасность -> Двухэтапная проверка ->")
    print("Пароли приложений. Ввод не отображается. Сохранится в диспетчере учётных данных Windows, не в файле.")
    pw = getpass.getpass("Пароль приложения: ").replace(" ", "")
    con = imap(user, pw)  # проверяем, что пароль подходит
    con.logout()
    keyring.set_password(KEYRING_SERVICE, user, pw)
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG, encoding="utf-8")
    cfg["mail"]["user"] = user
    with open(CONFIG, "w", encoding="utf-8") as f:
        cfg.write(f)
    print("Вход в почту работает, пароль сохранён. Дальше: python budget.py mail headers")


def imap(user=None, pw=None) -> imaplib.IMAP4_SSL:
    import keyring

    m = config()
    user = user or m.get("user", "").strip()
    pw = pw or (keyring.get_password(KEYRING_SERVICE, user) if user else None)
    if not user or not pw:
        raise SystemExit("Сначала: python budget.py mail login")
    con = imaplib.IMAP4_SSL(m.get("imap_host", "imap.gmail.com"))
    try:
        con.login(user, pw)
    except imaplib.IMAP4.error as e:
        raise SystemExit(f"Gmail не пустил: {e}. Нужен именно пароль приложения (не обычный пароль).")
    return con


def all_mail_folder(con) -> str:
    """«Вся почта» Gmail (название зависит от языка интерфейса) — ищем по флагу \\All."""
    _, folders = con.list()
    for raw in folders:
        line = raw.decode()
        if "\\All" in line:
            return line.rsplit(' "/" ', 1)[-1].strip()
    return "INBOX"


def db():
    return connect()  # таблицы писем — в core/db.py


def decode(value) -> str:
    if not value:
        return ""
    out = []
    for part, enc in email.header.decode_header(value):
        if isinstance(part, bytes):
            try:
                out.append(part.decode(enc or "utf-8", "replace"))
            except LookupError:
                out.append(part.decode("utf-8", "replace"))
        else:
            out.append(part)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def base_domain(addr: str) -> str:
    """mail.allegro.pl / powiadomienia.allegro.pl -> allegro.pl"""
    host = addr.rsplit("@", 1)[-1].lower().strip(">")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("com", "co", "org", "net", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# ---------------------------------------------------------------- шаг 1: заголовки

def headers():
    con, store = imap(), db()
    folder = all_mail_folder(con)
    typ, _ = con.select(folder, readonly=True)
    if typ != "OK":
        raise SystemExit(f"Не открылась папка {folder}")
    validity = con.response("UIDVALIDITY")[1][0].decode()
    if get_meta(store, "mail_uidvalidity") not in (None, validity):
        print("Gmail сменил нумерацию писем — загружаю заголовки заново.")
        store.execute("DELETE FROM emails")
    set_meta(store, "mail_uidvalidity", validity)
    store.commit()  # без этого запись висит незакрытой и блокирует базу, когда новых писем нет
    _, data = con.uid("search", None, "SINCE", SINCE)
    uids = [int(u) for u in data[0].split()]
    have = {r[0] for r in store.execute("SELECT uid FROM emails")}
    todo = [u for u in uids if u not in have]
    print(f"Писем с {SINCE}: {len(uids)}, новых заголовков: {len(todo)}")
    for n in range(0, len(todo), 200):
        chunk = ",".join(map(str, todo[n:n + 200]))
        _, resp = con.uid("fetch", chunk,
                          "(RFC822.SIZE X-GM-LABELS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
        rows = []
        for item in resp:
            if not isinstance(item, tuple):
                continue
            meta, raw = item[0].decode(errors="replace"), item[1]
            uid = int(re.search(r"UID (\d+)", meta).group(1))
            size = int((re.search(r"RFC822\.SIZE (\d+)", meta) or [0, 0])[1])
            labels = (re.search(r"X-GM-LABELS \((.*?)\)", meta) or [None, ""])[1]
            msg = email.message_from_bytes(raw)
            name, addr = email.utils.parseaddr(decode(msg.get("From")))
            try:
                date = email.utils.parsedate_to_datetime(msg.get("Date")).isoformat(timespec="seconds")[:19]
            except (TypeError, ValueError):
                date = ""
            rows.append((uid, msg.get("Message-ID"), date, addr.lower(), name, base_domain(addr),
                         decode(msg.get("Subject")), size, labels))
        store.executemany("INSERT OR REPLACE INTO emails (uid, msg_id, date, from_addr, from_name, domain, subject, "
                          "size, labels) VALUES (?,?,?,?,?,?,?,?,?)", rows)
        store.commit()
        print(f"  {min(n + 200, len(todo))}/{len(todo)}")
    con.logout()
    print("Готово. Дальше: python budget.py mail senders")


# ---------------------------------------------------------------- шаг 2: отправители

def senders(limit: int = 80):
    store = db()
    marks = {r["domain"]: r["status"] for r in store.execute("SELECT domain, status FROM mail_senders")}
    by = defaultdict(list)
    for r in store.execute("SELECT domain, from_name, subject, size FROM emails"):
        by[r["domain"]].append(r)
    rows = []
    for dom, msgs in by.items():
        shop = sum(1 for m in msgs if SHOP_WORDS.search(m["subject"] or ""))
        names = Counter(m["from_name"] for m in msgs if m["from_name"]).most_common(1)
        samples = [m["subject"] for m in msgs if SHOP_WORDS.search(m["subject"] or "")][:2] or [msgs[0]["subject"]]
        rows.append((dom, len(msgs), shop, names[0][0] if names else "", samples, marks.get(dom)))
    rows.sort(key=lambda r: (-(r[2] > 0), -r[2], -r[1]))
    print(f"{'домен':<28} {'писем':>6} {'покупк.':>7}  метка        имя / примеры тем")
    for dom, n, shop, name, samples, mark in rows[:limit]:
        print(f"{dom[:28]:<28} {n:>6} {shop:>7}  {STATUS.get(mark, ''):<12} {name[:30]}")
        for s in samples:
            print(f"{'':>45}  · {s[:90]}")
    print(f"\nВсего отправителей: {len(rows)}, с признаками покупок: {sum(1 for r in rows if r[2])}.")
    print("Отметить: python budget.py mail mark shop allegro.pl amazon.pl   (sub — подписки/связь, pay — платёжные посредники, ignore — не нужно)")


def mark(status: str, domains: list[str]):
    if status not in STATUS:
        raise SystemExit(f"Метка: {', '.join(STATUS)}")
    store = db()
    for d in domains:
        store.execute("INSERT OR REPLACE INTO mail_senders (domain, status) VALUES (?, ?)", (d.lower(), status))
    store.commit()
    print(f"{STATUS[status]}: {', '.join(domains)}")


# ---------------------------------------------------------------- шаг 3: полные письма

def bodies():
    store = db()
    rows = store.execute("""SELECT e.uid FROM emails e JOIN mail_senders s ON s.domain = e.domain
                            WHERE s.status IN ('shop', 'sub', 'pay') AND e.path IS NULL ORDER BY e.uid""").fetchall()
    if not rows:
        print("Нечего скачивать: отметь отправителей (mail mark shop ...) или всё уже скачано.")
        return
    con = imap()
    con.select(all_mail_folder(con), readonly=True)
    RAW.mkdir(parents=True, exist_ok=True)
    uids = [r["uid"] for r in rows]
    print(f"Скачиваю полные письма: {len(uids)}")
    for n in range(0, len(uids), 25):
        chunk = ",".join(map(str, uids[n:n + 25]))
        _, resp = con.uid("fetch", chunk, "(BODY.PEEK[])")
        for item in resp:
            if not isinstance(item, tuple):
                continue
            uid = int(re.search(r"UID (\d+)", item[0].decode(errors="replace")).group(1))
            path = RAW / f"{uid}.eml"
            path.write_bytes(item[1])
            store.execute("UPDATE emails SET path = ? WHERE uid = ?",
                          (str(path.relative_to(DATA)).replace("\\", "/"), uid))
        store.commit()
        print(f"  {min(n + 25, len(uids))}/{len(uids)}")
    con.logout()


def main(argv: list[str]):
    cmd = argv[0] if argv else ""
    if cmd == "login":
        login()
    elif cmd == "headers":
        headers()
    elif cmd == "senders":
        senders(int(argv[1]) if len(argv) > 1 else 80)
    elif cmd == "mark" and len(argv) >= 3:
        mark(argv[1], argv[2:])
    elif cmd == "bodies":
        bodies()
    elif cmd == "parse":
        from receipts import mail_orders
        mail_orders.parse_all()
    else:
        print(__doc__)
