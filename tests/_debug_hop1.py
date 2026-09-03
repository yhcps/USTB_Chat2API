# -*- coding: utf-8 -*-
"""诊断 agent_loop 两跳（串行、无并发干扰）（临时）"""
import json, os, sys, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tests"))
from test_stream_leak import _post_stream, TOOLS, REJECT_RE

for i in range(3):
    msgs = [{"role": "user", "content":
             "请调用 read_file 工具读取 f:\\Chat2API\\requirements.txt，"
             "然后告诉我文件里第一个依赖包的名字。"}]
    t0 = time.time()
    r, c, tcs, e = _post_stream(msgs, TOOLS)
    print(f"--- try{i + 1} err={e} calls={len(tcs)} elapsed={time.time() - t0:.1f}s")
    if tcs:
        call = tcs[min(tcs)]
        with open(os.path.join(BASE, "requirements.txt"), encoding="utf-8") as f:
            fcontent = f.read()
        msgs.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": "call_t", "type": "function",
                                     "function": {"name": call["name"], "arguments": call["arguments"]}}]})
        msgs.append({"role": "tool", "tool_call_id": "call_t", "name": call["name"], "content": fcontent})
        msgs.append({"role": "user", "content":
                     "根据上面的工具执行结果，用一行回答 requirements.txt 里第一个依赖包是什么，不要再调用工具。"})
        r2, c2, tcs2, e2 = _post_stream(msgs, TOOLS)
        print(f"    hop2 err={e2} answer={c2.strip()[:60]!r} reject={bool(REJECT_RE.search(c2))}")
    else:
        print(f"    content={c.strip()[:60]!r} reject={bool(REJECT_RE.search(c))}")
    time.sleep(3)
