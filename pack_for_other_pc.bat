@echo off
setlocal
echo ==================================================
echo   Pack Heart3D for another PC (creates a zip)
echo ==================================================
echo.
echo This will STOP all services, then create a zip on Desktop.
echo Size about 2 GB, may take several minutes.
echo.
pause
call "%~dp0stop.bat"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root='%~dp0'.TrimEnd('\'); $tmp=Join-Path $env:TEMP 'heart3d_pack'; if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }; New-Item -ItemType Directory -Path $tmp | Out-Null; $dst=Join-Path $tmp 'heart_3d_project'; robocopy $root $dst /E /XD venv __pycache__ /XF config.local.json config.local.bak_before_switch.json config.local.json.bak_before_drop_ds /NFL /NDL /NJH /NJS /NP | Out-Null; $zip=Join-Path ([Environment]::GetFolderPath('Desktop')) 'heart3d_portable.zip'; if (Test-Path $zip) { Remove-Item $zip -Force }; Compress-Archive -Path $dst -DestinationPath $zip -CompressionLevel Optimal; Remove-Item $tmp -Recurse -Force; Write-Host ('Done: ' + $zip)"
echo.
echo Finished. Copy the zip to the other PC, unzip it, run first_setup.bat there.
pause