// Страница «Настройки»: обновление, источники, автозапуск, журнал.
// ---------- настройки: обновление данных, источники, автозапуск, журнал
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
const fmtDays = d => d < 1 ? `${Math.max(1, Math.round(d * 24))} ч` : `${d.toLocaleString("ru-RU", {maximumFractionDigits: d < 10 ? 1 : 0})} дн.`;
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

async function renderSettings() {
  if (!UPD) UPD = await (await fetch("/api/update")).json();
  if (loginRunning() && !LOGIN_WAIT) { LOGIN_WAIT = UPD.login.key; pollLogin(); }  // вход начат до перезагрузки страницы
  if (!SCHED) {
    SCHED = {loading: true};
    fetch("/api/update/schedule").then(r => r.json()).then(s => { SCHED = s; if (S.page === "settings") drawSettings(); });
  }
  drawSettings();
}
function drawSettings() {
  const main = document.getElementById("main"), last = UPD.runs[0], run = updRunning();
  const cur = run && last && !last.finished ? last.steps.find(s => s.status === "running") : null;
  const lastDone = UPD.runs.find(r => r.finished);
  const sch = SCHED || {};
  const schText = sch.loading ? `<span class="muted">проверяю Планировщик заданий…</span>`
    : sch.installed ? `<b>включён</b>: каждый день в ${esc(sch.time || "?")}${sch.state === "Disabled" ? ` <span class="up">(задача отключена в Планировщике)</span>` : ""}.
        Следующий запуск: ${dtf(sch.next_run)}. Последний: ${dtf(sch.last_run)}${sch.last_run && !sch.last_ok ? ` <span class="up">(код ошибки ${sch.last_result})</span>` : ""}`
    : `<b>выключен</b>${sch.error ? ` <span class="up">(${esc(sch.error)})</span>` : ""}`;
  main.innerHTML = `
    <h2>Обновление данных</h2>
    <div class="updbox">
      <button class="btn" data-updrun="" ${run || loginRunning() ? "disabled" : ""}>${run ? "Обновление идёт…" : "Обновить всё сейчас"}</button>
      <span class="big">${run ? `<span class="spin"></span>${cur ? "сейчас: " + esc(cur.title) : "запускается…"}`
        : lastDone ? `Последнее обновление: <b>${dtf(lastDone.finished)}</b> (${TRIG[lastDone.trigger] || lastDone.trigger}) ${stBadge(lastDone.status)}
            <span class="muted">${esc(lastDone.summary || "")}</span>` : "Обновлений ещё не было."}</span>
    </div>
    <p class="muted">По очереди: Lidl, Kaufland, почта, фото из Telegram и папки receipts/inbox, выписка PKO, сверка и категории,
      резервная копия. Обычно 1–2 минуты. Ошибка одного источника не останавливает остальные.
      Входы (логины, пароли) обновление не запрашивает — если вход в Lidl, Kaufland или банк устарел, нажми
      «обновить токен» в строке источника.</p>

    <h2>Источники</h2>
    <table><tr><th>источник</th><th>подключение</th><th>успешно обновлено</th><th>данные по</th><th>последний результат</th><th></th></tr>` +
    UPD.sources.map(s => {
      const l = s.last, bad = (l && l.status === "error") || !!s.warn, wrn = l && (l.status === "warn" || l.status === "off");
      const btn = LOGIN_CMD.test(s.setup || "");  // вход кнопкой — подсказка-команда в строке не нужна
      const hint = l && l.hint && !(l.summary || "").includes(l.hint) && !(btn && LOGIN_CMD.test(l.hint)) ? `<br>→ ${cmd(l.hint)}` : "";
      return `<tr class="${bad ? "err" : wrn ? "wrn" : ""}"><td><b>${esc(s.title)}</b></td>
        <td>${s.always ? `<span class="muted">не требуется</span>` : btn
          ? `${s.ready && !(s.token || {}).dead ? "✓ подключено" : `<span class="warn">${esc(s.token && s.token.dead ? "токен истёк" : s.missing)}</span>`}
             ${tokenInfo(s)}${loginBtn(LOGIN_CMD.exec(s.setup)[1], s.ready ? "обновить токен" : "войти")}`
          : s.ready ? "✓ подключено" : `<span class="warn">${esc(s.missing)}</span><br>${cmd(s.setup)}`}</td>
        <td>${s.last_ok ? dtf(s.last_ok.finished) : "—"}</td>
        <td>${s.data ? dtf(s.data.slice(0, 16)) : ""}<div class="muted" style="font-size:.8em">${esc(s.note || "")}</div></td>
        <td>${l ? `${stBadge(l.status)} ${esc(l.summary || "")}${hint}` : "—"}
          ${s.warn ? `<div class="up">⚠ ${esc(s.warn)}${btn ? "" : ` — ${cmd(s.setup)}`}</div>` : ""}</td>
        <td><button class="chip" data-updrun="${s.key}" ${run || loginRunning() ? "disabled" : ""}>обновить</button></td></tr>`;
    }).join("") + `</table>
    <p class="muted">«Обновить токен» — новый вход: откроется окно Chrome, логин, пароль и коды вводишь там сам (программа их не
      видит), окно закроется само. В «Обновить всё» не входит. Средний срок жизни — от входа до отказа сервиса, считается
      с октября 2026. Команды в серых рамках нажатием копируются — вставь в терминал в папке budget. «Обновить» в строке —
      только этот источник (и сверка). Ошибка с пометкой «в программе» — открой журнал ниже и пришли текст Claude.</p>

    <h2>Автозапуск</h2>
    <div class="updbox"><span>${schText}</span>
      <span><input type="time" id="schedtime" value="${esc(sch.time || "07:30")}">
        <button class="btn" data-sched="on">${sch.installed ? "Сохранить время" : "Включить"}</button>
        ${sch.installed ? `<button class="chip" data-sched="off">выключить</button>` : ""}</span></div>
    <p class="muted">Задача «BudgetUpdate» в Планировщике заданий Windows, работает без окна. Если в это время компьютер выключен или
      спит — обновление запустится, как только он включится.</p>

    <h2>Программа</h2>
    ${upgradeBox()}

    ${D.hidden.length ? `<h2>Удалённые покупки</h2>
    <table><tr><th>покупка</th><th>источник</th><th>удалена</th><th></th></tr>` + D.hidden.map(h => `<tr><td>${esc(h.label)}</td>
      <td>${esc(SOURCES[h.source] || h.source)}</td><td>${dtf(h.created)}</td>
      <td><button class="chip" data-restore="${esc(h.id)}">вернуть</button></td></tr>`).join("") + `</table>
    <p class="muted">Удалённые покупки не возвращаются при обновлениях. «Вернуть» пересобирает источник и сверку (~30 секунд).</p>` : ""}

    <h2>Журнал обновлений</h2>` +
    (UPD.runs.length ? UPD.runs.map((r, i) => `<details ${i === 0 && r.status && r.status !== "ok" ? "open" : ""}>
      <summary>${dtf(r.started)} · ${TRIG[r.trigger] || r.trigger} · ${stBadge(r.finished ? r.status : "running")}
        ${esc(r.summary || "")} <span class="muted">${dur(r.started, r.finished)}</span></summary>
      <table><tr><th>шаг</th><th>результат</th><th>что делать</th><th class="n">время</th></tr>` +
      r.steps.map(s => `<tr class="${s.status === "error" ? "err" : s.status === "warn" || s.status === "off" ? "wrn" : ""}"><td>${esc(s.title)}</td>
        <td>${stBadge(s.status)} ${esc(s.summary || "")}${s.output ? `<details><summary class="muted">вывод</summary><pre class="log">${esc(s.output)}</pre></details>` : ""}</td>
        <td>${s.hint && s.status !== "ok" ? cmd(s.hint) : ""}</td><td class="n muted">${dur(s.started, s.finished)}</td></tr>`).join("") +
      `</table></details>`).join("") : `<p class="muted">Пусто.</p>`);
}
async function startUpdate(steps) {
  const before = UPD && UPD.runs.length ? UPD.runs[0].id : (D.meta.update_last ? D.meta.update_last.id : 0);
  try { await post("/api/update/run", {steps}); } catch (e) { toast("Не запустилось: " + e.message); return; }
  UPD_WAIT = {before, since: Date.now()};
  toast(steps.length ? "Обновляю источник…" : "Обновление запущено");
  if (S.page === "settings") drawSettings();
  pollUpdate();
}
async function pollUpdate() {
  clearTimeout(updTimer);
  UPD = await (await fetch("/api/update")).json();
  const r = UPD.runs[0], started = r && r.id > UPD_WAIT.before, done = started && r.finished;
  if (!done && (started || UPD.running || Date.now() - UPD_WAIT.since < 30000)) {
    if (S.page === "settings") drawSettings();
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
    if (S.page === "settings") drawSettings();
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
  if (!l || !confirm(`Установить версию ${l.version}?${UPG.check.auto_restart ? " Сервер перезапустится, страница обновится сама." : ""}`)) return;
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
