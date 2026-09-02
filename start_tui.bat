@echo off
rem 启动 TUI 控制台（需要控制台窗口，勿用 pythonw）
cd /d "%~dp0"
if not exist ".venv_chat2api\Scripts\python.exe" (
    echo [提示] 尚未安装，请先双击 install.bat 完成安装
    pause
    exit /b 1
)
start "" ".venv_chat2api\Scripts\python.exe" "tui.py"
