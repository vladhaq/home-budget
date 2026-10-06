"""Установка новой версии: заменяются только файлы программы, данные и настройки — нет."""
import io
import zipfile

import pytest

from core import upgrade


def archive(files: dict[str, str], version="9.9.9") -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("vladhaq-home-budget-abc123/budget.py", f'__version__ = "{version}"\n')
        for name, text in files.items():
            z.writestr("vladhaq-home-budget-abc123/" + name, text)
    return zipfile.ZipFile(buf)


def test_program_files_replaced_data_kept(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "budget.db").write_text("моя база")
    (tmp_path / "config.ini").write_text("мои настройки")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "x.py").write_text("old")
    (tmp_path / "core" / "same.py").write_text("same")
    z = archive({"core/x.py": "new", "core/same.py": "same", "core/added.py": "added", "config.ini": "чужие",
                 "config.ini.example": "пример", "data/budget.db": "чужая база", "../outside.py": "нет"})
    assert upgrade.apply_zip(z, "9.9.9", tmp_path) == 4  # budget.py, x.py, added.py, config.ini.example
    assert (tmp_path / "core" / "x.py").read_text() == "new" and (tmp_path / "core" / "added.py").exists()
    assert (tmp_path / "config.ini").read_text() == "мои настройки"
    assert (tmp_path / "data" / "budget.db").read_text() == "моя база"
    assert (tmp_path / "config.ini.example").exists() and not (tmp_path.parent / "outside.py").exists()


def test_wrong_version_archive_changes_nothing(tmp_path):
    (tmp_path / "budget.py").write_text('__version__ = "1.0.0"\n')
    with pytest.raises(RuntimeError):
        upgrade.apply_zip(archive({"core/x.py": "new"}, version="1.2.0"), "1.3.0", tmp_path)
    assert not (tmp_path / "core").exists() and "1.0.0" in (tmp_path / "budget.py").read_text()


def test_versions_compare_as_numbers():
    assert upgrade.vtuple("1.10.0") > upgrade.vtuple("1.9.3") and upgrade.vtuple("v1.2.1") == (1, 2, 1)


def test_zip_install_not_blocked(tmp_path):
    assert upgrade.blocker(tmp_path) is None  # распакованный ZIP без .git — обычная установка
