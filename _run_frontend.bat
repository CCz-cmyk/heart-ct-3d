@echo off
set "PATH=%~dp0_tools\node_extract\node-v22.23.4-win-x64;%PATH%"
cd /d "%~dp0frontend"
call npm run dev -- --port 5176 --strictPort
pause