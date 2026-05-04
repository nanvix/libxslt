# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# Thin wrapper that delegates to the nanvix-zutil CLI.
# Self-bootstraps nanvix-zutil into .nanvix\venv\ if it is not already installed.

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ZArgs
)

$ErrorActionPreference = 'Stop'

$zutilVersion = if ($env:NANVIX_ZUTIL_VERSION) {
    $env:NANVIX_ZUTIL_VERSION
}
else {
    "0.7.43"
}
$zutilVersion = $zutilVersion -replace "^v", ""

$repoRoot = $PSScriptRoot
$venvDir = Join-Path $repoRoot ".nanvix\venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvZutil = Join-Path $venvDir "Scripts\nanvix-zutil.exe"

$ShimCode = @'
import os,sys;os.getuid=getattr(os,'getuid',lambda:0);os.getgid=getattr(os,'getgid',lambda:0);from nanvix_zutil.__main__ import main;sys.exit(main())
'@

$zutilGlobalVersion = try {
    & nanvix-zutil --version 2>$null
}
catch {
    $null
}

function Bootstrap {
    Write-Information "nanvix-zutil not found -- bootstrapping nanvix-zutil==${zutilVersion}..." -InformationAction Continue

    $wheelUrl = "https://github.com/nanvix/zutils/releases/download/v${zutilVersion}/nanvix_zutil-${zutilVersion}-py3-none-any.whl"

    $venvArgs = @("-m", "venv")
    if (Test-Path $venvDir) {
        $venvArgs += "--clear"
    }
    $venvArgs += $venvDir

    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @venvArgs
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python @venvArgs
    }
    elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
        & python3 @venvArgs
    }
    else {
        throw "Python 3 not found. Install Python 3 and ensure py, python, or python3 is on PATH."
    }
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        throw "venv creation failed (exit code $LASTEXITCODE)"
    }
    & $venvPython -m pip install --quiet "nanvix-zutil[lint] @ $($wheelUrl)"
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        throw "pip install failed (exit code $LASTEXITCODE)"
    }
}

$bin = $null
if ((-not (Test-Path $venvDir)) -and (-not $zutilGlobalVersion)) {
    Bootstrap
    if (-not (Test-Path $venvZutil)) {
        throw "Bootstrap completed but $venvZutil not found."
    }
    $bin = $venvZutil
}
elseif (Test-Path $venvZutil) {
    $venvVersion = try {
        & $venvPython -c $ShimCode --version 2>$null
    }
    catch {
        $null
    }
    if ($venvVersion -ne "nanvix-zutil ${zutilVersion}") {
        Write-Warning "Venv nanvix-zutil version mismatch. Expected ${zutilVersion}, found ${venvVersion}. Re-bootstrapping..."
        Bootstrap
        if (-not (Test-Path $venvZutil)) {
            throw "Bootstrap completed but $venvZutil not found."
        }
    }
    $bin = $venvZutil
}
elseif ((Test-Path $venvDir) -and (-not $zutilGlobalVersion)) {
    Write-Warning "Incomplete venv detected (binary missing). Re-running bootstrap..."
    Bootstrap
    if (-not (Test-Path $venvZutil)) {
        throw "Bootstrap completed but $venvZutil not found."
    }
    $bin = $venvZutil
}
else {
    $bin = "nanvix-zutil"
    if ($zutilGlobalVersion -ne "nanvix-zutil ${zutilVersion}") {
        Write-Warning "nanvix-zutil global install does not match expected version. Expected ${zutilVersion}, found ${zutilGlobalVersion}."
    }
}

# Extract --with-nanvix PATH before forwarding to nanvix-zutil.
$filteredArgs = [System.Collections.Generic.List[string]]::new()
$i = 0
while ($i -lt $ZArgs.Count) {
    if ($ZArgs[$i] -eq '--with-nanvix') {
        if ($i + 1 -ge $ZArgs.Count) {
            throw "ERROR: --with-nanvix requires a path argument"
        }
        $item = Get-Item -LiteralPath $ZArgs[$i + 1] -ErrorAction Stop
        if (-not $item.PSIsContainer) {
            throw "ERROR: --with-nanvix path is not a directory: $($ZArgs[$i + 1])"
        }
        $env:WITH_NANVIX = $item.FullName
        $i += 2
    }
    elseif ($ZArgs[$i] -match '^--with-nanvix=(.+)$') {
        $item = Get-Item -LiteralPath $Matches[1] -ErrorAction Stop
        if (-not $item.PSIsContainer) {
            throw "ERROR: --with-nanvix path is not a directory: $($Matches[1])"
        }
        $env:WITH_NANVIX = $item.FullName
        $i++
    }
    else {
        $filteredArgs.Add($ZArgs[$i])
        $i++
    }
}

$filteredArray = $filteredArgs.ToArray()

if ($bin -eq $venvZutil) {
    & $venvPython -c $ShimCode @filteredArray
}
else {
    & $bin @filteredArray
}
exit $LASTEXITCODE
