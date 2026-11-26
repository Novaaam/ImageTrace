@echo off
rem Launch ImageTrace from source using the project venv. Temp/cache stay on the project drive.
setlocal
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "TEMP=%ROOT%\data\temp"
set "TMP=%TEMP%"
set "PYTHONPYCACHEPREFIX=%ROOT%\.cache\pycache"
set "PYTHONPATH=%ROOT%\app\src"
"%ROOT%\venv\Scripts\python.exe" -m imagetrace %*
endlocal
