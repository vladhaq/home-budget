"""Download Biedronka receipt PDFs using the Moja Biedronka web session.

Первая загрузка — вся история с HISTORY_START; дальше — с даты последнего скачанного чека минус OVERLAP_DAYS
(сайт может показать чек с опозданием), чтобы каждое обновление не листало все месяцы заново.
Подключение (cookies из Chrome через расширение) и истечение сессии записываются в core/logins.py —
в «Настройках» видно, когда подключал и сколько сессия в среднем живёт.

У электронного чека (e-paragon) сайт отдаёт и JSON — точные позиции, цены в грошах, скидки, оплату: такой чек
записывается как есть (источник «biedronka»), без распознавания. JSON есть не у всех: у бумажных чеков его нет,
у части электронных сайт отвечает «Błąd pobrania pliku» — тогда PDF и распознавание, как раньше. Чек, раньше
распознанный из PDF, при появлении JSON заменяется точным, PDF прикрепляется к нему.
"""
import argparse
import datetime as dt
import json
import re
import time
from pathlib import Path

import requests

from core.common import DATA, num
from core.db import connect, save_purchase

PANEL_URL = "https://moja.biedronka.pl/panel/paragons"
AJAX_URL = "https://moja.biedronka.pl/panel/ajax/paragons"
ACCOUNT = DATA / "biedronka"
DOWNLOADS = ACCOUNT / "downloads"
COMPLETED = ACCOUNT / "downloaded-actions.json"
SESSION = ACCOUNT / "session.json"
JSON_DIR = ACCOUNT / "json"  # электронные чеки в JSON: {"receipt": поля из списка чеков, "eparagon": ответ сайта}
REQUEST_TIMEOUT = 60
HISTORY_START = dt.date(2023, 1, 1)
RECEIPT_ID = re.compile(r"^\d{8,32}$")
OVERLAP_DAYS = 14
CONNECTOR = Path(__file__).resolve().parent.parent / "tools" / "browser-connector"  # папка расширения для Chrome
POLISH_MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "września": 9, "października": 10, "listopada": 11, "grudnia": 12,
}


class BiedronkaSessionExpired(Exception):
    """The saved website session is no longer authenticated."""


def receipt_date_hint(text: str) -> str | None:
    match = re.search(
        r"\b(\d{1,2})\s+(stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|"
        r"października|listopada|grudnia)\s+(\d{4})\b",
        text.lower(),
    )
    if match:
        day, month, year = match.groups()
        return f"{year}-{POLISH_MONTHS[month]:02d}-{int(day):02d}"
    match = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", text)
    if match:
        day, month, year = map(int, match.groups())
        if 1 <= day <= 31 and 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _save_session(session: requests.Session) -> None:
    ACCOUNT.mkdir(parents=True, exist_ok=True)
    data = {
        "user_agent": session.headers.get("User-Agent", ""),
        "cookies": [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "expires": cookie.expires,
                "secure": cookie.secure,
                "rest": cookie._rest,
            }
            for cookie in session.cookies
        ],
    }
    temporary = SESSION.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(SESSION)


def _load_session() -> requests.Session | None:
    if not SESSION.exists():
        return None
    try:
        data = json.loads(SESSION.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Не удалось прочитать сохранённую сессию Biedronka: {SESSION}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
        raise SystemExit(f"Некорректный формат сохранённой сессии Biedronka: {SESSION}")

    session = requests.Session()
    session.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": PANEL_URL,
        "User-Agent": data.get("user_agent", ""),
    })
    for cookie in data["cookies"]:
        if not isinstance(cookie, dict) or not all(
            isinstance(cookie.get(key), str) for key in ("name", "value", "domain", "path")
        ):
            raise SystemExit(f"Некорректные cookies в сохранённой сессии Biedronka: {SESSION}")
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie["domain"],
            path=cookie["path"],
            expires=cookie.get("expires"),
            secure=bool(cookie.get("secure", False)),
            rest=cookie.get("rest", {}),
        )
    if not session.cookies:
        raise SystemExit(f"В сохранённой сессии Biedronka нет cookies: {SESSION}")
    return session


