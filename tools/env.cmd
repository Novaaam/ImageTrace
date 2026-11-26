@echo off
rem ImageTrace dev environment for cmd.exe (not affected by PowerShell execution policy).
rem Usage:  call D:\imagetrace\tools\env.cmd
set "IMAGETRACE_ROOT=%~dp0.."
for %%I in ("%IMAGETRACE_ROOT%") do set "IMAGETRACE_ROOT=%%~fI"
set "TEMP=%IMAGETRACE_ROOT%\data\temp"
set "TMP=%TEMP%"
set "PIP_CACHE_DIR=%IMAGETRACE_ROOT%\.cache\pip"
set "PYTHONPYCACHEPREFIX=%IMAGETRACE_ROOT%\.cache\pycache"
set "RUFF_CACHE_DIR=%IMAGETRACE_ROOT%\.cache\ruff"
set "PYINSTALLER_CONFIG_DIR=%IMAGETRACE_ROOT%\.cache\pyinstaller"
set "PYTHONPATH=%IMAGETRACE_ROOT%\app\src"
if not exist "%TEMP%" mkdir "%TEMP%"
call "%IMAGETRACE_ROOT%\venv\Scripts\activate.bat"
echo ImageTrace env ready. Root=%IMAGETRACE_ROOT%
