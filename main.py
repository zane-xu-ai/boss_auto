"""使用 Cookie 和本机 Chrome 的持久化会话打开 BOSS 职位页面。"""
import argparse
import hashlib
from http.cookiejar import MozillaCookieJar
import json
from pathlib import Path
import select
import sys
import time
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError, sync_playwright

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
COOKIE_FILE = BASE_DIR / "cookies" / "www.zhipin.com_cookies.txt"
SESSION_FILE = DATA_DIR / "browser-session.json"
JOBS_URL = "https://www.zhipin.com/web/geek/jobs"
BROWSER_OPTIONS = {
    "channel": "chrome",
    "headless": False,
    "viewport": {"width": 1440, "height": 960},
    "locale": "zh-CN",
    "timezone_id": "Asia/Shanghai",
    "extra_http_headers": {"Accept-Language": "zh-CN"},
    # User-Agent 和 Sec-CH-UA 使用本机 Chrome 自己生成的真实版本信息。
}
JOB_XPATH = (
    "//div[@id='wrap']/div[@class='page-jobs-main']"
    "/div[@class='job-recommend-result']/div[@class='recommend-result-inner']"
    "/div[@class='recommend-result-job clearfix']/div[@class='job-list-container']"
    "/ul[@class='rec-job-list']/div[@class='card-area is-seen'][1]"
    "/div[@class='job-card-wrap active']/li[@class='job-card-box']"
    "/div[@class='job-info']/div[@class='job-title clearfix']/a[@class='job-name']"
)


def load_exported_cookies(path):
    jar = MozillaCookieJar(str(path))
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
    return cookies


def restore_cookies(context, cookie_file=COOKIE_FILE, session_file=SESSION_FILE):
    source_hash = hashlib.sha256(cookie_file.read_bytes()).hexdigest()
    saved = json.loads(session_file.read_text()) if session_file.exists() else {}
    if saved.get("source_hash") == source_hash:
        # 保留网站更新过的 Cookie，包括浏览器退出后可能丢失的会话 Cookie。
        cookies = saved["cookies"]
        print("复用上次运行保存的浏览器会话。", flush=True)
    else:
        cookies = load_exported_cookies(cookie_file)
        print("从 cookies/ 文件导入登录状态。", flush=True)
    cookies = [c for c in cookies if c.get("expires", -1) <= 0 or c["expires"] > time.time()]
    if not cookies:
        raise SystemExit("没有可用的 Cookie，请更新 cookies/ 中的登录文件。")
    context.add_cookies(cookies)
    return source_hash


def save_session(context, source_hash, path=SESSION_FILE):
    state = {"source_hash": source_hash, "cookies": context.cookies()}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False))
    temporary.chmod(0o600)
    temporary.replace(path)


def is_security_page(url):
    return urlsplit(url).path == "/web/passport/zp/security.html"


def wait_safely(page, milliseconds, security):
    deadline = time.monotonic() + milliseconds / 1000
    while time.monotonic() < deadline:
        if security["loop"]:
            raise SystemExit("检测到连续安全页跳转，停止本次运行。")
        page.wait_for_timeout(min(200, max(1, (deadline - time.monotonic()) * 1000)))


