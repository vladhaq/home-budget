"""Обновить всё: чеки Lidl и Kaufland, почта, фото чеков (Telegram и папка), выписка PKO, сверка, резервная копия.

  python budget.py update                      все шаги по очереди; ошибка одного шага не останавливает остальные
  python budget.py update lidl bank            только выбранные шаги (сверка добавляется сама)
  python budget.py update status               последние запуски и что в них сломалось
  python budget.py schedule on [ЧЧ:ММ]         автозапуск каждый день (Планировщик Windows), по умолчанию 07:30
  python budget.py schedule off | status

Журнал запусков хранится в базе (update_runs, update_steps) и виден в интерфейсе на странице «Настройки».
Входы (lidl/kaufland/bank login, первый вход в Telegram, пароль почты) здесь не запрашиваются: если вход
устарел, шаг завершается ошибкой с подсказкой, какую команду запустить.

config.ini (необязательно):
  [update]
  backup_every_days = 1      как часто делать резервную копию при обновлении (0 — не делать)
"""
import base64
import configparser
import contextlib
import datetime as dt
import gc
import io
import json
import msvcrt
import re
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

from core.common import BUDGET, DATA
from core.db import connect, set_meta

CONFIG = BUDGET / "config.ini"
LOCK = DATA / "update.lock"
TASK = "BudgetUpdate"
KEEP_RUNS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS update_runs (
  id INTEGER PRIMARY KEY, started TEXT, finished TEXT, trigger TEXT, steps TEXT, status TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS update_steps (
  run_id INTEGER, step TEXT, started TEXT, finished TEXT, status TEXT, summary TEXT, hint TEXT, output TEXT,
  PRIMARY KEY (run_id, step));
"""
TRIGGERS = {"manual": "вручную", "schedule": "автозапуск", "ui": "из интерфейса"}
STATUS_MARK = {"ok": "✓", "warn": "!", "off": "!", "skip": "–", "error": "✗", "running": "…"}  # off — не настроено, а надо


class Skip(Exception):
    """Шаг не выполнялся (источник не подключён и т. п.). warn=True — это стоит исправить."""

    def __init__(self, msg, hint=None, warn=False):
        super().__init__(msg)
        self.hint, self.warn = hint, warn


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def db():
    con = connect()
    con.executescript(SCHEMA)
    return con


def settings() -> dict:
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG, encoding="utf-8")
    u = cfg["update"] if cfg.has_section("update") else {}
    return {"backup_every_days": float(u.get("backup_every_days") or 1)}


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = abs(n) % 10, abs(n) % 100
    word = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {word}"


def count(con, sql: str, *args) -> int:
    return con.execute(sql, args).fetchone()[0] or 0


def n_purchases(con, source: str) -> int:
    return count(con, "SELECT count(*) FROM purchases WHERE source = ?", source)


def hint_from(msg: str) -> str | None:
    m = re.search(r"python budget\.py [\w -]+?(?=$|[.,;:)\n—]| —| -)", msg or "")
    return m.group(0).strip() if m else None


# ---------------------------------------------------------------- шаги

def step_lidl(con):
    from receipts import lidl
    if not lidl.TOKEN.exists():
        raise Skip("не подключено", "python budget.py lidl login")
    before = n_purchases(con, "lidl")
    lidl.sync()
    new = n_purchases(con, "lidl") - before
    return "ok", f"новых чеков: {new}" if new else "новых чеков нет"


def step_kaufland(con):
    from receipts import kaufland
    if not kaufland.TOKEN.exists():
        raise Skip("не подключено", "python budget.py kaufland login")
    before = n_purchases(con, "kaufland")
    kaufland.sync()
    new = n_purchases(con, "kaufland") - before
    return "ok", f"новых чеков: {new}" if new else "новых чеков нет"


def mail_ready() -> bool:
    import keyring
    from receipts import mail
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG, encoding="utf-8")
    user = cfg.get("mail", "user", fallback="").strip()
    return bool(user and keyring.get_password(mail.KEYRING_SERVICE, user))


def step_mail(con):
    from receipts import mail
    if not mail_ready():
        raise Skip("не подключено", "python budget.py mail login")
    store = mail.db()
    domains = {r[0] for r in store.execute("SELECT DISTINCT domain FROM emails")}
    emails = count(store, "SELECT count(*) FROM emails")
    shop = count(store, "SELECT count(*) FROM emails WHERE path IS NOT NULL")
    mail.headers()
    mail.bodies()  # разбор писем в покупки — в шаге «Сверка» (она всё равно пересобирает их из архива)
    new_mail = count(store, "SELECT count(*) FROM emails") - emails
    new_shop = count(store, "SELECT count(*) FROM emails WHERE path IS NOT NULL") - shop
    # новые отправители с письмами о покупках: их надо отметить, иначе их заказы не попадут в бюджет
    marked = {r[0] for r in store.execute("SELECT domain FROM mail_senders")}
    fresh = sorted({r["domain"] for r in store.execute("SELECT domain, subject FROM emails")
                    if r["domain"] not in domains and r["domain"] not in marked and mail.SHOP_WORDS.search(r["subject"] or "")})
    store.close()
    summary = f"новых писем: {new_mail}, из них от магазинов и сервисов: {new_shop}"
    if fresh:
        return "warn", summary + f". Новые отправители с покупками: {', '.join(fresh[:8])} — отметь их " \
                                 f"(python budget.py mail senders, затем mail mark shop <домен>)"
    return "ok", summary


def step_telegram(con):
    from receipts import telegram_inbox
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG, encoding="utf-8")
    if not cfg.get("telegram", "api_id", fallback="").strip() or not cfg.get("telegram", "receipts_chat", fallback=""):
        raise Skip("не настроено", "config.ini [telegram]")
    if not (DATA / "telegram.session").exists():
        raise Skip("нужен первый вход в Telegram", "python budget.py telegram", warn=True)
    before, att = n_purchases(con, "photo"), count(con, "SELECT count(*) FROM attachments")
    telegram_inbox.fetch(interactive=False)
    return photo_result(con, before, att)


def photo_result(con, before: int, att: int = 0):
    attached = count(con, "SELECT count(*) FROM attachments") - att
    status, summary = photo_new(con, before)
    if attached:
        summary += f"; фото совпало с уже известным чеком и прикреплено к нему: {attached}"
    return status, summary


def photo_new(con, before: int):
    new = n_purchases(con, "photo") - before
    check = count(con, """SELECT count(*) FROM (SELECT note FROM purchases WHERE source = 'photo'
                          ORDER BY rowid DESC LIMIT ?) WHERE note LIKE '%проверить%'""", new) if new else 0
    if check:
        return "warn", f"новых чеков: {new}, из них проверить вручную: {check} (Обзор → источник «фото чека»)"
    return "ok", f"новых чеков: {new}" if new else "новых чеков нет"


def step_photos(con):
    from receipts import photos
    files = sorted(p for p in photos.INBOX.iterdir() if p.suffix.lower() in photos.IMAGE_EXT | {".pdf"}) \
        if photos.INBOX.exists() else []
    if not files:
        return "ok", "папка пуста"
    before, att, failed = n_purchases(con, "photo"), count(con, "SELECT count(*) FROM attachments"), []
    done = photos.INBOX / "обработано"
    done.mkdir(exist_ok=True)
    for p in files:
        try:
            photos.import_file(con, p)
        except Exception as e:  # noqa: BLE001 — один плохой файл не должен останавливать остальные
            print(f"  ! {p.name}: {e}")
            failed.append(p.name)
            continue
        p.replace(done / p.name)  # копия уже лежит в data/photos; из папки убираем, чтобы не распознавать снова
    status, summary = photo_result(con, before, att)
    if failed:
        return "warn", summary + f"; не распознаны: {', '.join(failed)}"
    return status, summary


def step_bank(con):
    from bank import enablebanking
    if not enablebanking.SESSION.exists():
        raise Skip("не подключено", "python budget.py bank login")
    before = count(con, "SELECT count(*) FROM bank_tx")
    enablebanking.sync()
    new = count(con, "SELECT count(*) FROM bank_tx") - before
    last = con.execute("SELECT max(date) FROM bank_tx").fetchone()[0]
    summary = f"новых операций: {new}, выписка по {last}"
    days = consent_days_left()
    if days is not None and days <= 21:
        return "warn", summary + f". Доступ к банку закончится через {plural(days, 'день', 'дня', 'дней')} — " \
                                 f"продли: python budget.py bank login"
    return "ok", summary


def consent_days_left() -> int | None:
    from bank import enablebanking
    try:
        until = json.loads(enablebanking.SESSION.read_text(encoding="utf-8"))["valid_until"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return None
    return (dt.date.fromisoformat(until[:10]) - dt.date.today()).days


def step_reconcile(con):
    from core import categories, reconcile
    if count(con, "SELECT count(*) FROM bank_tx"):
        reconcile.reconcile(verbose=False)
    else:
        categories.categorize(con)
        con.commit()
    unknown = count(con, "SELECT count(DISTINCT lower(name)) FROM items WHERE category_id IS NULL")
    check = count(con, "SELECT count(*) FROM purchases WHERE note LIKE '%проверить: в банке%'")
    parts = [f"неопознанных позиций: {unknown}" if unknown else "все позиции с категориями"]
    if check:
        parts.append(f"чеков без пары в банке: {check}")
    return "ok", ", ".join(parts)


def step_deals(con):
    from core import deals
    r = deals.auto()
    summary = f"газеток: {r['flyers']}, акций на постоянные позиции: {r['deals']}"
    if r["reminded"]:
        summary += f", напоминаний поставлено: {r['reminded']}"
    if r["upcoming"] and not r["telegram"]["ready"]:
        raise Skip(summary + f" — напомнить можно о {r['upcoming']}, но нужен вход в Telegram",
                   "python budget.py telegram", warn=True)
    return "ok", summary


def last_backup():
    from core import backup
    s = backup.settings()
    files = sorted(s["dir"].glob("budget_*.zip")) if s["dir"].exists() else []
    return files[-1] if files else None


def step_backup(con):
    import keyring
    from core import backup
    every = settings()["backup_every_days"]
    if every <= 0:
        raise Skip("выключено в config.ini [update] backup_every_days")
    if not keyring.get_password(backup.KEYRING_SERVICE, backup.KEYRING_USER):
        raise Skip("пароль копий не задан — копии не делаются", "python budget.py backup password", warn=True)
    last = last_backup()
    if last and time.time() - last.stat().st_mtime < every * 86400 - 3600:
        return "ok", f"свежая копия уже есть: {last.name}"
    path = backup.create(verbose=False)
    return "ok", f"{path.name}, {path.stat().st_size / 1e6:.0f} МБ"


STEPS = [
    ("lidl", "Чеки Lidl Plus", step_lidl),
    ("kaufland", "Чеки Kaufland", step_kaufland),
    ("mail", "Почта Gmail", step_mail),
    ("telegram", "Фото чеков из Telegram", step_telegram),
    ("photos", "Папка receipts/inbox", step_photos),
    ("bank", "Выписка PKO", step_bank),
    ("reconcile", "Сверка и категории", step_reconcile),
    ("deals", "Газетки Lidl и напоминания", step_deals),
    ("backup", "Резервная копия", step_backup),
]
TITLES = {k: t for k, t, _ in STEPS}
NETWORK = {"lidl", "kaufland", "mail", "telegram", "bank", "deals"}


# ---------------------------------------------------------------- запуск

class Tee(io.TextIOBase):
    """Вывод шага: в журнал и (если есть консоль) на экран."""

    def __init__(self, echo):
        self.buf, self.echo = io.StringIO(), echo

    def write(self, s):
        self.buf.write(s)
        if self.echo:
            try:
                self.echo.write(s)
            except (OSError, ValueError, UnicodeError):
                pass
        return len(s)

    def flush(self):
        if self.echo:
            with contextlib.suppress(OSError, ValueError):
                self.echo.flush()


def acquire_lock(wait: float = 0):
    """Не даёт запустить два обновления сразу. Блокировка снимается сама, если процесс упал.
    wait — сколько секунд подождать: интерфейс на миг берёт блокировку, когда проверяет, идёт ли обновление."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + wait
    f = open(LOCK, "a+")
    while True:
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return f
        except OSError:
            if time.time() >= deadline:
                f.close()
                return None
            time.sleep(0.2)


def is_running() -> bool:
    f = acquire_lock()
    if f is None:
        return True
    f.seek(0)
    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    f.close()
    return False


def online(wait: int = 0) -> bool:
    deadline = time.time() + wait
    while True:
        try:
            socket.create_connection(("api.enablebanking.com", 443), timeout=5).close()
            return True
        except OSError:
            if time.time() >= deadline:
                return False
            time.sleep(15)


def describe_error(e: BaseException) -> tuple[str, str | None]:
    import requests
    if isinstance(e, SystemExit):
        msg = str(e.code)
        return msg, hint_from(msg)
    if isinstance(e, (requests.ConnectionError, requests.Timeout, socket.gaierror, TimeoutError, ConnectionError)):
        return f"нет связи с сервисом ({e.__class__.__name__})", "повторится при следующем обновлении"
    if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code in (401, 403):
        return f"сервис отклонил вход ({e.response.status_code})", None
    return f"{e.__class__.__name__}: {e}", "ошибка в программе — подробности в журнале ниже"


def run(trigger: str = "manual", only: list[str] | None = None) -> int | None:
    lock = acquire_lock(wait=3)
    if lock is None:
        print("Обновление уже идёт (другой запуск). Подожди, пока закончится.")
        return None
    con = db()
    mark_dead(con)
    steps = [s for s in STEPS if not only or s[0] in only]
    if only and set(only) & {"lidl", "kaufland", "mail", "telegram", "photos", "bank"} and "reconcile" not in only:
        steps.append(next(s for s in STEPS if s[0] == "reconcile"))  # новые покупки без сверки и категорий не видны
    cur = con.execute("INSERT INTO update_runs (started, trigger, steps) VALUES (?, ?, ?)",
                      (now(), trigger, ",".join(k for k, _, _ in steps)))
    run_id = cur.lastrowid
    con.commit()
    echo = sys.__stdout__ if trigger == "manual" else None
    say = (lambda s: print(s, flush=True)) if echo else (lambda s: None)
    say(f"Обновление №{run_id}: {', '.join(TITLES[k] for k, _, _ in steps)}")
    net_ok = online(wait=180 if trigger == "schedule" else 10)  # после выхода из сна сеть появляется не сразу
    results = []
    try:
        for key, title, fn in steps:
            con.execute("INSERT INTO update_steps (run_id, step, started, status) VALUES (?, ?, ?, 'running')",
                        (run_id, key, now()))
            con.commit()
            say(f"\n— {title}")
            out, hint = Tee(echo), None
            if key in NETWORK and not net_ok:
                status, summary, hint = "error", "нет интернета", "повторится при следующем обновлении"
            else:
                try:
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                        status, summary = fn(con)
                except Skip as s:
                    status, summary, hint = ("off" if s.warn else "skip"), str(s), s.hint
                except (Exception, SystemExit) as e:  # noqa: BLE001 — сбой шага записываем и идём дальше
                    status = "error"
                    summary, hint = describe_error(e)
                    if not isinstance(e, SystemExit):
                        out.write("\n" + traceback.format_exc())
                hint = hint or (hint_from(summary) if status in ("warn", "off", "error") else None)
            gc.collect()  # соединения с базой, забытые внутри шага, закрываются сразу и не держат блокировку
            text = out.buf.getvalue()
            con.execute("UPDATE update_steps SET finished = ?, status = ?, summary = ?, hint = ?, output = ? "
                        "WHERE run_id = ? AND step = ?",
                        (now(), status, summary, hint, text[-20000:], run_id, key))
            con.commit()
            results.append((key, status, summary))
            say(f"{STATUS_MARK[status]} {title}: {summary}" + (f"  → {hint}" if hint and hint not in summary else ""))
    finally:
        errors = [TITLES[k] for k, st, _ in results if st == "error"]
        warns = [TITLES[k] for k, st, _ in results if st in ("warn", "off")]
        status = "error" if errors or len(results) < len(steps) else "warn" if warns else "ok"
        summary = ("ошибки: " + ", ".join(errors) if errors else "прервано" if len(results) < len(steps)
                   else "обратить внимание: " + ", ".join(warns) if warns else "без ошибок")
        con.execute("UPDATE update_runs SET finished = ?, status = ?, summary = ? WHERE id = ?",
                    (now(), status, summary, run_id))
        set_meta(con, "update_last", json.dumps({"id": run_id, "finished": now(), "status": status, "summary": summary},
                                                ensure_ascii=False))
        old = [r[0] for r in con.execute("SELECT id FROM update_runs ORDER BY id DESC LIMIT -1 OFFSET ?", (KEEP_RUNS,))]
        for rid in old:
            con.execute("DELETE FROM update_steps WHERE run_id = ?", (rid,))
            con.execute("DELETE FROM update_runs WHERE id = ?", (rid,))
        con.commit()
        lock.close()
    say(f"\nГотово: {summary}. Подробности — в интерфейсе, страница «Настройки».")
    return run_id


# ---------------------------------------------------------------- состояние (для интерфейса и `update status`)

def mark_dead(con):
    """Запуск без отметки об окончании, когда обновление не идёт, — процесс завершился аварийно
    (выключили компьютер, закрыли окно). Вызывать, только убедившись, что is_running() == False."""
    if not con.execute("SELECT 1 FROM update_runs WHERE finished IS NULL").fetchone():
        return
    msg = "прервано: процесс обновления завершился, не дойдя до конца"
    con.execute("UPDATE update_runs SET finished = ?, status = 'error', summary = ? WHERE finished IS NULL", (now(), msg))
    con.execute("UPDATE update_steps SET finished = ?, status = 'error', summary = ? WHERE status = 'running'", (now(), msg))
    con.commit()


def runs(con, limit: int = 30) -> list[dict]:
    out = []
    for r in con.execute("SELECT * FROM update_runs ORDER BY id DESC LIMIT ?", (limit,)):
        steps = [dict(s) for s in con.execute("SELECT * FROM update_steps WHERE run_id = ? ORDER BY rowid", (r["id"],))]
        for s in steps:
            s["title"] = TITLES.get(s["step"], s["step"])
        out.append(dict(r) | {"steps": steps})
    return out


def sources(con) -> list[dict]:
    """Каждый источник: подключён ли, когда последний раз обновлялся успешно, по какую дату данные, что сломалось."""
    import keyring
    from bank import enablebanking
    from core import backup
    from receipts import kaufland, lidl, photos

    def last(step, ok_only=False):
        q = "SELECT * FROM update_steps WHERE step = ? AND status != 'running'" + \
            (" AND status IN ('ok', 'warn')" if ok_only else "") + " ORDER BY run_id DESC LIMIT 1"
        r = con.execute(q, (step,)).fetchone()
        return dict(r) if r else None

    def max_date(source):
        return con.execute("SELECT max(date) FROM purchases WHERE source = ?", (source,)).fetchone()[0]

    have_emails = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'emails'").fetchone()
    days = consent_days_left()
    bk = last_backup()
    inbox = [p for p in photos.INBOX.iterdir() if p.suffix.lower() in photos.IMAGE_EXT | {".pdf"}] \
        if photos.INBOX.exists() else []
    info = {
        "lidl": (lidl.TOKEN.exists(), "python budget.py lidl login", max_date("lidl"),
                 f"чеков: {n_purchases(con, 'lidl')}"),
        "kaufland": (kaufland.TOKEN.exists(), "python budget.py kaufland login", max_date("kaufland"),
                     f"чеков: {n_purchases(con, 'kaufland')}"),
        "mail": (mail_ready(), "python budget.py mail login",
                 con.execute("SELECT max(date) FROM emails").fetchone()[0] if have_emails else None,
                 f"покупок из писем: {n_purchases(con, 'email')}"),
        "telegram": ((DATA / "telegram.session").exists(), "python budget.py telegram", max_date("photo"),
                     f"чеков с фото: {n_purchases(con, 'photo')}"),
        "photos": (True, None, None, f"файлов ждут распознавания: {len(inbox)}" if inbox else "папка пуста"),
        "bank": (enablebanking.SESSION.exists() and (days is None or days >= 0), "python budget.py bank login",
                 con.execute("SELECT max(date) FROM bank_tx").fetchone()[0],
                 (f"доступ до {(dt.date.today() + dt.timedelta(days=days)).isoformat()}" if days is not None and days >= 0
                  else "доступ истёк" if days is not None else "")),
        "reconcile": (True, None, None,
                      f"неопознанных позиций: {count(con, 'SELECT count(DISTINCT lower(name)) FROM items WHERE category_id IS NULL')}"),
        "deals": deals_source(),
        "backup": (bool(keyring.get_password(backup.KEYRING_SERVICE, backup.KEYRING_USER)),
                   "python budget.py backup password",
                   dt.datetime.fromtimestamp(bk.stat().st_mtime).isoformat(timespec="minutes") if bk else None,
                   f"папка: {backup.settings()['dir']}"),
    }
    out = []
    for key, title, _ in STEPS:
        ready, setup, data_date, note = info[key]
        warn = None
        if key == "bank" and days is not None and 0 <= days <= 21:
            warn = f"доступ к банку закончится через {plural(days, 'день', 'дня', 'дней')}"
        missing = {"telegram": "нет первого входа", "backup": "пароль не задан",
                   "bank": "доступ истёк" if enablebanking.SESSION.exists() else "не подключено"}.get(key, "не подключено")
        out.append({"key": key, "title": title, "ready": ready, "setup": setup, "data": data_date, "note": note,
                    "warn": warn, "missing": missing, "always": key in ("photos", "reconcile"),
                    "last": last(key), "last_ok": last(key, ok_only=True)})
    return out


def deals_source():
    """Строка «Газетки Lidl» на странице «Настройки»: (готово, команда настройки, данные по, пояснение)."""
    try:
        from core import deals
        s, tg = deals.summary(), deals.telegram_state()
    except Exception as e:  # noqa: BLE001
        return False, None, None, f"модуль скидок не загрузился: {e}"
    note = f"газеток: {s['flyers']}" + ("" if tg["ready"] else "; напоминания — после входа в Telegram")
    return True, None if tg["ready"] else "python budget.py telegram", (s["updated"] or "")[:16] or None, note


def show_status():
    con = db()
    rs = runs(con, 5)
    if not rs:
        print("Обновлений ещё не было: python budget.py update")
    for r in rs:
        print(f"\n№{r['id']} {r['started'].replace('T', ' ')} ({TRIGGERS.get(r['trigger'], r['trigger'])}): "
              f"{STATUS_MARK.get(r['status'], '…')} {r['summary'] or 'идёт'}")
        for s in r["steps"]:
            print(f"   {STATUS_MARK.get(s['status'], '?')} {s['title']:<26} {s['summary'] or ''}"
                  + (f"  → {s['hint']}" if s["hint"] and s["hint"] not in (s["summary"] or "") else ""))
    sch = schedule_status()
    print("\nАвтозапуск: " + (f"каждый день в {sch['time']}, следующий {sch.get('next_run') or '—'}"
                              if sch.get("installed") else "выключен (python budget.py schedule on)"))


# ---------------------------------------------------------------- автозапуск (Планировщик заданий Windows)

def _ps(script: str) -> str:
    full = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n$ErrorActionPreference = 'Stop'\n"
            "$ProgressPreference = 'SilentlyContinue'\ntry {\n" + script +
            "\n} catch { Write-Output ('ERROR: ' + $_.Exception.Message); exit 1 }")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                        base64.b64encode(full.encode("utf-16-le")).decode()],
                       capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out, err = r.stdout.decode("utf-8", "replace").strip(), r.stderr.decode("utf-8", "replace").strip()
    if r.returncode != 0:
        msg = next((s[7:] for s in out.splitlines() if s.startswith("ERROR: ")), None)
        raise RuntimeError(msg or err[-500:] or out or f"PowerShell: код {r.returncode}")
    return out


