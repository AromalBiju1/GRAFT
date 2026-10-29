# GRAFT dev launcher wrapper for Windows PowerShell.
#
# The actual startup logic lives in scripts/dev.py so the same code runs on
# Windows, macOS, and Linux. Run this from the repository root:
#   .\scripts\dev.ps1
$ErrorActionPreference = "Stop"
$devScript = Join-Path $PSScriptRoot "dev.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $devScript @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $devScript @args
} else {
    Write-Host "[dev] error: Python 3 not found. Install it from https://www.python.org/downloads/ and reopen PowerShell." -ForegroundColor Red
    exit 1
}

exit $LASTEXITCODE
