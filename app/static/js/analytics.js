// Страница «Аналитика»: прогноз месяца, календарь ожидаемых платежей и поступлений, регулярные платежи.
let ANALYTICS = null;
const MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const CONF = {"высокая": "--green", "средняя": "--orange", "низкая": "--gray"};
const signed = v => `${v >= 0 ? "+" : "−"}${zl(Math.abs(v))}`;

async function renderAnalytics() {
  if (!ANALYTICS) ANALYTICS = await (await fetch("/api/analytics")).json();
  const A = ANALYTICS, M = A.month, mname = MONTHS_NOM[+M.month.slice(5) - 1];
  const pace = M.typical_by_day ? M.spent / M.typical_by_day - 1 : null;
  const vsTyp = M.typical ? M.forecast / M.typical - 1 : null;
  const fixedLeft = M.fixed_left.reduce((s, x) => s + x.amount, 0);
  document.getElementById("main").innerHTML = `
    <h2>${mname[0].toUpperCase() + mname.slice(1)}: прогноз расходов <span class="muted" style="font-size:.6em;font-weight:400">${M.day}-й день из ${M.days}</span></h2>
    <div class="cards">
      <div class="card">потрачено с 1-го<b>${zl(M.spent)}</b>${pace === null ? "" : `обычно к ${M.day}-му — ${zl(M.typical_by_day)}`}</div>
      <div class="card">прогноз на конец месяца<b>${zl(M.forecast)}</b>${vsTyp === null ? "" : trend(vsTyp, `обычно ${zl(M.typical)}`)}</div>
      <div class="card">регулярные платежи до конца месяца<b>${zl(fixedLeft)}</b>${M.fixed_left.length
        ? M.fixed_left.map(x => `${ddmm(x.date)} ${esc(x.name)}`).slice(0, 3).join(", ") : "больше не ждём"}</div>
      <div class="card">поступления за месяц<b class="save">${zl(M.income_expected)}</b>пришло ${zl(M.income_got)}${M.income_left.length
        ? `, ждём ${M.income_left.map(x => `${esc(x.name.split(" ")[0])} ${ddmm(x.date)}`).join(", ")}` : ""}</div>
    </div>
    <table class="fc"><tr><th>группа</th><th class="n">потрачено</th><th class="n">обычно к ${M.day}-му</th>
      <th class="n">ещё регулярных</th><th class="n">прогноз</th><th class="n">обычно за месяц</th><th style="width:26%"></th></tr>
      ${M.groups.map(g => {
        const r = g.typical ? g.forecast / g.typical - 1 : null, w = Math.max(g.forecast, g.typical) || 1;
        return `<tr><td><b>${esc(g.name)}</b></td><td class="n">${zl(g.spent)}</td><td class="n muted">${zl(g.by_day)}</td>
          <td class="n">${g.fixed_left ? zl(g.fixed_left) : ""}</td><td class="n"><b>${zl(g.forecast)}</b></td><td class="n muted">${zl(g.typical)}</td>
          <td><div class="fcbar"><span class="fcf" style="width:${(g.spent / w * 100).toFixed(1)}%"></span><span class="fcp"
            style="width:${((g.forecast - g.spent) / w * 100).toFixed(1)}%"></span><i style="left:${(g.typical / w * 100).toFixed(1)}%"></i></div>
            ${r === null ? `<span class="muted">раньше не было</span>` : trend(r)}</td></tr>`; }).join("")}
    </table>
    <p class="muted">Прогноз = уже потрачено + регулярные платежи, которые ещё ждём, + переменные траты, которые обычно приходятся
      на оставшиеся дни (медиана за ${M.base_months.length} последних полных месяцев: ${M.base_months.map(m => MONTHS_NOM[+m.slice(5) - 1].slice(0, 3)).join(", ")}).
      Полоска: потрачено · прогноз остатка месяца · черта — обычный месяц. Крупные разовые покупки (техника, учёба) делают
      «обычно» в таких группах неровным — смотри на них с поправкой.</p>

    <h2>Календарь: что ожидается</h2>
    ${calendarTable(A.calendar)}

    <h2>Регулярные платежи и поступления</h2>
    <p class="muted">Найдены по выписке и онлайн-заказам: одна и та же сторона, похожая сумма, устойчивый интервал. Подтверди — и платёж
      всегда будет в календаре и прогнозе; «не регулярное» — убрать. Неподтверждённые с низкой уверенностью в прогноз не идут.</p>
    ${recurringTable(A.recurring.filter(r => r.state !== "rejected"))}
    ${A.recurring.some(r => r.state === "rejected") ? `<details class="rej"><summary class="muted">отклонённые: ${A.recurring.filter(r => r.state === "rejected").length}</summary>
      ${recurringTable(A.recurring.filter(r => r.state === "rejected"))}</details>` : ""}`;
}

