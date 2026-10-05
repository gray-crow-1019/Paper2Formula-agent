param([string]$CondaExe, [switch]$WithWorker)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$projectRoot = Get-ProjectRoot
Import-ProjectEnvironment -Root $projectRoot
$condaExecutable = Resolve-CondaExecutable -CondaExe $CondaExe
$target = Get-CondaTarget -Root $projectRoot
$environmentPrefix = Find-CondaEnvironment -CondaExe $condaExecutable -Target $target
if (-not $environmentPrefix) { throw 'Conda environment is missing. Run scripts/setup.ps1 first.' }
Add-NodePathFallback
$runtimeDir = Join-Path $projectRoot 'data\runtime'
$null = New-Item -Path $runtimeDir -ItemType Directory -Force
$pidFile = Join-Path $runtimeDir 'dev-processes.json'
if (Test-Path -LiteralPath $pidFile) {
    throw 'A previous development session is recorded. Run scripts/stop.ps1 before starting another session.'
}

$launches = @(
    @{ name = 'api'; args = @('python', '-m', 'apps.api') },
    @{ name = 'web'; args = @('npm', '--prefix', 'apps/web', 'run', 'dev') }
)
if ($WithWorker) { $launches += @{ name = 'worker'; args = @('python', '-m', 'workers.run') } }
$processes = @()
try {
    foreach ($launch in $launches) {
        $arguments = (@('run') + $target.args + @('--no-capture-output') + $launch.args) | ForEach-Object { ConvertTo-WindowsArgument -Value $_ }
        $process = Start-Process -FilePath $condaExecutable -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir "$($launch.name).out.log") -RedirectStandardError (Join-Path $runtimeDir "$($launch.name).err.log")
        $processes += @{ name = $launch.name; pid = $process.Id; started = $process.StartTime.ToUniversalTime().ToString('o') }
    }
    @{ root = $projectRoot; processes = $processes } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $pidFile -Encoding UTF8
} catch {
    foreach ($record in $processes) {
        $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
        if ($process -and $process.StartTime.ToUniversalTime().ToString('o') -eq $record.started) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID ([string]$record.pid) /T /F | Out-Null
        }
    }
    throw
}
Write-Host "Web: http://localhost:$env:WEB_PORT"
Write-Host "API docs: http://$($env:API_HOST):$($env:API_PORT)/docs"
Write-Host "Logs: $runtimeDir. Stop: .\scripts\stop.ps1"
Write-Host 'First startup can take a few seconds. If a service exits, inspect its err.log.'
