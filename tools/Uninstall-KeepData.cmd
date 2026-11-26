@echo off
setlocal
set "APP_DIR=%~dp0"
set "AUTO_YES=0"
if /I "%~1"=="/Y" set "AUTO_YES=1"

echo ============================================================================
echo ImageTrace Portable Uninstaller (Preserves Case Data by Default)
echo ============================================================================
echo.
echo Application folder: %APP_DIR%
echo.
echo This removes ONLY ImageTrace program files:
echo   - ImageTrace.exe
echo   - _internal\
echo   - tools\
echo   - docs\
echo   - THIRD_PARTY_LICENSES.txt, RELEASE_NOTES.md, README.md, MANIFEST.sha256
echo.
echo It DOES NOT delete:
echo   - data\  (your database, cases, original evidence, reports, backups)
echo   - imagetrace.local.json  (your remembered data folder path)
echo   - any external data folder (such as D:\ImageTraceData\)
echo.

if "%AUTO_YES%"=="0" (
    set /p "CONFIRM=Remove ImageTrace program files while keeping your data? [y/N]: "
    if /I not "%CONFIRM%"=="Y" (
        echo Cancelled. Nothing was removed.
        exit /b 0
    )
)

if exist "%APP_DIR%ImageTrace.exe" del /F /Q "%APP_DIR%ImageTrace.exe"
if exist "%APP_DIR%_internal" rmdir /S /Q "%APP_DIR%_internal"
if exist "%APP_DIR%tools" rmdir /S /Q "%APP_DIR%tools"
if exist "%APP_DIR%docs" rmdir /S /Q "%APP_DIR%docs"
for %%F in (THIRD_PARTY_LICENSES.txt RELEASE_NOTES.md README.md MANIFEST.sha256) do (
    if exist "%APP_DIR%%%F" del /F /Q "%APP_DIR%%%F"
)

echo.
echo ImageTrace program files removed. Your case data was NOT deleted.
exit /b 0
