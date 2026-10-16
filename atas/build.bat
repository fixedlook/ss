@echo off
REM Double-click this file to build and install the ATAS indicator.
REM It just calls build.ps1 with the execution policy bypassed.

setlocal
cd /d "%~dp0"

where powershell >nul 2>nul
if errorlevel 1 (
    echo.
    echo Windows PowerShell was not found. Open PowerShell manually and run:
    echo     powershell -ExecutionPolicy Bypass -File build.ps1
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*

echo.
pause
