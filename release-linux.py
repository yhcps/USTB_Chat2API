# -*- coding: utf-8 -*-
"""
USTB Chat2API - Linux DEB 构建脚本（在 WSL / Linux 中运行）

产物: dist/ustb-chat2api_<version>_<arch>.deb

用法:
    python3 release-linux.py              # 构建 DEB（含单文件二进制）
    python3 release-linux.py --bin        # 仅构建单文件二进制，不打包 DEB
    python3 release-linux.py --no-clean   # 跳过中间产物清理

DEB 内容:
    /usr/bin/ustb-chat2api                       单文件二进制（PyInstaller --onefile）
    /lib/systemd/system/ustb-chat2api.service    systemd 服务单元
    /etc/ustb-chat2api/config.example.json       配置模板

配置目录: 由服务单元的环境变量 CHAT2API_HOME=/etc/ustb-chat2api 指定，
          config.json / api_keys.json / stats.json 均落在该目录（源码各模块已支持该变量）。

说明:
- Linux 版面向服务器/无头场景，打包时排除 pystray/pillow（GUI 依赖），
  避免把 GTK 等运行库拖进包里；因此 `tray` 子命令不可用，默认入口为 headless serve。
- 需要 GUI 托盘时请改用源码部署并额外安装 pystray pillow。
"""
import argparse
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, "dist")
BUILD_DIR = os.path.join(BASE_DIR, "build")
DEB_ROOT = os.path.join(BUILD_DIR, "deb")

BIN_NAME = "ustb-chat2api"
PKG_NAME = "ustb-chat2api"

# PyInstaller 隐式导入（uvicorn/fastapi 动态加载链；Linux 版不含 GUI 依赖）
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
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.middleware",
    "uvicorn.middleware.proxy_headers",
    "uvicorn.importer",
    "anyio._backends._asyncio",
    "websocket",
]

COLLECT_ALL = ["uvicorn", "fastapi", "starlette", "pydantic"]

# 排除 GUI 依赖：托盘在服务器场景不可用，且会把 GTK/AppIndicator 等运行库拖进包
EXCLUDE_MODULES = ["pystray", "PIL", "tkinter", "_tkinter"]

ADD_DATA = [("config.example.json", ".")]

SYSTEMD_UNIT = """[Unit]
Description=USTB Chat2API - OpenAI compatible local gateway
Documentation=https://github.com/yhcps/USTB_Chat2API
After=network.target

[Service]
Type=simple
Environment=CHAT2API_HOME=/etc/ustb-chat2api
WorkingDirectory=/etc/ustb-chat2api
ExecStart=/usr/bin/ustb-chat2api serve
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

POSTINST = """#!/bin/bash
set -e
CONF_DIR=/etc/ustb-chat2api
mkdir -p "$CONF_DIR"

# 首次安装生成配置文件（已存在则保留，避免覆盖用户 Cookie/Key）
if [ ! -f "$CONF_DIR/config.json" ]; then
    cp "$CONF_DIR/config.example.json" "$CONF_DIR/config.json"
    chmod 600 "$CONF_DIR/config.json"
    echo "已生成配置: $CONF_DIR/config.json"
fi

# 注册 systemd 服务（不自动启动：需先配置 Cookie）
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    systemctl daemon-reload
    systemctl enable ustb-chat2api.service >/dev/null 2>&1 || true
fi

echo "--------------------------------------------------"
echo "USTB Chat2API 安装完成"
echo "  1) 登录校园账号: ustb-chat2api cli cookie"
echo "  2) 启动服务:     systemctl start ustb-chat2api"
echo "     或前台运行:   ustb-chat2api serve"
echo "  3) 端点:         http://127.0.0.1:8787/v1"
echo "--------------------------------------------------"
exit 0
"""

PRERM = """#!/bin/bash
set -e
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    systemctl stop ustb-chat2api.service >/dev/null 2>&1 || true
    systemctl disable ustb-chat2api.service >/dev/null 2>&1 || true
