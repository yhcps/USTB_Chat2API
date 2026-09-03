# -*- coding: utf-8 -*-
"""[弃用] 功能已合并至 cli.py —— 请使用: python cli.py key generate|list|revoke"""
import sys

if __name__ == "__main__":
    import cli
    sys.exit(cli.main(["key"] + sys.argv[1:]))
