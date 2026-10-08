"""Kaufland Card: вход, выгрузка электронных чеков, разбор.

API приложения Kaufland (группа Schwarz, как и Lidl): вход через cidaas на account.kaufland.com,
чеки — p.crm-dynamics.schwarz/api/v2/customers/{user}/transactions. Неофициально, может сломаться.
Идентификаторы клиента и эндпоинты — из открытого проекта Bonfire (github.com/Naxter/bonfire).
"""
import base64
import datetime as dt
import json
import re
import secrets
import time
import urllib.parse
from zoneinfo import ZoneInfo

import requests

from core.browser_login import pkce, wait_for_redirect
from core.common import DATA, money, num
from core.db import connect, save_purchase
from core.logins import alive, record

KAUF = DATA / "kaufland"
RAW = KAUF / "raw"
TOKEN = KAUF / "token.json"

COUNTRY = "PL"
AUTH = "https://account.kaufland.com"
CLIENT_ID = "fb1b425b-ab2f-4140-aef9-20263b6cfa49"          # вход (cidaas)
LOYALTY_CLIENT_ID = "88207bfc-780b-400d-92ee-893ae72dab40"  # API программы лояльности
API = "https://p.crm-dynamics.schwarz"
REDIRECT = "com.kaufland.kaufland://oauth/callback"
OAUTH_V = "1.5.22"
APP_HEADERS = {"client-id": LOYALTY_CLIENT_ID, "app-platform": "Android", "app-version": "6.17.1",
               "Accept": "application/json"}
PAGE = 20
TZ = ZoneInfo("Europe/Warsaw")


# ---------------------------------------------------------------- вход и токены

def login():
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(16)
    url = f"{AUTH}/authz-srv/authz?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID, "response_type": "code", "redirect_uri": REDIRECT, "ui_locales": "pl-PL",
        "v": OAUTH_V, "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "view_type": "login"})
    callback = wait_for_redirect(url, "com.kaufland.kaufland://", "Kaufland")
    if not callback:
        return
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(callback).query)
    if params.get("state", [None])[0] != state:
        raise SystemExit("Ответ входа не совпал с запросом (state) — попробуй ещё раз.")
    tok = token_request({"grant_type": "authorization_code", "code": params["code"][0], "client_id": CLIENT_ID,
                         "redirect_uri": REDIRECT, "code_verifier": verifier, "v": OAUTH_V})
    user = requests.get(f"{AUTH}/users-srv/userinfo", timeout=30,
                        headers={"Accept": "application/json", "Authorization": f"Bearer {tok['access_token']}"})
    user.raise_for_status()
    info = user.json()
    user_id = next((str(v) for v in (info.get("sub"), info.get("user_id"), info.get("userId")) if v), None)
    if not user_id:
        raise SystemExit(f"Не нашёл id пользователя в ответе Kaufland: поля {list(info)}")
    save_token(tok, user_id)
    record("kaufland")
    print("Вход в Kaufland выполнен. Токен сохранён в", TOKEN)
    print("ВАЖНО: token.json — доступ к твоему аккаунту Kaufland, никому не отдавай.")


