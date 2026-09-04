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
import sys
import threading
import traceback
from datetime import datetime
import httpx
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, JSONResponse

from .utils import (load_config, get_host, log_error, log_tools, content_to_text,
                    verify_key, DEBUG_LOG, angle_bracket_check)
from . import dashboard  # noqa: E402
from .dashboard import record_request  # noqa: E402  (采集 /stats 数据)

# ===== 配置 =====
UPSTREAM = "http://chat.ustb.edu.cn"
COMPOSE_ID = "3"          # DeepSeek 应用 id
MODEL_NAME = "DeepSeek"   # 上游模型名
PORT = 8787











# ===== 工具调用桥（prompt-based function calling） =====
# 上游 DeepSeek 应用不支持 OpenAI function calling。客户端(Trae等)下发 tools 时，
# 注入格式说明，并把模型输出中的工具调用 XML 转换回标准 tool_calls 字段。

# ===== 内核（kernel.py）：解析器/工具桥，可进程内热重载 =====
from . import kernel  # noqa: E402  (外壳经 kernel.X 动态引用以支持热重载)
from .kernel import (format_tools_prompt, render_tool_calls_xml, extract_tool_calls,  # noqa: F401
                     parse_invokes_xml, ReasoningXMLFilter, ToolCallStreamParser)  # noqa: F401



def check_auth(request: Request):
    """校验 Bearer key：固定简单 key 或 api_keys.json 的哈希 key"""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token or not verify_key(token):
        raise HTTPException(status_code=401, detail="Invalid API key")


# ---------- 在线热更新：内核热重载 / 对话间隙感知的进程重启 ----------

import importlib

RESTART_CALLBACK = None  # 宿主（托盘/TUI）注册的重启实现；None 时走进程自我替换兜底

_IDLE_LOCK = threading.Lock()
_IDLE = {"inflight": 0}                          # 活跃对话请求数
_PENDING = {"reload": False, "restart": False}   # 待执行的热更新/重启（等对话间隙）


def inflight_bump(delta: int) -> int:
    with _IDLE_LOCK:
        _IDLE["inflight"] = max(0, _IDLE["inflight"] + delta)
        return _IDLE["inflight"]


def get_inflight() -> int:
    """当前活跃对话请求数（供 /stats 与日志展示）"""
    with _IDLE_LOCK:
        return _IDLE["inflight"]


class InFlightMiddleware:
    """精确跟踪活跃对话：最后一个响应体块发送完才递减。
    流式响应以流被完整消费为结束——保证热更新只在「对话间隙」执行，
    绝不打断进行中的对话；进行中的响应绑定旧 kernel 实例，不受影响。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/v1/chat/completions":
            await self.app(scope, receive, send)
            return
        inflight_bump(1)
        closed = {"flag": False}

        async def send_wrap(message):
            if (message["type"] == "http.response.body"
                    and not message.get("more_body", False) and not closed["flag"]):
                closed["flag"] = True
                inflight_bump(-1)
            await send(message)

        try:
            await self.app(scope, receive, send_wrap)
        finally:
            if not closed["flag"]:
                inflight_bump(-1)


def _do_kernel_reload():
    """进程内热重载内核（解析器/工具桥）。进行中的请求用旧实例不受影响。"""
    importlib.reload(kernel)
    return True


def _idle_watcher():
    """间隙 watcher：活跃请求归零后执行挂起的内核热重载 / 整进程重启（每秒检查）"""
    while True:
        time.sleep(1)
        try:
            do_reload = do_restart = False
            with _IDLE_LOCK:
                if _IDLE["inflight"] == 0:
                    do_reload, _PENDING["reload"] = _PENDING["reload"], False
                    do_restart, _PENDING["restart"] = _PENDING["restart"], False
            if do_reload:
                try:
                    _do_kernel_reload()
                    log_warn("内核热重载完成（对话间隙执行）")
                except Exception:
                    log_error("内核热重载失败（旧内核继续服务）:\n" + traceback.format_exc())
            if do_restart:
                cb = RESTART_CALLBACK
                if callable(cb):
                    threading.Thread(target=cb, daemon=True, name="restart").start()
                else:
                    time.sleep(0.5)  # 让挂起的 HTTP 应答先送达
                    _self_exec()
        except Exception:
            pass


def request_restart() -> bool:
    """请求整进程重启（对话间隙感知：活跃对话结束后自动执行）"""
    with _IDLE_LOCK:
        _PENDING["restart"] = True
    return True


def _self_exec():
    """进程自我替换（裸跑 python chat2api.py 时用；托盘/TUI 托管时由宿主回调处理）"""
    os.execv(sys.executable, [sys.executable] + sys.argv)


app = FastAPI(title="USTB DeepSeek chat2api")
app.add_middleware(InFlightMiddleware)
threading.Thread(target=_idle_watcher, daemon=True, name="idle-watcher").start()
app.include_router(dashboard.router)  # /stats + /dashboard 本地仪表盘


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底: 未预期异常记录到 server_error.log 并把原因返回客户端"""
    log_error(f"{request.method} {request.url.path}")
    return JSONResponse(status_code=500, content={"detail": f"chat2api 内部错误: {exc.__class__.__name__}: {exc}"})





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
            content = (content + "\n" if content else "") + kernel.render_tool_calls_xml(m["tool_calls"])
            norm.append({"role": "assistant", "content": content})
        elif role in ("system", "user", "assistant"):
            norm.append({"role": role, "content": content})
    system_prompts = [m["content"] for m in norm if m["role"] == "system" and m["content"]]
    if tools:
        system_prompts.append(kernel.format_tools_prompt(tools))
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


