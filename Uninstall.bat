@echo off
cd /d "%~dp0installer"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo Python was not found on PATH.
    echo Install Python 3.11+ from https://python.org ^(check "Add python.exe to PATH"
    echo during install^), then run this again.
    echo.
    pause
    exit /b 1
)

python uninstall.py
pause
