"""Lidl Plus: вход, выгрузка чеков через API приложения, разбор (старый JSON и новый HTML)."""
import base64
import hashlib
import html
import json
import re
import secrets
import time
import urllib.parse

import requests

from core.common import DATA, money, num
from core.db import connect, save_purchase

LIDL = DATA / "lidl"
RAW = LIDL / "raw"
TOKEN = LIDL / "token.json"

COUNTRY, LANG = "PL", "pl"
AUTH = "https://accounts.lidl.com"
CLIENT_ID = "LidlPlusNativeClient"
REDIRECT = "com.lidlplus.app://callback"
TICKETS = "https://tickets.lidlplus.com/api/{v}/" + COUNTRY + "/tickets"
APP_HEADERS = {"App-Version": "17.9.3", "Operating-System": "iOs",
               "App": "com.lidl.eci.lidl.plus", "Accept-Language": LANG}


# ---------------------------------------------------------------- вход (вариант 1)

def chrome_service():
    """Драйвер под установленный Chrome. Старый chromedriver из PATH игнорируем — он не подходит к новому Chrome."""
    from selenium import webdriver
    from selenium.webdriver.common.selenium_manager import SeleniumManager

    paths = SeleniumManager().binary_paths(["--browser", "chrome", "--skip-driver-in-path"])
    return webdriver.ChromeService(executable_path=paths["driver_path"])


def authorize_url(challenge: str) -> str:
    return f"{AUTH}/connect/authorize?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID, "response_type": "code", "redirect_uri": REDIRECT,
        "scope": "openid profile offline_access lpprofile lpapis",
        "code_challenge": challenge, "code_challenge_method": "S256",
        "Country": COUNTRY, "language": f"{LANG}-{COUNTRY}"})


def login():
    """PKCE-вход как в приложении. Логин/пароль/код вводишь ты в окне Chrome — скрипт их не видит,
    он только ловит в сетевом логе браузера редирект с одноразовым кодом и меняет его на токен."""
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = authorize_url(challenge)

    opts = webdriver.ChromeOptions()
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    opts.add_argument("--window-size=520,900")
    print("Открываю окно входа Lidl. Войди как в приложении (email/телефон, пароль, код).")
    print("Окно закроется само, когда вход пройдёт. Ждём до 10 минут...")
    driver = webdriver.Chrome(options=opts, service=chrome_service())
    code = None
    try:
        driver.get(url)
        deadline = time.time() + 600
        while time.time() < deadline and not code:
            time.sleep(1)
            try:
                logs = driver.get_log("performance")
                current = driver.current_url
            except WebDriverException:
                print("Окно браузера закрыто — вход прерван.")
                return
            for entry in logs + [{"message": current}]:
                m = re.search(r"com\.lidlplus\.app://callback\?[^\"'\s]*?code=([0-9A-Za-z_\-]+)", entry["message"])
                if m:
                    code = m.group(1)
                    break
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass
    if not code:
        print("Не дождался входа. Попробуй ещё раз.")
        return
    global _access
    _access = token_request({"grant_type": "authorization_code", "code": code,
                             "redirect_uri": REDIRECT, "code_verifier": verifier})
    print("Вход выполнен, токен сохранён в", TOKEN)
    print("ВАЖНО: token.json — это доступ к твоему аккаунту Lidl Plus, никому не отдавай.")
    tickets = list_tickets()
    print(f"Чеков в аккаунте: {len(tickets)}. Теперь: python budget.py lidl sync")


