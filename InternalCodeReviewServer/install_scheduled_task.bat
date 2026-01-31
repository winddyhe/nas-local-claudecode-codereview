@echo off
chcp 65001 >nul
cd /d "%~dp0"
set TASK_NAME=InternalCodeReviewServer
set BAT_PATH=%~dp0start_server.bat
set VBS_PATH=%~dp0start_server_hidden.vbs

echo 安装计划任务: %TASK_NAME%
echo 启动脚本: %BAT_PATH%
echo.

:: 需要管理员权限
net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo 请右键「以管理员身份运行」此脚本
    pause
    exit /b 1
)

:: 若已存在则先删除
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo 删除已有任务...
    schtasks /delete /tn "%TASK_NAME%" /f
)

:: 创建任务：系统启动时运行，不显示命令行窗口（/ru SYSTEM 在后台会话运行）
:: 使用 start_server.bat，日志写入 InternalCodeReviewServer\logs\
schtasks /create /tn "%TASK_NAME%" /tr "\"%BAT_PATH%\"" /sc onstart /ru SYSTEM /rl highest /f
if %ERRORLEVEL% neq 0 (
    echo 创建失败。若希望「用户登录时」无窗口启动，可手动创建计划任务，操作程序填: wscript.exe "%VBS_PATH%"
    pause
    exit /b 1
)

echo.
echo 已创建计划任务 "%TASK_NAME%"，在「系统启动时」自动运行，无命令行窗口。
echo 日志目录: %~dp0logs\
echo.
echo 卸载: 运行 uninstall_scheduled_task.bat 或在「任务计划程序」中删除 "%TASK_NAME%"
pause
