@echo off
setlocal
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "NODE_DIR=%ROOT%\_tools\node_extract\node-v22.23.4-win-x64"
rem mysqld cannot handle non-ASCII install paths, so use an ASCII junction pointing to ROOT
set "LINK=D:\heart3d_link"
if not exist "%LINK%" mklink /J "%LINK%" "%ROOT%" >nul 2>&1
set "MYSQL_BASE=%LINK%\_tools\mysql_extract\mysql-8.0.42-winx64"
set "MYSQL_BIN=%MYSQL_BASE%\bin"
set "MYSQLD=%MYSQL_BIN%\mysqld.exe"
set "MYSQL_DATA=%LINK%\mysql-data"
set "PY=%ROOT%\backend\venv\Scripts\python.exe"
set "PSEXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

echo ==================================================
echo   Heart3D System - One-Click Start
echo ==================================================
echo.

if not exist "%PY%" (
  echo [ERROR] Python environment missing.
  echo         Run first_setup.bat once, then start again.
  echo.
  pause
  exit /b 1
)

if not exist "%ROOT%\data\dicom\case_01\IM0001.dcm" (
  echo [0/4] Generating DICOM series from volume data ...
  "%PY%" "%ROOT%\backend\make_dicom.py"
)

echo [1/4] Starting MySQL (port 3306) ...
"%PSEXE%" -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1',3306); $c.Close(); exit 0 } catch { exit 1 }"
if errorlevel 1 (
  if not exist "%MYSQL_DATA%\mysql" (
    echo   First run: initializing data dir ...
    "%MYSQLD%" --initialize-insecure --basedir="%MYSQL_BASE%" --datadir="%MYSQL_DATA%"
    "%PSEXE%" -NoProfile -Command "Start-Sleep -Seconds 5"
  )
  start "Heart3D-MySQL" /min /d "%MYSQL_BIN%" "%MYSQLD%" --basedir="%MYSQL_BASE%" --datadir="%MYSQL_DATA%" --port=3306
  "%PSEXE%" -NoProfile -Command "Start-Sleep -Seconds 12"
) else (
  echo   MySQL already running.
)

echo [2/4] Starting API (127.0.0.1:8000) ...
"%PSEXE%" -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1',8000); $c.Close(); exit 0 } catch { exit 1 }"
if errorlevel 1 (
  start "Heart3D-Backend" /min "%ROOT%\_run_backend.bat"
  "%PSEXE%" -NoProfile -Command "Start-Sleep -Seconds 8"
) else (
  echo   Backend already running.
)

echo [3/4] Starting Frontend (http://localhost:5176) ...
"%PSEXE%" -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1',5176); $c.Close(); exit 0 } catch { exit 1 }"
if errorlevel 1 (
  start "Heart3D-Frontend" /min "%ROOT%\_run_frontend.bat"
  "%PSEXE%" -NoProfile -Command "Start-Sleep -Seconds 12"
) else (
  echo   Frontend already running.
)

echo [4/4] Starting Landing Page (http://127.0.0.1:8902) ...
"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\start-landing.ps1"

start "" http://127.0.0.1:5176
start "" http://127.0.0.1:8902
echo.
echo System started.
echo   App:     http://127.0.0.1:5176
echo   Landing: http://127.0.0.1:8902
echo Login: root / root
echo Stop system: run stop.bat
"%PSEXE%" -NoProfile -Command "Start-Sleep -Seconds 8"