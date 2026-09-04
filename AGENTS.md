# AGENTS.md — agent 工作提示
这是一个偏应用的项目，不是纯 agent 项目。面向我本人和第一开发者的提示及时写入AGENTS.md，面向用户和第二开发者的提示及时写入README.md。AGENTS.md的语言风格简洁凝练，以节省Token为目标。
TODO.md是待办列表，项目中会涉及多个Agent的交互，即使写入和清理相关内容，并且每次执行操作都要加上待修改和修改中的文字标识。完成则直接压缩或者写入其他文件以节省token
## 删除规则（强制）
- 不要直接删除文件。任务结束后单独给出 PowerShell 命令由用户手动执行，否则会导致 IDE 崩溃

## 项目一句话
北科大 AI 助手（chat.ustb.edu.cn，DeepSeek）→ OpenAI 兼容 API 本地网关（`http://127.0.0.1:8787/v1`）。核心价值是**工具调用桥**：上游不支持 function calling，靠提示词约定 XML 格式，代理层实时转换为标准 `tool_calls`。

## 架构与文件
| 文件 | 职责 |
|---|---|
| `kernel.py` | **内核**：解析器（`ToolCallStreamParser`/`ReasoningXMLFilter`）+ 工具桥 prompt + 尖括号预警。纯逻辑，支持进程内热重载（`importlib.reload`） |
| `chat2api.py` | **外壳**：FastAPI 路由/鉴权/转发 + in-flight 跟踪 + `/reload` `/restart` 端点 + 间隙 watcher |
| `tray.py` | 系统托盘：uvicorn 内线程，服务掉线自动拉起，单实例 mutex |
| `tui.py` | TUI 控制台，`[h]` 可转交托盘 |
| `cli.py` | 命令行：`cookie` / `key generate|list|revoke` / `restart`（整进程）/ `reload`（内核热重载） |
| `dashboard.py` | `/dashboard` 可视化（含热重载/重启按钮）+ `/stats`（含 inflight） |
| `entry.py` | 单 EXE/DEB 统一入口：平台自适应（Win=托盘, Linux=serve）+ argv 分发 |
| `release.py` / `release-linux.py` | Windows EXE / Linux DEB 构建 |
| `config.json` | 运行时生成，存 cookies + api_key（勿提交） |

## 内核/外壳分离（热更新架构）
- 内核与外壳分文件；**外壳调用内核符号必须写 `kernel.X` 动态引用**，严禁 from-import 后直接调用（那样热重载不生效，reload 后仍跑旧绑定）。
- `/reload`：进程内热重载内核，毫秒级、不断连接；进行中的响应用旧实例不受影响。
- `/restart`：整进程替换（外壳级变更）。两者均**对话间隙感知**：in-flight>0 时挂起，最后一个响应体块发完归零后由 watcher 自动执行。
- in-flight 由 `InFlightMiddleware`（纯 ASGI，按最后一个 body 块递减）精确统计，`/stats` 暴露 `inflight`。

