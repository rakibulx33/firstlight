@echo off
REM ============================================================
REM  Upbit Watch - first-time setup (native Windows)
REM  Creates a virtual environment and installs dependencies.
REM ============================================================
setlocal
cd /d "%~dp0"
title Upbit Watch - setup

echo Creating virtual environment (.venv)...
py -3 -m venv .venv || python -m venv .venv
if errorlevel 1 (
  echo [ERROR] Could not create the virtual environment. Is Python installed?
  pause
  exit /b 1
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Dependency install failed.
  pause
  exit /b 1
)

echo.
echo  Setup complete. Start the app with:  run.bat
echo.
pause
