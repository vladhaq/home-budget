"""Дата входа и средний срок жизни токена (страница «Настройки»)."""
import pytest

from core import db, logins


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    monkeypatch.setattr(logins, "token_info", lambda key: {})
    return db.connect()


def test_lifetime_from_login_to_refusal(con):
    logins.record("lidl", at="2026-01-01T10:00:00")
    logins.record("lidl", "expired", at="2026-01-31T10:00:00")
    logins.record("lidl", "expired", at="2026-02-01T10:00:00")  # повторный отказ до нового входа — не в счёт
    logins.record("lidl", at="2026-02-02T10:00:00")
    logins.record("lidl", "expired", at="2026-02-12T10:00:00")
    logins.record("lidl", at="2026-02-13T10:00:00")
    s = logins.state(con, "lidl")
    assert s["since"] == "2026-02-13T10:00:00" and s["samples"] == 2 and s["avg_days"] == 20.0 and s["dead"] is None


def test_relogin_before_refusal_not_counted(con):
    logins.record("kaufland", at="2026-01-01T10:00:00")
    logins.record("kaufland", at="2026-01-05T10:00:00")  # вошёл заново, пока старый токен работал
    logins.record("kaufland", "expired", at="2026-01-15T10:00:00")
    s = logins.state(con, "kaufland")
    assert s["samples"] == 1 and s["avg_days"] == 10.0 and s["dead"] == "2026-01-15T10:00:00"


def test_false_alarm_removed_when_token_works(con):
    logins.record("bank", at="2026-01-01T10:00:00")
    logins.record("bank", "expired", at="2026-01-03T10:00:00")
    logins.alive("bank")  # токен снова сработал — отказ был сбоем сервиса
    s = logins.state(con, "bank")
    assert s["dead"] is None and s["samples"] == 0


def test_login_known_only_from_token(con, monkeypatch):
    monkeypatch.setattr(logins, "token_info", lambda key: {"since": "2026-01-01T10:00:00"})
    logins.record("lidl", "expired", at="2026-01-08T10:00:00")
    logins.record("lidl", at="2026-01-01T10:05:00")  # тот же вход, записанный с разницей в минуты, — не второй вход
    s = logins.state(con, "lidl")
    assert s["since"] == "2026-01-01T10:05:00" and s["samples"] == 1


def test_jwt_claims():
    import base64
    import json
    body = base64.urlsafe_b64encode(json.dumps({"auth_time": 1700000000}).encode()).rstrip(b"=").decode()
    assert logins.jwt_claims(f"x.{body}.y") == {"auth_time": 1700000000}
    assert logins.jwt_claims("opaque") == {} and logins.jwt_claims(None) == {}