def wait_for_job_list(page, security, timeout=60):
    """等待页面真正就绪；安全校验期间不点击，也不主动刷新或重试请求。"""
    deadline = time.monotonic() + timeout
    ready_since = None
    while time.monotonic() < deadline:
        if security["loop"]:
            raise SystemExit("检测到连续安全页跳转，已停止本次运行。")
        if page.is_closed():
            raise SystemExit("浏览器已关闭。")
        ready = (
            urlsplit(page.url).path == "/web/geek/jobs"
            and page.locator(".expect-list").is_visible()
        )
        if ready:
            ready_since = ready_since or time.monotonic()
            if time.monotonic() - ready_since >= 2:
                return
        else:
            ready_since = None
        page.wait_for_timeout(200)
    raise SystemExit("职位页尚未稳定就绪，本次不执行点击。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual-login", action="store_true", help="不导入旧 Cookie，等待人工登录并按回车继续")
    args = parser.parse_args()
    DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    profile_dir = DATA_DIR / ("manual-browser-profile" if args.manual_login else "browser-profile")
    session_path = DATA_DIR / "manual-browser-session.json" if args.manual_login else SESSION_FILE
    profile_dir.mkdir(mode=0o700, exist_ok=True)
    with sync_playwright() as playwright:
        # 专用配置目录，不使用或改动日常 Chrome 的个人配置目录。
        context = playwright.chromium.launch_persistent_context(profile_dir, **BROWSER_OPTIONS)
        security = {"times": [], "loop": False, "closed": False}
        source_hash = None

        def observe_page(watched_page):
            def on_navigation(frame):
                if frame != watched_page.main_frame or not is_security_page(frame.url):
                    return
                now = time.monotonic()
                security["times"] = [t for t in security["times"] if now - t < 10] + [now]
                print("进入网站安全校验，暂停自动操作，请在浏览器中处理。", flush=True)
                if len(security["times"]) >= 3:
                    security["loop"] = True
                    print("10 秒内连续进入安全页 3 次，将停止本次运行。", flush=True)
            watched_page.on("framenavigated", on_navigation)

        def on_close(_context):
            security["closed"] = True

        context.on("close", on_close)
        context.on("page", observe_page)
        for existing_page in context.pages:
            observe_page(existing_page)
        try:
            if args.manual_login:
                source_hash = "manual-login"
            else:
                source_hash = restore_cookies(context)
            page = context.pages[0] if context.pages else context.new_page()
            if args.manual_login:
                page.goto("https://www.zhipin.com/", wait_until="domcontentloaded")
                page.bring_to_front()
                print("未导入旧 Cookie。请手动登录并打开职位页面；准备好后在终端按回车继续。", flush=True)
                while not select.select([sys.stdin], [], [], 0)[0]:
                    save_session(context, source_hash, session_path)
                    wait_safely(page, 250, security)
                if not sys.stdin.readline():
                    raise SystemExit("输入已关闭，本次不执行职位点击。")
            if urlsplit(page.url).path != "/web/geek/jobs":
                page.goto(JOBS_URL, wait_until="domcontentloaded")
            page.bring_to_front()
            wait_for_job_list(page, security)
            save_session(context, source_hash, session_path)
            print("页面已稳定，请手动点击求职期望；5 秒后点击第一个职位名称。", flush=True)
            wait_safely(page, 5000, security)
            wait_for_job_list(page, security)
            job = page.locator(f"xpath={JOB_XPATH}")
            job.wait_for(state="visible", timeout=15000)
            job_name = job.inner_text().strip()
            previous_pages = set(context.pages)
            job.click(timeout=15000)
            wait_safely(page, 2000, security)
            new_pages = [p for p in context.pages if p not in previous_pages]
            result_page = new_pages[-1] if new_pages else page
            result_page.bring_to_front()
            if is_security_page(result_page.url):
                print("点击后进入安全校验，尚未确认职位打开。", flush=True)
            else:
                result_page.screenshot(path=str(DATA_DIR / "current-page.png"))
                print(f"已执行职位名称点击：{job_name}，请核对浏览器显示。", flush=True)
            while not security["closed"] and not result_page.is_closed():
                save_session(context, source_hash, session_path)
                wait_safely(result_page, 1000, security)
        except PlaywrightError:
            if security["loop"]:
                print("已停止安全页循环，未确认点击成功。", flush=True)
            elif not security["closed"]:
                raise
        except KeyboardInterrupt:
            pass
        finally:
            if not security["closed"]:
                try:
                    if source_hash:
                        save_session(context, source_hash, session_path)
                finally:
                    context.close()


if __name__ == "__main__":
    main()
