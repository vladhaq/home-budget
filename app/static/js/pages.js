// Страницы «Неопознанные», «Категории», «Банк», «Наличные».
// ---------- неопознанные
function doubtBlock() {
  const ds = D.purchases.filter(p => p.status === "doubt");
  if (!ds.length) return "";
  return `<h2>Платежи под вопросом: ${ds.length} на ${zl(ds.reduce((s, p) => s + p.total, 0))}</h2>
    <p class="muted">Платёжная система (Przelewy24, PayU) прислала письмо о <b>регистрации</b> платежа, но в выписке PKO
      списания нет, хотя выписка эти дни уже покрывает, — или это повтор той же суммы через минуту. Неоплаченную регистрацию
      платёжка отменяет сама. В суммы такие платежи не идут. Платил другой картой — «оплачено»; оплата не прошла — удали.</p>
    <table><tr><th>дата</th><th>где</th><th>категория</th><th class="n">сумма</th><th></th></tr>` + ds.map(p => `<tr>
      <td style="white-space:nowrap">${ddmmyy(p.date)} <span class="muted">${p.date.slice(11, 16)}</span></td>
      <td><div class="who">${ava(p.merchant)}<div>${esc(p.merchant)}<div class="muted">${esc(SOURCES[p.source] || p.source)}${
        p.file ? ` · <a href="/file/${esc(p.file)}" target="_blank">✉ письмо</a>` : ""}</div></div></div></td>
      <td>${p.items[0] && p.items[0].cat != null ? esc(catLabel(p.items[0].cat)) : ""}</td>
      <td class="n">${zl(p.total)}</td>
      <td style="white-space:nowrap"><button class="chip act" data-confirm="${esc(p.id)}">оплачено</button>${delBtn(p)}</td></tr>`).join("")
    + "</table>";
}
async function confirmPurchase(id, undo = false) {
  try { await post("/api/purchase/confirm", {id, undo}); } catch (e) { toast("Не получилось: " + e.message); return; }
  BANK = null; const y = scrollY; await load(); scrollTo(0, y);
  if (!undo) toast("Платёж считается оплаченным", {action: "Вернуть", onAction: () => confirmPurchase(id, true)});
}

function renderUnknown() {
  const items = aggregate(filtered()).filter(a => a.cat == null || a.mixed && a.cats[""]);
  const doubts = doubtBlock();
  document.getElementById("main").innerHTML = doubts + (items.length
    ? `<h2>Неопознанные позиции: ${items.length}</h2>
       <p class="muted">Выбери категорию — она применится ко всем таким позициям и запомнится для будущих чеков.
         Нажми на название — увидишь каждый случай: дату, где, операцию в банке и что ещё покупалось в эти дни.
         Там же можно поставить категорию одному случаю (галочка «только эта»).</p>` +
      itemsTable(items, ["где", a => esc([...a.merchants].join(", "))])
    : `<div class="empty">Неопознанных позиций нет 🎉</div>`);
}

