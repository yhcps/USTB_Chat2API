# -*- coding: utf-8 -*-
"""
USTB Chat2API 命令行工具箱（合并原 update_cookies.py 与 manage_keys.py）

子命令:
  cookie                                    # 自动: 拉起专用浏览器打开校园 chat 页，
                                            #       等待登录后自动提取并保存 Cookie
  cookie manual "easy_session=..; cookie_vjuid_login=.."
                                            # 手动粘贴 cookie 字符串
  cookie --port 9222                        # 连接已开启远程调试端口的浏览器提取
  key generate [名称]                       # 生成新 API Key（明文仅显示一次，落盘为哈希）
  key list                                  # 列出所有 Key（只显示前缀）
  key revoke <前缀>                         # 按 Key 前缀吊销
  restart                                   # 整进程重启（对话间隙自动执行；离线时拉起托盘）
  reload                                    # 内核热重载（解析器/工具桥，对话间隙毫秒级生效，不断连接）

说明:
- Cookie/Key 均热加载，增改后无需重启服务
- 自动模式使用独立配置目录 .browser_profile，登录一次后 profile 保留会话，
  下次更新通常无需重新登录（学校 SSO 会话过期除外）
"""
import hashlib
import json
import os
import secrets
import string
import subprocess
import sys
import time

import requests

BASE_DIR = os.environ.get("CHAT2API_HOME") or os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
sys.path.insert(0, BASE_DIR)

from chat2api import load_config, CONFIG_FILE, PORT  # noqa: E402

# ---------- Cookie 提取常量 ----------
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

# ---------- API Key 常量 ----------
KEYS_FILE = os.path.join(BASE_DIR, "api_keys.json")
KEY_PREFIX = "sk-ustb-"
KEY_ALPHABET = string.ascii_letters + string.digits  # 与 OpenAI key 相同的 base62 字符集


# ==================== Cookie 部分 ====================

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


def cookie_auto():
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


def cookie_manual(cookie_str: str):
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


def cookie_port(port: int):
    found = extract_needed(get_cookies_via_cdp(port))
    if len(found) != len(NEEDED):
        print(f"[错误] 该浏览器中未找到完整 cookie，缺: {set(NEEDED) - set(found)}")
        sys.exit(1)
    save_cookies(found)
    verify_cookies(found)


# ==================== API Key 部分 ====================

def _load_keys():
    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE, encoding="utf-8") as f:
            return json.load(f).get("keys", [])
    return []


def _save_keys(keys):
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump({"keys": keys}, f, ensure_ascii=False, indent=2)


def key_generate(name: str):
    keys = _load_keys()
    full_key = KEY_PREFIX + "".join(secrets.choice(KEY_ALPHABET) for _ in range(48))
    keys.append({
        "name": name,
        "key_hash": hashlib.sha256(full_key.encode()).hexdigest(),
        "key_prefix": full_key[:16] + "...",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "disabled": False,
    })
    _save_keys(keys)
    print(f"[OK] 已生成 API Key (名称: {name})")
    print(f"     {full_key}")
    print("     请立即保存，明文不会再次显示。")
    print("\n使用方式:")
    print(f'  curl http://127.0.0.1:{PORT}/v1/models -H "Authorization: Bearer {full_key}"')


def key_list():
    keys = _load_keys()
    if not keys:
        cmd = "ustb-chat2api cli" if getattr(sys, "frozen", False) else "python cli.py"
        print(f"暂无 key，先运行: {cmd} key generate [名称]")
        return
    print(f"共 {len(keys)} 个 key:")
    for i, k in enumerate(keys, 1):
        status = "已吊销" if k.get("disabled") else "有效"
        print(f"  {i}. {k['key_prefix']}  名称={k['name']}  创建={k['created']}  状态={status}")


def key_revoke(prefix: str):
    keys = _load_keys()
    prefix = prefix.rstrip(".")

    def matches(k):
        p = k["key_prefix"].rstrip(".")
        return p.startswith(prefix) or p[len(KEY_PREFIX):].startswith(prefix)

    hit = [k for k in keys if matches(k) and not k.get("disabled")]
    if not hit:
        print(f"未找到前缀为 {prefix} 的有效 key")
        sys.exit(1)
    for k in hit:
        k["disabled"] = True
    _save_keys(keys)
    for k in hit:
        print(f"[OK] 已吊销 {k['key_prefix']} (名称: {k['name']})")


# ==================== 服务重启/热更新部分 ====================

def _post_action(path: str, action: str):
    """向本地服务发送 POST 管理动作，返回 (ok, detail)"""
    cfg = load_config()
    try:
        r = requests.post(f"http://127.0.0.1:{PORT}{path}", timeout=10,
                          headers={"Authorization": f"Bearer {cfg['api_key']}"})
        if r.status_code == 200:
            return True, r.json().get("detail", "")
        return False, f"HTTP {r.status_code} {r.text[:120]}"
    except requests.RequestException as e:
        return False, f"{e.__class__.__name__}: 服务未运行? 端口 {PORT}"


def service_reload() -> int:
    """对话间隙热重载内核（kernel.py 解析器/工具桥），不断连接、不换进程"""
    ok, detail = _post_action("/reload", "reload")
    print(f"[{'OK' if ok else '错误'}] {detail}")
    return 0 if ok else 1


def service_restart() -> int:
    """整进程重启（对话间隙感知）；服务离线时拉起托盘兜底"""
    ok, detail = _post_action("/restart", "restart")
    if ok:
        print(f"[OK] {detail}（http://127.0.0.1:{PORT}/v1）")
        return 0
    if "服务未运行" in detail or "ConnectionError" in detail or "NameResolver" in detail:
        print(f"[提示] 服务未运行，拉起托盘以启动服务...")
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable], cwd=BASE_DIR)
        elif os.name == "nt":
            pythonw = sys.executable.replace("python.exe", "pythonw.exe")
            subprocess.Popen([pythonw, os.path.join(BASE_DIR, "tray.py")], cwd=BASE_DIR)
        else:
            subprocess.Popen([sys.executable, os.path.join(BASE_DIR, "tray.py")], cwd=BASE_DIR)
        print("[OK] 托盘已拉起，服务将由其自动启动")
        return 0
    print(f"[错误] 重启失败: {detail}")
    return 1


# ==================== 入口 ====================

def main(argv: list) -> int:
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "cookie":
        if rest and rest[0] == "manual":
            if len(rest) < 2:
                print('用法: python cli.py cookie manual "easy_session=...; cookie_vjuid_login=..."')
                return 1
            cookie_manual(rest[1])
        elif rest and rest[0] == "--port":
            cookie_port(int(rest[1]) if len(rest) > 1 else DEBUG_PORT)
        else:
            cookie_auto()
    elif cmd == "key":
        sub = rest[0] if rest else ""
        if sub == "generate":
            key_generate(rest[1] if len(rest) > 1 else f"key-{int(time.time())}")
        elif sub == "list":
            key_list()
        elif sub == "revoke":
            if len(rest) < 2:
                print("用法: python cli.py key revoke <key前缀>")
                return 1
            key_revoke(rest[1])
        else:
            print(__doc__)
            return 1
    elif cmd == "restart":
        return service_restart()
    elif cmd == "reload":
        return service_reload()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
