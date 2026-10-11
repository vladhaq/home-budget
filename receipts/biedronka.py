"""Download Biedronka receipt PDFs using the Moja Biedronka web session."""
import argparse
import datetime as dt
import json
import re
import time
from pathlib import Path

import requests

from core.common import DATA
from core.db import connect

PANEL_URL = "https://moja.biedronka.pl/panel/paragons"
AJAX_URL = "https://moja.biedronka.pl/panel/ajax/paragons"
ACCOUNT = DATA / "biedronka"
DOWNLOADS = ACCOUNT / "downloads"
COMPLETED = ACCOUNT / "downloaded-actions.json"
SESSION = ACCOUNT / "session.json"
REQUEST_TIMEOUT = 60
HISTORY_START = dt.date(2023, 1, 1)
RECEIPT_ID = re.compile(r"^\d{8,32}$")
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
        completed[receipt_id] = {"filename": name, "date": date.isoformat()}
        files.append((receipt, path))
        COMPLETED.write_text(json.dumps(completed, ensure_ascii=False, indent=1), encoding="utf-8")
    return files


def _date_arg(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ожидается дата в формате YYYY-MM-DD") from exc


def sync(start: dt.date | None = None, end: dt.date | None = None) -> None:
    """List receipts through the website's AJAX endpoint, download their PDFs and import them."""
    from receipts import photos

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
        downloaded = download_receipts(session, receipts)
    except BiedronkaSessionExpired as exc:
        raise SystemExit(
            "Сессия Biedronka истекла. Переподключи её через расширение в разделе «Настройки»."
        ) from exc
    _save_session(session)

    con = connect()
    imported = attached = 0
    for receipt, path in downloaded:
        receipt_id = str(receipt["Id"])
        hint_date = dt.datetime.fromisoformat(str(receipt["DateTime"]).replace("Z", "+00:00")).date().isoformat()
        result = photos.import_file(con, path, hint_date=hint_date, source_ref=f"biedronka:{receipt_id}")
        if result:
            if result.get("attached"):
                attached += 1
            else:
                imported += 1
    print(f"Biedronka: новых чеков: {imported}, прикреплено к известным чекам: {attached}. "
          f"PDF сохранены в {DOWNLOADS}")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="python budget.py biedronka sync")
    parser.add_argument("command", choices=("sync",))
    parser.add_argument("--from", dest="start", type=_date_arg, default=HISTORY_START)
    parser.add_argument("--to", dest="end", type=_date_arg, default=dt.date.today())
    args = parser.parse_args(argv)
    sync(args.start, args.end)
