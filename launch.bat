@echo off
REM =============================================================
REM  ApplyPilot — Launch UI (Windows)
REM  Double-click this file or run it in CMD from the repo root.
REM =============================================================
setlocal
cd /d "%~dp0"

echo.
echo  ================================================
echo   ApplyPilot UI Launcher
echo  ================================================
echo.

REM ── Check .venv ──────────────────────────────────────────────
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

REM ── Activate venv ────────────────────────────────────────────
call .venv\Scripts\activate.bat
echo [OK]  Virtual environment activated.

REM ── Install Flask if missing ─────────────────────────────────
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing Flask...
    python -m pip install flask --quiet
    echo [OK]  Flask installed.
) else (
    echo [OK]  Flask already installed.
)

REM ── Launch UI ────────────────────────────────────────────────
echo.
echo  Starting ApplyPilot UI at http://localhost:5000
echo  (Your browser will open automatically)
echo  Press Ctrl+C to stop.
echo.

python ui\app.py

echo.
echo  UI stopped.
pause
