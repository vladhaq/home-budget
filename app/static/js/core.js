// Состояние, оформление, фильтры, выбор категории. Файлы — обычные скрипты с общей областью видимости,
// подключаются по порядку (core → overview → pages → deals → settings → main); main.js запускает load().
const MONTHS = ["январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"];
const SOURCES = {lidl: "Lidl Plus", kaufland: "Kaufland", photo: "фото чека", email: "почта", bank: "банк (без чека)"};
const zl = x => (x ?? 0).toLocaleString("ru-RU", {minimumFractionDigits: 2, maximumFractionDigits: 2}) + " zł";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const monthName = m => MONTHS[+m.slice(5) - 1] + " " + m.slice(0, 4);
const net = it => (it.amount || 0) - (it.discount || 0);

let D = null, CAT = {}, KIDS = {};
const S = {page: "overview", month: null, view: "cats", sort: {key: "spent", dir: -1}, openOcc: new Set(), openCats: new Set(),
           openCatPage: new Set(), anim: true,
           flow: "out", bankOpen: new Set(),  // flow — что на графике обзора: out расходы / in поступления / both
           F: {from: "", to: "", pay: new Set(), merchant: "", cats: new Set(), source: "", q: ""}};

// ---------- оформление: иконки, тема, анимации
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)");
const SVG = d => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const ICON = {
  overview: SVG('<path d="M4 20h16"/><path d="M7 16v-5"/><path d="M12 16V6"/><path d="M17 16v-8"/>'),
  bank: SVG('<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3 10h18"/><path d="M7 15h4"/>'),
  cash: SVG('<path d="M4 7.5V7a2 2 0 0 1 2-2h11"/><rect x="3" y="7.5" width="18" height="12" rx="2.5"/><path d="M16.5 13.5h.01"/>'),
  unknown: SVG('<circle cx="12" cy="12" r="9"/><path d="M9.6 9.3a2.5 2.5 0 1 1 3.6 2.3c-.8.4-1.2 1-1.2 1.8v.3"/><path d="M12 17h.01"/>'),
  categories: SVG('<path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2.2h7.2a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>'),
  settings: SVG('<path d="M4 7h9"/><path d="M17 7h3"/><circle cx="15" cy="7" r="2"/><path d="M4 12h3"/><path d="M11 12h9"/>' +
                '<circle cx="9" cy="12" r="2"/><path d="M4 17h11"/><path d="M19 17h1"/><circle cx="17" cy="17" r="2"/>'),
  search: SVG('<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>'),
  moon: SVG('<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>'),
  sun: SVG('<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6 6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/>'),
  trash: SVG('<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/><path d="M9 7V4h6v3"/>'),
  note: SVG('<path d="M5 5h14a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 16h-8l-5 4v-4H5a1.5 1.5 0 0 1-1.5-1.5v-8A1.5 1.5 0 0 1 5 5z"/><path d="M8 9.5h8M8 12.5h5"/>'),
  deals: SVG('<path d="M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L3.6 13.6A2 2 0 0 1 3 12.2V5a2 2 0 0 1 2-2h7.2a2 2 0 0 1 1.4.6l7 7a2 2 0 0 1 0 2.8z"/><path d="M7.5 7.5h.01"/><path d="M15.5 9.5l-6 6"/>'),
  logo: SVG('<path d="M4 8.5A2.5 2.5 0 0 1 6.5 6H18a2 2 0 0 1 2 2v1"/><rect x="4" y="9" width="16" height="10" rx="2.5"/><path d="M15.5 14h.01"/>'),
};
// разделы — как чаты: цвета аватаров Telegram
const PAGES = [
  {k: "overview", t: "Обзор", c: ["#72d5fd", "#2a9ef1"]}, {k: "bank", t: "Банк", c: ["#a0de7e", "#54cb68"]},
  {k: "cash", t: "Наличные", c: ["#ffcd6a", "#ffa85c"]}, {k: "deals", t: "Скидки", c: ["#e0a2f3", "#d669ed"]}, {k: "unknown", t: "Неопознанные", short: "Неопозн.", c: ["#ff885e", "#ff516a"]},
  {k: "categories", t: "Категории", c: ["#82b1ff", "#665fff"]}, {k: "settings", t: "Настройки", c: ["#53edd6", "#28c9b7"]}];
