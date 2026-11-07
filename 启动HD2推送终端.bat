@echo off
chcp 65001 >nul
title HD2 真理部控制台
set "HD2_PROJECT_DIR=C:\Users\37420\lobsterai\project\HD2-Bot"
echo 正在启动 HD2 真理部控制台（浏览器操作，无命令行窗口）...
rem 先清理可能残留的旧服务进程
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8630 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
rem 后台启动 Web 控制台（python.exe 最小化窗口）
start "" /min "D:\AstrBot\backend\python\python.exe" -X utf8 "%HD2_PROJECT_DIR%\scripts\hd2_webui.py"
timeout /t 4 /nobreak >nul
start "" "http://127.0.0.1:8630"
echo 已打开控制台页面。若未自动打开，请手动访问 http://127.0.0.1:8630
pause
