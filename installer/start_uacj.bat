@echo off
REM UACJ OBD-II Simulator — one-click laptop launcher (Windows)
REM
REM Activates the Python virtualenv (creating it on first run), then
REM starts the dashboard on http://localhost:8000 and opens it in the
REM default browser.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] First run detected. Creating Python virtualenv...
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python is not installed or not on PATH.
        echo Install Python 3.11+ from https://python.org and rerun this script.
        echo Make sure "Add Python to PATH" is checked during install.
        pause
        exit /b 1
    )
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtualenv.
        pause
        exit /b 1
    )
    echo [setup] Installing UACJ simulator package and dependencies...
    call ".venv\Scripts\activate.bat"
    pip install --upgrade pip
    pip install -e .
    if errorlevel 1 (
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
    echo [setup] Pre-loading sample vehicle sessions...
    python scripts\seed_sample_sessions.py data
)

call ".venv\Scripts\activate.bat"

REM Launch the browser after a short delay so the server has time to bind.
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8000"

echo.
echo ============================================================
echo  UACJ OBD-II Training Simulator
echo  Dashboard: http://localhost:8000
echo  Press Ctrl+C in this window to stop the server.
echo ============================================================
echo.

uacj-obd --data data serve --host 0.0.0.0 --port 8000

endlocal
