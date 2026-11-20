# 由 start.bat 调用：启动官网静态服务（8902）
# 官网目录可用环境变量 HEART3D_LANDING_DIR 覆盖
# 默认指向本项目自己的 public 目录（landing.html 就放在那里），不再依赖外部路径

$ErrorActionPreference = 'Stop'

$landing = $env:HEART3D_LANDING_DIR
if (-not $landing) {
    $landing = Join-Path $PSScriptRoot 'frontend\public'
}

$entry = $null
foreach ($candidate in @('index.html', 'landing.html')) {
    if (Test-Path -LiteralPath (Join-Path $landing $candidate)) { $entry = $candidate; break }
}

if (-not $entry) {
    Write-Host "  [WARN] 未找到官网文件：$landing（需要 index.html 或 landing.html）"
    exit 1
}

$c = New-Object Net.Sockets.TcpClient
try {
    $c.Connect('127.0.0.1', 8902)
    $c.Close()
    Write-Host '  Landing page already running (8902).'
    exit 0
} catch {
}

$py = Join-Path $PSScriptRoot 'backend\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) {
    $py = 'python'
}

Start-Process -FilePath $py -ArgumentList @('-m', 'http.server', '8902', '--bind', '127.0.0.1') -WorkingDirectory $landing -WindowStyle Hidden
Start-Sleep -Seconds 2

$c2 = New-Object Net.Sockets.TcpClient
try {
    $c2.Connect('127.0.0.1', 8902)
    $c2.Close()
    Write-Host "  Landing page started: http://127.0.0.1:8902/$entry"
} catch {
    Write-Host '  [WARN] 官网启动失败，请检查端口 8902 是否被占用。'
    exit 1
}
