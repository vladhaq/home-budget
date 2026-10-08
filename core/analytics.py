"""Аналитика: регулярные платежи и поступления, календарь ожидаемого, прогноз расходов до конца месяца.

Регулярное — серия операций одного магазина / получателя / отправителя похожей суммы с устойчивым интервалом
(неделя, месяц, квартал, год). Ищем в выписке и онлайн-заказах (чеки Lidl/Kaufland — это обычные покупки, не подписки).
Каждую найденную серию можно подтвердить или отклонить (таблица recurring_marks): отклонённая не участвует
в календаре и прогнозе, подтверждённая — участвует, даже если в последнее время шла неровно.

Прогноз месяца = уже потрачено + оставшиеся в этом месяце регулярные платежи + переменные траты, которые
обычно приходятся на оставшиеся дни (медиана по последним 6 полным месяцам, по группам категорий).
"""
import datetime as dt
import json
import statistics as st
from collections import defaultdict

from core import categories
from core.db import connect

# (тип, дней в периоде, допустимый интервал от–до)
PERIODS = [("week", 7, 6, 8), ("month", 30.44, 26, 35), ("quarter", 91.3, 82, 100), ("year", 365.25, 345, 385)]
PERIOD_WORD = {"week": "каждую неделю", "month": "каждый месяц", "quarter": "раз в квартал", "year": "раз в год"}
HISTORY_DAYS = 400   # серию ищем по последним ~13 месяцам: закончившиеся давно подписки не интересны
BASE_MONTHS = 6      # «обычный» месяц — медиана по стольким последним полным месяцам


