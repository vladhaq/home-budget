"""Наличные на руках («кошелёк»).

Ты вносишь записи:
  count   — пересчёт: «на руках сейчас X zł»
  income  — пришли наличные (не с карты): пополняет кошелёк
  expense — расход наличными без чека
Автоматически (начиная с первой записи):
  + снятие в банкомате (выписка), − взнос наличных на счёт (выписка), − покупки по чекам с оплатой наличными.
При пересчёте разница «ожидалось − насчитано» — это траты наличными без чека (или неучтённый приход, если больше).

Доход наличными считается в момент взноса на счёт (категория «Доходы/Наличные» у операции в выписке).
Записи кошелька в доходы не идут — только меняют остаток на руках, чтобы одни деньги не посчитались дважды.

Время операций банка: банк даёт только дату. Операция появилась в выписке между двумя загрузками —
значит, случилась в этом окне; если в окно попадает твоя запись (пересчёт), порядок неизвестен —
такая операция помечается «уточни время», и время можно задать вручную (wallet_times).
"""
import json
import datetime as dt

from core import categories
from core.db import connect, save_purchase

KINDS = {"count": "пересчёт", "income": "доход наличными", "expense": "расход без чека"}


def db():
    return connect()  # таблицы wallet_entries / wallet_times — в core/db.py


def start_date(con) -> str | None:
    return con.execute("SELECT min(date) FROM wallet_entries").fetchone()[0]


def add(con, date: str, kind: str, amount: float, note: str | None = None) -> int:
    if kind not in KINDS:
        raise ValueError(f"тип: {', '.join(KINDS)}")
    if amount < 0:
        raise ValueError("сумма должна быть положительной")
    dt.datetime.fromisoformat(date)
    cur = con.execute("INSERT INTO wallet_entries (date, kind, amount, note) VALUES (?, ?, ?, ?)",
                      (date, kind, round(amount, 2), note or None))
    con.commit()
    return cur.lastrowid


def remove(con, entry_id: int):
    con.execute("DELETE FROM wallet_entries WHERE id = ?", (entry_id,))
    con.commit()


def bank_time(r, syncs: list[str], manual: list[str], fixed: dict) -> dict:
    """Когда была операция банка (известна только дата). -> {date, window, ambiguous, fixed}"""
    day0, day1 = r["date"] + "T00:00:00", r["date"] + "T23:59:59"
    if r["id"] in fixed:
        return {"date": fixed[r["id"]], "window": None, "ambiguous": False, "fixed": True}
    if not r["seen"]:  # загружена до того, как время загрузки стало запоминаться
        return {"date": r["date"] + "T12:00:00", "window": None, "ambiguous": False, "fixed": False}
    prev = max((s for s in syncs if s < r["seen"]), default=None)
    lo, hi = max(prev or day0, day0), min(r["seen"][:19], day1)
    if lo > hi:  # банк поставил дату позже загрузки — окно весь день
        lo, hi = day0, day1
    # в окне (lo, hi] — когда точно, неизвестно; ставим в конец окна. Твоя запись внутри окна — порядок под вопросом
    return {"date": hi, "window": [lo, hi], "ambiguous": any(lo < m < hi for m in manual), "fixed": False}