// ---------- категории
function renderCategories() {
  const ps = filtered(), spent = {}, count = {};
  for (const p of ps) for (const it of p.items) {
    let c = it.cat;
    while (c != null) { spent[c] = (spent[c] || 0) + net(it); count[c] = (count[c] || 0) + 1; c = CAT[c].parent_id; }
  }
  const byName = (a, b) => a.name.localeCompare(b.name, "ru");
  const row = (c, depth, fresh = false) => {
    const kids = (KIDS[c.id] || []).slice().sort(byName), open = S.openCatPage.has(c.id);
    const label = depth ? esc(c.name) : `<b>${esc(c.name)}</b>`;
    return `<tr class="${fresh ? "fresh" : ""}${open ? " opened" : ""}"><td style="padding-left:${10 + depth * 26}px">
      ${kids.length ? `<button class="cellbtn ctog" data-cpt="${c.id}" title="${open ? "свернуть" : "развернуть"}"><span class="chev${open ? " on" : ""}"></span>${label}</button>
        <span class="src">${kids.length} подкат.</span>` : `<span class="chev none"></span>${label}`}
      ${c.kind !== "expense" ? `<span class="src">${c.kind === "income" ? "доход" : "не расход"}</span>` : ""}</td>
      <td class="n">${count[c.id] || ""}</td><td class="n">${spent[c.id] ? zl(spent[c.id]) : ""}</td>
      <td style="white-space:nowrap"><button class="chip act" data-addcat="${c.id}">+ подкатегория</button>
        <button class="chip act" data-rencat="${c.id}">переименовать</button>
        ${catPick(null, "move", {id: c.id}).replace("⚠ неопознанные ▾", "перенести ▾").replace("catpick", "catpick chip act")}
        <button class="chip act" data-delcat="${c.id}">удалить</button></td></tr>` +
      (open ? kids.map(k => row(k, depth + 1, S.justOpenedCatPage === c.id)).join("") : ""); };
  document.getElementById("main").innerHTML = `
    <h2>Категории</h2>
    <div class="cattools"><button class="chip" data-addcat="">+ новая категория верхнего уровня</button>
      <button class="chip" data-cptall="1">развернуть всё</button><button class="chip" data-cptall="0">свернуть всё</button></div>
    <table class="tree"><tr><th>категория</th><th class="n">позиций</th><th class="n">потрачено</th><th></th></tr>
      ${(KIDS[0] || []).slice().sort((a, b) => (a.kind !== "expense") - (b.kind !== "expense") || byName(a, b)).map(c => row(c, 0)).join("")}</table>
    <p class="muted">Удаление: позиции, правила и подкатегории переходят в родительскую категорию.
      Переименование и перенос не ломают правила — старые названия запоминаются.</p>
    <h2>Правила (${D.rules.length})</h2>
    <p class="muted">Создаются при выборе категории «для всех таких». Удалишь правило — позиции вернутся к словарю.</p>
    ${D.rules.length ? `<table><tr><th>условие</th><th>категория</th><th>создано</th><th></th></tr>` + D.rules.map(r => `<tr>
      <td>${r.target === "merchant" ? "магазин / получатель «" + esc(r.pattern) + "»" : r.product_code ? "код товара " + esc(r.product_code)
        : "название «" + esc(humanPattern(r.pattern)) + "»"}</td>
      <td>${esc(r.path)}</td><td>${esc(r.created)}</td>
      <td><button class="chip" data-delrule="${r.id}">удалить</button></td></tr>`).join("") + "</table>" : `<p class="muted">Правил пока нет.</p>`}`;
}

