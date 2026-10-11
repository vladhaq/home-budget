"""Какие магазины учитываются: переключатели в «Настройках» → «Магазины».

Выключенный магазин не обновляется («Обновить всё», автозапуск), его нет в списке источников и на странице «Скидки»
(газетки, напоминания, «Где выгоднее» — только если включены оба магазина с газетками). Уже собранные чеки остаются
в бюджете. Пока переключатель не трогали, магазин включён, если к нему есть вход (токен или сессия).
"""
import json

from core.db import connect, get_meta, set_meta

# key -> (название, что даёт, есть газетки)
STORES = {
    "lidl": ("Lidl", "чеки из приложения Lidl Plus, газетки и скидки", True),
    "kaufland": ("Kaufland", "чеки из приложения Kaufland Card, газетки и скидки", True),
    "biedronka": ("Biedronka", "чеки с сайта Moja Biedronka и газетки (распознаются с картинок — цены могут быть неточными) — beta", True),
}


def _connected(key: str) -> bool:
    from receipts import biedronka, kaufland, lidl
    return {"lidl": lidl.TOKEN, "kaufland": kaufland.TOKEN, "biedronka": biedronka.SESSION}[key].exists()


def enabled(con=None) -> dict[str, bool]:
    con = con or connect()
    saved = json.loads(get_meta(con, "stores_enabled") or "{}")
    return {k: saved[k] if k in saved else _connected(k) for k in STORES}


def is_on(key: str, con=None) -> bool:
    return key not in STORES or enabled(con)[key]


def set_enabled(key: str, on: bool):
    if key not in STORES:
        raise ValueError(f"неизвестный магазин: {key}")
    con = connect()
    saved = json.loads(get_meta(con, "stores_enabled") or "{}")
    saved[key] = bool(on)
    set_meta(con, "stores_enabled", json.dumps(saved))
    con.commit()


def deal_stores(con=None) -> list[str]:
    """Включённые магазины с газетками (страница «Скидки», напоминания)."""
    on = enabled(con)
    return [k for k, (_, _, flyers) in STORES.items() if flyers and on[k]]


def listing(con=None) -> list[dict]:
    on = enabled(con)
    return [{"key": k, "title": t, "about": about, "flyers": fl, "on": on[k], "connected": _connected(k)}
            for k, (t, about, fl) in STORES.items()]