def timeline(con) -> dict:
    """События кошелька по времени с остатком после каждого. Учёт начинается с твоей первой записи."""
    start = start_date(con)
    if not start:
        return {"start": None, "events": [], "balance": None}
    ev = [{"date": r["date"], "kind": r["kind"], "amount": r["amount"], "note": r["note"], "id": r["id"], "auto": False}
          for r in con.execute("SELECT * FROM wallet_entries")]
    manual = [e["date"] for e in ev]
    has_bank = "category_id" in {r["name"] for r in con.execute("PRAGMA table_info(bank_tx)")}
    if has_bank:
        syncs = json.loads((con.execute("SELECT value FROM meta WHERE key = 'bank_syncs'").fetchone() or ["[]"])[0])
        fixed = {r["tx_id"]: r["at"] for r in con.execute("SELECT tx_id, at FROM wallet_times")}
        for r in con.execute("SELECT id, date, amount, type, description, seen FROM bank_tx WHERE date >= ?", (start[:10],)):
            if r["type"] == "CARD-ATM":
                kind, amount, note = "atm", -r["amount"], f"банкомат {r['description'] or ''}".strip()
            elif (r["type"] or "").startswith("CASH-IN"):
                kind, amount, note = "deposit", r["amount"], "взнос на счёт PKO"
            else:
                continue
            ev.append({"kind": kind, "amount": amount, "note": note, "auto": True, "tx": r["id"], "bank_date": r["date"],
                       **bank_time(r, syncs, manual, fixed)})
    for r in con.execute("SELECT date, total, merchant FROM purchases WHERE payment_method = 'cash' "
                         "AND source NOT IN ('wallet', 'bank') AND date >= ?", (start[:10],)):
        ev.append({"date": r["date"], "kind": "receipt", "amount": r["total"] or 0,
                   "note": f"чек {r['merchant'] or ''}".strip(), "auto": True})
    # до первой твоей записи кошелька ещё нет: такие взносы — доход, а не движение наличных
    ev = [e for e in ev if e["date"] >= start]
    # в один и тот же момент пересчёт идёт последним: «на руках сейчас» — после всех движений
    ev.sort(key=lambda e: (e["date"], e["kind"] == "count"))
    bal = None
    for e in ev:
        if e["kind"] == "count":
            e["expected"] = bal
            e["diff"] = None if bal is None else round(bal - e["amount"], 2)  # >0 — потрачено без чека
            bal = e["amount"]
        else:
            sign = 1 if e["kind"] in ("income", "atm") else -1
            bal = round((bal or 0) + sign * e["amount"], 2)
        e["balance"] = bal
    return {"start": start, "events": ev, "balance": bal}


def rebuild(con):
    """Покупки «кошелёк»: расходы без чека и разница при пересчёте; плюс вид взносов наличных в выписке."""
    categories.seed(con)
    K = categories.key_ids(con)
    con.execute("DELETE FROM items WHERE purchase_id LIKE 'wallet:%'")
    con.execute("DELETE FROM payments WHERE purchase_id LIKE 'wallet:%'")
    con.execute("DELETE FROM purchases WHERE source = 'wallet'")
    tl = timeline(con)
    for e in tl["events"]:
        if e["kind"] == "expense":
            name, amount, cat = e["note"] or "Расход наличными без чека", e["amount"], None
        elif e["kind"] == "count" and e.get("diff") and e["diff"] > 0.009:
            name, amount, cat = "Траты наличными без чека (по пересчёту)", e["diff"], K.get("other.cash")
        else:
            continue
        pid = f"wallet:{e.get('id') or e['date']}"
        save_purchase(con, {"id": pid, "source": "wallet", "date": e["date"][:19], "merchant": "Наличные",
                            "store": KINDS.get(e["kind"]), "total": amount, "payment_method": "cash", "discount": 0},
                      [{"name": name, "product_code": None, "qty": 1, "unit_price": amount, "amount": amount,
                        "discount": None}])
        if cat:
            con.execute("UPDATE items SET category_id = ?, category_source = 'wallet' WHERE purchase_id = ? "
                        "AND category_source IS NULL", (cat, pid))
    # взнос наличных на счёт — доход (так видно «получил / потратил»); в кошельке он уменьшает наличные на руках
    have = {r["name"] for r in con.execute("PRAGMA table_info(bank_tx)")}
    if "category_id" in have:
        con.execute("UPDATE bank_tx SET category_id = ?, category_source = 'wallet' "
                    "WHERE type LIKE 'CASH-IN%' AND coalesce(category_source, '') != 'manual'", (K.get("income.cash"),))
    con.commit()
    return tl


def set_time(con, tx_id: str, at: str | None):
    """Твоё уточнение времени операции банка (at = 'YYYY-MM-DDTHH:MM'); None — снова по выписке."""
    if at:
        dt.datetime.fromisoformat(at)
        con.execute("INSERT OR REPLACE INTO wallet_times VALUES (?, ?)", (tx_id, at[:16] + ":00"))
    else:
        con.execute("DELETE FROM wallet_times WHERE tx_id = ?", (tx_id,))
    con.commit()