// ---------- банк: баланс, доходы и расходы, покрытие чеками, наличные
let BANK = null, bankCharts = [];
async function renderBank() {
  const main = document.getElementById("main");
  if (!BANK) BANK = await (await fetch("/api/bank")).json();
  if (BANK.empty || !Object.keys(BANK.balance).length) {
    main.innerHTML = `<div class="empty">Выписка ещё не загружена. Подключить банк: python budget.py bank login, затем python budget.py bank sync (см. README)</div>`; return;
  }
  bankCharts.forEach(c => c.destroy()); bankCharts = [];
  const F = S.F, inRange = m => (!F.from || m >= F.from) && (!F.to || m <= F.to);
  const days = Object.keys(BANK.balance).sort().filter(d => inRange(d.slice(0, 7)));
  const months = [...new Set([...Object.keys(BANK.months), ...Object.keys(BANK.income)])].sort()
    .filter(m => inRange(m) && m >= Object.keys(BANK.balance).sort()[0].slice(0, 7));
  // доход: категории «Доходы/…» и ещё не размеченные поступления (так же считает список операций ниже)
  const inc = m => Object.entries(BANK.income[m] || {}).filter(([k]) => k.startsWith("Доходы") || k === "Прочие поступления")
    .reduce((s, [, v]) => s + v, 0);
  const mm = m => BANK.months[m] || {};
  const pko = m => (mm(m).receipts || 0) + (mm(m).bank_only || 0);
  const spent = m => pko(m) + (mm(m).cash || 0) + (mm(m).other || 0);
  const lastBal = BANK.balance[Object.keys(BANK.balance).sort().pop()];
  const totInc = months.reduce((s, m) => s + inc(m), 0), totSp = months.reduce((s, m) => s + spent(m), 0);
  const rec = months.reduce((s, m) => s + (mm(m).receipts || 0), 0), pkoSp = months.reduce((s, m) => s + pko(m), 0);
  const other = months.reduce((s, m) => s + (mm(m).other || 0), 0);
  const shopRec = months.reduce((s, m) => s + (mm(m).shop_receipts || 0), 0);
  const shopAll = shopRec + months.reduce((s, m) => s + (mm(m).shop_bank_only || 0), 0);
  const atm = months.reduce((s, m) => s + (mm(m).atm || 0), 0), cash = months.reduce((s, m) => s + (mm(m).cash || 0), 0);
  const incCats = {};
  for (const m of months) for (const [k, v] of Object.entries(BANK.income[m] || {})) incCats[k] = (incCats[k] || 0) + v;
  main.innerHTML = `
    <div class="cards">
      <div class="card">остаток на счёте<b>${zl(lastBal)}</b>на ${esc(BANK.last || "")}</div>
      <div class="card">доходы за период<b class="save">${zl(totInc)}</b>в месяц ${zl(totInc / (months.length || 1))}</div>
      <div class="card">расходы со счёта PKO<b>${zl(pkoSp)}</b>в месяц ${zl(pkoSp / (months.length || 1))}</div>
      <div class="card">трат PKO подтверждено чеками<b>${(rec / (pkoSp || 1) * 100).toFixed(0)}%</b>с арендой, учёбой, налогами</div>
      <div class="card">покупок подтверждено чеками<b>${(shopRec / (shopAll || 1) * 100).toFixed(0)}%</b>без аренды, учёбы, налогов, комиссий</div>
      <div class="card">другой картой/счётом<b>${zl(other)}</b>по чекам, в выписке PKO их нет</div>
      <div class="card">наличные<b>${zl(atm)}</b>снято; в чеках за наличные ${zl(cash)}</div>
      <div class="card">доступ к банку<b>${BANK.valid_until ? BANK.valid_until.slice(0, 10) : "—"}</b>потом: bank login</div>
    </div>
    <h2>Баланс</h2><div class="chartbox"><canvas id="bal" height="90"></canvas></div>
    <h2>Доходы и расходы по месяцам</h2>
    <p class="muted">Легенда включает и выключает виды. Клик по столбцу графика или по сумме в таблице — ниже откроются
      операции, из которых она сложилась.</p>
    <div class="chartbox"><canvas id="flow" height="110"></canvas></div>
    <div class="tscroll"><table id="banktable"><tr><th>месяц</th><th class="n">доходы</th><th class="n">расходы PKO</th><th class="n">из них по чекам</th>
      <th class="n">только выписка</th><th class="n">другой картой</th><th class="n">наличными (чеки)</th><th class="n">снято в банкомате</th>
      <th class="n">доходы − расходы PKO</th></tr>` +
    months.slice().reverse().map(m => `<tr data-row="${m}"><td><button class="cellbtn" data-bcell="all" data-bm="${m}"
        title="все операции месяца">${monthName(m)}</button></td>${cellBtn(m, "income", inc(m), "save")}${cellBtn(m, "pko", pko(m))}
      ${cellBtn(m, "receipts", mm(m).receipts)}${cellBtn(m, "bank_only", mm(m).bank_only)}${cellBtn(m, "other", mm(m).other)}
      ${cellBtn(m, "cash", mm(m).cash)}${cellBtn(m, "atm", mm(m).atm)}
      ${cellBtn(m, "net", inc(m) - pko(m), inc(m) - pko(m) >= 0 ? "save" : "up")}</tr>`).join("") +
    `</table></div>
    <section id="banklist"></section>
    <h2>Поступления по видам</h2><table><tr><th>вид</th><th class="n">сумма</th></tr>` +
    Object.entries(incCats).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<tr><td>${esc(k)}${k.startsWith("Переводы") ? ' <span class="src">не доход</span>' : ""}</td>
      <td class="n">${zl(v)}</td></tr>`).join("") + `</table>
    <p class="muted">Расходы — без переводов людям и снятия наличных. «Только выписка» — операции без чека: их можно
      разметить на странице «Неопознанные» или в обзоре (источник «банк»).</p>`;
  bankCharts.push(new Chart(document.getElementById("bal"), {type: "line",
    data: {labels: days, datasets: [{label: "остаток, zł", data: days.map(d => BANK.balance[d]), borderColor: css("--c-blue"),
      pointRadius: 0, pointHoverRadius: 4, stepped: true,
      fill: {target: "origin", above: alpha(css("--c-blue"), .12), below: alpha(css("--c-red"), .18)}}]},
    options: {animation: chartAnim(), interaction: {mode: "index", intersect: false}, plugins: {legend: {display: false}},
      scales: {x: {grid: {display: false}, ticks: {maxTicksLimit: 14}}}}}));
  bankCharts.push(new Chart(document.getElementById("flow"), {type: "bar",
    data: {labels: months.map(m => m.slice(5) + "." + m.slice(2, 4)), datasets: [
      {label: "доходы", data: months.map(inc), backgroundColor: css("--c-green"), stack: "in"},
      {label: "по чекам", data: months.map(m => mm(m).receipts || 0), backgroundColor: css("--c-blue"), stack: "out"},
      {label: "наличными (чеки)", data: months.map(m => mm(m).cash || 0), backgroundColor: css("--c-violet"), stack: "out"},
      {label: "только выписка", data: months.map(m => mm(m).bank_only || 0), backgroundColor: css("--c-orange"), stack: "out"},
      {label: "другой картой", data: months.map(m => mm(m).other || 0), backgroundColor: css("--c-gray"), stack: "out"},
      {label: "переводы и банкомат", data: months.map(m => flows(m).transfers), backgroundColor: css("--c-gray2"), stack: "tr",
       hidden: !S.bankShow.has("transfers")}]},
    options: {animation: chartAnim(), plugins: {legend: {position: "bottom", onClick: (e, item, legend) => {
        const ch = legend.chart, i = item.datasetIndex, key = BANK_TYPES[i].key;
        ch.isDatasetVisible(i) ? (ch.hide(i), S.bankShow.delete(key)) : (ch.show(i), S.bankShow.add(key));
        S.bankCell = null; drawBankList();
      }}}, scales: {x: {stacked: true, grid: {display: false}}, y: {stacked: true}},
      onClick: (e, els) => { if (els.length) { const m = months[els[0].index];
        S.bankMonth = S.bankMonth === m && !S.bankCell ? null : m; S.bankCell = null; drawBankList();
        document.getElementById("banklist").scrollIntoView({behavior: "smooth"}); } }}}));
  for (const [i, t] of BANK_TYPES.entries()) if (!S.bankShow.has(t.key) && i < 5) bankCharts[1].hide(i);
  drawBankList();
}

// ---------- операции под графиком банка
const BANK_TYPES = [
  {key: "income", label: "доход", color: "--c-green"}, {key: "receipts", label: "по чекам", color: "--c-blue"},
  {key: "cash", label: "наличными (чек)", color: "--c-violet"}, {key: "bank_only", label: "только выписка", color: "--c-orange"},
  {key: "other", label: "другой картой", color: "--c-gray"}, {key: "transfers", label: "перевод / банкомат", color: "--text-2"},
  {key: "atm", label: "банкомат", color: "--text-2"}];  // atm — только для подписи в списке, своего столбца на графике нет
// ячейки таблицы по месяцам: какие виды операций в них входят
const BANK_CELLS = {
  income: {label: "доходы", kinds: ["income"]}, pko: {label: "расходы PKO", kinds: ["receipts", "bank_only"]},
  receipts: {label: "расходы по чекам", kinds: ["receipts"]}, bank_only: {label: "только выписка", kinds: ["bank_only"]},
  other: {label: "другой картой", kinds: ["other"]}, cash: {label: "наличными (чеки)", kinds: ["cash"]},
  atm: {label: "снято в банкомате", kinds: ["atm"]},
  net: {label: "доходы − расходы PKO", kinds: ["income", "receipts", "bank_only"], net: true}};
const cellBtn = (m, key, v, cls = "") =>
  `<td class="n ${cls}"><button class="cellbtn" data-bcell="${key}" data-bm="${m}" title="показать операции">${zl(v)}</button></td>`;
S.bankShow = new Set(["income", "receipts", "cash", "bank_only", "other"]);
S.bankMonth = null; S.bankCell = null;
const isExp = it => !(it.cat != null && CAT[it.cat] && CAT[it.cat].kind !== "expense");
const isAtm = it => it.cat != null && CAT[it.cat] && CAT[it.cat].key === "transfer.atm";
const kindOf = r => r.atm ? "atm" : r.type;
function bankRows() {
  const rows = [];
  for (const p of D.purchases) {
    const exp = p.items.filter(isExp), tr = p.items.filter(it => !isExp(it));
    const type = p.source === "bank" ? "bank_only" : p.pay === "cash" ? "cash" : p.bt ? "receipts" : "other";
    if (exp.length) rows.push({type, date: p.date, p, items: exp, amount: exp.reduce((s, it) => s + net(it), 0)});
    for (const [atm, part] of [[true, tr.filter(isAtm)], [false, tr.filter(it => !isAtm(it))]])
      if (part.length) rows.push({type: "transfers", atm, date: p.date, p, items: part, amount: part.reduce((s, it) => s + net(it), 0)});
  }
  for (const x of BANK.incomes || []) {
    const k = x.cat != null && CAT[x.cat] ? CAT[x.cat].kind : "income";
    rows.push({type: k === "transfer" ? "transfers" : "income", date: x.date, inc: x, amount: x.amount});
  }
  return rows;
}
function flows(m) {  // переводы и банкомат за месяц (для графика)
  return {transfers: bankRows().filter(r => r.type === "transfers" && r.date.startsWith(m)).reduce((s, r) => s + r.amount, 0)};
}
function drawBankList() {
  const box = document.getElementById("banklist");
  if (!box) return;
  // месяц целиком, как в таблице (выписка может начинаться с середины первого месяца)
  const first = Object.keys(BANK.balance).sort()[0].slice(0, 7);
  const cell = S.bankCell ? BANK_CELLS[S.bankCell] : null;
  const fq = S.F.q ? foldTxt(S.F.q) : "", sel = selCats();
  const rows = bankRows().map(r => !sel || r.inc ? r : {...r, items: r.items.filter(it => inSel(sel, it.cat))})  // фильтр категорий
    .map(r => r.inc || !sel ? r : {...r, amount: r.items.reduce((s, it) => s + net(it), 0)})
    .filter(r => (r.inc ? !sel || inSel(sel, r.inc.cat) : r.items.length))
    .filter(r => (cell ? cell.kinds.includes(kindOf(r)) : S.bankShow.has(r.type)) && r.date.slice(0, 7) >= first
    && (!fq || foldTxt(r.inc ? `${r.inc.who} ${r.inc.desc}` : `${r.p.merchant} ${r.items.map(i => `${i.name} ${i.note || ""}`).join(" ")}`).includes(fq))
    && (S.bankMonth ? r.date.startsWith(S.bankMonth) : (!S.F.from || r.date.slice(0, 7) >= S.F.from) && (!S.F.to || r.date.slice(0, 7) <= S.F.to)))
    .sort((a, b) => a.date < b.date ? 1 : -1);
  const tot = {};
  for (const r of rows) tot[kindOf(r)] = (tot[kindOf(r)] || 0) + r.amount;
  const total = cell && cell.net ? rows.reduce((s, r) => s + (r.type === "income" ? r.amount : -r.amount), 0)
    : rows.reduce((s, r) => s + r.amount, 0);
  document.querySelectorAll("#banktable .cellbtn.sel").forEach(b => b.classList.remove("sel"));
  document.querySelectorAll("#banktable tr.selrow").forEach(b => b.classList.remove("selrow"));
  if (S.bankMonth) {
    const row = document.querySelector(`#banktable tr[data-row="${S.bankMonth}"]`);
    if (row) { row.classList.add("selrow"); const b = row.querySelector(`[data-bcell="${S.bankCell || "all"}"]`); if (b) b.classList.add("sel"); }
  }
  const chip = k => { const t = BANK_TYPES.find(x => x.key === k);
    return `<span class="st" style="--c:var(${t.color})">${t.label}</span>`; };
  box.innerHTML = `<div class="nav"><h2 style="margin:0">Операции: ${S.bankMonth ? monthName(S.bankMonth) : "весь период"}${cell
        ? ` · ${cell.label}: <span class="${cell.net ? (total >= 0 ? "save" : "up") : ""}">${zl(total)}</span>` : ""}</h2>
      ${cell ? `<button class="chip" data-bankcell-reset="1">все виды${S.bankMonth ? " за месяц" : ""}</button>` : ""}
      ${S.bankMonth ? `<button class="chip" data-bankall="1">весь период</button>
        <button class="chip" data-month-open="${S.bankMonth}">открыть месяц в обзоре</button>` : ""}</div>
    <p>${Object.entries(tot).map(([k, v]) => `${chip(k)} ${zl(v)}`).join(" &nbsp; ") || "нет операций"}
      ${cell && cell.net ? `<span class="muted"> — доходы минус расходы PKO</span>` : ""}</p>
    <table><tr><th>дата</th><th>вид</th><th>кто / магазин</th><th>что</th><th>категория</th><th class="n">сумма</th></tr>` +
    rows.slice(0, 800).map(r => {
      if (r.inc) {
        const x = r.inc;
        const key = "in:" + x.id, open = S.bankOpen.has(key);
        return `<tr class="${PENDING.has("tx|" + x.id) ? "pending" : ""}${open ? " opened" : ""}"><td>${x.date.slice(8, 10)}.${x.date.slice(5, 7)}.${x.date.slice(2, 4)}</td>
          <td>${chip(kindOf(r))}</td><td>${openBtn(key, open, x.who || x.desc)}</td><td>${esc(x.desc)}</td>
          <td>${x.id ? catPick(x.cat, "tx", {tx: x.id}) : esc(catLabel(x.cat))}</td><td class="n save">+${zl(x.amount)}</td></tr>`
          + (open ? bankDetail(r, key) : "");
      }
      const p = r.p, one = r.items.length === 1 ? r.items[0] : null, key = `${p.id}|${r.type}|${r.atm ? 1 : 0}`, open = S.bankOpen.has(key);
      const what = r.items.slice(0, 3).map(it => esc(it.name)).join("; ") + (r.items.length > 3 ? ` … ещё ${r.items.length - 3}` : "");
      return `<tr class="${one && PENDING.has(`item|${p.id}|${one.line}`) ? "pending" : ""}${open ? " opened" : ""}">
        <td>${p.date.slice(8, 10)}.${p.date.slice(5, 7)}.${p.date.slice(2, 4)}</td><td>${chip(kindOf(r))}</td>
        <td>${openBtn(key, open, p.merchant, p.card ? ` <span class="src">•${esc(p.card)}</span>` : "")}</td>
        <td>${what}${one ? noteHtml(one) : ""}${pnote(p) ? `<br><span class="warn" style="font-size:.8em">${esc(pnote(p))}</span>` : ""}</td>
        <td style="white-space:nowrap">${one ? catPick(one.cat, "item", {name: one.name, pid: p.id, line: one.line}) + noteBtn(p.id, one)
          : `<button class="linkbtn" data-bopen="${esc(key)}">${r.items.length} поз. — раскрыть</button>`}</td>
        <td class="n">${zl(r.amount)}</td></tr>` + (open ? bankDetail(r, key) : "");
    }).join("") + `</table>${rows.length > 800 ? `<p class="muted">Показаны первые 800 из ${rows.length} — выбери месяц в таблице или на графике.</p>` : ""}`;
}

