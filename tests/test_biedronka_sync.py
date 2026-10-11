"""Biedronka: первый прогон — вся история с сайта, дальше — с последнего чека минус 2 недели (как у Lidl);
скачанные, но не разобранные PDF подбираются в следующий раз."""
import datetime as dt
import json

import pytest

from core import db
from receipts import biedronka, photos


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    monkeypatch.setattr(biedronka, "COMPLETED", tmp_path / "done.json")
    monkeypatch.setattr(biedronka, "DOWNLOADS", tmp_path / "pdf")
    (tmp_path / "pdf").mkdir()
    monkeypatch.setattr(biedronka, "_load_session", lambda: object())
    monkeypatch.setattr(biedronka, "_save_session", lambda s: None)
    monkeypatch.setattr(biedronka, "download_receipts", lambda s, r: [])
    asked = []
    monkeypatch.setattr(biedronka, "list_receipts", lambda s, a, b: asked.append(a) or [])
    imported = []
    monkeypatch.setattr(photos, "import_file", lambda con, path, hint_date=None, source_ref=None:
                        imported.append(source_ref) or {"total": 1})
    return tmp_path, asked, imported


def test_first_run_takes_everything_then_only_tail(env):
    tmp, asked, _ = env
    biedronka.sync()
    assert asked == [biedronka.HISTORY_START]  # первый раз — с начала истории сайта
    (tmp / "done.json").write_text(json.dumps({"1234567890": {"filename": "a.pdf", "date": "2026-10-05",
                                                             "electronic": False, "json_tried": True}}))
    biedronka.sync()
    assert asked[-1] == dt.date(2026, 9, 21)  # потом — с последнего чека минус 14 дней


def test_receipts_from_before_json_support_relisted_once(env):
    tmp, asked, _ = env
    (tmp / "done.json").write_text(json.dumps({"1234567890": {"filename": "a.pdf", "date": "2026-10-05"}}))
    biedronka.sync()
    assert asked == [biedronka.HISTORY_START]  # старый журнал без признака e-paragon — весь список ещё раз, ради JSON


def test_downloaded_but_not_imported_pdf_is_picked_up(env):
    tmp, _, imported = env
    (tmp / "pdf" / "a.pdf").write_bytes(b"%PDF-test")
    (tmp / "done.json").write_text(json.dumps({"1234567890": {"filename": "a.pdf", "date": "2026-10-05"}}))
    r = biedronka.sync()
    assert imported == ["biedronka:1234567890"] and r["imported"] == 1
