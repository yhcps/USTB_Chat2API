"""
USTB Chat2API - 构建打包脚本
生成可独立部署的整合包，内置 Python 环境 + 依赖
"""
import os
import sys
import json
import shutil
import subprocess
import platform
import textwrap
import argparse
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, "dist")
BUILD_DIR = os.path.join(BASE_DIR, "build", "chat2api_pkg")
VENV_DIR = os.path.join(BUILD_DIR, "venv")
APP_NAME = "USTB-Chat2API"


def check_pyinstaller():
    """检查 PyInstaller 是否可用"""
    try:
        import PyInstaller
        return True
    except ImportError:
        return False


def install_pyinstaller():
    """安装 PyInstaller"""
    print("[*] 安装 PyInstaller...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"],
                       capture_output=False, timeout=60)
    return r.returncode == 0


def build_pyinstaller_exe(console: bool = False):
    """使用 PyInstaller 打包为单 exe"""
    print(f"\n{'='*60}")
    print(f"  PyInstaller 打包 - {'控制台' if console else '无窗口'} 模式")
    print(f"{'='*60}")

    # 入口点
    entry = os.path.join(BASE_DIR, "src", "ustb_chat2api", "__main__.py")
    icon_path = os.path.join(BASE_DIR, "assets", "icon.ico")
    if not os.path.exists(icon_path):
        icon_path = None

    # 清理旧构建
    for d in ["build", "dist"]:
        shutil.rmtree(os.path.join(BASE_DIR, d), ignore_errors=True)

    cmd = [
        "pyinstaller",
        "--clean",
        "--noconfirm",
        "--name", APP_NAME,
        "--distpath", DIST_DIR,
        "--workpath", os.path.join(BASE_DIR, "build", "pyi_temp"),
        "--add-data", f"{os.path.join(BASE_DIR, 'src', 'ustb_chat2api')}{os.pathsep}ustb_chat2api",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "httpx._transports.default",
    ]

    if not console:
        cmd.append("--noconsole")
        cmd.append("--uac-admin")  # 静默运行时提权

    if icon_path:
        cmd.extend(["--icon", icon_path])

    cmd.append(entry)

    print(f"[*] 执行: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=False, timeout=300)
    if r.returncode == 0:
        print(f"\n[✓] 打包成功!")
        exe_path = os.path.join(DIST_DIR, f"{APP_NAME}.exe")
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print(f"    EXE: {exe_path} ({size_mb:.1f} MB)")
        return True
    else:
        print(f"\n[✗] 打包失败 (exit code {r.returncode})")
        return False


def build_portable_package(include_venv: bool = True):
    """构建便携式整合包（目录结构，非单 exe）"""
    print(f"\n{'='*60}")
    print(f"  构建便携式整合包")
    print(f"{'='*60}")

    # 清理并重建
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR)

    # 复制包源码
    src_pkg = os.path.join(BUILD_DIR, "ustb_chat2api")
    shutil.copytree(
        os.path.join(BASE_DIR, "src", "ustb_chat2api"),
        src_pkg,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )

    # 复制启动脚本
    shutil.copy(
        os.path.join(BASE_DIR, "start_background.ps1"),
        os.path.join(BUILD_DIR, "start_background.ps1")
    )
    shutil.copy(
        os.path.join(BASE_DIR, "start_tray.bat"),
        os.path.join(BUILD_DIR, "start_tray.bat")
    )

    # 复制配置文件
    shutil.copy(
        os.path.join(BASE_DIR, "pyproject.toml"),
        os.path.join(BUILD_DIR, "pyproject.toml")
    )
    if os.path.exists(os.path.join(BASE_DIR, "config.example.json")):
        shutil.copy(
            os.path.join(BASE_DIR, "config.example.json"),
            os.path.join(BUILD_DIR, "config.example.json")
        )

    # 创建 install.bat
    install_bat = textwrap.dedent(f"""\
    @echo off
    chcp 65001 >nul
    echo ========================================
    echo   USTB Chat2API - 便携式安装
    echo ========================================
    echo.

    REM 检测 Python
    python --version >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [错误] 未检测到 Python，请先安装 Python 3.10+
        echo        下载: https://www.python.org/downloads/
        pause
        exit /b 1
    )

    REM 安装依赖
    echo [*] 安装依赖...
    python -m pip install -r requirements.txt
    if %ERRORLEVEL% NEQ 0 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )

    REM 安装包
    echo [*] 安装包...
    python -m pip install -e .
    if %ERRORLEVEL% NEQ 0 (
        echo [错误] 包安装失败
        pause
        exit /b 1
    )

    echo.
    echo [✓] 安装完成!
    echo.
    echo 启动方式:
    echo   python -m ustb_chat2api          - 后台静默运行
    echo   python -m ustb_chat2api tui       - TUI 控制台
    echo   python -m ustb_chat2api cli       - CLI 管理
    echo.
    pause
    """)
    with open(os.path.join(BUILD_DIR, "install.bat"), "w", encoding="utf-8") as f:
        f.write(install_bat)

    # 创建 requirements.txt
    req = textwrap.dedent("""\
    fastapi>=0.110
    uvicorn>=0.29
    httpx>=0.27
    requests>=2.31
    pystray>=0.19
    pillow>=10.0
    websocket-client>=1.7
    """)
    with open(os.path.join(BUILD_DIR, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write(req)

    if include_venv:
        print("[*] 嵌入虚拟环境...")
        build_embedded_venv()

    # 打包为 ZIP
    shutil.make_archive(
        os.path.join(DIST_DIR, f"{APP_NAME}_portable"),
        "zip",
        BUILD_DIR
    )
    zip_path = os.path.join(DIST_DIR, f"{APP_NAME}_portable.zip")
    size_mb = os.path.getsize(zip_path) / (1024 * 1024) if os.path.exists(zip_path) else 0
    print(f"\n[✓] 便携包已创建: {zip_path} ({size_mb:.1f} MB)")
    return True


def build_embedded_venv():
    """在整合包中嵌入虚拟环境"""
    venv_path = os.path.join(BUILD_DIR, "venv")
    print(f"[*] 创建虚拟环境: {venv_path}")

    # 创建 venv
    r = subprocess.run([sys.executable, "-m", "venv", venv_path],
                       capture_output=True, timeout=60)
    if r.returncode != 0:
        print(f"[!] 虚拟环境创建失败: {r.stderr.decode()[:200]}")
        return False

    # 获取 venv 的 pip
    if platform.system() == "Windows":
        pip_exe = os.path.join(venv_path, "Scripts", "pip.exe")
        python_exe = os.path.join(venv_path, "Scripts", "python.exe")
    else:
        pip_exe = os.path.join(venv_path, "bin", "pip")
        python_exe = os.path.join(venv_path, "bin", "python")

    # 安装依赖
    req_file = os.path.join(BUILD_DIR, "requirements.txt")
    if os.path.exists(req_file):
        print("[*] 安装依赖到虚拟环境...")
        r = subprocess.run([pip_exe, "install", "-r", req_file],
                           capture_output=False, timeout=120)
        if r.returncode != 0:
            print("[!] 依赖安装失败")
            return False

    # 创建启动脚本（使用嵌入的 venv）
    if platform.system() == "Windows":
        launcher = textwrap.dedent(f"""\
        @echo off
        chcp 65001 >nul
        "%~dp0venv\\Scripts\\python.exe" -m ustb_chat2api %*
        """)
        with open(os.path.join(BUILD_DIR, "run.bat"), "w", encoding="utf-8") as f:
            f.write(launcher)

        launcher_bg = textwrap.dedent(f"""\
        @echo off
        chcp 65001 >nul
        start /b "" "%~dp0venv\\Scripts\\pythonw.exe" -m ustb_chat2api serve
        """)
        with open(os.path.join(BUILD_DIR, "run_background.bat"), "w", encoding="utf-8") as f:
            f.write(launcher_bg)

    print("[✓] 嵌入虚拟环境完成")
    return True


def build_installer_nsis():
    """（可选）创建 NSIS 安装包"""
    nsis_script = os.path.join(BASE_DIR, "build", "installer.nsi")
    print(f"[*] NSIS 安装脚本: {nsis_script}")
    # NSIS 需要单独安装，仅生成脚本
    return True


def main():
    parser = argparse.ArgumentParser(description="USTB Chat2API 构建打包工具")
    parser.add_argument("target", nargs="?", default="portable",
                        choices=["exe", "exe-console", "portable", "all"],
                        help="构建目标: exe(无窗口), exe-console(控制台), portable(目录), all(全部)")
    parser.add_argument("--no-venv", action="store_true",
                        help="便携包不嵌入虚拟环境")
    args = parser.parse_args()

    os.makedirs(DIST_DIR, exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "build"), exist_ok=True)

    targets = []
    if args.target == "all":
        targets = ["exe", "exe-console", "portable"]
    else:
        targets = [args.target]

    for t in targets:
        if t.startswith("exe"):
            if not check_pyinstaller():
                if not install_pyinstaller():
                    print("[✗] 无法安装 PyInstaller，跳过 exe 打包")
                    continue
            console = t == "exe-console"
            build_pyinstaller_exe(console=console)
        elif t == "portable":
            build_portable_package(include_venv=not args.no_venv)

    print(f"\n{'='*60}")
    print(f"  构建完成! 输出目录: {DIST_DIR}")
    print(f"{'='*60}")
    for f in os.listdir(DIST_DIR):
        fpath = os.path.join(DIST_DIR, f)
        size = os.path.getsize(fpath) / (1024 * 1024)
        print(f"  {f:40s} {size:.1f} MB")
    print("=" * 60)


if __name__ == "__main__":
    main()
