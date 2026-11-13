@echo off
setlocal
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PY=%ROOT%\backend\venv\Scripts\python.exe"
set "NODE_DIR=%ROOT%\_tools\node_extract\node-v22.23.4-win-x64"
set "PSEXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

echo ==================================================
echo   Heart3D System - First Setup (run once)
echo ==================================================
echo.

echo [1/4] Checking Python environment ...
if exist "%PY%" (
  "%PY%" -c "import fastapi, uvicorn, pymysql, numpy, pydicom" >nul 2>&1
  if not errorlevel 1 (
    echo   Python environment OK, skip rebuild.
    goto STEP_DATA
  )
)
where python >nul 2>&1
if errorlevel 1 (
  echo   [ERROR] Python is not installed on this PC.
  echo   Install Python 3.11.9 first, and tick "Add python.exe to PATH":
  echo   https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
  echo.
  pause
  exit /b 1
)
echo   Rebuilding virtual environment ...
if exist "%ROOT%\backend\venv" rmdir /s /q "%ROOT%\backend\venv"
python -m venv "%ROOT%\backend\venv"
if errorlevel 1 (
  echo   [ERROR] venv creation failed.
  pause
  exit /b 1
)
echo   Installing dependencies, please wait ...
"%ROOT%\backend\venv\Scripts\python.exe" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
"%ROOT%\backend\venv\Scripts\python.exe" -m pip install -r "%ROOT%\backend\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
  echo   [ERROR] pip install failed. Check your network and retry.
  pause
  exit /b 1
)
echo   Python environment ready.

:STEP_DATA
echo.
echo [2/4] Checking database files ...
if exist "%ROOT%\mysql-data\mysql" (
  echo   Database OK, data is already included.
) else (
  echo   [WARN] mysql-data missing. An empty database will be created on first start,
  echo          then run: backend\venv\Scripts\python.exe backend\seed_data.py
)

echo.
echo Checking DICOM series ...
if exist "%ROOT%\data\dicom\case_01\IM0001.dcm" (
  echo   DICOM series OK.
) else (
  echo   Generating DICOM series, please wait 1-2 minutes ...
  "%PY%" "%ROOT%\backend\make_dicom.py"
)

echo.
echo [3/4] Checking frontend dependencies ...
if exist "%ROOT%\frontend\node_modules\vite" (
  if exist "%ROOT%\frontend\node_modules\three" (
    echo   Frontend dependencies OK.
  ) else (
    echo   Installing missing frontend dependency (three) ...
    pushd "%ROOT%\frontend"
    call "%NODE_DIR%\npm.cmd" install --registry=https://registry.npmmirror.com
    popd
  )
) else (
  echo   Installing frontend dependencies, please wait ...
  pushd "%ROOT%\frontend"
  call "%NODE_DIR%\npm.cmd" install --registry=https://registry.npmmirror.com
  popd
)

echo.
echo [4/4] Creating desktop shortcut ...
"%PSEXE%" -NoProfile -Command "$ws=New-Object -ComObject WScript.Shell; $d=[Environment]::GetFolderPath('Desktop'); $l=$ws.CreateShortcut($d + '\Heart3D System.lnk'); $l.TargetPath='%ROOT%\start.bat'; $l.WorkingDirectory='%ROOT%'; $l.Save(); Write-Host '  Shortcut created on Desktop.'"

echo.
echo Setup finished.
echo   Next: double-click start.bat (or the desktop shortcut)
echo   Login: root / root
echo.
pause