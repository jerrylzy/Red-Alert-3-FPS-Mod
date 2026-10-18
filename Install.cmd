@echo off
rem Red Alert 3 FPS Mod: install 90 or 60 FPS, or restore the original game file.
rem Double-click for a menu, or pass install.ps1 parameters, for example: Install.cmd -Fps 90
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0ra3fps\install.ps1" %*
set "rc=%errorlevel%"
if "%~1"=="" (
    echo.
    pause
)
exit /b %rc%
