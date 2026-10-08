"""Входы в Lidl Plus, Kaufland и банк: когда получен токен, сколько он в среднем живёт.

История — в meta `login_log:<источник>`: {"logins": [...], "expired": [...]} (местное время ISO). Срок жизни токена —
от входа до первого отказа сервиса после него; вход заново, пока старый токен ещё работал, в среднее не идёт.
Дата входа есть и в самих токенах (auth_time, срок согласия банка) — так видна и до начала учёта.
"""
import base64
import datetime as dt
import json

from core.db import connect, get_meta, set_meta

SOURCES = {"lidl": "Lidl Plus", "kaufland": "Kaufland", "bank": "банк"}


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def local(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def jwt_claims(token: str | None) -> dict:
    """Поля JWT без проверки подписи — нужна только дата входа (auth_time)."""
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (AttributeError, IndexError, ValueError):
        return {}


def _log(con, key: str) -> dict:
    return json.loads(get_meta(con, f"login_log:{key}") or '{"logins": [], "expired": []}')


def record(key: str, event: str = "login", at: str | None = None):
    """login — вход выполнен; expired — сервис отказал в токене (записываем один раз после каждого входа)."""
    con = connect()
    log = _log(con, key)
    if event == "expired" and any(e > max(log["logins"], default="") for e in log["expired"]):
        return
    field = "logins" if event == "login" else "expired"
    log[field] = sorted(log[field] + [at or now()])[-50:]
    set_meta(con, f"login_log:{key}", json.dumps(log))
    con.commit()
    con.close()


def alive(key: str):
    """Токен сработал: отказ после последнего входа был временным сбоем сервиса — убираем его."""
    con = connect()
    log = _log(con, key)
    last = max(log["logins"], default="")
    if any(e > last for e in log["expired"]):
        log["expired"] = [e for e in log["expired"] if e <= last]
        set_meta(con, f"login_log:{key}", json.dumps(log))
        con.commit()
    con.close()


def token_info(key: str) -> dict:
    """Что видно из файла токена: {"since": дата входа, "until": до какого срока действует}."""
    from bank import enablebanking
    from receipts import kaufland, lidl
    path = {"lidl": lidl.TOKEN, "kaufland": kaufland.TOKEN, "bank": enablebanking.SESSION}[key]
    try:
        t = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if key == "bank":  # session.json пишется только при входе
        until = t.get("valid_until")
        return {"since": t.get("created") or local(path.stat().st_mtime),
                "until": local(dt.datetime.fromisoformat(until).timestamp()) if until else None}
    auth = t.get("auth_time") or jwt_claims(t.get("access_token")).get("auth_time")
    return {"since": local(auth) if auth else None}


def _days(a: str, b: str) -> float:
    return (dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).total_seconds() / 86400


def state(con, key: str) -> dict:
    """{"since": последний вход, "until": срок (банк), "avg_days": средняя жизнь токена, "samples": по скольким,
    "dead": когда сервис отказал после последнего входа (None — токен работает)}."""
    log, info = _log(con, key), token_info(key)
    logins = sorted(log["logins"])
    since = info.get("since")
    if since and not any(abs(_days(since, x)) < 0.01 for x in logins):  # вход до начала учёта (или из терминала)
        logins = sorted(logins + [since])
    lives = []
    for i, x in enumerate(logins):
        nxt = logins[i + 1] if i + 1 < len(logins) else "9999"
        if end := next((e for e in sorted(log["expired"]) if x < e < nxt), None):
            lives.append(_days(x, end))
    last = logins[-1] if logins else None
    dead = [e for e in log["expired"] if e > (last or "")]
    return {"since": last, "until": info.get("until"), "samples": len(lives),
            "avg_days": round(sum(lives) / len(lives), 1) if lives else None, "dead": max(dead) if dead else None}
