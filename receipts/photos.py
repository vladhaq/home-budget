"""Фото/сканы бумажных чеков: любой формат -> OCR (Tesseract, при сомнении EasyOCR) -> позиции -> база.

Форматы: jpg/jpeg/png/webp/bmp/tiff, heic (iPhone), pdf (текстовый слой читается напрямую, без OCR).
"""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import requests
from PIL import Image, ImageOps

from core.common import BUDGET, DATA, money
from core.db import connect, save_purchase
from receipts.textparse import merge, parse_text

INBOX = BUDGET / "receipts" / "inbox"
TESSDATA = DATA / "tessdata"
PHOTOS = DATA / "photos"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
STATUS = {"ok": "сумма сходится", "derived": "≈ одна позиция вычислена из итога", "check": "⚠ проверить вручную"}


# ---------------------------------------------------------------- входные файлы

def load(path: Path) -> list:
    """-> список страниц: PIL.Image или str (готовый текст из PDF)."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        import pymupdf
        pages = []
        with pymupdf.open(path) as doc:
            for pg in doc:
                text = pg.get_text()
                if len(text.strip()) > 50:
                    pages.append(text)
                else:  # скан в PDF — рендерим страницу
                    pix = pg.get_pixmap(dpi=300)
                    pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        return pages
    if ext in (".heic", ".heif"):
        from pillow_heif import register_heif_opener
        register_heif_opener()
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)  # фото с телефона может быть повёрнуто
    return [img.convert("RGB")]


# ---------------------------------------------------------------- OCR

def tesseract_exe() -> str:
    exe = shutil.which("tesseract") or next((p for p in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe") if Path(p).exists()), None)
    if not exe:
        raise SystemExit("Нужен Tesseract OCR: winget install UB-Mannheim.TesseractOCR")
    TESSDATA.mkdir(parents=True, exist_ok=True)
    for lang in ("pol", "eng"):
        f = TESSDATA / f"{lang}.traineddata"
        if not f.exists():
            print(f"  скачиваю словарь OCR: {lang}")
            r = requests.get(f"https://github.com/tesseract-ocr/tessdata_best/raw/main/{lang}.traineddata", timeout=180)
            r.raise_for_status()
            f.write_bytes(r.content)
    return exe


def variants(img: Image.Image):
    """Разные подготовки картинки: у каждой свои ошибки OCR, голосование их гасит."""
    g = ImageOps.grayscale(img)
    scale = 2 if g.width < 1500 else 1
    big = g.resize((g.width * scale, g.height * scale), Image.LANCZOS) if scale > 1 else g
    big3 = g.resize((g.width * 3, g.height * 3), Image.LANCZOS) if g.width < 900 else big
    yield big, 4
    yield big, 6
    yield big.point(lambda v: 255 if v > 150 else 0), 6
    yield big3, 4
    yield big3.point(lambda v: 255 if v > 150 else 0), 6


def tesseract(img: Image.Image, psm: int, exe: str) -> str:
    tmp = DATA / "_ocr.png"
    img.save(tmp)
    out = subprocess.run([exe, str(tmp), "stdout", "-l", "pol+eng", "--psm", str(psm), "--tessdata-dir", str(TESSDATA)],
                         capture_output=True, text=True, encoding="utf-8",
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # без мелькающих окон при автозапуске
    tmp.unlink(missing_ok=True)
    return out.stdout


_reader = None


def easyocr_text(img: Image.Image) -> str | None:
    """EasyOCR -> строки текста (группировка слов по вертикали). None, если EasyOCR не установлен."""
    global _reader
    try:
        import easyocr
        import numpy as np
    except ImportError:
        return None
    if _reader is None:
        print("  загружаю EasyOCR (первый раз долго)...")
        _reader = easyocr.Reader(["pl", "en"], gpu=False, verbose=False)
    g = ImageOps.grayscale(img)
    if g.width < 1000:
        g = g.resize((g.width * 2, g.height * 2), Image.LANCZOS)
    boxes = _reader.readtext(np.array(g), detail=1, paragraph=False)
    words = sorted(((sum(p[1] for p in b) / 4, b[0][0], abs(b[2][1] - b[0][1]), t) for b, t, _ in boxes))
    lines, cur, cur_y = [], [], None
    for y, x, h, t in words:
        if cur and abs(y - cur_y) > max(h, 10) * 0.6:
            lines.append(" ".join(w for _, w in sorted(cur)))
            cur = []
        cur.append((x, t))
        cur_y = y if len(cur) == 1 else (cur_y + y) / 2
    if cur:
        lines.append(" ".join(w for _, w in sorted(cur)))
    return "\n".join(lines)


EASYOCR_WEIGHT = 2  # у EasyOCR другие ошибки, чем у Tesseract, а вариант у него один — двойной вес


def tesseract_texts(page) -> list[str]:
    """Все варианты подготовки картинки через Tesseract (текстовая страница PDF — как есть)."""
    if isinstance(page, str):
        return [page]
    exe = tesseract_exe()
    return [tesseract(img, psm, exe) for img, psm in variants(page)]


def vote(tess: list[str], easy, hint_date: str | None = None, verbose=False) -> tuple[dict, list[str]]:
    """Голосование, как в программе: сначала варианты Tesseract; если сумма позиций не сошлась с итогом —
    добавляется EasyOCR. easy — текст EasyOCR или функция, которая его вернёт (OCR дорогой — только по нужде).
    -> (чек, тексты, которые участвовали)"""
    runs = [parse_text(t) for t in tess]
    result = merge(runs, hint_date)
    if result["status"] == "ok" or easy is None:
        return result, list(tess)
    if verbose:
        print("  Tesseract не сошёлся с итогом — перепроверяю EasyOCR")
    text = easy() if callable(easy) else easy
    if not text:
        return result, list(tess)
    return merge(runs + [parse_text(text)] * EASYOCR_WEIGHT, hint_date), list(tess) + ["### EasyOCR\n" + text]


def recognize(path: Path, hint_date: str | None = None, verbose=False) -> tuple[dict, str]:
    """-> (чек после голосования, весь распознанный текст для архива)"""
    pages = load(path)
    tess = [t for page in pages for t in tesseract_texts(page)]
    images = [p for p in pages if not isinstance(p, str)]
    easy = (lambda: "\n".join(t for p in images if (t := easyocr_text(p)))) if images else None
    result, texts = vote(tess, easy, hint_date, verbose)
    return result, "\n\n### ---\n".join(texts)


# ---------------------------------------------------------------- импорт в базу

def import_file(con, path: Path, hint_date: str | None = None, source_ref: str | None = None) -> dict | None:
    digest = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    pid = f"photo:{digest}"
    if con.execute("SELECT 1 FROM purchases WHERE id = ?", (pid,)).fetchone():
        print(f"  уже импортирован: {path.name}")
        return None
    if a := con.execute("SELECT purchase_id FROM attachments WHERE path LIKE ?", (f"photos/{digest}.%",)).fetchone():
        print(f"  уже прикреплён к {a['purchase_id']}: {path.name}")
        return None
    r, text = recognize(path, hint_date, verbose=True)
    PHOTOS.mkdir(parents=True, exist_ok=True)
    keep = PHOTOS / f"{digest}{path.suffix.lower()}"
    if not keep.exists():
        shutil.copy2(path, keep)
    (PHOTOS / f"{digest}.txt").write_text(text, encoding="utf-8")
    (PHOTOS / f"{digest}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    rel = str(keep.relative_to(DATA)).replace("\\", "/")
    if dup := find_same_receipt(con, r):
        # тот же чек уже есть (электронный чек Kaufland/Lidl, письмо, другое фото) — фото прикрепляем к нему
        attach(con, dup["id"], rel, r, source_ref)
        print(f"  {path.name}: совпало с {dup['id']} ({dup['date'][:16].replace('T', ' ')}, {money(dup['total'])}) — фото прикреплено")
        return {**r, "attached": dup["id"]}
    note = None if r["status"] == "ok" else STATUS[r["status"]]
    save_purchase(con, {
        "id": pid, "source": "photo", "date": r["date"] or hint_date or "", "merchant": r["merchant"],
        "store": r["store"], "total": r["total"], "payment_method": r["payment"],
        "raw_path": rel,
    }, r["items"], [])
    con.execute("UPDATE purchases SET note = ? WHERE id = ?", (note, pid))
    from core import categories
    categories.categorize(con)
    con.commit()
    print(f"  {path.name}: {r['merchant'] or 'магазин?'} {r['date'] or 'дата?'} итог {money(r['total'] or 0)} "
          f"позиций {len(r['items'])} оплата {r['payment'] or '?'} — {STATUS[r['status']]}")
    return r


def find_same_receipt(con, r: dict):
    """Уже известная покупка с тем же чеком: та же сумма, время ±15 минут, магазин совпадает или не распознан."""
    if not r.get("total") or not r.get("date") or len(r["date"]) <= 10:
        return None
    rows = con.execute("""SELECT id, date, total, merchant FROM purchases WHERE source NOT IN ('bank', 'wallet')
                           AND abs(total - ?) < 0.01 AND abs(julianday(date) - julianday(?)) * 1440 <= 15
                           ORDER BY abs(julianday(date) - julianday(?))""", (r["total"], r["date"], r["date"])).fetchall()
    from core.common import fold
    want = fold(r.get("merchant") or "")
    return next((x for x in rows if not want or not x["merchant"] or fold(x["merchant"]) in want or want in fold(x["merchant"])), None)


def attach(con, purchase_id: str, rel: str, r: dict, source_ref: str | None = None):
    import datetime as dt
    con.execute("INSERT OR REPLACE INTO attachments VALUES (?, ?, ?, ?, ?, ?, ?)",
                (purchase_id, rel, source_ref, r.get("date"), r.get("total"), r.get("merchant"),
                 dt.datetime.now().isoformat(timespec="seconds")))
    con.commit()


def merge_duplicates(con) -> int:
    """Фото чека, импортированное раньше, чем пришёл электронный чек того же магазина, — убрать дубль и прикрепить фото."""
    n = 0
    for p in con.execute("SELECT * FROM purchases WHERE source = 'photo'").fetchall():
        same = find_same_receipt(con, {"total": p["total"], "date": p["date"], "merchant": p["merchant"]})
        same = same if same and same["id"] != p["id"] and not same["id"].startswith("photo:") else None
        if not same:
            continue
        attach(con, same["id"], p["raw_path"], {"date": p["date"], "total": p["total"], "merchant": p["merchant"]})
        for table, col in (("items", "purchase_id"), ("payments", "purchase_id"), ("purchases", "id")):
            con.execute(f"DELETE FROM {table} WHERE {col} = ?", (p["id"],))
        n += 1
    con.commit()
    return n


def import_paths(args: list[str]):
    paths = []
    for a in args or [str(INBOX)]:
        p = Path(a)
        if p.is_dir():
            paths += sorted(x for x in p.iterdir() if x.suffix.lower() in IMAGE_EXT | {".pdf"})
        elif p.exists():
            paths.append(p)
    if not paths:
        print(f"Нет файлов. Положи фото/PDF чеков в {INBOX} или укажи путь.")
        return
    con = connect()
    for p in paths:
        import_file(con, p)
