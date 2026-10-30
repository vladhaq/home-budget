// Язык интерфейса. Код и сервер пишут по-русски; при английском переводится то, что появилось на странице:
// текст, title, placeholder — по словарю EN (i18n-en.js). Ключ — русский текст; числа, суммы, даты и время в нём
// — {n} (подставляются как были), имена и названия — {s}; в переводе можно переставить: {1}, {2}… по порядку в ключе.
// Не нашлось в словаре — остаётся по-русски (ничего не ломается); window.untranslated() в консоли покажет такие места.
const LANG = (() => {
  const set = document.documentElement.dataset.lang;  // выбор в «Настройках» — сервер подставляет его в страницу
  if (set === "ru" || set === "en") return set;
  return /^(ru|be)\b/i.test(navigator.language || "") ? "ru" : "en";  // не выбран — как язык браузера
})();
const LOC = LANG === "en" ? "en-GB" : "ru-RU";  // формат чисел: «1,234.56» / «1 234,56»
document.documentElement.lang = LANG;

// число, сумма, процент, дата или время: «1 234,56 zł», «−6,50», «15.10.26», «2026-10-11», «22:30», «61%»
const NUM_SRC = String.raw`[-−+]?\d+(?:[   ]\d{3}(?!\d))*(?:[.,:\/-]\d+)*(?:[  ]?zł)?%?`;
const NUM_RE = new RegExp(NUM_SRC, "g");
const I18N = (() => {
  const plain = new Map(), patterns = [];
  if (typeof EN === "undefined") return {plain, patterns};
  for (const [k, v] of Object.entries(EN)) {
    if (!k.includes("{s}")) { plain.set(k, v); continue; }
    const kinds = [];
    const rx = k.split(/(\{n\}|\{s\})/).map(p => {
      if (p === "{n}") { kinds.push("n"); return `(${NUM_SRC})`; }
      if (p === "{s}") { kinds.push("s"); return "(.+?)"; }
      return p.replace(/[.*+?^$()|[\]\\]/g, "\\$&");
    }).join("");
    patterns.push([new RegExp("^" + rx + "$", "s"), v, k.length, kinds]);
  }
  patterns.sort((a, b) => b[2] - a[2]);  // длинный ключ точнее
  return {plain, patterns};
})();
const fill = (tpl, vals) => {
  let i = 0;
  return tpl.replace(/\{(n|s|\d+)\}/g, (m, k) => (/\d/.test(k) ? vals[+k - 1] : vals[i++]) ?? m);
};
// подставленное на место {s} тоже переводится: «ошибки: Чеки Biedronka», «(медиана …: апр, май, июн)»
const trValue = v => {
  const r = translate(v);
  if (r !== v || !v.includes(", ")) return r;
  const parts = v.split(", "), tr = parts.map(p => translate(p));
  return tr.some((p, i) => p !== parts[i]) ? tr.join(", ") : v;
};
// значки вокруг текста: «▸ Взнос наличных», «Еда ▾», «✓ готово», «автозапуск ·»
const DECOR = /^((?:[▸▾•✓✗⚠↩–—]\s*|[🔔🔕📷✉🛒]️?\s*)*)([\s\S]*?)((?:\s*[▾↗·])*)$/u;
function lookup(flat) {
  let out = I18N.plain.get(flat);
  if (out === undefined) {  // с числами: «операций: 914» -> «операций: {n}»
    const nums = flat.match(NUM_RE) || [];
    const tpl = nums.length ? I18N.plain.get(flat.replace(NUM_RE, "{n}")) : undefined;
    if (tpl !== undefined) out = fill(tpl, nums);
  }
  if (out === undefined) {
    for (const [rx, v, , kinds] of I18N.patterns) {
      const m = flat.match(rx);
      if (m) { out = fill(v, m.slice(1).map((x, i) => kinds[i] === "s" ? trValue(x) : x)); break; }
    }
  }
  if (out === undefined) {  // составное: «Еда › Сладкое», «Доходы/Зарплата», пометки «…; …», «… · …» — по частям
    for (const sep of [" › ", "; ", " · ", "/"]) {
      if (!flat.includes(sep)) continue;
      const parts = flat.split(sep), tr = parts.map(p => translate(p));
      if (tr.some((p, i) => p !== parts[i])) { out = tr.join(sep); break; }
    }
  }
  return out;
}
const TR_CACHE = new Map();
function translate(s) {
  if (LANG !== "en" || !s || !/[А-Яа-яЁё]/.test(s)) return s;
  if (TR_CACHE.has(s)) return TR_CACHE.get(s);
  const [, lead, core, trail] = s.match(/^(\s*)([\s\S]*?)(\s*)$/);
  const flat = core.replace(/\s+/g, " ");
  let out = lookup(flat);
  if (out === undefined) {
    const m = flat.match(DECOR);
    if (m && (m[1] || m[3]) && m[2]) {
      const inner = lookup(m[2]);
      if (inner !== undefined) out = m[1] + inner + m[3];
    }
  }
  const res = out === undefined ? s : lead + out + trail;
  if (TR_CACHE.size > 20000) TR_CACHE.clear();
  TR_CACHE.set(s, res);
  return res;
}
const t = translate;  // для того, что рисуется не текстом страницы: подписи графиков, confirm()

const TR_ATTRS = ["title", "placeholder", "aria-label"];
function translateTree(root) {
  if (LANG !== "en") return;
  if (root.nodeType === Node.TEXT_NODE) {
    const v = translate(root.data);
    if (v !== root.data) root.data = v;
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE || root.closest("script, style, textarea, [data-notr]")) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement && n.parentElement.closest("script, style, textarea, [data-notr]")
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const v = translate(n.data);
    if (v !== n.data) n.data = v;
  }
  for (const el of [root, ...root.querySelectorAll("[title], [placeholder], [aria-label]")]) {
    for (const a of TR_ATTRS) {
      const v = el.getAttribute(a);
      if (v && /[А-Яа-яЁё]/.test(v)) { const w = translate(v); if (w !== v) el.setAttribute(a, w); }
    }
  }
}
if (LANG === "en") {
  document.title = translate(document.title);
  const start = () => {
    translateTree(document.body);
    new MutationObserver(recs => {
      for (const r of recs) {
        if (r.type === "attributes") translateTree(r.target);
        else for (const n of r.addedNodes) translateTree(n);
      }
    }).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: TR_ATTRS});
  };
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
}
// в консоли: какие тексты на этой странице остались без перевода (для пополнения словаря)
window.untranslated = () => {
  const out = new Set(), walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (/[А-Яа-яЁё]/.test(n.data) && !(n.parentElement && n.parentElement.closest("script, style, [data-notr]")))
      out.add(n.data.trim().replace(/\s+/g, " ").replace(NUM_RE, "{n}"));
  }
  for (const el of document.querySelectorAll("[title], [placeholder], [aria-label]"))
    for (const a of TR_ATTRS) { const v = el.getAttribute(a); if (v && /[А-Яа-яЁё]/.test(v)) out.add(v.replace(NUM_RE, "{n}")); }
  return [...out];
};
