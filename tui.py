# -*- coding: utf-8 -*-
"""
USTB chat2api TUI 控制台

启动时自动: 初始化默认 Key -> 启动/检测本地服务 -> 验证端点与 Key -> 检测登录状态
所有核心操作单键完成:
  [1] 复制 Chat Completions 端点   [2] 复制 Base URL   [3] 复制 API Key
  [4] 登录 / 更新 Cookie（已登录自动截取存储；未登录自动拉起浏览器，登录后自动截取）
  [5] 重新生成 API Key（立即生效，无需重启）
  [6] 重新检测服务与密钥
  [7] 发送测试对话
  [9] 重启服务（整进程替换，加载最新代码，调试改完代码后用）
  [0] 检测登录状态
  [h] 隐藏到托盘（服务转交托盘常驻，窗口关闭）
  [q] 退出（停止服务）

运行: python tui.py
"""
import json
import os
import platform
import secrets
import string
import subprocess
import sys
import threading
import time

import requests

IS_WINDOWS = platform.system() == "Windows"

BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
sys.path.insert(0, BASE_DIR)

import chat2api
from chat2api import load_config, CONFIG_FILE, PORT, MODEL_NAME, UPSTREAM
from cli import (cdp_alive, get_cookies_via_cdp, extract_needed,
                 save_cookies, verify_cookies, launch_browser,
                 NEEDED, DEBUG_PORT, CHAT_URL)

BASE_URL = f"http://127.0.0.1:{PORT}/v1"
CHAT_ENDPOINT = f"{BASE_URL}/chat/completions"

# ANSI 颜色
GREEN, RED, YELLOW, CYAN, DIM, RESET, BOLD = ("\033[92m", "\033[91m", "\033[93m",
                                              "\033[96m", "\033[2m", "\033[0m", "\033[1m")

STATE = {"service": "checking", "service_detail": "", "session": "unknown", "msg": ""}
SERVICE_LOCK = threading.Lock()


# ---------- 配置 ----------

def ensure_default_key():
    """初始化: 无 config.json 或缺少 api_key 时生成默认 key"""
    cfg = load_config()
    if not cfg.get("api_key"):
        cfg["api_key"] = "sk-local"
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------- 服务控制与验证 ----------

def service_online():
    try:
        requests.get(f"{BASE_URL}/models", timeout=2)
        return True
    except requests.RequestException:
        return False


def start_server():
    def run():
        import uvicorn
        uvicorn.run(chat2api.app, host=chat2api.get_host(), port=PORT, log_level="warning")
    threading.Thread(target=run, daemon=True, name="uvicorn").start()


def _exec_self_tui():
    """TUI 整进程自我替换：同进程重启 uvicorn 不会重载 chat2api 模块，必须换新进程"""
    if getattr(sys, "frozen", False):
        os.execv(sys.executable, [sys.executable, "tui"])
    else:
        python_exe = sys.executable.replace("pythonw.exe", "python.exe")
        os.execv(python_exe, [python_exe, os.path.join(BASE_DIR, "tui.py")])


def _api_restart_callback():
    """供 /restart 端点调用：先让 HTTP 应答送达，再整进程重启"""
    time.sleep(0.5)
    _exec_self_tui()


def validate_service():
    """验证服务连通性 + Key 有效性，结果写入 STATE，附排查建议"""
    cfg = load_config()
    try:
        r = requests.get(f"{BASE_URL}/models",
                         headers={"Authorization": f"Bearer {cfg['api_key']}"}, timeout=5)
        if r.status_code == 200:
            STATE["service"], STATE["service_detail"] = "ok", "服务在线，API Key 有效"
        elif r.status_code == 401:
            STATE["service"] = "bad_key"
            STATE["service_detail"] = "Key 无效（config.json 的 api_key 与服务端不一致）→ 建议 [5] 重新生成 Key，或关闭旧服务实例后重开"
        else:
            STATE["service"] = "error"
            STATE["service_detail"] = f"HTTP {r.status_code} → 查看服务端日志"
    except requests.ConnectionError:
        STATE["service"] = "down"
        STATE["service_detail"] = f"无法连接 {BASE_URL} → 服务未启动或端口 {PORT} 被占用 (netstat -ano | findstr {PORT})"
    except requests.Timeout:
        STATE["service"], STATE["service_detail"] = "error", "连接超时 → 检查防火墙/本机回环设置"
    return STATE["service"]


# ---------- 登录状态检测 ----------

def check_session(quiet=True):
    """返回 'valid' / 'expired' / 'unconfigured'，可选自动截取 CDP 浏览器中的新 cookie"""
    cfg = load_config()
    ck = cfg.get("cookies", {})
    if not (ck.get("easy_session") and ck.get("cookie_vjuid_login")):
        return "unconfigured"

    # 有调试浏览器在线时，先尝试截取其会话中的最新 cookie（已登录则自动安全存储）
    if cdp_alive(DEBUG_PORT):
        try:
            found = extract_needed(get_cookies_via_cdp(DEBUG_PORT))
            if len(found) == len(NEEDED) and any(found[k] != ck.get(k) for k in NEEDED):
                save_cookies(found)
                cfg = load_config()
                ck = cfg["cookies"]
        except Exception:
            pass

    return "valid" if verify_cookies(ck, quiet=quiet) else "expired"


