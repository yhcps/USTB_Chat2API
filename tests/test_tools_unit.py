# -*- coding: utf-8 -*-
"""工具调用桥单元测试（确定性，不依赖上游）
运行: python tests/test_tools_unit.py
"""
import os
import sys
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))  # refactor 包结构优先
# 双写同步：默认测包版，--flat 强制测生产扁平版（两份实现都必须过同一套件）
if "--flat" in sys.argv:
    import chat2api as c
    print(f"== 目标实现: chat2api.py (扁平) ==")
else:
    try:
        from ustb_chat2api import server as c  # 打包后的实现
        print("== 目标实现: ustb_chat2api.server (包) ==")
    except ImportError:
        import chat2api as c  # master 扁平结构回退
        print("== 目标实现: chat2api.py (扁平, 回退) ==")

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

print("== H40 嵌套 XML（参数值内含标签字面文本）==")

# _json_guard：JSON 文本层 < > 转义，合规解析器可还原
g = c._json_guard('{"code": "<tool_calls>ok</tool_calls>"}')
check("json_guard转义", "<" not in g and ">" not in g and "\\u003c" in g and "\\u003e" in g)
check("json_guard可逆", json.loads(g)["code"] == "<tool_calls>ok</tool_calls>")


def _stream_split(pieces, names=("Write",)):
    pp = c.ToolCallStreamParser(list(names))
    parts = [pp.feed(t) for t in pieces]
    tail, tev = pp.flush()
    txt = "".join(x[0] for x in parts) + tail
    evs = [e for x in parts for e in x[1]] + tev
    return txt, evs


# Write 参数值内含完整工具调用 XML 字面文本（H40 真实场景：代码构造工具调用示例）
nested = ('<tool_calls><invoke name="Write"><parameter name="code">'
          'demo = "<tool_calls><invoke name=\\"L\\"><parameter name=\\"p\\">1</parameter>'
          '</invoke></tool_calls>"'
          '</parameter></invoke></tool_calls>')
expected_code = ('demo = "<tool_calls><invoke name=\\"L\\">'
                 '<parameter name=\\"p\\">1</parameter></invoke></tool_calls>"')

clean, tcs = c.extract_tool_calls(nested, ["Write"])
check("非流式嵌套XML提取", clean == "" and len(tcs) == 1
      and json.loads(tcs[0]["function"]["arguments"]).get("code") == expected_code)

# 非流式同块多 invoke（旧版 s<last 去重会漏掉同块的第二个调用）
multi = ('<tool_calls><invoke name="A"><parameter name="x">1</parameter></invoke>'
         '<invoke name="B"><parameter name="y">2</parameter></invoke></tool_calls>')
clean, tcs = c.extract_tool_calls(multi, ["A", "B"])
check("非流式同块多invoke", clean == "" and [t["function"]["name"] for t in tcs] == ["A", "B"])

# 流式：断点落在值内字面 </tool_calls> 内外，不得误闭、参数须完整
k = nested.index("</tool_calls>")  # 值内首个字面闭合
bad = []
for off in (-6, -3, 0, 3, 6):
    txt, evs = _stream_split([nested[:k + off], nested[k + off:]])
    if txt != "" or len(evs) != 1:
        bad.append(off)
    elif json.loads(evs[0]["function"]["arguments"]).get("code") != expected_code:
        bad.append(("args", off))
check("流式嵌套XML切分" + (f"，异常:{bad[:4]}" if bad else "全部无损"), not bad)

# 流式裸 invoke：值内字面 </invoke> 不得提前闭合
bare = ('<invoke name="Echo"><parameter name="text">he said "</invoke>" loudly'
        '</parameter></invoke>')
txt, evs = _stream_split([bare], names=("Echo",))
check("流式裸invoke值内闭合标签", txt == "" and len(evs) == 1
      and json.loads(evs[0]["function"]["arguments"]).get("text") == 'he said "</invoke>" loudly')

# 输出层：chunk / final 无裸尖括号，且 JSON 解析还原无损
chunk = c.make_chunk("t", "m", {"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                                "function": {"name": "Write",
                                                             "arguments": '{"code": "<b>x</b>"}'}}]})
check("chunk无裸尖括号", "<" not in chunk and "\\u003c" in chunk)
fin = c.make_final("t", "m", "", None, {}, tool_calls=[{"id": "c2", "type": "function",
                                                        "function": {"name": "Write",
                                                                     "arguments": '{"code": "<tool_calls>x</tool_calls>"}'}}])
