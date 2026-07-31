@echo off
setlocal

REM Start from this folder.
cd /d "%~dp0"

REM Use .env by default.
set "XTG_ENV_FILE=%cd%\.env"

REM Ensure server environment dependencies are installed.
call uv sync --locked
if errorlevel 1 exit /b 1

REM Auto-start miniQMT (non-blocking) then launch gateway.
call uv run ../../scripts/run_xtquant_gateway.py --autostart-miniqmt --env-file "%XTG_ENV_FILE%"

endlocal
