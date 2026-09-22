@echo off
cd /d "%~dp0"
where python >nul 2>nul
if not errorlevel 1 (
    set "PYCMD=python"
) else (
    set "PYCMD=py"
)
start "" %PYCMD% setup_gui.py
