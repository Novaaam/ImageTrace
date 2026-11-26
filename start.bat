@echo off
rem ===========================================================================
rem  ImageTrace - start the application
rem    start.bat            start without a console window
rem    start.bat --console  start with a console (shows errors; for troubleshooting)
rem  Temp files and caches are kept inside this folder (not on C:).
rem ===========================================================================
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PY=%ROOT%\venv\Scripts\python.exe"
set "PYW=%ROOT%\venv\Scripts\pythonw.exe"

if not exist "%PY%" (
    echo [ImageTrace] The Python environment was not found at "%ROOT%\venv".
    echo [ImageTrace] Run requirements.bat first to set it up.
    pause
    exit /b 1
)

rem Temp/cache on the project drive. Use data\temp only if it already exists, so a
rem missing data folder is still reported by the app instead of silently recreated.
if exist "%ROOT%\data\temp\" (
    set "TEMP=%ROOT%\data\temp"
) else (
    set "TEMP=%ROOT%\.cache\temp"
    if not exist "%ROOT%\.cache\temp\" mkdir "%ROOT%\.cache\temp"
)
set "TMP=%TEMP%"
set "PYTHONPYCACHEPREFIX=%ROOT%\.cache\pycache"
set "PYTHONPATH=%ROOT%\app\src"

"%PY%" -c "import PySide6, PIL" 1>nul 2>nul
if errorlevel 1 (
    echo [ImageTrace] Required packages are missing. Run requirements.bat first.
    pause
    exit /b 1
)

if /i "%~1"=="--console" (
    "%PY%" -m imagetrace
    exit /b %errorlevel%
)
start "ImageTrace" "%PYW%" -m imagetrace
exit /b 0
