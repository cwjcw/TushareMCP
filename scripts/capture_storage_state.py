from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from playwright.sync_api import sync_playwright


def _looks_logged_in(page) -> bool:
    try:
        url = page.url or ""
    except Exception:
        url = ""
    if "login" not in url:
        return True

    try:
        local_storage: dict[str, Any] = page.evaluate("() => Object.assign({}, window.localStorage)")
        keys = [str(k).lower() for k in local_storage.keys()]
        for k in keys:
            if "token" in k or "auth" in k:
                return True
    except Exception:
        pass

    try:
        cookies = page.context.cookies()
        for c in cookies:
            name = str(c.get("name", "")).lower()
            if "token" in name or "session" in name or "auth" in name:
                return True
    except Exception:
        pass

    return False


def main() -> None:
    url = "https://tushare.pro/weborder/#/login"
    state_path = Path("storage_state.json")
    flag = Path("LOGIN_DONE")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url)
        print("已打开登录页，请在浏览器内完成登录。")
        print("完成后可执行：touch LOGIN_DONE（或等待自动检测登录成功）。")

        while True:
            if flag.exists():
                break
            if _looks_logged_in(page):
                break
            time.sleep(1)

        context.storage_state(path=str(state_path))
        print(f"已保存登录态到: {state_path}")
        browser.close()
        flag.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

