# -*- coding: utf-8 -*-
"""工具调用桥单元测试（确定性，不依赖上游）
运行: python tests/test_tools_unit.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chat2api as c

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


print("== parse_invokes_xml ==")
calls = c.parse_invokes_xml('<invoke name="List"><parameter name="file_path">f:\\Chat2API</parameter></invoke>')
check("单 invoke", calls and calls[0]["name"] == "List")
check("参数解析", '"file_path"' in calls[0]["arguments"] and "Chat2API" in calls[0]["arguments"])

calls2 = c.parse_invokes_xml(
    '<invoke name="Read"><parameter name="file_path">a.py</parameter></invoke>'
    '<invoke name="Grep"><parameter name="pattern">foo</parameter><parameter name="path">x</parameter></invoke>')
check("多 invoke", len(calls2) == 2 and calls2[1]["name"] == "Grep")

calls3 = c.parse_invokes_xml('<invoke name=\'List\'><parameter name=\'path\' string="true">C:\\</parameter></invoke>')
check("单引号+string属性", calls3 and "C:\\" in calls3[0]["arguments"])

calls4 = c.parse_invokes_xml('<invoke name="Read" tool="fs"><parameter name="path">a.py</parameter></invoke>')
check("invoke携带额外属性", calls4 and calls4[0]["name"] == "Read" and "a.py" in calls4[0]["arguments"])

print("== extract_tool_calls ==")
text = '好的，我来查看。<tool_calls><invoke name="List"><parameter name="file_path">f:\\x</parameter></invoke></tool_calls>'
clean, tcs = c.extract_tool_calls(text, ["List"])
check("包裹式提取", clean == "好的，我来查看。" and tcs[0]["function"]["name"] == "List")

clean2, tcs2 = c.extract_tool_calls('<invoke name="List"><parameter name="file_path">y</parameter></invoke>', ["List"])
check("裸 invoke 提取", clean2 == "" and len(tcs2) == 1)

clean3, tcs3 = c.extract_tool_calls('<read path="f:\\y"/>', ["read"])
check("自闭合工具标签", clean3 == "" and tcs3[0]["function"]["name"] == "read"
      and json.loads(tcs3[0]["function"]["arguments"]).get("path") == "f:\\y")

clean4, tcs4 = c.extract_tool_calls("没有工具调用的普通回复 < 5 且 a>b", ["List"])
check("普通文本不动", clean4 == "没有工具调用的普通回复 < 5 且 a>b" and tcs4 == [])

clean5, tcs5 = c.extract_tool_calls("前文\n```xml\n<tool_calls><invoke name=\"List\"><parameter name=\"file_path\">z</parameter></invoke></tool_calls>\n```", ["List"])
check("围栏清理(非流式)", "```" not in clean5 and len(tcs5) == 1)

clean6, tcs6 = c.extract_tool_calls('前言<tool_call><invoke name="Grep"><parameter name="pattern">p</parameter></invoke></tool_call>结语', ["Grep"])
check("单数 tool_call 包裹", clean6 == "前言结语" and tcs6[0]["function"]["name"] == "Grep")

clean7, tcs7 = c.extract_tool_calls('查看<LS path="f:\\w"/>完成', ["LS"])
check("自闭合含前后文", clean7 == "查看完成" and tcs7[0]["function"]["name"] == "LS")

print("== ToolCallStreamParser ==")
p = c.ToolCallStreamParser(["List"])
c1, t1 = p.feed("前文<tool_")
c2, t2 = p.feed('calls><invoke name="List"><parameter name="file_path">f:\\Chat2API</para')
c3, t3 = p.feed("meter></invoke></tool_calls>尾部")
c4, e4 = p.flush()
check("跨分片内容无损", (c1 + c2 + c3 + c4) == "前文尾部")
all_t = t1 + t2 + t3
check("跨分片工具事件", len(all_t) == 1 and all_t[0]["function"]["name"] == "List"
      and "Chat2API" in all_t[0]["function"]["arguments"])
check("index 从0", all_t[0]["index"] == 0)

p2 = c.ToolCallStreamParser(["List"])
a1, b1 = p2.feed('x<invoke name="List"><parameter name="a">1</parameter></invoke>y')
a2, b2 = p2.flush()
check("裸 invoke 流式", a1 == "xy" and len(b1) == 1 and a2 == "" and b2 == [])

p3 = c.ToolCallStreamParser([])
u1, v1 = p3.feed("x<tool_calls><invoke name=\"Foo\"")
u2, v2 = p3.flush()
check("未闭合XML防丢失", u1 + u2 == "x<tool_calls><invoke name=\"Foo\"" and v2 == [])

p4 = c.ToolCallStreamParser(["read"])
r1, s1 = p4.feed('<read path="f:\\w"/>后缀')
check("自闭合流式", r1 == "后缀" and len(s1) == 1 and s1[0]["function"]["name"] == "read")

p5 = c.ToolCallStreamParser([])
q1, w1 = p5.feed("数学: 1<2 且 3>1，完成")
q2, w2 = p5.flush()
check("普通 < > 不误吞", (q1 + q2) == "数学: 1<2 且 3>1，完成" and w1 + w2 == [])

p6 = c.ToolCallStreamParser(["LS"])
f1, g1 = p6.feed('```xml\n<tool_calls><invoke name="LS"><parameter name="path">f:\\x</parameter></invoke></tool_calls>\n```尾巴')
f2, g2 = p6.flush()
check("流式围栏清理", (f1 + f2) == "尾巴" and len(g1) == 1)

p7 = c.ToolCallStreamParser(["LS"])
h1, i1 = p7.feed('查看<LS path="f:\\y"/>')
h2, i2 = p7.flush()
check("流末自闭合转事件", (h1 + h2) == "查看" and len(i1) + len(i2) == 1)

p8 = c.ToolCallStreamParser(["Read"])
j1, k1 = p8.feed('<invoke name="Read" tool="fs"><parameter name="path">a')
j2, k2 = p8.feed('.py</parameter></invoke>ok')
j3, k3 = p8.flush()
check("invoke额外属性流式", (j1 + j2 + j3) == "ok" and len(k1 + k2 + k3) == 1)

p9 = c.ToolCallStreamParser(["Grep"])
m1, n1 = p9.feed('前言<tool_call><invoke name="Grep"><parameter name="pattern">p</parameter></invoke></tool_call>结语')
m2, n2 = p9.flush()
check("流式单数包裹", (m1 + m2) == "前言结语" and len(n1) == 1)

print("== build_upstream_form 消息归一化 ==")
msgs = [
    {"role": "system", "content": "你是助手"},
    {"role": "user", "content": "列出目录"},
    {"role": "assistant", "content": None,
     "tool_calls": [{"id": "call_1", "type": "function",
                     "function": {"name": "List", "arguments": "{\"file_path\": \"f:\\\\x\"}"}}]},
    {"role": "tool", "tool_call_id": "call_1", "name": "List", "content": "a.txt\nb.txt"},
    {"role": "user", "content": "总结"},
]
fields = c.build_upstream_form(msgs, False, [{"type": "function", "function": {
    "name": "List", "description": "列目录", "parameters": {"type": "object", "properties": {}}}}])
check("tool消息转为user", any("工具 List 执行结果" in str(v) for v in fields.values()))
check("历史tool_calls还原XML", any("invoke" in str(v) and "List" in str(v) for v in fields.values()))
check("tools说明注入", any("可用工具" in str(v) for v in fields.values()))
check("最后一条为user", fields.get("content", "").endswith("总结") or "总结" in fields.get("content", ""))

# 交替性校验
hs = [fields[f"history[{i}][role]"] for i in range(10) if f"history[{i}][role]" in fields]
check("history严格交替", all(hs[i] != hs[i + 1] for i in range(len(hs) - 1)))
print("  history roles:", hs)

print("== ReasoningXMLFilter（思考区 XML 剥离）==")


def feed_all(chunks):
    """模拟流式分片输入 -> (全部输出, flush输出)"""
    f = c.ReasoningXMLFilter()
    out = "".join(f.feed(t) for t in chunks)
    return out, f.flush()


# 完整 <tool_calls> 块剥离，前后文字保留
o, fl = feed_all(["我在分析问题。<tool_calls><invoke name=\"List\">"
                  "<parameter name=\"path\">f:\\x</parameter></invoke></tool_calls>接下来做什么。"])
check("完整块剥离", o + fl == "我在分析问题。接下来做什么。")

# 跨分片：开始标签被拆开（'<tool_' + 'calls>'）
o, fl = feed_all(["思考中...<tool_", 'calls><invoke name="Read">'
                  '<parameter name="p">a</parameter></invoke></tool_calls>完'])
check("跨分片开始标签", o + fl == "思考中...完")

# 跨分片：结束标签被拆开（'</tool_' + 'calls>'）
o, fl = feed_all(["开头<tool_calls>x</tool_", "calls>结尾"])
check("跨分片结束标签", o + fl == "开头结尾")

# 跨分片：裸 <invoke 拆开
o, fl = feed_all(["前<inv", 'oke name="Grep"><parameter name="q">1</parameter></invoke>后'])
check("跨分片裸invoke", o + fl == "前后")

# 未闭合 XML（流中断）在 flush 丢弃，不吐原文
o, fl = feed_all(["计划<tool_calls><invoke name=\"List\">参数x", "更多思考"])
check("未闭合块丢弃", o + fl == "计划" and "invoke" not in o + fl)

# 普通 <> 与比较运算不受影响
o, fl = feed_all(["if a < b and c > d: 继续", "判断"])
check("普通<>不吞", o + fl == "if a < b and c > d: 继续判断")

# 大小写不敏感
o, fl = feed_all(["x<Tool_Calls><Invoke name=\"List\"></Invoke></Tool_Calls>y"])
check("大小写不敏感", o + fl == "xy")

# XML 位于最后一个分片、无后续文字
o, fl = feed_all(["决定调用工具", "<tool_calls><invoke name=\"List\"></invoke></tool_calls>"])
check("块在末尾", o + fl == "决定调用工具")

# 空输入
f = c.ReasoningXMLFilter()
check("空输入", f.feed("") == "" and f.flush() == "")

# 多块连续剥离
o, fl = feed_all(["a<tool_calls><invoke name=\"L\"></invoke></tool_calls>"
                  "b<invoke name=\"R\"></invoke>c"])
check("多块连续剥离", o + fl == "abc")

# 块内普通文本也一并丢弃（不留碎片）
o, fl = feed_all(["before<tool_calls>这里是要丢弃的思考草稿</tool_calls>after"])
check("块内文本丢弃", o + fl == "beforeafter")

print("== ReasoningXMLFilter 边界加固（真实泄露治理）==")

# 开标签恰好贴分片边界断开：'<tool_calls' + '>'（修复 hold 上界 off-by-one）
o, fl = feed_all(["思考<tool_calls", ">", '<invoke name="L"></invoke>', "</tool_calls>", "完"])
check("开标签贴边界断开", o + fl == "思考完")

# 闭标签贴分片边界断开：'</tool_calls' + '>'（修复抑制态卡死吞掉后续全部思考）
o, fl = feed_all(["a<tool_calls>x</tool_calls", ">", "后续正常思考"])
check("闭标签贴边界不断流", o + fl == "a后续正常思考")

# 单数 <tool_call> 包裹同样剥离（与正文通道对称）
o, fl = feed_all(['x<tool_call><invoke name="G"><parameter name="p">1</parameter></invoke></tool_call>y'])
check("单数tool_call剥离", o + fl == "xy")

# flush：悬挂的标签前缀碎片直接丢弃，不吐 '<' 开头的半截标签
f = c.ReasoningXMLFilter()
o1 = f.feed("计划中<tool_")
check("悬挂前缀flush丢弃", o1 == "计划中" and f.flush() == "")

# invoke 带额外属性也在剥离范围
o, fl = feed_all(['x<invoke name="R" tool="fs"><parameter name="p">1</parameter></invoke>y'])
check("invoke额外属性剥离", o + fl == "xy")

# 穷举切分（两片全量 + 三片抽样）：任意分片边界下标签不泄露、正文无损
sample = ('先看结构。<tool_calls><invoke name="Read" tool="fs">'
          '<parameter name="f">a.py</parameter></invoke></tool_calls>再决定。')
expected = "先看结构。再决定。"
bad2 = []
for i in range(len(sample) + 1):
    o, fl = feed_all([sample[:i], sample[i:]])
    if o + fl != expected:
        bad2.append(i)
check("reasoning两片穷举切分" + (f"，异常位:{bad2[:6]}" if bad2 else "全部无损"), not bad2)
bad3 = []
for i in range(0, len(sample) + 1, 3):
    for j in range(i, len(sample) + 1, 5):
        o, fl = feed_all([sample[:i], sample[i:j], sample[j:]])
        if o + fl != expected:
            bad3.append((i, j))
check("reasoning三片抽样切分" + (f"，异常位:{bad3[:6]}" if bad3 else "全部无损"), not bad3)

print("== ToolCallStreamParser 边界加固 ==")

# 容忍开/闭标签内部空白与大小写
p = c.ToolCallStreamParser(["List"])
a1, b1 = p.feed('<Tool_Calls ><invoke name="List"><parameter name="p">1</parameter></invoke></Tool_Calls >')
a2, b2 = p.flush()
check("标签内空白/大小写容忍", (a1 + a2) == "" and len(b1 + b2) == 1)

# 穷举切分：正文+完整调用块任意两片切分 -> 事件完整、无原文泄露
psample = ('前文<tool_calls><invoke name="List"><parameter name="file_path">f:\\x</parameter>'
           '</invoke><invoke name="Grep"><parameter name="p">y</parameter></invoke></tool_calls>尾文')


def _parser_split(pieces, names=("List", "Grep")):
    pp = c.ToolCallStreamParser(list(names))
    parts = [pp.feed(t) for t in pieces]
    tail, tev = pp.flush()
    txt = "".join(x[0] for x in parts) + tail
    evs = [e for x in parts for e in x[1]] + tev
    return txt, evs


pbad = []
for i in range(len(psample) + 1):
    txt, evs = _parser_split([psample[:i], psample[i:]])
    if txt != "前文尾文" or len(evs) != 2:
        pbad.append(i)
check("content两片穷举切分" + (f"，异常位:{pbad[:6]}" if pbad else "全部无损"), not pbad)
pbad3 = []
for i in range(0, len(psample) + 1, 4):
    for j in range(i, len(psample) + 1, 6):
        txt, evs = _parser_split([psample[:i], psample[i:j], psample[j:]])
        if txt != "前文尾文" or len(evs) != 2:
            pbad3.append((i, j))
check("content三片抽样切分" + (f"，异常位:{pbad3[:6]}" if pbad3 else "全部无损"), not pbad3)

print(f"\n结果: {PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
