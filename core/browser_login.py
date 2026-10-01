"""Вход в мобильное приложение через обычное окно Chrome (OAuth + PKCE).

Логин, пароль и коды вводишь ты сам в окне браузера — скрипт их не видит. Он только ловит в сетевом логе
браузера редирект на адрес приложения (например com.kaufland.kaufland://oauth/callback?code=...),
который браузер открыть не может, и достаёт из него одноразовый код.
"""
import base64
import hashlib
import re
import secrets
import time


def pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def chrome_service():
    """Драйвер под установленный Chrome; старый chromedriver из PATH игнорируем."""
    from selenium import webdriver
    from selenium.webdriver.common.selenium_manager import SeleniumManager

    paths = SeleniumManager().binary_paths(["--browser", "chrome", "--skip-driver-in-path"])
    return webdriver.ChromeService(executable_path=paths["driver_path"])


def wait_for_redirect(url: str, scheme: str, title: str, timeout: int = 600) -> str | None:
    """Открыть url в Chrome и дождаться редиректа на scheme://... Возвращает полный адрес редиректа."""
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException

    opts = webdriver.ChromeOptions()
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    opts.add_argument("--window-size=520,900")
    print(f"Открываю окно входа {title}. Войди как в приложении — окно закроется само. Ждём до {timeout // 60} мин...")
    rx = re.compile(re.escape(scheme) + r"[^\"'\s\\]+")
    driver = webdriver.Chrome(options=opts, service=chrome_service())
    try:
        driver.get(url)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1)
            try:
                logs = [e["message"] for e in driver.get_log("performance")] + [driver.current_url]
            except WebDriverException:
                print("Окно браузера закрыто — вход прерван.")
                return None
            for message in logs:
                if (m := rx.search(message)) and "code=" in m.group():
                    return m.group().replace("\\u0026", "&").replace("&amp;", "&")
        print("Не дождался входа.")
        return None
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass
