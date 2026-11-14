@echo off
setlocal
cd /d "%~dp0"

rem 1) Private runtime prepared by bootstrap_runtime.ps1 (no system Python needed).
if exist "%~dp0pyruntime\pythonw.exe" if exist "%~dp0pyruntime\Lib\site-packages\PySide6" (
    start "" "%~dp0pyruntime\pythonw.exe" "%~dp0app.py" %*
    exit /b 0
)

rem 2) A system Python that already has PySide6 installed.
where pythonw >nul 2>nul
if errorlevel 1 goto bootstrap
python -c "import PySide6" >nul 2>nul
if errorlevel 1 goto bootstrap
start "" pythonw "%~dp0app.py" %*
exit /b 0

:bootstrap
echo.
echo Darkitten Launcher needs Python with PySide6.
echo Setting up a private runtime now: one-time download, about 150 MB.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap_runtime.ps1"
if errorlevel 1 goto failed
if not exist "%~dp0pyruntime\pythonw.exe" goto failed
start "" "%~dp0pyruntime\pythonw.exe" "%~dp0app.py" %*
exit /b 0

:failed
echo.
echo Runtime setup failed. Check the network connection, then run this file again.
pause
exit /b 1