def day(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def add_months(d: dt.date, n: int, dom: int | None = None) -> dt.date:
    """Тот же день месяца через n месяцев (31-е в феврале -> последний день февраля)."""
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(y, m, min(dom or d.day, last))


def month_end(d: dt.date) -> dt.date:
    return add_months(d.replace(day=1), 1) - dt.timedelta(days=1)


def clusters(evs: list[dict], spread: float = 1.35) -> list[list[dict]]:
    """Операции одной стороны — по близости суммы: соседние суммы не дальше ×1,35 от наименьшей в группе.
    Аренда 1 830 и 1 900 — одна серия, коммунальные 500 — другая; Claude Pro 98,19…100,54 — одна (без границы на 100 zł)."""
    out = []
    for e in sorted(evs, key=lambda e: e["amount"]):
        if out and e["amount"] <= out[-1][0]["amount"] * spread:
            out[-1].append(e)
        else:
            out.append([e])
    return out


# ---------------------------------------------------------------- события

def events(con) -> list[dict]:
    """Траты (онлайн-заказы и операции банка без чека) и поступления на счёт за последние HISTORY_DAYS дней."""
    since = (dt.date.today() - dt.timedelta(days=HISTORY_DAYS)).isoformat()
    kinds = {r["id"]: r["kind"] for r in con.execute("SELECT id, kind FROM categories")}
    out = []
    # дата — из выписки, если покупка там нашлась: в этот день деньги уходят со счёта (в письме Stripe — на день-два раньше)
    for p in con.execute("""SELECT p.id, coalesce(b.date, p.date) date, p.merchant, p.total, p.source,
                                   (SELECT i.category_id FROM items i WHERE i.purchase_id = p.id ORDER BY i.amount DESC LIMIT 1) cat
                            FROM purchases p LEFT JOIN bank_tx b ON b.id = p.bank_tx_id
                            WHERE p.source IN ('bank', 'email') AND p.total > 0 AND p.refund_of IS NULL
                              AND coalesce(p.status, '') != 'doubt' AND coalesce(b.date, p.date) >= ?""", (since,)):
        shop = categories.shop_key(p["merchant"])
        if not shop or p["merchant"] in categories.PLACEHOLDER_SHOPS:
            continue
        kind = kinds.get(p["cat"]) or "expense"
        if kind == "income":
            continue
        out.append({"id": p["id"], "date": p["date"][:10], "amount": p["total"], "name": p["merchant"], "cat": p["cat"],
                    "flow": "out", "kind": kind, "side": f"out|{shop}"})
    for t in con.execute("""SELECT id, date, amount, counterparty, description, type, category_id FROM bank_tx
                            WHERE amount > 0 AND type NOT LIKE '%RETURN%' AND type NOT LIKE 'CASH-IN%' AND date >= ?""", (since,)):
        who = t["counterparty"] or t["description"] or ""
        shop = categories.shop_key(who)
        if not shop:
            continue
        out.append({"id": t["id"], "date": t["date"], "amount": t["amount"], "name": who, "cat": t["category_id"],
                    "flow": "in", "kind": kinds.get(t["category_id"]) or "income", "side": f"in|{shop}"})
    return out


def detect(evs: list[dict]) -> dict | None:
    """Серия -> период, типичная сумма, следующая дата, уверенность; None — не регулярная."""
    by_day = {}
    for e in sorted(evs, key=lambda e: e["date"]):  # две операции в один день — одна (доплата, разбитый платёж)
        prev = by_day.get(e["date"])
        by_day[e["date"]] = {**e, "amount": round(prev["amount"] + e["amount"], 2), "ids": prev["ids"] + [e["id"]]} \
            if prev else {**e, "ids": [e["id"]]}
    seq = list(by_day.values())
    dates = [day(e["date"]) for e in seq]
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    if not gaps:
        return None
    best = None
    for name, days, lo, hi in PERIODS:
        share = sum(lo <= g <= hi for g in gaps) / len(gaps)
        if best is None or share > best[1]:
            best = (name, share, days, lo, hi)
    name, share, days, lo, hi = best
    need = 2 if name == "year" else 3
    if len(seq) < need or share < 0.6 or not lo <= st.median(gaps) <= hi:
        return None
    amounts = [e["amount"] for e in seq]
    typical = st.median(amounts[-3:])
    last, today = dates[-1], dt.date.today()
    active = (today - last).days <= days * 1.6 + 5
    # по календарю (тот же день месяца) или по интервалу (проездной на 30 дней)
    doms = [d.day for d in dates[-6:]]
    by_calendar = name in ("month", "quarter", "year") and (max(doms) - min(doms) <= 6)
    step = {"month": 1, "quarter": 3, "year": 12}.get(name)
    dom = round(st.median(doms)) if by_calendar else None
    nxt = add_months(last, step, dom) if by_calendar else last + dt.timedelta(days=round(st.median(gaps)))
    late = active and nxt < today - dt.timedelta(days=3)
    n = len(seq)
    confidence = "высокая" if n >= 6 and share >= 0.8 else "средняя" if n >= 4 and share >= 0.7 else "низкая"
    change = None
    if n >= 2 and amounts[-2] and abs(amounts[-1] / amounts[-2] - 1) > 0.05:
        change = round((amounts[-1] / amounts[-2] - 1) * 100)
    spread = (max(amounts[-6:]) - min(amounts[-6:])) / typical if typical else 0
    return {"period": name, "period_word": PERIOD_WORD[name], "days": days, "dom": dom, "step": step,
            "by_calendar": by_calendar, "n": n, "share": round(share, 2), "confidence": confidence,
            "typical": round(typical, 2), "last_amount": amounts[-1], "variable": spread > 0.1, "change": change,
            "first": seq[0]["date"], "last": last.isoformat(), "next": nxt.isoformat(), "active": active, "late": late,
            "ids": [i for e in seq for i in e["ids"]], "history": [{"date": e["date"], "amount": e["amount"]} for e in seq[-12:]]}


def find_mark(marks: dict, side: str, amount: float):
    """Твоя отметка для серии: та же сторона и сумма в пределах ±30% (сумма подписки со временем немного меняется)."""
    best = None
    for key, state in marks.items():
        s, _, a = key.rpartition("|")
        try:
            a = float(a)
        except ValueError:
            continue
        if s == side and abs(a - amount) <= 0.3 * amount and (best is None or abs(a - amount) < best[0]):
            best = (abs(a - amount), state)
    return best[1] if best else None


def recurring(con) -> list[dict]:
    paths = categories.paths(con)
    marks = {r["key"]: r["state"] for r in con.execute("SELECT key, state FROM recurring_marks")}
    sides = defaultdict(list)
    for e in events(con):
        sides[e["side"]].append(e)
    out = []
    for side, evs in sides.items():
        for group in clusters(evs):
            r = detect(group)
            if not r:
                continue
            lastev = max(group, key=lambda e: e["date"])
            key = f"{side}|{r['typical']:.2f}"
            out.append(r | {"key": key, "flow": lastev["flow"], "kind": lastev["kind"], "name": lastev["name"],
                            "cat": lastev["cat"], "cat_path": paths.get(lastev["cat"]),
                            "state": find_mark(marks, side, r["typical"]) or "suggested"})
    out.sort(key=lambda r: (r["state"] == "rejected", not r["active"], r["flow"], -r["typical"]))
    return out


def counts(r: dict) -> bool:
    """Серия участвует в календаре и прогнозе: подтверждена или найдена уверенно и не отклонена."""
    return r["state"] == "confirmed" or (r["state"] != "rejected" and r["active"] and r["confidence"] != "низкая")


def occurrences(r: dict, until: dt.date) -> list[dt.date]:
    """Ожидаемые даты от следующей до until (просроченная — сегодняшней датой)."""
    out, d, today = [], day(r["next"]), dt.date.today()
    k = 0
    while d <= until and k < 60:
        out.append(max(d, today))
        k += 1
        d = add_months(day(r["next"]), r["step"] * k, r["dom"]) if r["by_calendar"] else d + dt.timedelta(days=round(r["days"]))
    return out


# ---------------------------------------------------------------- календарь и остаток на счёте

def bank_now(con) -> dict | None:
    """Остаток всех счетов сейчас: amount — доступно (без заблокированных покупок, которые банк ещё не провёл),
    booked — по выписке, blocked — разница. У счетов из файлов блокировок не видно: booked = amount."""
    rows = [json.loads(r["value"]) for r in con.execute("SELECT value FROM meta WHERE key LIKE 'bank_balance:%'")]
    if not rows:
        return None
    amount = sum(float(b["amount"]) for b in rows)
    booked = sum(float(b.get("booked", b["amount"])) for b in rows)
    return {"amount": round(amount, 2), "booked": round(booked, 2), "blocked": round(booked - amount, 2),
            "date": max(b["date"] for b in rows)}


def bank_balance(con) -> tuple[float | None, str | None]:
    """Доступный остаток (для прогноза и «наличные + карта») и на какую дату."""
    n = bank_now(con)
    return (n["amount"], n["date"]) if n else (None, None)


def calendar(con, rec: list[dict], horizon_days: int = 45) -> dict:
    until = max(month_end(dt.date.today()), dt.date.today() + dt.timedelta(days=horizon_days))
    items = []
    for r in rec:
        if r["state"] == "rejected" or not r["active"]:
            continue
        for d in occurrences(r, until):
            items.append({"date": d.isoformat(), "name": r["name"], "cat_path": r["cat_path"], "key": r["key"],
                          "amount": r["typical"] if r["flow"] == "in" else -r["typical"], "flow": r["flow"],
                          "sure": counts(r), "late": r["late"] and d == dt.date.today(), "expected": r["next"],
                          "variable": r["variable"]})
    items.sort(key=lambda x: (x["date"], x["amount"] < 0))
    bal, as_of = bank_balance(con)
    run = bal
    for x in items:
        if run is not None and x["sure"]:
            run = round(run + x["amount"], 2)
        x["balance"] = run if x["sure"] else None
    return {"items": items, "balance": bal, "balance_date": as_of, "until": until.isoformat()}


# ---------------------------------------------------------------- прогноз месяца

def month_forecast(con, rec: list[dict]) -> dict:
    today = dt.date.today()
    m0, mend = today.replace(day=1), month_end(today)
    K = categories.key_ids(con)
    rows = {r["id"]: r for r in con.execute("SELECT id, parent_id, name, kind FROM categories")}

    def group(cid):  # группа верхнего уровня; без категории — «⚠ без категории»
        seen = set()
        while cid in rows and rows[cid]["parent_id"] is not None and cid not in seen:
            seen.add(cid)
            cid = rows[cid]["parent_id"]
        return cid

    fixed_ids = {i for r in rec if counts(r) and r["flow"] == "out" for i in r["ids"]}
    start = add_months(m0, -BASE_MONTHS)
    spend = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0]))  # месяц -> группа -> [всё до дня d, после d — переменное, всё]
    first_purchase = con.execute("SELECT min(date) FROM purchases").fetchone()[0]
    for it in con.execute("""SELECT p.id, p.date, i.category_id cat, i.amount - coalesce(i.discount, 0) v
                             FROM items i JOIN purchases p ON p.id = i.purchase_id
                             WHERE coalesce(p.status, '') != 'doubt' AND p.date >= ? AND p.date <= ?""",
                          (start.isoformat(), mend.isoformat() + "T23:59:59")):
        if it["cat"] is not None and rows.get(it["cat"], {"kind": "expense"})["kind"] != "expense":
            continue
        d = day(it["date"])
        g = group(it["cat"]) if it["cat"] in rows else None
        cell = spend[d.strftime("%Y-%m")][g]
        if d.day <= today.day:
            cell[0] += it["v"]
        elif it["id"] not in fixed_ids:
            cell[1] += it["v"]
        cell[2] += it["v"]
    months = [add_months(m0, -k).strftime("%Y-%m") for k in range(BASE_MONTHS, 0, -1)]
    months = [m for m in months if first_purchase and m >= first_purchase[:7]]
    cur = today.strftime("%Y-%m")
    # регулярные платежи, которые ещё ждём в этом месяце (по группам)
    fixed_left = defaultdict(float)
    fixed_list = []
    for r in rec:
        if not counts(r) or r["flow"] != "out" or r["kind"] != "expense":
            continue
        for d in occurrences(r, mend):
            if d.strftime("%Y-%m") == cur and not (r["last"][:7] == cur and not r["late"] and d <= today):
                g = group(r["cat"]) if r["cat"] in rows else None
                fixed_left[g] += r["typical"]
                fixed_list.append({"date": d.isoformat(), "name": r["name"], "amount": r["typical"]})
    groups = set(fixed_left) | {g for m in months + [cur] for g in spend[m]}
    med = lambda xs: st.median(xs) if xs else 0.0  # noqa: E731
    table = []
    for g in groups:
        spent = spend[cur][g][0] if g in spend[cur] else 0.0
        later = med([spend[m][g][1] for m in months])
        typical = med([spend[m][g][2] for m in months])
        by_day = med([spend[m][g][0] for m in months])
        forecast = spent + fixed_left[g] + later
        if max(spent, forecast, typical) < 0.5:
            continue
        table.append({"group": g, "name": rows[g]["name"] if g in rows else "⚠ без категории",
                      "key": next((k for k, v in K.items() if v == g), None),
                      "spent": round(spent, 2), "by_day": round(by_day, 2), "fixed_left": round(fixed_left[g], 2),
                      "later": round(later, 2), "forecast": round(forecast, 2), "typical": round(typical, 2)})
    table.sort(key=lambda r: -max(r["forecast"], r["typical"]))
    totals = [sum(spend[m][g][2] for g in spend[m]) for m in months]
    total_by_day = [sum(spend[m][g][0] for g in spend[m]) for m in months]
    # поступления: уже пришли в этом месяце + ожидаемые регулярные
    got = con.execute("SELECT coalesce(sum(amount), 0) FROM bank_tx WHERE amount > 0 AND type NOT LIKE '%RETURN%' "
                      "AND date >= ? AND date <= ?", (m0.isoformat(), mend.isoformat())).fetchone()[0]
    inc_left = [{"date": d.isoformat(), "name": r["name"], "amount": r["typical"]}
                for r in rec if counts(r) and r["flow"] == "in"
                for d in occurrences(r, mend) if not (r["last"][:7] == cur and not r["late"] and d <= today)]
    return {"month": cur, "today": today.isoformat(), "day": today.day, "days": mend.day, "base_months": months,
            "spent": round(sum(r["spent"] for r in table), 2), "forecast": round(sum(r["forecast"] for r in table), 2),
            "typical": round(med(totals), 2), "typical_by_day": round(med(total_by_day), 2),
            "fixed_left": fixed_list, "income_got": round(got, 2), "income_left": inc_left,
            "income_expected": round(got + sum(x["amount"] for x in inc_left), 2), "groups": table}


def report() -> dict:
    con = connect()
    categories.seed(con)
    rec = recurring(con)
    return {"recurring": rec, "calendar": calendar(con, rec), "month": month_forecast(con, rec)}


def mark(key: str, state: str | None):
    """Подтвердить (confirmed) / отклонить (rejected) серию; None — снова решает программа.
    Ключ — «сторона|сумма»: старые отметки той же стороны с близкой суммой заменяются."""
    con = connect()
    side, _, amount = key.rpartition("|")
    for r in con.execute("SELECT key FROM recurring_marks").fetchall():
        s, _, a = r["key"].rpartition("|")
        try:
            if s == side and abs(float(a) - float(amount)) <= 0.3 * float(amount):
                con.execute("DELETE FROM recurring_marks WHERE key = ?", (r["key"],))
        except ValueError:
            pass
    if state in ("confirmed", "rejected"):
        con.execute("INSERT OR REPLACE INTO recurring_marks VALUES (?, ?, ?)",
                    (key, state, dt.datetime.now().isoformat(timespec="seconds")))
    else:
        con.execute("DELETE FROM recurring_marks WHERE key = ?", (key,))
    con.commit()
