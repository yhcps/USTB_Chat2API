# -*- coding: utf-8 -*-
"""真实场景稳定性测试：把本地 chat2api 网关当 sub Agent，做文档/分支维护任务。

用法: python tests/test_subagent_stability.py [--rounds N]（默认 2）

每轮两个真实 agentic 任务（多跳工具往返）:
  任务A 文档维护: 查看 README.md 章节结构 -> 归纳文档维护清单
  任务B 分支维护: 查看当前分支与最近提交 -> 总结工作区状态
硬断言（每跳）:
  1) content/reasoning 无工具 XML 原文、无 ｜DSML｜ 腐蚀标签
  2) tool_calls 事件 name 存在、arguments 为合法 JSON
  3) 工具循环真实走通（调用 -> 本地执行 -> 回填 -> 总结），最多 5 跳
  4) 上游内容审核拦截按 SKIP 透传（不判失败）
联动验证（只报告不判失败）: 测试结束后统计 server_warn.log 新增的尖括号预警行数。
"""
import json
import os
import re
import subprocess
import sys

import httpx

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)
API = "http://127.0.0.1:8787/v1/chat/completions"
HDR = {"Authorization": "Bearer " + CFG["api_key"]}
# 泄露判据比 test_stream_leak 更宽：含 parameter 闭合与 ｜DSML｜ 腐蚀变体（H41）
LEAK_MARK = re.compile(r'</?\s*(?:tool_calls?|invoke|parameter|tocalls)\b|｜DSML｜', re.I)
REJECT_RE = re.compile(r"抱歉.{0,10}无法回答|换个话题", re.S)
WARN_LOG = os.path.join(BASE, "server_warn.log")

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "查看本地文本文件内容（可指定起始行与行数）",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string", "description": "文件绝对路径"},
                                      "offset": {"type": "integer", "description": "起始行号，从1开始"},
                                      "limit": {"type": "integer", "description": "读取行数，默认100"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "list_dir", "description": "列出目录下的文件与子目录",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string", "description": "目录绝对路径"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "view_branch", "description": "查看当前 git 分支、工作区改动概况与最近提交",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "view_diff", "description": "查看工作区未暂存改动的 diff 摘要（仅改动的文件列表与行数统计，不输出全文）",
        "parameters": {"type": "object", "properties": {"staged": {"type": "boolean", "description": "是否查看已暂存（staged）的改动，默认 false"}}},
        "required": []}},
    {"type": "function", "function": {
        "name": "view_file_log", "description": "查看指定文件的提交历史",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string", "description": "文件相对路径（相对工作区根目录），如 'chat2api.py'"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "view_commit", "description": "查看指定提交的详情（作者、日期、完整 diff 统计）",
        "parameters": {"type": "object",
                       "properties": {"ref": {"type": "string", "description": "提交引用，如 'HEAD~2' 或完整的 commit hash"}},
                       "required": ["ref"]}}},
]


# ---------- 上游请求（与 test_stream_leak 同款 SSE 解析，保留事件 id） ----------
def _post_stream(msgs, timeout=300):
    body = {"model": "DeepSeek", "messages": msgs, "stream": True, "tools": TOOLS}
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=15)) as cl:
        with cl.stream("POST", API, json=body, headers=HDR) as resp:
            if resp.status_code != 200:
                raw = resp.read().decode("utf-8", "ignore")[:150]
                return "", "", [], f"HTTP {resp.status_code}: {raw}"
            return _parse_sse(resp)


