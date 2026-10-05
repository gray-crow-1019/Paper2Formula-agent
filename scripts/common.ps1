Set-StrictMode -Version Latest

function Get-ProjectRoot {
    return [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
}

function Import-ProjectEnvironment {
    param([Parameter(Mandatory = $true)][string]$Root)
    $envFile = Join-Path $Root '.env'
    if (-not (Test-Path -LiteralPath $envFile)) {
        throw 'Missing .env. Run scripts/setup.ps1 or copy .env.example to .env.'
    }
    foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        if ($trimmed -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            throw 'Invalid .env line. Use KEY=value; shell commands are not supported.'
        }
        $key = $Matches[1]
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($key, $value, 'Process')
    }
}

function Resolve-CondaExecutable {
    param([string]$CondaExe)
    if ($CondaExe) {
        if (-not (Test-Path -LiteralPath $CondaExe -PathType Leaf)) {
            throw "Conda executable does not exist: $CondaExe"
        }
        return (Resolve-Path -LiteralPath $CondaExe).Path
    }
    $command = Get-Command conda.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        $env:CONDA_EXE,
        'D:\anaconda\Scripts\conda.exe',
        'C:\ProgramData\miniconda3\Scripts\conda.exe',
        'C:\ProgramData\anaconda3\Scripts\conda.exe',
        (Join-Path $env:USERPROFILE 'miniconda3\Scripts\conda.exe'),
        (Join-Path $env:USERPROFILE 'anaconda3\Scripts\conda.exe')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw 'Conda was not found. Install Miniconda/Anaconda or pass -CondaExe with the full conda.exe path.'
}

function Invoke-CheckedNative {
    param([Parameter(Mandatory = $true)][string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Executable"
    }
}

function Get-CondaTarget {
    param([Parameter(Mandatory = $true)][string]$Root)
    $localPrefix = Join-Path $Root '.conda\env'
    if ($env:CONDA_ENV_PREFIX) {
        $prefix = $env:CONDA_ENV_PREFIX
        if (-not [System.IO.Path]::IsPathRooted($prefix)) { $prefix = Join-Path $Root $prefix }
        $prefix = [System.IO.Path]::GetFullPath($prefix)
        return @{ args = @('--prefix', $prefix); display = $prefix; prefix = $prefix }
    }
    if (Test-Path -LiteralPath (Join-Path $localPrefix 'conda-meta\history')) {
        return @{ args = @('--prefix', $localPrefix); display = $localPrefix; prefix = $localPrefix }
    }
    $name = 'paper2formula'
    if ($env:CONDA_ENV) { $name = $env:CONDA_ENV }
    if ($name -notmatch '^[A-Za-z0-9_.-]+$') { throw 'CONDA_ENV must be a simple Conda environment name.' }
    return @{ args = @('--name', $name); display = $name; prefix = $null }
}

function Find-CondaEnvironment {
    param([Parameter(Mandatory = $true)][string]$CondaExe, [Parameter(Mandatory = $true)][hashtable]$Target)
    $raw = & $CondaExe env list --json
    if ($LASTEXITCODE -ne 0) { throw 'Could not read Conda environments.' }
    $environments = ($raw -join "`n" | ConvertFrom-Json).envs
    foreach ($environment in $environments) {
        if ($Target.prefix) {
            if ([System.IO.Path]::GetFullPath($environment) -eq $Target.prefix) { return $environment }
        } elseif ((Split-Path $environment -Leaf) -eq $Target.display) { return $environment }
    }
    return $null
}

function ConvertTo-WindowsArgument {
    param([string]$Value)
    # Start-Process joins ArgumentList; preserve spaces and literal quotation marks.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Add-NodePathFallback {
    # Conda's own Node installation takes precedence after conda run activates it.
    # This also permits using an existing supported system Node while bootstrapping.
    if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { return }
    $nodeDirectory = Join-Path $env:ProgramFiles 'nodejs'
    if (Test-Path -LiteralPath (Join-Path $nodeDirectory 'npm.cmd')) {
        $env:PATH = $env:PATH + [System.IO.Path]::PathSeparator + $nodeDirectory
    }
}
