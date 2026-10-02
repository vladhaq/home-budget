// Страница «Скидки» (газетки Lidl).
// ---------- скидки Lidl: газетки, постоянные позиции, поиск, напоминания, история цен
let DEALS = null, DSEARCH = null, DINFL = null, DHIST = null, dCharts = [];
const ddmm = d => d ? `${d.slice(8, 10)}.${d.slice(5, 7)}` : "";
// слова названия: «PrimoSkyrNaturalny150g» -> primo skyr naturalny 150g (как в словаре категорий)
const nameWords = s => foldTxt(String(s).replace(/([a-ząćęłńóśźż])([A-ZĄĆĘŁŃÓŚŹŻ])/g, "$1 $2")).split(/[^a-z0-9а-я]+/).filter(Boolean);
const wordHit = (w, tok) => { const st = tok.length >= 4 && /[aeiouy]$/.test(tok) ? tok.slice(0, -1) : tok;
                              return w.startsWith(st) && w.length - st.length <= 3; };
function mine(q) {  // этот товар в твоих чеках: сколько раз, почём обычно
  const toks = foldTxt(q).split(/\s+/).filter(x => x.length > 2);
  if (!toks.length) return null;
  const hits = [];
  for (const p of D.purchases) for (const it of p.items) {
    const ws = nameWords(it.name);
    if (toks.every(tk => ws.some(w => wordHit(w, tk)))) hits.push({date: p.date, merchant: p.merchant, qty: it.qty || 1, price: net(it) / (it.qty || 1)});
  }
  if (!hits.length) return null;
  hits.sort((a, b) => a.date < b.date ? -1 : 1);
  const last = hits[hits.length - 1], pcs = hits.every(h => Number.isInteger(h.qty));
  return {n: hits.length, last, avg: hits.reduce((s, h) => s + h.price, 0) / hits.length, pcs};
}
const mineText = m => m ? `<span class="muted">· ты покупал ${m.n} раз, последний ${ddmmyy(m.last.date)} по ${zl(m.last.price)}
  (${esc(m.last.merchant)}), в среднем ${zl(m.avg)}${m.pcs ? "" : " за кг/шт."}</span>` : `<span class="muted">· в твоих чеках не встречался</span>`;