def _parse_sse(resp):
    """SSE 流 -> (reasoning全文, content全文, [{id,name,arguments}], 错误)"""
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
                slot = tcs.setdefault(tc.get("index", 0),
                                      {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                slot["arguments"] += fn.get("arguments") or ""
    return "".join(reasoning), "".join(content), [tcs[k] for k in sorted(tcs)], err


def leak_info(text):
    m = LEAK_MARK.search(text)
    if not m:
        return None
    s = max(0, m.start() - 30)
    return "...{}...".format(text[s:m.end() + 50].replace("\n", "\\n"))


# ---------- 本地工具实现（只读，不改动工作区） ----------
def _safe_path(p):
    p = os.path.abspath(p)
    return p if p.lower().startswith(BASE.lower()) else None


def tool_read_file(args):
    path = _safe_path(args.get("path", ""))
    if not path:
        return "错误: 仅允许查看工作区内的文件"
    if not os.path.isfile(path):
        return f"错误: 文件不存在 {path}"
    off = max(int(args.get("offset") or 1), 1)
    lim = min(int(args.get("limit") or 100), 400)
    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    seg = lines[off - 1: off - 1 + lim]
    head = f"[{path} 第{off}-{off + len(seg) - 1}行，共{len(lines)}行]\n"
    return head + "".join(seg)[:20000]


def tool_list_dir(args):
    path = _safe_path(args.get("path", ""))
    if not path or not os.path.isdir(path):
        return f"错误: 目录不存在 {args.get('path', '')}"
    entries = sorted(os.listdir(path))
    out = []
    for e in entries[:200]:
        full = os.path.join(path, e)
        out.append(("[D] " if os.path.isdir(full) else "[F] ") + e)
    return "\n".join(out)


def tool_view_branch(_args):
    def git(*a):
        r = subprocess.run(["git", "--no-pager", *a], cwd=BASE,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=30)
        return (r.stdout or r.stderr).strip()
    return ("== 分支状态 ==\n" + git("status", "--short", "--branch")
            + "\n== 最近提交 ==\n" + git("log", "--oneline", "-5"))


def tool_view_diff(args):
    def git(*a):
        r = subprocess.run(["git", "--no-pager", *a], cwd=BASE,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=30)
        return (r.stdout or r.stderr).strip()
    staged = args.get("staged", False)
    target = "--cached" if staged else ""
    diff = git("diff", "--stat", target)
    files = git("diff", "--name-only", target)
    return f"== 改动文件摘要 ==\n{diff or '（无改动）'}\n== 改动的文件列表 ==\n{files or '（无）'}"


def tool_view_file_log(args):
    def git(*a):
        r = subprocess.run(["git", "--no-pager", *a], cwd=BASE,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=30)
        return (r.stdout or r.stderr).strip()
    path = args.get("path", "")
    if not path:
        return "错误: 请提供文件路径"
    return git("log", "--oneline", "-10", "--", path) or "该文件无提交历史"


def tool_view_commit(args):
    def git(*a):
        r = subprocess.run(["git", "--no-pager", *a], cwd=BASE,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=30)
        return (r.stdout or r.stderr).strip()
    ref = args.get("ref", "HEAD")
    return (git("log", "--format=%H%n%an <%ae>%n%ai%n%s", ref, "-1")
            + "\n== 改动统计 ==\n" + git("diff-tree", "--no-commit-id", "-r", "--stat", ref))


TOOL_IMPL = {"read_file": tool_read_file, "list_dir": tool_list_dir,
             "view_branch": tool_view_branch,
             "view_diff": tool_view_diff, "view_file_log": tool_view_file_log,
             "view_commit": tool_view_commit}


# ---------- agentic 循环 ----------
# 上游动态审核随机拦截（同文本时拦时不拦）：hop1 被拦时换一种措辞重试一次
# （遵守「勿原样重发」约定），hop2+ 被拦则跳过该任务
def run_task(label, prompts, rep):
    """完整工具往返循环；全程断言泄露/事件合法性，结果写回 rep"""
    for attempt, prompt in enumerate(prompts):
        msgs = [{"role": "user", "content": prompt}]
        rejected = False
        for hop in range(1, 6):
            rtxt, ctxt, tcs, err = _post_stream(msgs)
            if err:
                rep["ok"] = False
                rep["notes"].append(f"{label} hop{hop} 上游错误: {err}")
                return "failed"
            if REJECT_RE.search(ctxt):
                rejected = True
                if hop == 1 and attempt < len(prompts) - 1:
                    rep["notes"].append(f"{label} hop1 被上游拦截，换措辞重试")
                    break  # 换下一种措辞重来
                rep["notes"].append(f"{label} hop{hop} 被上游拦截（上游随机行为），跳过该任务")
                return "rejected"
            for name, txt in (("reasoning", rtxt), ("content", ctxt)):
                li = leak_info(txt)
                if li:
                    rep["ok"] = False
                    rep["notes"].append(f"{label} hop{hop} {name}泄露: {li}")
            for i, tc in enumerate(tcs):
                if not tc["name"]:
                    rep["ok"] = False
                    rep["notes"].append(f"{label} hop{hop} 事件{i}缺name")
                try:
                    json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    rep["ok"] = False
                    rep["notes"].append(
                        f"{label} hop{hop} 事件{i}({tc['name']})arguments非法: {tc['arguments'][:80]}")
            rep["hops"].append({"task": label, "hop": hop, "n_calls": len(tcs),
                                "chars": len(ctxt)})
            if not tcs:
                if not ctxt.strip():
                    rep["ok"] = False
                    rep["notes"].append(f"{label} hop{hop} 无调用且无正文（空响应）")
                    return "failed"
                return "done"  # 最终总结
            # 本地执行并回填（含 assistant tool_calls 历史，保持桥格式一致性）
            tc_openai = [{"id": tc["id"] or f"call_t{hop}{i}", "type": "function",
                          "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                         for i, tc in enumerate(tcs)]
            msgs.append({"role": "assistant", "content": ctxt or None, "tool_calls": tc_openai})
            for i, tc in enumerate(tcs):
                impl = TOOL_IMPL.get(tc["name"])
                try:
                    result = impl(json.loads(tc["arguments"] or "{}")) if impl \
                        else f"错误: 未知工具 {tc['name']}"
                except Exception as ex:  # 工具执行异常也回填，让模型自行调整
                    result = f"工具执行异常: {ex.__class__.__name__}: {ex}"
                msgs.append({"role": "tool", "tool_call_id": tc_openai[i]["id"],
                             "name": tc["name"], "content": str(result)})
        if not rejected:
            break
    return "failed"


TASKS = [
    ("文档维护", [
        "请用 read_file 工具查看 " + BASE + "\\README.md 的前 100 行，"
        "说说这份文档的主要章节，并给出 3 条让结构更清晰的小建议。",
        "帮我看看 " + BASE + "\\README.md 开头写了什么：先调用 read_file 工具查看前 80 行，"
        "然后用几句话概括一下即可。"]),
    ("分支维护", [
        "请先调用 view_branch 查看当前分支与最近提交，然后调用 view_diff 了解工作区未暂存改动，"
        "最后用两句话总结工作区近况与主要改动方向。",
        "想了解一下代码库现状：先调用 view_branch 看看分支信息，再调用 view_diff 看看有哪些改动的文件，然后描述一下当前工作区状态。"]),
]


def run_round(n):
    rep = {"round": n, "ok": True, "skipped": False, "notes": [], "hops": [],
           "tasks": {}}
    for label, prompts in TASKS:
        outcome = run_task(label, prompts, rep)
        rep["tasks"][label] = outcome
        if outcome == "rejected":
            rep["skipped"] = True
        elif outcome != "done":
            rep["ok"] = False
    return rep


def warn_log_delta(before):
    try:
        with open(WARN_LOG, encoding="utf-8") as f:
            return sum(1 for _ in f) - before
    except OSError:
        return 0


def main():
    rounds = 2
    if "--rounds" in sys.argv:
        rounds = int(sys.argv[sys.argv.index("--rounds") + 1])
    try:
        with open(WARN_LOG, encoding="utf-8") as f:
            warn_before = sum(1 for _ in f)
    except OSError:
        warn_before = 0

    print(f"== 真实场景稳定性测试（{rounds} 轮 x 2 任务，打真实上游）==")
    task_done = task_rejected = task_failed = 0
    for n in range(1, rounds + 1):
        rep = run_round(n)
        for label, outcome in rep["tasks"].items():
            if outcome == "done":
                task_done += 1
            elif outcome == "rejected":
                task_rejected += 1
            else:
                task_failed += 1
        status = "PASS" if rep["ok"] else ("SKIP" if rep["skipped"] else "FAIL")
        print(f"  {status} 轮{n} 任务={rep['tasks']} "
              f"跳数={[(h['task'][:2], h['hop'], h['n_calls']) for h in rep['hops']]}")
        for note in rep["notes"]:
            print(f"       - {note}")

    total = rounds * len(TASKS)
    warns = warn_log_delta(warn_before)
    print(f"\n结果: 任务完成 {task_done}/{total}"
          f"（审核拦截 {task_rejected} 为上游随机行为不计失败, 失败 {task_failed}）, "
          f"server_warn.log 新增预警 {warns} 行")
    if warns:
        print("   （预警只报告不判失败；排查请看 server_warn.log）")
    sys.exit(0 if task_failed == 0 and task_done + task_rejected == total else 1)


if __name__ == "__main__":
    main()
