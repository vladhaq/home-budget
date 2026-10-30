// Страница «Настройки»: обновление, источники, автозапуск, журнал.
// ---------- настройки: обновление данных, источники, автозапуск, журнал
let BIEDRONKA_EXTENSION = null, BIEDRONKA_EXTENSION_CHECKING = false;
let BIEDRONKA_SESSION = null, BIEDRONKA_SESSION_CHECKING = false;
let UPD = null, SCHED = null, updTimer = null, UPD_WAIT = null, loginTimer = null, LOGIN_WAIT = null;
let UPG = null, upgTimer = null;  // новая версия программы: результат проверки, ход установки
const UST = {ok: ["✓", "--green", "готово"], warn: ["!", "--orange", "внимание"], off: ["!", "--orange", "не настроено"],
             skip: ["–", "--text-2", "пропущено"], error: ["✗", "--red", "ошибка"], running: ["…", "--accent", "идёт"]};
const TRIG = {manual: "из терминала", schedule: "автозапуск", ui: "из интерфейса"};
const stBadge = st => { const [m, c, txt] = UST[st] || UST.running; return `<span class="st" style="--c:var(${c})">${m} ${txt}</span>`; };
const dtf = s => s ? `${s.slice(8, 10)}.${s.slice(5, 7)}.${s.slice(2, 4)}${s.length > 10 ? " " + s.slice(11, 16) : ""}` : "—";
const dur = (a, b) => { if (!a || !b) return ""; const s = Math.round((new Date(b) - new Date(a)) / 1000);
  return s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин ${s % 60} с`; };
const LOGIN_CMD = /^python budget\.py (lidl|kaufland|bank) login$/;  // такие входы — кнопкой, без терминала
const cmd = c => !c ? "" : LOGIN_CMD.test(c) ? loginBtn(LOGIN_CMD.exec(c)[1], "войти заново")
  : /^python /.test(c) ? `<code data-copy="${esc(c)}" title="нажми, чтобы скопировать">${esc(c)}</code>` : esc(c);
const updRunning = () => !!(UPD_WAIT || (UPD && UPD.running));
const loginRunning = () => !!(UPD && UPD.login && UPD.login.running);
const loginBtn = (key, label) => loginRunning() && UPD.login.key === key
  ? `<span class="muted"><span class="spin"></span>жду вход в окне Chrome…</span>`
  : `<button class="chip" data-login="${key}" ${loginRunning() || updRunning() ? "disabled" : ""}
      title="откроется окно Chrome: логин, пароль и коды вводишь там, программа их не видит">${label}</button>`;
const fmtDays = d => d < 1 ? `${Math.max(1, Math.round(d * 24))} ч` : `${d.toLocaleString(LOC, {maximumFractionDigits: d < 10 ? 1 : 0})} дн.`;
// дата входа и срок жизни токена (Lidl, Kaufland, банк): средний — от входа до отказа сервиса
function tokenInfo(s) {
  const t = s.token;
  if (!t || !t.since) return "";
  const age = ((t.dead ? new Date(t.dead) : Date.now()) - new Date(t.since)) / 864e5;
  return `<div class="tok">токен получен ${dtf(t.since)}${t.until ? `, действует до ${dtf(t.until.slice(0, 10))}` : ""}
    <br>${t.dead ? `<span class="warn">отклонён ${dtf(t.dead)}, прожил ${fmtDays(age)}</span>` : `работает ${fmtDays(age)}`}
    <br>средний срок жизни: ${t.avg_days != null ? `<b>${fmtDays(t.avg_days)}</b>${t.samples > 1 ? ` (входов: ${t.samples})` : ""}`
      : "— ещё не истекал"}</div>`;
}

function checkBiedronkaExtension() {
  if (BIEDRONKA_EXTENSION !== null || BIEDRONKA_EXTENSION_CHECKING) return;
  BIEDRONKA_EXTENSION_CHECKING = true;
  const deadline = Date.now() + 2000;
  const check = () => {
    if (document.getElementById("home-budget-biedronka-extension")) {
      BIEDRONKA_EXTENSION = true;
      BIEDRONKA_EXTENSION_CHECKING = false;
      if (S.page === "settings") drawSettings();
      return;
    }
    if (Date.now() < deadline) {
      setTimeout(check, 100);
    } else {
      BIEDRONKA_EXTENSION_CHECKING = false;
      BIEDRONKA_EXTENSION = false;
      if (S.page === "settings") drawSettings();
    }
  };
  check();
}

async function renderSettings() {
  checkBiedronkaExtension();
  if (!UPD) UPD = await (await fetch("/api/update")).json();
  if (loginRunning() && !LOGIN_WAIT) { LOGIN_WAIT = UPD.login.key; pollLogin(); }  // вход начат до перезагрузки страницы
  if ((UPD.stores || []).some(s => s.key === "biedronka" && s.on)) checkBiedronkaSession(false);
  if (!SCHED) {
    SCHED = {loading: true};
    fetch("/api/update/schedule").then(r => r.json()).then(s => { SCHED = s; if (S.page === "settings") drawSettings(); });
  }
  drawSettings();
}
// статус сессии Biedronka: обычно — по локальным данным; net — проверить на сайте (кнопка «проверить на сайте»)
async function checkBiedronkaSession(net) {
  if (BIEDRONKA_SESSION_CHECKING) return;
  BIEDRONKA_SESSION_CHECKING = true;
  if (net && S.page === "settings") drawSettings();
  try { BIEDRONKA_SESSION = await (await fetch("/api/biedronka/session-status" + (net ? "?check=1" : ""))).json(); }
  catch (e) { BIEDRONKA_SESSION = {status: "unavailable"}; }
  finally {
    BIEDRONKA_SESSION_CHECKING = false;
    if (S.page === "settings") drawSettings();
  }
}
// разделы настроек сворачиваются; что открыто — запоминается (по умолчанию открыты обновление, магазины и источники)
Object.assign(SECT_DEFAULT, {update: true, stores: true, sources: true, schedule: false, lang: false, program: false, hidden: false, journal: false});

// Biedronka: подключение сессии из Chrome через расширение «Budget Browser Connector»
function biedronkaBox() {
  const st = BIEDRONKA_SESSION || {}, s = st.status;
  const ext = BIEDRONKA_EXTENSION === false ? `<div class="muted">Расширение «Budget Browser Connector» не найдено. В Chrome открой
      <code>chrome://extensions</code>, включи «Режим разработчика», нажми «Загрузить распакованное расширение» и выбери папку
      <code data-copy="${esc(st.connector || "")}" title="нажми, чтобы скопировать">${esc(st.connector || "tools\\browser-connector")}</code>.</div>` : "";
  const how = `<div class="muted">Войди в Moja Biedronka в Chrome, нажми значок расширения «Budget Browser Connector» →
      «Подключить к бюджету». Передаются только cookies сайта Biedronka и только этой программе на этом компьютере; пароль
      расширение не видит.</div>`;
  const head = !s ? `<span class="muted"><span class="spin"></span>проверяю…</span>`
    : s === "missing" ? `<b>Biedronka ещё не подключена</b>`
    : s === "expired" ? `<b class="warn">Сессия Moja Biedronka истекла</b> — подключи заново`
    : s === "unavailable" ? `<span class="warn">Сайт Biedronka не ответил — попробуй позже</span>`
    : `✓ сессия сохранена${s === "valid" ? " и проверена на сайте" : ""}`;
  return `<div class="storeconn">${head}
    ${s === "saved" || s === "valid" ? `<button class="chip" data-bcheck="1" ${BIEDRONKA_SESSION_CHECKING ? "disabled" : ""}>проверить на сайте</button>` : ""}
    ${s === "missing" || s === "expired" ? ext + how : ""}</div>`;
}
function storesBox() {
  const L = UPD.stores || [];
  return `<div class="stores">${L.map(s => `<div class="store ${s.on ? "" : "off"}">
      <label><input type="checkbox" data-store="${s.key}" ${s.on ? "checked" : ""}> <b>${esc(s.title)}</b></label>
      <span class="muted">${esc(s.about)}</span>
      ${s.key === "biedronka" && s.on ? biedronkaBox() : ""}</div>`).join("")}</div>
    <p class="muted">Выключенный магазин не обновляется («Обновить всё» и автозапуск), его нет в источниках и на странице «Скидки».
      Уже собранные чеки остаются в бюджете.</p>`;
}
async function setAutoStep(key, on) {
  try { await post("/api/update/steps", {key, on}); toast(on ? "Шаг снова в «Обновить всё»" : "Шаг не входит в «Обновить всё» — только кнопкой «обновить»"); }
  catch (e) { toast("Не получилось: " + e.message); }
  UPD = null;
  await renderSettings();
}
async function setStore(key, on) {
  try { const j = await post("/api/stores", {key, on}); UPD.stores = j.stores; }
  catch (e) { toast("Не получилось: " + e.message); }
  UPD = null; DEALS = null; DINFL = null; DCMP = null;
  await renderSettings();
  load();  // боковое меню и «Скидки» — без выключенного магазина
}

function drawSettings() {
  const main = document.getElementById("main"), last = UPD.runs[0], run = updRunning();
  const cur = run && last && !last.finished ? last.steps.find(s => s.status === "running") : null;
  const lastDone = UPD.runs.find(r => r.finished);
  const sch = SCHED || {};
  const schText = sch.loading ? `<span class="muted">проверяю Планировщик заданий…</span>`
    : sch.installed ? `<b>включён</b>: каждый день в ${esc(sch.time || "?")}${sch.logon ? ", а если обновление прервалось (сбой Windows, выключение) — ещё раз при входе в Windows" : ""}${sch.state === "Disabled" ? ` <span class="up">(задача отключена в Планировщике)</span>` : ""}.
        Следующий запуск: ${dtf(sch.next_run)}. Последний: ${dtf(sch.last_run)}${sch.last_run && !sch.last_ok ? ` <span class="up">(код ошибки ${sch.last_result})</span>` : ""}
        ${sch.stale ? `<br><span class="warn">Задача создана старой версией${sch.logon ? "" : " (без повтора после сбоя"}${sch.limit && sch.limit !== "PT12H" ? `${sch.logon ? " (" : ", "}Windows снимает её через ${esc(sch.limit.replace("PT", "").replace("H", " ч"))}` : ""}${!sch.logon || (sch.limit && sch.limit !== "PT12H") ? ")" : ""} — нажми «Сохранить время», чтобы обновить.</span>` : ""}`
    : `<b>выключен</b>${sch.error ? ` <span class="up">(${esc(sch.error)})</span>` : ""}`;
  const srcBad = UPD.sources.filter(s => (s.last && s.last.status === "error") || s.warn).length;
  const srcWarn = UPD.sources.filter(s => s.last && (s.last.status === "warn" || s.last.status === "off")).length;
  const onStores = (UPD.stores || []).filter(s => s.on).map(s => s.title);
  const order = UPD.sources.filter(s => s.auto !== false && !["reconcile", "backup"].includes(s.key)).map(s => s.title).join(", ");

  const updBody = `<div class="updbox">
      <button class="btn" data-updrun="" ${run || loginRunning() ? "disabled" : ""}>${run ? "Обновление идёт…" : "Обновить всё сейчас"}</button>
      <span class="big">${run ? `<span class="spin"></span>${cur ? "сейчас: " + esc(cur.title) : "запускается…"}`
        : lastDone ? `Последнее обновление: <b>${dtf(lastDone.finished)}</b> (${TRIG[lastDone.trigger] || lastDone.trigger}) ${stBadge(lastDone.status)}
            <span class="muted">${esc(lastDone.summary || "")}</span>` : "Обновлений ещё не было."}</span>
    </div>
    <p class="muted">По очереди: ${esc(order)}, затем сверка и категории, резервная копия. Обычно 1–2 минуты. Ошибка одного
      источника не останавливает остальные. Входы (логины, пароли) обновление не запрашивает — если вход в Lidl, Kaufland
      или банк устарел, нажми «обновить токен» в строке источника.</p>`;

  const srcBody = `<table><tr><th title="участвует в «Обновить всё» и автозапуске">в «Обновить всё»</th><th>источник</th><th>подключение</th><th>успешно обновлено</th><th>данные по</th><th>последний результат</th><th></th></tr>` +
    UPD.sources.map(s => {
      const l = s.last, bad = (l && l.status === "error") || !!s.warn, wrn = l && (l.status === "warn" || l.status === "off");
      const btn = LOGIN_CMD.test(s.setup || "");  // вход кнопкой — подсказка-команда в строке не нужна
      const hint = l && l.hint && !(l.summary || "").includes(l.hint) && !(btn && LOGIN_CMD.test(l.hint)) ? `<br>→ ${cmd(l.hint)}` : "";
      return `<tr class="${bad ? "err" : wrn ? "wrn" : ""}${s.auto === false ? " off" : ""}"><td class="n"><label class="switch" title="${s.fixed
          ? "сверка нужна всегда — без неё новые покупки не видны" : "участвует в «Обновить всё» и автозапуске; выключено — только кнопкой «обновить»"}">
          <input type="checkbox" data-autostep="${s.key}" ${s.auto !== false ? "checked" : ""} ${s.fixed ? "disabled" : ""}><span></span></label></td>
        <td><b>${esc(s.title)}</b></td>
        <td>${s.always ? `<span class="muted">не требуется</span>` : btn
          ? `${s.ready && !(s.token || {}).dead ? "✓ подключено" : `<span class="warn">${esc(s.token && s.token.dead ? "токен истёк" : s.missing)}</span>`}
             ${tokenInfo(s)}${loginBtn(LOGIN_CMD.exec(s.setup)[1], s.ready ? "обновить токен" : "войти")}`
          : `${s.ready && !(s.token || {}).dead ? "✓ подключено" : `<span class="warn">${esc(s.token && s.token.dead ? "сессия истекла" : s.missing)}</span><br>${cmd(s.setup)}`}${tokenInfo(s)}`}</td>
        <td>${s.last_ok ? dtf(s.last_ok.finished) : "—"}</td>
        <td>${s.data ? dtf(s.data.slice(0, 16)) : ""}<div class="muted" style="font-size:.8em">${esc(s.note || "")}</div></td>
        <td>${l ? `${stBadge(l.status)} ${esc(l.summary || "")}${hint}` : "—"}
          ${s.warn ? `<div class="up">⚠ ${esc(s.warn)}${btn ? "" : ` — ${cmd(s.setup)}`}</div>` : ""}</td>
        <td><button class="chip" data-updrun="${s.key}" ${run || loginRunning() ? "disabled" : ""}>обновить</button></td></tr>`;
    }).join("") + `</table>
    <p class="muted">Переключатель слева — участвует ли источник в «Обновить всё» и автозапуске: выключенный обновляется только
      кнопкой «обновить» в его строке (например, долгое распознавание газеток Biedronka, если нужны только чеки).
      «Обновить токен» — новый вход: откроется окно Chrome, логин, пароль и коды вводишь там сам (программа их не
      видит), окно закроется само. В «Обновить всё» не входит. Средний срок жизни — от входа до отказа сервиса, считается
      с октября 2026. Команды в серых рамках нажатием копируются — вставь в терминал в папке budget. «Обновить» в строке —
      только этот источник (и сверка). Ошибка с пометкой «в программе» — открой журнал ниже и пришли текст Claude.</p>`;

  const schedBody = `<div class="updbox"><span>${schText}</span>
      <span><input type="time" id="schedtime" value="${esc(sch.time || "07:30")}">
        <button class="btn" data-sched="on">${sch.installed ? "Сохранить время" : "Включить"}</button>
        ${sch.installed ? `<button class="chip" data-sched="off">выключить</button>` : ""}</span></div>
    <p class="muted">Задача «BudgetUpdate» в Планировщике заданий Windows, работает без окна. Если в это время компьютер выключен или
      спит — обновление запустится, как только он включится; пока оно идёт, Windows не усыпит компьютер. Если оно
      всё же прервалось (Windows упала во сне, компьютер выключили) — повторится при следующем входе в Windows.</p>`;

  const hiddenBody = `<table><tr><th>покупка</th><th>источник</th><th>удалена</th><th></th></tr>` + D.hidden.map(h => `<tr><td>${esc(h.label)}</td>
      <td>${esc(SOURCES[h.source] || h.source)}</td><td>${dtf(h.created)}</td>
      <td><button class="chip" data-restore="${esc(h.id)}">вернуть</button></td></tr>`).join("") + `</table>
    <p class="muted">Удалённые покупки не возвращаются при обновлениях. «Вернуть» пересобирает источник и сверку (~30 секунд).</p>`;

  const journalBody = UPD.runs.length ? UPD.runs.map((r, i) => `<details ${i === 0 && r.status && r.status !== "ok" ? "open" : ""}>
      <summary>${dtf(r.started)} · ${TRIG[r.trigger] || r.trigger} · ${stBadge(r.finished ? r.status : "running")}
        ${esc(r.summary || "")} <span class="muted">${dur(r.started, r.finished)}</span></summary>
      <table><tr><th>шаг</th><th>результат</th><th>что делать</th><th class="n">время</th></tr>` +
      r.steps.map(s => `<tr class="${s.status === "error" ? "err" : s.status === "warn" || s.status === "off" ? "wrn" : ""}"><td>${esc(s.title)}</td>
        <td>${stBadge(s.status)} ${esc(s.summary || "")}${s.output ? `<details><summary class="muted">вывод</summary><pre class="log">${esc(s.output)}</pre></details>` : ""}</td>
        <td>${s.hint && s.status !== "ok" ? cmd(s.hint) : ""}</td><td class="n muted">${dur(s.started, s.finished)}</td></tr>`).join("") +
      `</table></details>`).join("") : `<p class="muted">Пусто.</p>`;

  main.innerHTML =
    section("update", "Обновление данных", run ? `<span class="spin"></span>идёт` : lastDone ? `${dtf(lastDone.finished)} ${stBadge(lastDone.status)}` : "", updBody) +
    section("stores", "Магазины", onStores.length ? `учитываются: ${esc(onStores.join(", "))}` : "все выключены", storesBox()) +
    section("sources", "Источники", srcBad ? `<span class="up">с ошибкой: ${srcBad}</span>` : srcWarn ? `<span class="warn">с замечаниями: ${srcWarn}</span>` : "все в порядке", srcBody) +
    section("schedule", "Автозапуск", sch.loading ? "" : sch.installed ? `каждый день в ${esc(sch.time || "?")}` : "выключен", schedBody) +
    section("lang", "Язык · Language", LANG === "en" ? "English" : "Русский", `<div class="updbox">
      <span data-notr>Язык интерфейса и напоминаний в Telegram · Interface and Telegram reminders language</span>
      <span data-notr><button class="chip ${LANG === "ru" ? "on" : ""}" data-setlang="ru">Русский</button>
        <button class="chip ${LANG === "en" ? "on" : ""}" data-setlang="en">English</button></span></div>`) +
    section("program", "Программа", `версия ${esc((UPG && UPG.check && UPG.check.current) || UPD.version || "?")}`, upgradeBox()) +
    (D.hidden.length ? section("hidden", "Удалённые покупки", `${D.hidden.length}`, hiddenBody) : "") +
    section("journal", "Журнал обновлений", UPD.runs.length ? `запусков: ${UPD.runs.length}, последний ${dtf(UPD.runs[0].started)} ${stBadge(UPD.runs[0].finished ? UPD.runs[0].status : "running")}` : "", journalBody);
}
async function startUpdate(steps) {
  const before = UPD && UPD.runs.length ? UPD.runs[0].id : (D.meta.update_last ? D.meta.update_last.id : 0);
  try { await post("/api/update/run", {steps}); } catch (e) { toast("Не запустилось: " + e.message); return; }
  UPD_WAIT = {before, since: Date.now()};
  toast(steps.length ? "Обновляю источник…" : "Обновление запущено");
  if (S.page === "settings") drawSettings();
  pollUpdate();
}
// опрос раз в 2 с: страницу перерисовываем, только если ответ сервера изменился — иначе она «моргает»
let UPD_SEEN = "";
function redrawIfChanged() {
  const now = JSON.stringify(UPD);
  if (now === UPD_SEEN || S.page !== "settings") return;
  UPD_SEEN = now;
  drawSettings();
}
async function pollUpdate() {
  clearTimeout(updTimer);
  UPD = await (await fetch("/api/update")).json();
  const r = UPD.runs[0], started = r && r.id > UPD_WAIT.before, done = started && r.finished;
  if (!done && (started || UPD.running || Date.now() - UPD_WAIT.since < 30000)) {
    redrawIfChanged();
    updTimer = setTimeout(pollUpdate, 2000);
    return;
  }
  UPD_WAIT = null;
  if (!started) toast("Обновление не запустилось — попробуй в терминале: python budget.py update");
  else toast(r.status === "ok" ? "Обновление завершено без ошибок" : "Обновление завершено: " + r.summary);
  BANK = null; WALLET = null;
  const y = scrollY; await load(); scrollTo(0, y);
}
async function startLogin(key) {
  try { await post(`/api/login/${key}`, {}); } catch (e) { toast("Не запустилось: " + e.message); return; }
  LOGIN_WAIT = key;
  toast("Открываю окно Chrome — войди там, как в приложении. Если окна не видно, оно на панели задач");
  pollLogin();
}
async function pollLogin() {
  clearTimeout(loginTimer);
  UPD = await (await fetch("/api/update")).json();
  const l = UPD.login;
  if (l && l.running) {
    redrawIfChanged();
    loginTimer = setTimeout(pollLogin, 2000);
    return;
  }
  if (LOGIN_WAIT && l) toast(l.ok ? `${l.title}: вход выполнен, токен обновлён. «Обновить» в строке скачает новые данные`
                                  : `${l.title}: вход не выполнен — ${l.message || "окно закрыто"}`);
  LOGIN_WAIT = null;
  if (S.page === "settings") drawSettings();
}
async function setSchedule(on) {
  const time = (document.getElementById("schedtime") || {}).value || "07:30";
  SCHED = {loading: true}; drawSettings();
  try { const j = await post("/api/update/schedule", {on, time}); SCHED = j.schedule;
        toast(on ? `Автозапуск: каждый день в ${SCHED.time}` : "Автозапуск выключен"); }
  catch (e) { SCHED = null; toast("Не получилось: " + e.message); }
  if (S.page === "settings") renderSettings();
}

// ---------- новая версия программы с GitHub
// «что нового» из выпуска на GitHub: пункты «- …» (с продолжением строк), **жирный**, `код`
function releaseNotes(md) {
  const items = [];
  for (const line of (md || "").split(/\r?\n/)) {
    if (/^\s*[-*] /.test(line)) items.push(line.replace(/^\s*[-*] /, ""));
    else if (line.trim() && !/^#/.test(line.trim())) items.length ? items[items.length - 1] += " " + line.trim() : items.push(line.trim());
  }
  const fmt = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  return items.length ? `<ul class="notes">${items.map(s => `<li>${fmt(s)}</li>`).join("")}</ul>` : "";
}
function upgradeBox() {
  const u = UPG || {}, ver = (u.check && u.check.current) || (UPD && UPD.version) || "?";
  const busy = u.state === "running" || u.state === "restarting";
  let text = "", btn = `<button class="btn" data-upcheck ${u.checking || busy ? "disabled" : ""}>Проверить обновление</button>`, notes = "";
  if (u.checking) text = `<span class="spin"></span>проверяю GitHub…`;
  else if (busy) { text = `<span class="spin"></span>Устанавливаю ${esc(u.version || "")}: ${esc(u.step || "")}`; btn = ""; }
  else if (u.state === "error") text = `<span class="up">Не установилось: ${esc(u.error || "")}</span>`;
  else if (u.state === "done") text = `<span class="warn">${esc(u.step || "")}</span>`;
  else if (u.check && u.check.error) text = `<span class="up">${esc(u.check.error)}</span>`;
  else if (u.check && !u.check.newer) text = `✓ установлена последняя версия (выпуск ${esc(u.check.latest.version)} от ${dtf(u.check.latest.date)})`;
  else if (u.check && u.check.newer) {
    const l = u.check.latest;
    text = `Доступна версия <b>${esc(l.version)}</b> от ${dtf(l.date)} — <a href="${esc(l.url)}" target="_blank" rel="noopener">на GitHub</a>`;
    btn = u.check.blocker ? "" : `<button class="btn" data-upinstall>${u.check.auto_restart ? "Установить и перезапустить" : "Установить"}</button>`;
    notes = (u.check.blocker ? `<p class="up">Установить отсюда нельзя: ${esc(u.check.blocker)}</p>` : "")
      + (u.check.auto_restart || u.check.blocker ? "" : `<p class="muted">Сервер запущен старой версией и сам не перезапустится —
          после установки закрой окно сервера и запусти снова: <code>python budget.py serve</code></p>`)
      + `<details open><summary class="muted">что нового</summary>${releaseNotes(l.notes)}</details>`;
  }
  return `<div class="updbox"><span>Версия <b>${esc(ver)}</b>${text ? ` · ${text}` : ""}</span>${btn}</div>${notes}
    <p class="muted">Обновляются только файлы программы: база, токены, <code>config.ini</code>, резервные копии и папки входящих
      не меняются. Перед установкой — копия базы в <code>data/upgrade/</code>. Сеть — только по кнопке.</p>`;
}
async function checkUpgrade() {
  UPG = {checking: true}; if (S.page === "settings") drawSettings();
  try { UPG = {check: await (await fetch("/api/upgrade/check")).json()}; }
  catch (e) { UPG = {check: {error: "сервер не ответил: " + e.message}}; }
  if (S.page === "settings") drawSettings();
}
async function installUpgrade() {
  const l = UPG && UPG.check && UPG.check.latest;
  if (!l || !confirm(t(`Установить версию ${l.version}?`) + (UPG.check.auto_restart ? " " + t("Сервер перезапустится, страница обновится сама.") : ""))) return;
  try { await post("/api/upgrade/install", {}); } catch (e) { toast("Не запустилось: " + e.message); return; }
  UPG = {state: "running", version: l.version, step: "начинаю…", check: UPG.check};
  drawSettings();
  pollUpgrade(l.version, Date.now());
}
async function pollUpgrade(target, since) {
  clearTimeout(upgTimer);
  let s = null, gone = false;
  try {
    const r = await fetch("/api/upgrade/status", {cache: "no-store"});
    gone = r.status === 404;  // новая версия без этой проверки — сервер уже поднялся
    s = gone ? null : await r.json();
  } catch (e) { /* сервер перезапускается */ }
  if (gone || (s && s.running === target && !s.state)) {  // поднялся уже новый сервер — новые скрипты и стили только после перезагрузки
    try { sessionStorage.setItem("budget-upgraded", target); } catch (e) { /* без хранилища — просто без сообщения */ }
    location.reload();
    return;
  }
  if (s && s.state) UPG = {...UPG, ...s};
  if (s && (s.state === "error" || s.state === "done")) { if (S.page === "settings") drawSettings(); return; }
  if (Date.now() - since > 10 * 60000) { UPG = {...UPG, state: "error", error: "сервер не вернулся за 10 минут — запусти: python budget.py serve"}; drawSettings(); return; }
  if (S.page === "settings") drawSettings();
  upgTimer = setTimeout(() => pollUpgrade(target, since), 1500);
}
(() => {  // после перезапуска на новой версии
  let v = null;
  try { v = sessionStorage.getItem("budget-upgraded"); sessionStorage.removeItem("budget-upgraded"); } catch (e) { /* нет хранилища */ }
  if (v) setTimeout(() => toast(`Установлена версия ${v}`), 800);
})();

// «^medaliony\ z\ fileta$» -> «medaliony z fileta» (точное название); остальное показываем как есть
const humanPattern = p => /^\^.*\$$/.test(p) ? p.slice(1, -1).replace(/\\(.)/g, "$1") : p;
