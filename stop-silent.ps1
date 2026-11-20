# Heart3D 停止：按端口精确停掉四个服务，MySQL 用 mysqladmin 优雅关闭
# 双击 stop-silent.bat 调用本文件

$root = $PSScriptRoot
$ErrorActionPreference = 'SilentlyContinue'

function Stop-Port([int]$Port, [string]$Name) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) { Write-Host "  $Name`t未运行"; return }
    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 600
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "  $Name`t停止失败" -ForegroundColor Yellow
    } else {
        Write-Host "  $Name`t已停止"
    }
}

Write-Host ''
Write-Host '  Heart3D 停止中...' -ForegroundColor Cyan
Write-Host ''

# MySQL 优先优雅关闭，避免下次启动走 crash recovery
$mysqladmin = Join-Path $root '_tools\mysql_extract\mysql-8.0.42-winx64\bin\mysqladmin.exe'
if ((Get-NetTCPConnection -LocalPort 3306 -State Listen -ErrorAction SilentlyContinue) -and (Test-Path $mysqladmin)) {
    & $mysqladmin -u root --port=3306 shutdown 2>$null | Out-Null
    Start-Sleep -Seconds 3
}
Stop-Port 3306 'MySQL'
Stop-Port 8000 '后端 API'
Stop-Port 5176 '前端'
Stop-Port 8902 '官网'

Write-Host ''
Write-Host '  全部已停止。' -ForegroundColor DarkGray
Write-Host ''