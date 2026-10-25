param(
    [ValidateSet('Start', 'Status', 'Restart', 'Stop')][string]$Action = 'Status',
    [ValidateSet('127.0.0.1', '0.0.0.0')][string]$BackendHost = '127.0.0.1',
    [int]$BackendPort = 8001,
    [int]$AdminPort = 5174
)
$ErrorActionPreference = 'Stop'
$backendRoot = Split-Path -Parent $PSScriptRoot
$adminRoot = Join-Path (Split-Path -Parent $backendRoot) 'CampusQA-admin-users'
$python = Join-Path $backendRoot '.venv\Scripts\python.exe'
$statePath = Join-Path $backendRoot 'data\cache\local-test-processes.json'
$logRoot = Join-Path $backendRoot 'data\logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$managed = @{}
if (Test-Path -LiteralPath $statePath) {
    $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    foreach ($property in $saved.PSObject.Properties) { $managed[$property.Name] = $property.Value }
}

function Get-OwnedProcess($record) {
    if (!$record) { return $null }
    $candidate = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.Path -eq $record.executable -and
        $candidate.StartTime.ToUniversalTime().Ticks.ToString() -eq $record.startedTicks) {
        return $candidate
    }
    return $null
}

function Stop-OwnedProcesses {
    foreach ($entry in $managed.Values) {
        $owned = Get-OwnedProcess $entry
        if ($owned) { & taskkill.exe /PID $owned.Id /T /F | Out-Null }
    }
    $managed.Clear()
    '{}' | Set-Content -LiteralPath $statePath -Encoding UTF8
}

if ($Action -in @('Stop', 'Restart')) { Stop-OwnedProcesses }
if ($Action -in @('Start', 'Restart')) {
    foreach ($root in @($backendRoot, $adminRoot)) {
        $branch = & git -C $root branch --show-current
        if ($LASTEXITCODE -ne 0 -or $branch -ne 'codex/user-isolation') {
            throw "Expected codex/user-isolation in $root; current: $branch"
        }
    }
    if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath (Join-Path $backendRoot '.env'))) {
        throw 'Missing backend .venv or .env. See LOCAL_TESTING.md.'
    }
    $node = (Get-Command node.exe).Source
    $vite = Join-Path $adminRoot 'node_modules\vite\bin\vite.js'
    if (!(Test-Path -LiteralPath $vite)) { throw 'Run npm ci in the admin worktree first.' }
    $specs = @(
        @{name='backend'; port=$BackendPort; host=$BackendHost; executable=$python; directory=$backendRoot;
          arguments=@('-u', '-m', 'uvicorn', 'server:app', '--host', $BackendHost, '--port', "$BackendPort", '--workers', '1')},
        @{name='admin'; port=$AdminPort; host='127.0.0.1'; executable=$node; directory=$adminRoot;
          arguments=@($vite, '--host', '127.0.0.1', '--port', "$AdminPort", '--strictPort')}
    )
    foreach ($spec in $specs) {
        if (Get-OwnedProcess $managed[$spec.name]) { Write-Output "$($spec.name) already running"; continue }
        if (Get-NetTCPConnection -LocalPort $spec.port -State Listen -ErrorAction SilentlyContinue) {
            throw "Port $($spec.port) is occupied; no existing service was stopped."
        }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $stdout = Join-Path $logRoot "$($spec.name)-$stamp.stdout.log"
        $stderr = Join-Path $logRoot "$($spec.name)-$stamp.stderr.log"
        $env:PYTHONUTF8 = '1'
        $env:PYTHONUNBUFFERED = '1'
        $process = Start-Process -FilePath $spec.executable -ArgumentList $spec.arguments `
            -WorkingDirectory $spec.directory -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $managed[$spec.name] = @{
            pid=$process.Id; startedTicks=$process.StartTime.ToUniversalTime().Ticks.ToString();
            executable=$spec.executable; port=$spec.port; host=$spec.host; stdout=$stdout; stderr=$stderr
        }
        $managed | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $statePath -Encoding UTF8
        Write-Output "Started $($spec.name); PID $($process.Id); log: $stdout"
    }
}

foreach ($name in @('backend', 'admin')) {
    $record = $managed[$name]
    if (!$record) { Write-Output "$name : stopped"; continue }
    $owned = Get-OwnedProcess $record
    Write-Output "$name : $(if ($owned) {'running'} else {'exited'}); port $($record.port)"
    if (!$owned) { Write-Output "Error log: $($record.stderr)" }
}
try {
    $health = Invoke-RestMethod "http://127.0.0.1:$BackendPort/api/health" -TimeoutSec 3
    Write-Output "Backend health: $($health.status)"
} catch { Write-Output 'Backend health: not ready; check startup logs if the process remains running.' }
Write-Output "Mini Program backend: http://127.0.0.1:$BackendPort"
if ($managed['backend'] -and $managed['backend'].host -eq '0.0.0.0') {
    Write-Output "LAN access enabled on port $($managed['backend'].port); use this computer's Wi-Fi IPv4 address on the phone."
}
Write-Output "Admin: http://127.0.0.1:$AdminPort/admin/"