def make_chunk(cid: str, model: str, delta: dict, finish=None, usage=None) -> str:
    obj = {
        "id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage:
        obj["usage"] = usage
    return f"data: {_json_guard(json.dumps(obj, ensure_ascii=False))}\n\n"


def _json_guard(s: str) -> str:
    """在序列化后的 JSON 文本层把 < > 转为 \\uXXXX 转义：合规 JSON 解析器解回原字符
    （客户端无感知），而直接扫描 SSE 原文的客户端不再见到裸尖括号——
    防止 arguments 中携带的 XML 字符串被下游误解析为标签（H40 嵌套 XML 教训）。
    注意必须在 json.dumps 之后替换；提前改 arguments 会被 dumps 二次转义损坏数据。"""
    return s.replace("<", "\\u003c").replace(">", "\\u003e")


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
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
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
        rfilter = kernel.ReasoningXMLFilter()  # 思考区 XML 剥离（与非流式路径保持一致）
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
        text, calls = kernel.extract_tool_calls(content, known_tools)
        if calls:
            log_tools(calls)
        angle_bracket_check(text, "content", {})      # 尖括号预警（调试阶段）
        angle_bracket_check(reasoning, "reasoning", {})
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
        parser = kernel.ToolCallStreamParser(known_tools)
        rfilter = kernel.ReasoningXMLFilter()  # 思考区 XML 剥离（Trae <> 直出治理）
        first_tok = None     # 首 token 延迟 s
        out_chars = 0        # 输出字符数（usage 缺失时估算用）
        usage_pt = usage_ct = None
        status = 200
        warn_c, warn_r = {}, {}  # 尖括号预警累计状态（content / reasoning 各一）
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
                            angle_bracket_check(r_txt, "reasoning", warn_r)
                            yield make_chunk(cid, model, {"reasoning_content": r_txt})
                    if text:
                        out_chars += len(text)
                        out_c, out_t = parser.feed(text)
                        if out_c:
                            angle_bracket_check(out_c, "content", warn_c)
                            yield make_chunk(cid, model, {"content": out_c})
                        for ev in out_t:
                            yield make_chunk(cid, model, {"tool_calls": [ev]})
                    if finish:
                        held_finish = finish  # 最后再发，保证 flush 事件先于结束帧
            r_rest = rfilter.flush()
            if r_rest:
                angle_bracket_check(r_rest, "reasoning", warn_r)
                yield make_chunk(cid, model, {"reasoning_content": r_rest})
            left, left_ev = parser.flush()
            for ev in left_ev:
                yield make_chunk(cid, model, {"tool_calls": [ev]})
            if left:
                out_chars += len(left)
                angle_bracket_check(left, "content", warn_c)
                yield make_chunk(cid, model, {"content": left})
            if held_finish or parser.found:
                yield make_chunk(cid, model, {},
                                 finish="tool_calls" if parser.found else held_finish)
            if parser.events:
                log_tools(parser.events)
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


@app.post("/restart")
async def restart(request: Request):
    """整进程重启（外壳级更新）。对话间隙感知：有活跃对话时挂起，
    全部结束后自动替换进程。需 API Key 鉴权。"""
    check_auth(request)
    request_restart()
    n = get_inflight()
    if n == 0:
        return JSONResponse({"ok": True, "detail": "无活跃对话，进程即将替换重启"})
    return JSONResponse({"ok": True, "detail": f"等待对话间隙: {n} 个活跃对话结束后自动重启"})


@app.post("/reload")
async def reload_kernel(request: Request):
    """进程内热重载内核 kernel.py（解析器/工具桥），不断连接、不换进程。
    对话间隙感知：有活跃对话时挂起，全部结束后自动热重载。需 API Key 鉴权。"""
    check_auth(request)
    n = get_inflight()
    if n > 0:
        with _IDLE_LOCK:
            _PENDING["reload"] = True
        return JSONResponse({"ok": True, "detail": f"等待对话间隙: {n} 个活跃对话结束后自动热重载"})
    try:
        _do_kernel_reload()
        return JSONResponse({"ok": True, "detail": "内核热重载完成"})
    except Exception as e:
        log_error("内核热重载失败:\n" + traceback.format_exc())
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": f"热重载失败（旧内核继续服务）: {e}"})


def serve():
    """headless 服务启动入口（Linux/服务器部署默认入口，供 entry.py 调用）"""
    import uvicorn
    cfg = load_config()
    print(f"chat2api 启动: http://{get_host()}:{PORT}/v1")
    print(f"API Key: {cfg['api_key']}  |  Cookies: {'已配置' if cfg['cookies'].get('easy_session') else '未配置，请运行 ustb-chat2api cli cookie'}")
    uvicorn.run(app, host=get_host(), port=PORT, log_level="warning")


if __name__ == "__main__":
    serve()
