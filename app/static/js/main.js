// Общий рендер, уведомления, сохранение, обработчики событий, запуск.
// ---------- общий рендер
const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const when = d => !d ? "" : d.slice(0, 10) === today() ? (d.slice(11, 16) || "сегодня") : `${d.slice(8, 10)}.${d.slice(5, 7)}`;
function renderNav(unknownCount, setWarn) {
  const nDoubt = D.purchases.filter(p => p.status === "doubt").length;
  const nav = document.getElementById("nav"), ul = D.meta.update_last, lp = D.meta.last_purchase, lm = lp ? lp.slice(0, 7) : null;
  let monthSpent = 0;
  if (lm) for (const p of D.purchases) if (p.date.startsWith(lm)) for (const it of p.items) if (isExp(it)) monthSpent += net(it);
  const info = {
    overview: {s: lm ? `${MONTHS[+lm.slice(5) - 1]}: ${zl(monthSpent)}` : "покупок пока нет", tm: when(lp)},
    bank: {s: !D.meta.bank_last_date ? "не подключён" : D.meta.bank_last_date === today() ? "выписка за сегодня"
           : `выписка по ${when(D.meta.bank_last_date)}`,
           bd: D.meta.bank_days_left != null && D.meta.bank_days_left <= 21 ? `<span class="badge red">!</span>` : ""},
    cash: {s: WALLET && WALLET.start ? `на руках ${zl(WALLET.balance)}` : "наличные на руках"},
    deals: {s: !D.meta.deals ? "газетки Lidl" : D.meta.deals.deals ? `${D.meta.deals.deals} акций на твои позиции`
              : `газеток: ${D.meta.deals.flyers}, на позиции акций нет`, tm: D.meta.deals ? when(D.meta.deals.updated || "") : "",
            bd: D.meta.deals && D.meta.deals.deals ? `<span class="badge">${D.meta.deals.deals}</span>` : ""},
    unknown: {s: [unknownCount ? `${unknownCount} без категории` : "", nDoubt ? `${nDoubt} под вопросом` : ""].filter(Boolean).join(" · ")
                 || "всё разложено ✓",
              bd: unknownCount + nDoubt ? `<span class="badge">${unknownCount + nDoubt}</span>` : ""},
    categories: {s: `${D.categories.length} категорий · ${D.rules.length} правил`},
    settings: {s: updRunning() ? `<span class="spin"></span>идёт обновление` : ul ? (ul.status === "error" ? "обновление с ошибкой"
               : ul.status === "warn" ? "обновлено, есть замечания" : "обновлено без ошибок") : "ещё не обновлялось",
               tm: ul ? when(ul.finished) : "", bd: setWarn ? `<span class="badge red">!</span>` : ""}};
  for (const P of PAGES) {
    let b = nav.querySelector(`.chat[data-page="${P.k}"]`);
    if (!b) {
      b = document.createElement("button");
      b.className = "chat"; b.dataset.page = P.k; b.title = P.t;
      b.innerHTML = `<span class="ico" style="--a:${P.c[0]};--b:${P.c[1]}">${ICON[P.k]}</span><span class="t" data-short="${P.short || P.t}">${P.t}</span>
        <span class="s"></span><span class="tm"></span><span class="bd"></span>`;
      nav.appendChild(b);
    }
    b.classList.toggle("on", S.page === P.k);
    for (const [cls, html] of [["s", info[P.k].s], ["tm", info[P.k].tm], ["bd", info[P.k].bd]]) {
      const el = b.querySelector("." + cls);
      if (el.dataset.v !== (html || "")) { el.dataset.v = html || ""; el.innerHTML = html || ""; }  // без лишней перерисовки
    }
  }
}
function render() {
  chartTheme();
  const unknownCount = aggregate(D.purchases.filter(p => p.status !== "doubt")).filter(a => a.cat == null || a.cats[""]).length;
  const ul = D.meta.update_last, bdays = D.meta.bank_days_left;
  const setWarn = !ul || ul.status === "error" || (bdays != null && bdays <= 21);
  renderNav(unknownCount, setWarn);
  document.body.dataset.page = S.page;
  document.getElementById("ptitle").textContent = PAGES.find(x => x.k === S.page).t;
  const lp = D.meta.last_purchase;
  document.getElementById("banner").innerHTML = `последняя покупка: ${lp ? lp.slice(0, 10) : "—"} ·
    ${D.meta.bank_last_date ? `выписка по ${D.meta.bank_last_date}` : `<span class="warn">выписка не подключена</span>`} ·
    <button class="linkbtn" data-page="settings">${updRunning() ? `<span class="spin"></span>идёт обновление`
      : ul ? `обновлено ${dtf(ul.finished)} ${ul.status === "error" ? `<span class="up">✗ ошибка</span>` : ul.status === "warn" ? `<span class="warn">!</span>` : "✓"}`
      : `<span class="warn">ещё не обновлялось</span>`}</button>`;
  if (S.page === "settings" || S.page === "deals") document.getElementById("filters").innerHTML = "";  // фильтры там ни к чему
  else renderFilters();
  CHART_ANIM = S.anim;
  const done = ({overview: renderOverview, bank: renderBank, cash: renderCash, unknown: renderUnknown, categories: renderCategories,
    settings: renderSettings, deals: renderDeals})[S.page]();
  if (S.anim) { S.anim = false; Promise.resolve(done).then(() => animateEnter(document.getElementById("main"))); }
  updateSavebar();
}

