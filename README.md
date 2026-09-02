# USTB Chat2API

将北京科技大学 AI 助手（chat.ustb.edu.cn，DeepSeek）转换为 **OpenAI Chat Completions 兼容 API** 的本地网关。

适用于把校内 DeepSeek 接入任意支持自定义 OpenAI 兼容模型的客户端：**Trae / CherryStudio / ChatBox / NextChat / OpenAI SDK / curl** 等，完整支持 **Agent 模式的工具调用（function calling）桥接**。

> 仅限个人学习测试用途，请遵守学校信息化使用规范，勿用于生产或对外提供服务。

---

## 功能特性

- **OpenAI 兼容**：`/v1/chat/completions`（流式/非流式）+ `/v1/models`，标准 `Authorization: Bearer` 鉴权
- **思考过程透传**：保留 DeepSeek 的 `reasoning_content` 字段
- **Agent 工具调用桥**：客户端下发 `tools` 时自动注入调用规范，把模型输出的工具调用 XML 实时转换为标准 `tool_calls` 字段；`tool` 角色结果自动回传上游，Agent 循环可无限续跑（已适配 Trae 全量 24 个工具）
- **消息结构自适应**：自动归一化 content 分片列表、`developer` 角色、`stream_options:null`、连续同角色消息等各客户端差异，规避上游 `4028 history参数异常`
- **双前端交互**：
  - **TUI 控制台**：单键操作、启动自检、一键复制端点/Key、登录检测
  - **系统托盘**：无窗口常驻后台，图标即状态灯（🟢 正常 / 🟡 会话失效 / 🔴 服务异常），掉线自动拉起服务（守护进程）
- **凭证自动化**：登录状态自动检测；未登录自动拉起浏览器完成 SSO 后自动截取 Cookie 存储
- **API Key 管理**：初始化生成默认 Key，支持热重载（增删改无需重启）

## 项目结构

```
Chat2API/
├── install.bat          # 一键安装（创建 venv + 装依赖）
├── start_tray.bat       # 启动托盘常驻模式（推荐，无窗口）
├── start_tui.bat        # 启动 TUI 控制台
├── chat2api.py          # 核心转发服务（FastAPI + 工具调用桥）
├── tray.py              # 系统托盘常驻程序（含服务守护）
├── tui.py               # TUI 控制台
├── update_cookies.py    # Cookie 提取/更新工具（可独立运行）
├── manage_keys.py       # API Key 管理工具
├── requirements.txt
├── config.example.json  # 配置模板（首次运行自动生成 config.json）
├── tests/
│   └── test_tools_unit.py   # 工具调用桥单元测试（28 项，确定性）
└── .gitignore
```

## 环境要求

- Windows 10/11
- Python 3.10+（安装时勾选 *Add Python to PATH*）
- 学校统一身份认证账号（能登录 chat.ustb.edu.cn）

## 快速开始（三步）

### 1. 安装

双击 `install.bat`，自动完成虚拟环境创建与依赖安装。

<details>
<summary>手动安装（命令行方式）</summary>

```powershell
cd F:\Chat2API
python -m venv .venv_chat2api
.venv_chat2api\Scripts\python.exe -m pip install -r requirements.txt
```
</details>

### 2. 启动

双击 `start_tray.bat` —— 服务在后台常驻，任务栏右下角出现托盘图标。

| 托盘图标 | 含义 |
|---|---|
| 🟢 绿色 | 服务在线且校内会话有效 |
| 🟡 黄色 | 会话失效或 Key 无效，需重新登录 |
| 🔴 红色 | 服务异常 |

右键托盘图标可执行：复制 Chat 端点 / 复制 API Key / 重新生成 Key / 巡检 / 测试对话 / 登录更新 Cookie / 打开 TUI 控制台 / 退出。

### 3. 登录

- **已在校内平台登录**：托盘自动检测并截取 Cookie，图标变绿
- **未登录**：托盘右键 →「登录 / 更新 Cookie」，自动弹出浏览器，完成学校 SSO 登录后凭证自动截取保存（5 分钟超时）

## 客户端接入

在任意支持自定义 OpenAI 模型的客户端中填写：

| 配置项 | 值 |
|---|---|
| API 格式 | OpenAI Chat Completions |
| 请求地址 | `http://127.0.0.1:8787/v1/chat/completions`（完整 URL，不拼接路径） |
| 模型 ID | `deepseek-r1` / `deepseek-chat` / `deepseek-reasoner` / `DeepSeek` 任意一个 |
| API Key | `sk-local`（默认，可在托盘/TUI 中重新生成） |

> 注意：模型 ID 与展示名是两个字段，Key 只输入一次（填成两次会 401）。

### OpenAI SDK 示例

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8787/v1", api_key="sk-local")

# 非流式
r = client.chat.completions.create(
    model="deepseek-r1",
    messages=[{"role": "user", "content": "你好"}],
)
print(r.choices[0].message.content)

