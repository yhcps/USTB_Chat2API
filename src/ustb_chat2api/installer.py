"""
USTB Chat2API - 智能安装脚本
功能：
  - 环境分析（Python 版本、OS、pip 可用性）
  - 依赖关系分析与选择性安装
  - 一键安装大体积依赖（PyTorch 等可选）
  - 配置目录与默认配置生成
  - 服务注册（Windows 计划任务/服务）
"""
import os
import sys
import json
import subprocess
import platform
import shutil
import textwrap
from datetime import datetime

# 确保能找到包内模块
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ustb_chat2api.utils import CONFIG_DIR, LOG_DIR, CONFIG_FILE, DEFAULT_CONFIG


# ===== 环境检测 =====

def check_python() -> dict:
    """检测 Python 环境"""
    info = {
        "version": sys.version,
        "version_info": sys.version_info,
        "executable": sys.executable,
        "arch": platform.machine(),
        "os": platform.system(),
        "os_release": platform.release(),
    }
    ok = sys.version_info >= (3, 10)
    return {"ok": ok, "info": info, "message": "" if ok else "需要 Python 3.10+"}


def check_pip() -> dict:
    """检测 pip 可用性"""
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "--version"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return {"ok": True, "info": r.stdout.strip()}
        return {"ok": False, "message": r.stderr.strip()}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def check_dependencies() -> list:
    """分析已安装的依赖"""
    required = {
        "fastapi": "fastapi>=0.110",
        "uvicorn": "uvicorn>=0.29",
        "httpx": "httpx>=0.27",
        "requests": "requests>=2.31",
        "websocket-client": "websocket-client>=1.7",
        "pystray": "pystray>=0.19",
        "pillow": "pillow>=10.0",
    }
    results = []
    for name, spec in required.items():
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "show", name],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                ver_line = [l for l in r.stdout.splitlines() if l.startswith("Version:")]
                version = ver_line[0].split(":")[1].strip() if ver_line else "?"
                results.append({"name": name, "spec": spec, "installed": True, "version": version})
            else:
                results.append({"name": name, "spec": spec, "installed": False, "version": None})
        except Exception:
            results.append({"name": name, "spec": spec, "installed": False, "version": None, "error": True})
    return results


# ===== 安装动作 =====

