@echo off
chcp 65001 >nul
title HD2 Bot Launcher
echo ============================================
echo   HD2 Bot Launcher (AstrBot + NapCat + WebUI)
echo ============================================
echo.

rem Project root = this script's directory (with trailing backslash)
set "HD2_PROJECT_DIR=%~dp0"

if not exist "%HD2_PROJECT_DIR%config.json" (
    echo [WARN] config.json not found.
    echo   Copy config.example.json to config.json first, then fill in:
    echo   - bot.self_id / bot.groups
    echo   - napcat.dir / napcat.token
    echo   - astrbot.exe / astrbot.python
    echo   - llm.api_key
    echo.
    pause
    exit /b 1
)

rem Read python path from config.json and launch launch.py via PowerShell.
rem (Avoid complex inline commands in cmd's for /f - parentheses break parsing.)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-Content -Raw -Encoding UTF8 -Path '%HD2_PROJECT_DIR%config.json' | ConvertFrom-Json; $py = if ($c.astrbot.python) { $c.astrbot.python } else { 'python' }; Write-Host ('[launch] Python: ' + $py); & $py -X utf8 '%HD2_PROJECT_DIR%scripts\launch.py'"

echo.
pause
