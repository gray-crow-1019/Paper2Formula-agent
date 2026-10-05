param([string]$CondaExe)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$projectRoot = Get-ProjectRoot
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination (Join-Path $projectRoot '.env')
}
Import-ProjectEnvironment -Root $projectRoot
$condaExecutable = Resolve-CondaExecutable -CondaExe $CondaExe
$target = Get-CondaTarget -Root $projectRoot
Push-Location $projectRoot
try {
    $existing = Find-CondaEnvironment -CondaExe $condaExecutable -Target $target
    if ($existing) {
        Invoke-CheckedNative -Executable $condaExecutable -Arguments (@('env', 'update') + $target.args + @('--file', 'environment.yml'))
    } else {
        Invoke-CheckedNative -Executable $condaExecutable -Arguments (@('env', 'create') + $target.args + @('--file', 'environment.yml'))
    }
    Invoke-CheckedNative -Executable $condaExecutable -Arguments (@('run') + $target.args + @('--no-capture-output', 'npm', 'install', '--prefix', 'apps/web'))
    Write-Host "Ready. Conda environment: $($target.display). Start with: .\scripts\dev.ps1"
} finally {
    Pop-Location
}