def _kbhit(sec=0.1):
    """跨平台按键检测：返回 True 表示有按键被按下"""
    if IS_WINDOWS:
        import msvcrt
        return msvcrt.kbhit()
    else:
        import select
        return select.select([sys.stdin], [], [], sec) == ([sys.stdin], [], [])


def _getch():
    """跨平台读取单键"""
    if IS_WINDOWS:
        import msvcrt
        return msvcrt.getch()
    else:
        ch = sys.stdin.read(1)
        return ch.encode() if ch else b""


def login_flow():
    """未登录: 拉起浏览器到登录页，轮询截取登录后的身份信息；按任意键取消"""
    STATE["msg"] = "正在启动浏览器并等待登录（登录完成后自动截取，按任意键取消）..."
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
            STATE["session"] = "valid" if ok else "expired"
            STATE["msg"] = ("Cookie 已截取并保存 ✓ 会话有效"
                            if ok else "Cookie 已保存，但会话验证未通过，可能需要重新登录 SSO")
            return
        if _kbhit():
            _getch()
            STATE["msg"] = "已取消登录流程"
            return
        time.sleep(1.5)
    STATE["msg"] = "等待登录超时（5 分钟），可按 [4] 重试"


# ---------- 工具 ----------

def copy_to_clipboard(text) -> bool:
    try:
        if IS_WINDOWS:
            p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, shell=True)
            p.communicate(text.encode("gbk", "replace"))
        elif sys.platform == "darwin":
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
        else:  # Linux X11/Wayland
            for cmd in (["xclip", "-selection", "clipboard"], ["wl-copy"]):
                try:
                    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                    p.communicate(text.encode("utf-8"))
                    if p.returncode == 0:
                        break
                except FileNotFoundError:
                    continue
            else:
                return False
        return True
    except Exception:
        return False


def test_chat():
    cfg = load_config()
    try:
        r = requests.post(CHAT_ENDPOINT, timeout=120,
                          headers={"Authorization": f"Bearer {cfg['api_key']}"},
                          json={"model": MODEL_NAME,
                                "messages": [{"role": "user", "content": "回复两个字：正常"}]})
        if r.status_code == 200:
            data = r.json()
            content = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            STATE["msg"] = f"对话测试成功 ✓ 回复: {content[:30]}  (tokens: {usage.get('total_tokens', '?')})"
        elif r.status_code == 401:
            STATE["msg"] = "对话测试失败: 401 Key 无效 → [5] 重新生成后重试"
        else:
            STATE["msg"] = f"对话测试失败: HTTP {r.status_code} → {r.text[:100]}"
    except requests.RequestException as e:
        STATE["msg"] = f"对话测试失败: {e.__class__.__name__} → 服务是否在线? 按 [6] 检测"


# ---------- TUI 渲染 ----------

def dot(status):
    return {"ok": f"{GREEN}●{RESET}", "valid": f"{GREEN}●{RESET}",
            "down": f"{RED}●{RESET}", "bad_key": f"{RED}●{RESET}",
            "error": f"{RED}●{RESET}", "expired": f"{YELLOW}●{RESET}",
            "unconfigured": f"{YELLOW}●{RESET}", "checking": f"{YELLOW}●{RESET}",
            "unknown": f"{YELLOW}●{RESET}"}.get(status, f"{YELLOW}●{RESET}")


SESSION_TEXT = {"valid": "已登录（会话有效）", "expired": "会话已失效，请按 [4] 重新登录",
                "unconfigured": "未配置 Cookie，请按 [4] 登录", "unknown": "未检测（按 [0] 检测）"}


