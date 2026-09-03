# -*- coding: utf-8 -*-
"""上游模型上下文长度探测脚本

通过本代理向上游发送逐步增大的合成对话，定位上游可接受的最大上下文。
非流式 + include_usage，可拿到上游真实返回的 prompt_tokens。

运行:
  python tests/test_context_length.py            # 标准探测(粗台阶+二分细化)
  python tests/test_context_length.py --quick    # 快速探测(3 个台阶, 不细化)

说明:
- Trae"压缩上下文"时的 destination-addr loopback 400 是 Trae SOLO 云端沙箱限制，
  与上游上下文上限无关（请求未到达本服务）；本脚本绕过 Trae 直接测上游真实容量。
- 结果写入 tests/context_len_result.txt
"""
import json
import os
import sys
import time

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:8787"
KEY = "sk-local"
try:
    KEY = json.load(open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8")).get("api_key", KEY)
except Exception:
    pass
H = {"Authorization": f"Bearer {KEY}"}
OUT = []
TIMEOUT = 300

# 合成对话素材：中英混合，贴近真实 token 密度
_FILLER = ("这是一段用于上下文长度测试的背景资料，包含中文与English mixed content。"
           "系统需要在长上下文场景下保持稳定，The quick brown fox jumps over the lazy dog. ")


def build_messages(target_chars: int):
    """构造符合上游交替约束的合成历史：user/assistant 严格交替，最后一条 user"""
    msgs = []
    total = 0
    i = 0
    while total < target_chars - 200:
        chunk = _FILLER * 8  # 每轮约 900 字符
        i += 1
        msgs.append({"role": "user", "content": f"[背景资料 {i}] {chunk}"})
        msgs.append({"role": "assistant", "content": f"收到，背景资料 {i} 已记录。"})
        total += len(chunk) + 40
    msgs.append({"role": "user",
                 "content": f"以上共 {i} 段背景资料。请只回答两个数字：你收到的总字数约等于多少千字？直接输出数字。"})
    return msgs


def probe(target_chars: int):
    msgs = build_messages(target_chars)
    actual_chars = sum(len(m["content"]) for m in msgs)
    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/v1/chat/completions", headers=H, timeout=TIMEOUT, json={
            "model": "deepseek-r1", "stream": False,
            "stream_options": {"include_usage": True},
            "messages": msgs})
        dur = time.time() - t0
        if r.status_code == 200:
            d = r.json()
            usage = d.get("usage") or {}
            pt = usage.get("prompt_tokens")
            ct = usage.get("completion_tokens")
            rep = (f"OK   chars={actual_chars:>8}  usage.prompt_tokens={pt}  "
                   f"completion={ct}  dur={dur:.0f}s")
            return True, actual_chars, pt, rep
        rep = (f"FAIL chars={actual_chars:>8}  HTTP {r.status_code}  "
               f"body={r.text[:120]!r}  dur={dur:.0f}s")
        return False, actual_chars, None, rep
    except Exception as e:
        return False, actual_chars, None, f"FAIL chars={actual_chars:>8}  异常 {e.__class__.__name__}: {str(e)[:120]}"


def main():
    quick = "--quick" in sys.argv
    ladder = [8000, 32000, 64000] if quick else [4000, 8000, 16000, 32000, 64000, 128000, 256000]
    OUT.append(f"== 上游上下文长度探测 ({'quick' if quick else '标准'}) ==")
    last_ok, first_fail = None, None
    for chars in ladder:
        ok, actual, pt, rep = probe(chars)
        OUT.append(rep)
        print(rep)
        if ok:
            last_ok = (actual, pt)
        else:
            first_fail = actual
            break
        time.sleep(2)  # 温和探测，避免触发上游频控

    # 二分细化（最多 4 次）
    if not quick and last_ok and first_fail:
        lo, hi = last_ok[0], first_fail
        for _ in range(4):
            if hi - lo < 4000:
                break
            mid = (lo + hi) // 2
            ok, actual, pt, rep = probe(mid)
            OUT.append(f"  细化: {rep}")
            print("  细化:", rep)
            if ok:
                lo, last_ok = actual, (actual, pt)
            else:
                hi = actual
            time.sleep(2)

    if last_ok:
        chars, pt = last_ok
        OUT.append(f"\n结论: 上游可接受 ≥ {chars} 字符 (约 {chars} / 2.5 ≈ {chars // 2500}k tokens"
                   + (f"，上游实测 prompt_tokens={pt}" if pt else "") + ")")
        OUT.append(f"        失败阈值: {first_fail if first_fail else '未触及(已达探测上限)'}")
        OUT.append("建议: Trae 等 Agent 的单会话历史保持在上述字符数以内，超出前手动新开会话。")
    else:
        OUT.append("\n结论: 所有探针均失败——检查服务在线、Key、上游会话（先跑 python tests/run_all.py）")
    result = "\n".join(OUT)
    with open(os.path.join(BASE_DIR, "tests", "context_len_result.txt"), "w", encoding="utf-8") as f:
        f.write(result)
    print("\ndone → tests/context_len_result.txt")


if __name__ == "__main__":
    main()
