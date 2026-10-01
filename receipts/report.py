"""HTML-отчёт по покупкам (временный, до веб-интерфейса на этапе 3)."""
import json
import re

from core.common import DATA, fold, money, open_file, word_matches
from core import categories
from core.db import PAYMENT_METHODS, connect

REPORT = DATA / "report.html"

def report():
    con = connect()
    n = con.execute("SELECT count(*) FROM purchases").fetchone()[0]
    if not n:
        print("Чеков пока нет: python budget.py lidl sync")
        return
    months = con.execute("""
        SELECT substr(date, 1, 7) m, count(*), round(sum(total), 2), round(sum(discount), 2)
        FROM purchases WHERE date <> '' GROUP BY m ORDER BY m""").fetchall()
    print(f"\n{'месяц':<8} {'чеков':>6} {'потрачено':>13} {'сэкономлено':>13}")
    for m, cnt, tot, disc in months:
        print(f"{m:<8} {cnt:>6} {money(tot or 0):>13} {money(disc or 0):>13}")
    rows = con.execute("SELECT name, qty, amount, discount FROM items").fetchall()
    agg = {}
    for name, qty, amount, disc in rows:
        a = agg.setdefault(fold(name).strip(), {"name": name, "times": 0, "spent": 0.0})
        a["times"] += 1
        a["spent"] += (amount or 0) - (disc or 0)
    top_spent = sorted(agg.values(), key=lambda a: -a["spent"])[:15]
    top_times = sorted(agg.values(), key=lambda a: -a["times"])[:15]
    print("\nНа что больше всего денег:")
    for a in top_spent:
        print(f"  {a['name'][:40]:<40} {money(a['spent']):>12}  ({a['times']} раз)")
    print("\nЧто покупаешь чаще всего:")
    for a in top_times:
        print(f"  {a['name'][:40]:<40} {a['times']:>4} раз  {money(a['spent']):>12}")
    write_report(con)
    print(f"\nОтчёт с графиком: {REPORT}")
    open_file(REPORT)


