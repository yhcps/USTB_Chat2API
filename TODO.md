# TODO — 尖括号直出问题全链路修复

> 状态：代码修复与单元验证完成（两版 58/58），真实请求回归见文末。

## 背景

上游 DeepSeek R1 模型不原生支持 function calling，靠提示词注入 XML 格式模拟。当模型输出中的 XML 标签（`<tool_calls>`、`<invoke>`）被流式解析器误判，或者 arguments 中的 XML 字符串泄露到客户端，就会导致「尖括号直出」——Trae 等客户端把 `<invoke>` 原文当普通文本渲染。

关键架构事实（本次发现）：**生产实际运行的是根目录扁平 `chat2api.py`**（tray/tui/cli 均导入它），此前修复全部打在 `src/ustb_chat2api/` 包版上、从未生效。经确认采用**双写同步**策略：修复同时落在两份实现，测试两版都要过（`--flat` 开关）。

## 已完成修复（扁平版 + 包版双写）

### 1. 嵌套 XML 状态机（H40 核心修复，高优先级）

- **问题**: 参数值内的 `<tool_calls>`/`</invoke>`/`<parameter>` 字面文本被裸正则非贪婪截断误判为结构标签 → 调用块提前闭合、参数截断、残片直出（H40 真实案例）。
- **修复**:
  - `_iter_wrapper_spans` / `_iter_invoke_spans` / `_parse_params`：配对按 `<parameter>` 深度状态机计算，值内同名标签视为字面文本；深度无法闭合时退回非贪婪正则（旧版行为兜底）。
  - 流式闭合门控：`feed()` 中候选 closer 命中后先 `_param_depth(acc+buf)` 校验，值内闭合跳过找真闭合。
  - 顺带修复非流式同块多 invoke 只提取第一个的存量 bug（`s < last` 去重误伤同块 span）。
  - 删除废弃的 `_INVOKE_RE`。
- **位置**: [chat2api.py](file:///f:\Chat2API\chat2api.py) 与 [server.py](file:///f:\Chat2API\src\ustb_chat2api\server.py) 同步。

### 2. ToolCallStreamParser._hold() 上下文感知

- **问题**: `arguments` 内对 `<` 不加区分扣留，导致解析器误判。
- **修复**: `mode='inv'` 时仅扣留已知标签前缀，普通 `<` 直接输出。

### 3. 输出层 _json_guard 尖括号转义

- **修复**: `make_chunk()` / `make_final()` 在 `json.dumps` 之后将 `< >` → `\u003c/\u003e`。合规 JSON 解析器解回原字符（客户端无感知），直接扫描 SSE 原文的客户端不再见裸尖括号。非流式返回由 `JSONResponse(json.loads(...))` 改为 `Response(content=...)` 直接透传（避免还原转义）。
- **坑**: 必须在 dumps 之后替换；提前改 arguments 会被二次转义损坏数据。

### 4. TOOL_INSTRUCTION 安全提醒（内容审核规避）

- **修复**: 末尾增加【安全提醒】，随 tools 注入下发：中性措辞替代高风险表述（在终端验证/清理/修正/排查/查看实现等）。与 AGENTS.md 措辞规避表同源。

### 5. 附带修复

- 包版 `server.py` 补导入 `DEBUG_LOG`（请求摘要日志此前 NameError 被 except 吞掉，静默失效）。
- `ReasoningXMLFilter` 跨分片二次校验：确认原 `_hold` 机制已满足「合并后不构成完整标签则释放」，仅加注释，无代码变更。
- `flush()` 超时退出：实测未闭合块流结束时自然处理（reasoning 丢弃 / content 原文吐出），未加超时机制。

## 测试

- [x] 单元测试（含穷举切分 + H40 嵌套段 + `--flat` 双版）：包版 58/58，扁平版 58/58
- [x] 服务重启（杀旧 tray + 拉起新 tray，/v1/models 200）
- [x] 真实请求回归 `test_stream_leak.py --rounds 3`

## 文档

- [x] AGENTS.md：新增「嵌套 XML 防护（H40 教训，强制）」「双写同步（当前架构事实）」章节；常用命令补 `--flat`
- 后续统一到包（refactor 收尾）时再清理双写约束

## DeepSeek R1 敏感拦截调研结论

- 上游内容审核为**动态随机**（同文本时拦时不拦），命中即固定话术秒回，无法从代理层根除，只能措辞规避。
- DeepSeek R1 官方 model card 承认安全对齐偏保守、存在过度拒答（over-refusal），社区通用缓解即提示词层面的中性化措辞 + 拆解指令，与本项目实测一致。
- 结论：提示词注入可降低误拦概率但不可根除；代码层「拦截话术原样透传、不自动重试」仍是正确兜底。
