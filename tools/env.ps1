# ImageTrace development environment (dot-source in PowerShell):
#   . D:\imagetrace\tools\env.ps1
# Redirects temp/cache locations to D-drive and activates the project venv.
# Affects only the current PowerShell session; no system settings are changed.

$ImageTraceRoot = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path -LiteralPath $ImageTraceRoot)) {
    Write-Error "ImageTrace root not found: $ImageTraceRoot (refusing to fall back to C-drive)."
    return
}

$env:IMAGETRACE_ROOT      = $ImageTraceRoot
$env:TEMP                 = Join-Path $ImageTraceRoot 'data\temp'
$env:TMP                  = $env:TEMP
$env:PIP_CACHE_DIR        = Join-Path $ImageTraceRoot '.cache\pip'
$env:PYTHONPYCACHEPREFIX  = Join-Path $ImageTraceRoot '.cache\pycache'
$env:RUFF_CACHE_DIR       = Join-Path $ImageTraceRoot '.cache\ruff'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $ImageTraceRoot '.cache\pyinstaller'
$env:PYTHONDONTWRITEBYTECODE = $null

foreach ($d in @($env:TEMP, $env:PIP_CACHE_DIR, $env:PYTHONPYCACHEPREFIX, $env:RUFF_CACHE_DIR, $env:PYINSTALLER_CONFIG_DIR)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

$activate = Join-Path $ImageTraceRoot 'venv\Scripts\Activate.ps1'
if (Test-Path -LiteralPath $activate) { . $activate } else { Write-Warning "venv not found at $activate" }

Write-Host "ImageTrace env ready. Root=$ImageTraceRoot TEMP=$env:TEMP"
