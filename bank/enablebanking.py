"""Выписка PKO через Enable Banking (PSD2, только чтение; бесплатный режим для своих счетов).

  python budget.py bank login     согласие в PKO (окно Chrome, входишь в iPKO сам) -> сессия на срок согласия
  python budget.py bank sync      скачать операции и остатки (повторно — только новые)
  python budget.py bank status    срок согласия, последняя операция, остаток
  python budget.py bank import [файлы]   выписка из файла (CSV, MT940, camt.053), без файлов — из bank/inbox/

Нужен закрытый ключ приложения: data/bank/<application-id>.pem (скачивается при регистрации приложения).
"""
import datetime as dt
import json
import re
import time
import uuid
from pathlib import Path

import requests

from core.browser_login import wait_for_redirect
from core.common import DATA, money
from core.db import connect, get_meta, set_meta

BANK = DATA / "bank"
SESSION = BANK / "session.json"
API = "https://api.enablebanking.com"
REDIRECT = "https://localhost/enablebanking"
HISTORY_FROM = "2024-07-01"


def app_key() -> tuple[str, str]:
    """-> (application_id, private key PEM). Ключ ищем в data/bank/*.pem, id — имя файла."""
    keys = sorted(BANK.glob("*.pem"))
    if not keys:
        raise SystemExit(f"Нет закрытого ключа приложения: положи <application-id>.pem в {BANK}")
    if len(keys) > 1:
        raise SystemExit(f"В {BANK} несколько .pem — оставь один (ключ нужного приложения)")
    return keys[0].stem, keys[0].read_text(encoding="utf-8")


