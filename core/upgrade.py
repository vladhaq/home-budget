"""Новая версия программы с GitHub: проверить и установить. Данные и настройки не трогаются.

  python budget.py upgrade            проверить и, если есть новее, установить (спросит подтверждение)
  python budget.py upgrade --check    только проверить

В интерфейсе — «Настройки» → «Проверить обновление»: установка там же, сервер перезапускается сам.
Заменяются только файлы программы; data/, backups/, config.ini и папки входящих (receipts/inbox, bank/inbox) — нет.
Перед установкой — копия базы в data/upgrade/. Установка из git-клона — git pull; копия для разработки
(git без origin vladhaq/home-budget или со своими правками) не обновляется.
"""
import io
import re
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import requests

from core.common import BUDGET, DATA

REPO = "vladhaq/home-budget"
API = f"https://api.github.com/repos/{REPO}"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "home-budget-upgrade"}
WORK = DATA / "upgrade"
KEEP_DIRS = ("data/", "backups/", "receipts/inbox/", "bank/inbox/", ".git/")  # твоё — не из архива
KEEP_FILES = {"config.ini", "lidl-deals/config.ini"}
RESTART = 3  # код выхода сервера «запусти меня заново» (app/server.py, serve)


def current() -> str:
    m = re.search(r'__version__ = "([^"]+)"', (BUDGET / "budget.py").read_text(encoding="utf-8"))
    return m.group(1) if m else "0"


def vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def latest() -> dict:
    r = requests.get(f"{API}/releases/latest", headers=HEADERS, timeout=20)
    if r.status_code == 404:
        raise RuntimeError("на GitHub ещё нет выпусков")
    r.raise_for_status()
    j = r.json()
    return {"version": j["tag_name"].lstrip("v"), "tag": j["tag_name"], "date": (j.get("published_at") or "")[:10],
            "notes": j.get("body") or "", "url": j.get("html_url")}


def git(*args) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=BUDGET, capture_output=True, text=True, encoding="utf-8", timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def blocker(root: Path = BUDGET) -> str | None:
    """Почему установить нельзя (None — можно)."""
    if not (root / ".git").exists():
        return None  # распакованный ZIP — обычная установка
    if REPO not in (git("remote", "get-url", "origin") or ""):
        return "это копия для разработки (git без origin vladhaq/home-budget) — она обновляется не отсюда"
    if git("status", "--porcelain", "--untracked-files=no"):
        return "в папке программы есть свои правки (git status) — обнови вручную: git pull"
    return None


def check() -> dict:
    info = {"current": current()}
    try:
        lat = latest()
    except (requests.RequestException, RuntimeError, KeyError, ValueError) as e:
        return info | {"error": f"не удалось проверить: {e}"}
    return info | {"latest": lat, "newer": vtuple(lat["version"]) > vtuple(info["current"]), "blocker": blocker()}


def apply_zip(z: zipfile.ZipFile, version: str, dest: Path) -> int:
    """Файлы программы из архива GitHub (папка «vladhaq-home-budget-<sha>/») — поверх dest. -> сколько файлов изменилось."""
    names = [n for n in z.namelist() if not n.endswith("/")]
    root = names[0].split("/")[0] + "/" if names else ""
    try:
        ok = f'__version__ = "{version}"' in z.read(root + "budget.py").decode("utf-8")
    except KeyError:
        ok = False
    if not ok:
        raise RuntimeError(f"в скачанном архиве нет версии {version} — установка отменена, файлы не тронуты")
    base, changed = dest.resolve(), 0
    for name in names:
        rel = name[len(root):]
        if not rel or rel in KEEP_FILES or rel.startswith(KEEP_DIRS):
            continue
        target = (dest / rel).resolve()
        if base not in target.parents:  # путь за пределы папки программы
            continue
        data = z.read(name)
        if target.exists() and target.read_bytes() == data:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        changed += 1
    return changed


def install(lat: dict, say=print) -> dict:
    """Установить выпуск lat (из latest()). -> {"version", "files", "deps"}."""
    if why := blocker():
        raise RuntimeError(why)
    WORK.mkdir(parents=True, exist_ok=True)
    db = DATA / "budget.db"
    if db.exists():
        say("Копия базы…")
        src, dst = sqlite3.connect(db), sqlite3.connect(WORK / f"budget_before_{lat['version']}.db")
        src.backup(dst)
        src.close()
        dst.close()
    req = BUDGET / "requirements.txt"
    req_before = req.read_bytes() if req.exists() else b""
    if (BUDGET / ".git").exists():
        say("git pull…")
        if git("pull", "--ff-only") is None:
            raise RuntimeError("git pull не прошёл — обнови вручную")
        files = None
    else:
        say(f"Скачиваю {lat['tag']}…")
        r = requests.get(f"{API}/zipball/{lat['tag']}", headers=HEADERS, timeout=180)
        r.raise_for_status()
        say("Заменяю файлы программы…")
        files = apply_zip(zipfile.ZipFile(io.BytesIO(r.content)), lat["version"], BUDGET)
    deps = req.exists() and req.read_bytes() != req_before
    if deps:
        say("Ставлю новые зависимости (pip)…")
        p = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)], cwd=BUDGET, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.returncode:
            raise RuntimeError("файлы обновлены, но pip install не прошёл — запусти вручную: pip install -r requirements.txt\n"
                               + (p.stderr or p.stdout)[-400:])
    return {"version": current(), "files": files, "deps": deps}


def main(argv: list[str]):
    info = check()
    print(f"Установлена версия {info['current']}.")
    if "error" in info:
        print(info["error"])
        return
    lat = info["latest"]
    if not info["newer"]:
        print(f"Это последняя версия (на GitHub — {lat['version']} от {lat['date']}).")
        return
    print(f"Доступна {lat['version']} от {lat['date']}: {lat['url']}\n\n{lat['notes'].strip()}\n")
    if "--check" in argv:
        return
    if info["blocker"]:
        print("Установить нельзя: " + info["blocker"])
        return
    if input(f"Установить {lat['version']}? [y/N] ").strip().lower() not in ("y", "yes", "д", "да"):
        return
    r = install(lat)
    print(f"Установлена {r['version']}" + (f", файлов изменено: {r['files']}" if r["files"] is not None else "")
          + (", зависимости обновлены" if r["deps"] else "") + ".\nПерезапусти сервер: python budget.py serve")
