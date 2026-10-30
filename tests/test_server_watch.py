"""Сервер следит за своим кодом: файл поменялся — выходит с кодом перезапуска («сторож» поднимет его заново),
иначе правка категории в интерфейсе размечала всё старым словарём."""
import time

from app import server


def test_restarts_when_code_changes(tmp_path, monkeypatch):
    (tmp_path / "core").mkdir()
    f = tmp_path / "core" / "m.py"
    f.write_text("x = 1\n")
    (tmp_path / "budget.py").write_text("")
    monkeypatch.setattr(server, "BUDGET", tmp_path)
    exits = []
    monkeypatch.setattr(server.os, "_exit", lambda code: exits.append(code) or time.sleep(60))
    monkeypatch.setattr(server, "login_running", lambda: False)
    server.watch_code(every=0.05)
    time.sleep(0.2)
    assert exits == []  # ничего не менялось
    f.write_text("x = 2\n")
    import os
    os.utime(f, (time.time() + 5, time.time() + 5))
    deadline = time.time() + 3
    while not exits and time.time() < deadline:
        time.sleep(0.05)
    assert exits == [server.upgrade.RESTART]