fi
exit 0
"""


def _version() -> str:
    """DEB 版本号（不含 v 前缀，符合 Debian 版本规范）"""
    try:
        tag = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=BASE_DIR, stderr=subprocess.DEVNULL, text=True,
        ).strip()
        if tag:
            return tag.lstrip("v")
    except Exception:
        pass
    return "2.0.0"


def _arch() -> str:
    m = os.uname().machine
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(m, m)


def _require(cmd: str, pkg: str, hint: str):
    if shutil.which(cmd):
        return
    print(f"[ERR] 缺少 {pkg}。安装: {hint}")
    sys.exit(1)


def build_binary() -> str:
    """PyInstaller --onefile 构建单文件二进制"""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "PyInstaller", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        print("[ERR] 未安装 PyInstaller，先执行: pip3 install pyinstaller")
        sys.exit(1)

    bin_dir = os.path.join(BUILD_DIR, "bin")
    os.makedirs(bin_dir, exist_ok=True)

    cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--clean", "--noconfirm",
           "--name", BIN_NAME,
           "--distpath", bin_dir,
           "--workpath", os.path.join(BUILD_DIR, "pyinstaller"),
           "--specpath", BUILD_DIR]
    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    for pkg in COLLECT_ALL:
        cmd += [f"--collect-all={pkg}"]
    for mod in EXCLUDE_MODULES:
        cmd += ["--exclude-module", mod]
    for src, dst in ADD_DATA:
        if not os.path.isabs(src):
            src = os.path.join(BASE_DIR, src)
        if os.path.exists(src):
            cmd += ["--add-data", f"{src}{os.pathsep}{dst}"]
    cmd.append(os.path.join(BASE_DIR, "entry.py"))

    print("[INFO] PyInstaller 打包中（Linux 单文件二进制，已排除 GUI 依赖）...")
    subprocess.check_call(cmd, cwd=BASE_DIR)

    bin_path = os.path.join(bin_dir, BIN_NAME)
    if not os.path.exists(bin_path):
        print(f"[ERR] 构建失败，未找到 {bin_path}")
        sys.exit(1)
    size_mb = os.path.getsize(bin_path) / 1024 / 1024
    print(f"[OK] 二进制完成: {bin_path}（{size_mb:.1f} MB）")
    return bin_path


def build_deb(bin_path: str) -> str:
    """组装 DEB 目录结构并打包"""
    _require("dpkg-deb", "dpkg-deb", "apt-get install -y dpkg")

    version = _version()
    arch = _arch()
    deb_name = f"{PKG_NAME}_{version}_{arch}.deb"
    deb_path = os.path.join(DIST_DIR, deb_name)

    if os.path.exists(DEB_ROOT):
        shutil.rmtree(DEB_ROOT)

    # /usr/bin
    usr_bin = os.path.join(DEB_ROOT, "usr", "bin")
    os.makedirs(usr_bin, exist_ok=True)
    shutil.copy2(bin_path, os.path.join(usr_bin, BIN_NAME))
    os.chmod(os.path.join(usr_bin, BIN_NAME), 0o755)

    # /lib/systemd/system
    systemd_dir = os.path.join(DEB_ROOT, "lib", "systemd", "system")
    os.makedirs(systemd_dir, exist_ok=True)
    with open(os.path.join(systemd_dir, f"{PKG_NAME}.service"), "w", encoding="utf-8") as f:
        f.write(SYSTEMD_UNIT)

    # /etc/ustb-chat2api
    etc_dir = os.path.join(DEB_ROOT, "etc", PKG_NAME)
    os.makedirs(etc_dir, exist_ok=True)
    src_cfg = os.path.join(BASE_DIR, "config.example.json")
    if os.path.exists(src_cfg):
        shutil.copy2(src_cfg, os.path.join(etc_dir, "config.example.json"))

    # DEBIAN 控制文件
    deb_meta = os.path.join(DEB_ROOT, "DEBIAN")
    os.makedirs(deb_meta, exist_ok=True)
    control = f"""Package: {PKG_NAME}
Version: {version}
Section: net
Priority: optional
Architecture: {arch}
Depends: libc6
Maintainer: USTB Chat2API <noreply@example.com>
Homepage: https://github.com/yhcps/USTB_Chat2API
Description: USTB AI assistant to OpenAI-compatible API gateway
 Bridges chat.ustb.edu.cn (DeepSeek) to an OpenAI-compatible local
 endpoint at http://127.0.0.1:8787/v1, with tool-call translation,
 streaming support and a web dashboard.
"""
    with open(os.path.join(deb_meta, "control"), "w", encoding="utf-8") as f:
        f.write(control)
    for name, content in (("postinst", POSTINST), ("prerm", PRERM)):
        p = os.path.join(deb_meta, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(p, 0o755)

    os.makedirs(DIST_DIR, exist_ok=True)
    print("[INFO] dpkg-deb 打包中 ...")
    subprocess.check_call(["dpkg-deb", "--build", "--root-owner-group", DEB_ROOT, deb_path])

    size_mb = os.path.getsize(deb_path) / 1024 / 1024
    print(f"[OK] DEB 完成: {deb_path}（{size_mb:.1f} MB）")
    return deb_path


def clean() -> None:
    for d in (os.path.join(BUILD_DIR, "pyinstaller"), DEB_ROOT):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            print(f"[OK] 已清理 {d}")


def main() -> None:
    if sys.platform == "win32":
        print("[ERR] 本脚本用于构建 Linux DEB，请在 WSL / Linux 中运行。")
        print("      Windows EXE 请改用: python release.py --exe")
        sys.exit(1)

    ap = argparse.ArgumentParser(description="USTB Chat2API Linux DEB 构建脚本")
    ap.add_argument("--bin", action="store_true", help="仅构建单文件二进制，不打包 DEB")
    ap.add_argument("--no-clean", action="store_true", help="打包前不清理中间产物")
    args = ap.parse_args()

    if not args.no_clean:
        clean()

    bin_path = build_binary()
    if not args.bin:
        build_deb(bin_path)


if __name__ == "__main__":
    main()
