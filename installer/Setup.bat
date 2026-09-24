@echo off
cd /d "%~dp0.."
python --version >nul 2>nul
if not errorlevel 1 (
    set "PYCMD=python"
) else (
    set "PYCMD=py"
)
start "" %PYCMD% -m bridge.ui.setup_app