## 常用命令
```powershell
# 单元测试（包版 + 生产扁平版各跑一遍）
.\.venv_chat2api\Scripts\python.exe tests\test_tools_unit.py
.\.venv_chat2api\Scripts\python.exe tests\test_tools_unit.py --flat

# 真实请求泄露/桥接回归（每轮 6 用例并发，打真实上游）
.\.venv_chat2api\Scripts\python.exe tests\test_stream_leak.py --rounds 3

# 真实场景 sub-agent 稳定性（文档/分支维护任务 agentic loop，打真实上游）
.\.venv_chat2api\Scripts\python.exe tests\test_subagent_stability.py --rounds 2

# 重启服务（改 chat2api.py 后必须做；在线重启，托盘/TUI 整进程换新加载最新代码）
.\.venv_chat2api\Scripts\python.exe cli.py restart
# 其他入口: 托盘菜单「重启服务」/ TUI [9] / POST /restart（需 Bearer key）/ Dashboard 按钮
# 服务离线时 cli restart 会自动拉起托盘；手动兜底: 杀 python(w) 后 Start-Process pythonw tray.py

# 打包 release zip（dist/USTB-Chat2API-v*.zip）
.\.venv_chat2api\Scripts\python.exe release.py

# 打包单文件 EXE（dist/exe/USTB-Chat2API.exe，需 pyinstaller）
.\.venv_chat2api\Scripts\python.exe release.py --exe

# EXE 冒烟（不影响线上服务）
.\dist\exe\USTB-Chat2API.exe cli key list
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
7. **venv pythonw 是启动器**：`.venv_chat2api\Scripts\pythonw.exe`（anaconda venv）会派生子进程 `D:\anaconda3\pythonw.exe` 跑真实代码，两者命令行相同、父子同秒创建——任务管理器里「两个 pythonw」是正常形态，不是双实例；只杀启动器会连带服务退出。

## 尖括号预警（调试阶段）

服务端对下发客户端的正文/思考流做尖括号监控（`angle_bracket_check`，两版均有）：命中泄露模式（`<tool_calls`/`</invoke`/`｜DSML｜` 等）立即告警，或累计尖括号 ≥200 / 密度 >8% 告警，每响应每通道最多 1 次。落盘 `server_warn.log`（扁平版在根目录，包版在 logs/）。只记录不拦截，供排查泄露用。

## 措辞规避（上游内容审核误拦）

上游有**动态内容审核**：命中时只回固定话术「抱歉，我无法回答这个问题，让我们换个话题再聊聊吧」（1~3s 秒回；同文本时拦时不拦，属上游随机行为，不是代码 bug）。发给上游模型的一切文本（对话消息、Task 子代理的 query、测试用例 prompt）都按下面方式规避：

| 高风险表述 | 改用 |
|---|---|
| 执行/运行命令、跑脚本 | 「在终端验证」「让测试通过」等目标式描述 |
| 删除/覆盖/注入/攻击/绕过/破解 | 中性词：清理、修正、排查 |
| 读取文件内容并原样输出 | 「查看 XX 的实现」「定位 XX 逻辑」 |
| 一长串命令式指令 | 拆成普通编程问答口吻的短句 |

遇到拦截时：人工对话换措辞重问（勿原样重发）；测试脚本中标记 SKIP 透传、勿自动重试轰炸（样板见 test_stream_leak.py 的 `REJECT_RE`）。TOOL_INSTRUCTION 末尾已内置同样的【安全提醒】，随 tools 注入下发。

## 嵌套 XML 防护（H40 教训，强制）

参数值里的 `<tool_calls>`/`</invoke>` 等是**字面文本**，不是结构标签：
- 配对一律走 `<parameter>` 深度状态机：`_iter_wrapper_spans` / `_iter_invoke_spans` / `_parse_params`；流式闭合前先 `_param_depth()` 校验，值内闭合标签跳过找真闭合。**严禁退回裸正则非贪婪截断**。
- 输出层 `_json_guard()` 在 `json.dumps` **之后**把 `< >` 转成 `\uXXXX`（提前改 arguments 会被 dumps 二次转义损坏）；非流式返回必须 `Response(content=make_final(...))` 直接透传，经 `json.loads` 会还原转义。
- 解析逻辑改动必须加嵌套字面文本用例（test_tools_unit.py 的 H40 段）并跑穷举切分。

## DSML 腐蚀标签（H41 教训，强制）

上游偶发把**任意结构标签**腐蚀成 `｜DSML｜` 变体（DeepSeek 内部标记泄漏，实测泄露案例）：`</｜DSML｜>`/`</｜DSML｜e>` 替 `</tool_calls>`/`</invoke>`/`</parameter>`，`<｜DSML｜ ...>` 替 `<parameter ...>`，`</tocalls>` 替 `</tool_calls>`。旧版闭合不识别 → 块永不闭合 → flush 整块原文外露 + 工具调用不执行。
- `｜DSML｜` 只可能是上游噪声、**不可能是合法内容**：一律按「闭合最内层结构」消化（`_tok_kind`/`_unclosed_invoke`/`_DSML_*_RE`），不像 H40 那样当字面文本跳过。
- 包裹兜底搜索必须从状态机 `last_end` 之后起（块内已消费的 token 不得再命中）；深度归零无包裹闭合时块视为在 `last_end` 结束（开标签不得残留正文）。
- 用例见 test_tools_unit.py 的 H41 段（含两片穷举切分）；flush 容错提取仅在 `_corrupt` 标记（流中出现过腐蚀）时启用，纯截断流保持「按原文吐出」旧约定。

## 双写同步（当前架构事实）

refactor 分支引入 `src/ustb_chat2api/` 包版，但**生产仍跑根目录扁平 `chat2api.py`**（tray/tui/cli 导入扁平版，config.json 在根目录）。过渡期约束：
- 解析/服务端修复**必须同时改两份**：扁平 `kernel.py` + `chat2api.py` 与包版 `src/ustb_chat2api/kernel.py` + `server.py`（包版 content_to_text/angle_bracket_check 在 utils.py，不随 kernel 热重载）。
- 测试两版都要过：`python tests/test_tools_unit.py`（包版）+ `python tests/test_tools_unit.py --flat`（扁平版）。
- 包版 `config/`、`logs/` 目录从未启用，勿按包版路径排查线上问题。

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
