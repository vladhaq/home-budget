"""Журнал обновлений: прерванный запуск понятно подписан; выход из почты после работы — не ошибка."""
import imaplib

from core import db, update


def test_run_killed_before_first_step(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    con = db.connect()
    con.execute("INSERT INTO update_runs (started, trigger, steps) VALUES ('2026-10-08T07:35:13', 'schedule', 'lidl')")
    con.execute("INSERT INTO update_runs (started, trigger, steps) VALUES ('2026-10-08T08:00:00', 'ui', 'lidl')")
    con.execute("INSERT INTO update_steps (run_id, step, started, status) VALUES (2, 'lidl', '2026-10-08T08:00:01', 'running')")
    update.mark_dead(con)
    summary = dict(con.execute("SELECT id, summary FROM update_runs").fetchall())
    assert summary[1].startswith("прервано до первого шага") and summary[2].startswith("прервано: процесс")


def test_mail_logout_after_work_is_not_an_error():
    from receipts import mail

    class Gone:
        def logout(self):
            raise imaplib.IMAP4.abort("command: LOGOUT => socket error: EOF")

    mail.bye(Gone())  # не падает