function toast(msg, o = {}) {  // o.action + o.onAction — кнопка «Вернуть» с обратным отсчётом, как в Telegram; o.spin — идёт работа
  const t = document.getElementById("toast"), ms = o.ms || (o.action ? 7000 : 2600);
  clearInterval(t._cd);
  t.innerHTML = (o.spin ? `<span class="spin"></span>` : "") +
    (o.action ? `<span class="cd"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" style="animation-duration:${ms}ms"/></svg><b>${Math.round(ms / 1000)}</b></span>` : "") +
    `<span>${esc(msg)}</span>` + (o.action ? `<button class="tact" type="button">${esc(o.action)}</button>` : "");
  t.classList.toggle("act", !!o.action);
  if (o.action) {
    const end = Date.now() + ms;
    t._cd = setInterval(() => { const b = t.querySelector(".cd b"); if (b) b.textContent = Math.max(0, Math.ceil((end - Date.now()) / 1000)); }, 250);
    t.querySelector(".tact").onclick = () => { t.classList.remove("show"); clearInterval(t._cd); o.onAction(); };
  }
  t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => { t.classList.remove("show"); clearInterval(t._cd); }, ms);
}

async function post(url, body) {
  const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const j = await r.json();
  if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
  return j;
}

async function setCategory(body) {
  await post("/api/set-category", body);
  toast(body.mode === "item" ? "Категория позиции изменена" : `«${body.name}»: категория изменена для всех таких и запомнена`);
  BANK = null;
  const y = scrollY; await load(); scrollTo(0, y);
}

