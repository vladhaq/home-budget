"""Локальный веб-интерфейс бюджета: python budget.py serve -> http://127.0.0.1:8765"""
import datetime as dt
import json
import re
import threading
import webbrowser

from flask import Flask, jsonify, request, send_from_directory

from core import categories, update
from core.common import BUDGET, DATA, fold
from core.db import PAYMENT_METHODS, connect, get_meta

HOST, PORT = "127.0.0.1", 8765  # только локально, наружу не открываем
app = Flask(__name__, static_folder=None)
STATIC = BUDGET / "app" / "static"


ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"localhost:{PORT}"}


@app.before_request
def local_only():
    """Только этот компьютер. Чужой сайт в браузере не прочитает данные через подмену DNS (проверка Host)
    и не отправит команду формой: POST принимается только как JSON — такой запрос с чужого сайта браузер не пустит."""
    if request.host not in ALLOWED_HOSTS:
        return "нет доступа", 403
    if request.method == "POST" and not request.is_json:
        return jsonify(ok=False, error="ожидается JSON"), 415


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html", max_age=0)


@app.get("/static/<path:rel>")
def static_file(rel):
    """Стили и скрипты интерфейса; max_age=0 — после обновления кода браузер не держит старую версию."""
    return send_from_directory(STATIC, rel, max_age=0)


@app.get("/file/<path:rel>")
def file(rel):
    """Исходные фото чеков — только из data/photos."""
    target = (DATA / rel).resolve()
    if not any(target.is_relative_to((DATA / d).resolve()) for d in ("photos", "mail/raw")):
        return "нет доступа", 403
    if rel.startswith("mail/raw/") and rel.endswith(".eml"):
        # письмо показываем текстом: без картинок и скриптов, чтобы не грузились трекеры отправителя
        from receipts import mail_parse
        msg = mail_parse.load(rel)
        body = mail_parse.text(msg).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        head = f"{msg['From']} · {msg['Date']}\n{msg['Subject']}".replace("<", "&lt;").replace(">", "&gt;")
        return (f"<!doctype html><meta charset='utf-8'><title>Письмо</title>"
                f"<pre style='white-space:pre-wrap;font:14px/1.5 system-ui;max-width:800px;margin:20px auto'>"
                f"<b>{head}</b>\n\n{body}</pre>")
    if not rel.startswith("photos/"):
        return "нет доступа", 403
    return send_from_directory(DATA, rel)


@app.get("/api/data")
def data():
    con = connect()  # только чтение: дерево категорий создаётся при запуске сервера и при пересчёте категорий
    cats = [dict(r) for r in con.execute("SELECT id, parent_id, name, kind, key FROM categories ORDER BY id")]
    cat_paths = categories.paths(con)
    for c in cats:
        c["path"] = cat_paths[c["id"]]
    items = {}
    notes = {(r["purchase_id"], r["line"]): r["note"] for r in con.execute("SELECT purchase_id, line, note FROM item_notes")}
    photos = {}
    for r in con.execute("SELECT purchase_id, path, date, total FROM attachments ORDER BY added"):
        photos.setdefault(r["purchase_id"], []).append({"path": r["path"], "date": r["date"], "total": r["total"]})
    for r in con.execute("""SELECT purchase_id, line, name, product_code, qty, unit_price, amount, discount,
                                   category_id, category_source FROM items ORDER BY purchase_id, line"""):
        items.setdefault(r["purchase_id"], []).append(
            {"line": r["line"], "name": r["name"], "code": r["product_code"], "qty": r["qty"],
             "unit_price": r["unit_price"], "amount": r["amount"], "discount": r["discount"],
             "cat": r["category_id"], "cat_src": r["category_source"], "note": notes.get((r["purchase_id"], r["line"]))})
    # операция в выписке, к которой привязана покупка: тип и описание — чтобы опознать трату без чека
    bank_info = {r["id"]: {"type": r["type"], "desc": r["description"] or "", "who": r["counterparty"] or "",
                           "date": r["date"], "amount": r["amount"]}
                 for r in con.execute("SELECT id, date, amount, type, counterparty, description FROM bank_tx")}
    purchases = [{
        "id": p["id"], "source": p["source"], "date": p["date"], "merchant": p["merchant"] or "?",
        "store": p["store"] or "", "total": p["total"] or 0, "discount": p["discount"] or 0,
        "pay": p["payment_method"], "card": p["card_last4"], "items": items.get(p["id"], []),
        "note": p["note"], "file": p["raw_path"] if p["source"] in ("photo", "email") else None,
        "bt": p["bank_tx_id"] is not None, "bank": bank_info.get(p["bank_tx_id"]), "photos": photos.get(p["id"], []),
        "status": p["status"], "refund_of": p["refund_of"], "orig_total": p["orig_total"],
    } for p in con.execute("SELECT * FROM purchases WHERE date <> '' ORDER BY date")]
    return jsonify({
        "purchases": purchases, "categories": cats,
        "payments": {k or "unknown": v for k, v in PAYMENT_METHODS.items()},
        "meta": {"bank_last_date": get_meta(con, "bank_last_date"),
                 "last_purchase": purchases[-1]["date"] if purchases else None,
                 "update_last": json.loads(get_meta(con, "update_last") or "null"),
                 "update_running": update.is_running(), "bank_days_left": update.consent_days_left(),
                 "deals": _deals_summary()},
        "incomes": _incomes(con),
        "hidden": [dict(r) for r in con.execute("SELECT id, source, label, created FROM hidden_purchases ORDER BY created DESC")],
        "rules": [dict(r) | {"path": cat_paths.get(r["category_id"])} for r in con.execute(
            "SELECT id, target, pattern, product_code, category_id, source, created FROM rules ORDER BY id DESC")],
    })


