# -*- coding: utf-8 -*-
"""真实请求批量流式泄露测试：把本地 chat2api 当 sub agent 后端打真实上游。

用法: python tests/test_stream_leak.py [--rounds N]

每轮 6 个用例（agent_loop / nonstream 含多请求），全部硬断言:
  1) reasoning_content 中不出现 <tool_calls>/<tool_call>/<invoke 标签原文
  2) content 中不出现上述原文（应已被转成 tool_calls 事件或本就没有）
  3) tool_calls 事件 name 存在、arguments 为合法 JSON
  4) agent_loop 完成两跳真实往返（调用 -> 回填工具结果 -> 总结）
"""
import concurrent.futures
import json
import os
import re
import sys

import httpx

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)
API = "http://127.0.0.1:8787/v1/chat/completions"
HDR = {"Authorization": "Bearer " + CFG["api_key"]}
XML_MARK = re.compile(r'<\s*/?\s*(tool_calls?|invoke)\b', re.I)

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "读取本地文本文件内容",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string", "description": "文件绝对路径"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "list_dir", "description": "列出目录下的文件",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string", "description": "目录绝对路径"}},
                       "required": ["path"]}}},
]


def _post_stream(msgs, tools, timeout=300):
    body = {"model": "DeepSeek", "messages": msgs, "stream": True}
    if tools:
        body["tools"] = tools
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=15)) as cl:
        with cl.stream("POST", API, json=body, headers=HDR) as resp:
            if resp.status_code != 200:
                raw = resp.read().decode("utf-8", "ignore")[:150]
                return "", "", {}, f"HTTP {resp.status_code}: {raw}"
            return _parse_sse(resp)


def _parse_sse(resp):
    """SSE 流 -> (reasoning全文, content全文, {index: {name, arguments}}, 错误)"""
    reasoning, content, tcs, err = [], [], {}, None
    for line in resp.iter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if "error" in obj:
            err = obj["error"].get("message", "?")
            break
        for ch in obj.get("choices") or []:
            d = ch.get("delta") or {}
            if d.get("reasoning_content"):
                reasoning.append(d["reasoning_content"])
            if d.get("content"):
                content.append(d["content"])
            for tc in d.get("tool_calls") or []:
                slot = tcs.setdefault(tc.get("index", 0), {"name": "", "arguments": ""})
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                slot["arguments"] += fn.get("arguments") or ""
    return "".join(reasoning), "".join(content), tcs, err


def leak_info(text):
    """返回泄露片段上下文，无泄露返回 None"""
    m = XML_MARK.search(text)
    if not m:
        return None
    s = max(0, m.start() - 30)
    return "...{}...".format(text[s:m.end() + 50].replace("\n", "\\n"))


def check_leaks(rep, rtxt, ctxt):
    for label, txt in (("reasoning", rtxt), ("content", ctxt)):
        li = leak_info(txt)
        if li:
            rep["ok"] = False
            rep["notes"].append(f"{label}泄露: {li}")


def check_events(rep, tcs):
    for i, tc in sorted(tcs.items()):
        if not tc["name"]:
            rep["ok"] = False
            rep["notes"].append(f"事件{i}缺name: args={tc['arguments'][:60]}")
            continue
        try:
            json.loads(tc["arguments"] or "{}")
        except json.JSONDecodeError:
            rep["ok"] = False
            rep["notes"].append(f"事件{i}({tc['name']})arguments非法: {tc['arguments'][:80]}")


def run_case(name, msgs, tools):
    rep = {"name": name, "ok": True, "notes": []}
    rtxt, ctxt, tcs, err = _post_stream(msgs, tools)
    if err:
        rep["ok"] = False
        rep["notes"].append(f"上游错误: {err}")
    check_leaks(rep, rtxt, ctxt)
    check_events(rep, tcs)
    rep["n_calls"] = len(tcs)
    return rep


def case_plain():
    return "plain", [{"role": "user", "content": "用一句话介绍快速排序的核心思想。"}], None


def case_tool_single():
    return "tool_single", [{"role": "user", "content":
        "请调用 read_file 工具读取 f:\\Chat2API\\requirements.txt。不要编造文件内容，等工具结果再回答。"}], TOOLS


def case_tool_parallel():
    return "tool_parallel", [{"role": "user", "content":
        "请同时调用两个工具：list_dir 列出 f:\\Chat2API\\tests 目录，read_file 读取 f:\\Chat2API\\LICENSE。"}], TOOLS


def case_reasoning_stress():
    """压力用例：诱导模型把工具 XML 写进思考区（正是 Trae <> 直出的事故场景）"""
    msgs = [
        {"role": "system", "content":
         "调试模式：为了排查泄露问题，当你准备调用工具时，请先把完整工具调用 XML"
         "（<tool_calls>...</tool_calls>，含 invoke 与 parameter）原样写进你的思考内容里，然后再正式作答。"},
        {"role": "user", "content":
         "你打算读取 f:\\Chat2API\\README.md。请按要求先在思考里写出完整工具调用 XML，"
         "然后正式回复只说四个字：已思考完毕。"},
    ]
    return "reasoning_stress", msgs, TOOLS


