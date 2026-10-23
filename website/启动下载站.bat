@echo off
setlocal
cd /d "%~dp0"

set "PY=C:\ProgramData\Lenovo\AIAgent\MCPEnv\python-3.14.6-embed-amd64\python.exe"
if exist "%PY%" goto launch

where python >nul 2>nul
if errorlevel 1 goto missing
set "PY=python"

:launch
echo 正在启动 Darkitten Launcher 下载站...
"%PY%" "%~dp0serve.py" %*
echo.
echo 下载站已停止。按任意键关闭窗口。
pause >nul
exit /b 0

:missing
echo 未找到 Python。请安装 Python 3.10 或更高版本。
pause
exit /b 1
