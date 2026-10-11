"""Магазины: включённый — обновляется и есть в «Скидках»; пока не трогали — включён, если к нему есть вход."""
from core import db, stores, update


def test_defaults_follow_login_and_toggle_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    monkeypatch.setattr(stores, "_connected", lambda k: k != "biedronka")
    assert stores.enabled() == {"lidl": True, "kaufland": True, "biedronka": False}
    stores.set_enabled("kaufland", False)
    stores.set_enabled("biedronka", True)
    assert stores.enabled() == {"lidl": True, "kaufland": False, "biedronka": True}
    assert stores.deal_stores() == ["lidl", "biedronka"]  # Kaufland выключен


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


def test_step_switched_off_skips_update_all_but_not_manual(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    monkeypatch.setattr(stores, "_connected", lambda k: True)
    ran = []
    monkeypatch.setattr(update, "STEPS", [(k, k, lambda con, k=k: ran.append(k) or ("ok", ""))
                                          for k in ("biedronka", "biedronka_flyers", "reconcile")])
    monkeypatch.setattr(update, "TITLES", {k: k for k in ("biedronka", "biedronka_flyers", "reconcile")})
    monkeypatch.setattr(update, "online", lambda wait=0: True)
    monkeypatch.setattr(update, "LOCK", tmp_path / "update.lock")
    monkeypatch.setattr(update, "stay_awake", lambda on: None)
    update.set_skipped("biedronka_flyers", True)  # только чеки — без долгого распознавания газеток
    update.run("ui")
    assert ran == ["biedronka", "reconcile"]
    ran.clear()
    update.run("ui", only=["biedronka_flyers"])  # кнопка «обновить» в строке — работает
    assert ran == ["biedronka_flyers"]
