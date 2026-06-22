@echo off
title Upbit Watch - stop
echo Stopping Upbit Watch...
wsl -d Ubuntu bash -lc "tmux kill-session -t upbit 2>/dev/null && echo [stopped] || echo [was not running]"
echo.
pause