# 流式（含思考过程与 usage）
stream = client.chat.completions.create(
    model="deepseek-r1",
    messages=[{"role": "user", "content": "写一首诗"}],
    stream=True,
    stream_options={"include_usage": True},
)
for chunk in stream:
    delta = chunk.choices[0].delta if chunk.choices else None
    if delta and delta.reasoning_content:
        print("思考:", delta.reasoning_content, end="")
    elif delta and delta.content:
        print(delta.content, end="")
```

### curl 示例

```bash
curl http://127.0.0.1:8787/v1/chat/completions ^
  -H "Authorization: Bearer sk-local" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"deepseek-r1\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}]}"
```

### 在 Trae 中使用 Agent 模式

1. 设置 → 模型 → 添加自定义模型：按上表填写
2. 选中该模型，切到 **Agent** 模式
3. 正常对话即可——工具调用由本服务自动桥接，模型执行文件读写、搜索、命令等操作与官方模型一致

## Trae 针对性特调：`<>` 直出问题的完整解析

> 本章节面向使用 Trae（或其他 Agent 客户端）接入本服务的用户，以及希望为类似"不支持 function calling 的上游"做桥接的开发者，完整记录问题成因、解决措施与遗留边界。

### 现象

在 Trae 中把本服务配为自定义模型后，出现两类异常：

1. 模型回复中原样输出 `<tool_calls><invoke name="List">...` 等 XML 标签（即 `<>直出`），且 **Agent 不再继续执行**（工具未被调用，循环停止）
2. 伴随 `{"detail":"最后一条消息必须是 user"}` 或上游 `history参数异常 (4028)`

### 根因（三层叠加）

| # | 层次 | 说明 |
|---|---|---|
| 1 | 上游协议缺失 | 校内 DeepSeek 部署的是"应用对话"接口（`/site/ai/compose_chat`，multipart 表单 + SSE），**不支持 OpenAI function calling 协议**。Trae 下发的 `tools` 数组无处安放，只能靠提示词约定格式 |
| 2 | 工具调用 XML 被当正文 | 模型按提示词约定把工具调用以 XML 混在回复正文里输出；不做转换的话，客户端收到的是纯文本，既无法执行工具，界面上就是 `<>` 原文直出/被截断 |
| 3 | Agent 循环断裂 | 工具执行后客户端回传的 `role:"tool"` 消息，上游协议里不存在对应位置；桥接前直接丢弃 → 下一轮请求触发"最后一条消息必须是 user"/4028，循环彻底中断 |

### 解决措施（工具调用桥，见 chat2api.py）

```
客户端(tools) ──注入格式说明──▶ 上游(纯文本协议)
上游(XML输出) ──流式解析转换──▶ 客户端(标准 tool_calls delta)
客户端(tool结果) ──转"工具执行结果"user消息──▶ 上游
```

1. **格式注入**：请求携带 `tools` 时，在 system 提示中注入规范调用格式与工具清单（名称/描述/JSON Schema）
2. **流式转换器** `ToolCallStreamParser`：逐 chunk 增量解析模型输出，把工具调用 XML 实时转为 OpenAI 标准 `tool_calls` delta，并以 `finish_reason="tool_calls"` 结束；正文零泄漏
3. **结果回传**：`role:"tool"` 消息转为 `[工具 X 执行结果]` 文本回传上游；历史 `tool_calls` 还原为规范 XML 保持格式一致
4. **鲁棒性**（覆盖真实模型输出的各种"自由发挥"，均有单元测试锁定）：
   - `<tool_calls>` 包裹 / `<tool_call>` 单数包裹 / 裸 `<invoke>` / 自闭合 `<read path="..."/>`（任意位置）
   - `<invoke>` 标签携带额外属性、参数值跨 chunk 断裂、大小写混用、单双引号
   - ` ```xml ` 代码围栏紧贴调用块时自动剥离
   - 未闭合 XML 在流结束时按原文吐出（防截断静默丢内容）
   - 普通文本中的 `<`/`>`（如 "1<2"）不误吞

**实测**：Trae 全量 24 个工具（Task/Read/Write/RunCommand/...）可正常循环执行，工具调用转标准 `tool_calls=[('List', '{"path": "f:\\\\Chat2API"}')]`，28 项单元测试全通过。

### 给其他 Agent 客户端/开发者的参考要点

1. **上游只认 user/assistant 严格交替**：连续同角色消息会触发上游 `4028 history参数异常`，必须在桥接层合并
2. **消息角色映射**：`developer`→`system`；`tool`→带工具名的 user 消息；content 分片列表→纯文本
3. **pythonw/无控制台运行**：`sys.stdout` 为 `None`，uvicorn 默认日志配置会崩溃，须 `log_config=None`
4. **错误必须带上下文返回**：把上游非 SSE 响应原文写日志并把摘要返回客户端，否则 500 无线索
5. **内容审核**：上游对涉工具调用、文件读取类措辞有误拦（返回"无法回答"），桥接层原样透传即可，勿重试轰炸

### 已知边界：SOLO 模式无法访问本机回环地址（400 destination-addr）

