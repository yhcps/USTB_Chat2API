# USTB Chat2API - 后台静默启动脚本
# 由 installer.py 自动生成或手动执行
$ErrorActionPreference = "SilentlyContinue"
$workdir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "python"
$log = Join-Path $workdir "logs" "service.log"

# 确保日志目录存在
$logDir = Split-Path $log -Parent
if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

Set-Location $workdir

# 终止旧实例
$old = Get-Process -Name "python*" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*ustb_chat2api*" }
if ($old) { $old | Stop-Process -Force -ErrorAction SilentlyContinue }

# 后台静默启动
$proc = Start-Process -FilePath $python -ArgumentList "-m ustb_chat2api serve" -WorkingDirectory $workdir -WindowStyle Hidden -PassThru
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') PID=$($proc.Id) $($proc.ProcessName)" | Out-File -Append $log

# 输出状态
Write-Host "USTB Chat2API 已后台启动 (PID: $($proc.Id))"
Write-Host "日志: $log"
Write-Host "API: http://127.0.0.1:8787/v1"
