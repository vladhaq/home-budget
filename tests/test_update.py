"""Журнал обновлений: прерванный запуск понятно подписан (замер во сне или Windows перезагрузилась); плановый
запуск, который уже прошёл, при входе в Windows не повторяется, а прерванный — повторяется; выход из почты после
работы — не ошибка."""
import datetime as dt
import imaplib

from core import db, update


def test_run_killed_before_first_step(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    con = db.connect()
    con.execute("INSERT INTO update_runs (started, trigger, steps) VALUES ('2026-10-08T07:35:13', 'schedule', 'lidl')")
    con.execute("INSERT INTO update_runs (started, trigger, steps) VALUES ('2026-10-08T08:00:00', 'ui', 'lidl')")
    con.execute("INSERT INTO update_steps (run_id, step, started, status) VALUES (2, 'lidl', '2026-10-08T08:00:01', 'running')")
    update.mark_dead(con, booted=dt.datetime(2026, 10, 8, 6, 0))  # Windows загрузилась раньше — процессы просто умерли
    summary = dict(con.execute("SELECT id, summary FROM update_runs").fetchall())
    assert summary[1].startswith("прервано до первого шага") and summary[2].startswith("прервано: процесс")


def test_run_killed_by_windows_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    con = db.connect()
    con.execute("INSERT INTO update_runs (started, trigger, steps) VALUES ('2030-03-02T06:10:00', 'schedule', 'lidl')")
    update.mark_dead(con, booted=dt.datetime(2030, 3, 2, 11, 20))  # упала во сне, включили днём
    assert con.execute("SELECT summary FROM update_runs").fetchone()[0] == \
        "прервано: Windows выключилась или перезагрузилась во время обновления (снова включена 02.03 11:20)"


def test_logon_trigger_repeats_only_interrupted_scheduled_run(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    con = db.connect()
    assert update.caught_up(con, dt.datetime(2030, 3, 2, 11, 22)) is None  # задача старой версии — без проверки
    db.set_meta(con, "schedule_at", "07:30")
    con.execute("INSERT INTO update_runs (started, finished, trigger, steps, summary) VALUES "
                "('2030-03-01T07:30:02', '2030-03-01T07:31:14', 'schedule', 'lidl', 'без ошибок'), "
                "('2030-03-02T06:10:00', '2030-03-02T11:25:00', 'schedule', 'lidl', 'прервано: Windows выключилась')")
    assert update.caught_up(con, dt.datetime(2030, 3, 2, 11, 22)) is None  # сегодняшний прервался — повторить
    assert update.caught_up(con, dt.datetime(2030, 3, 2, 6, 50)) == "2030-03-01 07:30"  # до 07:30 — вчерашний прошёл
    con.execute("INSERT INTO update_runs (started, finished, trigger, steps, summary) VALUES "
                "('2030-03-02T11:23:00', '2030-03-02T11:24:30', 'schedule', 'lidl', 'ошибки: Чеки Lidl Plus')")
    assert update.caught_up(con, dt.datetime(2030, 3, 2, 18, 0)) == "2030-03-02 11:23"  # дошёл до конца — хватит
    assert update.caught_up(con, dt.datetime(2030, 3, 3, 7, 29)) is None  # ежедневный триггер чуть раньше 07:30


def test_mail_logout_after_work_is_not_an_error():
    from receipts import mail

    class Gone:
        def logout(self):
            raise imaplib.IMAP4.abort("command: LOGOUT => socket error: EOF")

    mail.bye(Gone())  # не падает