REPORT_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Lidl — мои чеки</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
body{font-family:system-ui,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#111;background:#fff}
h1{margin-bottom:4px} .muted{color:#666;font-size:.9em}
table{border-collapse:collapse;margin:8px 0;width:100%} td,th{border-bottom:1px solid #e5e5e5;padding:5px 8px;font-size:.9em;text-align:left}
th{background:#f6f6f6;cursor:pointer;user-select:none} td.n,th.n{text-align:right;white-space:nowrap}
#month{margin-top:24px;padding-top:8px;border-top:2px solid #2563eb}
.nav{display:flex;align-items:center;gap:12px} .nav button{font-size:1.1em;padding:2px 12px;cursor:pointer}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}
.card{background:#f6f8fb;border-radius:8px;padding:10px 12px} .card b{display:block;font-size:1.3em;margin-top:2px}
.up{color:#dc2626} .down{color:#16a34a} .save{color:#16a34a}
details{border-bottom:1px solid #e5e5e5;padding:6px 0} summary{cursor:pointer}
details table{margin:6px 0 4px 16px;width:calc(100% - 16px)}
.tabs button{padding:4px 12px;margin-right:6px;cursor:pointer;border:1px solid #ccc;background:#fff;border-radius:6px}
.tabs button.on{background:#2563eb;color:#fff;border-color:#2563eb}
</style></head><body>
<h1>Lidl — мои чеки</h1>
<p class="muted">Нажми на столбец месяца — ниже откроется полная статистика за месяц.</p>
<canvas id="chart" height="120"></canvas>
<section id="month"></section>
<script>
const RECEIPTS = __DATA__;
const MONTHS = ["январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"];
const zl = x => (x ?? 0).toFixed(2).replace(".", ",") + " zł";
const esc = s => String(s ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const byMonth = {};
for (const r of RECEIPTS) (byMonth[r.date.slice(0, 7)] ??= []).push(r);
const labels = Object.keys(byMonth).sort();
const sum = (arr, f) => arr.reduce((s, x) => s + (f(x) || 0), 0);
const spent = labels.map(m => +sum(byMonth[m], r => r.total).toFixed(2));
const saved = labels.map(m => +sum(byMonth[m], r => r.discount).toFixed(2));
const monthName = m => MONTHS[+m.slice(5) - 1] + " " + m.slice(0, 4);

const chart = new Chart(document.getElementById("chart"), {
  type: "bar",
  data: {labels: labels.map(m => m.slice(5) + "." + m.slice(2, 4)), datasets: [
    {label: "потрачено, zł", data: spent, backgroundColor: labels.map(() => "#2563eb")},
    {label: "сэкономлено на скидках, zł", data: saved, backgroundColor: labels.map(() => "#16a34a")}]},
  options: {
    interaction: {mode: "index", intersect: false},
    plugins: {legend: {position: "bottom"}},
    onClick: (e, els) => { if (els.length) show(labels[els[0].index]); },
    onHover: (e, els) => { e.native.target.style.cursor = els.length ? "pointer" : "default"; },
  },
});

let itemSort = {key: "spent", dir: -1}, view = "cats", current = null;

function catsTable(rs) {
  // верхний уровень -> подкатегории, с долей от суммы
  const top = {};
  let all = 0;
  for (const r of rs) for (const it of r.items) {
    const v = (it.amount || 0) - (it.discount || 0), [t, sub] = it.cat.split("/");
    const a = top[t] ??= {spent: 0, subs: {}};
    a.spent += v; all += v;
    if (sub) a.subs[sub] = (a.subs[sub] || 0) + v;
  }
  const bar = (v) => `<div style="background:#2563eb;height:8px;border-radius:4px;width:${(v / all * 100).toFixed(1)}%"></div>`;
  return "<table><tr><th>категория</th><th class='n'>потрачено</th><th class='n'>доля</th><th style='width:35%'></th></tr>" +
    Object.entries(top).sort((a, b) => b[1].spent - a[1].spent).map(([t, a]) =>
      `<tr><td><b>${esc(t)}</b></td><td class="n"><b>${zl(a.spent)}</b></td><td class="n">${(a.spent / all * 100).toFixed(0)}%</td><td>${bar(a.spent)}</td></tr>` +
      Object.entries(a.subs).sort((x, y) => y[1] - x[1]).map(([s, v]) =>
        `<tr><td style="padding-left:24px">${esc(s)}</td><td class="n">${zl(v)}</td><td class="n">${(v / all * 100).toFixed(0)}%</td><td>${bar(v)}</td></tr>`).join("")
    ).join("") + "</table>";
}

function aggregate(rs) {
  const agg = {};
  for (const r of rs) for (const it of r.items) {
    const k = it.name.toLowerCase();
    const a = agg[k] ??= {name: it.name, times: 0, qty: 0, spent: 0, discount: 0};
    a.times += 1; a.qty += it.qty || 1;
    a.spent += (it.amount || 0) - (it.discount || 0); a.discount += it.discount || 0;
  }
  return Object.values(agg);
}

function show(m) {
  current = m;
  const all = m === "all";
  const i = labels.indexOf(m), rs = (all ? RECEIPTS : byMonth[m]).slice().sort((a, b) => a.date < b.date ? -1 : 1);
  chart.data.datasets[0].backgroundColor = labels.map((_, j) => j === i ? "#1e3a8a" : "#2563eb");
  chart.data.datasets[1].backgroundColor = labels.map((_, j) => j === i ? "#14532d" : "#16a34a");
  chart.update("none");
  const total = all ? sum(spent, x => x) : spent[i], disc = all ? sum(saved, x => x) : saved[i];
  const prev = !all && i > 0 ? spent[i - 1] : null;
  const diff = prev ? (total / prev - 1) * 100 : null;
  const title = all ? `всё время <span class="muted">(${monthName(labels[0])} – ${monthName(labels[labels.length - 1])})</span>` : monthName(m);
  const items = aggregate(rs);
  const nItems = sum(rs, r => r.items.length);
  const byDay = {}; for (const r of rs) byDay[r.date.slice(0, 10)] = (byDay[r.date.slice(0, 10)] || 0) + r.total;
  const topDay = Object.entries(byDay).sort((a, b) => b[1] - a[1])[0];
  document.getElementById("month").innerHTML = `
    <div class="nav"><button ${i > 0 ? "" : "disabled"} onclick="show(labels[${i - 1}])">←</button>
      <h2 style="margin:0">${title}</h2>
      <button ${!all && i < labels.length - 1 ? "" : "disabled"} onclick="show(labels[${i + 1}])">→</button>
      <span class="tabs" style="margin-left:auto"><button class="${all ? "on" : ""}" onclick="show('all')">всё время</button></span></div>
    <div class="cards">
      <div class="card">потрачено<b>${zl(total)}</b>${diff === null ? "" :
        `<span class="${diff > 0 ? "up" : "down"}">${diff > 0 ? "+" : ""}${diff.toFixed(0)}% к прошлому месяцу</span>`}</div>
      <div class="card">сэкономлено<b class="save">${zl(disc)}</b>${(disc / (total + disc) * 100 || 0).toFixed(0)}% от цены без скидок</div>
      <div class="card">чеков<b>${rs.length}</b>средний ${zl(total / rs.length)}${all ? `<br>в месяц ${zl(total / labels.length)}` : ""}</div>
      <div class="card">позиций<b>${nItems}</b>разных товаров: ${items.length}</div>
      <div class="card">самый дорогой день<b>${topDay[0].slice(8, 10)}.${topDay[0].slice(5, 7)}</b>${zl(topDay[1])}</div>
    </div>
    <div class="tabs"><button class="${view === "cats" ? "on" : ""}" onclick="view='cats';show(current)">категории</button><button
      class="${view === "items" ? "on" : ""}" onclick="view='items';show(current)">товары</button><button
      class="${view === "receipts" ? "on" : ""}" onclick="view='receipts';show(current)">чеки</button></div>
    <div id="body"></div>`;
  document.getElementById("body").innerHTML =
    view === "items" ? itemsTable(items) : view === "cats" ? catsTable(rs) : receiptsList(rs);
  location.hash = m;
}

function itemsTable(items) {
  const k = itemSort.key, d = itemSort.dir;
  items.sort((a, b) => (typeof a[k] === "string" ? a[k].localeCompare(b[k]) : a[k] - b[k]) * d);
  const col = (key, title, cls = "n") => `<th class="${cls}" onclick="sortBy('${key}')">${title}${k === key ? (d < 0 ? " ▾" : " ▴") : ""}</th>`;
  return `<table><tr>${col("name", "товар", "")}${col("times", "раз")}${col("qty", "кол-во")}${col("spent", "потрачено")}${col("discount", "скидка")}</tr>` +
    items.map(a => `<tr><td>${esc(a.name)}</td><td class="n">${a.times}</td><td class="n">${+a.qty.toFixed(3)}</td>
      <td class="n">${zl(a.spent)}</td><td class="n save">${a.discount ? "−" + zl(a.discount) : ""}</td></tr>`).join("") + "</table>";
}

function sortBy(key) {
  itemSort = {key, dir: itemSort.key === key ? -itemSort.dir : (key === "name" ? 1 : -1)};
  show(current);
}

function receiptsList(rs) {
  return rs.map(r => `<details><summary><b>${r.date.slice(8, 10)}.${r.date.slice(5, 7)} ${r.date.slice(11, 16)}</b>
      · ${zl(r.total)}${r.discount ? ` · <span class="save">скидки −${zl(r.discount)}</span>` : ""}
      · ${r.items.length} поз. · ${esc(r.payment)} <span class="muted">${esc(r.merchant)} ${esc(r.store)}</span></summary>
    <table><tr><th>товар</th><th class="n">кол-во</th><th class="n">цена</th><th class="n">сумма</th><th class="n">скидка</th></tr>` +
    r.items.map(it => `<tr><td>${esc(it.name)}</td><td class="n">${+(it.qty || 1).toFixed(3)}</td>
      <td class="n">${it.unit_price != null ? zl(it.unit_price) : ""}</td><td class="n">${zl(it.amount)}</td>
      <td class="n save">${it.discount ? "−" + zl(it.discount) : ""}</td></tr>`).join("") + "</table></details>").join("");
}

const h = location.hash.slice(1);
show(labels.includes(h) || h === "all" ? h : labels[labels.length - 1]);
</script></body></html>"""


def write_report(con):
    categories.seed(con)
    cats = categories.paths(con)
    receipts = []
    for rid, date, store, total, disc, pay, merchant in con.execute(
            "SELECT id, date, store, total, discount, payment_method, merchant FROM purchases "
            "WHERE date <> '' ORDER BY date"):
        items = [{"name": n, "qty": q, "unit_price": u, "amount": a, "discount": d,
                  "cat": cats.get(c, "Неопознанные")} for n, q, u, a, d, c in con.execute(
            "SELECT name, qty, unit_price, amount, discount, category_id FROM items WHERE purchase_id = ? ORDER BY line",
            (rid,))]
        receipts.append({"date": date, "store": store or "", "merchant": merchant or "", "total": total or 0,
                         "discount": disc or 0, "payment": PAYMENT_METHODS.get(pay, pay), "items": items})
    data = json.dumps(receipts, ensure_ascii=False).replace("</", "<\\/")
    REPORT.write_text(REPORT_PAGE.replace("__DATA__", data), encoding="utf-8")


def item_history(query: str):
    tokens = [t for t in fold(query).split() if len(t) > 2]
    con = connect()
    rows = con.execute("""SELECT r.date, i.name, i.qty, i.unit_price, i.amount, i.discount
                          FROM items i JOIN purchases r ON r.id = i.purchase_id ORDER BY r.date""").fetchall()
    hits = [r for r in rows if tokens and all(any(word_matches(w, t) for w in re.findall(r"\w+", fold(r[1])))
                                              for t in tokens)]
    if not hits:
        print(f"«{query}»: в чеках не найдено.")
        return
    print(f"\n{'дата':<11} {'товар':<34} {'кол-во':>7} {'цена':>10} {'скидка':>9}")
    for date, name, qty, unit, amount, disc in hits:
        print(f"{date[:10]:<11} {name[:33]:<34} {qty or 1:>7g} {money(unit) if unit else '—':>10} "
              f"{('-' + money(disc)) if disc else '':>9}")
    paid = [r[3] for r in hits if r[3]]
    if len(paid) >= 2:
        print(f"\nЦена: {money(paid[0])} → {money(paid[-1])} ({(paid[-1] / paid[0] - 1) * 100:+.1f}%), "
              f"мин. {money(min(paid))}, покупок: {len(hits)}")

