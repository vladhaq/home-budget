// Обзор: график по периодам, панель периода, таблицы товаров/категорий/чеков, случаи одной позиции.
// ---------- обзор: траты по дням / неделям / месяцам / кварталам / годам
let chart = null, CUR_RANGE = null;  // CUR_RANGE — период графика (в т.ч. «последние 2 месяца»)
const GRANS = [["day", "день"], ["week", "неделя"], ["month", "месяц"], ["quarter", "квартал"], ["year", "год"]];
const DRILL = {year: "quarter", quarter: "month", month: "day", week: "day"};  // двойной клик — на уровень мельче
const DRILL_WORD = {year: "кварталы", quarter: "месяцы", month: "дни", week: "дни"};
const PREV_WORD = {day: "к прошлому дню", week: "к прошлой неделе", month: "к прошлому месяцу", quarter: "к прошлому кварталу", year: "к прошлому году"};
const PER_WORD = {day: "в день", week: "в неделю", month: "в месяц", quarter: "в квартал", year: "в год"};
const DOW = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
S.gran = "month"; S.period = null; S.range = null; S.crumbs = [];
const isoDay = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const addDays = (day, n) => { const d = new Date(day + "T12:00:00"); d.setDate(d.getDate() + n); return isoDay(d); };
const weekStart = day => { const d = new Date(day + "T12:00:00"); d.setDate(d.getDate() - (d.getDay() + 6) % 7); return isoDay(d); };
function pkey(date, g) {  // ключ периода: 2026-10-01 / неделя — её понедельник / 2026-10 / 2026-Q4 / 2026
  const day = date.slice(0, 10);
  return g === "day" ? day : g === "week" ? weekStart(day) : g === "month" ? day.slice(0, 7)
    : g === "quarter" ? `${day.slice(0, 4)}-Q${Math.floor((+day.slice(5, 7) - 1) / 3) + 1}` : day.slice(0, 4);
}
function pspan(k, g) {  // первый и последний день периода
  if (g === "day") return [k, k];
  if (g === "week") return [k, addDays(k, 6)];
  if (g === "month") return [k + "-01", isoDay(new Date(+k.slice(0, 4), +k.slice(5, 7), 0, 12))];
  if (g === "quarter") { const y = +k.slice(0, 4), q = +k.slice(6);
    return [`${y}-${String(q * 3 - 2).padStart(2, "0")}-01`, isoDay(new Date(y, q * 3, 0, 12))]; }
  return [k + "-01-01", k + "-12-31"];
}
const plabel = (k, g) => g === "day" || g === "week" ? `${k.slice(8, 10)}.${k.slice(5, 7)}` : g === "month" ? `${k.slice(5, 7)}.${k.slice(2, 4)}`
  : g === "quarter" ? `${k.slice(6)} кв. ${k.slice(2, 4)}` : k;
function pname(k, g) {
  if (g === "day") return `${+k.slice(8, 10)} ${MONTHS_GEN[+k.slice(5, 7) - 1]} ${k.slice(0, 4)}, ${DOW[new Date(k + "T12:00:00").getDay()]}`;
  if (g === "week") { const e = addDays(k, 6); return `неделя ${k.slice(8, 10)}.${k.slice(5, 7)} – ${e.slice(8, 10)}.${e.slice(5, 7)}.${e.slice(0, 4)}`; }
  if (g === "month") return monthName(k);
  if (g === "quarter") return `${k.slice(6)} квартал ${k.slice(0, 4)}`;
  return `${k} год`;
}
// ---------- вид графика: столбцы / линия / кольцо долей (выбор запоминается в браузере)
const CTYPE_KEY = "budget-chart-type";
S.ctype = (() => { try { return localStorage.getItem(CTYPE_KEY) || "bar"; } catch (e) { return "bar"; } })();
const CTYPES = [["bar", "столбцы", '<path d="M6 19v-7"/><path d="M12 19V5"/><path d="M18 19v-10"/>'],
                ["line", "линия", '<path d="M3 17l5-6 4 3 8-9"/>'],
                ["donut", "кольцо", '<circle cx="12" cy="12" r="7.5"/><path d="M12 4.5V12l5.3 5.3"/>']];
// цвета долей кольца — по месту категории в дереве: у «Еды» один цвет в любом месяце
// (если цвет уже занят долей крупнее — следующий свободный: соседние доли всегда разных цветов)
const PALETTE = ["#8b5cf6", "#14b8a6", "#f59e0b", "#3b82f6", "#ec4899", "#22c55e", "#f97316", "#06b6d4", "#ef4444",
                 "#a3e635", "#e879f9", "#facc15"];
