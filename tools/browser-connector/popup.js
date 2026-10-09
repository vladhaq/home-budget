const API = "http://127.0.0.1:8765/api/biedronka/session-transfer";
const START_API = `${API}/start`;
const button = document.getElementById("transfer");
const status = document.getElementById("status");

button.addEventListener("click", async () => {
  button.disabled = true;
  status.textContent = "Ищу cookies Moja Biedronka…";
  try {
    const cookies = await chrome.cookies.getAll({domain: "biedronka.pl"});
    const biedronkaCookies = cookies
      .filter(cookie => {
        const domain = cookie.domain.replace(/^\./, "").toLowerCase();
        return domain === "biedronka.pl" || domain.endsWith(".biedronka.pl");
      })
      .map(cookie => ({
        name: cookie.name,
        value: cookie.value,
        domain: cookie.domain,
        path: cookie.path,
        expires: Number.isFinite(cookie.expirationDate) ? Math.floor(cookie.expirationDate) : null,
        secure: cookie.secure
      }));
    if (!biedronkaCookies.length) {
      throw new Error("Не нашёл cookies. Сначала войди в Moja Biedronka в этом Chrome.");
    }
    status.textContent = "Создаю защищённое подключение…";
    const pairingResponse = await fetch(START_API, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: "{}"
    });
    const pairing = await pairingResponse.json();
    if (!pairingResponse.ok || !pairing.ok) {
      throw new Error(pairing.error || `Локальное приложение ответило ${pairingResponse.status}.`);
    }
    status.textContent = "Проверяю авторизацию на сайте…";
    const response = await fetch(API, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        token: pairing.token,
        user_agent: navigator.userAgent,
        cookies: biedronkaCookies
      })
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw new Error(result.error || `Локальное приложение ответило ${response.status}.`);
    }
    status.textContent = `Готово: сессия подключена и сохранена локально (${result.cookies} cookies).`;
  } catch (error) {
    status.textContent = error.message || "Не удалось подключить сессию.";
  } finally {
    button.disabled = false;
  }
});