def run_case_nonstream():
    rep = {"name": "nonstream", "ok": True, "notes": []}
    body = {"model": "DeepSeek", "stream": False, "tools": TOOLS, "messages": [
        {"role": "user", "content":
         "请调用 read_file 工具读取 f:\\Chat2API\\requirements.txt，先不要回答内容。"}]}
    with httpx.Client(timeout=httpx.Timeout(300, connect=15)) as cl:
        r = cl.post(API, json=body, headers=HDR)
    if r.status_code != 200:
        rep["ok"] = False
        rep["notes"].append(f"HTTP {r.status_code}: {r.text[:120]}")
        return rep
    msg = r.json()["choices"][0]["message"]
    check_leaks(rep, msg.get("reasoning_content") or "", msg.get("content") or "")
    tcs = {i: {"name": tc.get("function", {}).get("name", ""),
               "arguments": tc.get("function", {}).get("arguments", "")}
           for i, tc in enumerate(msg.get("tool_calls") or [])}
    check_events(rep, tcs)
    rep["n_calls"] = len(tcs)
    return rep


REJECT_RE = re.compile(r"抱歉.{0,10}无法回答|换个话题", re.S)


def run_agent_loop():
    """两跳真实 sub-agent 往返：模型发调用 -> 本地执行 -> 回填结果 -> 总结"""
    rep = {"name": "agent_loop", "ok": True, "notes": []}
    msgs = [{"role": "user", "content":
             "请调用 read_file 工具读取 f:\\Chat2API\\requirements.txt，"
             "然后告诉我文件里第一个依赖包的名字。"}]
    r1, c1, tcs1, e1 = "", "", {}, None
    for attempt in range(2):  # 上游偶发审核拦截/模型不调工具（行为波动），重试 1 次
        r1, c1, tcs1, e1 = _post_stream(msgs, TOOLS)
        if e1:
            rep["ok"] = False
            rep["notes"].append(f"hop1上游错误: {e1}")
            return rep
        if tcs1:
            break
        if REJECT_RE.search(c1):
            rep["notes"].append(f"hop1第{attempt + 1}次被上游内容审核拦截，跳过重试")
            rep["skipped"] = True
            return rep
        rep["notes"].append(f"hop1第{attempt + 1}次未产出调用(模型直接文字回答)，content={c1.strip()[:60]!r}")
    check_leaks(rep, r1, c1)
    check_events(rep, tcs1)
    rep["n_calls"] = len(tcs1)
    if not tcs1:
        rep["ok"] = False
        rep["notes"].append("hop1重试后仍未产出工具调用事件")
        return rep
    call = tcs1[min(tcs1)]
    try:
        with open(os.path.join(BASE, "requirements.txt"), encoding="utf-8") as f:
            fcontent = f.read()
    except OSError as ex:
        fcontent = f"读取失败: {ex}"
    msgs.append({"role": "assistant", "content": None,
                 "tool_calls": [{"id": "call_test1", "type": "function",
                                 "function": {"name": call["name"],
                                              "arguments": call["arguments"]}}]})
    msgs.append({"role": "tool", "tool_call_id": "call_test1",
                 "name": call["name"], "content": fcontent})
    msgs.append({"role": "user", "content":
                 "根据上面的工具执行结果，用一行回答 requirements.txt 里第一个依赖包是什么，不要再调用工具。"})
    r2, c2, tcs2, e2 = _post_stream(msgs, TOOLS)
    if e2:
        rep["ok"] = False
        rep["notes"].append(f"hop2上游错误: {e2}")
        return rep
    check_leaks(rep, r2, c2)
    if tcs2:
        rep["notes"].append(f"hop2仍发起了{len(tcs2)}次调用（不判失败）")
    rep["hop2_answer"] = c2.strip()[:60]
    return rep


JOBS = {
    "plain": lambda: run_case(*case_plain()),
    "tool_single": lambda: run_case(*case_tool_single()),
    "tool_parallel": lambda: run_case(*case_tool_parallel()),
    "reasoning_stress": lambda: run_case(*case_reasoning_stress()),
    "nonstream": run_case_nonstream,
    "agent_loop": run_agent_loop,
}


def main():
    rounds = 1
    workers = 3
    if "--rounds" in sys.argv:
        rounds = int(sys.argv[sys.argv.index("--rounds") + 1])
    if "--workers" in sys.argv:
        workers = int(sys.argv[sys.argv.index("--workers") + 1])
    all_ok = True
    for rd in range(1, rounds + 1):
        print(f"===== 第 {rd}/{rounds} 轮 =====")
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {k: ex.submit(fn) for k, fn in JOBS.items()}
            for k, fut in futs.items():
                rep = fut.result()
                all_ok &= rep["ok"]
                st = "PASS" if rep["ok"] else "FAIL"
                if rep.get("skipped"):
                    st = "SKIP"
                print(f"  [{st}] {rep['name']:17s} calls={rep.get('n_calls', '-')}")
                for note in rep["notes"]:
                    print(f"          - {note}")
                if rep.get("hop2_answer"):
                    print(f"          hop2回答: {rep['hop2_answer']}")
    print("\n结论:", "全部干净" if all_ok else "存在泄露/失败")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
