"""使用 cookies/ 中的登录状态打开 BOSS 直聘职位页面。"""
from http.cookiejar import MozillaCookieJar
import os
from pathlib import Path
import time

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BASE_DIR / "data" / "pw-browsers"))

jar = MozillaCookieJar(str(BASE_DIR / "cookies" / "www.zhipin.com_cookies.txt"))
jar.load(ignore_discard=True, ignore_expires=True)
cookies = []
for cookie in jar:
    if cookie.expires and cookie.expires <= time.time():
        continue
    item = {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path or "/",
        "secure": cookie.secure,
        "httpOnly": cookie.has_nonstandard_attr("HTTPOnly"),
    }
    if cookie.expires:
        item["expires"] = cookie.expires
    cookies.append(item)
if not cookies:
    raise SystemExit("Cookie 文件中没有可用的 Cookie，请更新登录状态。")

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(viewport={"width": 1440, "height": 960})
    context.add_cookies(cookies)
    page = context.new_page()
    try:
        page.goto("https://www.zhipin.com/web/geek/jobs", wait_until="domcontentloaded")
        page.bring_to_front()
        page.wait_for_timeout(5000)
        page.screenshot(path=str(BASE_DIR / "data" / "current-page.png"))
        print(f"已加载 {len(cookies)} 个 Cookie，已打开：{page.url}", flush=True)
        page.wait_for_event("close", timeout=0)
    except KeyboardInterrupt:
        pass
    finally:
        browser.close()
