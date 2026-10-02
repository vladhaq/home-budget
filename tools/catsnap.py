"""Снимок категорий и сравнение «было → стало» — проверка любого изменения словаря, правил, сверки.

  python tools/catsnap.py save [имя]      запомнить, как сейчас размечены позиции и операции банка
  python tools/catsnap.py diff [имя] [-n 5]  что изменилось с момента снимка: переходы категорий с суммами,
                                          покупки, которые появились/исчезли/поменяли сумму или статус

Снимки лежат в data/snapshots/ (там твои покупки — в git и экспорт не попадают).
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import categories  # noqa: E402
from core.common import DATA  # noqa: E402
from core.db import connect  # noqa: E402

SNAP = DATA / "snapshots"


def take(con) -> dict:
    paths = categories.paths(con)
    kinds = {r["id"]: r["kind"] for r in con.execute("SELECT id, kind FROM categories")}
    pcols = {r["name"] for r in con.execute("PRAGMA table_info(purchases)")}
    status = "status" if "status" in pcols else "NULL"
    items = {}
    for r in con.execute(f"""SELECT i.purchase_id, i.line, i.name, i.amount - coalesce(i.discount, 0) v, i.category_id,
                                    i.category_source, p.merchant, p.source, p.date, {status} st
                             FROM items i JOIN purchases p ON p.id = i.purchase_id"""):
        items[f"{r['purchase_id']}#{r['line']}"] = {
            "name": r["name"], "v": round(r["v"] or 0, 2), "cat": paths.get(r["category_id"]),
            "kind": kinds.get(r["category_id"]), "src": r["category_source"], "merchant": r["merchant"],
            "source": r["source"], "date": (r["date"] or "")[:10], "status": r["st"]}
    purchases = {r["id"]: {"total": r["total"], "merchant": r["merchant"], "date": (r["date"] or "")[:10],
                           "status": r["st"]}
                 for r in con.execute(f"SELECT id, total, merchant, date, {status} st FROM purchases")}
    bank = {}
    for r in con.execute("SELECT id, date, amount, counterparty, description, category_id, category_source FROM bank_tx "
                         "WHERE amount > 0"):
        bank[r["id"]] = {"cat": paths.get(r["category_id"]), "src": r["category_source"], "v": r["amount"],
                         "date": r["date"], "who": r["counterparty"] or (r["description"] or "")[:30]}
    return {"items": items, "purchases": purchases, "bank_in": bank}


def save(name="base"):
    SNAP.mkdir(parents=True, exist_ok=True)
    snap = take(connect())
    (SNAP / f"{name}.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    print(f"Снимок «{name}»: позиций {len(snap['items'])}, покупок {len(snap['purchases'])}, "
          f"поступлений {len(snap['bank_in'])}")


def counted(it) -> bool:
    """Позиция идёт в расходы: категория-расход или без категории, покупка не «под вопросом»."""
    return it["kind"] in (None, "expense") and it.get("status") != "doubt"


def diff(name="base", n=5):
    old = json.loads((SNAP / f"{name}.json").read_text(encoding="utf-8"))
    new = take(connect())
    moves = defaultdict(list)
    for k, it in new["items"].items():
        was = old["items"].get(k)
        if was and (was["cat"] != it["cat"] or (was.get("status") != it.get("status"))):
            key = (was["cat"] or "⚠ без категории", it["cat"] or "⚠ без категории")
            if was.get("status") != it.get("status"):
                key = (key[0] + f" [{was.get('status') or 'ok'}]", key[1] + f" [{it.get('status') or 'ok'}]")
            moves[key].append(it)
    print(f"=== Переходы категорий ({sum(len(v) for v in moves.values())} позиций)")
    for (a, b), its in sorted(moves.items(), key=lambda x: -abs(sum(i["v"] for i in x[1]))):
        print(f"  {a}  →  {b}: {len(its)} поз., {sum(i['v'] for i in its):.2f} zł")
        for it in sorted(its, key=lambda i: -abs(i["v"]))[:n]:
            print(f"      {it['v']:9.2f}  {it['name'][:70]}  [{it['merchant']}]")
    gone = [k for k in old["purchases"] if k not in new["purchases"]]
    came = [k for k in new["purchases"] if k not in old["purchases"]]
    changed = [k for k in new["purchases"] if k in old["purchases"]
               and abs((new["purchases"][k]["total"] or 0) - (old["purchases"][k]["total"] or 0)) > 0.005]
    for title, keys, src in (("Исчезли покупки", gone, old), ("Новые покупки", came, new)):
        if keys:
            print(f"=== {title}: {len(keys)}, {sum(src['purchases'][k]['total'] or 0 for k in keys):.2f} zł")
            for k in keys[: n * 4]:
                p = src["purchases"][k]
                print(f"      {p['date']}  {p['total'] or 0:9.2f}  {p['merchant']}  ({k})")
    if changed:
        print(f"=== Изменилась сумма: {len(changed)}")
        for k in changed[: n * 4]:
            print(f"      {k}: {old['purchases'][k]['total']} → {new['purchases'][k]['total']}")
    bmoves = defaultdict(list)
    for k, t in new["bank_in"].items():
        was = old["bank_in"].get(k)
        if was and was["cat"] != t["cat"]:
            bmoves[(was["cat"], t["cat"])].append(t)
    if bmoves:
        print("=== Поступления: переходы категорий")
        for (a, b), ts in sorted(bmoves.items(), key=lambda x: -sum(t["v"] for t in x[1])):
            print(f"  {a}  →  {b}: {len(ts)} шт., {sum(t['v'] for t in ts):.2f} zł")
            for t in ts[:n]:
                print(f"      {t['date']}  {t['v']:9.2f}  {t['who']}")
    tot = defaultdict(lambda: [0.0, 0.0])
    for i, src in ((0, old), (1, new)):
        for it in src["items"].values():
            if counted(it):
                tot[it["cat"] or "⚠ без категории"][i] += it["v"]
    rows = [(c, a, b) for c, (a, b) in tot.items() if abs(a - b) > 0.005]
    if rows:
        print("=== Расходы по категориям: было → стало")
        for c, a, b in sorted(rows, key=lambda r: -abs(r[2] - r[1])):
            print(f"  {c:50} {a:10.2f} → {b:10.2f}  ({b - a:+.2f})")
    sa = sum(a for _, (a, _b) in tot.items())
    sb = sum(b for _, (_a, b) in tot.items())
    print(f"=== Всего расходов: {sa:.2f} → {sb:.2f} ({sb - sa:+.2f})")


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "diff"
    rest = [a for a in args[1:] if not a.startswith("-") and not a.isdigit()]
    n = int(args[args.index("-n") + 1]) if "-n" in args else 5
    if cmd == "save":
        save(*(rest[:1] or ["base"]))
    elif cmd == "diff":
        diff(*(rest[:1] or ["base"]), n=n)
    else:
        print(__doc__)
