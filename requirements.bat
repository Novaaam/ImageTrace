@echo off
rem ===========================================================================
rem  ImageTrace - install / update dependencies
rem    requirements.bat           runtime + development tools (tests, linter)
rem    requirements.bat runtime   runtime only (what the app needs to run)
rem
rem  - Creates the virtual environment in .\venv if it doesn't exist.
rem  - Downloads pinned packages from PyPI (https://pypi.org) into .\venv.
rem  - Keeps pip's cache and temp files inside this folder (not on C:).
rem  Set IMAGETRACE_NOPAUSE=1 to skip the final "press any key".
rem ===========================================================================
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "VENV=%ROOT%\venv"
set "PY=%VENV%\Scripts\python.exe"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=all"
if /i "%MODE%"=="all" (
    set "REQ=%ROOT%\requirements-dev.txt"
) else if /i "%MODE%"=="runtime" (
    set "REQ=%ROOT%\requirements.txt"
) else (
    echo Usage: requirements.bat [all^|runtime]
    exit /b 2
)
if not exist "%REQ%" (
    echo [ImageTrace] Missing file: "%REQ%"
    goto fail
)

rem --- keep temp and caches on the project drive -------------------------------
set "TEMP=%ROOT%\.cache\temp"
set "TMP=%TEMP%"
set "PIP_CACHE_DIR=%ROOT%\.cache\pip"
set "PYTHONPYCACHEPREFIX=%ROOT%\.cache\pycache"
if not exist "%TEMP%\" mkdir "%TEMP%"
if not exist "%PIP_CACHE_DIR%\" mkdir "%PIP_CACHE_DIR%"

rem --- create the virtual environment if needed --------------------------------
if exist "%PY%" goto have_venv
echo [ImageTrace] Creating virtual environment in "%VENV%" ...
set "BASEPY="
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>nul
    if not errorlevel 1 (set "BASEPY=py -3.12") else (set "BASEPY=py -3")
)
if not defined BASEPY (
    where python >nul 2>nul
    if not errorlevel 1 set "BASEPY=python"
)
if not defined BASEPY (
    echo [ImageTrace] Python 3.11 or newer was not found. Install it from python.org and re-run.
    goto fail
)
%BASEPY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo [ImageTrace] Python 3.11 or newer is required ^(found an older version via: %BASEPY%^).
    goto fail
)
%BASEPY% -m venv "%VENV%"
if errorlevel 1 (
    echo [ImageTrace] Failed to create the virtual environment.
    goto fail
)

:have_venv
if not exist "%VENV%\pip.ini" (
    >"%VENV%\pip.ini" echo [global]
    >>"%VENV%\pip.ini" echo cache-dir = %ROOT%\.cache\pip
    >>"%VENV%\pip.ini" echo disable-pip-version-check = true
)

rem --- free space check (need about 1.5 GB for PySide6 and the download cache) --
"%PY%" -c "import shutil,sys; f=shutil.disk_usage(sys.argv[1]).free/1024**3; print(f'[ImageTrace] Free space on project drive: {f:.1f} GB'); sys.exit(0 if f >= 1.5 else 1)" "%ROOT%"
if errorlevel 1 (
    echo [ImageTrace] Less than 1.5 GB free on the project drive. Free some space and re-run.
    goto fail
)

rem --- install ----------------------------------------------------------------------
echo [ImageTrace] Installing packages from "%REQ%" ...
echo [ImageTrace] This downloads from PyPI ^(pypi.org^) into "%VENV%".
"%PY%" -m pip install -r "%REQ%"
if errorlevel 1 (
    echo [ImageTrace] Package installation failed. See the messages above.
    goto fail
)
"%PY%" -m pip check
if errorlevel 1 echo [ImageTrace] Warning: pip reported dependency conflicts ^(see above^).

"%PY%" -c "import PySide6; print('[ImageTrace] PySide6', PySide6.__version__, 'OK')"
if errorlevel 1 goto fail

echo.
echo [ImageTrace] Done. Start the app with start.bat
if not "%IMAGETRACE_NOPAUSE%"=="1" pause
exit /b 0

:fail
if not "%IMAGETRACE_NOPAUSE%"=="1" pause
exit /b 1
