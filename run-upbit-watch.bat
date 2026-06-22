@echo off
REM ============================================================
REM  Upbit Watch - launcher
REM  Starts the detector + control panel (running in WSL) in a
REM  detached tmux session, then opens the dashboard in Windows.
REM ============================================================
title Upbit Watch

echo Starting Upbit Watch in WSL...
wsl -d Ubuntu bash -lc "tmux has-session -t upbit 2>/dev/null && echo [already running] || tmux new -d -s upbit /home/rakibul/upbit-bot/run.sh"

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