def session_from_extension(user_agent: str, cookies: list[dict]) -> requests.Session:
    if not isinstance(user_agent, str) or not user_agent or len(user_agent) > 1024:
        raise ValueError("Некорректный User-Agent Chrome.")
    if not isinstance(cookies, list) or not 1 <= len(cookies) <= 100:
        raise ValueError("В расширении нет cookies Biedronka.")

    session = requests.Session()
    session.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": PANEL_URL,
        "User-Agent": user_agent,
    })
    for cookie in cookies:
        if not isinstance(cookie, dict):
            raise ValueError("Некорректный cookie из расширения Chrome.")
        name, value, domain, path = (cookie.get(key) for key in ("name", "value", "domain", "path"))
        if not all(isinstance(item, str) and item for item in (name, value, domain, path)):
            raise ValueError("Cookie Biedronka содержит пустые обязательные поля.")
        normalized_domain = domain.lstrip(".").lower()
        if normalized_domain != "biedronka.pl" and not normalized_domain.endswith(".biedronka.pl"):
            raise ValueError("Разрешены только cookies домена Biedronka.")
        if not path.startswith("/") or len(name) > 256 or len(value) > 8192:
            raise ValueError("Некорректный путь или размер cookie Biedronka.")
        expires = cookie.get("expires")
        if expires is not None and (not isinstance(expires, int) or expires <= 0):
            raise ValueError("Некорректный срок действия cookie Biedronka.")
        secure = cookie.get("secure", False)
        if not isinstance(secure, bool):
            raise ValueError("Некорректный атрибут secure cookie Biedronka.")
        session.cookies.set(
            name, value, domain=domain, path=path, expires=expires, secure=secure,
        )

    return session


