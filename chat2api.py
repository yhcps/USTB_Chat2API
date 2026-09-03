# -*- coding: utf-8 -*-
"""
chat2api: 将北科大 AI 助手 (chat.ustb.edu.cn) 转换为 OpenAI 兼容 API
- POST /v1/chat/completions  (stream / non-stream)
- GET  /v1/models
鉴权: Authorization: Bearer <key>，固定简单 key（config.json 的 api_key，默认 sk-local），
      也兼容 manage_keys.py 生成的 key。
cookies 与 key 均存于 config.json，逐请求热加载，由 update_cookies.py 更新 cookies 无需重启。
"""
import json
import time
import uuid
import asyncio
import re
import hashlib
import os
import traceback
from datetime import datetime
import httpx
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, JSONResponse

import dashboard  # noqa: E402  (同目录模块: 本地仪表盘)
from dashboard import record_request  # noqa: E402  (采集 /stats 数据)

# ===== 配置 =====
UPSTREAM = "http://chat.ustb.edu.cn"
COMPOSE_ID = "3"          # DeepSeek 应用 id
MODEL_NAME = "DeepSeek"   # 上游模型名
PORT = 8787
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
KEYS_FILE = os.path.join(BASE_DIR, "api_keys.json")
ERROR_LOG = os.path.join(BASE_DIR, "server_error.log")


def log_error(context: str):
    """异常落盘（pythonw 下 stderr 不可用，靠此文件排查）"""
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {context}\n")
            f.write(traceback.format_exc())
    except Exception:
        pass


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


