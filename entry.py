# -*- coding: utf-8 -*-
"""
USTB Chat2API - 单 EXE 入口点

PyInstaller --onefile 模式下，运行时解压到 sys._MEIPASS 临时目录。
本模块将工作目录切换到解压路径，然后按参数分发到 tray / tui / cli。

用法:
    USTB-Chat2API.exe           # 默认启动托盘（后台常驻）
    USTB-Chat2API.exe tui       # 启动 TUI 控制台
    USTB-Chat2API.exe cli ...   # 执行 CLI 子命令
"""
import os
import sys

BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else []
    if args and args[0] == 'tui':
        import tui
        tui.main()
    elif args and args[0] == 'cli':
        import cli
        sys.exit(cli.main(args[1:]))
    else:
        import tray
        tray.main()


if __name__ == '__main__':
    main()