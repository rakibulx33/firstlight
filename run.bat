@echo off
REM ============================================================
REM  Upbit Watch - launcher (native Windows)
REM  Starts the detector + control panel with uvicorn and opens
REM  the dashboard in your default browser.
REM ============================================================
setlocal
cd /d "%~dp0"
title Upbit Watch

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment not found. Run setup.bat first.
  pause
  exit /b 1
)

echo Opening http://localhost:8000 in a few seconds...
start "" /min cmd /c "timeout /t 4 /nobreak >nul & start "" http://localhost:8000"

echo Starting Upbit Watch (Ctrl+C to stop)...
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
