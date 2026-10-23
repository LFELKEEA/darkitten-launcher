@echo off
setlocal
cd /d "%~dp0"

rem Prefer the embedded Python that already has PySide6 installed.
set "PYW=C:\ProgramData\Lenovo\AIAgent\MCPEnv\python-3.14.6-embed-amd64\pythonw.exe"
if exist "%PYW%" goto launch

where pythonw >nul 2>nul
if errorlevel 1 goto missing
python -c "import PySide6" >nul 2>nul
if errorlevel 1 goto missing
set "PYW=pythonw"

:launch
start "" "%PYW%" "%~dp0app.py" %*
exit /b 0

:missing
echo Python was not found, or PySide6 is not installed.
echo Install Python 3.10+ and run:
echo     pip install PySide6-Essentials==6.12.0
pause
exit /b 1
