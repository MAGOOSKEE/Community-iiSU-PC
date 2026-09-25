@echo off
cd /d "%~dp0"

python --version >nul 2>nul
if not errorlevel 1 (
    set "PYCMD=python"
) else (
    py --version >nul 2>nul
    if not errorlevel 1 (
        set "PYCMD=py"
    ) else (
        echo.
        echo Python was not found on PATH.
        echo Install Python 3.11+ from https://python.org ^(check "Add python.exe to PATH"
        echo during install^), then run this again.
        echo.
        pause
        exit /b 1
    )
)

java -version >nul 2>nul
if errorlevel 1 (
    echo.
    echo Java was not found on PATH.
    echo This installer needs a JDK ^(for apktool and key generation^), install one,
    echo e.g. Eclipse Temurin: https://adoptium.net/
    echo Make sure java and keytool are on PATH afterward, then run this again.
    echo.
    pause
    exit /b 1
)

start "" %PYCMD% -m bridge.ui.setup_app