function trend(r, extra = "") {  // «выше обычного на 24%» / «как обычно»
  const p = Math.round(Math.abs(r) * 100);
  const t = r > 0.15 ? `<span class="up">выше обычного на ${p}%</span>` : r < -0.15 ? `<span class="save">ниже обычного на ${p}%</span>`
    : `<span class="muted">как обычно</span>`;
  return extra ? `${extra} · ${t}` : t;
}

function calendarTable(C) {
  if (!C.items.length) return `<p class="muted">Регулярных платежей пока не нашлось.</p>`;
  return `<p class="muted">${C.balance != null ? `Остаток на счёте PKO <b>${zl(C.balance)}</b> на ${ddmmyy(C.balance_date)}; ниже — каким он будет
      после каждого ожидаемого платежа (без обычных повседневных трат).` : ""} До ${ddmmyy(C.until)}.</p>
    <table><tr><th>дата</th><th>что</th><th>категория</th><th class="n">сумма</th><th class="n">остаток после</th></tr>` +
    C.items.map(x => `<tr class="${x.sure ? "" : "unsure"}"><td style="white-space:nowrap">${ddmmyy(x.date)} <span class="muted">${
        ["вс", "пн", "вт", "ср", "чт", "пт", "сб"][new Date(x.date + "T12:00:00").getDay()]}</span></td>
      <td><div class="who">${ava(x.name)}<div>${esc(x.name)}${x.late ? `<div class="warn" style="font-size:.85em">ожидалось ${ddmm(x.expected)} — ещё не было</div>` : ""}${
        x.sure ? "" : `<div class="muted" style="font-size:.85em">предположительно — подтверди ниже</div>`}</div></div></td>
      <td class="muted">${esc((x.cat_path || "").replaceAll("/", " › "))}</td>
      <td class="n ${x.amount > 0 ? "save" : ""}">${x.variable ? "≈" : ""}${signed(x.amount)}</td>
      <td class="n">${x.balance != null ? `<span class="${x.balance < 0 ? "up" : ""}">${zl(x.balance)}</span>` : ""}</td></tr>`).join("") + "</table>";
}

function recurringTable(rs) {
  if (!rs.length) return `<p class="muted">Ничего не найдено.</p>`;
  return `<table><tr><th>кто / что</th><th>как часто</th><th class="n">сумма</th><th>последний</th><th>следующий</th><th>уверенность</th><th></th></tr>` +
    rs.map(r => {
      const state = r.state === "confirmed" ? `<span class="st" style="--c:var(--green)">подтверждено</span>`
        : r.state === "rejected" ? `<span class="st" style="--c:var(--gray)">не регулярное</span>` : "";
      return `<tr class="${r.active ? "" : "unsure"}"><td><div class="who">${ava(r.name)}<div>${esc(r.name)} ${r.flow === "in" ? `<span class="src">поступление</span>` : ""}
          <div class="muted">${esc((r.cat_path || "без категории").replaceAll("/", " › "))}</div></div></div></td>
        <td>${esc(r.period_word)}${r.dom ? `, обычно ${r.dom}-го` : r.period === "month" ? `, каждые ~${Math.round(r.days)} дн.` : ""}</td>
        <td class="n ${r.flow === "in" ? "save" : ""}">${r.variable ? "≈" : ""}${zl(r.typical)}${r.change ? `<div class="${r.change > 0 ? "up" : "save"}" style="font-size:.85em">
          последний ${r.change > 0 ? "дороже" : "дешевле"} на ${Math.abs(r.change)}%</div>` : ""}</td>
        <td style="white-space:nowrap">${ddmmyy(r.last)}</td>
        <td style="white-space:nowrap">${r.active ? `${ddmmyy(r.next)}${r.late ? `<div class="warn" style="font-size:.85em">просрочено</div>` : ""}`
          : `<span class="muted">закончилось?</span>`}</td>
        <td><span class="st" style="--c:var(${CONF[r.confidence]})">${r.confidence}</span><div class="muted" style="font-size:.85em">${r.n} раз с ${ddmmyy(r.first)}</div></td>
        <td style="white-space:nowrap">${state}
          ${r.state !== "confirmed" ? `<button class="chip act" data-rmark="confirmed" data-rkey="${esc(r.key)}" title="всегда учитывать в календаре и прогнозе">✓ регулярное</button>` : ""}
          ${r.state !== "rejected" ? `<button class="chip act" data-rmark="rejected" data-rkey="${esc(r.key)}" title="не учитывать">✕ нет</button>` : ""}
          ${r.state !== "suggested" ? `<button class="chip act" data-rmark="" data-rkey="${esc(r.key)}" title="пусть решает программа">↺</button>` : ""}</td></tr>`;
    }).join("") + "</table>";
}

async function markRecurring(key, state) {
  try { await post("/api/analytics/mark", {key, state: state || null}); } catch (e) { toast("Не получилось: " + e.message); return; }
  ANALYTICS = null; const y = scrollY; await renderAnalytics(); scrollTo(0, y);
  toast(state === "confirmed" ? "Отмечено как регулярное" : state === "rejected" ? "Убрано из регулярных" : "Решает программа");
}
