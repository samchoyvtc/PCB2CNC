@echo off
setlocal
title PCB2CNC
cd /d "%~dp0"

set "PY="
where py >nul 2>&1 && (
  py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
)
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY where python3 >nul 2>&1 && set "PY=python3"

if not defined PY (
  echo Python 3 is required. Install it from https://www.python.org/downloads/
  echo Then double-click this file again.
  echo.
  pause
  exit /b 1
)

%PY% "scripts\start_server.py"
if errorlevel 1 (
  echo.
  pause
)
endlocal
