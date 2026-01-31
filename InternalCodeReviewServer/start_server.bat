@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "logs" mkdir logs
set LOG=logs\server_%date:~0,4%%date:~5,2%%date:~8,2%.log
echo [%date% %time%] InternalCodeReviewServer starting >> "%LOG%"
echo [%date% %time%] InternalCodeReviewServer starting

python -m uvicorn main:app --host 0.0.0.0 --port 8009 >> "%LOG%" 2>&1
echo [%date% %time%] Server exited with code %ERRORLEVEL% >> "%LOG%"