def headers() -> dict:
    import jwt

    app_id, key = app_key()
    now = int(time.time())
    token = jwt.encode({"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + 3600},
                       key, algorithm="RS256", headers={"kid": app_id})
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def call(method: str, path: str, **kw):
    for attempt in range(4):  # сеть/DNS иногда моргает — повторяем с паузой
        try:
            r = requests.request(method, API + path, headers=headers(), timeout=60, **kw)
        except requests.exceptions.ConnectionError as e:
            if attempt == 3:
                raise SystemExit(f"Нет связи с Enable Banking ({e.__class__.__name__}). Уже скачанное сохранено — "
                                 f"запусти bank sync ещё раз, когда интернет будет в порядке.")
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code in (429, 502, 503, 504) and attempt < 3:
            time.sleep(10 * (attempt + 1))
            continue
        break
    if r.status_code >= 400:
        raise SystemExit(f"Enable Banking {method} {path}: {r.status_code} {r.text[:400]}")
    return r.json()


def find_pko() -> dict:
    aspsps = call("GET", "/aspsps", params={"country": "PL"}).get("aspsps", [])
    pko = [a for a in aspsps if "pko" in a["name"].lower() and "biznes" not in a["name"].lower()]
    if not pko:
        raise SystemExit("PKO не найден в списке банков Enable Banking: " + ", ".join(a["name"] for a in aspsps[:40]))
    personal = [a for a in pko if "personal" in (a.get("psu_types") or ["personal"])]
    return (personal or pko)[0]


# ---------------------------------------------------------------- согласие (раз в несколько месяцев)

def login():
    aspsp = find_pko()
    seconds = aspsp.get("maximum_consent_validity") or 90 * 86400
    valid_until = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds - 3600)).isoformat(timespec="seconds")
    state = str(uuid.uuid4())
    auth = call("POST", "/auth", json={"access": {"valid_until": valid_until},
                                       "aspsp": {"name": aspsp["name"], "country": "PL"},
                                       "state": state, "redirect_url": REDIRECT, "psu_type": "personal"})
    print(f"Банк: {aspsp['name']}, согласие до {valid_until[:10]}.")
    callback = wait_for_redirect(auth["url"], REDIRECT, "PKO (через Enable Banking)")
    if not callback:
        return
    from urllib.parse import parse_qs, urlsplit
    q = parse_qs(urlsplit(callback).query)
    if q.get("state", [None])[0] != state:
        raise SystemExit("Ответ банка не совпал с запросом (state) — попробуй ещё раз.")
    if "error" in q:
        raise SystemExit(f"Банк отказал: {q.get('error')} {q.get('error_description')}")
    session = call("POST", "/sessions", json={"code": q["code"][0]})
    BANK.mkdir(parents=True, exist_ok=True)
    SESSION.write_text(json.dumps({"session_id": session["session_id"], "valid_until": valid_until,
                                   "accounts": session.get("accounts", []), "aspsp": aspsp["name"]},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Доступ получен до {valid_until[:10]}. Счетов: {len(session.get('accounts', []))}.")
    print("Дальше: python budget.py bank sync")


def session() -> dict:
    try:
        s = json.loads(SESSION.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit("Сначала: python budget.py bank login")
    if s["valid_until"] < dt.datetime.now(dt.timezone.utc).isoformat():
        raise SystemExit("Согласие банка истекло — продли: python budget.py bank login")
    return s


# ---------------------------------------------------------------- операции

def tx_id(t: dict, account: str) -> str:
    ref = t.get("entry_reference") or t.get("transaction_id")
    if ref:
        return f"{account[:8]}:{ref}"
    import hashlib
    key = json.dumps([t.get("booking_date"), t.get("transaction_amount"), t.get("remittance_information"),
                      (t.get("creditor") or {}).get("name"), (t.get("debtor") or {}).get("name")], sort_keys=True)
    return f"{account[:8]}:h{hashlib.sha1(key.encode()).hexdigest()[:16]}"


def parse_tx(t: dict, account: str) -> tuple:
    """PKO: тип операции — последняя строка remittance_information (CARD-PAYMENT, MOBILE-PAYMENT-C2C, FEE...),
    описание — остальные строки; остаток — balance_after_transaction.amount."""
    amount = float(t["transaction_amount"]["amount"])
    if t.get("credit_debit_indicator") == "DBIT":
        amount = -abs(amount)
    other = (t.get("creditor") if amount < 0 else t.get("debtor")) or {}
    info = [s for s in (t.get("remittance_information") or []) if s]
    kind = info[-1] if info and re.fullmatch(r"[A-Z0-9-]+", info[-1]) else \
        (t.get("bank_transaction_code") or {}).get("description")
    desc = " ".join(info[:-1] if kind and info and info[-1] == kind else info)
    bal_obj = t.get("balance_after_transaction") or {}
    bal = bal_obj.get("amount", (bal_obj.get("balance_amount") or {}).get("amount"))
    return (tx_id(t, account), t.get("booking_date") or t.get("value_date") or t.get("transaction_date"), amount,
            float(bal) if bal not in (None, "") else None, kind, other.get("name"), desc,
            json.dumps(t, ensure_ascii=False))


def reparse():
    """Пересобрать поля операций из сохранённого ответа банка (после правки разбора)."""
    con = connect()
    rows = con.execute("SELECT id, raw FROM bank_tx WHERE id NOT LIKE '%:F%'").fetchall()  # из файлов — не ответ банка
    for r in rows:
        account = r["id"].split(":")[0]
        p = parse_tx(json.loads(r["raw"]), account)
        con.execute("UPDATE bank_tx SET date=?, amount=?, balance=?, type=?, counterparty=?, description=? WHERE id=?",
                    (*p[1:7], r["id"]))
    con.commit()
    print(f"Операций пересобрано: {len(rows)}")


def sync():
    s = session()
    con = connect()
    total_new = 0
    seen = dt.datetime.now().isoformat(timespec="seconds")  # операции этой загрузки появились в банке не позже этого момента
    for acc in s["accounts"]:
        uid = acc["uid"] if isinstance(acc, dict) else acc
        date_from = get_meta(con, f"bank_from:{uid}") or HISTORY_FROM
        params, n = {"date_from": date_from}, 0
        while True:
            data = call("GET", f"/accounts/{uid}/transactions", params=params)
            rows = [parse_tx(t, uid) for t in data.get("transactions", []) if t.get("status", "BOOK") == "BOOK"]
            before = con.total_changes
            # колонки явно: сверка дописывает в bank_tx свои (purchase_id, category_id, category_source)
            con.executemany("INSERT OR IGNORE INTO bank_tx (id, date, amount, balance, type, counterparty, description, raw, seen) "
                            "VALUES (?,?,?,?,?,?,?,?,?)", [r + (seen,) for r in rows])
            n += con.total_changes - before
            con.commit()  # каждая страница сохраняется сразу — обрыв связи не теряет скачанное
            if not data.get("continuation_key"):
                break
            params = {"date_from": date_from, "continuation_key": data["continuation_key"]}
        bal = call("GET", f"/accounts/{uid}/balances").get("balances", [])
        if bal:
            b = bal[0]
            set_meta(con, f"bank_balance:{uid}", json.dumps({"amount": b["balance_amount"]["amount"],
                                                              "date": b.get("reference_date") or dt.date.today().isoformat()}))
        last = con.execute("SELECT max(date) FROM bank_tx WHERE id LIKE ?", (uid[:8] + ":%",)).fetchone()[0]
        if last:
            # следующая загрузка — с запасом в 5 дней: банк может дописать операции задним числом
            set_meta(con, f"bank_from:{uid}", (dt.date.fromisoformat(last) - dt.timedelta(days=5)).isoformat())
        total_new += n
        print(f"Счёт {(acc.get('account_id') or {}).get('iban', uid) if isinstance(acc, dict) else uid}: "
              f"новых операций {n}, последняя {last}")
    last_all = con.execute("SELECT max(date) FROM bank_tx").fetchone()[0]
    set_meta(con, "bank_last_date", last_all)
    syncs = json.loads(get_meta(con, "bank_syncs") or "[]")[-199:] + [seen]  # нужны кошельку: окно, когда появилась операция
    set_meta(con, "bank_syncs", json.dumps(syncs))
    con.commit()
    print(f"Всего новых: {total_new}. Данные банка по {last_all}.")


def status():
    s = session()
    con = connect()
    print(f"Банк: {s['aspsp']}, согласие до {s['valid_until'][:10]}")
    n, first, last = con.execute("SELECT count(*), min(date), max(date) FROM bank_tx").fetchone()
    print(f"Операций в базе: {n} ({first} — {last})")
    for acc in s["accounts"]:
        uid = acc["uid"] if isinstance(acc, dict) else acc
        if b := get_meta(con, f"bank_balance:{uid}"):
            b = json.loads(b)
            print(f"Остаток: {money(float(b['amount']))} на {b['date']}")


def main(argv: list[str]):
    cmd = argv[0] if argv else ""
    if cmd == "login":
        login()
    elif cmd == "sync":
        sync()
    elif cmd == "status":
        status()
    elif cmd == "reparse":
        reparse()
    elif cmd == "import":
        from bank import statement
        statement.main(argv[1:])
    else:
        print(__doc__)