def pythonw() -> str:
    """Тот же Python, что и сейчас, но без окна консоли."""
    w = Path(sys.executable).with_name("pythonw.exe")
    return str(w if w.exists() else sys.executable)


def schedule_on(at: str = "07:30"):
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", at):
        raise SystemExit("Время в формате ЧЧ:ММ, например 07:30")
    q = lambda s: str(s).replace("'", "''")  # noqa: E731
    _ps(f"""
$a = New-ScheduledTaskAction -Execute '{q(pythonw())}' -Argument '"{q(BUDGET / "budget.py")}" update --trigger schedule' -WorkingDirectory '{q(BUDGET)}'
$t = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours({int(at.split(":")[0])}).AddMinutes({int(at.split(":")[1])}))
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName '{TASK}' -Action $a -Trigger $t -Settings $s -Description 'Домашний бюджет: обновить всё (budget.py update). Если компьютер был выключен — запустится при включении.' -Force | Out-Null
""")
    return schedule_status()


def schedule_off():
    _ps(f"Unregister-ScheduledTask -TaskName '{TASK}' -Confirm:$false -ErrorAction SilentlyContinue")
    return schedule_status()


def schedule_status() -> dict:
    try:
        out = _ps(f"""
$t = Get-ScheduledTask -TaskName '{TASK}' -ErrorAction SilentlyContinue
if (-not $t) {{ '{{"installed": false}}'; exit }}
$i = $t | Get-ScheduledTaskInfo
$f = {{ param($d) if ($d -and $d.Year -gt 2000) {{ $d.ToString('s') }} else {{ $null }} }}
[pscustomobject]@{{ installed = $true; state = "$($t.State)"; start = "$($t.Triggers[0].StartBoundary)";
  last_run = (& $f $i.LastRunTime); last_result = $i.LastTaskResult; next_run = (& $f $i.NextRunTime) }} | ConvertTo-Json -Compress
""")
        s = json.loads(out.splitlines()[-1])
    except (RuntimeError, ValueError, IndexError, subprocess.TimeoutExpired) as e:
        return {"installed": False, "error": str(e)[:300]}
    if s.get("installed"):
        m = re.search(r"T(\d\d:\d\d)", s.get("start") or "")
        s["time"] = m.group(1) if m else None
        # 0 — успешно, 267011 — ещё не запускалось, 267009 — выполняется сейчас
        s["last_ok"] = s.get("last_result") in (0, 267011, 267009)
    return s


