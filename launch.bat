@echo off
REM Check if Python is in PATH
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo Error: Python is not found in your system's PATH.
    echo Please install Python or add it to your PATH environment variable.
    echo.
    pause
    exit /b 1
)

cd /d "%~dp0"
start "" pythonw master_launcher.py %*
exit /b 0