@app.get("/api/bank")
def bank():
    """Баланс по дням, доходы по месяцам и категориям, покрытие трат чеками, наличные."""
    con = connect()
    cat_paths = categories.paths(con)
    kinds = {r["id"]: r["kind"] for r in con.execute("SELECT id, kind FROM categories")}
    K = categories.key_ids(con)
    tops = {cid: top for cid, top in _top_groups(con).items()}
    balance = {}
    # остаток на конец дня: внутри дня порядок — по номеру операции в банке (id «счёт:O;382»)
    for r in con.execute("SELECT date, balance FROM bank_tx WHERE balance IS NOT NULL "
                         "ORDER BY date, CAST(substr(id, instr(id, ';') + 1) AS INTEGER)"):
        balance[r["date"]] = r["balance"]
    income = {}
    for r in con.execute("SELECT date, amount, category_id, counterparty, description FROM bank_tx "
                         "WHERE amount > 0 AND type NOT LIKE '%RETURN%'"):
        m = r["date"][:7]
        path = cat_paths.get(r["category_id"], "Прочие поступления")
        income.setdefault(m, {}).setdefault(path, 0)
        income[m][path] = round(income[m][path] + r["amount"], 2)
    months = {}
    for r in con.execute("""SELECT substr(p.date, 1, 7) m, p.source, p.payment_method pay, p.bank_tx_id bt,
                                   sum(i.amount - coalesce(i.discount, 0)) v, i.category_id cid
                            FROM purchases p JOIN items i ON i.purchase_id = p.id
                            WHERE coalesce(p.status, '') != 'doubt' GROUP BY p.id, i.line"""):
        if kinds.get(r["cid"]) in ("transfer", "income"):
            if r["cid"] is not None and r["cid"] == K.get("transfer.atm"):
                months.setdefault(r["m"], {}).setdefault("atm", 0)
                months[r["m"]]["atm"] += r["v"]
            continue
        s = months.setdefault(r["m"], {})
        # receipts — чек есть и найден в выписке PKO; other — чек есть, но оплачен не со счёта PKO (другая карта)
        key = "bank_only" if r["source"] == "bank" else "cash" if r["pay"] == "cash" else "receipts" if r["bt"] else "other"
        s[key] = round(s.get(key, 0) + r["v"], 2)
        # «покупки»: без аренды, учёбы, налогов и комиссий — на них чеков не бывает в принципе
        no_receipts = {K.get(k) for k in ("housing", "education", "finance")} - {None}
        if tops.get(r["cid"]) not in no_receipts and key in ("receipts", "bank_only"):
            s["shop_" + key] = round(s.get("shop_" + key, 0) + r["v"], 2)
    session = {}
    try:
        import json as _json
        from bank.enablebanking import SESSION
        session = _json.loads(SESSION.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    incomes = _incomes(con)
    return jsonify({"balance": balance, "income": income, "months": months, "incomes": incomes,
                    "valid_until": session.get("valid_until"), "last": get_meta(con, "bank_last_date")})


def _incomes(con) -> list[dict]:
    """Поступления на счёт (доходы и входящие переводы; возвраты на карту — не здесь, они у своих покупок).
    Доход наличными — это взносы на счёт (они здесь, в выписке); записи кошелька только меняют остаток на руках,
    иначе одни и те же деньги посчитались бы дважды."""
    return [{"id": r["id"], "date": r["date"], "amount": r["amount"], "type": r["type"] or "",
             "who": r["counterparty"] or ("Взнос наличных" if (r["type"] or "").startswith("CASH-IN") else ""),
             "desc": r["description"] or "", "cat": r["category_id"], "src": r["category_source"]}
            for r in con.execute("SELECT * FROM bank_tx WHERE amount > 0 AND type NOT LIKE '%RETURN%' ORDER BY date DESC")]


def _top_groups(con) -> dict:
    """id категории -> id её группы верхнего уровня"""
    parent = {r["id"]: r["parent_id"] for r in con.execute("SELECT id, parent_id FROM categories")}
    out = {}
    for cid in parent:
        c, seen = cid, set()
        while parent.get(c) is not None and c not in seen:
            seen.add(c)
            c = parent[c]
        out[cid] = c
    return out


@app.get("/api/wallet")
def wallet_get():
    from core import wallet
    from core.analytics import bank_balance
    con = wallet.db()
    bal, as_of = bank_balance(con)  # остаток на счёте PKO — для карточки «всего: наличные + карта»
    return jsonify(wallet.timeline(con) | {"kinds": wallet.KINDS, "bank": {"balance": bal, "date": as_of}})


@app.post("/api/wallet/add")
def wallet_add():
    from core import wallet
    b = request.get_json()
    con = wallet.db()
    try:
        wallet.add(con, b["date"], b["kind"], float(str(b["amount"]).replace(",", ".")), b.get("note"))
    except (ValueError, KeyError) as e:
        return jsonify(ok=False, error=str(e)), 400
    wallet.rebuild(con)
    categories.categorize(con)
    return jsonify(ok=True)


@app.post("/api/wallet/time")
def wallet_time():
    """Уточнить время взноса/снятия из выписки (банк даёт только дату)."""
    from core import wallet
    b = request.get_json()
    con = wallet.db()
    wallet.set_time(con, b["tx"], b.get("at") or None)
    wallet.rebuild(con)
    categories.categorize(con)
    return jsonify(ok=True)


@app.post("/api/wallet/delete")
def wallet_delete():
    from core import wallet
    con = wallet.db()
    wallet.remove(con, int(request.get_json()["id"]))
    wallet.rebuild(con)
    categories.categorize(con)
    return jsonify(ok=True)


@app.post("/api/set-categories")
def set_categories():
    """Пакет правок из интерфейса (кнопка «Сохранить»): [{mode, name | purchase_id+line, category_id}]"""
    changes = request.get_json().get("changes", [])
    for ch in changes:
        if ch["mode"] == "tx":  # поступление в выписке (доход / перевод): правка конкретной операции
            c = connect()
            c.execute("UPDATE bank_tx SET category_id = ?, category_source = 'manual' WHERE id = ?",
                      (ch["category_id"], ch["tx_id"]))
            c.commit()
        else:
            _apply_category(connect(), ch)
    con = connect()
    categories.categorize(con)
    return jsonify(ok=True, changed=len(changes))


@app.post("/api/category/<action>")
def category_action(action):
    b = request.get_json()
    con = connect()
    try:
        if action == "rename":
            categories.rename(con, int(b["id"]), b["name"])
        elif action == "move":
            categories.move(con, int(b["id"]), int(b["parent_id"]) if b.get("parent_id") not in (None, "") else None)
        elif action == "delete":
            categories.delete(con, int(b["id"]))
        else:
            return jsonify(ok=False, error="неизвестное действие"), 400
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    categories.categorize(con)
    return jsonify(ok=True)


def _apply_category(con, body: dict) -> int:
    """mode=item — только эта позиция (ручная правка);
    mode=same — все позиции с таким названием + правило на будущее (по коду товара, иначе по названию)."""
    cat = body.get("category_id")
    if body["mode"] == "item":
        con.execute("UPDATE items SET category_id = ?, category_source = 'manual' WHERE purchase_id = ? AND line = ?",
                    (cat, body["purchase_id"], body["line"]))
        con.commit()
        return 1
    pid = body.get("purchase_id") or ""
    if pid.startswith("bank:"):  # операция банка: «все такие» = все покупки в этом магазине / переводы этому человеку
        m = con.execute("SELECT merchant FROM purchases WHERE id = ?", (pid,)).fetchone()
        key = categories.add_shop_rule(con, m["merchant"], cat) if m else None
        if key is None:  # магазина нет (BLIK без названия) — только эта операция
            return _apply_category(con, body | {"mode": "item"})
        changed = 0
        for r in con.execute("SELECT i.purchase_id, i.line, p.merchant FROM items i JOIN purchases p ON p.id = i.purchase_id "
                             "WHERE i.category_source = 'manual' AND p.source = 'bank'").fetchall():
            if categories.shop_key(r["merchant"]) == key:  # ручные правки этого магазина заменяет правило
                changed += con.execute("UPDATE items SET category_source = NULL WHERE purchase_id = ? AND line = ?",
                                       (r["purchase_id"], r["line"])).rowcount
        con.commit()
        return changed
    name = body["name"]
    rows = con.execute("SELECT DISTINCT product_code FROM items WHERE lower(name) = lower(?)", (name,)).fetchall()
    codes = [r["product_code"] for r in rows if r["product_code"]]
    # старые правила для этих товаров больше не нужны — новое их заменяет
    for code in codes:
        con.execute("DELETE FROM rules WHERE target = 'item' AND product_code = ?", (code,))
    pattern = "^" + re.escape(fold(name).strip()) + "$"
    con.execute("DELETE FROM rules WHERE target = 'item' AND pattern = ?", (pattern,))
    if cat is not None:
        path = categories.paths(con)[cat]
        for code in codes:
            categories.add_rule(con, path, product_code=code)
        # позиции без кода (фото, почта, банк) ловятся по точному названию
        categories.add_rule(con, path, pattern=pattern)
    changed = con.execute("UPDATE items SET category_source = NULL WHERE lower(name) = lower(?) "
                          "AND category_source = 'manual'", (name,)).rowcount
    con.commit()
    return changed


@app.post("/api/set-category")
def set_category():
    con = connect()
    changed = _apply_category(con, request.get_json())
    categories.categorize(con)
    return jsonify(ok=True, changed=changed)


@app.post("/api/add-category")
def add_category():
    body = request.get_json()
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify(ok=False, error="пустое название"), 400
    con = connect()
    parent = body.get("parent_id")
    kind = con.execute("SELECT kind FROM categories WHERE id = ?", (parent,)).fetchone()["kind"] if parent else "expense"
    cur = con.execute("INSERT INTO categories (parent_id, name, kind) VALUES (?, ?, ?)", (parent, name, kind))
    con.commit()
    return jsonify(ok=True, id=cur.lastrowid)


@app.post("/api/delete-rule")
def delete_rule():
    con = connect()
    rule = con.execute("SELECT target FROM rules WHERE id = ?", (request.get_json()["id"],)).fetchone()
    con.execute("DELETE FROM rules WHERE id = ?", (request.get_json()["id"],))
    con.commit()
    if rule and rule["target"] == "merchant":  # категории операций банка и поступлений — заново по выписке
        from core import reconcile
        reconcile.match(verbose=False)
    categories.categorize(con)
    return jsonify(ok=True)


def _deals_summary():
    try:
        from core import deals
        return deals.summary()
    except Exception:  # noqa: BLE001 — нет модуля скидок или газеток: раздел просто без подсказки
        return None


# ---------------------------------------------------------------- скидки Lidl (газетки)

def _store():
    s = (request.args.get("store") or (request.get_json(silent=True) or {}).get("store") or "lidl").lower()
    return s if s in ("lidl", "kaufland") else "lidl"


@app.get("/api/deals")
def deals_state():
    from core import deals
    L, store = deals.mod(), _store()
    idx = deals.index(store)
    positions = [{"q": q, "deals": deals.search(q, idx, store)} for q in deals.src(store).watchlist()]
    flyers = [{"name": f["name"], "start": f["start"], "end": f["end"], "slug": s}
              for s, f in (idx or {}).get("flyers", {}).items()]
    return jsonify({"updated": (idx or {}).get("updated"), "flyers": sorted(flyers, key=lambda f: f["start"]), "store": store,
                    "positions": positions, "telegram": deals.telegram_state(), "sent": len(L.load_json(L.SENT, []))})


@app.get("/api/deals/compare")
def deals_compare():
    from core import deals
    return jsonify(deals.compare())


@app.get("/api/deals/search")
def deals_search():
    from core import deals
    q = request.args.get("q", "").strip()
    return jsonify({"q": q, "deals": deals.search(q, store=_store()) if q else []})


@app.post("/api/deals/update")
def deals_update():
    from core import deals
    try:
        idx = deals.src(_store()).update(verbose=False)
    except Exception as e:  # noqa: BLE001 — сайт магазина недоступен
        return jsonify(ok=False, error=f"газетки не скачались: {e}"), 502
    return jsonify(ok=True, flyers=len(idx["flyers"]))


@app.post("/api/deals/watch")
def deals_watch():
    from core import deals
    L, b = deals.mod(), request.get_json()
    q = (b.get("q") or "").strip()
    if not q:
        return jsonify(ok=False, error="пустой запрос"), 400
    if b.get("action") == "remove":
        deals.src(_store()).remove_position(q)
    else:
        deals.src(_store()).add_position(q)
    return jsonify(ok=True, positions=deals.src(_store()).watchlist())


@app.post("/api/deals/remind")
def deals_remind():
    from core import deals
    b = request.get_json()
    try:
        made = deals.remind(b["q"], b.get("keys") or [], _store())
    except RuntimeError as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, made=made)