function ctypeTabs(donutOK) {
  const cur = S.ctype === "donut" && !donutOK ? "bar" : S.ctype;
  return `<div class="tabs ctabs" data-key="ctype">${CTYPES.map(([k, n, d]) => {
    const off = k === "donut" && !donutOK;
    return `<button class="${cur === k ? "on" : ""}" data-ctype="${k}" ${off ? "disabled" : ""} title="${off
      ? "кольцо — когда в выбранном больше одной категории" : n}">${SVG(d)}<span>${n}</span></button>`;
  }).join("")}</div>`;
}

// доли кольца: без фильтра — группы категорий; выбрана одна — её подкатегории; выбрано несколько — они.
// Если вышла одна доля, а у неё есть подкатегории (все поступления — «Доходы»), кольцо спускается к ним
function donutParts(rows, pickOverride = null) {
  const pick = pickOverride || [...S.F.cats].map(String);
  const one = pick.length === 1 && CAT[pick[0]] ? pick[0] : null;
  const order = (!pick.length ? KIDS[0] || [] : one ? KIDS[one] || [] : pick.filter(k => CAT[k]).map(k => CAT[k])).map(c => String(c.id));
  const up = k => k === "none" || CAT[k].parent_id == null ? "none" : String(CAT[k].parent_id);
  const keyOf = cat => {
    let k = cat == null || !CAT[cat] ? "none" : String(cat);
    if (!pick.length) { while (k !== "none" && CAT[k].parent_id != null) k = up(k); return k; }
    if (one) {  // ребёнок выбранной категории на пути к позиции; позиция прямо в ней — она сама
      let prev = null;
      while (k !== "none" && k !== one) { prev = k; k = up(k); }
      return k === "none" ? "none" : prev ?? one;
    }
    while (!pick.includes(k) && k !== "none") k = up(k);  // ближайшая выбранная вверх по дереву
    return k;
  };
  const sums = {};
  for (const r of rows) for (const it of r.items) { const k = keyOf(it.cat); sums[k] = (sums[k] || 0) + net(it); }
  let parts = Object.entries(sums).filter(([, v]) => v > 0.005).map(([k, v]) => (
    {k, v, name: k === "none" ? "Неопознанные" : k === one ? `${CAT[k].name}: без подкатегории` : CAT[k].name}))
    .sort((a, b) => b.v - a.v);
  const only = parts.length === 1 && parts[0].k !== "none" && parts[0].k !== one ? parts[0].k : null;
  if (only && (KIDS[only] || []).length) return donutParts(rows, [only]);
  const used = new Set();
  for (const p of parts) {
    const i = order.indexOf(p.k);
    if (i < 0) { p.color = css("--c-gray"); continue; }
    let j = i % PALETTE.length;
    for (let n = 0; n < PALETTE.length && used.has(j); n++) j = (j + 1) % PALETTE.length;
    used.add(j); p.color = PALETTE[j];
  }
  if (parts.length > 8) {  // мелочь — одной долей
    const rest = parts.slice(7);
    parts = parts.slice(0, 7).concat({k: null, v: rest.reduce((s, p) => s + p.v, 0), name: `остальное (${rest.length})`, color: css("--c-gray2")});
  }
  return {parts, total: parts.reduce((s, p) => s + p.v, 0)};
}

// «по дням» без выбранного в фильтре периода: окно в N дней (длина запоминается), стрелки сдвигают его на свою длину
const DAY_SPANS = [[14, "2 нед.", "2 недели"], [31, "месяц", "30 дней"], [61, "2 мес.", "2 месяца"], [92, "3 мес.", "3 месяца"],
                   [183, "полгода", "полгода"], [365, "год", "год"]];
const DSPAN_KEY = "budget-day-span";
S.daySpan = (() => { try { return +localStorage.getItem(DSPAN_KEY) || 61; } catch (e) { return 61; } })();
S.dayShift = 0;  // на сколько окон назад от последней покупки
function dayWindow(first, last) {
  const to = addDays(last, -S.daySpan * S.dayShift), from = addDays(to, -(S.daySpan - 1));
  const word = (DAY_SPANS.find(([d]) => d === S.daySpan) || [, , `${S.daySpan} дней`])[2];
  const dm = d => `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(2, 4)}`;
  return {from, to, window: true, older: from > first, label: S.dayShift ? `${dm(from)} – ${dm(to)}` : `последние ${word}`};
}
function dayWindowBar(range) {
  return `<span class="crumbs"><div class="tabs" data-key="dspan">${DAY_SPANS.map(([d, n]) =>
      `<button class="${S.daySpan === d ? "on" : ""}" data-dspan="${d}">${n}</button>`).join("")}</div>
    <button class="chip" data-dshift="1" ${range.older ? "" : "disabled"} title="раньше">‹</button>
    <b>${esc(range.label)}</b>
    <button class="chip" data-dshift="-1" ${S.dayShift > 0 ? "" : "disabled"} title="позже">›</button></span>`;
}

function granBar(range, donutOK = false) {  // переключатель день/неделя/... и «хлебные крошки» после двойного клика
  const path = S.crumbs.map((c, i) => `<button class="linkbtn" data-crumb="${i}">${esc(c.range ? c.range.label : "всё время")}</button>`).join(" › ");
  const sides = flowSides(), flow = curFlow();
  const flowTabs = sides.out && sides.in ? `<div class="tabs" data-key="flow">${[["out", "расходы"], ["in", "поступления"], ["both", "расходы и поступления"]]
    .map(([k, n]) => `<button class="${flow === k ? "on" : ""}" data-flow="${k}">${n}</button>`).join("")}</div>` : "";
  return `<div class="ovbar"><div class="tabs" data-key="gran">${GRANS.map(([k, n]) =>
      `<button class="${S.gran === k ? "on" : ""}" data-gran="${k}">${n}</button>`).join("")}</div>${flowTabs}${ctypeTabs(donutOK)}
    ${range && range.window ? dayWindowBar(range) : range ? `<span class="crumbs">${S.crumbs.length
      ? `<button class="chip" data-back="1">‹ назад</button> ${path} ›` : ""}<b>${esc(range.label)}</b></span>` : ""}</div>`;
}
// скрытые глазом категории (с подкатегориями): уходят из графика, карточек, «товаров» и «чеков»; в дереве — зачёркнуты
const HIDDEN_KEY = "budget-hidden-cats";
S.hidden = new Set((() => { try { return JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]"); } catch (e) { return []; } })());
function hiddenCat(cat) {
  if (!S.hidden.size) return false;
  let k = cat == null || !CAT[cat] ? "none" : String(cat);
  for (;;) {
    if (S.hidden.has(k)) return true;
    if (k === "none" || !CAT[k] || CAT[k].parent_id == null) return false;
    k = String(CAT[k].parent_id);
  }
}
function visible(r) {  // строка обзора без скрытых категорий; null — если в ней ничего не осталось
  if (!S.hidden.size) return r;
  const items = r.items.filter(it => !hiddenCat(it.cat));
  if (!items.length) return null;
  if (items.length === r.items.length) return r;
  return {...r, items, value: items.reduce((s, it) => s + net(it), 0), saved: items.reduce((s, it) => s + (it.discount || 0), 0)};
}
function toggleHidden(k) {
  S.hidden.has(k) ? S.hidden.delete(k) : S.hidden.add(k);
  try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...S.hidden])); } catch (e) { /* только до перезагрузки */ }
}
function hiddenBar() {
  // скрытые, которые сейчас вообще есть в дереве (старые id после удаления категорий не показываем)
  const names = [...S.hidden].filter(k => k === "none" || CAT[k]).map(k => k === "none" ? "Неопознанные" : CAT[k].name);
  return names.length ? `<div class="hidbar">${ICON.eyeOff} скрыто из графика и сводки: <b>${names.map(esc).join(", ")}</b>
    <button class="chip" data-eyeall="1">показать всё</button></div>` : "";
}

function renderOverview() {
  const g = S.gran, main = document.getElementById("main"), flow = curFlow();
  let outs = flow === "in" ? [] : filtered(), ins = flow === "out" ? [] : filteredIncome(), range = S.range;
  let ps = outs.concat(ins);
  // дни за всю историю не уместятся — окно (по умолчанию 2 месяца); выбран период в фильтре — он весь
  if (!range && g === "day" && ps.length && !S.F.from && !S.F.to) {
    const ds = ps.map(p => p.date.slice(0, 10));
    range = dayWindow(ds.reduce((a, b) => a < b ? a : b), ds.reduce((a, b) => a > b ? a : b));
  }
  CUR_RANGE = range;
  const inRange = p => !range || p.date.slice(0, 10) >= range.from && p.date.slice(0, 10) <= range.to;
  outs = outs.filter(inRange); ins = ins.filter(inRange); ps = outs.concat(ins);
  if (!ps.length) { main.innerHTML = granBar(range) + `<div class="empty">За этот период ничего нет.</div>`; placeIndicators(); return; }
  const byKey = {}, byIn = {}, days = ps.map(p => p.date.slice(0, 10)).sort();
  for (const p of outs) (byKey[pkey(p.date, g)] ??= []).push(p);
  for (const p of ins) (byIn[pkey(p.date, g)] ??= []).push(p);
  const labels = [];  // все периоды подряд — пустой день или неделя тоже видны
  for (let d = range ? range.from : days[0]; d <= (range ? range.to : days[days.length - 1]); d = addDays(d, 1)) {
    const k = pkey(d, g); if (labels[labels.length - 1] !== k) labels.push(k);
  }
  for (const k of labels) { byKey[k] ??= []; byIn[k] ??= []; }
  if (S.period !== "all" && !labels.includes(S.period)) S.period = g === "day" || g === "week" || range ? "all" : labels[labels.length - 1];
  // кольцо — если есть что делить (больше одной доли) хотя бы у расходов или поступлений за весь показанный период
  const vis = rows => rows.map(visible).filter(Boolean);
  const donutOK = donutParts(vis(outs)).parts.length > 1 || donutParts(vis(ins)).parts.length > 1;
  const type = S.ctype === "donut" && !donutOK ? "bar" : S.ctype;
  const hint = type === "donut" ? "Доли категорий за выбранный период; клик по доле — показать только её (у одной категории — её подкатегории). Период — стрелками ‹ › ниже."
    : `Клик по ${type === "line" ? "точке" : "столбцу"} — статистика периода, повторный — снять выбор.
      Двойной клик — ${g === "day" ? "чеки этого дня" : "разбить на " + DRILL_WORD[g]}.`;
  main.innerHTML = `${granBar(range, donutOK)}<p class="muted">${hint}</p>
    ${hiddenBar()}${type === "donut" ? `<div class="chartbox donutbox" id="donuts"></div>`
      : `<div class="chartbox"><canvas id="chart" height="110"></canvas></div>`}<section id="panel"></section>`;
  // суммы на графике — без скрытых глазом категорий
  const vsum = (rows, f) => +rows.reduce((s, p) => { const v = visible(p); return s + (v ? v[f] : 0); }, 0).toFixed(2);
  const spent = labels.map(k => vsum(byKey[k], "value"));
  const saved = labels.map(k => vsum(byKey[k], "saved"));
  const recv = labels.map(k => vsum(byIn[k], "value"));
  const pc = {labels, byKey, byIn, spent, saved, recv, flow};
  chart?.destroy(); chart = null;
  for (const d of DONUTS) d.destroy();
  DONUTS = [];
  if (type === "donut") { drawDonuts(pc); renderPanel(pc); return; }
  const on = i => S.period === "all" || labels[i] === S.period;
  // расходы — синие (и скидки — зелёные пунктиром на линии), поступления — зелёные; вместе — расходы и поступления
  const sets = flow === "out" ? [["потрачено, zł", spent, "--c-blue"], ["сэкономлено на скидках, zł", saved, "--c-green", true]]
    : flow === "in" ? [["поступило, zł", recv, "--c-green"]] : [["потрачено, zł", spent, "--c-blue"], ["поступило, zł", recv, "--c-green"]];
  const dataset = ([label, data, v, minor]) => {
    const c = css(v);
    if (type === "bar") return {label, data, type: "bar", solid: c, borderRadius: {topLeft: 8, topRight: 8}, borderSkipped: "bottom",
      backgroundColor: x => on(x.dataIndex) ? fade(x.chart, c, .95, .5) : fade(x.chart, c, .5, .2),
      hoverBackgroundColor: x => fade(x.chart, c, 1, .6), glow: alpha(c, .4), glowBlur: 12};
    const few = labels.length <= 16;
    return {label, data, type: "line", solid: c, borderColor: c, borderWidth: minor ? 2 : 2.6, cubicInterpolationMode: "monotone",
      borderDash: minor ? [6, 5] : [], fill: minor ? false : "origin",
      backgroundColor: x => fade(x.chart, c, flow === "both" ? .2 : .32, 0),
      pointRadius: x => S.period !== "all" && labels[x.dataIndex] === S.period ? 6 : few && !minor ? 3 : 0,
      pointHoverRadius: 6, pointBackgroundColor: c, pointBorderColor: css("--panel"), pointBorderWidth: 2,
      glow: alpha(c, minor ? .25 : .45), glowBlur: 12};
  };
  const band = {id: "selBand", beforeDatasetsDraw(ch) {  // на линии выбранный период — светлой полосой
    if (type !== "line" || S.period === "all") return;
    const i = labels.indexOf(S.period), x = ch.scales.x, a = ch.chartArea;
    if (i < 0) return;
    const w = labels.length > 1 ? Math.abs(x.getPixelForValue(1) - x.getPixelForValue(0)) : a.width;
    const cx = x.getPixelForValue(i), c = ch.ctx;
    c.save(); c.fillStyle = alpha(css("--c-blue"), .1); c.fillRect(cx - w / 2, a.top, w, a.bottom - a.top); c.restore();
  }};
  chart = new Chart(document.getElementById("chart"), {
    type,
    data: {labels: labels.map(k => plabel(k, g)), datasets: sets.map(dataset)},
    plugins: [GLOW, band],
    options: {animation: chartAnim(), interaction: {mode: "index", intersect: false}, plugins: {legend: {position: "bottom", labels: solidLegend},
        tooltip: {callbacks: {title: items => pname(labels[items[0].dataIndex], g),
                              label: x => ` ${x.dataset.label.replace(", zł", "")}: ${zl(x.parsed.y)}`}}},
      scales: {x: softX({ticks: {autoSkip: true, maxRotation: 0}}), y: softY({beginAtZero: true})},
      onClick: (e, els, ch) => {
        // второй клик двойного нажатия (detail 2) пропускаем — его обрабатывает dblclick ниже. Chart.js склеивает
        // события в пределах кадра, поэтому двойной клик ловим не по времени, а настоящим событием браузера
        if (ch !== chart || !els.length || (e.native && e.native.detail > 1)) return;
        const k = labels[els[0].index];
        S.period = S.period === k ? "all" : k;  // повторный клик по выбранному — снова весь период
        ch.update("none");  // цвета и точки считаются от S.period — перерисовываем, а не пересоздаём график
        renderPanel(pc); animateEnter(document.getElementById("panel"));
      },
      onHover: (e, els) => { e.native.target.style.cursor = els.length ? "pointer" : "default"; }},
  });
  const ch = chart;
  ch.canvas.addEventListener("dblclick", ev => {  // двойной клик — уровень мельче
    const els = ch.getElementsAtEventForMode(ev, "index", {intersect: false}, true);
    if (ch === chart && els.length) drill(labels[els[0].index]);
  });
  renderPanel(pc);
}

let DONUTS = [];
function drawDonuts({labels, byKey, byIn, flow}) {  // кольцо: доли за выбранный период; при «расходы и поступления» — два
  const g = S.gran, all = S.period === "all";
  const rows = by => (all ? labels.flatMap(k => by[k]) : by[S.period] || []).map(visible).filter(Boolean);
  const when = all ? (CUR_RANGE ? CUR_RANGE.label : "всё время") : pname(S.period, g);
  const rings = [flow !== "in" && {word: "потрачено", ...donutParts(rows(byKey))},
                 flow !== "out" && {word: "поступило", ...donutParts(rows(byIn))}].filter(r => r && r.parts.length);
  const box = document.getElementById("donuts");
  if (!rings.length) { box.innerHTML = `<div class="empty">За этот период ничего нет.</div>`; return; }
  box.innerHTML = rings.map((r, j) => `<div class="donut">
      <div class="dwrap"><canvas id="donut${j}"></canvas>
        <div class="dcenter" id="dcenter${j}"></div></div>
      <div class="dlegend">${r.parts.map((p, i) => `<button type="button" class="dleg" data-dcat="${p.k ?? ""}" data-dj="${j}" data-di="${i}"
          ${p.k == null ? "disabled" : ""} title="${p.k == null ? "" : "показать только эту категорию"}"><i style="--c:${p.color}"></i>
          <span>${esc(p.name)}</span><b>${zl(p.v)}</b><em>${(p.v / (r.total || 1) * 100).toFixed(0)}%</em></button>`).join("")}</div>
    </div>`).join("");
  // наведение на долю или строку легенды показывает её в центре кольца (всплывающей плашки нет — она
  // перекрывала бы сумму в центре); увёл мышь — снова общая сумма
  const center = (j, i = null) => {
    const r = rings[j], p = i == null ? null : r.parts[i], el = document.getElementById("dcenter" + j);
    el.classList.toggle("part", !!p);
    el.innerHTML = p ? `<span class="dname"><i style="--c:${p.color}"></i>${esc(p.name)}</span><b>${zl(p.v)}</b>
        <span>${(p.v / (r.total || 1) * 100).toFixed(0)}% · ${r.word}</span>`
      : `<span>${r.word}</span><b>${zl(r.total)}</b><span>${esc(when)}</span>`;
  };
  rings.forEach((r, j) => {
    center(j);
    const ch = new Chart(document.getElementById("donut" + j), {
      type: "doughnut",
      data: {labels: r.parts.map(p => p.name), datasets: [{data: r.parts.map(p => +p.v.toFixed(2)), backgroundColor: r.parts.map(p => p.color),
        hoverBackgroundColor: r.parts.map(p => p.color), borderWidth: 0, borderRadius: 6, spacing: 3, hoverOffset: 8}]},
      plugins: [ARC_GLOW],
      options: {animation: chartAnim() && {animateRotate: true, duration: 800}, cutout: "70%", maintainAspectRatio: false,
        layout: {padding: 14}, plugins: {legend: {display: false}, tooltip: {enabled: false}},
        onClick: (e, els) => { const p = els.length && r.parts[els[0].index]; if (p && p.k != null) pickSlice(p.k); },
        onHover: (e, els) => {
          e.native.target.style.cursor = els.length && r.parts[els[0].index].k != null ? "pointer" : "default";
          center(j, els.length ? els[0].index : null);
        }},
    });
    DONUTS.push(ch);
  });
  for (const b of box.querySelectorAll(".dleg")) {  // наведение на строку легенды выдвигает долю и показывает её в центре
    const j = +b.dataset.dj, i = +b.dataset.di, ch = DONUTS[j];
    b.addEventListener("mouseenter", () => { ch.setActiveElements([{datasetIndex: 0, index: i}]); ch.update(); center(j, i); });
    b.addEventListener("mouseleave", () => { ch.setActiveElements([]); ch.update(); center(j); });
  }
}
function pickSlice(k) {  // клик по доле — фильтр по этой категории (у неё самой кольцо покажет подкатегории)
  S.F.cats = new Set([k === "none" ? "none" : +k]);
  CHART_ANIM = true; render();
}
function drill(k) {
  const g = S.gran;
  if (g === "day") {  // день — дальше дробить некуда: показываем его чеки
    S.period = k; S.view = "receipts"; CHART_ANIM = false; renderOverview();
    document.getElementById("panel").scrollIntoView({behavior: "smooth"}); return;
  }
  const [from, to] = pspan(k, g);
  S.crumbs.push({gran: g, range: S.range, period: k});
  S.gran = DRILL[g]; S.range = {from, to, label: pname(k, g)}; S.period = "all";
  CHART_ANIM = true; renderOverview(); animateEnter(document.getElementById("main"));
}
function backTo(i) {  // вернуться к уровню из «хлебных крошек»
  const c = S.crumbs[i];
  S.crumbs = S.crumbs.slice(0, i); S.gran = c.gran; S.range = c.range; S.period = c.period;
  CHART_ANIM = true; renderOverview(); animateEnter(document.getElementById("main"));
}

function renderPanel({labels, byKey, byIn, spent, saved, recv, flow}) {
  const g = S.gran, all = S.period === "all", i = labels.indexOf(S.period);
  const pick = by => (all ? labels.flatMap(k => by[k]) : by[S.period]).slice().sort((a, b) => a.date < b.date ? -1 : 1);
  const rsAll = pick(byKey), insAll = pick(byIn);  // все — для дерева категорий (там скрытые зачёркнуты)
  const rs = rsAll.map(visible).filter(Boolean), ins = insAll.map(visible).filter(Boolean);
  const sum = arr => all ? arr.reduce((a, b) => a + b, 0) : arr[i];
  const total = sum(spent), disc = sum(saved), got = sum(recv);
  const change = arr => !all && i > 0 && arr[i - 1] ? (arr[i] / arr[i - 1] - 1) * 100 : null;
  const diff = change(spent), diffIn = change(recv);
  const pct = (d, good) => d === null ? "" : `<span class="${(d > 0) === good ? "down" : "up"}">${d > 0 ? "+" : ""}${d.toFixed(0)}% ${PREV_WORD[g]}</span>`;
  const items = aggregate(rs), nItems = rs.reduce((s, r) => s + r.items.length, 0);
  const byDay = {}; for (const r of rs) byDay[r.date.slice(0, 10)] = (byDay[r.date.slice(0, 10)] || 0) + r.value;
  const topDay = Object.entries(byDay).sort((a, b) => b[1] - a[1])[0];
  const topBuy = rs.slice().sort((a, b) => b.value - a.value)[0];
  const first = rs.length ? rs[0].date.slice(0, 7) : null, last = rs.length ? rs[rs.length - 1].date.slice(0, 7) : null;
  // «в среднем за месяц» — делим на длину показанного периода в днях: неполный текущий месяц (или неделя
  // данных в первом) не занижает среднее, как было бы при делении на число месяцев
  const today = isoDay(new Date());
  const spanFrom = CUR_RANGE ? CUR_RANGE.from : pspan(labels[0], g)[0];
  const spanTo = CUR_RANGE ? CUR_RANGE.to : [pspan(labels[labels.length - 1], g)[1], today].sort()[0];
  const months = ((new Date(spanTo) - new Date(spanFrom)) / 864e5 + 1) / 30.44;
  // карточка видна и при выбранном месяце (так открывается «Обзор»): среднее — по всему показанному периоду,
  // а у завершённого месяца — насколько он выше или ниже среднего
  const avgCard = months < 1.5 ? "" : (() => {
    const whole = arr => arr.reduce((a, b) => a + b, 0);
    const avgOut = whole(spent) / months, avgIn = whole(recv) / months, inc = flow === "in";
    const per = g === "day" || g === "week" ? `<br>${PER_WORD[g]} ${zl(whole(inc ? recv : spent) / labels.length)}` : "";
    const span = `за ${months.toLocaleString("ru-RU", {maximumFractionDigits: 1})} мес.`;
    const d = !all && g === "month" && pspan(S.period, g)[1] < today && (inc ? avgIn : avgOut)
      ? ((inc ? got : total) / (inc ? avgIn : avgOut) - 1) * 100 : null;
    const vs = d === null ? "" : `<br><span class="${(d > 0) === inc ? "down" : "up"}">этот месяц ${d > 0 ? "+" : ""}${d.toFixed(0)}% к среднему</span>`;
    return inc ? `<div class="card">в среднем за месяц<b class="save">${zl(avgIn)}</b>${span}${per}${vs}</div>`
      : `<div class="card">в среднем за месяц<b>${zl(avgOut)}</b>${flow === "both" ? `поступает ${zl(avgIn)}` : span}${per}${vs}</div>`;
  })();
  const title = !all ? pname(S.period, g) : CUR_RANGE ? esc(CUR_RANGE.label)
    : `всё время <span class="muted">(${first ? monthName(first) : ""} – ${last ? monthName(last) : ""})</span>`;
  const dayCard = g === "day" && !all
    ? `<div class="card">самая крупная покупка<b>${topBuy ? zl(topBuy.value) : "—"}</b>${topBuy ? esc(topBuy.merchant) : "покупок нет"}</div>`
    : `<div class="card">самый дорогой день<b>${topDay ? `${topDay[0].slice(8, 10)}.${topDay[0].slice(5, 7)}` : "—"}</b>${topDay ? zl(topDay[1]) : ""}</div>`;
  document.getElementById("panel").innerHTML = `
    <div class="nav"><button ${!all && i > 0 ? "" : "disabled"} data-period="${labels[i - 1]}" title="предыдущий период">‹</button>
      <h2>${title}</h2>
      <button ${!all && i < labels.length - 1 ? "" : "disabled"} data-period="${labels[i + 1]}" title="следующий период">›</button>
      <button class="chip ${all ? "on" : ""}" data-period="all" style="margin-left:auto">${CUR_RANGE ? "весь период" : "всё время"}</button></div>
    <div class="cards">${flow === "in" ? incomeCards(ins, got, diffIn, labels, all, pct) + avgCard
      : `${flow === "both" ? `<div class="card">поступило<b class="save">${zl(got)}</b>${pct(diffIn, true)}</div>` : ""}
      <div class="card">потрачено<b>${zl(total)}</b>${pct(diff, false)}</div>
      ${flow === "both" ? `<div class="card">разница<b class="${got - total >= 0 ? "save" : "up"}">${got - total >= 0 ? "+" : "−"}${zl(Math.abs(got - total))}</b>поступило минус потрачено</div>` : ""}
      ${avgCard}
      <div class="card">сэкономлено<b class="save">${zl(disc)}</b>${(disc / (total + disc) * 100 || 0).toFixed(0)}% от цены без скидок</div>
      <div class="card">покупок<b>${rs.length}</b>${rs.length ? `средняя ${zl(total / rs.length)}` : ""}</div>
      ${flow === "out" ? `<div class="card">позиций<b>${nItems}</b>разных товаров: ${items.length}</div>${dayCard}` : ""}`}
    </div>
    <div class="tabs" data-key="view">${[["cats", "категории"], ["items", flow === "in" ? "поступления" : "товары"], ["receipts", flow === "in" ? "по отправителям" : "чеки"]].map(([k, n]) =>
      `<button class="${S.view === k ? "on" : ""}" data-view="${k}">${n}</button>`).join("")}</div>
    <div id="body">${!rsAll.length && !insAll.length ? `<p class="muted">В этот период ничего нет.</p>`
      : S.view === "cats" ? catsTable(rsAll, insAll)
      : (rs.length ? (S.view === "items" ? itemsTable(items) : receiptsList(rs)) : "")
        + (ins.length ? `${rs.length ? `<h3 style="margin:18px 0 6px">Поступления</h3>` : ""}${S.view === "items" ? incomeTable(ins) : incomeBySender(ins)}` : "")}</div>`;
  placeIndicators();
}

function aggregate(rs) {
  const agg = {};
  for (const r of rs) for (const it of r.items) {
    const a = agg[it.name.toLowerCase()] ??= {name: it.name, times: 0, qty: 0, spent: 0, discount: 0, cats: {}, srcs: {},
                                               merchants: new Set(), occ: []};
    a.occ.push({p: r, it});
    a.times += 1; a.qty += it.qty || 1; a.spent += net(it); a.discount += it.discount || 0;
    a.cats[it.cat ?? ""] = (a.cats[it.cat ?? ""] || 0) + 1; a.srcs[it.cat_src ?? ""] = 1; a.merchants.add(r.merchant);
  }
  for (const a of Object.values(agg)) {
    const [top] = Object.entries(a.cats).sort((x, y) => y[1] - x[1]);
    a.cat = top[0] === "" ? null : +top[0];
    a.mixed = Object.keys(a.cats).length > 1;
    a.catName = a.cat == null ? "" : CAT[a.cat].path;
  }
  return Object.values(agg);
}

const SRC_LABEL = {manual: "вручную", note: "по комментарию", code: "правило", rule: "правило", keyword: "словарь",
                   group: "группа магазина", section: "отдел магазина", merchant: "по магазину", refund: "как у покупки",
                   shop: "правило магазина",
                   bank: "по выписке"};
// примечание покупки; «проверить по банку» уже не нужно, если списание нашлось
const pnote = p => (p.bt ? (p.note || "").replace(/регистрация платежа без подтверждения — проверить по банку(; )?/, "")
                         : p.note || "").trim();
// возвраты рядом с покупкой: у покупки — что и когда вернули, у возврата — по какой покупке
function refundInfo(p) {
  const out = [];
  for (const r of REFUNDS[p.id] || [])
    out.push(`<div class="save" style="font-size:.85em">↩ возврат ${zl(-r.total)} · ${ddmmyy(r.date)}</div>`);
  if (p.refund_of) {
    const o = D.purchases.find(x => x.id === p.refund_of);
    out.push(`<div class="save" style="font-size:.85em">↩ возврат по покупке ${o ? `«${esc(o.merchant)}» ${ddmmyy(o.date)} на ${zl(o.total)}` : ""}</div>`);
  }
  return out.join("");
}
const photoLinks = p => (p.photos || []).map((f, i) => ` · <a href="/file/${esc(f.path)}" target="_blank"
  title="фото бумажного чека совпало с этой покупкой по сумме и времени">📷 фото чека${p.photos.length > 1 ? " " + (i + 1) : ""}</a>`).join("")
  + ((p.photos || []).length ? ` <span class="st" style="--c:var(--green)">✓ совпало с фото</span>` : "");
const noteHtml = it => it.note ? `<div class="note">${esc(it.note)}</div>` : "";
const noteBtn = (pid, it) => `<button class="mini${it.note ? " has" : ""}" type="button" data-note-pid="${esc(pid)}" data-note-line="${it.line}"
  data-note-name="${esc(it.name)}" data-note-text="${esc(it.note || "")}" title="${it.note ? "изменить комментарий" : "комментарий: что купил"}">${ICON.note}</button>`;
const delBtn = p => p.source === "bank" ? "" :
  `<button class="mini danger" type="button" data-hide="${esc(p.id)}" title="удалить покупку (незавершённая оплата, дубль)">${ICON.trash}</button>`;
async function editNote(d) {
  const v = await promptBox({title: "Что купил?", value: d.noteText, placeholder: "например: кофе и круассан",
    text: `«${d.noteName}». Комментарий видишь только ты; по нему определится категория, если ты не ставил её вручную.`, ok: "Сохранить"});
  if (v === null) return;
  const j = await post("/api/item-note", {purchase_id: d.notePid, line: +d.noteLine, note: v});
  BANK = null; const y = scrollY; await load(); scrollTo(0, y);
  toast(!v.trim() ? "Комментарий удалён" : j.by_note ? `Сохранено · категория по комментарию: ${j.category.replaceAll("/", " › ")}` : "Комментарий сохранён");
}
async function hidePurchase(id) {
  const p = D.purchases.find(x => x.id === id);
  if (!p) return;
  const ok = await confirmBox({title: "Удалить покупку?", ok: "Удалить", danger: true,
    text: `${ddmmyy(p.date)} · ${p.merchant} · ${zl(p.total)}. Она пропадёт из бюджета и не вернётся при обновлениях. ` +
          `Вернуть можно сразу кнопкой в уведомлении или потом в «Настройках».`});
  if (!ok) return;
  if (p.bt) toast("Удаляю и пересверяю с банком…", {spin: true, ms: 90000});
  try { await post("/api/purchase/hide", {id}); } catch (e) { toast("Не получилось: " + e.message); return; }
  BANK = null; WALLET = null; const y = scrollY; await load(); scrollTo(0, y);
  toast("Покупка удалена", {action: "Вернуть", onAction: () => restorePurchase(id)});
}
async function restorePurchase(id) {
  toast("Возвращаю: пересобираю чеки и письма, сверяю с банком…", {spin: true, ms: 120000});
  try { await post("/api/purchase/restore", {id}); } catch (e) { toast("Не получилось: " + e.message); return; }
  BANK = null; WALLET = null; const y = scrollY; await load(); scrollTo(0, y);
  toast("Покупка возвращена");
}
function itemsTable(items, extraCol = null) {
  const k = S.sort.key, d = S.sort.dir;
  items.sort((a, b) => (typeof a[k] === "string" ? a[k].localeCompare(b[k]) : a[k] - b[k]) * d);
  const col = (key, title, cls = "n") => `<th class="${cls}" data-sort="${key}">${title}${k === key ? (d < 0 ? " ▾" : " ▴") : ""}</th>`;
  return `<table><tr>${col("name", "товар", "")}${col("catName", "категория", "")}${extraCol ? `<th>${extraCol[0]}</th>` : ""}
      ${col("times", "раз")}${col("qty", "кол-во")}${col("spent", "потрачено")}${col("discount", "скидка")}</tr>` +
    items.map(a => { const open = S.openOcc.has(a.name.toLowerCase()); return `<tr class="${PENDING.has("same|" + a.name) ? "pending" : ""}${open ? " opened" : ""}">
      <td><button class="cellbtn" data-occ="${esc(a.name.toLowerCase())}" title="где и когда покупалось">${open ? "▾" : "▸"} ${esc(a.name)}</button>
        ${a.occ.length === 1 ? noteHtml(a.occ[0].it) : ""}</td>
      <td>${catPick(a.cat, "same", {name: a.name})}${a.occ.length === 1 ? noteBtn(a.occ[0].p.id, a.occ[0].it) : ""}
        <span class="src">${a.mixed ? "разные!" : SRC_LABEL[Object.keys(a.srcs)[0]] || ""}</span></td>
      ${extraCol ? `<td>${extraCol[1](a)}</td>` : ""}
      <td class="n">${a.times}</td><td class="n">${+a.qty.toFixed(3)}</td>
      <td class="n">${zl(a.spent)}</td><td class="n save">${a.discount ? "−" + zl(a.discount) : ""}</td></tr>` +
      (open ? occRow(a, extraCol ? 7 : 6) : ""); }).join("") + "</table>";
}

// ---------- случаи одной позиции: когда, где, какая операция в банке, что ещё покупалось в эти дни
const BANK_TYPE_LABEL = [[/RETURN/, "возврат"], [/^CARD-PAYMENT/, "оплата картой"], [/^MOBILE-PAYMENT-POS/, "BLIK-платёж"],
  [/^MOBILE-PAYMENT-C2C/, "BLIK на телефон"], [/^CARD-ATM/, "снятие в банкомате"], [/^CASH-IN/, "взнос наличных"],
  [/^US-TRANSFER/, "перевод в налоговую"], [/TRANSFER/, "перевод"], [/FEE/, "комиссия банка"], [/^INTEREST/, "проценты"]];
const bankType = tp => (BANK_TYPE_LABEL.find(([re]) => re.test(tp || "")) || [null, tp || ""])[1];
const ddmmyy = d => `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(2, 4)}`;
let DAYIDX = null, DAYIDX_D = null;
function nearby(p) {  // покупки за этот и два предыдущих дня: банк списывает BLIK и карту с задержкой
  if (DAYIDX_D !== D) { DAYIDX = {}; DAYIDX_D = D; for (const q of D.purchases) (DAYIDX[q.date.slice(0, 10)] ??= []).push(q); }
  const d0 = new Date(p.date.slice(0, 10) + "T12:00:00"), out = [];
  for (let k = 0; k <= 2; k++) {
    const d = new Date(d0 - k * 864e5).toISOString().slice(0, 10);
    for (const q of DAYIDX[d] || []) if (q.source !== "bank" && q.items.some(isExp)) out.push(q);  // чеки и заказы
  }
  return out.length ? out.slice(0, 6).map(q => `<div class="muted" style="white-space:nowrap">${ddmmyy(q.date)} ${esc(q.merchant.slice(0, 24))}
      ${zl(q.total)}</div>`).join("") + (out.length > 6 ? `<div class="muted">… ещё ${out.length - 6}</div>` : "")
    : `<span class="muted">чеков и заказов нет</span>`;
}
function occRow(a, cols) {
  const occ = a.occ.slice().sort((x, y) => x.p.date < y.p.date ? 1 : -1);
  const anyBank = occ.some(o => o.p.source === "bank");
  return `<tr class="occ${a.name.toLowerCase() === S.justOpened ? " fresh" : ""}"><td colspan="${cols}"><table class="occt"><tr><th>дата</th><th>где / откуда</th><th>операция в банке</th>
      ${anyBank ? `<th>чеки и заказы в эти дни</th>` : ""}<th>категория</th><th class="n">сумма</th></tr>` +
    occ.map(({p, it}) => {
      const b = p.bank, time = p.date.slice(11, 16);
      // у операций без чека «магазин» и «точка» — это и есть описание из выписки: не повторяем
      const fromBank = p.source === "bank", where = fromBank && /^[\d\s]+$/.test(p.merchant) ? "магазин не указан" : p.merchant;
      return `<tr class="${PENDING.has(`item|${p.id}|${it.line}`) ? "pending" : ""}">
        <td style="white-space:nowrap">${ddmmyy(p.date)}${time && time !== "12:00" ? ` <span class="muted">${time}</span>` : ""}${delBtn(p)}</td>
        <td><div class="who">${ava(where)}<div>${esc(where)}${p.store && !fromBank ? `<div class="muted">${esc(p.store)}</div>` : ""}
          <div class="muted">${esc(SOURCES[p.source] || p.source)} · ${esc(D.payments[p.pay ?? "unknown"] || p.pay || "")}${p.card ? " •" + esc(p.card) : ""}${
            p.file ? ` · <a href="/file/${esc(p.file)}" target="_blank">${p.source === "email" ? "✉ письмо" : "📷 фото"}</a>` : ""}${photoLinks(p)}${
            (p.n_items || p.items.length) > 1 ? ` · в чеке ${p.n_items || p.items.length} поз. на ${zl(p.total)}` : ""}</div>${noteHtml(it)}${refundInfo(p)}
          ${pnote(p) ? `<div class="warn" style="font-size:.8em">${esc(pnote(p))}</div>` : ""}</div></div></td>
        <td>${b ? `${esc(bankType(b.type))}<div class="muted">${esc([b.who, b.desc].filter(Boolean).join(" · "))}</div>${
            b.date !== p.date.slice(0, 10) ? `<div class="muted">списано ${ddmmyy(b.date)}</div>` : ""}`
          : `<span class="muted">${p.pay === "cash" ? "наличные" : "нет в выписке PKO"}</span>`}</td>
        ${anyBank ? `<td>${p.source === "bank" ? nearby(p) : ""}</td>` : ""}
        <td style="white-space:nowrap">${catPick(it.cat, "item", {name: it.name, pid: p.id, line: it.line})}${noteBtn(p.id, it)}</td>
        <td class="n">${zl(net(it))}</td></tr>`;
    }).join("") + `</table></td></tr>`;
}

// карточки периода, когда на графике только поступления
function incomeCards(ins, got, diffIn, labels, all, pct) {
  const by = {}; for (const r of ins) by[r.merchant] = (by[r.merchant] || 0) + r.value;
  const top = Object.entries(by).sort((a, b) => b[1] - a[1])[0];
  return `<div class="card">поступило<b class="save">${zl(got)}</b>${pct(diffIn, true)}</div>
    <div class="card">поступлений<b>${ins.length}</b>${ins.length ? `среднее ${zl(got / ins.length)}` : ""}</div>
    <div class="card">больше всего<b>${top ? zl(top[1]) : "—"}</b>${top ? esc(top[0]) : ""}</div>`;
}
// поступления списком: дата, от кого, описание, категория (правка — как у операций на вкладке «Банк»), сумма
function incomeTable(ins) {
  return `<table><tr><th>дата</th><th>от кого</th><th>описание</th><th>категория</th><th class="n">сумма</th></tr>` +
    ins.slice().sort((a, b) => a.date < b.date ? 1 : -1).map(r => { const x = r.inc;
      return `<tr class="${PENDING.has("tx|" + x.id) ? "pending" : ""}"><td style="white-space:nowrap">${ddmmyy(x.date)}</td>
        <td><div class="who">${ava(x.who || x.desc)}<div>${esc(x.who || "—")}<div class="muted">${esc(bankType(x.type))}</div></div></div></td>
        <td>${esc(x.desc)}</td><td>${catPick(x.cat, "tx", {tx: x.id})}</td><td class="n save">+${zl(x.amount)}</td></tr>`; }).join("") + "</table>";
}
function incomeBySender(ins) {  // «по отправителям»: от кого сколько пришло
  const by = {};
  for (const r of ins) { const a = by[r.merchant] ??= {n: 0, v: 0, cats: new Set(), last: ""}; a.n++; a.v += r.value; a.cats.add(r.inc.cat);
    if (r.date > a.last) a.last = r.date; }
  return `<table><tr><th>от кого</th><th>категория</th><th class="n">раз</th><th class="n">последнее</th><th class="n">сумма</th></tr>` +
    Object.entries(by).sort((a, b) => b[1].v - a[1].v).map(([who, a]) => `<tr><td><div class="who">${ava(who)}<span>${esc(who)}</span></div></td>
      <td>${a.cats.size > 1 ? "разные" : esc(catLabel([...a.cats][0]))}</td><td class="n">${a.n}</td><td class="n">${ddmmyy(a.last)}</td>
      <td class="n save">+${zl(a.v)}</td></tr>`).join("") + "</table>";
}

let CATS_RS = [[], []], CATS_BRANCHES = [];
function catsTable(rs, ins = []) {  // расходы и поступления — отдельными деревьями (у каждого своя доля)
  CATS_RS = [rs, ins]; CATS_BRANCHES = [];
  const parts = [];
  if (rs.length) parts.push(catTree(rs, ins.length ? "Расходы" : null, "потрачено"));
  if (ins.length) parts.push(catTree(ins, rs.length ? "Поступления" : null, "поступило"));
  return `<div class="cattools"><button class="chip" data-catall="1">развернуть всё</button>
      <button class="chip" data-catall="0">свернуть всё</button></div>` + parts.join("");
}
function catTree(rs, title, word) {  // категория -> подкатегории -> товары (или поступления); каждая ветка разворачивается
  const T = {}, roots = new Set(); let all = 0;  // all — только видимое (скрытое глазом в долю не входит)
  const node = k => (T[k] ??= {spent: 0, full: 0, items: [], kids: new Set()});
  for (const r of rs) for (const it of r.items) {
    const v = net(it), hid = hiddenCat(it.cat); if (!hid) all += v;
    let k = it.cat == null || !CAT[it.cat] ? "none" : String(it.cat);
    node(k).items.push({r, it});
    for (;;) {
      node(k).full += v; if (!hid) node(k).spent += v;
      const par = k === "none" ? null : CAT[k].parent_id;
      if (par == null) { roots.add(k); break; }
      node(String(par)).kids.add(k); k = String(par);
    }
  }
  CATS_BRANCHES.push(...Object.keys(T).filter(k => T[k].kids.size));
  const name = k => k === "none" ? "⚠ Неопознанные" : CAT[k] ? CAT[k].name : "?";
  // доля отрицательной суммы (одни возвраты) — без полоски: отрицательная ширина в CSS не работает и рисует полную
  const bar = v => `<div class="bar" style="width:${Math.max(0, v / (all || 1) * 100).toFixed(1)}%"></div>`;
  const bySpent = (a, b) => T[b].full - T[a].full;
  const rows = (k, depth, fresh) => {
    const n = T[k], open = S.openCats.has(k), kids = [...n.kids].sort(bySpent);
    const self = S.hidden.has(k), hid = self || hiddenCat(k === "none" ? null : +k);  // сама скрыта или скрыт родитель
    const label = depth ? esc(name(k)) : `<b>${esc(name(k))}</b>`;
    const v = hid ? n.full : n.spent;  // скрытая — полная сумма (зачёркнута); видимая — без скрытых подкатегорий
    let h = `<tr class="crow${open ? " opened" : ""}${fresh ? " fresh" : ""}${hid ? " hid" : ""}"><td style="padding-left:${12 + depth * 26}px">
        <button class="eye${self ? " off" : ""}" data-eye="${k}" type="button" title="${self ? "вернуть в график и сводку"
          : hid ? "скрыта вместе с родительской категорией" : "убрать из графика и сводки"}"${hid && !self ? " disabled" : ""}>${self ? ICON.eyeOff : ICON.eye}</button><button
          class="cellbtn ctog" data-cattog="${k}" title="${open ? "свернуть" : "развернуть"}"><span class="chev${open ? " on" : ""}"></span>${label}</button>
        ${kids.length ? "" : `<span class="src">${n.items.length} поз.</span>`}</td>
      <td class="n">${depth ? zl(v) : `<b>${zl(v)}</b>`}</td><td class="n">${hid || v < 0 ? "—" : (v / (all || 1) * 100).toFixed(0) + "%"}</td>
      <td>${hid ? "" : bar(v)}</td></tr>`;
    if (!open) return h;
    const fr = S.justOpenedCat === k;
    h += kids.map(c => rows(c, depth + 1, fr)).join("");
    if (n.items.length) {  // позиции прямо в этой категории: таблица товаров с правкой категории и раскрытием случаев
      const buys = n.items.filter(({r}) => !r.inc), incs = n.items.filter(({r}) => r.inc).map(({r}) => r);
      const agg = aggregate(buys.map(({r, it}) => ({...r, items: [it], n_items: r.items.length})));
      h += `<tr class="occ${fr ? " fresh" : ""}"><td colspan="4" style="padding-left:${26 + depth * 26}px">
        ${kids.length ? `<div class="muted" style="margin:2px 0 4px">прямо в «${esc(name(k))}», без подкатегории</div>` : ""}${
        buys.length ? itemsTable(agg) : ""}${incs.length ? incomeTable(incs) : ""}</td></tr>`;
    }
    return h;
  };
  return `${title ? `<h3 style="margin:14px 0 6px">${title}</h3>` : ""}
    <table class="cats"><tr><th>категория</th><th class="n">${word}</th><th class="n">доля</th><th style="width:35%"></th></tr>` +
    [...roots].sort(bySpent).map(k => rows(k, 0, false)).join("") + "</table>";
}

function receiptsList(rs) {
  const here = new Set(rs.map(r => r.id));
  const refundRows = r => (REFUNDS[r.id] || []).map(x => `<tr class="refund"><td>↩ ${esc(x.items[0] ? x.items[0].name : "возврат")}
      <div class="muted">${ddmmyy(x.date)} · вернули на карту</div></td><td>${catPick(x.items[0] ? x.items[0].cat : null, "item",
      {name: x.items[0] ? x.items[0].name : "", pid: x.id, line: 1})}</td><td></td><td></td><td class="n save">${zl(x.total)}</td><td></td></tr>`).join("");
  const refunded = r => (REFUNDS[r.id] || []).reduce((s, x) => s - x.total, 0);
  return rs.filter(r => !(r.refund_of && here.has(r.refund_of))).map(r => `<details ${r.items.some(it => PENDING.has(`item|${r.id}|${it.line}`)) ? "open" : ""}><summary>${ava(r.merchant)}<b>${r.date.slice(8, 10)}.${r.date.slice(5, 7)}.${r.date.slice(2, 4)} ${r.date.slice(11, 16)}</b>
      · ${zl(r.value)}${r.saved ? ` · <span class="save">скидки −${zl(r.saved)}</span>` : ""}
      · ${r.items.length} поз. · ${esc(D.payments[r.pay ?? "unknown"])}${r.card ? " •" + esc(r.card) : ""}
      <span class="muted">${esc(r.merchant)} · ${esc(r.store)}</span>
      ${r.file ? ` · <a href="/file/${esc(r.file)}" target="_blank">${r.source === "email" ? "✉ письмо" : "📷 фото"}</a>` : ""}${photoLinks(r)}
      ${pnote(r) ? ` · <span class="warn">${esc(pnote(r))}</span>` : ""}${refunded(r) ? ` · <span class="save">↩ возврат −${zl(refunded(r))}</span>` : ""}${
      r.refund_of ? refundInfo(r) : ""}${delBtn(r)}</summary>
    <table><tr><th>товар</th><th>категория</th><th class="n">кол-во</th><th class="n">цена</th><th class="n">сумма</th><th class="n">скидка</th></tr>` +
    r.items.map(it => `<tr><td>${esc(it.name)}${noteHtml(it)}</td>
      <td>${catPick(it.cat, "item", {name: it.name, pid: r.id, line: it.line})}${noteBtn(r.id, it)}
        <span class="src">${SRC_LABEL[it.cat_src] || ""}</span></td>
      <td class="n">${+(it.qty || 1).toFixed(3)}</td><td class="n">${it.unit_price != null ? zl(it.unit_price) : ""}</td>
      <td class="n">${zl(it.amount)}</td><td class="n save">${it.discount ? "−" + zl(it.discount) : ""}</td></tr>`).join("") +
    refundRows(r) + "</table></details>").join("");
}