def token_request(payload: dict) -> str:
    secret = base64.b64encode(f"{CLIENT_ID}:secret".encode()).decode()
    for _ in range(3):
        r = requests.post(f"{AUTH}/connect/token", data=payload, timeout=30,
                          headers={"Authorization": f"Basic {secret}",
                                   "Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code == 200 or payload["grant_type"] != "refresh_token":
            break
        time.sleep(5)  # свежий токен иногда ещё не разошёлся по серверам Lidl
    if r.status_code != 200:
        raise SystemExit(f"Lidl не выдал токен ({r.status_code}): {r.text[:300]}\n"
                         f"Если вход устарел — python budget.py lidl login")
    tok = r.json()
    LIDL.mkdir(parents=True, exist_ok=True)
    # refresh-токен одноразовый: каждый раз сохраняем новый
    TOKEN.write_text(json.dumps({"refresh_token": tok["refresh_token"]}), encoding="utf-8")
    return tok["access_token"]


_access = None


def headers() -> dict:
    global _access
    if not _access:
        try:
            refresh = json.loads(TOKEN.read_text(encoding="utf-8"))["refresh_token"]
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            raise SystemExit("Сначала войди: python budget.py lidl login")
        _access = token_request({"grant_type": "refresh_token", "refresh_token": refresh})
    return {"Authorization": f"Bearer {_access}", **APP_HEADERS}


def list_tickets() -> list[dict]:
    out, page = [], 1
    while True:
        r = requests.get(TICKETS.format(v="v2"), params={"pageNumber": page, "onlyFavorite": "false"},
                         headers=headers(), timeout=60)
        r.raise_for_status()
        data = r.json()
        out += data.get("tickets", [])
        if page * data.get("size", 10) >= data.get("totalCount", 0) or not data.get("tickets"):
            return out
        page += 1


def fetch_ticket(tid: str) -> dict:
    """Для PL детали отдаёт только v3 (v2 отвечает 400)."""
    for v in ("v3", "v2"):
        r = requests.get(f"{TICKETS.format(v=v)}/{tid}", headers=headers(), timeout=60)
        if r.ok:
            return r.json()
    r.raise_for_status()


# ---------------------------------------------------------------- разбор чека из API

PAYMENT_TYPES = {"creditcard": "card", "debitcard": "card", "card": "card", "cash": "cash",
                  "karta płatnicza": "card", "karta": "card", "gotówka": "cash", "blik": "blik",
                  "bon": "voucher", "voucher": "voucher", "kupon": "voucher"}


def payment_method(label: str) -> str | None:
    low = (label or "").lower()
    return next((v for k, v in PAYMENT_TYPES.items() if k in low), None)


def parse_payments(t: dict) -> list[dict]:
    out = []
    for p in t.get("payments") or []:  # старый формат
        card = (p.get("cardInfo") or {}).get("accountNumber") or ""
        out.append({"method": payment_method(p.get("type")) or payment_method(p.get("description")),
                    "amount": num(p.get("amount")), "card_last4": card[-4:] or None})
    if not out and t.get("htmlPrintedReceipt"):  # новый формат: строка «Płatność Karta płatnicza 39,74»
        text = html.unescape(re.sub(r"<[^>]+>", " ", t["htmlPrintedReceipt"]))
        for label, amount in re.findall(r"Płatność\s+(.+?)\s+(-?\d+,\d{2})", text):
            out.append({"method": payment_method(label), "amount": num(amount), "card_last4": None})
    return out


def parse_api_ticket(t: dict, tid: str, raw_path: str):
    """-> (покупка, позиции, оплаты) в формате core.db.save_purchase"""
    store = t.get("store") or {}
    if isinstance(store, dict):  # в name у Lidl PL уже «<город>, ul. ...»
        store_name = store.get("name") or " ".join(str(store[k]) for k in ("address", "locality") if store.get(k))
    else:
        store_name = str(store)
    items = []
    for it in t.get("itemsLine") or []:
        disc = sum(abs(num(d.get("amount")) or 0) for d in it.get("discounts") or [])
        qty = num(it.get("quantity")) or 1
        unit = num(it.get("currentUnitPrice"))
        amount = num(it.get("originalAmount"))
        if amount is None and unit is not None:
            amount = round(unit * qty, 2)
        items.append({"name": (it.get("name") or "").strip(), "product_code": it.get("codeInput"),
                      "qty": qty, "unit_price": unit, "amount": amount, "discount": round(disc, 2) or None})
    if not items and t.get("htmlPrintedReceipt"):
        items = parse_html_receipt(t["htmlPrintedReceipt"])
    purchase = {"id": f"lidl:{tid}", "source": "lidl", "date": (t.get("date") or "")[:19], "merchant": "Lidl",
                "store": store_name, "total": num(t.get("totalAmount")), "raw_path": raw_path}
    payments = parse_payments(t)
    paid = sum(p["amount"] or 0 for p in payments)
    if payments and purchase["total"] is not None and paid > purchase["total"]:
        # наличными дали 50/100 zł, в чеке записана купюра — вычитаем сдачу из наличной оплаты
        cash = next((p for p in payments if p["method"] == "cash"), payments[-1])
        cash["amount"] = round(cash["amount"] - (paid - purchase["total"]), 2)
    return purchase, items, payments


def check_total(purchase: dict, items: list[dict]) -> str:
    s = sum(i["amount"] or 0 for i in items) - sum(i["discount"] or 0 for i in items)
    total = purchase["total"]
    return "" if total is None or abs(s - total) < 0.05 else f"⚠ позиции {s:.2f} ≠ итог {total:.2f}"


SPAN_RE = re.compile(r'<span id="purchase_list_line_\d+" class="(article|discount)"([^>]*)>(.*?)</span>', re.S)
ATTR_RE = re.compile(r'data-([a-z-]+)="([^"]*)"')
# «2 * 10.99 21.98 C» и весовые «1,094kg x 24.8 27.13 C»
QTY_LINE_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*(?:kg|szt\.?)?\s*[x×*]\s*(-?\d+(?:[.,]\d{1,2})?)\s+(-?\d+[.,]\d{2})")


def parse_html_receipt(html_text: str) -> list[dict]:
    """Новые чеки Lidl PL: у позиции две строки <span class="article" data-...> —
    название и «2 * 10.99 21.98 C»; скидки — <span class="discount">Lidl Plus kupon -11,00</span>."""
    items, cur = [], None
    for kind, attrs, body in SPAN_RE.findall(html_text):
        text = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
        if kind == "discount":
            if items and (m := re.search(r"-?\d+[.,]\d{2}", text)):
                items[-1]["discount"] = round((items[-1]["discount"] or 0) + abs(num(m.group())), 2)
            continue
        a = dict(ATTR_RE.findall(attrs))
        if m := QTY_LINE_RE.search(text):
            if cur is None or cur["amount"] is not None:  # строка с суммой без строки-названия
                cur = new_item(a)
                items.append(cur)
            cur["qty"], cur["unit_price"], cur["amount"] = num(m.group(1)), num(m.group(2)), num(m.group(3))
        else:
            cur = new_item(a)
            items.append(cur)
    return items


def new_item(a: dict) -> dict:
    return {"name": html.unescape(a.get("art-description", "")).strip(), "product_code": a.get("art-id"),
            "qty": num(a.get("art-quantity")) or 1, "unit_price": num(a.get("unit-price")),
            "amount": None, "discount": None}


def rel(path) -> str:
    return str(path.relative_to(DATA)).replace("\\", "/")


def sync():
    con = connect()
    known = {r[0] for r in con.execute("SELECT id FROM purchases WHERE source = 'lidl'")}
    tickets = list_tickets()
    new = [t for t in tickets if f"lidl:{t.get('id')}" not in known]
    print(f"Чеков в аккаунте: {len(tickets)}, новых: {len(new)}")
    RAW.mkdir(parents=True, exist_ok=True)
    for n, t in enumerate(new, 1):
        tid = str(t["id"])
        data = fetch_ticket(tid)
        raw = RAW / f"{tid}.json"
        raw.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        purchase, items, payments = parse_api_ticket(data, tid, rel(raw))
        purchase["date"] = purchase["date"] or (t.get("date") or "")[:19]
        save_purchase(con, purchase, items, payments)
        con.commit()
        warn = check_total(purchase, items)
        print(f"  [{n}/{len(new)}] {purchase['date'][:10]}  {money(purchase['total'] or 0):>10}  позиций: {len(items)}  {warn}")
        time.sleep(0.3)


def reparse():
    """Пересобрать покупки Lidl из сохранённых JSON (после правки парсера), без повторной загрузки."""
    con = connect()
    files = sorted(RAW.glob("*.json"))
    bad = 0
    for raw in files:
        data = json.loads(raw.read_text(encoding="utf-8"))
        purchase, items, payments = parse_api_ticket(data, raw.stem, rel(raw))
        save_purchase(con, purchase, items, payments)
        if warn := check_total(purchase, items):
            bad += 1
            print(f"  {purchase['date'][:10]} {raw.name}: {warn}")
    con.commit()
    print(f"Lidl: пересобрано чеков {len(files)}, не сходится: {bad}")


def main(argv: list[str]):
    cmd = argv[0] if argv else ""
    if cmd == "login":
        login()
    elif cmd == "sync":
        sync()
    elif cmd == "reparse":
        reparse()
    else:
        print("python budget.py lidl login | sync | reparse")