@app.post("/api/deals/settings")
def deals_settings():
    from core import deals
    try:
        deals.set_remind_at(request.get_json().get("remind_at", ""))
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, telegram=deals.telegram_state())


@app.get("/api/deals/history")
def deals_history():
    from core import deals
    q = request.args.get("q", "").strip()
    from core import prices
    store = _store()
    return jsonify({"q": q, "items": deals.history(q, store=store) if q else [],
                    "receipts": prices.history(q, store) if q else []})


@app.get("/api/deals/inflation")
def deals_inflation():
    from core import deals
    return jsonify(deals.inflation(_store()))


# ---------------------------------------------------------------- аналитика

@app.get("/api/analytics")
def analytics_report():
    from core import analytics
    return jsonify(analytics.report())


@app.post("/api/analytics/mark")
def analytics_mark():
    from core import analytics
    b = request.get_json()
    analytics.mark(b["key"], b.get("state"))
    return jsonify(ok=True)


# ---------------------------------------------------------------- удалённые покупки и комментарии к позициям

@app.post("/api/purchase/hide")
def purchase_hide():
    """Удалить покупку (незавершённая оплата, дубль). Запоминается навсегда: пересборка её не вернёт."""
    pid = request.get_json()["id"]
    con = connect()
    p = con.execute("SELECT * FROM purchases WHERE id = ?", (pid,)).fetchone()
    if not p:
        return jsonify(ok=False, error="покупка не найдена"), 404
    if p["source"] == "bank":
        return jsonify(ok=False, error="операции из выписки не удаляются — это реальные списания со счёта"), 400
    total = f"{p['total'] or 0:.2f}".replace(".", ",")
    label = f"{(p['date'] or '')[:16].replace('T', ' ')} · {p['merchant'] or '?'} · {total} zł"
    con.execute("INSERT OR REPLACE INTO hidden_purchases VALUES (?, ?, ?, ?, ?)",
                (pid, p["source"], label, p["raw_path"], dt.datetime.now().isoformat(timespec="seconds")))
    for table, col in (("items", "purchase_id"), ("payments", "purchase_id"), ("purchases", "id")):
        con.execute(f"DELETE FROM {table} WHERE {col} = ?", (pid,))
    con.commit()
    if p["bank_tx_id"]:  # была привязана к операции банка — пересверяем, операция станет тратой «без чека»
        from core import reconcile
        reconcile.match(verbose=False)
    return jsonify(ok=True, reconciled=bool(p["bank_tx_id"]))


