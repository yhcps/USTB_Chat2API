# -*- coding: utf-8 -*-
"""
USTB Chat2API 统一构建脚本

用法:
    python release.py              # 打包为 release zip
    python release.py --exe        # 打包为单文件 EXE（需 PyInstaller）
    python release.py --exe --no-clean   # 跳过清理直接打包

构建产物输出到 dist/ 目录。
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, "dist")
BUILD_DIR = os.path.join(BASE_DIR, "build")

# 生产文件列表（扁平版根目录结构）
PROD_FILES = [
    "chat2api.py",
    "dashboard.py",
    "tray.py",
    "tui.py",
    "cli.py",
    "entry.py",
    "config.example.json",
    "requirements.txt",
    "AGENTS.md",
    "README.md",
]

# PyInstaller 隐式导入（uvicorn/fastapi 动态加载链 + 托盘依赖）
HIDDEN_IMPORTS = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.middleware",
    "uvicorn.middleware.proxy_headers",
    "uvicorn.importer",
    "anyio._backends._asyncio",
    "pystray._win32",
    "PIL._tkinter_finder",
    "websocket",
]

# PyInstaller 整包收集
COLLECT_ALL = ["uvicorn", "fastapi", "starlette", "pydantic", "pystray"]

# 打包进 EXE 的数据文件
ADD_DATA = [
    ("config.example.json", "."),
]

INSTALL_BAT = r"""@echo off
chcp 65001 >nul
echo USTB Chat2API - 北科大 AI 助手本地网关
echo ==========================================
echo.
echo 1. 安装依赖:  pip install -r requirements.txt
echo 2. 登录校园账号:  python cli.py cookie
echo 3. 启动托盘常驻:  pythonw tray.py
echo    或启动 TUI 控制台:  python tui.py
echo.
echo 服务端点: http://127.0.0.1:8787/v1
echo.
pause
"""


def _version() -> str:
    try:
        tag = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=BASE_DIR, stderr=subprocess.DEVNULL, text=True,
        ).strip()
        if tag:
            return tag
    except Exception:
        pass
    return "v2.0.0"


def build_release() -> str:
    """打包 release zip：生产文件 + tests + install.bat"""
    version = _version()
    zip_name = f"USTB-Chat2API-{version}.zip"
    zip_path = os.path.join(DIST_DIR, zip_name)
    os.makedirs(DIST_DIR, exist_ok=True)

    entries = []
    for name in PROD_FILES:
        src = os.path.join(BASE_DIR, name)
        if os.path.isfile(src):
            entries.append((src, name))
        else:
            print(f"[WARN] 缺少生产文件: {name}")

    tests_dir = os.path.join(BASE_DIR, "tests")
    if os.path.isdir(tests_dir):
        for root, _dirs, files in os.walk(tests_dir):
            for fn in files:
                if fn.endswith(".py"):
                    full = os.path.join(root, fn)
                    rel = os.path.relpath(full, BASE_DIR)
                    entries.append((full, rel.replace(os.sep, "/")))

    install_bat_path = os.path.join(DIST_DIR, "install.bat")
    with open(install_bat_path, "w", encoding="utf-8") as f:
        f.write(INSTALL_BAT)
    entries.append((install_bat_path, "install.bat"))

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arcname in entries:
            zf.write(src, arcname)
    os.remove(install_bat_path)

    print(f"[OK] release 包完成: {zip_path}（{len(entries)} 个文件）")
    return zip_path


def build_exe() -> str:
    """PyInstaller 单文件 EXE"""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "PyInstaller", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        print("[ERR] 未安装 PyInstaller，先执行: pip install pyinstaller")
        sys.exit(1)

    exe_name = "USTB-Chat2API"
    dist_path = os.path.join(DIST_DIR, "exe")
    os.makedirs(dist_path, exist_ok=True)

    cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--clean", "--noconfirm",
           "--name", exe_name,
           "--distpath", dist_path,
           "--workpath", os.path.join(BUILD_DIR, "pyinstaller"),
           "--specpath", BUILD_DIR]
    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    for pkg in COLLECT_ALL:
        cmd += [f"--collect-all={pkg}"]
    for src, dst in ADD_DATA:
        if not os.path.isabs(src):
            src = os.path.join(BASE_DIR, src)
        cmd += ["--add-data", f"{src}{os.pathsep}{dst}"]
    cmd.append(os.path.join(BASE_DIR, "entry.py"))

    print("[INFO] PyInstaller 打包中 ...")
    subprocess.check_call(cmd, cwd=BASE_DIR)
    exe_path = os.path.join(dist_path, f"{exe_name}.exe")
    size_mb = os.path.getsize(exe_path) / 1024 / 1024
    print(f"[OK] EXE 完成: {exe_path}（{size_mb:.1f} MB）")
    return exe_path


def clean() -> None:
    """清理打包中间产物（dist/ 与 build/ 目录由用户手动删除，此处仅清 spec 缓存）"""
    for d in [os.path.join(BUILD_DIR, "pyinstaller")]:
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            print(f"[OK] 已清理 {d}")


def main() -> None:
    ap = argparse.ArgumentParser(description="USTB Chat2API 统一构建脚本")
    ap.add_argument("--exe", action="store_true", help="打包单文件 EXE（默认打包 release zip）")
    ap.add_argument("--no-clean", action="store_true", help="EXE 打包前不清理中间产物")
    args = ap.parse_args()
    if args.exe:
        if not args.no_clean:
            clean()
        build_exe()
    else:
        build_release()


if __name__ == "__main__":
    main()