// раскрытие операции: что за операция в выписке, откуда покупка, все позиции с категориями, что ещё было в эти дни
const openBtn = (key, open, who, extra = "") => `<button class="cellbtn" data-bopen="${esc(key)}" title="${open ? "свернуть" : "подробнее"}">
  <div class="who">${ava(who)}<span>${open ? "▾" : "▸"} ${esc(who)}${extra}</span></div></button>`;
function bankDetail(r, key) {
  const fresh = S.bankJust === key ? " fresh" : "";
  if (r.inc) {
    const x = r.inc, same = (D.incomes || []).filter(y => y.who && y.who === x.who);
    const src = {manual: "поставлена тобой", wallet: "взнос наличных — доход", rule: "по правилу выписки"}[x.src] || "не определена";
    return `<tr class="occ${fresh}"><td colspan="6"><table class="occt"><tr><th>операция в банке</th><th>категория</th><th>от этого отправителя</th></tr>
      <tr><td>${esc(bankType(x.type))} · ${ddmmyy(x.date)}<div class="muted">${esc([x.who, x.desc].filter(Boolean).join(" · "))}</div>
        <div class="muted">${esc(x.type)}</div></td>
      <td>${esc(catLabel(x.cat))}<div class="muted">${src}</div></td>
      <td>${same.length > 1 ? `${same.length} поступлений на ${zl(same.reduce((s, y) => s + y.amount, 0))}<div class="muted">первое ${
        ddmmyy(same[same.length - 1].date)}, последнее ${ddmmyy(same[0].date)}</div>` : `<span class="muted">это единственное</span>`}</td></tr></table></td></tr>`;
  }
  const p = r.p, b = p.bank;
  return `<tr class="occ${fresh}"><td colspan="6"><table class="occt"><tr><th>откуда</th><th>операция в банке</th>
      ${p.source === "bank" ? "<th>чеки и заказы в эти дни</th>" : ""}</tr>
    <tr><td>${esc(SOURCES[p.source] || p.source)} · ${esc(D.payments[p.pay ?? "unknown"] || p.pay || "")}${p.card ? " •" + esc(p.card) : ""}${
        p.file ? ` · <a href="/file/${esc(p.file)}" target="_blank">${p.source === "email" ? "✉ письмо" : "📷 фото"}</a>` : ""}${photoLinks(p)}
        ${p.store && p.source !== "bank" ? `<div class="muted">${esc(p.store)}</div>` : ""}
        ${p.orig_total != null ? `<div class="muted">в чеке ${zl(p.orig_total)}, списано ${zl(p.total)}</div>` : ""}
        ${refundInfo(p)}${pnote(p) ? `<div class="warn" style="font-size:.8em">${esc(pnote(p))}</div>` : ""}</td>
      <td>${b ? `${esc(bankType(b.type))} · ${ddmmyy(b.date)}<div class="muted">${esc([b.who, b.desc].filter(Boolean).join(" · "))}</div>`
        : `<span class="muted">${p.pay === "cash" ? "наличные" : "нет в выписке PKO"}</span>`}</td>
      ${p.source === "bank" ? `<td>${nearby(p)}</td>` : ""}</tr></table>
    <table class="occt" style="margin-top:6px"><tr><th>позиция</th><th>категория</th><th class="n">сумма</th></tr>` +
    r.items.map(it => `<tr class="${PENDING.has(`item|${p.id}|${it.line}`) ? "pending" : ""}"><td>${esc(it.name)}${noteHtml(it)}</td>
      <td style="white-space:nowrap">${catPick(it.cat, "item", {name: it.name, pid: p.id, line: it.line})}${noteBtn(p.id, it)}
        <span class="src">${SRC_LABEL[it.cat_src] || ""}</span></td><td class="n">${zl(net(it))}</td></tr>`).join("") +
    `</table></td></tr>`;
}