@app.post("/api/purchase/confirm")
def purchase_confirm():
    """Платёж «под вопросом» на самом деле прошёл (другой картой): считать. undo=True — снова под вопрос."""
    b = request.get_json()
    con = connect()
    if b.get("undo"):
        con.execute("DELETE FROM confirmed_purchases WHERE id = ?", (b["id"],))
        con.execute("UPDATE purchases SET status = 'doubt' WHERE id = ?", (b["id"],))
    else:
        con.execute("INSERT OR REPLACE INTO confirmed_purchases VALUES (?, ?)",
                    (b["id"], dt.datetime.now().isoformat(timespec="seconds")))
        con.execute("UPDATE purchases SET status = NULL WHERE id = ?", (b["id"],))
    con.commit()
    return jsonify(ok=True)


@app.post("/api/purchase/restore")
def purchase_restore():
    """Вернуть удалённую покупку: пересобрать её источник и пересверить с банком."""
    pid = request.get_json()["id"]
    con = connect()
    h = con.execute("SELECT * FROM hidden_purchases WHERE id = ?", (pid,)).fetchone()
    if not h:
        return jsonify(ok=False, error="такой удалённой покупки нет"), 404
    con.execute("DELETE FROM hidden_purchases WHERE id = ?", (pid,))
    con.commit()
    if h["source"] == "photo" and h["raw_path"] and (DATA / h["raw_path"]).exists():
        from receipts import photos
        photos.import_file(con, DATA / h["raw_path"])
    from core import reconcile
    reconcile.reconcile(verbose=False)  # Lidl, Kaufland и письма пересобираются из исходников здесь же
    return jsonify(ok=True)


