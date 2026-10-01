"""Зашифрованные резервные копии всей папки budget (данные + код + конфиги).

  python budget.py backup password          задать/сменить пароль копий (хранится в диспетчере учётных данных Windows)
  python budget.py backup                   создать копию (ZIP с AES-256; открывается 7-Zip/WinRAR с паролем)
  python budget.py backup list              список копий
  python budget.py backup restore <файл>    распаковать копию в отдельную папку (текущие данные не трогаются)

config.ini:
  [backup]
  dir = D:\\budget-backups                 основная папка (по умолчанию budget/backups)
  copy_to = D:\\Backup\\budget                вторая папка (флешка, OneDrive...) — необязательно
  keep = 10                                  сколько последних копий хранить в каждой папке
"""
import configparser
import datetime as dt
import getpass
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from core.common import BUDGET, DATA

KEYRING_SERVICE = "budget-backup"
KEYRING_USER = "backup"
CONFIG = BUDGET / "config.ini"
SKIP_DIRS = {"__pycache__", "tessdata", "backups", ".claude"}   # tessdata скачивается заново
SKIP_FILES = {"_ocr.png", "_t.png", "report.html", "update.lock"}  # update.lock занят идущим обновлением


def settings() -> dict:
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG, encoding="utf-8")
    b = cfg["backup"] if cfg.has_section("backup") else {}
    return {"dir": Path(b.get("dir") or BUDGET / "backups"), "copy_to": Path(b["copy_to"]) if b.get("copy_to") else None,
            "keep": int(b.get("keep") or 10)}


def set_password():
    import keyring

    print("Пароль резервных копий. Без него копию не открыть — запиши его в надёжное место (не в эту папку).")
    p1 = getpass.getpass("Новый пароль (не короче 10 символов): ")
    if len(p1) < 10:
        raise SystemExit("Слишком короткий пароль.")
    if getpass.getpass("Ещё раз: ") != p1:
        raise SystemExit("Пароли не совпали.")
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, p1)
    print("Пароль сохранён в диспетчере учётных данных Windows. Дальше: python budget.py backup")


def password() -> str:
    import keyring

    p = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    if not p:
        raise SystemExit("Сначала задай пароль копий: python budget.py backup password")
    return p


def files_to_backup():
    for root, dirs, files in os.walk(BUDGET):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f in SKIP_FILES or f.endswith((".pyc", "-journal")):
                continue
            path = Path(root) / f
            if path.resolve() == (DATA / "budget.db").resolve():
                continue  # база кладётся отдельно — согласованным снимком
            yield path


def create(verbose=True) -> Path:
    import pyzipper

    s = settings()
    pw = password().encode()
    s["dir"].mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    target = s["dir"] / f"budget_{stamp}.zip"
    part = target.with_suffix(".zip.part")  # своё имя архив получает только после проверки
    try:
        write_archive(part, pw)
        with pyzipper.AESZipFile(part) as z:  # проверка: открывается паролем, контрольные суммы сходятся
            z.setpassword(pw)
            bad = z.testzip()
            if bad:
                raise SystemExit(f"Копия повреждена ({bad}) — запусти backup ещё раз.")
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)
    return finish(target, s, verbose)


def write_archive(target: Path, pw: bytes) -> int:
    import pyzipper

    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "budget.db"
        # снимок базы через SQLite backup API: корректен, даже если в этот момент открыт интерфейс
        src = sqlite3.connect(DATA / "budget.db")
        dst = sqlite3.connect(snap)
        src.backup(dst)
        dst.close()
        src.close()
        n = 0
        with pyzipper.AESZipFile(target, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
            z.setpassword(pw)
            z.setencryption(pyzipper.WZ_AES, nbits=256)
            z.write(snap, "budget/data/budget.db")
            for path in files_to_backup():
                z.write(path, "budget/" + str(path.relative_to(BUDGET)).replace("\\", "/"))
                n += 1
    return n + 1


def finish(target: Path, s: dict, verbose: bool) -> Path:
    """Вторая папка, ротация, отчёт."""
    size = target.stat().st_size / 1e6
    copies = [target]
    if s["copy_to"]:
        try:
            s["copy_to"].mkdir(parents=True, exist_ok=True)
            copies.append(Path(shutil.copy2(target, s["copy_to"] / target.name)))
        except OSError as e:
            print(f"! Вторая папка недоступна ({s['copy_to']}): {e}")
    for folder in {c.parent for c in copies}:
        old = sorted(folder.glob("budget_*.zip"))[:-s["keep"]]
        for f in old:
            f.unlink()
    if verbose:
        print(f"Копия: {target} ({size:.0f} МБ, проверена)")
        for c in copies[1:]:
            print(f"Вторая копия: {c}")
    return target


def list_backups():
    s = settings()
    for folder in [s["dir"]] + ([s["copy_to"]] if s["copy_to"] else []):
        files = sorted(folder.glob("budget_*.zip")) if folder.exists() else []
        print(f"{folder}: {len(files)} копий")
        for f in files[-15:]:
            print(f"  {f.name}  {f.stat().st_size / 1e6:.0f} МБ")


def restore(path: str):
    """Распаковка в отдельную папку рядом с budget — текущие данные не трогаются."""
    import pyzipper

    src = Path(path)
    if not src.exists():
        src = settings()["dir"] / path
    if not src.exists():
        raise SystemExit(f"Нет файла {path}")
    out = BUDGET.parent / f"budget_restore_{dt.datetime.now():%Y-%m-%d_%H%M%S}"
    with pyzipper.AESZipFile(src) as z:
        z.setpassword(password().encode())
        z.extractall(out)
    print(f"Распаковано в {out / 'budget'}")
    print("Чтобы вернуть данные: закрой интерфейс и замени папку budget\\data (и при необходимости config.ini) "
          "на распакованные. Сначала сделай backup текущего состояния.")


def main(argv: list[str]):
    cmd = argv[0] if argv else "create"
    if cmd == "password":
        set_password()
    elif cmd == "list":
        list_backups()
    elif cmd == "restore" and len(argv) > 1:
        restore(argv[1])
    elif cmd == "create":
        create()
    else:
        print(__doc__)