check("final无裸尖括号", "<" not in fin and "\\u003c" in fin)
back = json.loads(fin)["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
check("final解析还原", json.loads(back)["code"] == "<tool_calls>x</tool_calls>")

print("== H41 DSML 腐蚀标签（｜DSML｜ 变体 / </tocalls>，真实泄露治理）==")

# 案例1（真实泄露现场）：包裹闭合被腐蚀成 </｜DSML｜>，旧版 flush 整块原文外露、调用不执行
dsml1 = ('<tool_calls><invoke name="SearchReplace"><parameter name="file_path">f:\\x.py</parameter>'
         '</invoke></｜DSML｜>后续正文')
clean, tcs = c.extract_tool_calls(dsml1, ["SearchReplace"])
check("非流式DSML包裹闭合", clean == "后续正文" and len(tcs) == 1
      and json.loads(tcs[0]["function"]["arguments"]).get("file_path") == "f:\\x.py")
txt, evs = _stream_split([dsml1], names=("SearchReplace",))
check("流式DSML包裹闭合", txt == "后续正文" and len(evs) == 1
      and json.loads(evs[0]["function"]["arguments"]).get("file_path") == "f:\\x.py")

# 案例2（真实泄露现场）：参数闭合被腐蚀且无真闭合（</｜DSML｜> 替 </parameter>）
dsml2 = ('<tool_calls><invoke name="Write"><parameter name="file_path">f:\\a.py</parameter>'
         '<parameter name="content">print(1)</｜DSML｜></｜DSML｜>')
clean, tcs = c.extract_tool_calls(dsml2, ["Write"])
args = json.loads(tcs[0]["function"]["arguments"]) if tcs else {}
check("非流式DSML参数闭合", clean == "" and len(tcs) == 1
      and args.get("file_path") == "f:\\a.py" and args.get("content") == "print(1)")
txt, evs = _stream_split([dsml2], names=("Write",))
args = json.loads(evs[0]["function"]["arguments"]) if evs else {}
check("流式DSML参数闭合(容错提取)", txt == "" and len(evs) == 1
      and args.get("file_path") == "f:\\a.py" and args.get("content") == "print(1)")

# 案例3：</tocalls> 替 </tool_calls>
toc = ('<tool_calls><invoke name="Read"><parameter name="file_path">f:\\b.py</parameter>'
       '</invoke></tocalls>尾部')
txt, evs = _stream_split([toc], names=("Read",))
check("流式tocalls闭合", txt == "尾部" and len(evs) == 1
      and json.loads(evs[0]["function"]["arguments"]).get("file_path") == "f:\\b.py")

# 案例4（tui.py 真实案例）：参数开标签被腐蚀 <｜DSML｜ name="file_path" ...> 替 <parameter ...>
dsml4 = ('<tool_calls><invoke name="SearchReplace">'
         '<｜DSML｜ name="file_path" string="true">f:\\tui.py</parameter>'
         '<parameter name="old_str">def main():</parameter></invoke></tool_calls>')
clean, tcs = c.extract_tool_calls(dsml4, ["SearchReplace"])
args = json.loads(tcs[0]["function"]["arguments"]) if tcs else {}
check("非流式DSML参数开标签", len(tcs) == 1
      and args.get("file_path") == "f:\\tui.py" and args.get("old_str") == "def main():")

# 案例5：孤立腐蚀标签（<｜DSML｜e>）在正文/思考区剥离，不外露
txt, evs = _stream_split(["回答前<｜DSML｜e>回答后"], names=("Read",))
check("孤立DSML标签剥离(正文)", txt == "回答前回答后" and not evs)
o, fl = feed_all(["思考<｜DSML｜e>继续思考"])
check("孤立DSML标签剥离(思考区)", o + fl == "思考继续思考")
o, fl = feed_all(["思考<tool_calls><invoke name=\"R\"></invoke></｜DSML｜>后续思考"])
check("reasoning DSML闭合", o + fl == "思考后续思考")

# 穷举切分（两片全量）：任意分片边界下腐蚀闭合不泄露、调用不丢失
h41 = ('前文<tool_calls><invoke name="Write"><parameter name="file_path">f:\\h.py</parameter>'
       '</invoke></｜DSML｜>后文')
h41bad = []
for i in range(len(h41) + 1):
    txt, evs = _stream_split([h41[:i], h41[i:]], names=("Write",))
    if txt != "前文后文" or len(evs) != 1:
        h41bad.append(i)
    elif json.loads(evs[0]["function"]["arguments"]).get("file_path") != "f:\\h.py":
        h41bad.append(("args", i))
check("H41 content两片穷举切分" + (f"，异常位:{h41bad[:6]}" if h41bad else "全部无损"), not h41bad)

r41 = '先想。<tool_calls><invoke name="R"></invoke></｜DSML｜>再想。'
r41bad = []
for i in range(len(r41) + 1):
    o, fl = feed_all([r41[:i], r41[i:]])
    if o + fl != "先想。再想。":
        r41bad.append(i)
check("H41 reasoning两片穷举切分" + (f"，异常位:{r41bad[:6]}" if r41bad else "全部无损"), not r41bad)

print("== 尖括号预警（调试阶段）==")

st = {}
c.angle_bracket_check("这是一段正常的回答，包含 a < b 的比较与 `code` 片段。" * 10, "content", st)
check("预警-正常文本不触发", "warned" not in st)

st = {}
c.angle_bracket_check("前文很短", "content", st)
c.angle_bracket_check("<tool_calls><invoke name=\"Read\">", "content", st)
check("预警-泄露模式立即触发", st.get("warned") is True and st.get("cnt", 0) > 0)

st = {"cnt": 150, "chars": 800}
c.angle_bracket_check("<" * 60, "reasoning", st)
check("预警-累计总量触发", st.get("warned") is True)

print(f"\n结果: {PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
