# -*- coding: utf-8 -*-
"""
USTB chat2api 系统托盘常驻程序

- 后台运行 OpenAI 兼容转发服务 (http://127.0.0.1:8787/v1)
- 托盘图标即状态灯: 绿=服务与会话正常, 黄=服务在线但会话失效, 红=服务异常
- 每 5 分钟自动巡检并刷新图标
- 所有操作通过托盘右键菜单完成，结果以系统通知反馈

运行: pythonw tray.py   （无任何窗口，纯后台常驻）
      python tray.py    （控制台自动隐藏）
退出: 托盘图标右键 -> 退出
"""
import ctypes
import json
import os
import secrets
import string
import subprocess
import sys
import threading
import time
import traceback

import requests
from PIL import Image, ImageDraw, ImageFont
import pystray

from .utils import BASE_DIR, LOG_DIR, CONFIG_FILE, PORT, MODEL_NAME, load_config, TRAY_LOG

LOG_FILE = TRAY_LOG


def log(msg: str):
    """pythonw 无控制台，异常落盘到 tray.log"""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

from . import server as chat2api
from .cli import (cdp_alive, get_cookies_via_cdp, extract_needed,
                 save_cookies, verify_cookies, launch_browser,
                 NEEDED, DEBUG_PORT, CHAT_URL)

BASE_URL = f"http://127.0.0.1:{PORT}/v1"
CHAT_ENDPOINT = f"{BASE_URL}/chat/completions"
CHECK_INTERVAL = 300  # 自动巡检间隔（秒）

STATE = {"service": "checking", "session": "unknown"}
STATE_LOCK = threading.Lock()

COLORS = {"green": (46, 204, 113), "yellow": (241, 196, 15), "red": (231, 76, 60),
          "gray": (128, 128, 128)}

# ---------- 服务控制 ----------

def service_online():
    try:
        requests.get(f"{BASE_URL}/models", timeout=2)
        return True
    except requests.RequestException:
        return False


def start_server():
    def run():
        try:
            import uvicorn
            # pythonw 下 sys.stdout 为 None, uvicorn 默认日志配置会崩溃, 须禁用
            uvicorn.run(chat2api.app, host=chat2api.get_host(), port=PORT,
                        log_level="warning", log_config=None, access_log=False)
        except Exception:
            log("uvicorn 线程异常:\n" + traceback.format_exc())
    threading.Thread(target=run, daemon=True, name="uvicorn").start()


def start_server_if_needed():
    if not service_online():
        start_server()
        for _ in range(20):
            if service_online():
                return True
            time.sleep(0.3)
        return False
    return True


# ---------- 状态检测 ----------

def check_session():
    """已登录则自动截取 CDP 浏览器最新 cookie 并安全存储; 返回 'valid'/'expired'/'unconfigured'"""
    cfg = load_config()
    ck = cfg.get("cookies", {})
    if not (ck.get("easy_session") and ck.get("cookie_vjuid_login")):
        return "unconfigured"
    if cdp_alive(DEBUG_PORT):
        try:
            found = extract_needed(get_cookies_via_cdp(DEBUG_PORT))
            if len(found) == len(NEEDED) and any(found[k] != ck.get(k) for k in NEEDED):
                save_cookies(found)
                ck = load_config()["cookies"]
        except Exception:
            pass
    return "valid" if verify_cookies(ck, quiet=True) else "expired"


def refresh_state():
    """巡检服务 + 会话，返回图标颜色"""
    cfg = load_config()
    with STATE_LOCK:
        try:
            r = requests.get(f"{BASE_URL}/models",
                             headers={"Authorization": f"Bearer {cfg['api_key']}"}, timeout=5)
            STATE["service"] = "ok" if r.status_code == 200 else ("bad_key" if r.status_code == 401 else "error")
        except requests.RequestException:
            STATE["service"] = "down"
        STATE["session"] = check_session()
        if STATE["service"] in ("down", "error"):
            color = "red"
        elif STATE["service"] == "bad_key" or STATE["session"] in ("expired", "unconfigured"):
            color = "yellow"
        else:
            color = "green"
    return color


# ---------- 托盘图标 ----------

def make_icon(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=COLORS[color])
    try:
        font = ImageFont.load_default(size=30)
    except TypeError:
        font = ImageFont.load_default()
    d.text((32, 30), "AI", font=font, fill="white", anchor="mm")
    return img


def status_text(_=None):
    with STATE_LOCK:
        svc = {"ok": "正常", "bad_key": "Key无效", "down": "未运行",
               "error": "异常", "checking": "检测中"}.get(STATE["service"], "未知")
        ses = {"valid": "已登录", "expired": "已失效", "unconfigured": "未登录",
               "unknown": "检测中"}.get(STATE["session"], "未知")
    return f"服务: {svc}    会话: {ses}"


def notify(icon, msg):
    try:
        icon.notify(msg, "chat2api")
        threading.Timer(5, icon.remove_notification).start()
    except Exception:
        pass


def refresh_icon(icon):
    icon.icon = make_icon(refresh_state())
    icon.title = f"chat2api - {status_text()}"


