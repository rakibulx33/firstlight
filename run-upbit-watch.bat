@echo off
REM ============================================================
REM  Upbit Watch - background launcher (native Windows)
REM  Starts the detector + control panel in a separate window,
REM  then opens the dashboard. Stop it with stop-upbit-watch.bat.
REM ============================================================
setlocal
cd /d "%~dp0"
title Upbit Watch

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment not found. Run setup.bat first.
  pause
  exit /b 1
)

echo Starting Upbit Watch server...
start "UpbitWatchServer" /min ".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000

echo Waiting for the server to come up...
timeout /t 4 /nobreak >nul

echo Opening http://localhost:8000 ...
start "" http://localhost:8000

echo.
echo  Upbit Watch is running at http://localhost:8000
echo  Both detectors auto-arm on launch (autostart).
echo  Stop it with:  stop-upbit-watch.bat
echo.
pause
