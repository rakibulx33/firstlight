@echo off
REM ============================================================
REM  Upbit Watch - stop (native Windows)
REM  Stops the background server window started by
REM  run-upbit-watch.bat.
REM ============================================================
title Upbit Watch - stop
echo Stopping Upbit Watch...
taskkill /FI "WINDOWTITLE eq UpbitWatchServer*" /T /F >nul 2>&1 && echo [stopped] || echo [was not running]
echo.
pause
