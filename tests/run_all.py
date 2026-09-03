# -*- coding: utf-8 -*-
"""全链路回归测试（合并原 test_conn / test_client_style / test_agent_loop）

运行: python tests/run_all.py [--skip-agent]
输出写入 tests/run_all_out.txt（避免 GBK 控制台编码问题）
"""
import json
import os
import sys

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

BASE = f"http://127.0.0.1:8787"
KEY = "sk-local"
try:
    KEY = json.load(open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8")).get("api_key", KEY)
except Exception:
    pass
H = {"Authorization": f"Bearer {KEY}"}
OUT = []


def log(s):
    OUT.append(s)


TOOLS = [
    {"type": "function", "function": {
        "name": "List", "description": "列出指定目录下的文件和子目录",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "目录绝对路径"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "read", "description": "读取文件内容",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件绝对路径"}}, "required": ["path"]}}},
]


def part1_connectivity():
    log("== Part1 基础连通性 ==")
    r = requests.get(f"{BASE}/v1/models",
                     headers={"Authorization": "Bearer sk-localsk-local"}, timeout=5)
    log(f"错误密钥: {r.status_code} {r.text.strip()[:40]} (期望401)")
    r = requests.get(f"{BASE}/v1/models", headers=H, timeout=5)
    ids = [m["id"] for m in r.json()["data"]]
    log(f"模型列表: {r.status_code} {ids}")
    r = requests.post(f"{BASE}/v1/chat/completions", headers=H, timeout=120,
                      json={"model": "deepseek-r1",
                            "messages": [{"role": "user", "content": "1+1等于几？只回答数字"}]})
    c = json.loads(r.text)["choices"][0]["message"]["content"].strip()
    log(f"非流式对话: {r.status_code} 回复={c[:20]!r}")
    chunks = []
    with requests.Session() as s:
        with s.post(f"{BASE}/v1/chat/completions", headers=H, timeout=120, stream=True,
                    json={"model": "deepseek-chat", "stream": True,
                          "messages": [{"role": "user", "content": "回复两个字：正常"}]}) as resp:
            raw = list(resp.iter_lines(decode_unicode=True))
            for line in raw:
                if line.startswith("data: ") and line != "data: [DONE]":
                    d = json.loads(line[6:]).get("choices", [{}])[0].get("delta", {})
                    if d.get("content"):
                        chunks.append(d["content"])
    done_ok = any(l.strip() == "data: [DONE]" for l in raw)
    log(f"流式对话: {resp.status_code} 回复={''.join(chunks).strip()[:20]!r} [DONE]帧完整={done_ok}")


def part2_client_style():
    log("== Part2 客户端风格 payload ==")
    cases = [
        ("A system+user", {"model": "deepseek-r1", "messages": [
            {"role": "system", "content": "你是简洁助手"},
            {"role": "user", "content": "1+2等于几？只回答数字"}]}),
        ("B content分片", {"model": "deepseek-r1", "messages": [
            {"role": "user", "content": [{"type": "text", "text": "2+2等于几？只回答数字"}]}]}),
        ("C stream_options=null", {"model": "deepseek-r1", "stream_options": None,
            "temperature": 0.7, "max_tokens": 2048,
            "messages": [{"role": "user", "content": "3+1等于几？只回答数字"}]}),
        ("D 连续user(4028修复)", {"model": "deepseek-r1", "messages": [
            {"role": "user", "content": "你好"},
            {"role": "user", "content": "4+1等于几？只回答数字"}]}),
    ]
    for name, payload in cases:
        r = requests.post(f"{BASE}/v1/chat/completions", headers=H, timeout=120, json=payload)
        c = json.loads(r.text)["choices"][0]["message"]["content"].strip() if r.status_code == 200 else r.text[:50]
        log(f"{name}: {r.status_code} {c[:24]!r}")


def part3_agent_loop():
    log("== Part3 Agent 工具循环 ==")
    r1 = requests.post(f"{BASE}/v1/chat/completions", headers=H, timeout=180, json={
        "model": "deepseek-r1", "tools": TOOLS,
        "messages": [{"role": "user", "content": "帮我看看 f:\\Chat2API 这个文件夹里有哪些文件"}]})
    d1 = r1.json()
    tcs = d1["choices"][0]["message"].get("tool_calls") or []
    log(f"首轮: {r1.status_code} finish={d1['choices'][0]['finish_reason']} "
        f"calls={[(t['function']['name'], t['function']['arguments']) for t in tcs]}")
    if not tcs:
        log("!! 未产生工具调用（可能被上游审核拦截），跳过续跑")
        return
    followup = [
        {"role": "user", "content": "帮我看看 f:\\Chat2API 这个文件夹里有哪些文件"},
        {"role": "assistant", "content": None, "tool_calls": tcs},
        {"role": "tool", "tool_call_id": tcs[0]["id"], "name": "List",
         "content": "chat2api.py\ntray.py\ntui.py\ncli.py\ndashboard.py"},
        {"role": "user", "content": "文件列表已经给你了，用一句话总结有哪些 py 文件"},
    ]
    r2 = requests.post(f"{BASE}/v1/chat/completions", headers=H, timeout=180,
                       json={"model": "deepseek-r1", "tools": TOOLS, "messages": followup})
    d2 = r2.json()
    log(f"结果回传承跑: {r2.status_code} 回复={(d2['choices'][0]['message'].get('content') or '')[:60]!r}")


if __name__ == "__main__":
    part1_connectivity()
    part2_client_style()
    if "--skip-agent" not in sys.argv:
        part3_agent_loop()
    r = requests.get(f"{BASE}/stats", timeout=5)
    log(f"== /stats 可用: {r.status_code}, 累计请求={r.json().get('total_requests')} ==")
    with open(os.path.join(BASE_DIR, "tests", "run_all_out.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("done")