def install_dependencies(deps: list, auto_yes: bool = False) -> bool:
    """安装缺失的依赖"""
    missing = [d for d in deps if not d.get("installed")]
    if not missing:
        print("[✓] 所有依赖已就绪")
        return True

    print(f"\n[!] 发现 {len(missing)} 个缺失依赖:")
    for d in missing:
        print(f"    - {d['name']} ({d['spec']})")

    if not auto_yes:
        ans = input("\n是否安装缺失依赖? [Y/n] ").strip().lower()
        if ans not in ("", "y", "yes"):
            print("[!] 跳过安装")
            return False

    specs = [d["spec"] for d in missing]
    cmd = [sys.executable, "-m", "pip", "install"] + specs
    print(f"\n[*] 执行: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=False, timeout=120)
    if r.returncode == 0:
        print("[✓] 依赖安装完成")
        return True
    else:
        print(f"[✗] 依赖安装失败 (exit code {r.returncode})")
        return False


def install_optional_large_packages(auto_yes: bool = False) -> bool:
    """安装可选的体积较大的包（用户按需选择）"""
    optionals = {
        "sentencepiece": "sentencepiece (用于本地 tokenizer 测试)",
        "transformers": "transformers (大模型推理框架，~2GB)",
        "torch": "torch (PyTorch 深度学习框架，~3GB)",
    }

    print("\n[可选] 以下大体积包可按需安装:")
    for name, desc in optionals.items():
        r = subprocess.run([sys.executable, "-m", "pip", "show", name],
                           capture_output=True, text=True, timeout=10)
        status = f"已安装" if r.returncode == 0 else "未安装"
        print(f"  {name:20s} - {desc:40s} [{status}]")

    if auto_yes:
        print("[*] 自动模式跳过可选安装")
        return True

    ans = input("\n是否安装以上可选包? [y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        print("[!] 跳过可选安装")
        return True

    for name in optionals:
        r = subprocess.run([sys.executable, "-m", "pip", "show", name],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            yn = input(f"  安装 {name}? [y/N] ").strip().lower()
            if yn in ("y", "yes"):
                print(f"  [*] 安装 {name}...")
                subprocess.run([sys.executable, "-m", "pip", "install", name],
                               capture_output=False, timeout=300)
    return True


# ===== 配置初始化 =====

def init_config() -> bool:
    """初始化配置目录和默认配置"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        print(f"[✓] 默认配置已创建: {CONFIG_FILE}")
        print("    [-] 请编辑 config.json 填入 cookies 后启动服务")
        print("    [-] 或使用 cli 命令: python -m ustb_chat2api cli cookie <cookies_json>")
    else:
        print(f"[*] 配置已存在: {CONFIG_FILE}")

    return True


# ===== 服务注册（Windows） =====

def register_service(auto_yes: bool = False) -> bool:
    """注册为 Windows 计划任务（开机自启）"""
    if platform.system() != "Windows":
        print("[!] 服务注册仅支持 Windows")
        return False

    script_path = os.path.join(BASE_DIR, "start_background.ps1")
    task_name = "USTB-Chat2API"

    ps1_content = textwrap.dedent(f"""\
    # USTB Chat2API - 后台静默启动脚本
    # 由 installer.py 自动生成
    $ErrorActionPreference = "SilentlyContinue"
    $python = "{sys.executable}"
    $workdir = "{BASE_DIR}"
    $log = "{os.path.join(LOG_DIR, 'service.log')}"
    Set-Location $workdir
    $proc = Start-Process -FilePath $python -ArgumentList "-m ustb_chat2api serve" -WorkingDirectory $workdir -WindowStyle Hidden -PassThru
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') PID=$($proc.Id)" | Out-File -Append $log
    """)

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(ps1_content)
    print(f"[✓] 启动脚本已创建: {script_path}")

    if not auto_yes:
        ans = input("\n注册为开机自启计划任务? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("[!] 跳过服务注册")
            return True

    # 创建计划任务
    cmd = [
        "schtasks", "/Create", "/F",
        "/SC", "ONLOGON",
        "/TN", task_name,
        "/TR", f'powershell.exe -ExecutionPolicy Bypass -File "{script_path}"',
        "/DELAY", "0000:30",
    ]
    print(f"[*] 注册计划任务: {task_name}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        print(f"[✓] 计划任务已注册: {task_name}")
        print("    [-] 下次登录时将自动启动")
        return True
    else:
        print(f"[✗] 注册失败: {r.stderr.strip()}")
        print("    [-] 请以管理员身份运行此脚本")
        return False


# ===== 主入口 =====

def print_report(env: dict, pip_info: dict, deps: list):
    """打印环境检测报告"""
    print("\n" + "=" * 60)
    print("  USTB Chat2API - 环境检测报告")
    print("=" * 60)
    print(f"  Python:    {env['info']['version_info'][0]}.{env['info']['version_info'][1]}.{env['info']['version_info'][2]}")
    print(f"  OS:        {env['info']['os']} {env['info']['os_release']}")
    print(f"  Arch:      {env['info']['arch']}")
    print(f"  Exec:      {env['info']['executable']}")
    print(f"  Pip:       {'可用' if pip_info.get('ok') else '不可用'}")
    print(f"  工作目录:  {BASE_DIR}")
    print("-" * 60)
    print("  依赖状态:")
    for d in deps:
        status = f"✓ {d['version']}" if d.get("installed") else "✗ 未安装"
        print(f"    {d['name']:20s} {status}")
    print("=" * 60)


def main():
    print("=" * 60)
    print("  USTB Chat2API - 智能安装程序")
    print("=" * 60)

    # 1. 环境检测
    print("\n[1/5] 检测运行环境...")
    env = check_python()
    if not env["ok"]:
        print(f"[✗] {env['message']}")
        sys.exit(1)
    print(f"    [✓] Python {env['info']['version_info'][0]}.{env['info']['version_info'][1]}.{env['info']['version_info'][2]}")

    pip_info = check_pip()
    if not pip_info.get("ok"):
        print(f"[!] pip 不可用: {pip_info.get('message', '未知')}")
        print("    [-] 请先安装 pip")
        sys.exit(1)

    # 2. 依赖分析
    print("\n[2/5] 分析依赖关系...")
    deps = check_dependencies()
    print_report(env, pip_info, deps)

    # 3. 安装依赖
    print("\n[3/5] 安装依赖...")
    auto_yes = "--yes" in sys.argv or "-y" in sys.argv
    install_dependencies(deps, auto_yes)

    # 4. 可选大包
    if "--with-optional" in sys.argv:
        install_optional_large_packages(auto_yes)

    # 5. 初始化配置
    print("\n[4/5] 初始化配置...")
    init_config()

    # 6. 服务注册
    print("\n[5/5] 服务注册...")
    register_service(auto_yes)

    print("\n" + "=" * 60)
    print("  安装完成!")
    print("=" * 60)
    print("  启动方式:")
    print(f"    python -m ustb_chat2api          # 后台静默运行")
    print(f"    python -m ustb_chat2api tui       # TUI 交互式控制台")
    print(f"    python -m ustb_chat2api cli       # CLI 管理工具")
    print(f"    python -m ustb_chat2api install   # 重新运行安装程序")
    print("=" * 60)


if __name__ == "__main__":
    main()