@app.post("/api/item-note")
def item_note():
    """Комментарий к позиции («что купил»). Пустой — удалить. По комментарию сразу пересчитывается категория."""
    b = request.get_json()
    con = connect()
    note = (b.get("note") or "").strip()[:300]
    if note:
        row = con.execute("SELECT name FROM items WHERE purchase_id = ? AND line = ?", (b["purchase_id"], b["line"])).fetchone()
        con.execute("INSERT OR REPLACE INTO item_notes VALUES (?, ?, ?, ?, ?)",
                    (b["purchase_id"], b["line"], row["name"] if row else None, note, dt.datetime.now().isoformat(timespec="seconds")))
    else:
        con.execute("DELETE FROM item_notes WHERE purchase_id = ? AND line = ?", (b["purchase_id"], b["line"]))
    con.commit()
    categories.categorize(con)
    it = con.execute("SELECT category_id, category_source FROM items WHERE purchase_id = ? AND line = ?",
                     (b["purchase_id"], b["line"])).fetchone()
    path = categories.paths(con).get(it["category_id"]) if it and it["category_id"] else None
    return jsonify(ok=True, category=path, by_note=bool(it and it["category_source"] == "note"))


# ---------------------------------------------------------------- обновление и автозапуск (страница «Настройки»)

@app.get("/api/update")
def update_state():
    con = update.db()
    running = update.is_running()
    if not running:
        update.mark_dead(con)
    return jsonify({"running": running, "runs": update.runs(con, 30), "sources": update.sources(con)})


@app.get("/api/update/schedule")
def update_schedule():
    return jsonify(update.schedule_status())


@app.post("/api/update/run")
def update_run():
    """Запуск в отдельном процессе: интерфейс не ждёт, а следит за журналом (/api/update)."""
    import subprocess
    import sys

    if update.is_running():
        return jsonify(ok=False, error="обновление уже идёт"), 409
    steps = [s for s in (request.get_json(silent=True) or {}).get("steps", []) if s in update.TITLES]
    subprocess.Popen([sys.executable, str(BUDGET / "budget.py"), "update", *steps, "--trigger", "ui"], cwd=BUDGET,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return jsonify(ok=True)


@app.post("/api/update/schedule")
def update_schedule_set():
    b = request.get_json()
    try:
        s = update.schedule_on(b.get("time") or "07:30") if b.get("on") else update.schedule_off()
    except (RuntimeError, SystemExit) as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, schedule=s)


def serve(open_browser=True):
    categories.seed(connect())  # схема базы и дерево категорий — до первого запроса
    url = f"http://{HOST}:{PORT}"
    print(f"Бюджет: {url}  (Ctrl+C — остановить)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=PORT, debug=False)
