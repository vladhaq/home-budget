// Страница «Скидки»: газетки Lidl и Kaufland (вкладки), постоянные позиции, поиск, напоминания,
// цены по твоим чекам (личная инфляция, история) и по газеткам.
let DEALS = null, DSEARCH = null, DINFL = null, DHIST = null, dCharts = [];
const STORE_NAME = {lidl: "Lidl", kaufland: "Kaufland"};
let DCMP = null;
S.dealStore = (() => { try { return localStorage.getItem("budget-deal-store") || "lidl"; } catch (e) { return "lidl"; } })();
const sq = () => "store=" + S.dealStore;
const ddmm = d => d ? `${d.slice(8, 10)}.${d.slice(5, 7)}` : "";
const pct = v => `<span class="${v > 0 ? "up" : v < 0 ? "save" : "muted"}">${v > 0 ? "+" : ""}${v.toLocaleString("ru-RU")}%</span>`;
// слова названия: «PrimoSkyrNaturalny150g» -> primo skyr naturalny 150g (как в словаре категорий)
const nameWords = s => foldTxt(String(s).replace(/([a-ząćęłńóśźż])([A-ZĄĆĘŁŃÓŚŹŻ])/g, "$1 $2")
  .replace(/([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż])(\d)/g, "$1 $2").replace(/(\d)([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{2})/g, "$1 $2"))
  .split(/[^a-z0-9а-я]+/).filter(Boolean);  // и цифры отдельно: «Gouda300gpl» -> gouda 300 gpl
const wordHit = (w, tok) => { const st = tok.length >= 4 && /[aeiouy]$/.test(tok) ? tok.slice(0, -1) : tok;
                              return w.startsWith(st) && w.length - st.length <= 3; };
function mine(q, shop = STORE_NAME[S.dealStore]) {  // этот товар в твоих чеках этого магазина: сколько раз, почём обычно
  const toks = foldTxt(q).split(/\s+/).filter(x => x.length > 2);
  if (!toks.length) return null;
  const hits = [];
  for (const p of D.purchases) if (!shop || p.merchant === shop) for (const it of p.items) {
    const ws = nameWords(it.name);
    if (toks.every(tk => ws.some(w => wordHit(w, tk)))) {
      const qty = it.qty || 1, price = net(it) / qty, weighed = !Number.isInteger(qty);
      const pu = weighed ? {v: price, u: "kg"} : perUnit(it.name, price);
      hits.push({date: p.date, merchant: p.merchant, qty, price, pu});
    }
  }
  if (!hits.length) return null;
  hits.sort((a, b) => a.date < b.date ? -1 : 1);
  const last = hits[hits.length - 1], pcs = hits.every(h => Number.isInteger(h.qty));
  const pus = hits.filter(h => h.pu), u = pus.length ? pus[pus.length - 1].pu.u : null, same = pus.filter(h => h.pu.u === u);
  // «обычно» — медиана: одна покупка по акции (или пачка другого размера) не сдвигает её, в отличие от среднего
  return {n: hits.length, last, avg: median(hits.map(h => h.price)), pcs,
          avgPU: same.length ? median(same.map(h => h.pu.v)) : null, puUnit: u};
}
function median(xs) {
  const s = xs.slice().sort((a, b) => a - b), k = s.length >> 1;
  return s.length % 2 ? s[k] : (s[k - 1] + s[k]) / 2;
}
// цена за кг/л по фасовке в названии: «Skyr 150g» 4,49 -> 29,93 zł/kg (как на сервере, core/prices.py)
function perUnit(name, price) {
  const s = foldTxt(String(name).replace(/([A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż])(\d)/g, "$1 $2").replace(/(\d)([A-Za-z]{1,2})/g, "$1 $2"));
  const m = s.match(/(\d+(?:[.,]\d+)?) ?(?:x ?(\d+(?:[.,]\d+)?) ?)?(kg|g|ml|l)(?:pl)?\b/);  // «300gpl» — граммы, в пластах
  if (!m) return null;
  const a = parseFloat(m[1].replace(",", ".")) * (m[2] ? parseFloat(m[2].replace(",", ".")) : 1);
  const q = m[3] === "g" || m[3] === "ml" ? a / 1000 : a;
  return q ? {v: price / q, u: m[3] === "g" || m[3] === "kg" ? "kg" : "l"} : null;
}
const mineText = m => m ? `<span class="muted">· в ${STORE_NAME[S.dealStore]} ты покупал ${m.n} раз, последний ${ddmmyy(m.last.date)} по ${zl(m.last.price)},
  обычно ${zl(m.avg)}${m.pcs ? "" : " за кг/шт."}</span>` : `<span class="muted">· в чеках ${STORE_NAME[S.dealStore]} не встречался</span>`;
function dealCard(d, q, m) {
  const price = d.price != null ? (d.approx ? "≈" : "") + zl(d.price) : d.discount ? `−${d.discount}%` : "цена не распознана";
  // сравниваем цену за кг/л (пачки разного размера); без фасовки — за штуку, если и у тебя покупки штучные без веса
  const byUnit = m && m.avgPU && d.unit_price && d.unit === m.puUnit;
  const cmp = byUnit ? d.unit_price / m.avgPU - 1
    : m && m.pcs && !m.avgPU && !d.unit_price && d.price != null && !d.approx ? d.price / m.avg - 1 : null;
  const usual = byUnit ? `${zl(m.avgPU)} за ${m.puUnit}` : m ? zl(m.avg) : "";
  return `<div class="deal ${d.upcoming ? "soon" : "now"}">
    <div class="dtop"><span class="st" style="--c:var(${d.upcoming ? "--orange" : "--green"})">${esc(d.status)}</span>
      ${d.discount ? `<span class="dbadge">−${d.discount}%</span>` : ""}</div>
    <div class="dtitle">${esc(d.title)}</div>
    <div class="dprice"><b>${price}</b>${d.old_price != null && d.old_price !== d.price ? `<s>${zl(d.old_price)}</s>` : ""}
      ${d.size ? ` <span class="muted">${esc(d.size)}</span>` : ""}</div>
    ${d.unit_price ? `<div class="muted">${zl(d.unit_price)} за ${esc(d.unit)}${d.unit_regular && d.unit_regular !== d.unit_price ? ` (обычно ${zl(d.unit_regular)})` : ""}</div>` : ""}
    ${d.approx ? `<div class="muted">≈ — цена рассчитана по цене за кг/л из газетки, проверь на странице</div>` : ""}
    ${d.no_discount ? `<div class="muted">${d.prev ? `в газетке от ${ddmm(d.prev.start)} было ${zl(d.prev.price)}` : "скидки нет — обычная цена из газетки"}</div>` : ""}
    ${cmp != null && cmp < -0.03 ? `<div class="save">${byUnit ? "за " + m.puUnit + " " : ""}дешевле, чем ты обычно платишь (${usual}), на ${Math.round(-cmp * 100)}%</div>`
      : cmp != null && cmp > 0.03 ? `<div class="muted">ты обычно платишь меньше: ${usual}</div>` : ""}
    ${(d.conditions || []).map(c => `<div class="cond">• ${esc(c)}</div>`).join("")}
    <div class="dfoot"><a href="${esc(d.url)}" target="_blank">газетка, стр. ${d.page} ↗</a>
      ${d.reminded ? `<span class="muted">🔔 напоминание стоит</span>`
        : `<button class="chip" data-remind="${esc(d.key)}" data-q="${esc(q)}" title="сообщение в Избранное Telegram накануне начала акции">🔔 напомнить</button>`}</div>
  </div>`;
}
const dealGrid = (deals, q) => { const m = mine(q); return `<div class="dgrid">${deals.map(d => dealCard(d, q, m)).join("")}</div>`; };
const storeTabs = () => {
  const sum = D.meta.deals && D.meta.deals.stores || {};
  return `<div class="tabs" data-key="dstore">${Object.entries(STORE_NAME).map(([k, n]) => `<button class="${S.dealStore === k ? "on" : ""}" data-dstore="${k}">${n}${
    sum[k] && sum[k].deals ? ` <span class="badge">${sum[k].deals}</span>` : ""}</button>`).join("")}<button class="${S.dealStore === "compare" ? "on" : ""}"
    data-dstore="compare">Где выгоднее</button></div>`;
};
async function renderDeals() {
  if (S.dealStore === "compare") return renderCompare();
  if (!DEALS || DEALS.store !== S.dealStore) { DEALS = await (await fetch("/api/deals?" + sq())).json(); DINFL = null; }
  const X = DEALS, tg = X.telegram, name = STORE_NAME[S.dealStore], sum = D.meta.deals && D.meta.deals.stores || {};
  document.getElementById("main").innerHTML = `
    ${storeTabs()}
    <div class="updbox">
      <span class="big">Газетки ${name}: <b>${X.flyers.length}</b> · обновлены ${X.updated ? dtf(X.updated.slice(0, 16)) : "ещё не скачивались"}</span>
      <button class="btn" data-dupdate="1">Обновить газетки</button>
      <span class="muted">${tg.ready ? `🔔 напоминать в Избранное Telegram накануне акции в
          <input type="time" id="rat" value="${esc(tg.remind_at)}"> <button class="chip" data-rat="1">сохранить</button>`
        : `🔕 напоминания в Telegram — после первого входа: ${cmd("python budget.py telegram")}`}</span>
    </div>
    <div class="flyers">${X.flyers.map(f => `<span class="chip">${esc(f.name)} · ${ddmm(f.start)}–${ddmm(f.end)}</span>`).join("")}</div>
    <p class="muted">Газетки обоих магазинов и напоминания по постоянным позициям обновляются сами в «обновить всё» (шаг «Газетки»).${
      S.dealStore === "kaufland" ? ` У Kaufland цены в газетке разбросаны вокруг описания товара — где цена не нашлась рядом, она
      рассчитана по цене за кг/л (≈); всегда можно открыть страницу газетки.` : ""}</p>

    <h2>Постоянные позиции</h2>
    <div class="updbox"><span class="wlist">${X.positions.map(p => `<span class="wchip">${esc(p.q)}<button type="button" data-wrm="${esc(p.q)}"
        title="убрать из постоянных">×</button></span>`).join("") || `<span class="muted">пока пусто — добавь товары, за скидками на которые следить</span>`}</span>
      <span><input id="wq" placeholder="добавить: ser gouda, maslo…"> <button class="chip" data-wadd="1">+ добавить</button></span></div>
    <p class="muted">У каждого магазина свой список.</p>
    <div id="dpos">${X.positions.map(p => `<div class="poshead"><b>${esc(p.q)}</b>
        <span class="${p.deals.length ? "save" : "muted"}">${p.deals.length ? `акций: ${p.deals.length}` : `в газетках ${name} нет`}</span>${mineText(mine(p.q))}</div>
      ${p.deals.length ? dealGrid(p.deals, p.q) : ""}`).join("")}</div>

    <h2>Найти в газетках ${name}</h2>
    <div class="updbox"><input id="dq" style="flex:1;min-width:220px" value="${esc(DSEARCH && DSEARCH.store === S.dealStore ? DSEARCH.q : "")}"
        placeholder="товар по-польски, диакритику можно не ставить: maslo, kawa, piers z kurczaka">
      <button class="btn" data-dsearch="1">Найти</button></div>
    <div id="dres"></div>

    <h2>Инфляция в ${name}</h2><div id="dinfl"><p class="muted">считаю…</p></div>
    <h2>История цен товара в ${name}</h2>
    <div class="updbox"><input id="hq" style="flex:1;min-width:220px" value="${esc(DHIST && DHIST.store === S.dealStore ? DHIST.q : "")}" placeholder="например: maslo, ser gouda, skyr">
      <button class="btn" data-dhist="1">Показать</button></div>
    <div id="dhist"></div>`;
  placeIndicators();
  drawDealSearch(); drawHistory();
  if (!DINFL) DINFL = await (await fetch("/api/deals/inflation?" + sq())).json();
  drawInflation();
}
function drawDealSearch() {
  const box = document.getElementById("dres");
  if (!box || !DSEARCH || DSEARCH.store !== S.dealStore) return;
  const R = DSEARCH;
  box.innerHTML = `<div class="poshead"><b>«${esc(R.q)}»</b><span class="${R.deals.length ? "save" : "muted"}">${R.deals.length
      ? `найдено: ${R.deals.length}` : `скидок в газетках ${STORE_NAME[S.dealStore]} нет`}</span>${mineText(mine(R.q))}
    ${DEALS.positions.some(p => foldTxt(p.q) === foldTxt(R.q)) ? "" : `<button class="chip" data-wadd-q="${esc(R.q)}">+ в постоянные позиции</button>`}</div>
    ${R.deals.length ? dealGrid(R.deals, R.q) : ""}`;
}
const chgRow = r => `<tr><td>${esc(r.title)} <span class="src">${esc(r.unit)}</span></td>
  <td class="n">${zl(r.p0)} <span class="src">${ddmmyy(r.from)}</span></td><td class="n">${zl(r.p1)} <span class="src">${ddmmyy(r.to)}</span></td>
  <td class="n">${pct(r.pct)}</td>${r.n ? `<td class="n muted">${r.n}</td>` : ""}</tr>`;
function drawInflation() {
  const box = document.getElementById("dinfl");
  if (!box || !DINFL) return;
  const R = DINFL.receipts, name = STORE_NAME[S.dealStore];
  const head = `<tr><th>товар</th><th class="n">было</th><th class="n">стало</th><th class="n">изменение</th><th class="n">покупок</th></tr>`;
  const byQ = {}; for (const r of DINFL.rows) (byQ[r.q] ??= []).push(r);
  box.innerHTML = `<h3 style="margin:6px 0">Твоя инфляция по чекам ${name}</h3>${R.n ? `
    <div class="cards"><div class="card">в среднем по твоим покупкам<b>${pct(R.avg)}</b>${R.n} товаров, купленных 4+ раза, с ${ddmmyy(R.since)}</div></div>
    <p class="muted">Цена на полке (до скидки в чеке), начало — медиана первых трёх покупок, сейчас — последних трёх; среднее — с весом того,
      сколько ты на товар тратишь. У весовых товаров — за кг; овощи и фрукты сильно зависят от сезона.</p>
    <div class="infl2"><div><b>Подорожало сильнее всего</b><table>${head}${R.up.filter(r => r.pct > 0).map(chgRow).join("") || `<tr><td class="muted" colspan="5">ничего</td></tr>`}</table></div>
      <div><b>Подешевело</b><table>${head}${R.down.map(chgRow).join("") || `<tr><td class="muted" colspan="5">ничего</td></tr>`}</table></div></div>`
    : `<p class="muted">Пока мало повторных покупок в ${name}: нужен товар, купленный 4+ раза за 2+ месяца.</p>`}
    <h3 style="margin:14px 0 6px">Постоянные позиции</h3>
    <table><tr><th>позиция</th><th>по твоим чекам</th><th>по газеткам ${name}</th></tr>${R.positions.map(p => {
      const rc = p.receipts, fl = byQ[p.q] || [];
      return `<tr><td><b>${esc(p.q)}</b></td>
        <td>${rc ? `${esc(rc.title)}: ${zl(rc.p0)} → ${zl(rc.p1)} <span class="src">${esc(rc.unit)}</span> ${pct(rc.pct)}<div class="muted" style="font-size:.85em">${ddmmyy(rc.from)} – ${ddmmyy(rc.to)}, ${rc.n} покупок</div>`
          : `<span class="muted">мало покупок в ${name}</span>`}</td>
        <td>${fl.length ? fl.map(r => `${esc(r.title)}: ${zl(r.p0)} → ${zl(r.p1)} <span class="src">${esc(r.unit)}</span> ${pct(r.pct)}`).join("<br>")
          : `<span class="muted">нужны записи из двух разных газеток — копятся при каждом обновлении</span>`}</td></tr>`; }).join("")}</table>`;
}
function drawHistory() {
  const box = document.getElementById("dhist");
  dCharts.forEach(c => c.destroy()); dCharts = [];
  if (!box || !DHIST || DHIST.store !== S.dealStore) return;
  const name = STORE_NAME[S.dealStore], R = DHIST.receipts || [], F = DHIST.items || [];
  if (!R.length && !F.length) { box.innerHTML = `<p class="muted">«${esc(DHIST.q)}»: ни в чеках, ни в газетках ${name} не встречался.</p>`; return; }
  box.innerHTML = R.map((h, i) => `<div class="hcard"><h3>${esc(h.title)} <span class="src">по чекам</span></h3>
      <div class="muted">${h.rows.length} покупок с ${ddmmyy(h.rows[0].date)}${h.change ? ` · ${zl(h.change.p0)} → ${zl(h.change.p1)} (${esc(h.change.unit)}) ${pct(h.change.pct)}` : ""}
        · последняя ${ddmmyy(h.rows[h.rows.length - 1].date)}: ${zl(h.rows[h.rows.length - 1].price)}</div>
      <div class="chartbox inner"><canvas id="hr${i}" height="70"></canvas></div></div>`).join("") +
    F.map((h, i) => `<div class="hcard"><h3>${esc(h.title)} <span class="src">по газеткам</span></h3>
    <div class="muted">${h.rows.length} записей с ${ddmmyy(h.rows[0].start)}${h.change ? ` · обычная цена ${zl(h.change.p0)} → ${zl(h.change.p1)} (${esc(h.unit)}) ${pct(h.change.pct)}` : ""}
      ${h.best ? ` · лучшая цена по акции ${zl(h.best.price)} (−${h.best.discount}%, ${ddmmyy(h.best.start)})` : ""}</div>
    ${Object.keys(h.season).length ? `<div class="muted">акции по месяцам: ${Object.entries(h.season).map(([mo, s]) =>
      `${MONTHS[+mo - 1]} ×${s.n} (до −${s.best}%)`).join(", ")}</div>` : ""}
    <div class="chartbox inner"><canvas id="hc${i}" height="70"></canvas></div></div>`).join("");
  // в стиле остальных графиков: цена — плавная линия с градиентом и свечением, скидки и акции — светящиеся точки
  const blue = css("--c-blue");
  const line = (label, data, extra = {}) => ({label, data, solid: blue, borderColor: blue, borderWidth: 2.4,
    cubicInterpolationMode: "monotone", fill: "origin", backgroundColor: x => fade(x.chart, blue, .25, 0),
    pointRadius: 3, pointHoverRadius: 5, pointBackgroundColor: blue, pointBorderColor: css("--panel"), pointBorderWidth: 2,
    glow: alpha(blue, .4), glowBlur: 10, ...extra});
  const dots = (label, data, c) => ({label, data, solid: c, borderColor: c, backgroundColor: c, showLine: false,
    pointRadius: 5, pointHoverRadius: 7, pointBorderColor: css("--panel"), pointBorderWidth: 2, glow: alpha(c, .55), glowBlur: 10});
  const opts = () => ({animation: chartAnim(), interaction: {mode: "index", intersect: false},
    plugins: {legend: {position: "bottom", labels: solidLegend},
              tooltip: {callbacks: {label: x => ` ${x.dataset.label.split(",")[0]}: ${zl(x.parsed.y)}`}}},
    scales: {x: softX({ticks: {maxRotation: 0, autoSkip: true}}), y: softY()}});
  R.forEach((h, i) => {
    const unit = h.rows[0].unit === "kg" ? "zł/кг" : "zł/шт.";
    dCharts.push(new Chart(document.getElementById("hr" + i), {type: "line", plugins: [GLOW],
      data: {labels: h.rows.map(r => ddmmyy(r.date)), datasets: [
        line(`цена на полке, ${unit}`, h.rows.map(r => r.price)),
        dots("оплатил (со скидкой)", h.rows.map(r => r.paid < r.price - 0.005 ? r.paid : null), css("--c-green"))]},
      options: opts()}));
  });
  F.forEach((h, i) => {
    const perUnit = h.unit !== "zł/шт.", rows = h.rows;
    dCharts.push(new Chart(document.getElementById("hc" + i), {type: "line", plugins: [GLOW],
      data: {labels: rows.map(r => ddmmyy(r.start)), datasets: [
        line(`обычная цена, ${h.unit}`, rows.map(r => perUnit ? r.unit_regular : r.regular), {spanGaps: true}),
        dots("по акции", rows.map(r => r.discount ? (perUnit ? r.unit_price : r.price) : null), css("--c-red"))]},
      options: opts()}));
  });
}
async function dealsAction(fn, busy) {
  if (busy) toast(busy, {spin: true, ms: 120000});
  try { return await fn(); } catch (e) { toast("Не получилось: " + e.message); return null; }
}
async function refreshDeals() { DEALS = null; DINFL = null; const y = scrollY; await renderDeals(); scrollTo(0, y); load(); }
// «Где выгоднее»: одинаковое сейчас в обоих магазинах — мясо по видам, одна марка, постоянные позиции
const CMP_KIND = {position: "Твои постоянные позиции", meat: "Мясо — по цене за кг", brand: "Одна и та же марка"};
async function renderCompare() {
  if (!DCMP) DCMP = await (await fetch("/api/deals/compare")).json();
  const cell = (d, win) => `<td class="${win ? "cwin" : ""}"><div><b>${d.approx ? "≈" : ""}${zl(d.value)}</b> <span class="muted">${esc(d.basis || "")}</span>
      ${win ? `<span class="st" style="--c:var(--green)">выгоднее</span>` : ""}</div>
    <div>${esc(d.title)}</div><div class="muted">${d.price != null ? zl(d.price) : ""}${d.size ? " · " + esc(d.size) : ""}${d.discount ? ` · −${d.discount}%` : ""}
      · ${esc(d.status)} · <a href="${esc(d.url)}" target="_blank">стр. ${d.page} ↗</a></div></td>`;
  const groups = {};
  for (const p of DCMP.pairs) (groups[p.kind] ??= []).push(p);
  document.getElementById("main").innerHTML = `${storeTabs()}
    <p class="muted">Текущие и будущие акции обоих магазинов (Lidl: ${DCMP.lidl}, Kaufland: ${DCMP.kaufland}), где можно сравнить одинаковое:
      мясо — один вид, самое дешёвое за кг в каждом магазине (сырое, без колбас и полуфабрикатов); марка — тот же товар одной марки;
      и твои постоянные позиции. Сравнение — за кг/л или за одинаковую упаковку. Цены — из газеток; «≈» — рассчитано по цене за кг/л.</p>
    ${Object.keys(CMP_KIND).filter(k => groups[k]).map(k => `<h2>${CMP_KIND[k]}</h2>
      <table class="cmp"><tr><th>что</th><th>Lidl</th><th>Kaufland</th><th class="n">разница</th></tr>${groups[k].map(p => `<tr>
        <td><b>${esc(p.label)}</b></td>${cell({...p.lidl, basis: p.basis}, p.cheaper === "lidl")}${cell({...p.kaufland, basis: p.basis}, p.cheaper === "kaufland")}
        <td class="n">${p.cheaper ? `<b class="save">−${p.pct}%</b><div class="muted">в ${STORE_NAME[p.cheaper]}</div>` : `<span class="muted">одинаково</span>`}</td></tr>`).join("")}</table>`).join("")
      || `<div class="empty">Сейчас одинаковых акций в обоих магазинах не нашлось — обнови газетки на вкладках Lidl и Kaufland.</div>`}`;
  placeIndicators();
}

function switchStore(k) {
  S.dealStore = k; DEALS = null; DINFL = null; DCMP = null;
  try { localStorage.setItem("budget-deal-store", k); } catch (e) { /* только до перезагрузки */ }
  renderDeals();
}