def verify_session(session: requests.Session, timeout: int = REQUEST_TIMEOUT) -> None:
    today = dt.date.today().isoformat()
    response = session.post(
        AJAX_URL,
        data={"page": 0, "begin_data": today, "end_data": today, "order": "desc", "short": "0"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )
    _check_session(response)
    response.raise_for_status()
    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise BiedronkaSessionExpired("Сайт не принял cookies из браузера.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("Receipts"), list):
        raise BiedronkaSessionExpired("Сайт не подтвердил сессию по полученным cookies.")


def _check_session(response: requests.Response) -> None:
    if response.status_code in (401, 403) or "/realms/" in response.url:
        raise BiedronkaSessionExpired(
            "Сессия Moja Biedronka истекла или отклонена. Нужно войти в аккаунт повторно."
        )


def month_ranges(start: dt.date, end: dt.date):
    """Yield inclusive month-sized ranges covering the requested inclusive period."""
    month = start.replace(day=1)
    while month <= end:
        next_month = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield max(start, month), min(end, next_month - dt.timedelta(days=1))
        month = next_month


def _list_receipt_month(session: requests.Session, start: dt.date, end: dt.date) -> list[dict]:
    receipts = []
    page = 0
    pages = 1
    while page < pages:
        response = session.post(
            AJAX_URL,
            data={
                "page": page,
                "begin_data": start.isoformat(),
                "end_data": end.isoformat(),
                "order": "desc",
                "short": "0",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=REQUEST_TIMEOUT,
        )
        _check_session(response)
        response.raise_for_status()
        try:
            payload = response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise BiedronkaSessionExpired(
                "Сайт вернул страницу входа вместо списка чеков. Переподключи cookies через расширение."
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("Receipts"), list):
            raise SystemExit("Неожиданный ответ списка чеков Biedronka: отсутствует список Receipts.")
        try:
            pages = int(payload["NumberOfPages"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit("В ответе Biedronka отсутствует число страниц чеков.") from exc
        if pages < 0:
            raise SystemExit("Biedronka вернула некорректное число страниц.")
        receipts.extend(payload["Receipts"])
        page += 1
        if page < pages:
            time.sleep(0.2)
    return receipts


def list_receipts(session: requests.Session, start: dt.date, end: dt.date) -> list[dict]:
    """Fetch every receipt in serial monthly requests and return oldest first."""
    receipts = []
    for month_start, month_end in month_ranges(start, end):
        month_receipts = _list_receipt_month(session, month_start, month_end)
        receipts.extend(month_receipts)
        print(f"  список за {month_start:%Y-%m}: {len(month_receipts)} чеков")
    return sorted(
        receipts,
        key=lambda receipt: dt.datetime.fromisoformat(
            str(receipt["DateTime"]).replace("Z", "+00:00")
        ),
    )


def receipt_pdf(session: requests.Session, receipt: dict) -> bytes:
    receipt_id = str(receipt.get("Id") or "")
    if not RECEIPT_ID.fullmatch(receipt_id):
        raise ValueError("Biedronka вернула чек с некорректным ID.")
    paths = [f"/panel/download/pdf/{receipt_id}", f"/panel/download/{receipt_id}"]
    for path in paths:
        response = session.get(
            f"https://moja.biedronka.pl{path}",
            headers={"Accept": "application/pdf"},
            timeout=REQUEST_TIMEOUT,
        )
        _check_session(response)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        if response.content.startswith(b"%PDF-"):
            return response.content
        if path.endswith("/pdf/" + receipt_id):
            continue
        raise SystemExit(f"Ответ Biedronka для чека {receipt_id} не является PDF.")
    raise SystemExit(f"Для чека {receipt_id} сайт не предоставил PDF.")


def _load_manifest() -> dict[str, dict[str, str]]:
    if not COMPLETED.exists():
        return {}
    try:
        value = json.loads(COMPLETED.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Повреждён журнал загрузки чеков: {COMPLETED}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Некорректный формат журнала загрузки чеков: {COMPLETED}")
    return value


def receipt_json(session: requests.Session, receipt: dict) -> dict | None:
    """Электронный чек в JSON (кнопка «JSON» на сайте). None — бумажный чек или сайт не отдал («Błąd pobrania pliku»)."""
    receipt_id = str(receipt.get("Id") or "")
    if not receipt.get("IsElectronicPrintoutRequested") or not RECEIPT_ID.fullmatch(receipt_id):
        return None
    response = session.get(f"https://moja.biedronka.pl/panel/download/json/{receipt_id}",
                           headers={"Accept": "application/json"}, timeout=REQUEST_TIMEOUT)
    _check_session(response)
    if response.status_code != 200 or "json" not in (response.headers.get("Content-Type") or ""):
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("body"), list) else None


def download_receipts(session: requests.Session, receipts: list[dict]) -> list[tuple[dict, Path]]:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    completed = _load_manifest()
    files = []
    for receipt in receipts:
        receipt_id = str(receipt.get("Id") or "")
        if not RECEIPT_ID.fullmatch(receipt_id):
            raise SystemExit("Biedronka вернула чек с некорректным ID.")
        try:
            date = dt.datetime.fromisoformat(str(receipt.get("DateTime", "")).replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise SystemExit(f"У чека {receipt_id} некорректная дата.") from exc
        name = f"biedronka_{date.isoformat()}_{receipt_id}.pdf"
        path = DOWNLOADS / name
        if not path.exists() or not path.read_bytes().startswith(b"%PDF-"):
            partial = path.with_suffix(".pdf.part")
            partial.write_bytes(receipt_pdf(session, receipt))
            partial.replace(path)
            print(f"  скачан PDF: {path.name}")
        entry = completed.get(receipt_id, {}) | {"filename": name, "date": date.isoformat(),
                                                  "electronic": bool(receipt.get("IsElectronicPrintoutRequested"))}
        if entry["electronic"] and not entry.get("json_tried"):  # JSON пробуем один раз на чек
            if data := receipt_json(session, receipt):
                JSON_DIR.mkdir(parents=True, exist_ok=True)
                keep = {k: receipt.get(k) for k in ("Id", "DateTime", "TotalValue", "Discount", "Store", "Address")}
                (JSON_DIR / f"{receipt_id}.json").write_text(json.dumps({"receipt": keep, "eparagon": data}, ensure_ascii=False),
                                                             encoding="utf-8")
                entry["json"] = f"json/{receipt_id}.json"
                print(f"  скачан JSON: {receipt_id}")
            entry["json_tried"] = True
        completed[receipt_id] = entry
        files.append((receipt, path))
        COMPLETED.write_text(json.dumps(completed, ensure_ascii=False, indent=1), encoding="utf-8")
    return files


def downloaded() -> dict:
    """Скачанные чеки: {"n": сколько, "last": дата последнего} — для «Настроек» и шага обновления."""
    m = _load_manifest() if COMPLETED.exists() else {}
    return {"n": len(m), "last": max((v.get("date") or "" for v in m.values()), default=None) or None}


PAYMENT = [(r"got[oó]wk", "cash"), (r"blik", "blik"), (r"kart|card|visa|master|maestro", "card")]


def _jwt_discounts(doc: dict) -> list[float] | None:
    """Скидки по позициям из подписанной копии чека (JPK_KASA_PARAGON в поле data): «rabat.wart» в грошах."""
    import base64
    try:
        part = doc["data"].split(".")[1]
        pay = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        poz = pay["dokument"]["paragon"]["pozycja"]
        return [abs((x.get("towar") or {}).get("rabat", {}).get("wart") or 0) / 100 for x in poz]
    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
        return None


def parse_json(doc: dict, receipt_id: str, rel: str) -> tuple[dict, list[dict], list[dict], bool]:
    """-> (покупка, позиции, оплаты, сумма сходится). Цены в JSON — в грошах; скидка — строка сразу под позицией."""
    meta, j = doc.get("receipt") or {}, doc["eparagon"]
    items, payments, total = [], [], None
    for line in j.get("body", []):
        for kind, v in line.items():
            if kind == "sellLine":
                sign = -1 if v.get("isStorno") else 1
                items.append({"name": re.sub(r"\s+[A-G]$", "", (v.get("name") or "").strip()).strip(),
                              "qty": num(str(v.get("quantity") or "1")) or 1, "unit_price": (v.get("price") or 0) / 100,
                              "amount": round(sign * (v.get("total") or 0) / 100, 2), "discount": None, "product_code": None})
            elif kind == "discountLine" and items:
                value = (v.get("value") or 0) / 100
                if v.get("isPercent"):  # процент от base: в каких единицах value — неизвестно, проверим итогом
                    value = (v.get("base") or 0) / 100 * (v.get("value") or 0) / 100 / 100
                sign = (1 if v.get("isDiscount", True) else -1) * (-1 if v.get("isStorno") else 1)
                items[-1]["discount"] = round((items[-1]["discount"] or 0) + sign * value, 2)
            elif kind == "sumInCurrency":
                total = (v.get("fiscalTotal") or 0) / 100
            elif kind == "payment":
                name = (v.get("name") or "").lower()
                method = next((m for rx, m in PAYMENT if re.search(rx, name)), "card")
                payments.append({"method": method, "amount": (v.get("amount") or 0) / 100, "card_last4": None})
    net = lambda: round(sum(i["amount"] - (i["discount"] or 0) for i in items), 2)  # noqa: E731
    ok = total is not None and abs(net() - total) < 0.01
    if not ok and (rab := _jwt_discounts(j)) and len(rab) == len(items):  # скидки из подписанной копии чека
        for it, d in zip(items, rab):
            it["discount"] = d or None
        ok = total is not None and abs(net() - total) < 0.01
    when = str(meta.get("DateTime") or "")[:19]
    if not when:  # дата из подвала чека — в UTC
        from zoneinfo import ZoneInfo
        foot = next((x["fiscalFooter"]["date"] for x in j.get("body", []) if "fiscalFooter" in x), None)
        when = dt.datetime.fromisoformat(foot.replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Warsaw")).isoformat()[:19] if foot else ""
    store = " ".join(filter(None, [f"Sklep {meta['Store']}" if meta.get("Store") else None, meta.get("Address")]))
    purchase = {"id": f"biedronka:{receipt_id}", "source": "biedronka", "date": when, "merchant": "Biedronka",
                "store": store or None, "total": total if total is not None else net(), "raw_path": rel}
    return purchase, items, payments, ok


def import_json(con, receipt_id: str, info: dict) -> bool:
    """Записать точный чек из JSON. Распознанная из PDF копия того же чека удаляется, PDF прикрепляется к точному.
    -> True, если чек новый (раньше его не было ни точным, ни распознанным)."""
    import hashlib
    path = ACCOUNT / info["json"]
    pid = f"biedronka:{receipt_id}"
    existed = bool(con.execute("SELECT 1 FROM purchases WHERE id = ?", (pid,)).fetchone())
    purchase, items, payments, ok = parse_json(json.loads(path.read_text(encoding="utf-8")), receipt_id,
                                               str(path.relative_to(DATA)).replace("\\", "/"))
    save_purchase(con, purchase, items, payments)
    con.execute("UPDATE purchases SET note = ? WHERE id = ?", (None if ok else "⚠ проверить: позиции не сходятся с итогом", pid))
    replaced = False
    pdf = DOWNLOADS / info["filename"] if info.get("filename") else None
    if pdf and pdf.exists():
        digest = hashlib.sha1(pdf.read_bytes()).hexdigest()[:16]
        old = f"photo:{digest}"
        if con.execute("SELECT 1 FROM purchases WHERE id = ?", (old,)).fetchone():
            for table, col in (("items", "purchase_id"), ("payments", "purchase_id"), ("attachments", "purchase_id"),
                               ("purchases", "id")):
                con.execute(f"DELETE FROM {table} WHERE {col} = ?", (old,))
            replaced = True
        con.execute("INSERT OR REPLACE INTO attachments VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pid, str(pdf.relative_to(DATA)).replace("\\", "/"), f"biedronka:{receipt_id}", purchase["date"],
                     purchase["total"], "Biedronka", dt.datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return not existed and not replaced


def reparse():
    """Пересобрать точные чеки из сохранённых JSON (после правки разбора) — в «Сверке», как у Lidl и Kaufland."""
    if not COMPLETED.exists():
        return
    con = connect()
    for receipt_id, info in _load_manifest().items():
        if info.get("json") and (ACCOUNT / info["json"]).exists():
            import_json(con, receipt_id, info)


def _imported(con, path: Path) -> bool:
    """PDF уже в бюджете — своей покупкой или вложением к известной (те же ключи, что у photos.import_file)."""
    import hashlib
    digest = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    return bool(con.execute("SELECT 1 FROM purchases WHERE id = ?", (f"photo:{digest}",)).fetchone()
                or con.execute("SELECT 1 FROM attachments WHERE path LIKE ?", (f"photos/{digest}.%",)).fetchone())


def _date_arg(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ожидается дата в формате YYYY-MM-DD") from exc


def sync(start: dt.date | None = None, end: dt.date | None = None) -> dict:
    """List receipts through the website's AJAX endpoint, download their PDFs and import them.
    -> {"listed", "imported", "attached"}."""
    from core.logins import alive, record
    from receipts import photos

    # первый раз — всё, что есть на сайте (с HISTORY_START); потом — хвост с последнего скачанного чека, как у Lidl
    manifest = _load_manifest() if COMPLETED.exists() else {}
    # чеки, скачанные до поддержки JSON, — один раз ещё весь список: у электронных берём JSON
    backfill = any(v.get("electronic") is None for v in manifest.values())
    if start is None and not backfill and (last := downloaded()["last"]):
        start = dt.date.fromisoformat(last) - dt.timedelta(days=OVERLAP_DAYS)
    start = max(start or HISTORY_START, HISTORY_START)
    end = end or dt.date.today()
    if start > end:
        raise SystemExit("Начальная дата не может быть позже конечной.")

    session = _load_session()
    if session is None:
        raise SystemExit(
            "Нет сохранённой сессии Biedronka. Подключи её через расширение в разделе «Настройки» "
            "и запусти синхронизацию повторно."
        )
    try:
        receipts = list_receipts(session, start, end)
        print(f"Чеков Biedronka с {start.isoformat()} по {end.isoformat()}: {len(receipts)}")
        download_receipts(session, receipts)
    except BiedronkaSessionExpired as exc:
        record("biedronka", "expired")
        raise SystemExit(
            "Сессия Biedronka истекла. Переподключи её через расширение в разделе «Настройки»."
        ) from exc
    _save_session(session)
    alive("biedronka")

    con = connect()
    imported = attached = 0
    # разбираем все скачанные PDF, которых ещё нет в бюджете, а не только сегодняшние: прошлый прогон мог
    # оборваться между скачиванием и разбором — с инкрементальной загрузкой такие PDF иначе не подобрались бы
    for receipt_id, info in sorted(_load_manifest().items(), key=lambda kv: kv[1].get("date") or ""):
        if info.get("json") and (ACCOUNT / info["json"]).exists():  # точный чек — без распознавания
            if import_json(con, receipt_id, info):
                imported += 1
            continue
        path = DOWNLOADS / info["filename"]
        if not path.exists() or _imported(con, path):
            continue
        result = photos.import_file(con, path, hint_date=info.get("date"), source_ref=f"biedronka:{receipt_id}")
        if result:
            if result.get("attached"):
                attached += 1
            else:
                imported += 1
    print(f"Biedronka: новых чеков: {imported}, прикреплено к известным чекам: {attached}. "
          f"PDF сохранены в {DOWNLOADS}")
    return {"listed": len(receipts), "imported": imported, "attached": attached}


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="python budget.py biedronka sync")
    parser.add_argument("command", choices=("sync",))
    parser.add_argument("--from", dest="start", type=_date_arg, default=None,
                        help="по умолчанию — с последнего скачанного чека (первый раз — с 2023-01-01)")
    parser.add_argument("--to", dest="end", type=_date_arg, default=dt.date.today())
    args = parser.parse_args(argv)
    sync(args.start, args.end)