**报错**：`header destination-addr invalid: 127.0.0.1:8787, specifing loopback address is NOT allowed (HTTP Status: 400)`

**原因**：Trae 的 **SOLO 模式在云端沙箱执行**，模型请求由沙箱侧网关代发（请求带 `destination-addr` 头供网关校验）。网关出于 SSRF 防护**拒绝回环地址**——云端沙箱在物理上也根本无法访问你本机的 127.0.0.1。这不是本服务的问题（此时请求不会到达本服务，`server_debug.log` 无记录）。普通 **Chat / Builder / Agent 模式由本地进程直连**，不受影响。

**解决**：

- 优先使用 **Agent（Builder）模式**接入本服务
- 确需 SOLO 场景：在 `config.json` 中加入 `"host": "0.0.0.0"` 并重启服务，客户端地址改用本机**局域网 IP**（如 `http://192.168.x.x:8787/v1/chat/completions`，`ipconfig` 查看）。注意这会将服务暴露到局域网，请务必同时更换强随机 API Key
- 同类问题参考：[mimo-proxy#4](https://github.com/Mintneko/mimo-proxy/issues/4)（Trae + 本地代理同款报错）

## TUI 控制台按键

`start_tui.bat` 启动（或托盘右键 → 打开 TUI 控制台）：

| 按键 | 功能 |
|---|---|
| `[1]` `[2]` `[3]` | 一键复制 Chat 端点 / Base URL / API Key |
| `[4]` | 登录 / 更新 Cookie（自动拉起浏览器 + 自动截取） |
| `[5]` | 重新生成 API Key（立即生效，旧 Key 失效） |
| `[6]` | 重新检测服务与密钥 |
| `[7]` | 发送测试对话 |
| `[0]` | 检测登录状态 |
| `[h]` | 隐藏 TUI（服务转交托盘常驻） |
| `[q]` | 退出（停止服务） |

启动时自动执行：初始化默认 Key → 启动/复用服务 → 验证 Key → 检测登录状态，全部结果直接显示在面板上。

## API Key 管理

```bash
python manage_keys.py generate [名称]   # 生成随机 key（明文仅显示一次，落盘为哈希）
python manage_keys.py list              # 列出所有 key（只显示前缀）
python manage_keys.py revoke <前缀>     # 吊销
```

所有变更热加载，无需重启服务。

## 开机自启（可选）

`Win+R` → `shell:startup` → 将 `start_tray.bat` 的快捷方式放入该文件夹。

## 常见问题排查

| 现象 | 原因与解决 |
|---|---|
| `401 Invalid API key` | 客户端 Key 与服务端不一致（注意不要重复粘贴）。托盘/TUI 中复制最新 Key，或 `[5]` 重新生成 |
| `上游未返回SSE(200): history参数异常 (4028)` | 已内置自动修复（合并连续同角色消息）。若仍出现，新建会话重试，旧会话历史可能已被污染 |
| `500` 带具体错误信息 | 查看 `server_error.log` 中的完整堆栈 |
| 回复"抱歉，我无法回答" | 上游内容审核拦截，换个措辞重试即可（服务本身正常） |
| 模型回复中出现 `<>` 标签 | 已由工具调用桥实时转换。确认服务已升级到最新版并重启；可查看 `server_debug.log` 中该请求的 tools 与转换记录 |
| 托盘图标黄色 | 会话失效：托盘右键 → 登录/更新 Cookie |
| `destination-addr invalid ... loopback NOT allowed (400)` | Trae SOLO 云端沙箱无法访问回环地址，与代理开关无关，详见上方"Trae 特调"章节的"已知边界"；改用 Agent 模式或绑定局域网 IP |
| `ConnectError ... 127.0.0.1:7890 拒绝连接` | Trae **自身的代理设置**指向了已关闭的本地代理（设置 → 网络代理中清除，或重新开启对应代理）。该残留影响 Trae 内所有模型请求，与本服务无关 |
| 端口 8787 被占用 | 关闭占用进程：先 `netstat -ano` 找到 8787 对应 PID，再 `taskkill /PID <pid> /F`；或修改 `chat2api.py` 中 `PORT` |
| Cookie 频繁过期 | 学校 SSO 会话有有效期，失效后重新按 `[4]` 登录即可 |

### 日志文件

| 文件 | 内容 |
|---|---|
| `server_error.log` | 未处理异常堆栈（含上游非 SSE 响应时的 history 结构） |
| `server_debug.log` | 每个请求的 model/stream/tools/消息结构 + tool_calls 转换记录 |
| `tray.log` | 托盘后台运行异常 |

## 安全与合规

- 所有凭证（Cookie、Key）仅保存在本机 `config.json` / `api_keys.json`，**已被 .gitignore 排除，不会上传**
- 默认仅监听 `127.0.0.1`，不对外网暴露
- 该接口走你的校内账号额度，请仅用于个人测试，遵守学校信息化使用规范

## License

[MIT](LICENSE)
