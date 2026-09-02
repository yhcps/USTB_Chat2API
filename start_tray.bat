@echo off
rem 以 pythonw 无窗口启动托盘常驻程序
cd /d "%~dp0"
if not exist ".venv_chat2api\Scripts\pythonw.exe" (
    echo [提示] 尚未安装，请先双击 install.bat 完成安装
    pause
    exit /b 1
)
start "" ".venv_chat2api\Scripts\pythonw.exe" "tray.py"