// ---------- наличные на руках
let WALLET = null, cashChart = null;
const WKIND = {count: "пересчёт", income: "пришли наличные", expense: "расход без чека", atm: "снято в банкомате",
               deposit: "внесено на счёт", receipt: "оплата наличными по чеку"};
async function renderCash() {
  const main = document.getElementById("main");
  if (!WALLET) WALLET = await (await fetch("/api/wallet")).json();
  const W = WALLET, ev = W.events || [];
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const sum = k => ev.filter(e => e.kind === k).reduce((s, e) => s + e.amount, 0);
  const lastCount = [...ev].reverse().find(e => e.kind === "count");
  const untracked = ev.filter(e => e.kind === "count" && e.diff > 0).reduce((s, e) => s + e.diff, 0);
  const extraInc = ev.filter(e => e.kind === "count" && e.diff < 0).reduce((s, e) => s - e.diff, 0);
  const form = `<div class="form">
      <label>дата и время<input type="datetime-local" id="wdate" value="${now}"></label>
      <label>что произошло<select id="wkind">
        <option value="count">пересчёт: на руках сейчас</option>
        <option value="income">пришли наличные, не с карты (+)</option>
        <option value="expense">расход наличными без чека (−)</option></select></label>
      <label>сумма, zł<input id="wamount" inputmode="decimal" placeholder="0,00" style="width:110px"></label>
      <label style="flex:1;min-width:180px">комментарий<input id="wnote" placeholder="например: зарплата, рынок, подарок"></label>
      <button class="btn" id="wsave">Сохранить запись</button></div>`;
  if (!W.start) {
    main.innerHTML = `<h2>Наличные на руках</h2>
      <p>Учёт ещё не начат. Посчитай наличные в кошельке и внеси <b>пересчёт</b> — это стартовая точка.
      Дальше снятия в банкомате будут прибавляться, а взносы на счёт и покупки по чекам за наличные — вычитаться сами.</p>${form}
      <p class="muted">Доход наличными считается в момент <b>взноса на счёт</b> — он виден в доходах на вкладке «Банк».
      Записи здесь только ведут остаток на руках и в доходы не идут, чтобы одни и те же деньги не посчитались дважды.</p>`;
    return;
  }
  main.innerHTML = `
    <div class="cards">
      ${W.bank && W.bank.balance != null ? `<div class="card">всего: наличные + карта<b>${zl((W.balance || 0) + W.bank.balance)}</b>
        наличные ${zl(W.balance || 0)} + на счёте PKO ${zl(W.bank.balance)} <span class="muted">(${ddmmyy(W.bank.date)})</span></div>` : ""}
      <div class="card">на руках сейчас (расчёт)<b class="${W.balance < 0 ? "up" : ""}">${zl(W.balance)}</b>
        ${W.balance < 0 ? "меньше нуля — похоже, не записаны пришедшие наличные или пересчёт" : "проверь пересчётом"}</div>
      <div class="card">последний пересчёт<b>${lastCount ? zl(lastCount.amount) : "—"}</b>${lastCount ? lastCount.date.slice(0, 16).replace("T", " ") : ""}</div>
      <div class="card">снято в банкомате<b>${zl(sum("atm"))}</b>прибавлено к наличным</div>
      <div class="card">внесено на счёт — доход<b class="save">${zl(sum("deposit"))}</b>в доходах на вкладке «Банк»</div>
      <div class="card">пришло наличными<b>${zl(sum("income") + extraInc)}</b>${extraInc ? `из них по пересчёту ${zl(extraInc)}` : "твои записи, в доходы не идут"}</div>
      <div class="card">потрачено наличными<b>${zl(sum("receipt") + sum("expense") + untracked)}</b>
        по чекам ${zl(sum("receipt"))}, без чека ${zl(sum("expense") + untracked)}</div>
    </div>
    ${form}
    <h2>Остаток наличных</h2><div class="chartbox"><canvas id="cashc" height="80"></canvas></div>
    <h2>Движение наличных</h2>
    <table><tr><th>когда</th><th>событие</th><th class="n">сумма</th><th class="n">остаток</th><th>комментарий</th><th></th></tr>` +
    [...ev].reverse().map(e => {
      const sign = e.kind === "count" ? "=" : (e.kind === "income" || e.kind === "atm") ? "+" : "−";
      const diff = e.kind === "count" && e.diff != null && Math.abs(e.diff) >= 0.01
        ? `<br><span class="${e.diff > 0 ? "up" : "save"}">${e.diff > 0 ? "без чека потрачено " + zl(e.diff) : "неучтённый приход " + zl(-e.diff)}</span>` : "";
      const hm = s => s.slice(11, 16);
      const whenCell = e.tx ? `${e.date.slice(0, 10)} ${e.fixed ? hm(e.date) : e.window ? `${hm(e.window[0]) === "00:00" ? "до" : hm(e.window[0]) + "–"}${hm(e.window[1])}` : ""}
          <button class="mini${e.ambiguous ? " has" : ""}" type="button" data-wtime="${esc(e.tx)}" data-wday="${e.bank_date}"
            data-wcur="${e.fixed ? hm(e.date) : ""}" title="уточнить время (банк сообщает только дату)">🕓</button>
          ${e.ambiguous ? `<div class="warn" style="font-size:.8em">время неизвестно — до или после твоей записи? уточни</div>`
            : e.fixed ? `<div class="muted" style="font-size:.8em">время уточнено тобой</div>` : ""}`
        : e.date.slice(0, 16).replace("T", " ");
      return `<tr><td>${whenCell}</td><td>${WKIND[e.kind]}${e.auto ? ' <span class="src">авто</span>' : ""}${diff}</td>
        <td class="n">${sign} ${zl(e.amount)}</td><td class="n">${zl(e.balance)}</td><td>${esc(e.note || "")}</td>
        <td>${e.auto ? "" : `<button class="chip act" data-wdel="${e.id}">удалить</button>`}</td></tr>`;
    }).join("") + `</table>
    <p class="muted">Банк сообщает только дату взноса или снятия, поэтому время — интервал между двумя загрузками выписки:
      операция появилась в нём. Если твой пересчёт попадает внутрь интервала, порядок неизвестен — нажми 🕓 и укажи время.
      Авто-записи: банкомат (+) и взносы на счёт (−) из выписки, оплата наличными по чекам (−).
      Взнос на счёт уменьшает наличные на руках и считается доходом («Доходы › Наличные»).
      При пересчёте разница с расчётом записывается как траты без чека (категория «Прочее › Наличные без чека»)
      или как неучтённый приход наличных.</p>`;
  cashChart?.destroy();
  cashChart = new Chart(document.getElementById("cashc"), {type: "line",
    data: {labels: ev.map(e => e.date.slice(0, 10)), datasets: [{label: "наличные, zł", data: ev.map(e => e.balance),
      borderColor: css("--c-violet"), backgroundColor: alpha(css("--c-violet"), .12), fill: "origin", stepped: true, pointRadius: 2}]},
    options: {animation: chartAnim(), plugins: {legend: {display: false}}}});
}
async function walletSave() {
  const amount = document.getElementById("wamount").value.replace(",", ".").replace(/\s/g, "");
  if (!amount || isNaN(+amount)) { toast("Укажи сумму"); return; }
  await post("/api/wallet/add", {date: document.getElementById("wdate").value + ":00", kind: document.getElementById("wkind").value,
                                 amount: +amount, note: document.getElementById("wnote").value});
  toast("Запись сохранена"); WALLET = null; BANK = null; await load();
}
