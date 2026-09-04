# -*- coding: utf-8 -*-
"""
本地 Dashboard: 吐词速度 / 上下文用量 / 基础 API 参数展示

- GET /stats      JSON 统计数据（供脚本/外部工具轮询）
- GET /dashboard  可视化面板（深色卡片 + tokens/s 迷你折线图 + 最近请求表，1s 自动刷新）

数据由 chat2api.py 在每次 /v1/chat/completions 完成时调用 record_request() 采集；
usage 缺失时 tokens 按字符数估算（约 2.5 字符/token，中英混合粗略值）并标记 estimated。
"""
import json
import os
import sys
import threading
import time
import atexit
from collections import deque

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

MAX_CONTEXT_TOKENS = 65536  # 展示用参考上限（上游真实上限用 tests/test_context_length.py 探测）

_LOCK = threading.Lock()
_BASE_DIR = os.environ.get("CHAT2API_HOME") or os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
_STATS_FILE = os.path.join(_BASE_DIR, "stats.json")


def _save_stats():
    """持久化统计数据到磁盘（服务重启保留）"""
    with _LOCK:
        d = {
            "started": _STATS["started"],
            "total_requests": _STATS["total_requests"],
            "total_errors": _STATS["total_errors"],
            "total_prompt_tokens": _STATS["total_prompt_tokens"],
            "total_completion_tokens": _STATS["total_completion_tokens"],
            "recent": list(_STATS["recent"]),
            "tps_series": list(_STATS["tps_series"]),
            "session_cache": _STATS["session_cache"],
        }
    try:
        with open(_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def _load_stats():
    """从磁盘加载持久化的统计数据"""
    try:
        with open(_STATS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return
    with _LOCK:
        _STATS["started"] = d.get("started", time.time())
        _STATS["total_requests"] = d.get("total_requests", 0)
        _STATS["total_errors"] = d.get("total_errors", 0)
        _STATS["total_prompt_tokens"] = d.get("total_prompt_tokens", 0)
        _STATS["total_completion_tokens"] = d.get("total_completion_tokens", 0)
        for r in d.get("recent", []):
            _STATS["recent"].append(r)
        for t in d.get("tps_series", []):
            _STATS["tps_series"].append(t)
        _STATS["session_cache"] = d.get("session_cache", {"ts": 0, "state": "unknown"})


_STATS = {
    "started": time.time(),
    "total_requests": 0,
    "total_errors": 0,
    "total_prompt_tokens": 0,
    "total_completion_tokens": 0,
    "recent": deque(maxlen=50),   # 每请求摘要
    "tps_series": deque(maxlen=120),  # (ts, tokens_per_s)
    "session_cache": {"ts": 0, "state": "unknown"},
}
_load_stats()
atexit.register(_save_stats)


def note_session(state: str, ttl: int = 60):
    """缓存登录状态检测结果，避免 /stats 轮询频繁打上游"""
    with _LOCK:
        _STATS["session_cache"] = {"ts": time.time(), "state": state}


def _get_session():
    with _LOCK:
        c = _STATS["session_cache"]
    if time.time() - c["ts"] > 60:
        try:
            from chat2api import load_config  # 延迟导入避免循环
            from cli import verify_cookies
            ck = load_config().get("cookies", {})
            if not (ck.get("easy_session") and ck.get("cookie_vjuid_login")):
                note_session("unconfigured")
            else:
                note_session("valid" if verify_cookies(ck, quiet=True) else "expired")
        except Exception:
            note_session("unknown")
        with _LOCK:
            c = _STATS["session_cache"]
    return c["state"]


def record_request(model: str, stream: bool, prompt_chars: int, prompt_tokens,
                   completion_tokens, duration_s: float, first_token_s,
                   status: int):
    """每次对话完成后调用；tokens 缺失时按字符估算"""
    now = time.time()
    estimated = False
    if prompt_tokens is None:
        prompt_tokens = int(prompt_chars / 2.5)
        estimated = True
    if completion_tokens is None:
        completion_tokens = 0  # 由调用方传入估算值时无需再估
    tps = completion_tokens / duration_s if duration_s > 0.2 else None
    with _LOCK:
        _STATS["total_requests"] += 1
        if status != 200:
            _STATS["total_errors"] += 1
        _STATS["total_prompt_tokens"] += prompt_tokens
        _STATS["total_completion_tokens"] += completion_tokens
        _STATS["recent"].append({
            "ts": now, "model": model, "stream": stream,
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "duration_s": round(duration_s, 1), "first_token_s":
                round(first_token_s, 1) if first_token_s else None,
            "tps": round(tps, 1) if tps else None, "status": status,
            "estimated": estimated,
        })
        if tps:
            _STATS["tps_series"].append([now, round(tps, 1)])


@router.get("/stats")
async def stats():
    with _LOCK:
        d = {
            "uptime_s": int(time.time() - _STATS["started"]),
            "total_requests": _STATS["total_requests"],
            "total_errors": _STATS["total_errors"],
            "total_prompt_tokens": _STATS["total_prompt_tokens"],
            "total_completion_tokens": _STATS["total_completion_tokens"],
            "total_context_tokens": _STATS["total_prompt_tokens"] + _STATS["total_completion_tokens"],
            "recent": list(_STATS["recent"]),
            "tps_series": list(_STATS["tps_series"]),
        }
    d["session"] = _get_session()
    d["max_context_tokens_ref"] = MAX_CONTEXT_TOKENS
    return d


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>USTB Chat2API Dashboard</title>
<style>
  body{background:#111827;color:#e5e7eb;font-family:"Microsoft YaHei",sans-serif;margin:0;padding:20px}
  h1{font-size:18px;margin:0 0 12px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;max-width:960px}
  .card{background:#1f2937;border-radius:10px;padding:14px}
  .card .k{font-size:12px;color:#9ca3af}
  .card .v{font-size:26px;font-weight:700;margin-top:4px}
  .ok{color:#34d399}.warn{color:#fbbf24}.bad{color:#f87171}
  canvas{background:#1f2937;border-radius:10px;margin-top:12px;max-width:960px;width:100%}
  table{border-collapse:collapse;margin-top:12px;font-size:12px;width:100%;max-width:960px}
  th,td{border-bottom:1px solid #374151;padding:5px 8px;text-align:left;white-space:nowrap}
  th{color:#9ca3af}
  .est{color:#9ca3af;font-size:10px}
</style>
</head>
<body>
<h1>USTB Chat2API Dashboard <span id="dot" class="warn">●</span>
  <span id="sess" style="font-size:12px;color:#9ca3af"></span>
  <button onclick="doRestart()" style="float:right;background:#374151;color:#e5e7eb;border:0;border-radius:6px;padding:6px 14px;cursor:pointer;font-size:12px">重启服务</button></h1>
<div class="grid">
  <div class="card"><div class="k">吐词速度（最近请求）</div><div class="v"><span id="tps">--</span> tok/s</div></div>
  <div class="card"><div class="k">最近上下文用量</div><div class="v"><span id="ctx">--</span> tok</div></div>
  <div class="card"><div class="k">累计 tokens (入/出)</div><div class="v" style="font-size:18px"><span id="tot">--</span></div></div>
  <div class="card"><div class="k">累计 token (总)</div><div class="v" style="font-size:18px"><span id="totctx">--</span></div></div>
  <div class="card"><div class="k">请求总数 / 错误</div><div class="v" style="font-size:18px"><span id="req">--</span></div></div>
  <div class="card"><div class="k">服务运行时长</div><div class="v" style="font-size:18px"><span id="up">--</span></div></div>
</div>
<canvas id="chart" width="960" height="140"></canvas>
<table>
  <thead><tr><th>时间</th><th>模型</th><th>模式</th><th>上下文 tok</th><th>输出 tok</th>
    <th>耗时 s</th><th>首字 s</th><th>tok/s</th><th>状态</th></tr></thead>
  <tbody id="rows"></tbody>
</table>
<script>
const $ = id => document.getElementById(id);
function fmtT(s){const h=Math.floor(s/3600),m=Math.floor(s%3600/60);return h?h+"h"+m+"m":m?m+"m"+Math.floor(s%60)+"s":Math.floor(s)+"s"}
function fmtTok(n){return n>=1e6?(n/1e6).toFixed(2)+"M":n>=1e3?(n/1e3).toFixed(1)+"K":String(n)}
function draw(series){
  const c=$("chart"),x=c.getContext("2d");x.clearRect(0,0,c.width,c.height);
  x.fillStyle="#374151";x.font="11px sans-serif";x.fillText("tokens/s 时间序列",8,14);
  if(!series.length)return;
  const vs=series.map(p=>p[1]),mx=Math.max(...vs,10),W=c.width-20,H=c.height-40;
  x.strokeStyle="#34d399";x.lineWidth=2;x.beginPath();
  series.forEach((p,i)=>{const px=10+i/(Math.max(series.length-1,1))*W,
    py=c.height-20-(p[1]/mx)*H;i?x.lineTo(px,py):x.moveTo(px,py)});
  x.stroke();
  x.fillStyle="#6b7280";x.fillText(mx.toFixed(1)+" tok/s",10,c.height-4);
}
async function doRestart(){
  const key=prompt("请输入 API Key 以重启服务：");
  if(!key)return;
  try{
    const r=await fetch("/restart",{method:"POST",headers:{Authorization:"Bearer "+key}});
    const d=await r.json().catch(()=>({}));
    alert(r.ok?(d.detail||"重启已受理"):"重启失败: HTTP "+r.status+" "+(d.detail||""));
  }catch(e){alert("请求失败: "+e)}
}
async function tick(){
  try{
    const s=await (await fetch("/stats")).json();
    const dot=$("dot"),sess=$("sess");
    dot.className={valid:"ok",unconfigured:"warn",expired:"warn",unknown:"warn"}[s.session]||"warn";
    dot.style.color={valid:"#34d399",expired:"#fbbf24",unconfigured:"#fbbf24",unknown:"#9ca3af"}[s.session];
    sess.textContent="上游会话: "+{valid:"已登录",expired:"已失效",unconfigured:"未登录",unknown:"检测中"}[s.session];
    const last=[...s.recent].reverse().find(r=>r.tps)||{};
    $("tps").textContent=last.tps!=null?last.tps:"--";
    const lc=[...s.recent].reverse()[0];
    $("ctx").textContent=lc?lc.prompt_tokens:"--";
    $("tot").textContent=fmtTok(s.total_prompt_tokens)+" / "+fmtTok(s.total_completion_tokens);
    $("totctx").textContent=fmtTok(s.total_context_tokens);
    $("req").textContent=s.total_requests+" / "+s.total_errors;
    $("up").textContent=fmtT(s.uptime_s);
    draw(s.tps_series);
    $("rows").innerHTML=[...s.recent].reverse().slice(0,12).map(r=>
      `<tr><td>${new Date(r.ts*1000).toLocaleTimeString()}</td><td>${r.model}</td>
       <td>${r.stream?"流式":"同步"}</td><td>${r.prompt_tokens}${r.estimated?' <span class="est">est</span>':""}</td>
       <td>${r.completion_tokens}</td><td>${r.duration_s}</td><td>${r.first_token_s??"-"}</td>
       <td>${r.tps??"-"}</td><td class="${r.status==200?"ok":"bad"}">${r.status}</td></tr>`).join("");
  }catch(e){$("sess").textContent="统计获取失败: "+e}
}
tick();setInterval(tick,1000);
</script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return _DASHBOARD_HTML
