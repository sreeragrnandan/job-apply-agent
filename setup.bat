@echo off
REM =============================================================================
REM  ApplyPilot - One-Click Setup Script (Windows)
REM
REM  Run this from inside the cloned repo:
REM    setup.bat
REM
REM  Tip: Place an 'applypilot_content' folder next to this script
REM  with your pre-populated config files and they will be copied
REM  automatically to %USERPROFILE%\.applypilot\ automatically.
REM
REM  Expected files inside applypilot_content\:
REM    .env            (API keys)
REM    profile.json    (your personal profile)
REM    resume.txt      (plain-text resume)
REM    resume.pdf      (PDF resume)
REM    searches.yaml   (job search queries)
REM    employers.yaml  (Workday employer list)
REM    sites.yaml      (direct career sites)
REM =============================================================================

setlocal enabledelayedexpansion

REM Script directory (where setup.bat lives)
set "SCRIPT_DIR=%~dp0"
REM Remove trailing backslash
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "CONTENT_DIR=%SCRIPT_DIR%\applypilot_content"
set "ENV_DIR=%USERPROFILE%\.applypilot"

echo.
echo ================================================
echo        ApplyPilot - Environment Setup
echo ================================================
echo.

REM ── 1. Prerequisites ─────────────────────────────────────────────────────────
echo [INFO]  Checking prerequisites...

REM Add user local Python, Node, and npm directories to PATH if not already present
set "PATH=%LOCALAPPDATA%\Programs\Python\Python313;%LOCALAPPDATA%\Programs\Python\Python313\Scripts;%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;C:\Program Files\nodejs;%APPDATA%\npm;%PATH%"

REM Check Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found. Install Python 3.11+ from https://python.org
    echo         Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

REM Check Python version >= 3.11
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 (
    for /f "tokens=*" %%v in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PY_VER=%%v
    echo [ERROR] Python 3.11+ required. You have !PY_VER!. Upgrade from https://python.org
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PY_VER=%%v
echo [OK]    Python !PY_VER! found

REM Check pip
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip not available. Run: python -m ensurepip --upgrade
    pause
    exit /b 1
)
echo [OK]    pip found

REM Check Node.js (needed for auto-apply)
set "PATH=C:\Program Files\nodejs;%APPDATA%\npm;%PATH%"
where node >nul 2>&1
if errorlevel 1 (
    echo [INFO]  Node.js not found. Attempting automatic installation via winget...
    where winget >nul 2>&1
    if not errorlevel 1 (
        winget install --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
        set "PATH=C:\Program Files\nodejs;%APPDATA%\npm;%PATH%"
    )
)

where node >nul 2>&1
if errorlevel 1 (
    echo [WARN]  Node.js not found. Auto-apply will not work without it.
    echo         Install from https://nodejs.org
) else (
    for /f "tokens=*" %%v in ('node --version') do set NODE_VER=%%v
    echo [OK]    Node.js !NODE_VER! found
)

REM ── 2. Python virtual environment ────────────────────────────────────────────
echo.
echo [INFO]  Setting up Python virtual environment...

if not exist ".venv" (
    python -m venv .venv
    echo [OK]    Created .venv
) else (
    echo [INFO]  .venv already exists, skipping creation.
)

call .venv\Scripts\activate.bat
echo [OK]    Virtual environment activated

REM ── 3. Install Python dependencies ───────────────────────────────────────────
echo.
echo [INFO]  Installing applypilot (editable/dev mode)...
python -m pip install --upgrade pip --no-warn-script-location
python -m pip install -e ".[dev]" --no-warn-script-location
echo [OK]    applypilot installed

REM python-jobspy workaround (pins numpy in metadata, use --no-deps)
echo [INFO]  Installing python-jobspy (--no-deps workaround)...
python -m pip install --no-deps python-jobspy --no-warn-script-location
python -m pip install pydantic tls-client requests markdownify regex --no-warn-script-location
echo [OK]    python-jobspy installed

REM ── 4. Playwright browsers ───────────────────────────────────────────────────
echo.
echo [INFO]  Installing Playwright Chromium browser...
python -m playwright install chromium
echo [OK]    Playwright Chromium installed

REM ── 5. Claude Code CLI (auto-apply) ──────────────────────────────────────────
echo.
set "PATH=C:\Program Files\nodejs;%APPDATA%\npm;%PATH%"
where node >nul 2>&1
if not errorlevel 1 (
    where claude >nul 2>&1
    if errorlevel 1 (
        echo [INFO]  Installing Claude Code CLI...
        call npm install -g --allow-scripts=@anthropic-ai/claude-code @anthropic-ai/claude-code
        if errorlevel 1 (
            echo [WARN]  Could not install Claude Code CLI. Get it from https://claude.ai/code
        ) else (
            echo [OK]    Claude Code CLI installed
        )
    ) else (
        echo [OK]    Claude Code CLI already installed
    )
) else (
    echo [WARN]  Skipping Claude Code CLI install ^(Node.js not available^).
)

REM ── 6. Copy config files to %USERPROFILE%\.applypilot ───────────────────────
echo.
if not exist "%ENV_DIR%" mkdir "%ENV_DIR%"

if exist "%CONTENT_DIR%" (
    echo [INFO]  Found applypilot_content\ - copying your pre-populated config files...
    xcopy /E /I /Y "%CONTENT_DIR%\*" "%ENV_DIR%\" >nul
    echo [OK]    All files from applypilot_content\ copied to %ENV_DIR%\
    echo.
    echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    echo  Config restored from applypilot_content\
    echo  Destination: %ENV_DIR%\
    echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
) else (
    echo [WARN]  applypilot_content\ not found next to this script.
    echo [WARN]  Falling back to .env.example - fill in your API keys manually.
    if not exist "%ENV_DIR%\.env" (
        copy ".env.example" "%ENV_DIR%\.env" >nul
        echo [OK]    Copied .env.example to %ENV_DIR%\.env
    ) else (
        echo [WARN]  .env already exists at %ENV_DIR%\.env - skipping.
    )
    echo.
    echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    echo  ACTION REQUIRED: Add your API keys
    echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    echo.
    echo   Edit: %ENV_DIR%\.env
    echo.
    echo   GEMINI_API_KEY=^<your key^>     ^(Required - free at aistudio.google.com^)
    echo   CAPSOLVER_API_KEY=^<your key^>  ^(Optional - CAPTCHA solving^)
    echo.
    echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
)

REM ── 7. Next steps ────────────────────────────────────────────────────────────
echo.
echo Next steps:
echo.
if exist "%CONTENT_DIR%" (
    echo   1. Verify setup:       applypilot doctor
    echo   2. Start the pipeline: applypilot run
    echo   3. Auto-apply:         applypilot apply
) else (
    echo   1. Add your API keys:    notepad %ENV_DIR%\.env
    echo   2. Run the setup wizard: applypilot init
    echo   3. Verify setup:         applypilot doctor
    echo   4. Start the pipeline:   applypilot run
    echo   5. Auto-apply:           applypilot apply
)
echo.

REM ── 8. Doctor ────────────────────────────────────────────────────────────────
echo Running applypilot doctor...
echo.
applypilot doctor
if errorlevel 1 (
    echo [WARN]  Some checks failed. Fix the issues above, then re-run: applypilot doctor
)

echo.
echo Setup complete! Happy job hunting!
echo.
pause
