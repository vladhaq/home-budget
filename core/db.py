"""Единая база бюджета: data/budget.db. Вся схема — здесь; изменения схемы — миграции ниже (PRAGMA user_version).

purchases — покупка (чек, онлайн-заказ, операция банка без чека), источник любой;
items     — позиции покупки;
bank_tx   — операции из выписки; связь с покупкой — purchases.bank_tx_id (одна операция — одна или несколько покупок);
categories / rules — дерево категорий (у системных — постоянный ключ key) и правила назначения;
meta      — служебные значения (например, дата последней выписки).
"""
import sqlite3

from core.common import DATA

DB = DATA / "budget.db"

# способы оплаты — единый словарь для всех источников
PAYMENT_METHODS = {
    "card": "карта",
    "cash": "наличные",
    "blik": "BLIK",
    "online": "онлайн",
    "transfer": "перевод",
    "voucher": "купон/ваучер",
    "mixed": "смешанная",
    None: "неизвестно",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
    id TEXT PRIMARY KEY,            -- источник:идентификатор, например lidl:<номер чека>
    source TEXT NOT NULL,           -- lidl, kaufland, photo, email
    date TEXT,                      -- ISO, YYYY-MM-DDTHH:MM:SS
    merchant TEXT,                  -- сеть/магазин: Lidl, Kaufland, Allegro...
    store TEXT,                     -- конкретная точка/адрес
    total REAL,                     -- к оплате, после скидок
    discount REAL,                  -- сумма скидок
    currency TEXT DEFAULT 'PLN',
    payment_method TEXT,            -- ключ из PAYMENT_METHODS; при нескольких — mixed
    card_last4 TEXT,
    raw_path TEXT,                  -- исходник: JSON, фото, письмо
    bank_tx_id TEXT,                -- привязка к операции в выписке
    note TEXT
);
CREATE TABLE IF NOT EXISTS payments (
    purchase_id TEXT, method TEXT, amount REAL, card_last4 TEXT
);
CREATE TABLE IF NOT EXISTS items (
    purchase_id TEXT, line INTEGER,
    name TEXT, product_code TEXT,
    qty REAL, unit_price REAL, amount REAL, discount REAL,
    category_id INTEGER, category_source TEXT,   -- manual / code / rule / llm
    PRIMARY KEY (purchase_id, line)
);
CREATE TABLE IF NOT EXISTS bank_tx (
    id TEXT PRIMARY KEY, date TEXT, amount REAL, balance REAL,
    type TEXT, counterparty TEXT, description TEXT, raw TEXT
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY, parent_id INTEGER, name TEXT NOT NULL,
    kind TEXT DEFAULT 'expense'     -- expense / income / transfer
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY, target TEXT,          -- item / merchant / bank
    pattern TEXT, product_code TEXT, category_id INTEGER,
    source TEXT, created TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- покупки, которые ты удалил (незавершённая оплата, дубль): при пересборке из чеков и писем они пропускаются
CREATE TABLE IF NOT EXISTS hidden_purchases (id TEXT PRIMARY KEY, source TEXT, label TEXT, raw_path TEXT, created TEXT);
-- твой комментарий к позиции («что купил»): переживает пересборку, по нему определяется категория
-- фото бумажного чека, совпавшее с уже известной покупкой (электронный чек Kaufland/Lidl, письмо): прикреплено к ней
CREATE TABLE IF NOT EXISTS attachments (purchase_id TEXT, path TEXT, source_ref TEXT, date TEXT, total REAL, merchant TEXT,
    added TEXT, PRIMARY KEY (purchase_id, path));
CREATE TABLE IF NOT EXISTS item_notes (purchase_id TEXT, line INTEGER, name TEXT, note TEXT, updated TEXT,
    PRIMARY KEY (purchase_id, line));
-- платёж «под вопросом» (регистрация без подтверждения, которой нет в банке), который ты подтвердил: считать
CREATE TABLE IF NOT EXISTS confirmed_purchases (id TEXT PRIMARY KEY, created TEXT);
-- наличные: твои записи и уточнённое время операций банка (банк даёт только дату)
CREATE TABLE IF NOT EXISTS wallet_entries (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, kind TEXT NOT NULL, amount REAL NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS wallet_times (tx_id TEXT PRIMARY KEY, at TEXT NOT NULL);
-- журнал «обновить всё»
CREATE TABLE IF NOT EXISTS update_runs (
  id INTEGER PRIMARY KEY, started TEXT, finished TEXT, trigger TEXT, steps TEXT, status TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS update_steps (
  run_id INTEGER, step TEXT, started TEXT, finished TEXT, status TEXT, summary TEXT, hint TEXT, output TEXT,
  PRIMARY KEY (run_id, step));
-- почта: заголовки писем и решения по отправителям
CREATE TABLE IF NOT EXISTS emails (
    uid INTEGER PRIMARY KEY, msg_id TEXT, date TEXT, from_addr TEXT, from_name TEXT, domain TEXT,
    subject TEXT, size INTEGER, labels TEXT, path TEXT
);
CREATE INDEX IF NOT EXISTS emails_domain ON emails(domain);
CREATE TABLE IF NOT EXISTS mail_senders (domain TEXT PRIMARY KEY, status TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS items_purchase ON items(purchase_id);
CREATE INDEX IF NOT EXISTS purchases_date ON purchases(date);
CREATE INDEX IF NOT EXISTS payments_purchase ON payments(purchase_id);
"""


def add_columns(con, table: str, cols: dict):
    have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
    for col, kind in cols.items():
        if col not in have:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")


def m1_legacy_columns(con):
    """Колонки, которые раньше добавлялись по месту (connect, сверка)."""
    add_columns(con, "items", {"section": "TEXT", "group_code": "TEXT"})  # отдел магазина, товарная группа магазина
    # seen — когда операция впервые пришла из банка (для порядка с пересчётами наличных)
    add_columns(con, "bank_tx", {"category_id": "INTEGER", "category_source": "TEXT", "seen": "TEXT"})


def m2_keys_refunds_doubts(con):
    add_columns(con, "categories", {"key": "TEXT"})  # постоянный ключ системной категории: «food.meat»
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS categories_key ON categories(key) WHERE key IS NOT NULL")
    # refund_of — возврат: id исходной покупки; status — doubt («под вопросом», в суммы не идёт) или NULL;
    # orig_total / orig_* — сумма из чека до поправки сверкой под фактическое списание в банке
    add_columns(con, "purchases", {"refund_of": "TEXT", "status": "TEXT", "orig_total": "REAL"})
    add_columns(con, "items", {"orig_amount": "REAL", "orig_unit_price": "REAL", "orig_discount": "REAL"})
    con.execute("CREATE INDEX IF NOT EXISTS purchases_bank ON purchases(bank_tx_id)")


def m3_single_link(con):
    """Связь с выпиской — только purchases.bank_tx_id (bank_tx.purchase_id дублировал её списком через запятую)."""
    if "purchase_id" in {r["name"] for r in con.execute("PRAGMA table_info(bank_tx)")}:
        con.execute("ALTER TABLE bank_tx DROP COLUMN purchase_id")


MIGRATIONS = [m1_legacy_columns, m2_keys_refunds_doubts, m3_single_link]  # номер миграции = позиция + 1; только дописывать в конец


def migrate(con):
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    for n, step in enumerate(MIGRATIONS[ver:], ver + 1):
        step(con)
        con.execute(f"PRAGMA user_version = {n}")
        con.commit()


def connect() -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)  # обновление в фоне и интерфейс могут писать одновременно
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    if con.execute("PRAGMA user_version").fetchone()[0] < len(MIGRATIONS):
        migrate(con)
    return con


def save_purchase(con, p: dict, items: list[dict], payments: list[dict] = ()):
    """Записать покупку целиком (повторная запись того же id заменяет старую).
    Ручные правки категорий у позиций сохраняются, если позиция не изменилась. Удалённые тобой покупки не записываются."""
    if con.execute("SELECT 1 FROM hidden_purchases WHERE id = ?", (p["id"],)).fetchone():
        return
    manual = {(r["line"], r["name"]): (r["category_id"], r["category_source"]) for r in con.execute(
        "SELECT line, name, category_id, category_source FROM items WHERE purchase_id = ? AND category_source = 'manual'",
        (p["id"],))}
    link = con.execute("SELECT bank_tx_id, note FROM purchases WHERE id = ?", (p["id"],)).fetchone()
    con.execute("DELETE FROM items WHERE purchase_id = ?", (p["id"],))
    con.execute("DELETE FROM payments WHERE purchase_id = ?", (p["id"],))
    discount = p.get("discount")
    if discount is None:
        discount = round(sum(i.get("discount") or 0 for i in items), 2)
    methods = {x["method"] for x in payments}
    method = p.get("payment_method") or (methods.pop() if len(methods) == 1 else ("mixed" if methods else None))
    card = p.get("card_last4") or next((x.get("card_last4") for x in payments if x.get("card_last4")), None)
    con.execute("""INSERT OR REPLACE INTO purchases
        (id, source, date, merchant, store, total, discount, currency, payment_method, card_last4, raw_path, bank_tx_id, note)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (p["id"], p["source"], p.get("date"), p.get("merchant"), p.get("store"), p.get("total"), discount,
                 p.get("currency", "PLN"), method, card, p.get("raw_path"),
                 link["bank_tx_id"] if link else None, link["note"] if link else None))
    con.executemany("INSERT INTO payments VALUES (?,?,?,?)",
                    [(p["id"], x["method"], x.get("amount"), x.get("card_last4")) for x in payments])
    rows = []
    for n, i in enumerate(items, 1):
        cat, src = manual.get((n, i["name"]), (None, None))
        rows.append((p["id"], n, i["name"], i.get("product_code"), i.get("qty"), i.get("unit_price"),
                     i.get("amount"), i.get("discount"), cat, src, i.get("section"), i.get("group_code")))
    con.executemany("""INSERT INTO items (purchase_id, line, name, product_code, qty, unit_price, amount, discount,
                       category_id, category_source, section, group_code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", rows)


def get_meta(con, key: str, default=None):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(con, key: str, value):
    con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))