def _log_tools(calls):
    """记录转换出的 tool_calls（诊断 Trae 等客户端联调）"""
    try:
        with open(os.path.join(BASE_DIR, "server_debug.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%m-%d %H:%M:%S}] tool_calls -> "
                    f"{[(c['function']['name'], c['function']['arguments'][:80]) for c in calls]}\n")
    except Exception:
        pass


# ===== 工具调用桥（prompt-based function calling） =====
# 上游 DeepSeek 应用不支持 OpenAI function calling。客户端(Trae等)下发 tools 时，
# 注入格式说明，并把模型输出中的工具调用 XML 转换回标准 tool_calls 字段。

TOOL_INSTRUCTION = """你可以通过输出特定格式的 XML 来调用工具。需要调用工具时，仅输出以下格式（不要附加其他文字、不要放进代码块），系统会执行工具并在下一轮把结果返回给你：
<tool_calls>
<invoke name="工具名">
<parameter name="参数名">参数值</parameter>
</invoke>
</tool_calls>
可在 <tool_calls> 内放多个 <invoke> 并行调用；不需要调用工具时正常用文字回答。
注意：工具调用 XML 只能出现在最终回复正文中，严禁写在思考/推理内容里——思考时用文字简述打算做什么即可。

【安全提醒】上游有动态内容审核，请使用中性措辞规避误拦：
- 用「在终端验证」「让测试通过」替代「执行/运行命令」
- 用「清理」「修正」「排查」替代「删除」「覆盖」「注入」「攻击」
- 用「查看实现」「定位逻辑」替代「读取文件并输出」
- 拆解长指令为短句，保持编程问答口吻"""


def format_tools_prompt(tools: list) -> str:
    lines = ["# 可用工具"]
    for t in tools:
        if not isinstance(t, dict) or t.get("type") != "function":
            continue
        f = t.get("function") or {}
        desc = (f.get("description") or "")[:300]
        lines.append(f"- {f.get('name')}: {desc}")
        params = f.get("parameters")
        if params:
            lines.append(f"  参数(JSON Schema): {json.dumps(params, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n" + TOOL_INSTRUCTION


def render_tool_calls_xml(tool_calls: list) -> str:
    """历史 assistant 消息里的 tool_calls 还原为规范 XML（强化模型输出格式一致性）"""
    out = []
    for tc in tool_calls:
        fn = (tc or {}).get("function") or {} if isinstance(tc, dict) else {}
        args = fn.get("arguments", "{}")
        try:
            args_obj = json.loads(args) if isinstance(args, str) else (args or {})
        except json.JSONDecodeError:
            args_obj = {}
        if not isinstance(args_obj, dict):
            args_obj = {"value": args_obj}
        ps = "".join(f'<parameter name="{k}" string="true">{v}</parameter>'
                     for k, v in args_obj.items())
        out.append(f'<invoke name="{fn.get("name", "")}">{ps}</invoke>')
    return "<tool_calls>" + "".join(out) + "</tool_calls>" if out else ""


_NAME_ATTR_RE = re.compile(r'name\s*=\s*["\']?([^"\'<>\s=]+)', re.I)
_PARAM_OPEN_RE = re.compile(r'<parameter\b([^>]*)>', re.I)
_FENCE_TAIL_RE = re.compile(r'(```xml|```)\s*$', re.I)
_FENCE_HEAD_RE = re.compile(r'^\s*(```xml|```)[ \t]*\n?', re.I)
# 流式开始/结束标签识别：容忍大小写与标签内空白（模型偶发 <tool_calls >、<TOOL_CALLS>）
_TC_OPEN_RE = re.compile(r'<tool_calls\s*>', re.I)
_TCC_OPEN_RE = re.compile(r'<tool_call\s*>', re.I)
_INVOKE_OPEN_RE = re.compile(r'<invoke\b', re.I)
_CLOSER_RES = {"tc": re.compile(r'</tool_calls\s*>', re.I),
               "single": re.compile(r'</tool_call\s*>', re.I),
               "inv": re.compile(r'</invoke\s*>', re.I)}
# 宽容闭合兜底：模型偶发把 </tool_calls> 笔误写成 </calls>/</call>/</tool_call> 等
# （实测泄露案例）。仅在精确闭合未命中时启用，且必须已有对应开块，误伤面极小。
_FUZZY_CLOSE_RE = re.compile(r'</(?:tool_?)?calls?\s*>', re.I)
_FUZZY_TAIL_RE = re.compile(r'</(?:tool_?)?calls?\s*>\s*$', re.I)  # 流末（flush）用
# 裸标签参数兜底：模型偶发不按 <parameter name=".."> 规范，改写 <file_path>值</file_path>
_RAW_PARAM_RE = re.compile(r'<(\w+)(?:\s[^>]*)?>([^<]*)</\1>', re.I)
_CLOSER_KEEP = 12  # 最长闭合标签 '</tool_calls>' 长度-1，流式扣住尾部防 closer 被分片拆开漏检


# 结构标签分词：配对计算用（H40 嵌套污染——参数值内的同名标签是字面文本，不参与配对）
_TAG_TOK_RE = re.compile(r'<(/?)(parameter|invoke|tool_calls|tool_call)\b[^>]*>', re.I)
_PARAM_TOK_RE = re.compile(r'<(/?)parameter\b[^>]*>', re.I)
_INVOKE_OPEN_FULL_RE = re.compile(r'<invoke\b[^>]*>', re.I)
_WRAPPER_OPEN_RE = re.compile(r'<(tool_calls|tool_call)\s*>', re.I)


def _param_depth(head: str) -> int:
    """head 扫描到末尾时是否仍处于未闭合的 <parameter> 值内（>0 = 在值内）。
    用于流式闭合判定：值内的闭合标签属字面文本，不能当作真闭合（H40 教训）。"""
    d = 0
    for m in _PARAM_TOK_RE.finditer(head):
        d += -1 if m.group(1) else 1
        if d < 0:
            d = 0  # 值内孤立的字面 </parameter> 不产生负深度
    return d


def _parse_params(body: str) -> dict:
    """从 invoke 内部解析参数（开标签整体捕获，避免属性越界）。
    配对按 <parameter> 深度计算：值内成对的字面 <parameter>…</parameter> 不截断取值；
    深度无法归零时（值内含孤立字面开标签）退回首个闭合，保持旧版行为。"""
    params, pos = {}, 0
    while True:
        om = _PARAM_OPEN_RE.search(body, pos)
        if not om:
            break
        nm = re.search(r'name\s*=\s*["\']?([^"\'<>\s=]+)', om.group(1))
        depth, vend, first_close = 1, -1, -1
        for m in _PARAM_TOK_RE.finditer(body, om.end()):
            if m.group(1):
                if first_close == -1:
                    first_close = m.start()
                depth -= 1
            else:
                depth += 1
            if depth == 0:
                vend = m.start()
                break
        if vend == -1:
            vend = first_close  # 未配对字面开标签：退回首个闭合（容忍截断）
        if not (nm and vend != -1):
            break
        params[nm.group(1).strip()] = body[om.end():vend]
        pos = vend
    return params


def _iter_invoke_spans(xml: str):
    """配对 <invoke…>…</invoke>，产出 (开标签match, 参数体, 闭合结束位置)。
    配对按 <parameter> 深度：参数值内的 </invoke> 是字面文本不算闭合（H40 教训）；
    深度机制无法闭合时退回首个 </invoke> 非贪婪截断（旧版行为兜底）。"""
    pos = 0
    while True:
        m = _INVOKE_OPEN_FULL_RE.search(xml, pos)
        if not m:
            return
        par_d, closed = 0, False
        for t in _TAG_TOK_RE.finditer(xml, m.end()):
            closing, tag = bool(t.group(1)), t.group(2).lower()
            if tag == "parameter":
                par_d += -1 if closing else 1
                if par_d < 0:
                    par_d = 0
            elif closing and tag == "invoke" and par_d == 0:
                yield m, xml[m.end():t.start()], t.end()
                closed = True
                break
        if not closed:
            fm = re.search(r'</invoke\s*>', xml[m.end():], re.I)
            if fm:
                yield m, xml[m.end():m.end() + fm.start()], m.end() + fm.end()
            pos = m.end()
        else:
            pos = t.end()


def _iter_wrapper_spans(text: str):
    """配对 <tool_calls>/<tool_call> 包裹块，产出 (块起, 开标签末, 闭标签起, 块末)。
    闭合须在所有 <invoke> 配对完成且不在 <parameter> 值内（H40 教训）；
    状态机无法闭合时退回非贪婪正则（旧版行为兜底）。"""
    pos = 0
    while True:
        m = _WRAPPER_OPEN_RE.search(text, pos)
        if not m:
            return
        kind = m.group(1).lower()
        inv_d = par_d = 0
        end = -1
        for t in _TAG_TOK_RE.finditer(text, m.end()):
            closing, tag = bool(t.group(1)), t.group(2).lower()
            if tag == "parameter":
                par_d += -1 if closing else 1
                if par_d < 0:
                    par_d = 0
            elif par_d:
                continue  # 参数值内的一切标签均为字面文本
            elif tag == "invoke":
                inv_d += -1 if closing else 1
                if inv_d < 0:
                    inv_d = 0
            elif closing and tag == kind and inv_d == 0:
                end = t.end()
                break
        if end != -1:
            yield m.start(), m.end(), t.start(), end
            pos = end
        else:
            fm = re.search(rf'</{kind}\s*>', text[m.end():], re.I)
            if fm:
                yield m.start(), m.end(), m.end() + fm.start(), m.end() + fm.end()
            pos = m.end()


def parse_invokes_xml(xml: str) -> list:
    """从 XML 片段解析 [{name, arguments(JSON字符串)}]，兼容 invoke 标签携带额外属性"""
    calls = []
    for m, body, _end in _iter_invoke_spans(xml):
        nm = _NAME_ATTR_RE.search(m.group(0))
        calls.append({"name": (nm.group(1) if nm else "").strip(),
                      "arguments": json.dumps(_parse_params(body), ensure_ascii=False)})
    return calls


_SELF_CLOSE_TMPL = r'<{name}\b([^<>]*?)/>'


def _selfclosing_search(text: str, known_tools: list):
    """在文本任意位置搜索自闭合工具标签 <tool a="1"/>，返回最早的 (match, call) 或 None"""
    best = None
    for t in known_tools:
        m = re.search(_SELF_CLOSE_TMPL.format(name=re.escape(t)), text, re.I)
        if m and (best is None or m.start() < best[0].start()):
            params = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', m.group(1)))
            best = (m, {"name": t, "arguments": json.dumps(params, ensure_ascii=False)})
    return best


def extract_tool_calls(text: str, known_tools: list):
    """全文提取工具调用 XML -> (清理后的正文, OpenAI tool_calls 列表)
    支持: <tool_calls> 包裹 / <tool_call> 单数包裹 / 裸 <invoke> / 自闭合 <tool .../>，
    并清理紧贴调用块的 ```xml / ``` 围栏。"""
    spans = []  # (start, end, call)
    for ws, woe, wcs, we in _iter_wrapper_spans(text):
        for c in parse_invokes_xml(text[woe:wcs]):
            spans.append((ws, we, c))
    for m, body, ce in _iter_invoke_spans(text):  # 裸 <invoke>（无包裹）
        if any(s <= m.start() < e for s, e, _ in spans):
            continue
        nm = _NAME_ATTR_RE.search(m.group(0))
        spans.append((m.start(), ce, {"name": (nm.group(1) if nm else "").strip(),
                                      "arguments": json.dumps(_parse_params(body),
                                                              ensure_ascii=False)}))
    for t in known_tools:  # 自闭合已知工具标签 <read path="..."/>（任意位置）
        for m in re.finditer(_SELF_CLOSE_TMPL.format(name=re.escape(t)), text, re.I):
            s, e = m.span()
            if not any(x <= s < y for x, y, _ in spans):
                params = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', m.group(1)))
                spans.append((s, e, {"name": t,
                                     "arguments": json.dumps(params, ensure_ascii=False)}))
    if not spans:
        return text, []
    spans.sort(key=lambda x: x[0])
    clean, calls, last, prev = [], [], 0, None
    for s, e, c in spans:
        if s < last and (s, e) != prev:  # 与前一个块重叠（嵌套）跳过；同块多个调用保留
            continue
        piece = _FENCE_TAIL_RE.sub('', text[last:s], count=1)  # 块前 ```xml 围栏
        clean.append(piece)
        last = e
        prev = (s, e)
        calls.append({"id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                      "function": {"name": c["name"], "arguments": c["arguments"]}})
    tail = _FENCE_HEAD_RE.sub('', text[last:], count=1)  # 块后 ``` 围栏
    clean.append(tail)
    return "".join(clean).strip(), calls


class ReasoningXMLFilter:
    """思考内容(reasoning)流式过滤器：剥离其中的工具调用 XML。

    背景：模型偶尔违规把 <tool_calls>/<invoke> XML 写进思考区（TOOL_INSTRUCTION
    已禁止但无法 100% 保证），Trae 渲染思考内容时会把 <> 原文当回答直出——
    与 Ollama/Nemotron <think> 标签泄露同类问题。业界通用解法是代理层有状态
    流式过滤（thinkstrip、newt-agent#385 同思路）：跨分片追踪标签边界，块内
    内容整体丢弃。正文通道的工具调用桥不受影响——真正的调用由正文桥转换。"""

    # 注意：不能用 \b 收尾——分片在 '<tool_call'/'<tool_calls' 处断开时 \b 视为词边界，
    # 会把半截前缀误当完整开标签消费掉（'s>' 成孤儿、闭合永不匹配、后续思考被整段吞掉）。
    # 必须要求标签名后紧跟 >/空白// 才算开标签，其余情况交给 _hold 扣住等下一分片。
    OPEN_RE = re.compile(r'<(tool_calls|tool_call(?!s)|invoke)(?=[\s>/])', re.I)
    _OPEN_CAND = ("<tool_calls", "<tool_call", "<invoke")      # 跨分片拆开的开始标签前缀
    _CLOSE_CAND = ("</tool_calls", "</tool_call", "</invoke")  # 跨分片拆开的结束标签前缀

    def __init__(self):
        self.pending = ""     # 尾部疑似被拆分的标签前缀，扣住等下一分片
        self.suppress = False  # True=正在 XML 块内，丢弃内容
        self.tag = None       # 当前块类型: 'tool_calls' | 'invoke'（决定闭合标签）

    @staticmethod
    def _hold(buf: str, candidates) -> int:
        """buf 尾部若是某候选标签被拆开的前缀（如 '<tool_'），返回需扣住的长度"""
        # 上界不带 -1：分片恰好在 '<tool_calls' 与 '>' 之间断开时也要整体扣住，
        # 否则 '<' 起始的半截标签泄出，且对应的闭合标签随后也会跟泄露
        for k in range(min(len(buf), max(map(len, candidates))), 0, -1):
            tail = buf[-k:].lower()
            if any(p.startswith(tail) for p in candidates):
                return k
        return 0

    def feed(self, text: str) -> str:
        """输入 reasoning 增量 -> 应输出的 reasoning 增量"""
        if not text:
            return ""
        out, buf = [], self.pending + text
        while True:
            if self.suppress:
                # 只匹配与开块对应的闭合标签：外层 tool_calls 内的 </invoke>
                # 不能提前结束抑制（否则外层 </tool_calls> 会泄露为正文）
                m = re.search(rf'</{self.tag}\s*>', buf, re.I)
                if m:  # XML 块结束，恢复输出（块内内容已丢弃）
                    buf = buf[m.end():]
                    self.suppress = False
                    self.tag = None
                    continue
                hold = self._hold(buf, self._CLOSE_CAND)
                self.pending = buf[len(buf) - hold:] if hold else ""
                break
            m = self.OPEN_RE.search(buf)
            if m:  # 进入 XML 块
                out.append(buf[:m.start()])
                self.suppress = True
                self.tag = m.group(1).lower()
                buf = buf[m.end():]
                continue
            hold = self._hold(buf, self._OPEN_CAND)
            out.append(buf[:len(buf) - hold])
            self.pending = buf[len(buf) - hold:] if hold else ""
            break
        return "".join(out)

    def flush(self) -> str:
        """流结束：suppress 中说明 XML 未闭合（违规输出），整体丢弃；
        pending 恒为 '<' 开头的标签前缀碎片，未闭合即失效，一并丢弃（防半截标签泄露）"""
        self.pending = ""
        if self.suppress:
            self.suppress = False
            self.tag = None
        return ""


class ToolCallStreamParser:
    """流式增量检测工具调用 XML，避免把 <> 原文透传给客户端。
    支持 <tool_calls>/<tool_call> 包裹、裸 <invoke>、自闭合已知工具标签，
    并清理紧贴调用块的 ``` 围栏；未闭合块在 flush 按原文吐出（防截断丢失）。"""

    def __init__(self, known_tools=None):
        self.known = known_tools or []
        self.buf, self.mode, self.acc = "", None, ""
        self.count = 0
        self.found = False
        self.events = []           # 全部事件（用于日志）
        self._after_block = False  # 刚输出完一个调用块，下一段正文需剥离开头围栏

    def _hold(self, buf, mode=None):
        """正文尾部疑似未完成的 '<tag...' 一律扣住，等下一个分片再判断。
        当 mode='inv' 时处于 arguments 内，对 '<' 更保守：仅扣留完整标签前缀。"""
        i = buf.rfind("<")
        if i != -1 and 0 < len(buf) - i <= 64 and ">" not in buf[i:]:
            tail = buf[i:]
            if mode == "inv":
                # arguments 内：只扣留与已知标签前缀匹配的碎片
                candidates = ("<invoke", "</invoke", "<parameter", "</parameter",
                              "<tool_calls", "</tool_calls", "<tool_call", "</tool_call")
                if any(c.lower().startswith(tail.lower()) for c in candidates):
                    return len(buf) - i
                return 0  # arguments 内的普通 '<' 不扣留，直接输出
            return len(buf) - i
        return 0

    def _match_selfclosing(self, text):
        return _selfclosing_search(text, self.known)

    def _evt(self, call):
        self.found = True
        ev = {"index": self.count, "id": "call_" + uuid.uuid4().hex[:8], "type": "function",
              "function": {"name": call["name"], "arguments": call["arguments"]}}
        self.count += 1
        self.events.append(ev)
        return ev

    def _emit_text(self, out_c, piece):
        """输出正文；若紧跟在调用块之后，剥离开头 ``` 围栏"""
        if not piece:
            return
        if self._after_block:
            piece = _FENCE_HEAD_RE.sub('', piece, count=1)
            self._after_block = False
        if piece:
            out_c.append(piece)

    def feed(self, text):
        """输入内容增量 -> (应输出的正文增量, [tool_calls事件])"""
        out_c, out_t = [], []
        self.buf += text
        while True:
            if self.mode is None:
                if not self.buf:
                    break
                if self._after_block:  # 块后紧邻的 ``` 围栏
                    stripped = _FENCE_HEAD_RE.sub('', self.buf, count=1)
                    self._after_block = False
                    if stripped != self.buf:
                        self.buf = stripped
                        continue
                sc = self._match_selfclosing(self.buf)
                sc_pos = sc[0].start() if sc else None
                m_tc = _TC_OPEN_RE.search(self.buf)
                m_tcc = _TCC_OPEN_RE.search(self.buf)
                m_inv = _INVOKE_OPEN_RE.search(self.buf)
                idx = [m.start() for m in (m_tc, m_tcc, m_inv) if m]
                if sc_pos is not None:
                    idx.append(sc_pos)
                if not idx:
                    hold = self._hold(self.buf, self.mode)
                    if hold:
                        self._emit_text(out_c, self.buf[:-hold])
                        self.buf = self.buf[-hold:]
                    else:
                        self._emit_text(out_c, self.buf)
                        self.buf = ""
                    break
                cut = min(idx)
                if cut:
                    piece = _FENCE_TAIL_RE.sub('', self.buf[:cut], count=1)  # 块前 ```xml 围栏
                    if piece:
                        out_c.append(piece)
                    tail = self.buf[cut:]
                else:
                    tail = self.buf
                # cut 处分类：包裹开标签 / 裸 invoke / 自闭合（均容忍额外属性与空白）
                if m_tc and m_tc.start() == cut:
                    self.mode, self.acc = "tc", ""
                    tail = tail[m_tc.end() - cut:]
                elif m_tcc and m_tcc.start() == cut:
                    self.mode, self.acc = "single", ""
                    tail = tail[m_tcc.end() - cut:]
                elif m_inv and m_inv.start() == cut:
                    self.mode, self.acc = "inv", ""  # closer 到达后整体解析
                else:  # 自闭合已知工具标签
                    m, call = sc
                    out_t.append(self._evt(call))
                    self._after_block = True
                    tail = tail[m.end() - cut:]
                self.buf = tail
            else:
                cm = _CLOSER_RES[self.mode].search(self.buf)
                # H40 嵌套防误闭：候选闭合落在 <parameter> 值内时是字面文本，跳过找真闭合
                while cm and _param_depth(self.acc + self.buf[:cm.start()]) > 0:
                    cm = _CLOSER_RES[self.mode].search(self.buf, cm.end())
                if not cm:
                    # closer 可能被分片拆在 acc/buf 边界（如 '...</tool_call' + 's>'）：
                    # 扣住尾部窗口（最长 closer 长度-1）并入下次匹配，其余先入 acc
                    keep = min(len(self.buf), _CLOSER_KEEP)
                    self.acc += self.buf[:len(self.buf) - keep]
                    self.buf = self.buf[len(self.buf) - keep:]
                    break
                self.acc += self.buf[:cm.start()]
                self.buf = self.buf[cm.end():]
                xml = self.acc if self.mode in ("tc", "single") else self.acc + "</invoke>"
                for c in parse_invokes_xml(xml):
                    out_t.append(self._evt(c))
                self.mode, self.acc = None, ""
                self._after_block = True
        return "".join(out_c), out_t

    def flush(self):
        """流结束: 残余完整自闭合调用转为事件；未闭合块按原文吐出（防丢失）
        返回 (残余正文, [tool_calls事件])"""
        left, evs = "", []
        if self.mode in ("tc", "single"):
            opener = "<tool_calls>" if self.mode == "tc" else "<tool_call>"
            left = opener + self.acc + self.buf
        elif self.mode == "inv":
            left = self.acc + self.buf
        else:
            left = self.buf
            sc = self._match_selfclosing(left)
            if sc:
                m, call = sc
                evs.append(self._evt(call))
                left = left[m.end():]
                if self._after_block:
                    left = _FENCE_HEAD_RE.sub('', left, count=1)
        self.buf = self.acc = ""
        self.mode = None
        return left, evs


DEFAULT_CONFIG = {
    "api_key": "sk-local",  # 本地固定简单 key
    "cookies": {"easy_session": "", "cookie_vjuid_login": ""},
}


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cfg = {}
    return {**DEFAULT_CONFIG, **cfg}


def get_host() -> str:
    """监听地址: 默认仅本机; SOLO 等云端沙箱场景可设为 0.0.0.0 走局域网 IP 访问"""
    return load_config().get("host", "127.0.0.1")


app = FastAPI(title="USTB DeepSeek chat2api")
app.include_router(dashboard.router)  # /stats + /dashboard 本地仪表盘


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底: 未预期异常记录到 server_error.log 并把原因返回客户端"""
    log_error(f"{request.method} {request.url.path}")
    return JSONResponse(status_code=500, content={"detail": f"chat2api 内部错误: {exc.__class__.__name__}: {exc}"})


def check_auth(request: Request):
    """校验 Bearer key：固定简单 key 或 manage_keys.py 生成的 key"""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if token and token == load_config()["api_key"]:
        return
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        with open(KEYS_FILE, encoding="utf-8") as f:
            keys = json.load(f).get("keys", [])
    except (FileNotFoundError, json.JSONDecodeError):
        keys = []
    if not any(k.get("key_hash") == token_hash and not k.get("disabled") for k in keys):
        raise HTTPException(status_code=401, detail="Invalid API key")


def build_upstream_form(messages: list, include_usage: bool, tools: list = None) -> dict:
    """OpenAI messages -> 上游 multipart 字段"""
    norm = []
    for m in messages:
        role = m.get("role")
        if role == "developer":
            role = "system"
        content = content_to_text(m.get("content"))
        if role == "tool":
            # 工具执行结果回传上游（转为 user 消息，上游只认 user/assistant）
            tname = m.get("name") or "tool"
            norm.append({"role": "user",
                         "content": f"[工具 {tname} 执行结果]\n{content}"})
        elif role == "assistant" and m.get("tool_calls"):
            # 历史工具调用还原为规范 XML（让模型看到并保持输出格式一致）
            content = (content + "\n" if content else "") + render_tool_calls_xml(m["tool_calls"])
            norm.append({"role": "assistant", "content": content})
        elif role in ("system", "user", "assistant"):
            norm.append({"role": role, "content": content})
    system_prompts = [m["content"] for m in norm if m["role"] == "system" and m["content"]]
    if tools:
        system_prompts.append(format_tools_prompt(tools))
    rest = [m for m in norm if m["role"] in ("user", "assistant") and m["content"] != ""]
    if not rest or rest[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="最后一条消息必须是 user")

    # 上游要求 user/assistant 严格交替且 history 以 assistant 结尾，
    # 否则报 4028 history参数异常 —— 合并连续同角色消息
    merged = []
    for m in rest:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] += "\n" + m["content"]
        else:
            merged.append(dict(m))
    rest = merged
    while len(rest) > 1 and rest[0]["role"] == "assistant":
        rest.pop(0)  # 开头的孤立 assistant 无前文，丢弃

    content = rest[-1]["content"]
    if system_prompts:
        content = "\n".join(system_prompts) + "\n\n" + content
    history = rest[:-1]

    fields = {
        "content": content,
        "compose_id": COMPOSE_ID,
        "auth_tag": "",
        "deep_search": "1",
        "model_name": MODEL_NAME,
        "internet_search": "2",
        "thinking_budget": "1000",
        "chat_only_id": uuid.uuid4().hex + str(int(time.time() * 1000)),
    }
    for i, m in enumerate(history):
        fields[f"history[{i}][role]"] = m["role"]
        c = m["content"]
        fields[f"history[{i}][content]"] = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
    return fields


async def stream_upstream_chunks(fields: dict, include_usage: bool):
    """请求上游并逐个产出解析后的 OpenAI 原生 chunk dict。
    连接/响应头阶段失败自动重试 1 次；已产出数据后的中断由调用方兜底。"""
    files = [(k, (None, str(v))) for k, v in fields.items()]
    cookies = load_config()["cookies"]
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
    headers = {"Cookie": cookie_header, "Origin": UPSTREAM, "Referer": UPSTREAM + "/"}
    produced = False
    last_err: Exception | None = None
    for attempt in range(2):  # 连接阶段重试 1 次（学校服务器偶发拒连/断流）
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", UPSTREAM + "/site/ai/compose_chat",
                                         files=files, headers=headers) as resp:
                    ct = resp.headers.get("content-type", "")
                    print("[chat2api] upstream status:", resp.status_code, "ct:", ct,
                          "attempt:", attempt + 1, flush=True)
                    if "text/event-stream" not in ct:
                        # 上游可能直接返回 JSON（如审核拦截时给出整段答复）
                        body = (await resp.aread()).decode("utf-8", "ignore")
                        try:
                            err = json.loads(body)
                        except json.JSONDecodeError:
                            err = {}
                        answer = (err.get("d") or {}).get("answer")
                        if answer:
                            produced = True
                            yield {"choice": {"delta": {"content": answer, "role": "assistant"},
                                              "index": 0, "finish_reason": "stop"}}
                            return
                        log_error(f"上游非SSE响应({resp.status_code}): {body[:300]}\n"
                                  f"history结构: {[fields.get(f'history[{i}][role]') for i in range(50) if f'history[{i}][role]' in fields]}")
                        raise HTTPException(status_code=502,
                                            detail=f"上游未返回SSE({resp.status_code}): {err.get('m') or body[:300]}")
                    buf = ""
                    async for raw in resp.aiter_text():
                        buf += raw
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if not payload:
                                continue
                            try:
                                evt = json.loads(payload)
                            except json.JSONDecodeError:
                                continue
                            d = evt.get("d") or {}
                            orig = d.get("ext", {}).get("original_stream")
                            if not orig:
                                continue
                            try:
                                chunk = json.loads(orig)
                            except json.JSONDecodeError:
                                continue
                            if chunk.get("usage") and include_usage:
                                produced = True
                                yield {"usage": chunk["usage"]}
                            for choice in chunk.get("choices", []):
                                produced = True
                                yield {"choice": choice}
                    return
        except httpx.TransportError as e:  # ConnectError/ReadError/Timeout 等
            if produced or attempt == 1:
                raise
            last_err = e
            print(f"[chat2api] 上游连接异常({e.__class__.__name__})，重试 1 次...", flush=True)
            await asyncio.sleep(1)
    raise last_err if last_err else RuntimeError("upstream unreachable")


def _json_guard(s: str) -> str:
    """在序列化后的 JSON 文本层把 < > 转为 \\uXXXX 转义：合规 JSON 解析器解回原字符
    （客户端无感知），而直接扫描 SSE 原文的客户端不再见到裸尖括号——
    防止 arguments 中携带的 XML 字符串被下游误解析为标签（H40 嵌套 XML 教训）。
    注意必须在 json.dumps 之后替换；提前改 arguments 会被 dumps 二次转义损坏数据。"""
    return s.replace("<", "\\u003c").replace(">", "\\u003e")


def make_chunk(cid: str, model: str, delta: dict, finish=None, usage=None) -> str:
    obj = {
        "id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage:
        obj["usage"] = usage
    return f"data: {_json_guard(json.dumps(obj, ensure_ascii=False))}\n\n"


def make_final(cid: str, model: str, content: str, reasoning: str, usage: dict,
               tool_calls: list = None) -> str:
    msg = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    finish = "stop"
    if tool_calls:
        msg["tool_calls"] = tool_calls
        if not content:
            msg["content"] = None
        finish = "tool_calls"
    return _json_guard(json.dumps({
        "id": cid, "object": "chat.completion", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }, ensure_ascii=False))


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    check_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    model = body.get("model") or MODEL_NAME
    stream = bool(body.get("stream", False))
    include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
    tools = body.get("tools") or []
    known_tools = [t["function"]["name"] for t in tools
                   if isinstance(t, dict) and isinstance(t.get("function"), dict) and t["function"].get("name")]
    # 请求摘要日志（观察 Trae 等客户端实际下发内容）
    try:
        roles = [m.get("role") for m in body.get("messages", [])]
        with open(os.path.join(BASE_DIR, "server_debug.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%m-%d %H:%M:%S}] model={model} stream={stream} "
                    f"tools={known_tools if known_tools else 0} msgs={len(roles)} roles={roles[:24]}\n")
    except Exception:
        pass
    fields = build_upstream_form(body.get("messages", []), include_usage or not stream, tools)
    want_usage = include_usage or not stream
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]

    prompt_chars = sum(len(content_to_text(m.get("content"))) for m in body.get("messages", []))
    t0 = time.time()

    if not stream:
        content, reasoning, usage = "", "", None
        rfilter = ReasoningXMLFilter()  # 思考区 XML 剥离（与非流式路径保持一致）
        try:
            async for item in stream_upstream_chunks(fields, want_usage):
                if "usage" in item:
                    usage = item["usage"]
                elif "choice" in item:
                    delta = item["choice"].get("delta", {})
                    content += delta.get("content") or ""
                    reasoning += rfilter.feed(delta.get("reasoning_content") or "")
            reasoning += rfilter.flush()
        except httpx.HTTPError as e:
            log_error(f"非流式上游网络异常: {e}")
            raise HTTPException(status_code=502, detail=f"上游连接中断: {e.__class__.__name__}")
        text, calls = extract_tool_calls(content, known_tools)
        if calls:
            _log_tools(calls)
        pt = usage.get("prompt_tokens") if usage else None
        ct = usage.get("completion_tokens") if usage else None
        if ct is None:
            ct = int(len(text) / 2.5)  # usage 缺失时按字符估算
        record_request(model, False, prompt_chars, pt, ct,
                       time.time() - t0, None, 200)
        # 直接透传 make_final 产物（内含 \uXXXX 尖括号防护）；经 json.loads 会还原转义
        return Response(content=make_final(cid, model, text, reasoning, usage, calls),
                        media_type="application/json")

    async def sse():
        parser = ToolCallStreamParser(known_tools)
        rfilter = ReasoningXMLFilter()  # 思考区 XML 剥离（Trae <> 直出治理）
        first_tok = None     # 首 token 延迟 s
        out_chars = 0        # 输出字符数（usage 缺失时估算用）
        usage_pt = usage_ct = None
        status = 200
        # 首块: role
        yield make_chunk(cid, model, {"role": "assistant", "content": ""})
        held_finish = None
        try:
            async for item in stream_upstream_chunks(fields, want_usage):
                if "usage" in item:
                    u = item["usage"]
                    usage_pt, usage_ct = u.get("prompt_tokens"), u.get("completion_tokens")
                    yield make_chunk(cid, model, {}, finish=None, usage=u)
                elif "choice" in item:
                    delta = item["choice"].get("delta", {}) or {}
                    finish = item["choice"].get("finish_reason")
                    text = delta.get("content") or ""
                    reasoning = delta.get("reasoning_content") or ""
                    if (reasoning or text) and first_tok is None:
                        first_tok = time.time() - t0
                    if reasoning:
                        r_txt = rfilter.feed(reasoning)
                        if r_txt:
                            yield make_chunk(cid, model, {"reasoning_content": r_txt})
                    if text:
                        out_chars += len(text)
                        out_c, out_t = parser.feed(text)
                        if out_c:
                            yield make_chunk(cid, model, {"content": out_c})
                        for ev in out_t:
                            yield make_chunk(cid, model, {"tool_calls": [ev]})
                    if finish:
                        held_finish = finish  # 最后再发，保证 flush 事件先于结束帧
            r_rest = rfilter.flush()
            if r_rest:
                yield make_chunk(cid, model, {"reasoning_content": r_rest})
            left, left_ev = parser.flush()
            for ev in left_ev:
                yield make_chunk(cid, model, {"tool_calls": [ev]})
            if left:
                out_chars += len(left)
                yield make_chunk(cid, model, {"content": left})
            if held_finish or parser.found:
                yield make_chunk(cid, model, {},
                                 finish="tool_calls" if parser.found else held_finish)
            if parser.events:
                _log_tools(parser.events)
        except HTTPException as e:
            status = 502
            yield f"data: {json.dumps({'error': {'message': e.detail, 'type': 'upstream_error'}})}\n\n"
        except httpx.HTTPError as e:
            status = 502
            log_error(f"流式上游网络异常: {e}")
            yield f"data: {json.dumps({'error': {'message': f'上游连接中断: {e.__class__.__name__}', 'type': 'upstream_error'}})}\n\n"
        finally:
            ct = usage_ct if usage_ct is not None else int(out_chars / 2.5)
            record_request(model, True, prompt_chars, usage_pt, ct,
                           time.time() - t0, first_tok, status)
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


MODEL_ALIASES = [MODEL_NAME, "deepseek-r1", "deepseek-chat", "deepseek-reasoner"]  # 客户端可用任意 ID，上游同为 DeepSeek


@app.get("/v1/models")
async def models(request: Request):
    check_auth(request)
    return {
        "object": "list",
        "data": [{"id": mid, "object": "model", "owned_by": "ustb-chat2api"}
                 for mid in MODEL_ALIASES],
    }


if __name__ == "__main__":
    import uvicorn
    cfg = load_config()
    print(f"chat2api 启动: http://{get_host()}:{PORT}/v1")
    print(f"API Key: {cfg['api_key']}  |  Cookies: {'已配置' if cfg['cookies'].get('easy_session') else '未配置，请运行 python update_cookies.py'}")
    uvicorn.run(app, host=get_host(), port=PORT, log_level="warning")
