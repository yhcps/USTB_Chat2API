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
