@echo off
chcp 65001 >nul
echo ========================================
echo   USTB Chat2API - 后台静默启动
echo ========================================
echo.
echo 启动服务中...
start /b "" pythonw -m ustb_chat2api serve
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 启动失败
    pause
    exit /b 1
)
echo [✓] 服务已后台启动
echo     API: http://127.0.0.1:8787/v1
echo     日志: logs\server_error.log
echo.
pause
