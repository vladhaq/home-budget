"""Домашний бюджет: сбор покупок из всех источников, категории, сверка с банком.  (python budget.py version)

  python budget.py serve                          веб-интерфейс: http://127.0.0.1:8765
  python budget.py update [шаги | status]         обновить всё: чеки, почта, фото, банк, сверка, копия
  python budget.py schedule on [ЧЧ:ММ] | off | status   автозапуск обновления каждый день
  python budget.py lidl login | sync | reparse   чеки Lidl Plus
  python budget.py kaufland login | sync | reparse   чеки Kaufland Card
  python budget.py categorize                     назначить категории (делается и после sync)
  python budget.py unknown                        неопознанные позиции
  python budget.py setcat "<шаблон>" "Еда/Сладкое"   правило категории (до веб-интерфейса)
  python budget.py bank login | sync | status | reparse   выписка PKO через Enable Banking
  python budget.py backup [password | list | restore <файл>]   зашифрованные резервные копии
  python budget.py reconcile                      сверка покупок с банком (делается и после bank sync)
  python budget.py mail login | headers | senders | mark | bodies | parse   онлайн-покупки из Gmail
  python budget.py telegram                      забрать новые фото чеков из Telegram-группы
  python budget.py photos [папка|файлы]           фото/сканы/PDF чеков с диска (по умолчанию receipts/inbox)
  python budget.py report                         старый статичный HTML-отчёт (всё есть в serve)
  python budget.py item <товар>                   цены товара по твоим чекам

Газетки и скидки Lidl — отдельно: python lidl-deals/lidl.py
"""
import sys

__version__ = "1.2.0"


def main(argv: list[str]):
    if sys.stdout is None:  # pythonw (автозапуск) — консоли нет, вывод пишем в журнал
        from core.common import DATA
        sys.stdout = sys.stderr = open(DATA / "update_console.log", "w", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    cmd, rest = (argv[0] if argv else ""), argv[1:]
    if cmd == "lidl":
        from receipts import lidl
        lidl.main(rest)
        if rest and rest[0] in ("sync", "reparse"):
            categorize()
    elif cmd == "kaufland":
        from receipts import kaufland
        kaufland.main(rest)
        if rest and rest[0] in ("sync", "reparse"):
            categorize()
    elif cmd == "bank":
        from bank import enablebanking
        enablebanking.main(rest)
        if rest and rest[0] == "sync":
            from core import reconcile
            reconcile.reconcile()
    elif cmd == "update":
        from core import update
        update.main(rest)
    elif cmd == "schedule":
        from core import update
        update.schedule_main(rest)
    elif cmd == "backup":
        from core import backup
        backup.main(rest)
    elif cmd == "reconcile":
        from core import reconcile
        reconcile.reconcile()
    elif cmd == "mail":
        from receipts import mail
        mail.main(rest)
    elif cmd in ("version", "--version"):
        print(f"Домашний бюджет {__version__}")
    elif cmd == "serve":
        from app import server
        server.serve(open_browser="--no-browser" not in rest)
    elif cmd == "categorize":
        categorize()
    elif cmd == "unknown":
        unknown()
    elif cmd == "setcat" and len(rest) == 2:
        from core import categories
        from core.db import connect
        con = connect()
        categories.add_rule(con, rest[1], pattern=rest[0])
        print(f"Правило: «{rest[0]}» -> {rest[1]}")
        categorize()
    elif cmd == "photos":
        from receipts import photos
        photos.import_paths(rest)
    elif cmd == "telegram":
        from receipts import telegram_inbox
        telegram_inbox.fetch()
    elif cmd == "report":
        from receipts import report
        report.report()
    elif cmd == "item":
        from receipts import report
        report.item_history(" ".join(rest))
    else:
        print(__doc__)


def categorize():
    from core import categories
    from core.db import connect
    s = categories.categorize(connect())
    print(f"Категории: словарь {s['keyword']}, правила {s['rule'] + s['code']}, вручную {s['manual']}, "
          f"по группе товара {s['group']}, по отделу магазина {s['section']}, по магазину {s['merchant']}, неопознанных {s['unknown']}"
          + (" — python budget.py unknown" if s["unknown"] else ""))


def unknown():
    from core.db import connect
    rows = connect().execute("""SELECT i.name, count(*) n, round(sum(i.amount), 2) s, max(p.merchant) m
        FROM items i JOIN purchases p ON p.id = i.purchase_id WHERE i.category_id IS NULL
        GROUP BY lower(i.name) ORDER BY n DESC""").fetchall()
    if not rows:
        print("Неопознанных позиций нет.")
        return
    for r in rows:
        print(f"  {r['name'][:40]:<40} {r['n']:>3} раз  {r['s'] or 0:>9.2f} zł  {r['m'] or ''}")
    print('\nНазначить: python budget.py setcat "<часть названия без диакритик>" "Еда/Сладкое"')


if __name__ == "__main__":
    main(sys.argv[1:])
