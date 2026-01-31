@echo off
chcp 65001 >nul
set TASK_NAME=InternalCodeReviewServer

net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo 请右键「以管理员身份运行」此脚本
    pause
    exit /b 1
)

schtasks /delete /tn "%TASK_NAME%" /f
if %ERRORLEVEL% equ 0 (
    echo 已删除计划任务 "%TASK_NAME%"
) else (
    echo 任务不存在或删除失败
)
pause
