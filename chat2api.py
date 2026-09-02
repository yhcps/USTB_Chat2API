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
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

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
可在 <tool_calls> 内放多个 <invoke> 并行调用；不需要调用工具时正常用文字回答。"""


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


_INVOKE_RE = re.compile(r'<invoke\b([^>]*)>(.*?)</invoke>', re.I | re.S)
_NAME_ATTR_RE = re.compile(r'name\s*=\s*["\']?([^"\'<>\s=]+)', re.I)
_PARAM_OPEN_RE = re.compile(r'<parameter\b([^>]*)>', re.I)
_FENCE_TAIL_RE = re.compile(r'(```xml|```)\s*$', re.I)
_FENCE_HEAD_RE = re.compile(r'^\s*(```xml|```)[ \t]*\n?', re.I)


def _parse_params(body: str) -> dict:
    """从 invoke 内部解析参数（开标签整体捕获，避免属性越界）"""
    params, pos = {}, 0
    while True:
        om = _PARAM_OPEN_RE.search(body, pos)
        if not om:
            break
        nm = re.search(r'name\s*=\s*["\']?([^"\'<>\s=]+)', om.group(1))
        cm = re.search(r'</parameter>', body[om.end():], re.I)
        if not (nm and cm):
            break
        params[nm.group(1).strip()] = body[om.end():om.end() + cm.start()]
        pos = om.end() + cm.end()
    return params


def parse_invokes_xml(xml: str) -> list:
    """从 XML 片段解析 [{name, arguments(JSON字符串)}]，兼容 invoke 标签携带额外属性"""
    calls = []
    for m in _INVOKE_RE.finditer(xml):
        nm = _NAME_ATTR_RE.search(m.group(1))
        calls.append({"name": (nm.group(1) if nm else "").strip(),
                      "arguments": json.dumps(_parse_params(m.group(2)), ensure_ascii=False)})
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
    for pat in (r'<tool_calls>(.*?)</tool_calls>', r'<tool_call>(.*?)</tool_call>'):
        for m in re.finditer(pat, text, re.I | re.S):
            for c in parse_invokes_xml(m.group(1)):
                spans.append((m.start(), m.end(), c))
    for m in _INVOKE_RE.finditer(text):  # 裸 <invoke>（无包裹）
        if any(s <= m.start() < e for s, e, _ in spans):
            continue
        nm = _NAME_ATTR_RE.search(m.group(1))
        spans.append((m.start(), m.end(), {"name": (nm.group(1) if nm else "").strip(),
                                           "arguments": json.dumps(_parse_params(m.group(2)),
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
    clean, calls, last = [], [], 0
    for s, e, c in spans:
        if s < last:  # 与前一个 span 重叠（嵌套），跳过
            continue
        piece = _FENCE_TAIL_RE.sub('', text[last:s], count=1)  # 块前 ```xml 围栏
        clean.append(piece)
        last = e
        calls.append({"id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                      "function": {"name": c["name"], "arguments": c["arguments"]}})
    tail = _FENCE_HEAD_RE.sub('', text[last:], count=1)  # 块后 ``` 围栏
    clean.append(tail)
    return "".join(clean).strip(), calls


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

    def _hold(self, buf):
        """正文尾部疑似未完成的 '<tag...' 一律扣住，等下一个分片再判断"""
        i = buf.rfind("<")
        if i != -1 and 0 < len(buf) - i <= 64 and ">" not in buf[i:]:
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
                idx = [i for i in (self.buf.find("<tool_calls>"),
                                   self.buf.find("<tool_call>"),
                                   self.buf.find("<invoke")) if i != -1]
                if sc_pos is not None:
                    idx.append(sc_pos)
                if not idx:
                    hold = self._hold(self.buf)
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
                    self.buf = self.buf[cut:]
                if sc_pos is not None and cut == sc_pos and \
                        not self.buf.startswith(("<tool_calls>", "<tool_call>", "<invoke")):
                    m, call = sc
                    out_t.append(self._evt(call))
                    self._after_block = True
                    self.buf = self.buf[m.end():]
                elif self.buf.startswith("<tool_calls>"):
                    self.mode, self.acc = "tc", ""
                    self.buf = self.buf[len("<tool_calls>"):]
                elif self.buf.startswith("<tool_call>"):
                    self.mode, self.acc = "single", ""
                    self.buf = self.buf[len("<tool_call>"):]
                else:  # <invoke...：closer 到达后整体解析
                    self.mode, self.acc = "inv", ""
            else:
                closer = {"tc": "</tool_calls>", "single": "</tool_call>",
                          "inv": "</invoke>"}[self.mode]
                end = self.buf.find(closer)
                if end == -1:
                    self.acc += self.buf
                    self.buf = ""
                    break
                self.acc += self.buf[:end]
                self.buf = self.buf[end + len(closer):]
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
    """请求上游并逐个产出解析后的 OpenAI 原生 chunk dict"""
    files = [(k, (None, str(v))) for k, v in fields.items()]
    cookies = load_config()["cookies"]
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
    headers = {"Cookie": cookie_header, "Origin": UPSTREAM, "Referer": UPSTREAM + "/"}
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", UPSTREAM + "/site/ai/compose_chat",
                                 files=files, headers=headers) as resp:
            ct = resp.headers.get("content-type", "")
            print("[chat2api] upstream status:", resp.status_code, "ct:", ct, flush=True)
            if "text/event-stream" not in ct:
                # 上游可能直接返回 JSON（如审核拦截时给出整段答复）
                body = (await resp.aread()).decode("utf-8", "ignore")
                try:
                    err = json.loads(body)
                except json.JSONDecodeError:
                    err = {}
                answer = (err.get("d") or {}).get("answer")
                if answer:
                    yield {"choice": {"delta": {"content": answer, "role": "assistant"}, "index": 0,
                                      "finish_reason": "stop"}}
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
                        yield {"usage": chunk["usage"]}
                    for choice in chunk.get("choices", []):
                        yield {"choice": choice}


def make_chunk(cid: str, model: str, delta: dict, finish=None, usage=None) -> str:
    obj = {
        "id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage:
        obj["usage"] = usage
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


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
    return json.dumps({
        "id": cid, "object": "chat.completion", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }, ensure_ascii=False)


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

    if not stream:
        content, reasoning, usage = "", "", None
        async for item in stream_upstream_chunks(fields, want_usage):
            if "usage" in item:
                usage = item["usage"]
            elif "choice" in item:
                delta = item["choice"].get("delta", {})
                content += delta.get("content") or ""
                reasoning += delta.get("reasoning_content") or ""
        text, calls = extract_tool_calls(content, known_tools)
        if calls:
            _log_tools(calls)
        return JSONResponse(json.loads(make_final(cid, model, text, reasoning, usage, calls)))

    async def sse():
        parser = ToolCallStreamParser(known_tools)
        # 首块: role
        yield make_chunk(cid, model, {"role": "assistant", "content": ""})
        try:
            async for item in stream_upstream_chunks(fields, want_usage):
                if "usage" in item:
                    yield make_chunk(cid, model, {}, finish=None, usage=item["usage"])
                elif "choice" in item:
                    delta = item["choice"].get("delta", {}) or {}
                    finish = item["choice"].get("finish_reason")
                    text = delta.get("content") or ""
                    reasoning = delta.get("reasoning_content") or ""
                    if reasoning:
                        yield make_chunk(cid, model, {"reasoning_content": reasoning})
                    if text:
                        out_c, out_t = parser.feed(text)
                        if out_c:
                            yield make_chunk(cid, model, {"content": out_c})
                        for ev in out_t:
                            yield make_chunk(cid, model, {"tool_calls": [ev]})
                    if finish:
                        yield make_chunk(cid, model, {},
                                         finish="tool_calls" if parser.found else finish)
            left = parser.flush()
            if left:
                yield make_chunk(cid, model, {"content": left})
        except HTTPException as e:
            yield f"data: {json.dumps({'error': {'message': e.detail, 'type': 'upstream_error'}})}\n\n"
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
