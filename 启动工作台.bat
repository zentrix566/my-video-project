@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ╔══════════════════════════════════════════════╗
echo ║       🎬 AI 视频生成工作台 - 启动中...      ║
echo ╚══════════════════════════════════════════════╝
echo.

:: 优先使用虚拟环境
if exist "env\Scripts\python.exe" (
    echo 使用虚拟环境 Python...
    "env\Scripts\python.exe" start_web.py
) else (
    echo 使用系统 Python...
    python start_web.py
)

if errorlevel 1 (
    echo.
    echo 启动失败，请检查错误信息
    pause
)
