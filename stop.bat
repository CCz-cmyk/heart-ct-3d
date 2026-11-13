@echo off
echo Stopping all Heart3D services ...
taskkill /FI "WINDOWTITLE eq Heart3D-Backend*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Heart3D-Frontend*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Heart3D-MySQL*" /T /F >nul 2>&1
echo All stopped. You can close this window.
timeout /t 5 >nul
