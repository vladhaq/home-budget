import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Ни один тест не трогает настоящие данные: база, журнал чеков Biedronka, их PDF и сессия — во временной папке.
    Тест, которому нужно своё, переопределяет поверх. (Раньше тест разбирал настоящие PDF Biedronka и мешал
    идущей загрузке — «файл занят».)"""
    from core import db
    from receipts import biedronka
    monkeypatch.setattr(db, "DB", tmp_path / "isolated.db")
    monkeypatch.setattr(biedronka, "COMPLETED", tmp_path / "biedronka-done.json")
    monkeypatch.setattr(biedronka, "DOWNLOADS", tmp_path / "biedronka-pdf")
    monkeypatch.setattr(biedronka, "SESSION", tmp_path / "biedronka-session.json")
