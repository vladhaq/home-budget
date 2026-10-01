"""Единая база бюджета: data/budget.db.

purchases — покупка (чек, онлайн-заказ), источник любой;
items     — позиции покупки;
bank_tx   — операции из выписки (проверка полноты данных);
categories / rules — дерево категорий и правила назначения;
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
CREATE INDEX IF NOT EXISTS items_purchase ON items(purchase_id);
CREATE INDEX IF NOT EXISTS purchases_date ON purchases(date);
CREATE INDEX IF NOT EXISTS payments_purchase ON payments(purchase_id);
"""


ITEM_EXTRA_COLUMNS = {"section": "TEXT", "group_code": "TEXT"}  # отдел магазина, товарная группа магазина
# когда операция впервые пришла из банка: банк даёт только дату, а для наличных важен порядок с твоими пересчётами
BANK_EXTRA_COLUMNS = {"seen": "TEXT"}


def connect() -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)  # обновление в фоне и интерфейс могут писать одновременно
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    have = {r["name"] for r in con.execute("PRAGMA table_info(items)")}
    for col, kind in ITEM_EXTRA_COLUMNS.items():
        if col not in have:
            con.execute(f"ALTER TABLE items ADD COLUMN {col} {kind}")
    have = {r["name"] for r in con.execute("PRAGMA table_info(bank_tx)")}
    for col, kind in BANK_EXTRA_COLUMNS.items():
        if col not in have:
            con.execute(f"ALTER TABLE bank_tx ADD COLUMN {col} {kind}")
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
