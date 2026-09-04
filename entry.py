# -*- coding: utf-8 -*-
"""
USTB Chat2API - 单文件统一入口（Windows EXE / Linux DEB 共用）

PyInstaller --onefile 模式下，运行时解压到 sys._MEIPASS 临时目录。
本模块将工作目录切换到解压路径，然后按参数分发到 tray / tui / cli / serve。

用法:
    ustb-chat2api                # Windows: 启动托盘   Linux: 启动 headless 服务
    ustb-chat2api serve          # headless 服务（前台运行，服务器/容器部署）
    ustb-chat2api tui            # TUI 控制台
    ustb-chat2api tray           # 系统托盘（Linux 需 GUI 环境）
    ustb-chat2api cli ...        # CLI 子命令（cookie / key / restart）
    ustb-chat2api cli cookie     # 登录并保存 Cookie
    ustb-chat2api cli key list   # 列出 API Key

Linux 说明:
- 无参数默认 serve（headless），适配无 GUI 的服务器/WSL 场景
- 打包时已排除 pystray/pillow，故 tray 子命令在 Linux 版不可用（会给出提示）
"""
import os
import sys

BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

IS_WINDOWS = sys.platform == 'win32'

USAGE = __doc__


def _run_tray():
    """启动托盘；Linux 精简版未打包 pystray/pillow 时给出明确提示"""
    try:
        import tray
    except ImportError as e:
        print(f"[错误] 当前构建未包含 GUI 依赖（pystray/pillow），无法启动托盘: {e}")
        print("       Linux 服务器环境请改用: ustb-chat2api serve   或   ustb-chat2api tui")
        sys.exit(1)
    tray.main()


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else []
    head = args[0] if args else ''

    if head == 'tui':
        import tui
        tui.main()
    elif head == 'cli':
        import cli
        sys.exit(cli.main(args[1:]))
    elif head == 'tray':
        _run_tray()
    elif head == 'serve':
        import chat2api
        chat2api.serve()
    elif head in ('-h', '--help', 'help'):
        print(USAGE)
    elif head:
        print(f"[错误] 未知参数: {head}\n")
        print(USAGE)
        sys.exit(1)
    else:
        # 默认入口按平台分流: Windows 有托盘则常驻托盘, Linux/服务器直接跑服务
        if IS_WINDOWS:
            _run_tray()
        else:
            import chat2api
            chat2api.serve()


if __name__ == '__main__':
    main()