// ---------- события
document.addEventListener("click", async e => {
  if (e.target.closest("#picker, #modal, #toast")) return;
  const cp = e.target.closest("code[data-copy]");
  if (cp) { navigator.clipboard.writeText(cp.dataset.copy).then(() => toast("Скопировано: " + cp.dataset.copy)); return; }
  const pick = e.target.closest(".catpick");
  if (pick) { e.preventDefault(); openPicker(pick); return; }
  if (PICK) closePicker();
  const t = e.target.closest("button, th");
  if (!t) { hidePopup(e); return; }
  if (t.id === "savebtn") { await saveAll(); return; }
  if (t.id === "themebtn") { toggleTheme(e); return; }
  if (t.dataset.hide) { e.preventDefault(); await hidePurchase(t.dataset.hide); return; }
  if (t.dataset.confirm) { e.preventDefault(); await confirmPurchase(t.dataset.confirm); return; }
  if (t.dataset.cpt) {
    const id = +t.dataset.cpt; S.openCatPage.has(id) ? S.openCatPage.delete(id) : S.openCatPage.add(id);
    S.justOpenedCatPage = S.openCatPage.has(id) ? id : null; renderCategories(); S.justOpenedCatPage = null; return;
  }
  if (t.dataset.cptall) {
    S.openCatPage = t.dataset.cptall === "1" ? new Set(Object.values(CAT).filter(c => (KIDS[c.id] || []).length).map(c => c.id)) : new Set();
    renderCategories(); return;
  }
  if (t.dataset.dupdate) {
    const j = await dealsAction(() => post("/api/deals/update", {}), "Скачиваю свежие газетки Lidl…");
    if (j) { await refreshDeals(); toast(`Газетки обновлены: ${j.flyers}`); }
    return;
  }
  if (t.dataset.wadd || t.dataset.waddQ) {
    const inp = document.getElementById("wq"), q = (t.dataset.waddQ || (inp && inp.value) || "").trim();
    if (!q) { inp && inp.focus(); return; }
    if (await dealsAction(() => post("/api/deals/watch", {action: "add", q}))) { await refreshDeals(); drawDealSearch(); toast(`«${q}» — в постоянных позициях`); }
    return;
  }
  if (t.dataset.wrm) {
    const q = t.dataset.wrm;
    if (await dealsAction(() => post("/api/deals/watch", {action: "remove", q}))) {
      await refreshDeals();
      toast(`«${q}» убран из постоянных`, {action: "Вернуть", onAction: async () => { await post("/api/deals/watch", {action: "add", q}); await refreshDeals(); }});
    }
    return;
  }
  if (t.dataset.dsearch) { await dealSearch(); return; }
  if (t.dataset.rat) {
    const v = document.getElementById("rat").value;
    const j = await dealsAction(() => post("/api/deals/settings", {remind_at: v}));
    if (j) { DEALS.telegram = j.telegram;
             toast(`Новые напоминания — накануне в ${j.telegram.remind_at}. Уже поставленные остаются на прежнее время`, {ms: 5000}); }
    return;
  }
  if (t.dataset.wtime) {
    const v = await promptBox({title: "Во сколько это было?", value: t.dataset.wcur || "", placeholder: "ЧЧ:ММ, например 20:15",
      text: `${t.dataset.wday}: банк сообщает только дату. Укажи время, чтобы операция встала до или после твоего пересчёта. Пусто — снова по выписке.`,
      ok: "Сохранить"});
    if (v === null) return;
    const hm = v.trim().replace(".", ":");
    if (hm && !/^([01]?\d|2[0-3]):[0-5]\d$/.test(hm)) { toast("Время в формате ЧЧ:ММ"); return; }
    await post("/api/wallet/time", {tx: t.dataset.wtime, at: hm ? `${t.dataset.wday}T${hm.padStart(5, "0")}` : null});
    WALLET = null; BANK = null; const y = scrollY; await load(); scrollTo(0, y);
    toast(hm ? `Время операции: ${hm}` : "Время — снова по выписке"); return;
  }
  if (t.dataset.dhist) { await dealHistory(); return; }
  if (t.dataset.remind) {
    const j = await dealsAction(() => post("/api/deals/remind", {q: t.dataset.q, keys: [t.dataset.remind]}), "Ставлю напоминание в Telegram…");
    if (j) {
      const r = j.made[0];
      toast(!r ? "Акция не найдена — обнови газетки" : r.skipped ? "Напоминание уже стояло" : `Напоминание в Избранном: ${r.when}`);
      DEALS = null; DSEARCH = DSEARCH && {...DSEARCH, deals: (await (await fetch("/api/deals/search?q=" + encodeURIComponent(DSEARCH.q))).json()).deals};
      const y = scrollY; await renderDeals(); scrollTo(0, y);
    }
    return;
  }  // preventDefault: не сворачивать чек
  if (t.dataset.restore) { await restorePurchase(t.dataset.restore); return; }
  if (t.dataset.notePid) { await editNote(t.dataset); return; }
  if (t.dataset.cattog) {
    const k = t.dataset.cattog;
    S.openCats.has(k) ? S.openCats.delete(k) : S.openCats.add(k);
    S.justOpenedCat = S.openCats.has(k) ? k : null;
    document.getElementById("body").innerHTML = catsTable(...CATS_RS); S.justOpenedCat = null; return;
  }
  if (t.dataset.catall) {
    S.openCats = t.dataset.catall === "1" ? new Set(CATS_BRANCHES) : new Set();
    document.getElementById("body").innerHTML = catsTable(...CATS_RS); return;
  }
  if (t.dataset.bankall) { S.bankMonth = null; S.bankCell = null; drawBankList(); return; }
  if (t.dataset.bcell) {
    const cell = t.dataset.bcell === "all" ? null : t.dataset.bcell;
    if (S.bankMonth === t.dataset.bm && S.bankCell === cell) { S.bankMonth = null; S.bankCell = null; drawBankList(); return; }
    S.bankMonth = t.dataset.bm; S.bankCell = cell; drawBankList();
    document.getElementById("banklist").scrollIntoView({behavior: "smooth"}); return;
  }
  if (t.dataset.bankcellReset) { S.bankCell = null; drawBankList(); return; }
  if (t.dataset.occ !== undefined) {
    const k = t.dataset.occ; S.openOcc.has(k) ? S.openOcc.delete(k) : S.openOcc.add(k);
    S.justOpened = S.openOcc.has(k) ? k : null;
    const y = scrollY; render(); scrollTo(0, y); S.justOpened = null; return;
  }
  if (t.dataset.updrun !== undefined) { await startUpdate(t.dataset.updrun ? [t.dataset.updrun] : []); return; }
  if (t.dataset.sched) { await setSchedule(t.dataset.sched === "on"); return; }
  if (t.dataset.monthOpen) {
    S.page = "overview"; S.gran = "month"; S.range = null; S.crumbs = []; S.period = t.dataset.monthOpen; render(); scrollTo(0, 0); return;
  }
  if (t.dataset.flow) { S.flow = t.dataset.flow; S.period = "all"; CHART_ANIM = true; renderOverview(); return; }
  if (t.dataset.bopen) {
    const k = t.dataset.bopen; S.bankOpen.has(k) ? S.bankOpen.delete(k) : S.bankOpen.add(k);
    S.bankJust = S.bankOpen.has(k) ? k : null; drawBankList(); S.bankJust = null; return;
  }
  if (t.dataset.gran) { S.gran = t.dataset.gran; S.range = null; S.crumbs = []; S.period = null; CHART_ANIM = true; renderOverview();
                        animateEnter(document.getElementById("main")); return; }
  if (t.dataset.back) { backTo(S.crumbs.length - 1); return; }
  if (t.dataset.crumb !== undefined) { backTo(+t.dataset.crumb); return; }
  if (t.id === "cancelbtn") { PENDING.clear(); updateSavebar(); render(); return; }
  if (t.dataset.page) {
    S.page = t.dataset.page; S.anim = true;
    if (S.page === "settings" && !UPD_WAIT) { UPD = null; SCHED = null; }
    render(); scrollTo(0, 0);
  }
  else if (t.dataset.period) { S.period = t.dataset.period; CHART_ANIM = false; renderOverview(); animateEnter(document.getElementById("panel")); }
  else if (t.dataset.view) { S.view = t.dataset.view; CHART_ANIM = false; renderOverview(); animateEnter(document.getElementById("body")); }
  else if (t.dataset.sort) {
    const k = t.dataset.sort;
    S.sort = {key: k, dir: S.sort.key === k ? -S.sort.dir : (k === "name" || k === "catName" ? 1 : -1)};
    render();
  }
  else if (t.dataset.pay) { S.F.pay.has(t.dataset.pay) ? S.F.pay.delete(t.dataset.pay) : S.F.pay.add(t.dataset.pay); render(); }
  else if (t.id === "reset") {
    S.F = {from: "", to: "", pay: new Set(), merchant: "", cats: new Set(), source: "", q: ""};
    document.getElementById("q").value = ""; render();
  }
  else if ("addcat" in t.dataset) {
    const parent = t.dataset.addcat ? +t.dataset.addcat : null;
    const name = await promptBox({title: parent ? "Новая подкатегория" : "Новая категория",
                                  text: parent ? `Внутри «${CAT[parent].path.replaceAll("/", " › ")}»` : "", ok: "Добавить"});
    if (name) { await post("/api/add-category", {name, parent_id: parent}); if (parent) S.openCatPage.add(parent);
                toast("Категория добавлена"); await load(); }
  }
  else if (t.dataset.rencat) {
    const c = CAT[t.dataset.rencat];
    const name = await promptBox({title: "Переименовать категорию", text: c.path.replaceAll("/", " › "), value: c.name, ok: "Сохранить"});
    if (name && name !== c.name) { await post("/api/category/rename", {id: c.id, name}); toast("Переименовано"); await load(); }
  }
  else if (t.dataset.delcat) {
    const c = CAT[t.dataset.delcat];
    if (await confirmBox({title: "Удалить категорию?", ok: "Удалить", danger: true,
        text: `«${c.path}». Позиции и подкатегории перейдут в «${c.parent_id ? CAT[c.parent_id].path : "неопознанные / верхний уровень"}».`})) {
      await post("/api/category/delete", {id: c.id}); toast("Категория удалена"); await load();
    }
  }
  else if (t.dataset.wdel) {
    if (await confirmBox({title: "Удалить запись о наличных?", text: "Остаток пересчитается.", ok: "Удалить", danger: true})) { await post("/api/wallet/delete", {id: +t.dataset.wdel}); WALLET = null; BANK = null; await load(); }
  }
  else if (t.id === "wsave") { await walletSave(); }
  else if (t.dataset.delrule) { await post("/api/delete-rule", {id: +t.dataset.delrule}); toast("Правило удалено"); await load(); }
  else if (t.dataset.choice) { await applyChoice(t.dataset.choice); }
  else hidePopup(e);
});

