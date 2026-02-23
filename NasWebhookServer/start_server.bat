@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "logs" mkdir logs

REM 使用 PowerShell 获取可靠的日期格式
for /f "usebackq" %%I in (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) do set DATETIME=%%I
set TODAY=%DATETIME:~0,8%
set LOG=logs\server_%TODAY%.log
set TIMESTAMP=%DATETIME:~0,4%/%DATETIME:~4,2%/%DATETIME:~6,2% %DATETIME:~9,2%:%DATETIME:~11,2%:%DATETIME:~13,2%

echo [%TIMESTAMP%] NasWebhookServer starting >> "%LOG%"
echo [%TIMESTAMP%] NasWebhookServer starting
echo.
echo NasWebhookServer 启动中...
echo 监听端口: 8000
echo 转发目标: http://10.10.1.232:8009/webhook/trigger
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8000 >> "%LOG%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

REM 重新获取时间用于退出日志
for /f "usebackq" %%I in (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) do set DATETIME=%%I
set TIMESTAMP=%DATETIME:~0,4%/%DATETIME:~4,2%/%DATETIME:~6,2% %DATETIME:~9,2%:%DATETIME:~11,2%:%DATETIME:~13,2%
echo [%TIMESTAMP%] Server exited with code %EXIT_CODE% >> "%LOG%"

echo.
echo Server exited with code %EXIT_CODE%
if %EXIT_CODE% neq 0 (
    echo Error occurred! Check log: %LOG%
    type "%LOG%"
    pause
)