# ---------- 菜单动作 ----------

def copy_to_clipboard(text) -> bool:
    try:
        p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, shell=True)
        p.communicate(text.encode("gbk", "replace"))
        return True
    except Exception:
        return False


def on_copy_endpoint(icon, _):
    ok = copy_to_clipboard(CHAT_ENDPOINT)
    notify(icon, ("端点已复制 ✓\n" if ok else "复制失败: ") + CHAT_ENDPOINT)


def on_copy_key(icon, _):
    key = load_config().get("api_key", "")
    ok = copy_to_clipboard(key)
    notify(icon, ("API Key 已复制 ✓\n" if ok else "复制失败: ") + key)


def on_check(icon, _):
    refresh_icon(icon)
    notify(icon, f"已巡检\n{status_text()}")


def on_test_chat(icon, _):
    def worker():
        cfg = load_config()
        try:
            r = requests.post(CHAT_ENDPOINT, timeout=120,
                              headers={"Authorization": f"Bearer {cfg['api_key']}"},
                              json={"model": MODEL_NAME,
                                    "messages": [{"role": "user", "content": "回复两个字：正常"}]})
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"].strip()
                notify(icon, f"对话测试成功 ✓\n回复: {content[:40]}")
            else:
                notify(icon, f"对话测试失败: HTTP {r.status_code}")
        except requests.RequestException as e:
            notify(icon, f"对话测试失败: {e.__class__.__name__}")
    threading.Thread(target=worker, daemon=True).start()


def on_login(icon, _):
    def worker():
        notify(icon, "正在拉起浏览器，请在窗口中完成学校 SSO 登录...")
        if not cdp_alive(DEBUG_PORT):
            launch_browser()
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                found = extract_needed(get_cookies_via_cdp(DEBUG_PORT))
            except Exception:
                found = {}
            if len(found) == len(NEEDED):
                save_cookies(found)
                ok = verify_cookies(found, quiet=True)
                refresh_icon(icon)
                notify(icon, "Cookie 已截取并保存 ✓ 会话有效" if ok
                       else "Cookie 已保存，但会话验证未通过，请重试登录")
                return
            time.sleep(2)
        notify(icon, "等待登录超时（5 分钟），请重试")
    threading.Thread(target=worker, daemon=True).start()


def on_regen_key(icon, _):
    cfg = load_config()
    cfg["api_key"] = "sk-" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    notify(icon, f"已生成新 API Key（立即生效，旧 Key 失效）:\n{cfg['api_key']}")


_last_tui = 0.0


def on_open_tui(icon, _):
    """打开 TUI 控制台（托盘左键默认动作；防抖避免双击触发两次）"""
    global _last_tui
    if time.time() - _last_tui < 2:
        return
    _last_tui = time.time()
    python_exe = sys.executable.replace("pythonw.exe", "python.exe")
    # 启动 TUI 控制台: 使用 python -m 方式调用包模块
    subprocess.Popen([python_exe, "-m", "ustb_chat2api", "tui"],
                     cwd=BASE_DIR, creationflags=subprocess.CREATE_NEW_CONSOLE)


def on_open_dashboard(icon, _):
    """在默认浏览器中打开 Dashboard"""
    if not service_online():
        notify(icon, f"服务未运行，Dashboard 无法打开\n请先启动服务")
        return
    import webbrowser
    webbrowser.open(f"http://127.0.0.1:{PORT}/dashboard")


def on_quit(icon, _):
    icon.stop()
    os._exit(0)


def auto_refresh(icon):
    """定期巡检: 服务掉线自动拉起（托盘兼任守护进程），并刷新状态图标"""
    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            if not service_online():
                start_server_if_needed()
            refresh_icon(icon)
        except Exception:
            pass


def main():
    # 单实例保护: 已有托盘常驻进程时本次启动直接退出
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "chat2api_tray_mutex")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        log("已有托盘实例在运行，本次启动退出")
        return

    # 隐藏控制台窗口（以 python.exe 启动时）
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)

    try:
        if not start_server_if_needed():
            log(f"服务启动失败，端口 {PORT} 未能监听")
        menu = pystray.Menu(
            pystray.MenuItem(status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打开 TUI 控制台（左键双击图标）", on_open_tui, default=True),
            pystray.MenuItem("打开 Dashboard", on_open_dashboard),
            pystray.MenuItem("复制 Chat 端点", on_copy_endpoint),
            pystray.MenuItem("复制 API Key", on_copy_key),
            pystray.MenuItem("重新生成 API Key", on_regen_key),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("巡检服务与会话", on_check),
            pystray.MenuItem("测试对话", on_test_chat),
            pystray.MenuItem("登录 / 更新 Cookie", on_login),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", on_quit),
        )
        icon = pystray.Icon("chat2api", make_icon("gray"), "chat2api 启动中...", menu=menu)
        refresh_icon(icon)  # 启动自检并着色
        threading.Thread(target=auto_refresh, args=(icon,), daemon=True).start()
        icon.run()
    except Exception:
        log("托盘主循环异常:\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
