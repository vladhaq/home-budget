"""Проверка распознавания фото чеков на твоих же чеках: каждое фото, которое совпало с электронным чеком
Lidl Plus / Kaufland Card и прикрепилось к нему, — готовый тест с правильным ответом.

  python tools/ocrbench.py            итог, позиции (сумма и скидка), похожесть названий — по уровням OCR
  python tools/ocrbench.py -v         то же и позиции каждого чека: что распознано против электронного чека
  python tools/ocrbench.py --fresh    распознать заново (иначе тексты OCR берутся из data/ocr_cache/)

Уровни: Tesseract (5 вариантов картинки), EasyOCR, «программа» — как при импорте (EasyOCR — только если
у Tesseract сумма позиций не сошлась с итогом). Новый движок OCR — функция в ENGINES и уровень в LEVELS.
Кэш и отчёт — в data/ (там твои чеки — в git и экспорт не попадают).
"""
import hashlib
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.common import DATA, fold, money  # noqa: E402
from core.db import connect  # noqa: E402
from receipts import photos  # noqa: E402
from receipts.textparse import merge, parse_text  # noqa: E402

CACHE = DATA / "ocr_cache"


def _easyocr(path: Path) -> str:
    return "\n".join(t for p in photos.load(path) if not isinstance(p, str) and (t := photos.easyocr_text(p)))


# движок -> распознанный текст (Tesseract — список вариантов картинки)
ENGINES = {
    "tesseract": lambda path: [t for p in photos.load(path) for t in photos.tesseract_texts(p)],
    "easyocr": _easyocr,
}
# уровень -> чек из текстов движков (hint — дата чека для выбора даты)
LEVELS = {
    "Tesseract": lambda tx, hint: merge([parse_text(t) for t in tx["tesseract"]], hint),
    "EasyOCR": lambda tx, hint: merge([parse_text(tx["easyocr"])], hint),
    "программа": lambda tx, hint: photos.vote(tx["tesseract"], tx["easyocr"], hint)[0],
}


def cases(con) -> list[dict]:
    """Фото, прикреплённые к электронным чекам, и позиции этих чеков (суммы — из чека, не подогнанные сверкой)."""
    out = []
    for a in con.execute("""SELECT a.purchase_id, a.path, p.date, p.total, p.merchant FROM attachments a
                            JOIN purchases p ON p.id = a.purchase_id
                            WHERE p.source IN ('lidl', 'kaufland') ORDER BY p.date""").fetchall():
        path = DATA / a["path"]
        if not path.exists():
            continue
        ref = [dict(r) for r in con.execute(
            """SELECT name, coalesce(orig_amount, amount) amount, coalesce(orig_discount, discount, 0) discount
               FROM items WHERE purchase_id = ? ORDER BY line""", (a["purchase_id"],))]
        out.append(dict(a) | {"path": path, "ref": ref})
    return out


def texts(path: Path, fresh=False) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (hashlib.sha1(path.read_bytes()).hexdigest()[:16] + ".json")
    tx = {} if fresh or not f.exists() else json.loads(f.read_text(encoding="utf-8"))
    missing = [e for e in ENGINES if e not in tx]
    for e in missing:
        print(f"  {path.name}: распознаю ({e})…")
        tx[e] = ENGINES[e](path)
    if missing:
        f.write_text(json.dumps(tx, ensure_ascii=False), encoding="utf-8")
    return tx


def compare(r: dict, ref: list[dict], total: float) -> dict:
    """Позиции сравниваются по товару: электронный чек Kaufland пишет каждую штуку строкой («2,99» × 3),
    а бумажный — одной («2 шт.» и «1 шт.»), поэтому суммы и скидки складываются по названию."""
    groups = {}
    for i in ref:
        g = groups.setdefault(fold(i["name"]), {"name": i["name"], "amount": 0.0, "discount": 0.0, "got": 0.0, "got_d": 0.0})
        g["amount"] += i["amount"] or 0
        g["discount"] += i["discount"] or 0
    extra, sims = [], []
    for it in r["items"]:
        f = fold(it["name"] or "")
        sim, key = max(((SequenceMatcher(None, f, k).ratio(), k) for k in groups), default=(0, None))
        if sim < 0.5:
            extra.append(it)
            continue
        sims.append(sim)
        groups[key]["got"] += it["amount"] or 0
        groups[key]["got_d"] += it["discount"] or 0
    ok = [g for g in groups.values() if abs(g["got"] - g["amount"]) < 0.011 and abs(g["got_d"] - g["discount"]) < 0.011]
    return {"total_ok": r["total"] is not None and abs(r["total"] - total) < 0.011, "status": r["status"],
            "groups": len(groups), "groups_ok": len(ok), "extra": len(extra),
            "name_sim": sum(sims) / len(sims) if sims else 0.0, "bad": [g for g in groups.values() if g not in ok],
            "extra_items": extra}


def main(argv):
    fresh, verbose = "--fresh" in argv, "-v" in argv
    con = connect()
    cs = cases(con)
    if not cs:
        print("Нет фото, прикреплённых к электронным чекам Lidl/Kaufland: отправь фото такого чека в Telegram-группу.")
        return
    totals = {lvl: {"total_ok": 0, "groups": 0, "groups_ok": 0, "extra": 0, "status_ok": 0, "sim": []} for lvl in LEVELS}
    for c in cs:
        tx = texts(c["path"], fresh)
        print(f"\n{c['date'][:16].replace('T', ' ')}  {c['merchant']}  {money(c['total'])}  позиций в чеке: {len(c['ref'])}")
        for lvl, fn in LEVELS.items():
            r = fn(tx, c["date"])
            m = compare(r, c["ref"], c["total"])
            t = totals[lvl]
            t["total_ok"] += m["total_ok"]
            t["groups"] += m["groups"]
            t["groups_ok"] += m["groups_ok"]
            t["extra"] += m["extra"]
            t["status_ok"] += r["status"] == "ok"
            t["sim"].append(m["name_sim"])
            extra = f", лишних {m['extra']}" if m["extra"] else ""
            print(f"  {lvl:<10} итог {'✓' if m['total_ok'] else '✗'}  товары {m['groups_ok']}/{m['groups']}{extra}"
                  f"  названия {m['name_sim']:.0%}  статус {r['status']}")
            if verbose:
                for g in m["bad"]:
                    print(f"      ✗ {g['name'][:36]:<36} надо {g['amount']:.2f} −{g['discount']:.2f}, "
                          f"распознано {g['got']:.2f} −{g['got_d']:.2f}")
                for it in m["extra_items"]:
                    print(f"      + лишнее: {str(it['name'])[:36]:<36} {it['amount'] or 0:.2f}")
    n = len(cs)
    print(f"\nИтого по {n} чек{'у' if n == 1 else 'ам'}:")
    for lvl, t in totals.items():
        share = t["groups_ok"] / t["groups"] if t["groups"] else 0
        print(f"  {lvl:<10} итог верно {t['total_ok']}/{n}, товары верно {t['groups_ok']}/{t['groups']} ({share:.0%}), "
              f"лишних строк {t['extra']}, сумма сошлась {t['status_ok']}/{n}, названия {sum(t['sim']) / n:.0%}")


if __name__ == "__main__":
    main(sys.argv[1:])
