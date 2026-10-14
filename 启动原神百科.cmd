@echo off
chcp 65001 >nul
setlocal
title 原神百科
cd /d "%~dp0app"
if errorlevel 1 goto failed
echo 正在启动原神百科，请稍候。首次准备资料可能需要一些时间。
if not exist ".venv\Scripts\python.exe" (
    echo 正在配置独立 Python 环境……
    python -m venv .venv
    if errorlevel 1 goto failed
    set PIP_NO_INDEX=false
    ".venv\Scripts\python.exe" -m pip install --index-url https://pypi.org/simple -r requirements-lock.txt
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m twwiki %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo 启动未完成。请保留本窗口中的错误信息，并查看 app\README.md。
pause
exit /b 1
