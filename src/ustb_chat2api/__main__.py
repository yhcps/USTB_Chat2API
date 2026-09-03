"""
USTB Chat2API 入口点
运行: python -m ustb_chat2api [serve|tray|tui|cli]
"""
import sys
import os

# 确保 src 在路径中
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else ["serve"]

    if args[0] in ("serve", "service", "server"):
        # 后台静默模式（默认）
        from ustb_chat2api.tray import main as tray_main
        tray_main()
    elif args[0] == "tui":
        from ustb_chat2api.tui import main as tui_main
        tui_main()
    elif args[0] == "cli":
        from ustb_chat2api.cli import main as cli_main
        sys.exit(cli_main(args[1:]))
    elif args[0] == "install":
        from ustb_chat2api.installer import main as install_main
        install_main()
    elif args[0] in ("-h", "--help"):
        print(__doc__)
    else:
        print(f"未知子命令: {args[0]}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