def render(cfg):
    _clear_screen()
    key = cfg["api_key"]
    s_detail = STATE["service_detail"]
    print(f"{BOLD}┌─ USTB chat2api 控制台 ──────────────────────────────────────┐{RESET}")
    print(f"│  服务状态: {dot(STATE['service'])} {'服务在线' if STATE['service'] in ('ok','bad_key') else '服务异常'}"
          f"    上游会话: {dot(STATE['session'])} {SESSION_TEXT.get(STATE['session'], '')}")
    print(f"│  {DIM}{s_detail}{RESET}" if s_detail else "│")
    print(f"├─ OpenAI 兼容接口 ───────────────────────────────────────────┤")
    print(f"│  端点: {CYAN}{CHAT_ENDPOINT}{RESET}")
    print(f"│  Base URL: {CYAN}{BASE_URL}{RESET}    模型: {CYAN}{MODEL_NAME}{RESET}")
    print(f"│  API Key: {CYAN}{key}{RESET}")
    print(f"│  Dashboard: {CYAN}http://127.0.0.1:{PORT}/dashboard{RESET}")
    print(f"├─ 操作（单键） ──────────────────────────────────────────────┤")
    print(f"│  {BOLD}[1]{RESET} 复制端点   {BOLD}[2]{RESET} 复制 Base URL   {BOLD}[3]{RESET} 复制 API Key")
    print(f"│  {BOLD}[4]{RESET} 登录/更新 Cookie   {BOLD}[5]{RESET} 重新生成 Key")
    print(f"│  {BOLD}[6]{RESET} 重新检测服务   {BOLD}[7]{RESET} 测试对话   {BOLD}[8]{RESET} 打开 Dashboard")
    print(f"│  {BOLD}[9]{RESET} 重启服务   {BOLD}[0]{RESET} 检测登录状态   {BOLD}[h]{RESET} 隐藏到托盘   {BOLD}[q]{RESET} 退出")
    print(f"└─────────────────────────────────────────────────────────────┘")
    if STATE["msg"]:
        color = GREEN if "✓" in STATE["msg"] else (RED if ("失败" in STATE["msg"] or "无效" in STATE["msg"]) else YELLOW)
        print(f"  {color}» {STATE['msg']}{RESET}")
    print(f"  {DIM}等待按键...{RESET}")


def handle_key(k, cfg):
    if k == b"1":
        STATE["msg"] = ("端点已复制到剪贴板 ✓" if copy_to_clipboard(CHAT_ENDPOINT)
                        else "复制失败，请手动复制")
    elif k == b"2":
        STATE["msg"] = ("Base URL 已复制到剪贴板 ✓" if copy_to_clipboard(BASE_URL)
                        else "复制失败，请手动复制")
    elif k == b"3":
        STATE["msg"] = ("API Key 已复制到剪贴板 ✓" if copy_to_clipboard(cfg["api_key"])
                        else "复制失败，请手动复制")
    elif k == b"4":
        login_flow()
    elif k == b"5":
        cfg["api_key"] = "sk-" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
        save_config(cfg)
        validate_service()
        STATE["msg"] = f"已生成新 API Key 并立即生效 ✓（旧 Key 失效）"
    elif k == b"6":
        validate_service()
        STATE["msg"] = "已重新检测服务与密钥"
    elif k == b"7":
        STATE["msg"] = "测试对话发送中（上游思考约需数秒~数十秒）..."
        render(cfg)
        test_chat()
    elif k == b"8":
        if not service_online():
            STATE["msg"] = f"服务未运行，Dashboard 无法打开（端口 {PORT} 无响应）"
        else:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{PORT}/dashboard")
            STATE["msg"] = "已在浏览器中打开 Dashboard"
    elif k == b"9":
        STATE["msg"] = "正在重启服务（整进程替换，加载最新代码）..."
        render(load_config())
        _exec_self_tui()
    elif k == b"0":
        STATE["session"] = check_session()
        STATE["msg"] = f"登录状态: {SESSION_TEXT.get(STATE['session'], '未知')}"


def _clear_screen():
    """跨平台清屏"""
    os.system("cls" if IS_WINDOWS else "clear")


def main():
    os.system("")  # 启用 Windows 终端 ANSI 转义
    cfg = ensure_default_key()
    chat2api.RESTART_CALLBACK = _api_restart_callback  # /restart 端点 -> 整进程重启

    # 启动服务: 端口空闲则内嵌启动，否则复用已有实例
    if service_online():
        STATE["msg"] = f"检测到 {PORT} 端口已有服务实例，直接复用"
    else:
        start_server()
        for _ in range(20):
            if service_online():
                break
            time.sleep(0.3)

    # 启动自检: 服务连通性 + Key 有效性
    validate_service()
    # 启动自检: 登录状态（已登录则自动安全截取存储 CDP 浏览器中的最新 cookie）
    STATE["session"] = check_session()

    while True:
        cfg = load_config()
        render(cfg)
        k = _getch()
        if k in (b"q", b"Q", b"\x03"):
            _clear_screen()
            print("chat2api 已退出（服务已停止）")
            return
        if k in (b"h", b"H"):
            # 隐藏 TUI: 转交托盘常驻（托盘会在服务掉线时自动拉起）
            if getattr(sys, "frozen", False):  # 单 EXE: 无参数启动即托盘
                subprocess.Popen([sys.executable], cwd=BASE_DIR)
            elif IS_WINDOWS:
                pythonw = sys.executable.replace("python.exe", "pythonw.exe")
                subprocess.Popen([pythonw, os.path.join(BASE_DIR, "tray.py")], cwd=BASE_DIR)
            else:
                subprocess.Popen([sys.executable, os.path.join(BASE_DIR, "tray.py")], cwd=BASE_DIR)
            _clear_screen()
            print("TUI 已隐藏，服务转由系统托盘常驻管理（任务栏右下角图标，左键点击图标可重新打开本控制台）。")
            time.sleep(1)
            return
        handle_key(k, cfg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nchat2api 已退出")
