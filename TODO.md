# TODO — 打包发布系统（release.zip + 单 EXE）

> 状态：**已完成**（构建系统可用；旧脚本清理命令已交用户手动执行）

## 背景

需求：项目可分发为（1）少量文件的 release 包；（2）单个可直接运行的 EXE。
生产仍跑根目录扁平版；打包针对扁平版（chat2api.py + dashboard.py + tray/tui/cli/entry）。

## 新构建系统

| 文件 | 职责 |
|---|---|
| `entry.py` | PyInstaller 单 EXE 入口：`sys._MEIPASS` 路径适配 + 按 argv 分发 tray/tui/cli |
| `release.py` | 统一构建脚本：默认 zip（`dist/USTB-Chat2API-v*.zip`），`--exe` 走 PyInstaller（`dist/exe/USTB-Chat2API.exe`） |

- zip 模式已验证：17 个文件（生产 10 + tests + install.bat）。
- PyInstaller 6.22.2 已装入 `.venv_chat2api`。
- hidden-import 覆盖 uvicorn 动态加载链（loops/protocols/lifespan）、anyio asyncio backend、pystray._win32、PIL._tkinter_finder、websocket。

## 待办

- [x] entry.py 写入（Write 工具曾空写失败，改经 PowerShell WriteAllText 落盘成功）
- [x] release.py 写入（Write 工具正常）
- [x] zip 模式验证（17 文件）
- [x] EXE 首次构建成功（24.4 MB，PyInstaller 6.22.2）
- [x] frozen 路径适配：chat2api/dashboard/tray/tui/cli 五模块 BASE_DIR 改为 `sys.executable` 目录（frozen 时），config.json/stats.json/日志落 EXE 同目录；tui `[h]` 转交与托盘打开 TUI 补 frozen 分支
- [x] 回归：py_compile 全过 + 两版单元测试 58/58
- [x] EXE 重建 + 冒烟（`cli key list` 输出正常 exit=0；stats.json 落 EXE 同目录而非临时目录）
- [ ] 清理旧构建脚本（命令已交用户，**手动执行**）：
  ```powershell
  Remove-Item "F:\Chat2API\build.py","F:\Chat2API\build_pkg.py","F:\Chat2API\fixup_server.py","F:\Chat2API\pyproject.toml","F:\Chat2API\src\ustb_chat2api\installer.py" -Force
  ```
- [x] AGENTS.md 增补构建命令与文件表
- [x] dist/、build/ 已在 .gitignore（无需改动）

## 遗留事实（勿忘）

- 上一任务「尖括号直出全链路修复」已全部完成（两版 58/58，真实回归通过），详情见 git 历史。
- dashboard 持久化（stats.json 存 `_BASE_DIR`，重启保留）已合并 main/refactor 同步。
- PyInstaller 单文件 `__file__` 指向 `sys._MEIPASS` 的问题已解：五模块 frozen 时 BASE_DIR 取 `sys.executable` 目录（冒烟验证 stats.json 落 EXE 旁）。
- 本轮改了 chat2api.py 等 5 个生产文件（仅 frozen 分支，非 frozen 行为不变）；按用户约束未重启线上服务，下次重启时一并生效。

---

# TODO — H41 DSML 腐蚀标签修复（｜DSML｜ / </tocalls>）

> 状态：**已完成**（两版 72/72；服务已重启生效；真实场景回归通过）

## 根因（来自真实聊天记录排查）

上游把结构标签腐蚀成 `｜DSML｜` 变体（如 `</｜DSML｜>` 替 `</tool_calls>`），旧解析器闭合不识别 → 块永不闭合 → flush 整块 XML 原文外露 + 工具调用不执行（tui.py 曾因此编辑未落盘）。

## 完成记录

- [x] chat2api.py + src/ustb_chat2api/server.py 双写修复（`_DSML_*_RE`、`_tok_kind`、`_unclosed_invoke`、wrapper 兜底 last_end、flush 容错提取）
- [x] tests/test_tools_unit.py 新增 H41 段（真实泄露案例 + 两片穷举切分），两版 72/72
- [x] AGENTS.md 增补「DSML 腐蚀标签（H41 教训，强制）」章节
- [x] 服务已重启（H41 修复 + 尖括号预警一并生效）
- [ ] 用户手动清理临时调试脚本：
  ```powershell
  Remove-Item "F:\Chat2API\tests\_debug_cases.py" -Force
  ```

---

# TODO — 尖括号预警 + 真实场景 sub-agent 稳定性测试

> 状态：**已完成**

## 成果

- [x] 尖括号预警机制（双写，单测 3 项）
- [x] 真实场景测试框架 `tests/test_subagent_stability.py`：文档维护 + 分支维护
- [x] 分支维护增强：新增 `view_diff`、`view_file_log`、`view_commit` 三个只读 git 工具
- [x] 实测共计 5 轮 x 2 任务，7/10 完成、0 失败，3 次为上游随机审核拦截
- [x] 排障记录：venv 启动器 pythonw 派生真实解释器（写 AGENTS.md 必踩坑 7）