function dealCard(d, q, m) {
  const price = d.price != null ? (d.approx ? "≈" : "") + zl(d.price) : d.discount ? `−${d.discount}%` : "цена не распознана";
  const cmp = m && m.pcs && d.price != null && !d.approx ? d.price / m.avg - 1 : null;
  return `<div class="deal ${d.upcoming ? "soon" : "now"}">
    <div class="dtop"><span class="st" style="--c:var(${d.upcoming ? "--orange" : "--green"})">${esc(d.status)}</span>
      ${d.discount ? `<span class="dbadge">−${d.discount}%</span>` : ""}</div>
    <div class="dtitle">${esc(d.title)}</div>
    <div class="dprice"><b>${price}</b>${d.old_price != null && d.old_price !== d.price ? `<s>${zl(d.old_price)}</s>` : ""}
      ${d.size ? ` <span class="muted">${esc(d.size)}</span>` : ""}</div>
    ${d.unit_price ? `<div class="muted">${zl(d.unit_price)} за ${esc(d.unit)}${d.unit_regular && d.unit_regular !== d.unit_price ? ` (обычно ${zl(d.unit_regular)})` : ""}</div>` : ""}
    ${d.no_discount ? `<div class="muted">${d.prev ? `в газетке от ${ddmm(d.prev.start)} было ${zl(d.prev.price)}` : "скидки нет — обычная цена из газетки"}</div>` : ""}
    ${cmp != null && cmp < -0.03 ? `<div class="save">дешевле, чем ты обычно платишь, на ${Math.round(-cmp * 100)}%</div>`
      : cmp != null && cmp > 0.03 ? `<div class="muted">ты обычно платишь меньше: ${zl(m.avg)}</div>` : ""}
    ${(d.conditions || []).map(c => `<div class="cond">• ${esc(c)}</div>`).join("")}
    <div class="dfoot"><a href="${esc(d.url)}" target="_blank">газетка, стр. ${d.page} ↗</a>
      ${d.reminded ? `<span class="muted">🔔 напоминание стоит</span>`
        : `<button class="chip" data-remind="${esc(d.key)}" data-q="${esc(q)}" title="сообщение в Избранное Telegram накануне начала акции">🔔 напомнить</button>`}</div>
  </div>`;
}
const dealGrid = (deals, q) => { const m = mine(q); return `<div class="dgrid">${deals.map(d => dealCard(d, q, m)).join("")}</div>`; };
async function renderDeals() {
  if (!DEALS) DEALS = await (await fetch("/api/deals")).json();
  const X = DEALS, tg = X.telegram;
  document.getElementById("main").innerHTML = `
    <div class="updbox">
      <span class="big">Газетки Lidl: <b>${X.flyers.length}</b> · обновлены ${X.updated ? dtf(X.updated.slice(0, 16)) : "ещё не скачивались"}</span>
      <button class="btn" data-dupdate="1">Обновить газетки</button>
      <span class="muted">${tg.ready ? `🔔 напоминать в Избранное Telegram накануне акции в
          <input type="time" id="rat" value="${esc(tg.remind_at)}"> <button class="chip" data-rat="1">сохранить</button>`
        : `🔕 напоминания в Telegram — после первого входа: ${cmd("python budget.py telegram")}`}</span>
    </div>
    <div class="flyers">${X.flyers.map(f => `<span class="chip">${esc(f.name)} · ${ddmm(f.start)}–${ddmm(f.end)}</span>`).join("")}</div>
    <p class="muted">Газетки и напоминания по постоянным позициям обновляются сами в «обновить всё» (шаг «Газетки Lidl»).</p>

    <h2>Постоянные позиции</h2>
    <div class="updbox"><span class="wlist">${X.positions.map(p => `<span class="wchip">${esc(p.q)}<button type="button" data-wrm="${esc(p.q)}"
        title="убрать из постоянных">×</button></span>`).join("") || `<span class="muted">пока пусто — добавь товары, за скидками на которые следить</span>`}</span>
      <span><input id="wq" placeholder="добавить: ser gouda, maslo…"> <button class="chip" data-wadd="1">+ добавить</button></span></div>
    <div id="dpos">${X.positions.map(p => `<div class="poshead"><b>${esc(p.q)}</b>
        <span class="${p.deals.length ? "save" : "muted"}">${p.deals.length ? `акций: ${p.deals.length}` : "в текущих газетках нет"}</span>${mineText(mine(p.q))}</div>
      ${p.deals.length ? dealGrid(p.deals, p.q) : ""}`).join("")}</div>

    <h2>Найти в газетках</h2>
    <div class="updbox"><input id="dq" style="flex:1;min-width:220px" value="${esc(DSEARCH ? DSEARCH.q : "")}"
        placeholder="товар по-польски, диакритику можно не ставить: maslo, kawa, piers z kurczaka">
      <button class="btn" data-dsearch="1">Найти</button></div>
    <div id="dres"></div>

    <h2>Инфляция по постоянным позициям</h2><div id="dinfl"><p class="muted">считаю…</p></div>
    <h2>История цен товара</h2>
    <div class="updbox"><input id="hq" style="flex:1;min-width:220px" value="${esc(DHIST ? DHIST.q : "")}" placeholder="например: maslo, ser gouda">
      <button class="btn" data-dhist="1">Показать</button></div>
    <div id="dhist"></div>`;
  drawDealSearch(); drawHistory();
  if (!DINFL) DINFL = await (await fetch("/api/deals/inflation")).json();
  drawInflation();
}
function drawDealSearch() {
  const box = document.getElementById("dres");
  if (!box || !DSEARCH) return;
  const R = DSEARCH;
  box.innerHTML = `<div class="poshead"><b>«${esc(R.q)}»</b><span class="${R.deals.length ? "save" : "muted"}">${R.deals.length
      ? `найдено: ${R.deals.length}` : "скидок в текущих газетках нет"}</span>${mineText(mine(R.q))}
    ${DEALS.positions.some(p => foldTxt(p.q) === foldTxt(R.q)) ? "" : `<button class="chip" data-wadd-q="${esc(R.q)}">+ в постоянные позиции</button>`}</div>
    ${R.deals.length ? dealGrid(R.deals, R.q) : ""}`;
}
function drawInflation() {
  const box = document.getElementById("dinfl");
  if (!box || !DINFL) return;
  box.innerHTML = DINFL.rows.length ? `<table><tr><th>товар</th><th class="n">было</th><th class="n">стало</th><th class="n">изменение</th></tr>` +
    DINFL.rows.map(r => `<tr><td>${esc(r.title)} <span class="src">${esc(r.unit)}</span></td><td class="n">${zl(r.p0)} <span class="src">${ddmmyy(r.from)}</span></td>
      <td class="n">${zl(r.p1)} <span class="src">${ddmmyy(r.to)}</span></td>
      <td class="n ${r.pct > 0 ? "up" : r.pct < 0 ? "save" : ""}">${r.pct > 0 ? "+" : ""}${r.pct.toLocaleString("ru-RU")}%</td></tr>`).join("") +
    (DINFL.avg != null ? `<tr><td><b>В среднем по корзине</b></td><td></td><td></td><td class="n"><b>${DINFL.avg > 0 ? "+" : ""}${DINFL.avg.toLocaleString("ru-RU")}%</b></td></tr>` : "") + `</table>`
    : `<p class="muted">Пока не с чем сравнивать: у постоянных позиций нужны записи хотя бы из двух разных газеток.
       История цен копится при каждом обновлении газеток (по цене за кг/л, если она есть — так видно и уменьшение упаковки).</p>`;
}
function drawHistory() {
  const box = document.getElementById("dhist");
  dCharts.forEach(c => c.destroy()); dCharts = [];
  if (!box || !DHIST) return;
  if (!DHIST.items.length) { box.innerHTML = `<p class="muted">«${esc(DHIST.q)}»: в истории газеток не встречался.</p>`; return; }
  box.innerHTML = DHIST.items.map((h, i) => `<div class="hcard"><h3>${esc(h.title)}</h3>
    <div class="muted">${h.rows.length} записей с ${ddmmyy(h.rows[0].start)}${h.change ? ` · обычная цена ${zl(h.change.p0)} → ${zl(h.change.p1)} (${esc(h.unit)}),
      <span class="${h.change.pct > 0 ? "up" : "save"}">${h.change.pct > 0 ? "+" : ""}${h.change.pct.toLocaleString("ru-RU")}%</span>` : ""}
      ${h.best ? ` · лучшая цена по акции ${zl(h.best.price)} (−${h.best.discount}%, ${ddmmyy(h.best.start)})` : ""}</div>
    ${Object.keys(h.season).length ? `<div class="muted">акции по месяцам: ${Object.entries(h.season).map(([mo, s]) =>
      `${MONTHS[+mo - 1]} ×${s.n} (до −${s.best}%)`).join(", ")}</div>` : ""}
    <div class="chartbox" style="box-shadow:none;padding:6px 0 0;margin:6px 0 0"><canvas id="hc${i}" height="70"></canvas></div></div>`).join("");
  DHIST.items.forEach((h, i) => {
    const perUnit = h.unit !== "zł/шт.", rows = h.rows;
    dCharts.push(new Chart(document.getElementById("hc" + i), {type: "line",
      data: {labels: rows.map(r => ddmmyy(r.start)), datasets: [
        {label: `обычная цена, ${h.unit}`, data: rows.map(r => perUnit ? r.unit_regular : r.regular), borderColor: css("--c-blue"),
         spanGaps: true, pointRadius: 3, tension: .2},
        {label: "по акции", data: rows.map(r => r.discount ? (perUnit ? r.unit_price : r.price) : null), borderColor: css("--c-red"),
         backgroundColor: css("--c-red"), showLine: false, pointRadius: 5}]},
      options: {animation: chartAnim(), plugins: {legend: {position: "bottom"}}, scales: {x: {grid: {display: false}}}}}));
  });
}
async function dealsAction(fn, busy) {
  if (busy) toast(busy, {spin: true, ms: 120000});
  try { return await fn(); } catch (e) { toast("Не получилось: " + e.message); return null; }
}
async function refreshDeals() { DEALS = null; DINFL = null; const y = scrollY; await renderDeals(); scrollTo(0, y); load(); }