document.addEventListener("change", async e => {
  const t = e.target;
  if (t.dataset.f) { S.F[t.dataset.f] = t.value; render(); return; }
  if (t.dataset.only) { const p = PENDING.get(t.dataset.only); if (p) p.mode = t.checked ? "item" : "same"; }
});

let pending = null;
function showPopup(sel, cat) {
  pending = {sel, cat};
  const p = document.getElementById("popup"), r = sel.getBoundingClientRect();
  p.innerHTML = `<div class="muted">«${esc(sel.dataset.name)}» → ${cat == null ? "неопознанные" : esc(CAT[cat].path)}</div>
    <button data-choice="same">для всех таких позиций (и запомнить)</button>
    <button data-choice="item">только для этой позиции</button>
    <button data-choice="cancel">отмена</button>`;
  p.style.left = (r.left + scrollX) + "px"; p.style.top = (r.bottom + scrollY + 4) + "px"; p.style.display = "block";
}
function hidePopup(e) {
  const p = document.getElementById("popup");
  if (p.style.display === "block" && !(e && p.contains(e.target))) { p.style.display = "none"; if (pending) { pending = null; render(); } }
}
async function applyChoice(choice) {
  const {sel, cat} = pending; pending = null;
  document.getElementById("popup").style.display = "none";
  if (choice === "cancel") { render(); return; }
  if (choice === "same") await setCategory({mode: "same", name: sel.dataset.name, category_id: cat});
  else await setCategory({mode: "item", purchase_id: sel.dataset.pid, line: +sel.dataset.line, category_id: cat});
}

