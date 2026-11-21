@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "DEBUG_MODE="
if /I "%~1"=="--debug" set "DEBUG_MODE=1"

rem 1) Launcher-local runtime created by bootstrap_runtime.ps1
call :probe "%~dp0pyruntime\Scripts\pythonw.exe"
if defined PYEXE goto run
call :probe "%~dp0pyruntime\pythonw.exe"
if defined PYEXE goto run
call :probe "%~dp0.venv\Scripts\pythonw.exe"
if defined PYEXE goto run

rem 2) Any installed Python that already has PySide6
for %%P in (pythonw.exe) do call :probe "%%~$PATH:P"
if defined PYEXE goto run
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python3*\pythonw.exe") do call :probe "%%~fP"
if defined PYEXE goto run
for %%P in ("C:\Python3*\pythonw.exe") do call :probe "%%~fP"
if defined PYEXE goto run
for %%P in ("C:\ProgramData\Lenovo\AIAgent\MCPEnv\python-*\pythonw.exe") do call :probe "%%~fP"
if defined PYEXE goto run
for %%P in ("%LOCALAPPDATA%\uv\python\cpython-*\pythonw.exe") do call :probe "%%~fP"
if defined PYEXE goto run
for %%P in ("%USERPROFILE%\.local\share\uv\python\cpython-*\pythonw.exe") do call :probe "%%~fP"
if defined PYEXE goto run

rem 3) The py launcher, if present
where py >nul 2>nul
if errorlevel 1 goto bootstrap
for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do call :probe_exe "%%~fP"
if defined PYEXE goto run

:bootstrap
echo.
echo Darkitten Launcher needs Python with PySide6.
if not exist "%~dp0bootstrap_runtime.ps1" goto no_bootstrap
echo Setting up a private runtime now: one-time download, about 100 MB.
echo.
where powershell >nul 2>nul
if errorlevel 1 goto no_powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap_runtime.ps1"
if errorlevel 1 goto failed
call :probe "%~dp0pyruntime\Scripts\pythonw.exe"
if not defined PYEXE call :probe "%~dp0pyruntime\pythonw.exe"
if not defined PYEXE goto failed
goto run

:no_bootstrap
echo bootstrap_runtime.ps1 is missing from this folder.
echo Re-download the launcher package, or install Python 3.12+ and run:
echo     python -m pip install PySide6-Essentials==6.12.0
goto failed

:no_powershell
echo Windows PowerShell was not found.
echo Install Python 3.12+ and run:
echo     python -m pip install PySide6-Essentials==6.12.0
goto failed

:failed
echo.
echo Launcher could not start. See the messages above.
pause
exit /b 1

:run
if defined DEBUG_MODE goto run_debug
start "" "%PYEXE%" "%~dp0app.py" %*
exit /b 0

:run_debug
for %%I in ("%PYEXE%") do set "PYCON=%%~dpIpython.exe"
if not exist "%PYCON%" set "PYCON=%PYEXE%"
echo Using: %PYCON%
"%PYCON%" "%~dp0app.py" --debug %*
set "CODE=%errorlevel%"
echo.
echo Launcher exited with code %CODE%.
pause
exit /b %CODE%

:probe
set "PYEXE="
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import PySide6" >nul 2>nul
if errorlevel 1 exit /b 0
set "PYEXE=%~1"
exit /b 0

:probe_exe
if "%~1"=="" exit /b 0
for %%I in ("%~1") do call :probe "%%~dpIpythonw.exe"
exit /b 0