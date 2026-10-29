@echo off
rem GRAFT dev launcher wrapper for Windows cmd.
rem The actual startup logic lives in scripts/dev.py so the same code runs on
rem Windows, macOS, and Linux. Run this from the repository root:
rem   scripts\dev.bat
setlocal

where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0dev.py" %*
    goto :done
)

where python >nul 2>nul
if errorlevel 1 (
    echo [dev] error: Python 3 not found. Install it from https://www.python.org/downloads/ and reopen the terminal.
    exit /b 1
)

python "%~dp0dev.py" %*

:done
exit /b %ERRORLEVEL%
