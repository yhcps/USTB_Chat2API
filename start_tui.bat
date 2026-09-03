@echo off
chcp 65001 >nul
echo ========================================
echo   USTB Chat2API - TUI 控制台
echo ========================================
echo.
python -m ustb_chat2api tui
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 启动失败，请先运行 install.bat 安装依赖
    pause
    exit /b 1
)
pause
