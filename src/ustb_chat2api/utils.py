"""
USTB Chat2API - 共享工具函数
"""
import json
import os
import re
import time
import hashlib
import secrets
import string
import sys
import traceback
from datetime import datetime

# ===== 路径常量 =====
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
KEYS_FILE = os.path.join(CONFIG_DIR, "api_keys.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")
ERROR_LOG = os.path.join(LOG_DIR, "server_error.log")
DEBUG_LOG = os.path.join(LOG_DIR, "server_debug.log")
TRAY_LOG = os.path.join(LOG_DIR, "tray.log")
WARN_LOG = os.path.join(LOG_DIR, "server_warn.log")

# 确保目录存在
for d in [CONFIG_DIR, LOG_DIR]:
    os.makedirs(d, exist_ok=True)

# ===== 日志 =====
def log_error(context: str):
    """异常落盘（pythonw 下 stderr 不可用，靠此文件排查）"""
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {context}\n")
            f.write(traceback.format_exc())
    except Exception:
        pass


def log_tray(msg: str):
    """托盘日志"""
    try:
        with open(TRAY_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def log_tools(calls: list):
    """记录转换出的 tool_calls（诊断 Trae 等客户端联调）"""
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%m-%d %H:%M:%S}] tool_calls -> "
                    f"{[(c['function']['name'], c['function']['arguments'][:80]) for c in calls]}\n")
    except Exception:
        pass


def log_warn(msg: str):
    """尖括号预警落盘（调试阶段专用，独立于 server_error.log）"""
    try:
        with open(WARN_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


# 尖括号预警判据（调试阶段）：命中已知泄露模式立即告警；否则按密度/总量阈值
_LEAK_PAT_RE = re.compile(r'</?\s*(?:tool_calls?|invoke|parameter|tocalls)\b|｜DSML｜', re.I)
_ANGLE_DENSITY = 0.08    # 尖括号占字符比阈值（正常代码/正文远低于此）
_ANGLE_MIN_CHARS = 200   # 参与密度判定的样本下限
_ANGLE_MAX_CNT = 200     # 单次响应累计尖括号数硬阈值


def angle_bracket_check(text: str, channel: str, state: dict):
    """下发给客户端的正文/思考流尖括号预警（调试阶段）。
    大量尖括号或已知泄露模式（<tool_calls/</invoke/｜DSML｜ 等）自动写 server_warn.log。
    state: 每次响应每通道一个 dict（累计计数）；每通道每次响应最多告警 1 次防刷屏。"""
    if not text:
        return
    state["cnt"] = state.get("cnt", 0) + text.count("<") + text.count(">")
    state["chars"] = state.get("chars", 0) + len(text)
    m = _LEAK_PAT_RE.search(text)
    dense = (state["chars"] >= _ANGLE_MIN_CHARS
             and state["cnt"] / state["chars"] > _ANGLE_DENSITY)
    if (m or dense or state["cnt"] >= _ANGLE_MAX_CNT) and not state.get("warned"):
        state["warned"] = True
        sample = text[:120].replace("\n", "\\n")
        log_warn(f"尖括号预警 channel={channel} cnt={state['cnt']} chars={state['chars']} "
                 f"pattern={m.group(0) if m else '-'} sample={sample}")


# ===== 配置管理 =====

DEFAULT_CONFIG = {
    "api_key": "sk-local",
    "cookies": {"easy_session": "", "cookie_vjuid_login": ""},
}


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cfg = {}
    return {**DEFAULT_CONFIG, **cfg}


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_host() -> str:
    """监听地址: 默认仅本机; SOLO 等云端沙箱场景可设为 0.0.0.0 走局域网 IP 访问"""
    return load_config().get("host", "127.0.0.1")


# ===== API Key 管理 =====

KEY_PREFIX = "sk-ustb-"
KEY_ALPHABET = string.ascii_letters + string.digits


def _load_keys():
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, encoding="utf-8") as f:
                return json.load(f).get("keys", [])
        except (json.JSONDecodeError, FileNotFoundError):
            pass
    return []


def _save_keys(keys):
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump({"keys": keys}, f, ensure_ascii=False, indent=2)


def key_generate(name: str = None):
    keys = _load_keys()
    full_key = KEY_PREFIX + "".join(secrets.choice(KEY_ALPHABET) for _ in range(48))
    name = name or f"key-{int(time.time())}"
    keys.append({
        "name": name,
        "key_hash": hashlib.sha256(full_key.encode()).hexdigest(),
        "key_prefix": full_key[:16] + "...",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "disabled": False,
    })
    _save_keys(keys)
    return full_key, name


def key_list():
    return _load_keys()


def key_revoke(prefix: str):
    keys = _load_keys()
    prefix = prefix.rstrip(".")
    def matches(k):
        p = k["key_prefix"].rstrip(".")
        return p.startswith(prefix) or p[len(KEY_PREFIX):].startswith(prefix)
    hit = [k for k in keys if matches(k) and not k.get("disabled")]
    for k in hit:
        k["disabled"] = True
    _save_keys(keys)
    return hit


def verify_key(token: str) -> bool:
    """校验 Bearer key：config.json 的 api_key 或 api_keys.json 的哈希 key"""
    cfg = load_config()
    if cfg.get("api_key") and token == cfg["api_key"]:
        return True
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    keys = _load_keys()
    return any(k.get("key_hash") == token_hash and not k.get("disabled") for k in keys)


# ===== 内容工具 =====

def content_to_text(content):
    """兼容 OpenAI 内容分片格式: 字符串 或 [{"type":"text","text":...}, ...]"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text")
    return str(content)
