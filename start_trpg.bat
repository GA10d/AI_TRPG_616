@echo off
setlocal
set SCRIPT_DIR=%~dp0
title AI_TRPG_616 Launcher
echo AI_TRPG_616 launcher
echo.
powershell -NoLogo -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_trpg.ps1"
if errorlevel 1 (
  echo.
  echo Launcher exited with an error.
  pause
)
endlocal
