# -*- coding: utf-8 -*-
"""
从浏览器提取登录 Cookie 并更新到 config.json（chat2api.py 逐请求热加载，无需重启）

三种用法:
  python update_cookies.py                 # 自动: 拉起专用浏览器打开校园 chat 页，
                                           #       等待你完成登录后自动提取并保存
  python update_cookies.py --port 9222     # 连接已开启远程调试端口的浏览器提取
  python update_cookies.py manual "easy_session=xxx; cookie_vjuid_login=yyy"
                                           # 手动粘贴 cookie 字符串

说明:
- 自动模式使用独立配置目录 .browser_profile，登录一次后该 profile 会保留会话，
  之后再次运行通常无需重新登录（除非学校 SSO 会话过期）
- 提取目标: easy_session / cookie_vjuid_login（ustb.edu.cn 域）
"""
import json
import os
import subprocess
import sys
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
PROFILE_DIR = os.path.join(BASE_DIR, ".browser_profile")
DEBUG_PORT = 9222
CHAT_URL = "http://chat.ustb.edu.cn/page/front/Mdefault/chat?ext=%7B%7D"
COOKIE_DOMAIN = "ustb.edu.cn"
NEEDED = ["easy_session", "cookie_vjuid_login"]
WAIT_SECONDS = 300  # 自动模式等待登录的最长时间

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"api_key": "sk-local", "cookies": {}}


def save_cookies(cookies: dict):
    cfg = load_config()
    cfg.setdefault("cookies", {}).update(cookies)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"[OK] 已写入 {CONFIG_FILE}")
    for k, v in cookies.items():
        print(f"     {k} = {v[:20]}{'...' if len(v) > 20 else ''}")


def verify_cookies(cookies: dict, quiet: bool = False):
    """访问一次 chat 页面粗略验证会话是否有效（302 跳 SSO 视为失效）"""
    try:
        r = requests.get(CHAT_URL, cookies=cookies, timeout=10, allow_redirects=False)
        ok = r.status_code == 200
        if not quiet:
            print(("[OK] 会话验证通过" if ok else f"[警告] 会话可能已失效 (HTTP {r.status_code})，建议重新登录学校 SSO"))
        return ok
    except requests.RequestException as e:
        if not quiet:
            print(f"[警告] 无法验证会话: {e}")
        return False


# ---------- CDP 提取 ----------

def cdp_alive(port):
    try:
        requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return True
    except requests.RequestException:
        return False


def get_cookies_via_cdp(port) -> dict:
    from websocket import create_connection  # websocket-client

    ver = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=3).json()
    # suppress_origin: Edge/Chrome 111+ 默认拒绝带 Origin 的 CDP WebSocket 握手 (403)
    ws = create_connection(ver["webSocketDebuggerUrl"], timeout=5, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                break
    finally:
        ws.close()
    if "error" in msg:
        raise RuntimeError(f"CDP 错误: {msg['error']}")
    return {c["name"]: c["value"] for c in msg["result"]["cookies"]
            if COOKIE_DOMAIN in c.get("domain", "")}


def extract_needed(all_cookies: dict) -> dict:
    return {k: v for k, v in all_cookies.items() if k in NEEDED and v}


def launch_browser():
    exe = next((p for p in BROWSER_CANDIDATES if os.path.exists(p)), None)
    if not exe:
        print("[错误] 未找到 Edge/Chrome，请用 --port 模式连接已运行的浏览器，或用 manual 模式")
        sys.exit(1)
    os.makedirs(PROFILE_DIR, exist_ok=True)
    subprocess.Popen([exe, f"--remote-debugging-port={DEBUG_PORT}",
                      f"--user-data-dir={PROFILE_DIR}",
                      "--remote-allow-origins=*",
                      "--no-first-run", "--no-default-browser-check", CHAT_URL])
    print(f"[..] 已启动浏览器（独立配置目录 {PROFILE_DIR}）")


def auto_mode():
    launched = False
    if not cdp_alive(DEBUG_PORT):
        launch_browser()
        launched = True
    else:
        print(f"[..] 检测到调试端口 {DEBUG_PORT} 已有浏览器，直接连接")

    print(f"[..] 等待登录并提取 cookie（最长 {WAIT_SECONDS} 秒，请在浏览器窗口完成学校 SSO 登录）...")
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        try:
            found = extract_needed(get_cookies_via_cdp(DEBUG_PORT))
        except Exception:
            found = {}
        if len(found) == len(NEEDED):
            save_cookies(found)
            verify_cookies(found)
            if launched:
                print("[提示] 浏览器保持打开；该 profile 已记住登录，下次更新通常无需重新登录")
            return
        time.sleep(2)
    print("[错误] 超时未提取到完整 cookie，请确认已在打开的浏览器中登录")


def manual_mode(cookie_str: str):
    cookies = {}
    for part in cookie_str.replace("\n", ";").split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies[k.strip()] = v.strip()
    found = extract_needed(cookies)
    if len(found) != len(NEEDED):
        missing = set(NEEDED) - set(found)
        print(f"[错误] 缺少: {', '.join(missing)}（请从浏览器 F12 -> Network -> 请求头 Cookie 中复制完整值）")
        sys.exit(1)
    save_cookies(found)
    verify_cookies(found)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "manual":
        if len(args) < 2:
            print('用法: python update_cookies.py manual "easy_session=...; cookie_vjuid_login=..."')
            sys.exit(1)
        manual_mode(args[1])
    elif args and args[0] == "--port":
        port = int(args[1]) if len(args) > 1 else DEBUG_PORT
        found = extract_needed(get_cookies_via_cdp(port))
        if len(found) != len(NEEDED):
            print(f"[错误] 该浏览器中未找到完整 cookie，缺: {set(NEEDED) - set(found)}")
            sys.exit(1)
        save_cookies(found)
        verify_cookies(found)
    else:
        auto_mode()
