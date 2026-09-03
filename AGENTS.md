# AGENTS.md — agent 工作提示

## 删除规则（强制）
- 不要直接删除文件。任务结束后单独给出 PowerShell 命令由用户手动执行，否则会导致 IDE 崩溃

## 项目一句话
北科大 AI 助手（chat.ustb.edu.cn，DeepSeek）→ OpenAI 兼容 API 本地网关（`http://127.0.0.1:8787/v1`）。核心价值是**工具调用桥**：上游不支持 function calling，靠提示词约定 XML 格式，代理层实时转换为标准 `tool_calls`。

## 架构与文件
| 文件 | 职责 |
|---|---|
| `chat2api.py` | 核心：FastAPI 转发 + `ToolCallStreamParser`（XML→tool_calls）+ `ReasoningXMLFilter`（思考区 XML 剥离）+ 消息归一 |
| `tray.py` | 系统托盘：uvicorn 内线程，服务掉线自动拉起，单实例 mutex |
| `tui.py` | TUI 控制台，`[h]` 可转交托盘 |
| `cli.py` | 命令行：`python cli.py cookie` / `key generate|list|revoke`（热重载） |
| `dashboard.py` | `/dashboard` 可视化 + `/stats` JSON |
| `config.json` | 运行时生成，存 cookies + api_key（勿提交） |

## 常用命令
```powershell
# 单元测试
.\.venv_chat2api\Scripts\python.exe tests\test_tools_unit.py

# 真实请求泄露/桥接回归（每轮 6 用例并发，打真实上游）
.\.venv_chat2api\Scripts\python.exe tests\test_stream_leak.py --rounds 3

# 重启服务（改 chat2api.py 后必须做）
Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" |
Stop-Process -Id <pids> -Force
Start-Process -FilePath ".\.venv_chat2api\Scripts\pythonw.exe" -ArgumentList "tray.py" -WorkingDirectory "F:\Chat2API"
```

## 必踩坑
1. **改完服务不自动重载**。uvicorn 无 reload；旧进程用旧代码，表现就是「明明修了还是泄露」。对比进程 CreationDate 与文件 LastWriteTime，改完必须重启。
2. **同一文件多次编辑必须串行**：一条消息里对同一文件发多个 SearchReplace，只有最后一条落盘。每步编辑后用 Grep/Read 验证。
3. **流式过滤器设计约束**（`ReasoningXMLFilter` / `ToolCallStreamParser`）：
   - 标签匹配不能用 `\b` 收尾——分片在 `<tool_call|s>` 处断开时 `\b` 是词边界，会把半截前缀当完整标签（`s>` 成孤儿、闭合永不匹配、后续整段被吞）。用 `(?=[\s>/])` 前瞻。
   - `_hold` 扣留窗口上界不能 `-1`；`flush()` 对未闭合 pending 必须丢弃不能吐出。
   - 修改后跑穷举切分测试：两片全量 + 三片抽样的所有断点位置（test_tools_unit.py 已有样板）。
4. **pythonw/无控制台**：`sys.stdout` 为 None，uvicorn 须 `log_config=None`；诊断靠 `server_error.log` / `tray.log`。
5. **上游限制**：user/assistant 严格交替（连续同角色 4028）；`tool` 角色转 user 文本回传；内容审核误拦时原样透传勿重试。
6. **端口 8787 被占**：托盘发现服务在线就不自己拉，所以杀旧进程时要把托盘一起杀，避免它守护旧服务。

## 测试约定
- 新解析/过滤逻辑：先在 `tests/test_tools_unit.py` 加确定性用例（含穷举切分），全过再上真实请求。
- 真实请求验证用 `test_stream_leak.py`，判据是 reasoning/content 全文无 `<tool_calls>/<tool_call>/<invoke` 原文、tool_calls 事件 arguments 合法 JSON。
- 临时调试脚本放 `tests/_debug_cases.py`，不并入正式测试。

## 上游能力测试结果
| 测试项 | 实测值 | 备注 |
|---|---|---|
| 上下文长度（标准） | ≥ 209214 字符 (~72883 prompt_tokens) | 251982 字符时 502 失败 |
| 输出窗口 | ~61725 tokens (104170 字符) | 上游自然结束，未达 131072 目标 |
| 输出速度 | ~111.3 tok/s | 555s 输出 61725 tokens |

> 测试命令: `python tests/test_context_length.py` / `python tests/test_output_length.py`