def token_request(form: dict) -> dict:
    r = requests.post(f"{AUTH}/token-srv/token", data=form, timeout=30,
                      headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code != 200:
        if form["grant_type"] == "refresh_token" and r.status_code in (400, 401):
            record("kaufland", "expired")  # для «среднего срока жизни токена» на странице «Настройки»
        raise SystemExit(f"Kaufland не выдал токен ({r.status_code}): {r.text[:300]}\n"
                         f"Если вход устарел — python budget.py kaufland login")
    return r.json()


def save_token(tok: dict, user_id: str):
    KAUF.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(json.dumps({"refresh_token": tok.get("refresh_token"), "access_token": tok["access_token"],
                                 "expires_at": time.time() + int(tok.get("expires_in", 300)), "user_id": user_id}),
                     encoding="utf-8")


def session() -> tuple[str, str]:
    """-> (access_token, user_id), с обновлением токена при необходимости."""
    try:
        t = json.loads(TOKEN.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit("Сначала войди: python budget.py kaufland login")
    if t["expires_at"] > time.time() + 60:
        return t["access_token"], t["user_id"]
    if not t.get("refresh_token"):
        raise SystemExit("Вход в Kaufland устарел: python budget.py kaufland login")
    tok = token_request({"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": CLIENT_ID,
                         "v": OAUTH_V})
    tok.setdefault("refresh_token", t["refresh_token"])
    save_token(tok, t["user_id"])
    alive("kaufland")
    return tok["access_token"], t["user_id"]


def list_transactions() -> list[dict]:
    access, user_id = session()
    out, start = [], 0
    while True:
        url = f"{API}/api/v2/customers/{urllib.parse.quote(user_id, safe='')}/transactions"
        r = requests.get(url, params={"start": start, "limit": PAGE, "country": COUNTRY, "version": 2},
                         headers={**APP_HEADERS, "Authorization": f"Bearer {access}"}, timeout=60)
        if r.status_code == 401:
            record("kaufland", "expired")
            raise SystemExit("Kaufland отклонил токен: python budget.py kaufland login")
        if r.status_code >= 400:
            raise SystemExit(f"Kaufland вернул {r.status_code}: {r.text[:300]}")
        data = r.json()
        page = extract(data)
        out += page
        if len(page) < PAGE:
            return out
        start += PAGE
        time.sleep(0.3)


def extract(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    for container in (payload, payload.get("data") if isinstance(payload, dict) else None):
        if isinstance(container, dict):
            for key in ("transactions", "items", "receipts", "results", "data"):
                if isinstance(container.get(key), list):
                    return container[key]
    raise SystemExit(f"Неожиданный ответ Kaufland: поля {list(payload) if isinstance(payload, dict) else type(payload)}")


# ---------------------------------------------------------------- разбор

def money_value(v):
    """Суммы Kaufland: целые — гроши (349 = 3,49 zł), строки/дроби — злотые."""
    if v is None or v == "":
        return None
    if isinstance(v, dict):
        v = v.get("value", v.get("amount"))
    if isinstance(v, int):
        return v / 100
    return num(v)


def local_time(v) -> str:
    """'2026-03-08T12:30:00Z' (UTC) -> '2026-03-08T13:30:00' (Варшава)."""
    s = str(v or "")
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s[:19]
    return (d.astimezone(TZ) if d.tzinfo else d).replace(tzinfo=None).isoformat(timespec="seconds")


PRICE_LINE = re.compile(r"^(.*?)\s+(-?\d+,\d{2})(?:\s+([A-E]))?$")
QTY_LINE = re.compile(r"^[\d,]+\s*(\*|KG)")
PAY_LINES = [(re.compile(r"^P[lł]atno[sś][cć] kart", re.I), "card"), (re.compile(r"^(Polski z[lł]oty|Got[oó]wk)", re.I), "cash"),
             (re.compile(r"^BLIK", re.I), "blik"), (re.compile(r"^(Bon|Karta podarunk|Voucher|Kupon)", re.I), "voucher")]


def receipt_text(t: dict) -> str:
    """Напечатанный чек (base64) без служебных меток форматирования вида &1...&1."""
    try:
        return re.sub(r"&\d", "", base64.b64decode(t.get("receiptText") or "").decode("utf-8", "replace"))
    except (ValueError, TypeError):
        return ""


def parse_receipt_text(text: str, names: set[str]) -> dict:
    """Из текста чека: строки товаров с отделом Kaufland, акции с номерами позиций, способы оплаты, карта."""
    lines = [ln.strip() for ln in text.splitlines()]
    start = next((i for i, ln in enumerate(lines) if ln.startswith("Cena PLN")), None)
    if start is None:
        return {"lines": [], "promos": [], "payments": [], "card": None}
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("Suma")), len(lines))
    section, pending, out = None, None, []
    for ln in lines[start + 1:end]:
        if not ln:
            continue
        m = PRICE_LINE.match(ln)
        if m and QTY_LINE.match(ln) and pending:            # « 2 * 8,99   17,98 C» / « 0,202 KG  10,08 C»
            out.append({"name": pending, "amount": num(m.group(2)), "section": section})
            pending = None
        elif m and m.group(1).strip():                      # «TymbarkNapój 3,19 C», «Ziemniaki kg 1,918 KG 4,78 C»
            name = re.sub(r"\s+[\d,]+\s*KG$", "", m.group(1).strip())
            out.append({"name": name, "amount": num(m.group(2)), "section": section, "taxed": bool(m.group(3))})
        elif ln in names:                                    # название, сумма — на следующей строке
            pending = ln
        else:
            section = ln                                     # заголовок отдела
    promos, n = [], 0
    tail = lines[end:]
    if "Promocja" in "\n".join(lines):
        p0 = next(i for i, ln in enumerate(lines) if "Promocja" in ln)
        block = lines[p0 + 1:]
        while n < len(block) and not block[n].startswith("Suma"):
            m = re.match(r"^(.*?)\s+(-\d+,\d{2})$", block[n])
            if m:
                pos = []
                if n + 1 < len(block) and (pm := re.match(r"^Pozycje:\s*([\d,\s]+)", block[n + 1])):
                    pos = [int(x) for x in re.findall(r"\d+", pm.group(1))]
                promos.append({"desc": m.group(1).strip(), "amount": abs(num(m.group(2))), "pos": pos})
            n += 1
    payments = []
    # оплата — только после итоговой «Suma 54,67» (не «Suma cząstkowa»): «Kupon -10,00» выше — это скидка
    last_sum = max((i for i, ln in enumerate(lines) if re.match(r"^Suma\s+-?\d+,\d{2}$", ln)), default=end)
    tail = lines[last_sum + 1:]
    for ln in tail:
        for rx, method in PAY_LINES:
            if rx.match(ln) and (m := re.search(r"(\d+,\d{2})\s*$", ln)):
                payments.append({"method": method, "amount": num(m.group(1)), "card_last4": None})
    change = next((num(m.group(1)) for ln in tail if (m := re.match(r"^Reszta\s+(\d+,\d{2})", ln))), 0) or 0
    if change and payments:  # наличными дали 200, сдача 74,50 — записываем фактически потраченное
        cash = next((p for p in payments if p["method"] == "cash"), payments[-1])
        cash["amount"] = round(cash["amount"] - change, 2)
    card = next((m.group(1) for ln in tail if (m := re.search(r"#{6,}(\d{4})", ln))), None)
    # в распечатке чека оплата картой повторяется в слипе терминала — оставляем по одной записи на способ
    uniq = {}
    for p in payments:
        uniq.setdefault(p["method"], p)
    for p in uniq.values():
        if p["method"] == "card":
            p["card_last4"] = card
    return {"lines": out, "promos": promos, "payments": list(uniq.values()), "card": card}


def parse_transaction(t: dict, raw_path: str):
    store = t.get("store") if isinstance(t.get("store"), dict) else {}
    address = ", ".join(str(store[k]) for k in ("street", "city") if store.get(k))
    positions = t.get("positions") or []
    refunds = t.get("refundPositions") or []
    rt = parse_receipt_text(receipt_text(t), {str(p.get("name")) for p in positions})
    section_of = {}
    for ln in rt["lines"]:
        section_of.setdefault(ln["name"], ln["section"])

    def section(name):
        return section_of.get(name) or next((s for n, s in section_of.items() if n.startswith(name[:12])), None)

    # суммы позиций в API — уже ПОСЛЕ скидок; «2 × товар» API дробит на отдельные позиции
    items, by_pos = [], {}
    for pos in positions:
        net = money_value(pos.get("total"))
        unit = money_value(pos.get("unitPrice"))
        qty = num(pos.get("quantity")) or 1
        name = str(pos.get("name") or "?").strip()
        if str(pos.get("quantityUnit")).upper() == "KG":
            qty = None  # вес уточним после скидки: API пишет 1.0
        item = {"name": name, "product_code": pos.get("gtin") or pos.get("itemno"), "qty": qty, "unit_price": unit,
                "amount": net, "discount": None, "section": section(name), "group_code": pos.get("materialGroup"),
                "_kg": qty is None}
        items.append(item)
        by_pos[pos.get("pos")] = item
    known = {i["name"] for i in items}
    refund_amounts = []
    negative = [ln for ln in rt["lines"] if (ln["amount"] or 0) < 0]
    for pos in refunds:  # возврат тары и т.п. — отрицательные позиции (в чеке может быть одной строкой на все)
        amount = -abs(money_value(pos.get("total")) or 0)
        refund_amounts.append(amount)
        line = next((ln for ln in negative if abs(ln["amount"] - amount) < 0.005), negative[0] if negative else {})
        items.append({"name": line.get("name") or str(pos.get("name") or "zwrot"), "product_code": pos.get("gtin"),
                      "qty": num(pos.get("quantity")) or 1, "unit_price": -abs(money_value(pos.get("unitPrice")) or 0),
                      "amount": amount, "discount": None, "section": line.get("section"),
                      "group_code": pos.get("materialGroup")})
        known.add(line.get("name"))
    for ln in rt["lines"]:  # строки чека, которых нет в API: залог за банки/бутылки «Puszka zwrotna ALU 2,00»
        matched = any(n and (ln["name"].startswith(n[:12]) or n.startswith(ln["name"][:12])) for n in known)
        if (ln["amount"] or 0) < 0 and refunds:
            continue  # возврат тары уже учтён через refundPositions
        if not matched and ln["amount"] not in refund_amounts:
            items.append({"name": ln["name"], "product_code": None, "qty": 1, "unit_price": ln["amount"],
                          "amount": ln["amount"], "discount": None, "section": ln["section"], "group_code": None})

    leftover = 0.0
    printed = [ln for ln in rt["lines"] if (ln["amount"] or 0) > 0]
    for promo in rt["promos"]:
        # «Z Kaufland Card -1,77 / Pozycje:10» — 10-я напечатанная строка товара; API мог разбить её на несколько позиций
        names = {printed[p - 1]["name"] for p in promo["pos"] if 0 < p <= len(printed)}
        targets = [i for i in items if i["name"] in names or any(n.startswith(i["name"][:12]) for n in names)] \
            if names else []
        base = sum(i["amount"] or 0 for i in targets)
        if not targets or not base:
            leftover += promo["amount"]
            continue
        rest = promo["amount"]
        for n, i in enumerate(targets):
            share = rest if n == len(targets) - 1 else round(promo["amount"] * (i["amount"] or 0) / base, 2)
            rest = round(rest - share, 2)
            i["discount"] = round((i["discount"] or 0) + share, 2)
    for i in items:
        if i["discount"]:
            i["amount"] = round(i["amount"] + i["discount"], 2)  # цена без скидки = сумма API + скидка
        if i.pop("_kg", False):
            i["qty"] = round(i["amount"] / i["unit_price"], 3) if i["unit_price"] else 1
    total = money_value(t.get("sum"))
    if not rt["promos"] and t.get("saving"):
        # скидка без разбивки по позициям: суммы API уже её учитывают, показываем только как информацию
        leftover = 0.0
    payments = rt["payments"]
    if not payments:
        method = {"MAE": "card", "PLN": "cash"}.get(str(t.get("paymentMethods")), None)
        payments = [{"method": method, "amount": total, "card_last4": None}] if method else []
    purchase = {"id": f"kaufland:{t.get('id')}", "source": "kaufland", "date": local_time(t.get("timestamp")),
                "merchant": "Kaufland", "store": f"{store.get('name')}, {address}" if store.get("name") else address,
                "total": total, "raw_path": raw_path}
    if leftover:  # акция без номеров позиций: в суммах API она уже учтена, для итога — только информация
        purchase["discount"] = round(sum(i["discount"] or 0 for i in items) + leftover, 2)
        purchase["_leftover"] = leftover
    return purchase, items, payments


def check(purchase, items) -> str:
    if purchase["total"] is None or not items:
        return "⚠ нет позиций" if not items else ""
    s = sum((i["amount"] or 0) - (i["discount"] or 0) for i in items)
    return "" if abs(s - purchase["total"]) < 0.05 else f"⚠ позиции {s:.2f} ≠ итог {purchase['total']:.2f}"


def rel(path) -> str:
    return str(path.relative_to(DATA)).replace("\\", "/")


def sync():
    con = connect()
    known = {r[0] for r in con.execute("SELECT id FROM purchases WHERE source = 'kaufland'")}
    txs = list_transactions()
    new = [t for t in txs if f"kaufland:{t.get('id')}" not in known]
    print(f"Покупок в Kaufland Card: {len(txs)}, новых: {len(new)}")
    RAW.mkdir(parents=True, exist_ok=True)
    for n, t in enumerate(new, 1):
        raw = RAW / f"{t.get('id')}.json"
        raw.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
        purchase, items, payments = parse_transaction(t, rel(raw))
        save_purchase(con, purchase, items, payments)
        con.commit()
        print(f"  [{n}/{len(new)}] {purchase['date'][:10]} {money(purchase['total'] or 0):>10} "
              f"позиций {len(items)}  {check(purchase, items)}")
    if new and not any(parse_transaction(t, "")[1] for t in new):
        print("! Покупки скачались, но позиции не разобрались — формат ответа другой. Поля первой покупки:",
              sorted(new[0].keys()))


def reparse():
    con = connect()
    files = sorted(RAW.glob("*.json"))
    bad = 0
    for raw in files:
        purchase, items, payments = parse_transaction(json.loads(raw.read_text(encoding="utf-8")), rel(raw))
        save_purchase(con, purchase, items, payments)
        if warn := check(purchase, items):
            bad += 1
            print(f"  {purchase['date'][:10]} {raw.name}: {warn}")
    con.commit()
    print(f"Kaufland: пересобрано {len(files)}, с расхождениями: {bad}")


def main(argv: list[str]):
    cmd = argv[0] if argv else ""
    if cmd == "login":
        login()
    elif cmd == "sync":
        sync()
    elif cmd == "reparse":
        reparse()
    else:
        print("python budget.py kaufland login | sync | reparse")
