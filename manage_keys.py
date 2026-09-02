# -*- coding: utf-8 -*-
"""
OpenAI 兼容格式 API Key 管理脚本

生成的 key 格式: sk-ustb-<48位随机字母数字>（与 OpenAI `sk-...` 格式兼容，
任何接受 Bearer key 的客户端均可直接使用）

用法:
  python manage_keys.py generate [名称]    # 生成新 key（仅此一次显示完整 key）
  python manage_keys.py list               # 列出所有 key（只显示前缀）
  python manage_keys.py revoke <前缀>      # 按 key 前缀吊销

key 以 SHA-256 哈希形式存放在同目录 api_keys.json，明文不落盘。
chat2api.py 会实时读取该文件，增删 key 无需重启服务。
"""
import hashlib
import json
import os
import secrets
import string
import sys
import time

KEYS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_keys.json")
PREFIX = "sk-ustb-"
ALPHABET = string.ascii_letters + string.digits  # 与 OpenAI key 相同的 base62 字符集


def load_keys():
    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE, encoding="utf-8") as f:
            return json.load(f).get("keys", [])
    return []


def save_keys(keys):
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump({"keys": keys}, f, ensure_ascii=False, indent=2)


def generate(name: str):
    keys = load_keys()
    full_key = PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(48))
    entry = {
        "name": name,
        "key_hash": hashlib.sha256(full_key.encode()).hexdigest(),
        "key_prefix": full_key[:16] + "...",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "disabled": False,
    }
    keys.append(entry)
    save_keys(keys)
    print(f"[OK] 已生成 API Key (名称: {name})")
    print(f"     {full_key}")
    print("     请立即保存，明文不会再次显示。")
    print("\n使用方式:")
    print(f'  curl http://127.0.0.1:8787/v1/models -H "Authorization: Bearer {full_key}"')


def list_keys():
    keys = load_keys()
    if not keys:
        print("暂无 key，先运行: python manage_keys.py generate [名称]")
        return
    print(f"共 {len(keys)} 个 key:")
    for i, k in enumerate(keys, 1):
        status = "已吊销" if k.get("disabled") else "有效"
        print(f"  {i}. {k['key_prefix']}  名称={k['name']}  创建={k['created']}  状态={status}")


def revoke(prefix: str):
    keys = load_keys()
    prefix = prefix.rstrip(".")

    def matches(k):
        p = k["key_prefix"].rstrip(".")
        return p.startswith(prefix) or p[len(PREFIX):].startswith(prefix)

    hit = [k for k in keys if matches(k) and not k.get("disabled")]
    if not hit:
        print(f"未找到前缀为 {prefix} 的有效 key")
        sys.exit(1)
    for k in hit:
        k["disabled"] = True
    save_keys(keys)
    for k in hit:
        print(f"[OK] 已吊销 {k['key_prefix']} (名称: {k['name']})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "generate":
        generate(sys.argv[2] if len(sys.argv) > 2 else f"key-{int(time.time())}")
    elif cmd == "list":
        list_keys()
    elif cmd == "revoke":
        if len(sys.argv) < 3:
            print("用法: python manage_keys.py revoke <key前缀>")
            sys.exit(1)
        revoke(sys.argv[2])
    else:
        print(__doc__)
