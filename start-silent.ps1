# Heart3D 静默启动：四个服务全部以隐藏进程拉起，脚本跑完立即退出，之后关任何窗口都不影响
# 双击 start-silent.bat 调用本文件

$root = $PSScriptRoot
$ErrorActionPreference = 'SilentlyContinue'

function Test-Port([int]$Port) {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', $Port); $c.Close(); return $true } catch { return $false }
}

function Wait-Port([int]$Port, [int]$Seconds) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (Test-Port $Port) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

Write-Host ''
Write-Host '  Heart3D 启动中...' -ForegroundColor Cyan
Write-Host ''

# 1/4 MySQL
if (Test-Port 3306) {
    Write-Host '  [1/4] MySQL      已在运行'
} else {
    $base = Join-Path $root '_tools\mysql_extract\mysql-8.0.42-winx64'
    $data = Join-Path $root 'mysql-data'
    if (Test-Path (Join-Path $base 'bin\mysqld.exe')) {
        Start-Process -FilePath (Join-Path $base 'bin\mysqld.exe') `
            -ArgumentList @("--basedir=$base", "--datadir=$data", "--port=3306") `
            -WindowStyle Hidden
        if (Wait-Port 3306 30) { Write-Host '  [1/4] MySQL      已启动' }
        else { Write-Host '  [1/4] MySQL      启动失败（检查 mysql-data 是否完好）' -ForegroundColor Yellow }
    } else {
        Write-Host '  [1/4] MySQL      找不到 mysqld.exe' -ForegroundColor Yellow
    }
}

# 2/4 后端 API
if (Test-Port 8000) {
    Write-Host '  [2/4] 后端 API    已在运行'
} else {
    $py = Join-Path $root 'backend\venv\Scripts\python.exe'
    if (Test-Path $py) {
        Start-Process -FilePath $py `
            -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000') `
            -WorkingDirectory (Join-Path $root 'backend') -WindowStyle Hidden
        if (Wait-Port 8000 25) { Write-Host '  [2/4] 后端 API    已启动' }
        else { Write-Host '  [2/4] 后端 API    启动失败' -ForegroundColor Yellow }
    } else {
        Write-Host '  [2/4] 后端 API    找不到 venv 里的 python' -ForegroundColor Yellow
    }
}

# 3/4 前端
if (Test-Port 5176) {
    Write-Host '  [3/4] 前端        已在运行'
} else {
    $node = Join-Path $root '_tools\node_extract\node-v22.23.4-win-x64\node.exe'
    $vite = Join-Path $root 'frontend\node_modules\vite\bin\vite.js'
    if ((Test-Path $node) -and (Test-Path $vite)) {
        Start-Process -FilePath $node -ArgumentList @($vite,'--port','5176','--strictPort') `
            -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Hidden
        if (Wait-Port 5176 30) { Write-Host '  [3/4] 前端        已启动' }
        else { Write-Host '  [3/4] 前端        启动失败' -ForegroundColor Yellow }
    } else {
        Write-Host '  [3/4] 前端        找不到 node 或 vite' -ForegroundColor Yellow
    }
}

# 4/4 官网（静态）
if (Test-Port 8902) {
    Write-Host '  [4/4] 官网        已在运行'
} else {
    $py = Join-Path $root 'backend\venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { $py = 'python' }
    Start-Process -FilePath $py -ArgumentList @('-m','http.server','8902','--bind','127.0.0.1') `
        -WorkingDirectory (Join-Path $root 'frontend\public') -WindowStyle Hidden
    if (Wait-Port 8902 15) { Write-Host '  [4/4] 官网        已启动' }
    else { Write-Host '  [4/4] 官网        启动失败' -ForegroundColor Yellow }
}

Write-Host ''
Write-Host '  ────────────────────────────────────────'
Write-Host '  官网  http://127.0.0.1:8902/'
Write-Host '  系统  http://127.0.0.1:5176/login   (root / root)'
Write-Host '  ────────────────────────────────────────'
Write-Host ''
Write-Host '  服务已在后台独立运行，关掉这个窗口也不会停。' -ForegroundColor DarkGray
Write-Host ''

Start-Process 'http://127.0.0.1:8902/'
Start-Sleep -Seconds 2