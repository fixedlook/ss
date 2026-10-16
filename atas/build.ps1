<#
    build.ps1 — build and install the SS Draw On Liquidity ATAS indicator.

    Does the whole job:
      1. finds your ATAS install folder
      2. reads OFT.Platform.runtimeconfig.json to learn whether your platform
         runs .NET 8 or .NET 10 (the version shown in the platform's corner does
         NOT tell you this)
      3. checks a .NET SDK is present
      4. builds the indicator
      5. copies the DLL into your ATAS indicators folder

    Usage:
      .\build.ps1
      .\build.ps1 -AtasPlatformDir "D:\ATAS Platform"
      .\build.ps1 -NoInstall
#>

[CmdletBinding()]
param(
    [string] $AtasPlatformDir,
    [switch] $NoInstall
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 1. find ATAS
Write-Step 'Locating ATAS'

function Find-AtasFolder {
    param([string] $Hint)

    $candidates = New-Object System.Collections.ArrayList
    if ($Hint) { [void]$candidates.Add($Hint) }
    [void]$candidates.Add('C:\Program Files (x86)\ATAS Platform')
    [void]$candidates.Add('C:\Program Files\ATAS Platform')
    if ($env:LOCALAPPDATA) {
        [void]$candidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\ATAS Platform'))
        [void]$candidates.Add((Join-Path $env:LOCALAPPDATA 'ATAS Platform'))
    }

    foreach ($c in $candidates) {
        if (-not $c) { continue }
        if (Test-Path (Join-Path $c 'ATAS.Indicators.dll')) { return $c }
    }
    return $null
}

$atas = Find-AtasFolder -Hint $AtasPlatformDir

if (-not $atas) {
    Write-Host ''
    Write-Host 'Could not find your ATAS install folder.' -ForegroundColor Red
    Write-Host 'It must be the folder that contains ATAS.Indicators.dll.' -ForegroundColor Red
    Write-Host ''
    Write-Host 'Find it by copying this into the Windows search box / Explorer address bar:'
    Write-Host '    ATAS.Indicators.dll' -ForegroundColor White
    Write-Host ''
    Write-Host 'Then run:'
    Write-Host '    .\build.ps1 -AtasPlatformDir "C:\path\to\that\folder"' -ForegroundColor White
    exit 1
}
Write-Ok "found: $atas"

# ------------------------------------------------------- 2. work out framework
Write-Step 'Checking your platform .NET version'

$tfm = 'net8.0-windows'
$rcPath = Join-Path $atas 'OFT.Platform.runtimeconfig.json'

if (Test-Path $rcPath) {
    try {
        $rc = Get-Content $rcPath -Raw | ConvertFrom-Json
        $raw = $null
        if ($rc.runtimeOptions -and $rc.runtimeOptions.tfm) {
            $raw = $rc.runtimeOptions.tfm
        }
        if (-not $raw -and $rc.runtimeOptions -and $rc.runtimeOptions.framework -and $rc.runtimeOptions.framework.version) {
            $raw = $rc.runtimeOptions.framework.version
        }
        if ($raw -and ($raw -match '^net(\d+)\.(\d+)')) {
            $tfm = "net$($Matches[1]).$($Matches[2])-windows"
            Write-Ok "runtimeconfig says '$raw' -> targeting $tfm"
        }
        else {
            Write-Warn2 "could not parse '$raw'; defaulting to $tfm"
        }
    }
    catch {
        Write-Warn2 "could not read runtimeconfig.json; defaulting to $tfm"
    }
}
else {
    Write-Warn2 "OFT.Platform.runtimeconfig.json not found; defaulting to $tfm"
}

# ------------------------------------------------------------- 3. check SDK
Write-Step 'Checking for the .NET SDK'

$sdks = $null
try { $sdks = & dotnet --list-sdks 2>$null } catch { $sdks = $null }

if (-not $sdks) {
    Write-Host ''
    Write-Host 'No .NET SDK found — you only have the .NET runtime.' -ForegroundColor Red
    Write-Host 'The runtime runs programs; the SDK is what compiles them.' -ForegroundColor Red
    Write-Host ''
    Write-Host 'Install it with either of these, then open a NEW terminal:' -ForegroundColor White
    Write-Host ''
    Write-Host '    winget install Microsoft.DotNet.SDK.8' -ForegroundColor White
    Write-Host '        (or)'
    Write-Host '    winget install Microsoft.DotNet.SDK.10' -ForegroundColor White
    Write-Host ''
    Write-Host 'Or download from: https://dotnet.microsoft.com/download' -ForegroundColor White
    Write-Host ''
    exit 1
}

Write-Ok 'installed SDKs:'
foreach ($line in $sdks) { Write-Host "      $line" }

# ------------------------------------------------------------------ 4. build
Write-Step 'Building'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$proj = Join-Path $root 'src\SsDrawOnLiquidity\SsDrawOnLiquidity.csproj'

if (-not (Test-Path $proj)) {
    Write-Host "Cannot find $proj" -ForegroundColor Red
    Write-Host 'Run this script from inside the extracted repository folder.' -ForegroundColor Red
    exit 1
}

& dotnet build $proj -c Release -p:AtasPlatformDir="$atas" -p:AtasTfm="$tfm"

if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'Build failed. The errors above are the useful part; paste them back and' -ForegroundColor Red
    Write-Host 'they can be fixed. Most common cause: wrong .NET target for your platform.' -ForegroundColor Red
    exit $LASTEXITCODE
}

$dll = Join-Path $root 'src\SsDrawOnLiquidity\bin\Release\SsDrawOnLiquidity.dll'
if (-not (Test-Path $dll)) {
    Write-Host "Build reported success but $dll is missing." -ForegroundColor Red
    Write-Host 'Search the bin folder for SsDrawOnLiquidity.dll and use that path.' -ForegroundColor Red
    exit 1
}
Write-Ok "built: $dll"

# ---------------------------------------------------------------- 5. install
if ($NoInstall) {
    Write-Host ''
    Write-Host 'Skipped installing (-NoInstall).' -ForegroundColor Yellow
    Write-Host 'Install it in ATAS with: indicators window -> Add custom indicator -> pick the DLL above.'
    exit 0
}

Write-Step 'Installing into ATAS'

$targets = New-Object System.Collections.ArrayList
if ($env:APPDATA) { [void]$targets.Add((Join-Path $env:APPDATA 'ATAS\Indicators')) }
$docs = [Environment]::GetFolderPath('MyDocuments')
if ($docs) { [void]$targets.Add((Join-Path $docs 'ATAS\Indicators')) }

$dest = $null
foreach ($t in $targets) {
    if (Test-Path $t) { $dest = $t; break }
}
if (-not $dest -and $targets.Count -gt 0) {
    $dest = $targets[0]
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    Write-Warn2 "created $dest"
}

if (-not $dest) {
    Write-Host 'Could not determine the ATAS indicators folder.' -ForegroundColor Yellow
    Write-Host "Copy this file there by hand: $dll"
    exit 0
}

Copy-Item -Path $dll -Destination $dest -Force
Write-Ok "installed to: $dest"

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
Write-Host 'In ATAS: open a chart, press Ctrl+I, and add "SS Draw On Liquidity" (category SS).'
Write-Host 'If it is not listed, use the "Add custom indicator" button and pick the DLL above.'
