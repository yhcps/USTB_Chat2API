@echo off
chcp 65001 >nul
title USTB Chat2API - 安装
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+ 并勾选 "Add Python to PATH"
    echo        下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist ".venv_chat2api\Scripts\python.exe" (
    echo [1/2] 创建虚拟环境 .venv_chat2api ...
    python -m venv .venv_chat2api
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败
        pause
        exit /b 1
    )
) else (
    echo [1/2] 虚拟环境已存在，跳过创建
)

echo [2/2] 安装依赖 ...
".venv_chat2api\Scripts\python.exe" -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试
    pause
    exit /b 1
)

echo.
echo ============================================
echo   安装完成！
echo   下一步: 双击 start_tray.bat 启动托盘服务
echo   首次使用请在托盘图标右键选择"登录 / 更新 Cookie"
echo ============================================
pause