def main(argv: list[str]):
    args, trigger = list(argv), "manual"
    if "--trigger" in args:
        i = args.index("--trigger")
        trigger = args[i + 1] if i + 1 < len(args) else trigger
        del args[i:i + 2]
    if args[:1] == ["status"]:
        show_status()
        return
    unknown = [a for a in args if a not in TITLES]
    if unknown:
        raise SystemExit(f"Нет такого шага: {', '.join(unknown)}. Шаги: {', '.join(TITLES)}")
    run(trigger, args or None)


def schedule_main(argv: list[str]):
    cmd = argv[0] if argv else "status"
    if cmd == "on":
        s = schedule_on(argv[1] if len(argv) > 1 else "07:30")
        print(f"Автозапуск включён: каждый день в {s.get('time')}, следующий запуск {s.get('next_run')}. "
              "Если компьютер в это время выключен — обновление запустится при включении.")
    elif cmd == "off":
        schedule_off()
        print("Автозапуск выключен.")
    else:
        s = schedule_status()
        if s.get("installed"):
            print(f"Автозапуск: каждый день в {s['time']} ({s['state']}), последний запуск {s.get('last_run') or '—'}, "
                  f"следующий {s.get('next_run') or '—'}")
        else:
            print("Автозапуск выключен. Включить: python budget.py schedule on 07:30" +
                  (f"\n({s['error']})" if s.get("error") else ""))
