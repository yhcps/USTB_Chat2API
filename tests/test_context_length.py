# -*- coding: utf-8 -*-
"""上游模型上下文长度 / 输出窗口探测脚本

通过本代理向上游发送逐步增大的合成对话，定位上游可接受的最大上下文；
或用 prompt 驱动长生成，探测上游实际允许的最大输出 tokens。
非流式 + include_usage，可拿到上游真实返回的 prompt_tokens。

运行:
  python tests/test_context_length.py            # 上下文标准探测(粗台阶+二分细化)
  python tests/test_context_length.py --quick    # 上下文快速探测(3 个台阶, 不细化)
  python tests/test_context_length.py --start-chars 256000   # 跳过低台阶(直接测大上下文)
  python tests/test_context_length.py --output-tokens 131072 --max-minutes 30  # 输出窗口探测

说明:
- Trae"压缩上下文"时的 destination-addr loopback 400 是 Trae SOLO 云端沙箱限制，
  与上游上下文上限无关（请求未到达本服务）；本脚本绕过 Trae 直接测上游真实容量。
- 上游为 Web 表单接口，无 max_tokens 字段，输出窗口靠 prompt 驱动持续生成实测。
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


def output_probe(target_tokens: int, max_minutes: float):
    """输出窗口探测：prompt 驱动模型持续生成，流式统计实测输出规模。
    停止条件（先到者）：usage 报告的 completion_tokens >= target / 超时 / 流自然结束。
    1M 字符级上下文 ≠ 输出窗口；本探测回答"模型一次最多能吐多少"。"""
    global OUT
    OUT.append(f"\n== 输出窗口探测: 目标 {target_tokens} tokens, 时限 {max_minutes:g} 分钟 ==")
    print(OUT[-1])
    prompt = ("请从 1 开始每行输出一个连续整数：1、2、3……一直数到 999999。"
              "除数字外不要输出任何解释、标题、空行或省略号，不要中途停止，坚持输出到底。")
    t0 = time.time()
    limit_t = t0 + max_minutes * 60
    got_pt = got_ct = None
    out_chars = 0
    stopped = "?"
    try:
        r = requests.post(f"{BASE}/v1/chat/completions", headers=H, stream=True,
                          timeout=(15, 120),
                          json={"model": "deepseek-r1", "stream": True,
                                "stream_options": {"include_usage": True},
                                "messages": [{"role": "user", "content": prompt}]})
        if r.status_code != 200:
            rep = f"FAIL HTTP {r.status_code}  body={r.text[:150]!r}"
            OUT.append(rep)
            print(rep)
            return
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                stopped = "上游自然结束([DONE])"
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            u = obj.get("usage")
            if u:
                got_pt, got_ct = u.get("prompt_tokens"), u.get("completion_tokens")
            for ch in obj.get("choices", []):
                d = (ch or {}).get("delta") or {}
                out_chars += len(d.get("content") or "") + len(d.get("reasoning_content") or "")
            now = time.time()
            if now > limit_t:
                stopped = f"达到时限 {max_minutes:g} 分钟（主动断开）"
                r.close()
                break
            if got_ct is not None and got_ct >= target_tokens:
                stopped = f"达到目标 {target_tokens} tokens"
                r.close()
                break
    except Exception as e:
        stopped = f"异常 {e.__class__.__name__}: {str(e)[:120]}"
    dur = time.time() - t0
    est_tokens = got_ct if got_ct is not None else int(out_chars / 2.5)
    rep = (f"实测输出 ≈ {est_tokens} tokens ({out_chars} 字符)  "
           f"耗时 {dur:.0f}s  平均 {est_tokens / max(dur, 1):.1f} tok/s\n"
           f"停止原因: {stopped}" + (f"\n上游 usage: prompt={got_pt} completion={got_ct}" if got_ct else
                                     "\n(usage 未拿到，tokens 为字符估算)"))
    verdict = (f"\n结论: 输出窗口实测可达 ≈ {est_tokens} tokens"
               + ("（已达探测目标，可再调大 --output-tokens 继续向上探）"
                  if est_tokens >= target_tokens else
                  f"（未达目标 {target_tokens}——{stopped}；即为当前实测上限或需更长时限）"))
    OUT += [rep, verdict]
    print(rep)
    print(verdict)


def main():
    # 输出窗口探测模式
    if "--output-tokens" in sys.argv:
        target = int(sys.argv[sys.argv.index("--output-tokens") + 1])
        max_minutes = 30.0
        if "--max-minutes" in sys.argv:
            max_minutes = float(sys.argv[sys.argv.index("--max-minutes") + 1])
        OUT.append(f"== 上游输出窗口探测 ==")
        output_probe(target, max_minutes)
        with open(os.path.join(BASE_DIR, "tests", "output_len_result.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(OUT))
        print("\ndone → tests/output_len_result.txt")
        return

    quick = "--quick" in sys.argv
    # 默认台阶到 1M 字符（≈400k tokens，覆盖主流长上下文场景）
    ladder = [8000, 32000, 64000] if quick else \
        [4000, 8000, 16000, 32000, 64000, 128000, 256000, 512000, 1048576]
    if "--start-chars" in sys.argv:  # 跳过低台阶（直接测大上下文时提速）
        start = int(sys.argv[sys.argv.index("--start-chars") + 1])
        ladder = [c for c in ladder if c >= start] or [start]
    max_chars = None
    if "--max-chars" in sys.argv:  # 自定义探测上限
        max_chars = int(sys.argv[sys.argv.index("--max-chars") + 1])
        ladder = [c for c in ladder if c <= max_chars] + \
                 ([max_chars] if max_chars not in ladder else [])
        ladder = sorted(set(ladder))
    OUT.append(f"== 上游上下文长度探测 ({'quick' if quick else '标准'}) ==")
    last_ok, first_fail = None, None
    for chars in ladder:
        ok, actual, pt, rep = probe(chars)
        OUT.append(rep)
        print(rep)
        if ok:
            last_ok = (actual, pt)
            time.sleep(2)  # 温和探测，避免触发上游频控
            continue
        first_fail = first_fail or actual
        if last_ok is None and chars > 16000:
            # --start-chars 跳台阶后首探即失败：1/4 回退定位通过点，再进入二分
            rp = chars // 4
            while rp >= 8000:
                ok2, actual2, pt2, rep2 = probe(rp)
                OUT.append(f"  回退: {rep2}")
                print("  回退:", rep2)
                if ok2:
                    last_ok = (actual2, pt2)
                    break
                rp //= 4
                time.sleep(2)
        break  # 已触及失败点（单调性假设），交给二分细化

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
