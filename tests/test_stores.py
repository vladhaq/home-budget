"""Магазины: включённый — обновляется и есть в «Скидках»; пока не трогали — включён, если к нему есть вход."""
from core import db, stores, update


def test_defaults_follow_login_and_toggle_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    monkeypatch.setattr(stores, "_connected", lambda k: k != "biedronka")
    assert stores.enabled() == {"lidl": True, "kaufland": True, "biedronka": False}
    stores.set_enabled("kaufland", False)
    stores.set_enabled("biedronka", True)
    assert stores.enabled() == {"lidl": True, "kaufland": False, "biedronka": True}
    assert stores.deal_stores() == ["lidl"]  # у Biedronka газеток нет


def test_disabled_store_not_updated_nor_listed(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    monkeypatch.setattr(stores, "_connected", lambda k: True)
    stores.set_enabled("lidl", False)
    ran = []
    monkeypatch.setattr(update, "STEPS", [(k, k, lambda con, k=k: ran.append(k) or ("ok", "")) for k in ("lidl", "kaufland", "reconcile")])
    monkeypatch.setattr(update, "online", lambda wait=0: True)
    monkeypatch.setattr(update, "LOCK", tmp_path / "update.lock")
    monkeypatch.setattr(update, "stay_awake", lambda on: None)
    update.run("ui")
    assert ran == ["kaufland", "reconcile"]