const AVA = [["#ff885e", "#ff516a"], ["#ffcd6a", "#ffa85c"], ["#82b1ff", "#665fff"], ["#a0de7e", "#54cb68"],
             ["#53edd6", "#28c9b7"], ["#72d5fd", "#2a9ef1"], ["#e0a2f3", "#d669ed"]];
function ava(name, cls = "sm") {  // кружок с буквами, цвет по имени — как аватар собеседника
  const s = String(name || "?").trim();
  let h = 0; for (const ch of s.toLowerCase()) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const [a, b] = AVA[h % AVA.length];
  const words = s.match(/[\p{L}]+/gu) || [];
  const txt = /^\d/.test(s) || !words.length ? "#" : words.slice(0, 2).map(w => w[0]).join("").toUpperCase();
  return `<span class="ava ${cls}" style="--a:${a};--b:${b}">${esc(txt)}</span>`;
}
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const alpha = (hex, a) => { const n = parseInt(hex.replace("#", ""), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`; };
function chartTheme() {  // Chart.js в цветах текущей темы
  const C = Chart.defaults;
  C.color = css("--text-2"); C.borderColor = css("--line"); C.font.family = css("--font"); C.font.size = 12;
  C.animation.duration = REDUCED.matches ? 0 : 700;
  Object.assign(C.plugins.legend.labels, {usePointStyle: true, pointStyle: "circle", boxWidth: 8, boxHeight: 8, padding: 16});
  Object.assign(C.plugins.tooltip, {backgroundColor: css("--panel"), titleColor: css("--text"), bodyColor: css("--text"),
    borderColor: css("--border"), borderWidth: 1, padding: 10, cornerRadius: 10, boxPadding: 4, usePointStyle: true});
  C.elements.bar.borderRadius = 5; C.datasets.bar.maxBarThickness = 34;
  C.elements.line.borderWidth = 2;
}
const THEME_KEY = "budget-theme";
const themeStored = () => { try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; } };
function applyTheme(th) {
  document.documentElement.dataset.theme = th;
  const b = document.getElementById("themebtn");
  b.innerHTML = th === "dark" ? ICON.sun : ICON.moon;
  b.title = th === "dark" ? "Светлая тема" : "Тёмная тема";
}
async function toggleTheme(e) {  // круговое раскрытие новой темы от кнопки — как в Telegram
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem(THEME_KEY, next); } catch (err) { /* без сохранения — только до перезагрузки */ }
  const swap = () => { applyTheme(next); if (D) render(); };
  if (!document.startViewTransition || REDUCED.matches) { swap(); return; }
  const x = e.clientX || innerWidth - 40, y = e.clientY || 30;
  const r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  const vt = document.startViewTransition(swap);
  await vt.ready;
  document.documentElement.animate({clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`]},
    {duration: 650, easing: "cubic-bezier(.2,.8,.2,1)", pseudoElement: "::view-transition-new(root)"});
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", e => {
  if (!themeStored()) { applyTheme(e.matches ? "dark" : "light"); if (D) render(); }
});
function animateEnter(el) {  // появление содержимого снизу с задержкой по блокам + счётчики в карточках
  if (!el || REDUCED.matches) return;
  el.classList.remove("enter"); void el.offsetWidth; el.classList.add("enter");
  clearTimeout(el._enter); el._enter = setTimeout(() => el.classList.remove("enter"), 1300);
  for (const b of el.querySelectorAll(".card b")) countUp(b);
}
function countUp(el) {
  const m = el.textContent.match(/^(-?)([\d\u00a0\u202f ]+)(?:,(\d+))?(\s*(?:zł|%))?$/);
  if (!m) return;
  const dec = m[3] ? m[3].length : 0, target = parseFloat(m[1] + m[2].replace(/\D/g, "") + (dec ? "." + m[3] : ""));
  const fmt = v => v.toLocaleString("ru-RU", {minimumFractionDigits: dec, maximumFractionDigits: dec}) + (m[4] || "");
  const t0 = performance.now();
  const step = now => { const k = Math.min(1, (now - t0) / 700); el.textContent = fmt(target * (1 - Math.pow(1 - k, 3)));
                        if (k < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
let CHART_ANIM = true;  // графики «вырастают» только при входе на страницу и смене месяца, не при каждой правке
const chartAnim = () => CHART_ANIM && !REDUCED.matches ? {duration: 700} : false;
function modal({title, text = "", value = null, placeholder = "", ok = "OK", danger = false}) {
  // окно подтверждения (value = null) или ввода строки; resolve: true / введённый текст, или null — отмена
  return new Promise(resolve => {
    const m = document.getElementById("modal"), inp = m.querySelector("input"), okb = m.querySelector(".mok");
    m.querySelector("h3").textContent = title; m.querySelector("p").textContent = text;
    inp.style.display = value === null ? "none" : ""; inp.value = value ?? ""; inp.placeholder = placeholder;
    okb.textContent = ok; okb.classList.toggle("danger", danger);
    m.classList.add("show");
    setTimeout(() => (value === null ? okb : inp).focus(), 60);
    const done = v => { m.classList.remove("show"); document.removeEventListener("keydown", onKey, true); resolve(v); };
    const onKey = e => { if (e.key === "Escape") { e.stopPropagation(); done(null); }
                         if (e.key === "Enter") { e.preventDefault(); done(value === null ? true : inp.value); } };
    document.addEventListener("keydown", onKey, true);
    m.onclick = e => { if (e.target === m || e.target.closest(".mno")) done(null); else if (e.target.closest(".mok")) done(value === null ? true : inp.value); };
  });
}
const confirmBox = o => modal(o).then(v => v === true);
const promptBox = o => modal({...o, value: o.value ?? ""});
const IND = {};
function placeIndicators() {  // «бегунок» под выбранной вкладкой переезжает с прошлого места
  for (const tb of document.querySelectorAll(".tabs[data-key]")) {
    const on = tb.querySelector("button.on");
    let ind = tb.querySelector(".ind");
    if (!ind) { ind = document.createElement("span"); ind.className = "ind"; tb.prepend(ind); }
    if (!on) { ind.style.opacity = 0; continue; }
    const to = {l: on.offsetLeft, w: on.offsetWidth}, from = IND[tb.dataset.key];
    if (from) { ind.style.transition = "none"; ind.style.left = from.l + "px"; ind.style.width = from.w + "px"; void ind.offsetWidth; ind.style.transition = ""; }
    ind.style.left = to.l + "px"; ind.style.width = to.w + "px"; ind.style.opacity = 1;
    IND[tb.dataset.key] = to;
  }
}
document.addEventListener("pointerdown", e => {  // «волна» от точки нажатия
  const b = e.target.closest(".btn, .chip, .chat, .tabs button, #savebar button, .iconbtn, .nav > button, .mbtns button");
  if (!b || b.disabled || REDUCED.matches) return;
  const r = b.getBoundingClientRect(), d = Math.max(r.width, r.height) * 2.2, s = document.createElement("span");
  s.className = "ripple";
  s.style.cssText = `width:${d}px;height:${d}px;left:${e.clientX - r.left - d / 2}px;top:${e.clientY - r.top - d / 2}px`;
  b.appendChild(s); setTimeout(() => s.remove(), 650);
});

let REFUNDS = {};  // id покупки -> её возвраты на карту
async function load() {
  D = await (await fetch("/api/data")).json();
  WALLET = null;
  CAT = {}; KIDS = {}; REFUNDS = {};
  for (const c of D.categories) { CAT[c.id] = c; (KIDS[c.parent_id ?? 0] ??= []).push(c); }
  for (const p of D.purchases) if (p.refund_of) (REFUNDS[p.refund_of] ??= []).push(p);
  if (D.meta.update_running && !UPD_WAIT) {
    UPD_WAIT = {before: D.meta.update_last ? D.meta.update_last.id : 0, since: Date.now()};
    setTimeout(pollUpdate, 2000);
  }
  render();
}

// ---------- фильтры
function descendants(id) {
  const out = new Set([+id]);
  for (const k of KIDS[id] || []) for (const x of descendants(k.id)) out.add(x);
  return out;
}

// выбранные в фильтре категории вместе с подкатегориями; null — фильтр не задан
function selCats() {
  if (!S.F.cats.size) return null;
  const ids = new Set();
  for (const v of S.F.cats) if (v !== "none" && CAT[v]) for (const x of descendants(v)) ids.add(x);
  return {ids, none: S.F.cats.has("none")};
}
const inSel = (sel, cat) => cat == null || !CAT[cat] ? sel.none : sel.ids.has(cat);

// что вообще может быть в выбранных категориях: траты (расходы, переводы людям) и/или поступления (доходы, переводы от людей)
function flowSides() {
  const sel = selCats();
  if (!sel) return {out: true, in: true};
  const kinds = new Set([...sel.ids].map(id => CAT[id].kind));
  return {out: sel.none || kinds.has("expense") || kinds.has("transfer"), in: sel.none || kinds.has("income") || kinds.has("transfer")};
}
function curFlow() { const s = flowSides(); return !s.out ? "in" : !s.in ? "out" : S.flow; }

// поступления на счёт с теми же фильтрами. Без фильтра категорий — доходы (и без категории), переводы — только если выбраны
function filteredIncome() {
  const F = S.F, sel = selCats(), fq = F.q ? foldTxt(F.q) : "";
  if (F.pay.size || F.merchant || (F.source && F.source !== "bank")) return [];  // у поступлений нет способа оплаты и магазина
  return (D.incomes || []).filter(x => {
    const m = x.date.slice(0, 7), kind = x.cat != null && CAT[x.cat] ? CAT[x.cat].kind : null;
    if (F.from && m < F.from || F.to && m > F.to) return false;
    if (sel ? !inSel(sel, x.cat) : kind === "transfer") return false;
    return !fq || foldTxt(`${x.who} ${x.desc}`).includes(fq);
  }).map(x => ({id: "in:" + x.id, inc: x, source: "bank_in", date: x.date + "T12:00:00", value: x.amount, saved: 0,
                merchant: x.who || x.desc, items: [{line: 1, name: x.who || x.desc, amount: x.amount, discount: 0, cat: x.cat}]}));
}

function filtered() {
  const F = S.F, sel = selCats();
  const res = [];
  for (const p of D.purchases) {
    if (p.status === "doubt") continue;  // регистрация платежа без оплаты в банке — в очереди на «Неопознанных»
    const m = p.date.slice(0, 7);
    if (F.from && m < F.from) continue;
    if (F.to && m > F.to) continue;
    if (F.pay.size && !F.pay.has(p.pay ?? "unknown")) continue;
    if (F.merchant && p.merchant !== F.merchant) continue;
    if (F.source && p.source !== F.source) continue;
    // поиск: магазин целиком или отдельные позиции по названию
    const fq = F.q ? foldTxt(F.q) : "", shopHit = !fq || foldTxt(`${p.merchant} ${p.store}`).includes(fq);
    // переводы людям, банкомат, взносы — не расходы: в суммы не идут, если не выбраны фильтром явно
    const items = p.items.filter(it => (sel ? inSel(sel, it.cat) : !(it.cat != null && CAT[it.cat] && CAT[it.cat].kind !== "expense"))
                                       && (shopHit || foldTxt(`${it.name} ${it.note || ""}`).includes(fq)));
    if (!items.length) continue;
    const value = items.reduce((s, it) => s + net(it), 0);
    res.push({...p, items, value, saved: items.reduce((s, it) => s + (it.discount || 0), 0)});
  }
  return res;
}

function renderFilters() {
  const months = [...new Set(D.purchases.map(p => p.date.slice(0, 7)))].sort();
  const monthOpts = sel => `<option value="">—</option>` + months.map(m =>
    `<option value="${m}" ${m === sel ? "selected" : ""}>${m.slice(5)}.${m.slice(0, 4)}</option>`).join("");
  const pays = [...new Set(D.purchases.map(p => p.pay ?? "unknown"))];
  const merchants = [...new Set(D.purchases.map(p => p.merchant))].sort();
  const sources = [...new Set(D.purchases.map(p => p.source))];
  document.getElementById("filters").innerHTML = `
    <span><label>период</label> <select data-f="from">${monthOpts(S.F.from)}</select> –
      <select data-f="to">${monthOpts(S.F.to)}</select></span>
    <span><label>оплата</label> ${pays.map(k => `<button class="chip ${S.F.pay.has(k) ? "on" : ""}" data-pay="${k}">${esc(D.payments[k] || k)}</button>`).join(" ")}</span>
    <span><label>магазин</label> <select data-f="merchant"><option value="">все</option>${merchants.map(m =>
      `<option ${m === S.F.merchant ? "selected" : ""}>${esc(m)}</option>`).join("")}</select></span>
    <span><label>категория</label> ${catPick(null, "filter")}</span>
    <span><label>источник</label> <select data-f="source"><option value="">все</option>${sources.map(s =>
      `<option value="${s}" ${s === S.F.source ? "selected" : ""}>${esc(SOURCES[s] || s)}</option>`).join("")}</select></span>
    <button class="chip" id="reset">сбросить</button>`;
}

const catLabel = id => id == null || id === "" ? "⚠ неопознанные" : CAT[id] ? CAT[id].path.replaceAll("/", " › ") : "?";
const foldTxt = s => s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/ł/g, "l");
const sortedCats = () => Object.values(CAT).sort((a, b) => a.path.localeCompare(b.path, "ru"));

// ctx: filter | same (все такие) | item (позиция в чеке) | move (перенос категории)
function catPick(current, ctx, data = {}) {
  const key = pendKey(ctx, data);
  const p = key && PENDING.get(key);
  const shown = p ? p.cat : current;
  const label = ctx === "filter" ? filterLabel() : catLabel(shown);
  const attrs = Object.entries(data).map(([k, v]) => `data-${k}="${esc(v)}"`).join(" ");
  const cls = p ? "pending" : (shown == null && ctx !== "filter" && ctx !== "move" ? "unknown" : "");
  const bankOp = String(data.pid || "").startsWith("bank:");  // у операции банка «все такие» — весь магазин / получатель
  const only = p && ctx === "item" ? `<label class="only" title="${bankOp ? "без галочки — правило для всех покупок в этом магазине (переводов этому человеку)"
    : "без галочки — для всех позиций с таким названием и запомнить"}"><input type="checkbox" data-only="${esc(key)}" ${p.mode === "item" ? "checked" : ""}> только эта${
    bankOp && p.mode !== "item" ? " <span class=\"muted\">(иначе — весь магазин)</span>" : ""}</label>` : "";
  return `<button type="button" class="catpick ${cls}" data-ctx="${ctx}" ${attrs} title="${esc(label)}">${esc(label)} ▾</button>${only}`;
}
function filterLabel() {  // «Еда, Табак и вейп» / «Еда, Сыр и ещё 2»
  const names = [...S.F.cats].map(v => v === "none" ? "⚠ неопознанные" : CAT[v] ? CAT[v].name : "?");
  return !names.length ? "все" : names.length <= 2 ? names.join(", ") : `${names.slice(0, 2).join(", ")} и ещё ${names.length - 2}`;
}
const pendKey = (ctx, d) => ctx === "same" ? "same|" + d.name : ctx === "item" ? `item|${d.pid}|${d.line}`
  : ctx === "tx" ? "tx|" + d.tx : null;

const PENDING = new Map();
function updateSavebar() {
  const bar = document.getElementById("savebar");
  bar.style.display = PENDING.size ? "flex" : "none";
  document.body.classList.toggle("saving", PENDING.size > 0);
  document.getElementById("savecount").textContent = `Несохранённых изменений категорий: ${PENDING.size}`;
}
window.addEventListener("beforeunload", e => { if (PENDING.size) { e.preventDefault(); e.returnValue = ""; } });

let PICK = null;
function openPicker(btn) {
  PICK = btn;
  const box = document.getElementById("picker"), r = btn.getBoundingClientRect(), input = box.querySelector("input");
  box.style.left = Math.min(r.left + scrollX, scrollX + innerWidth - 360) + "px";
  box.style.top = (r.bottom + scrollY + 4) + "px";
  box.style.display = "block";
  input.value = ""; drawPicker(""); input.focus();
}
function drawPicker(q) {
  const ctx = PICK.dataset.ctx, f = foldTxt(q.trim());
  let opts = [];
  if (ctx === "filter") opts.push({v: "", t: "все категории"}, {v: "none", t: "⚠ неопознанные"});
  else if (ctx === "move") opts.push({v: "", t: "— на верхний уровень"});
  else opts.push({v: "", t: "⚠ неопознанные (без категории)"});
  for (const c of sortedCats()) {
    if (ctx === "move" && (c.id === +PICK.dataset.id || descendants(PICK.dataset.id).has(c.id))) continue;
    const parts = c.path.split("/");
    opts.push({v: c.id, t: c.path, html: (parts.length > 1 ? `<span class="par">${esc(parts.slice(0, -1).join(" › "))} › </span>` : "")
      + esc(parts[parts.length - 1]), top: parts.length === 1});
  }
  if (f) opts = opts.filter(o => foldTxt(o.t).includes(f));
  // фильтр — несколько категорий: отмеченные с галочкой, список не закрывается
  const on = o => ctx === "filter" && (o.v === "" ? !S.F.cats.size : S.F.cats.has(o.v === "none" ? "none" : +o.v));
  document.querySelector("#picker .list").innerHTML = (ctx === "filter" ? `<div class="muted" style="padding:4px 10px 6px;font-size:12.5px">
      можно выбрать несколько — нажимай по очереди</div>` : "") + opts.map((o, i) =>
    `<div class="opt ${o.top ? "top" : ""} ${i === 0 && f ? "hl" : ""} ${on(o) ? "on" : ""}" data-v="${o.v}">${on(o) ? "✓ " : ""}${o.html || esc(o.t)}</div>`).join("")
    || `<div class="opt muted">ничего не найдено</div>`;
}
function closePicker() { document.getElementById("picker").style.display = "none"; PICK = null; }
async function choose(v) {
  if (PICK.dataset.ctx === "filter") {  // переключить категорию в фильтре; список остаётся открытым
    if (v === "") S.F.cats.clear();
    else { const k = v === "none" ? "none" : +v; S.F.cats.has(k) ? S.F.cats.delete(k) : S.F.cats.add(k); }
    const q = document.querySelector("#picker input").value;
    render();
    PICK = document.querySelector('#filters .catpick[data-ctx="filter"]') || PICK;
    drawPicker(q); return;
  }
  const btn = PICK; closePicker();
  const ctx = btn.dataset.ctx, d = btn.dataset;
  if (ctx === "move") {
    await post("/api/category/move", {id: +d.id, parent_id: v === "" ? null : +v});
    toast("Категория перенесена"); await load(); return;
  }
  const cat = v === "" ? null : +v, key = pendKey(ctx, d);
  const prev = PENDING.get(key);
  PENDING.set(key, {mode: ctx === "tx" ? "tx" : ctx === "item" ? (prev ? prev.mode : "same") : "same", cat,
                    name: d.name, pid: d.pid, line: +d.line, tx: d.tx});
  updateSavebar();
  if (S.page === "bank") drawBankList(); else render();
}
document.querySelector("#picker input").addEventListener("input", e => drawPicker(e.target.value));
document.querySelector("#picker input").addEventListener("keydown", e => {
  if (e.key === "Escape") closePicker();
  if (e.key === "Enter") { const o = document.querySelector("#picker .opt[data-v]"); if (o) choose(o.dataset.v); }
});
document.querySelector("#picker .list").addEventListener("click", e => {
  // список перерисовывается при выборе (фильтр — несколько категорий): дальше клик не пускаем, иначе общий
  // обработчик увидит уже удалённый пункт «вне списка» и закроет его
  e.stopPropagation();
  const o = e.target.closest(".opt[data-v]"); if (o) choose(o.dataset.v);
});

async function saveAll() {
  const changes = [...PENDING.values()].map(p => p.mode === "tx" ? {mode: "tx", tx_id: p.tx, category_id: p.cat}
    : p.mode === "item" ? {mode: "item", purchase_id: p.pid, line: p.line, category_id: p.cat}
    : {mode: "same", name: p.name, purchase_id: p.pid, category_id: p.cat});
  await post("/api/set-categories", {changes});
  toast(`Сохранено изменений: ${changes.length}`);
  PENDING.clear(); updateSavebar(); BANK = null;
  const y = scrollY; await load(); scrollTo(0, y);
}
