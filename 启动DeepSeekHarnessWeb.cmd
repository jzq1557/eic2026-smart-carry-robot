@echo off
setlocal EnableExtensions
title DeepSeek Harness Web

rem ============================================================
rem  DeepSeek Harness Web launcher - double-click to run.
rem
rem  Port and workspace are left at their defaults: dsh listens on
rem  3080 and reuses the session it last had open in this
rem  workspace. This script only brings the server up, so it
rem  changes neither of them.
rem
rem  ASCII ONLY BY DESIGN. cmd.exe reads this file with the OEM
rem  code page (936 on Chinese Windows), so a non-ASCII byte can
rem  split a line and shred set / if / npx - the window then
rem  flashes open and vanishes. Keep comments and messages in
rem  ASCII.
rem ============================================================

where.exe npx >nul 2>nul
if errorlevel 1 (
    echo [DSH] npx / npm not found in PATH.
    echo [DSH] Install Node.js from https://nodejs.org then run this again.
    echo.
    pause
    exit /b 1
)

echo [DSH] Starting DeepSeek Harness Web ...
echo [DSH] The server opens your browser once it is ready.
echo [DSH] Closing this window stops the server.
echo.

call npx -y @deepseek-ai/dsh web
if not "%errorlevel%"=="0" (
    echo.
    echo [DSH] The server exited with code %errorlevel% - the reason is above.
    echo [DSH] Press any key to retry, or close this window to stop.
    pause >nul
    call npx -y @deepseek-ai/dsh web
)

endlocal
