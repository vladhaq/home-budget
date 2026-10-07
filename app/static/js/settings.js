// Страница «Настройки»: обновление, источники, автозапуск, журнал.
// ---------- настройки: обновление данных, источники, автозапуск, журнал
let UPD = null, SCHED = null, updTimer = null, UPD_WAIT = null;
let BIEDRONKA_EXTENSION = null, BIEDRONKA_EXTENSION_CHECKING = false;
let BIEDRONKA_SESSION = null, BIEDRONKA_SESSION_CHECKING = false;
const UST = {ok: ["✓", "--green", "готово"], warn: ["!", "--orange", "внимание"], off: ["!", "--orange", "не настроено"],
             skip: ["–", "--text-2", "пропущено"], error: ["✗", "--red", "ошибка"], running: ["…", "--accent", "идёт"]};
const TRIG = {manual: "из терминала", schedule: "автозапуск", ui: "из интерфейса"};
const stBadge = st => { const [m, c, txt] = UST[st] || UST.running; return `<span class="st" style="--c:var(${c})">${m} ${txt}</span>`; };
const dtf = s => s ? `${s.slice(8, 10)}.${s.slice(5, 7)}.${s.slice(2, 4)}${s.length > 10 ? " " + s.slice(11, 16) : ""}` : "—";
const dur = (a, b) => { if (!a || !b) return ""; const s = Math.round((new Date(b) - new Date(a)) / 1000);
  return s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин ${s % 60} с`; };
const cmd = c => c ? (/^python /.test(c) ? `<code data-copy="${esc(c)}" title="нажми, чтобы скопировать">${esc(c)}</code>` : esc(c)) : "";
const updRunning = () => !!(UPD_WAIT || (UPD && UPD.running));

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
  BIEDRONKA_SESSION = null;
  checkBiedronkaExtension();
  checkBiedronkaSession();
  if (!UPD) UPD = await (await fetch("/api/update")).json();
  if (!SCHED) {
    SCHED = {loading: true};
    fetch("/api/update/schedule").then(r => r.json()).then(s => { SCHED = s; if (S.page === "settings") drawSettings(); });
  }
  drawSettings();
}
async function checkBiedronkaSession() {
  if (BIEDRONKA_SESSION !== null || BIEDRONKA_SESSION_CHECKING) return;
  BIEDRONKA_SESSION_CHECKING = true;
  try {
    const response = await fetch("/api/biedronka/session-status");
    const result = await response.json();
    if (!response.ok || !["valid", "missing", "expired", "unavailable"].includes(result.status)) {
      throw new Error("Unexpected Biedronka session status response");
    }
    BIEDRONKA_SESSION = result.status;
  } catch {
    BIEDRONKA_SESSION = "unavailable";
  } finally {
    BIEDRONKA_SESSION_CHECKING = false;
    if (S.page === "settings") drawSettings();
  }
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
      <button class="btn" data-updrun="" ${run ? "disabled" : ""}>${run ? "Обновление идёт…" : "Обновить всё сейчас"}</button>
      <span class="big">${run ? `<span class="spin"></span>${cur ? "сейчас: " + esc(cur.title) : "запускается…"}`
        : lastDone ? `Последнее обновление: <b>${dtf(lastDone.finished)}</b> (${TRIG[lastDone.trigger] || lastDone.trigger}) ${stBadge(lastDone.status)}
            <span class="muted">${esc(lastDone.summary || "")}</span>` : "Обновлений ещё не было."}</span>
    </div>
    <p class="muted">По очереди: Lidl, Kaufland, Biedronka, почта, фото из Telegram и папки receipts/inbox, выписка PKO, сверка и категории,
      резервная копия. Обычно 1–2 минуты; первая загрузка чеков Biedronka может занять дольше. Ошибка одного источника не останавливает остальные.
      Пароли здесь не запрашиваются; Biedronka подключается расширением Chrome, остальные источники — по команде из строки.</p>

    ${BIEDRONKA_SESSION === "missing" || BIEDRONKA_SESSION === "expired" ? `
      <h2>Подключение Biedronka</h2>
      <div class="updbox">
        ${BIEDRONKA_EXTENSION === false ? `<div class="muted">Расширение «Budget Browser Connector» не обнаружено. В Chrome открой
          <code>chrome://extensions</code>, включи «Режим разработчика» и выбери «Загрузить распакованное расширение»,
          указав папку <code>C:\\LB\\budget\\tools\\browser-connector</code>.</div>` : ""}
        <div><b>${BIEDRONKA_SESSION === "expired" ? "Сессия Moja Biedronka истекла" : "Biedronka ещё не подключена"}</b>
          <div class="muted">Войди в Moja Biedronka, нажми значок расширения «Budget Browser Connector» и кнопку «Подключить к бюджету».
            Cookies передаются только этому приложению на этом компьютере.</div></div>
      </div>
      ` : ""}
    ${BIEDRONKA_SESSION === "unavailable" ? `<p class="muted">Не удалось проверить сессию Biedronka: сайт временно недоступен. Попробуй позже.</p>` : ""}

    <h2>Источники</h2>
    <table><tr><th>источник</th><th>подключение</th><th>успешно обновлено</th><th>данные по</th><th>последний результат</th><th></th></tr>` +
    UPD.sources.map(s => {
      const l = s.last, bad = (l && l.status === "error") || !!s.warn, wrn = l && (l.status === "warn" || l.status === "off");
      return `<tr class="${bad ? "err" : wrn ? "wrn" : ""}"><td><b>${esc(s.title)}</b></td>
        <td>${s.always ? `<span class="muted">не требуется</span>` : s.ready ? "✓ подключено"
          : `<span class="warn">${esc(s.missing)}</span><br>${cmd(s.setup)}`}</td>
        <td>${s.last_ok ? dtf(s.last_ok.finished) : "—"}</td>
        <td>${s.data ? dtf(s.data.slice(0, 16)) : ""}<div class="muted" style="font-size:.8em">${esc(s.note || "")}</div></td>
        <td>${l ? `${stBadge(l.status)} ${esc(l.summary || "")}${l.hint && !(l.summary || "").includes(l.hint) ? `<br>→ ${cmd(l.hint)}` : ""}` : "—"}
          ${s.warn ? `<div class="up">⚠ ${esc(s.warn)} — ${cmd(s.setup)}</div>` : ""}</td>
        <td><button class="chip" data-updrun="${s.key}" ${run ? "disabled" : ""}>обновить</button></td></tr>`;
    }).join("") + `</table>
    <p class="muted">Команды в серых рамках нажатием копируются — вставь в терминал в папке budget. «Обновить» в строке — только этот
      источник (и сверка). Ошибка с пометкой «в программе» — открой журнал ниже и пришли текст Claude.</p>

    <h2>Автозапуск</h2>
    <div class="updbox"><span>${schText}</span>
      <span><input type="time" id="schedtime" value="${esc(sch.time || "07:30")}">
        <button class="btn" data-sched="on">${sch.installed ? "Сохранить время" : "Включить"}</button>
        ${sch.installed ? `<button class="chip" data-sched="off">выключить</button>` : ""}</span></div>
    <p class="muted">Задача «BudgetUpdate» в Планировщике заданий Windows, работает без окна. Если в это время компьютер выключен или
      спит — обновление запустится, как только он включится.</p>

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
async function setSchedule(on) {
  const time = (document.getElementById("schedtime") || {}).value || "07:30";
  SCHED = {loading: true}; drawSettings();
  try { const j = await post("/api/update/schedule", {on, time}); SCHED = j.schedule;
        toast(on ? `Автозапуск: каждый день в ${SCHED.time}` : "Автозапуск выключен"); }
  catch (e) { SCHED = null; toast("Не получилось: " + e.message); }
  if (S.page === "settings") renderSettings();
}
// «^medaliony\ z\ fileta$» -> «medaliony z fileta» (точное название); остальное показываем как есть
const humanPattern = p => /^\^.*\$$/.test(p) ? p.slice(1, -1).replace(/\\(.)/g, "$1") : p;
