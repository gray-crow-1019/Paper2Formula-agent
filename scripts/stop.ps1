$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$projectRoot = Get-ProjectRoot
$pidFile = Join-Path $projectRoot 'data\runtime\dev-processes.json'
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host 'No recorded development session.'
    exit 0
}
$session = Get-Content -LiteralPath $pidFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ([System.IO.Path]::GetFullPath($session.root) -ne $projectRoot) { throw 'Recorded session belongs to a different project.' }
foreach ($record in $session.processes) {
    $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    if ($process.StartTime.ToUniversalTime().ToString('o') -ne $record.started) {
        Write-Warning "Skipping reused PID $($record.pid)."
        continue
    }
    # taskkill traverses this verified process's live descendants, without requiring
    # WMI access or racing a snapshot against child-process shutdown.
    & "$env:SystemRoot\System32\taskkill.exe" /PID ([string]$record.pid) /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $remaining = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
        if ($remaining -and $remaining.StartTime.ToUniversalTime().ToString('o') -eq $record.started) {
            throw "Could not stop recorded $($record.name) process."
        }
    }
    Write-Host "Stopped $($record.name)."
}
Remove-Item -LiteralPath $pidFile
