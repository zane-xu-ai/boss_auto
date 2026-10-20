"""打开 BOSS 直聘职位页面；关闭页面或按 Ctrl+C 退出。"""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH", str(Path(__file__).parent / "data" / "pw-browsers")
)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page(viewport={"width": 1440, "height": 960})
    try:
        page.goto("https://www.zhipin.com/web/geek/jobs", wait_until="domcontentloaded")
        page.bring_to_front()
        print(f"已打开：{page.url}", flush=True)
        page.wait_for_event("close", timeout=0)
    except KeyboardInterrupt:
        pass
    finally:
        browser.close()