async function dealSearch() {
  const q = (document.getElementById("dq") || {}).value?.trim();
  if (!q) return;
  DSEARCH = await (await fetch("/api/deals/search?q=" + encodeURIComponent(q))).json();
  drawDealSearch(); animateEnter(document.getElementById("dres"));
}
async function dealHistory() {
  const q = (document.getElementById("hq") || {}).value?.trim();
  if (!q) return;
  DHIST = await (await fetch("/api/deals/history?q=" + encodeURIComponent(q))).json();
  CHART_ANIM = true; drawHistory(); CHART_ANIM = false; animateEnter(document.getElementById("dhist"));
}
document.addEventListener("keydown", e => {  // Enter в полях страницы «Скидки»
  if (e.key !== "Enter" || !e.target.id) return;
  if (e.target.id === "dq") dealSearch();
  else if (e.target.id === "hq") dealHistory();
  else if (e.target.id === "wq") document.querySelector('[data-wadd="1"]').click();
});
// панель разделов: нажатие на «Бюджет» сворачивает до иконок, повторное — разворачивает (запоминается)
const SIDE_KEY = "budget-side";
function setSide(collapsed) {
  document.querySelector(".app").classList.toggle("collapsed", collapsed);
  try { localStorage.setItem(SIDE_KEY, collapsed ? "collapsed" : "open"); } catch (e) { /* только до перезагрузки */ }
}
document.querySelector(".side-head").title = "свернуть / развернуть список разделов";
document.querySelector(".side-head").addEventListener("click", () => setSide(!document.querySelector(".app").classList.contains("collapsed")));
try { if (localStorage.getItem(SIDE_KEY) === "collapsed") document.querySelector(".app").classList.add("collapsed"); } catch (e) { /* нет хранилища */ }

// широкие таблицы прокручиваются внутри своей карточки, а не сдвигают всю страницу
new MutationObserver(() => {
  for (const tb of document.querySelectorAll("#main table")) {
    if (tb.parentElement.classList.contains("tscroll") || tb.parentElement.closest("table")) continue;
    const w = document.createElement("div");
    w.className = "tscroll"; tb.before(w); w.appendChild(tb);
  }
}).observe(document.getElementById("main"), {childList: true, subtree: true});

// поиск в шапке: по названию товара или магазину (с задержкой, чтобы не перерисовывать на каждую букву)
let qTimer = null;
const qInput = document.getElementById("q");
qInput.addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(() => { S.F.q = qInput.value.trim(); render(); }, 200); });
qInput.addEventListener("keydown", e => { if (e.key === "Escape") { qInput.value = ""; S.F.q = ""; render(); } });

document.querySelector(".logo").innerHTML = ICON.logo;
document.getElementById("qicon").innerHTML = ICON.search;
applyTheme(document.documentElement.dataset.theme);
load();
