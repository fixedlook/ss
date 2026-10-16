<#
    build.ps1 - build and install the SS Draw On Liquidity ATAS indicator.

    Finds your ATAS install, works out whether the platform runs .NET 8 or
    .NET 10, checks a .NET SDK is present, builds the indicator, and copies the
    DLL into the right ATAS indicators folder.

    If it cannot find ATAS, it tells you the one command to fix that.

    Usage:
      .\build.ps1
      .\build.ps1 -AtasPlatformDir "D:\ATAS Platform"
      .\build.ps1 -NoInstall
      .\build.ps1 -FindOnly          (just report where ATAS is)
#>

[CmdletBinding()]
param(
    [string] $AtasPlatformDir,
    [switch] $NoInstall,
    [switch] $FindOnly
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

# --------------------------------------------------------------- ATAS finding

function Test-AtasFolder {
    <#
        Returns the folder that actually contains ATAS.Indicators.dll, or $null.
        Accepts either the install root or the Bin subfolder, because different
        ATAS builds place the assemblies in either.
    #>
    param([string] $Path)

    if (-not $Path) { return $null }

    $candidates = @($Path, (Join-Path $Path 'Bin'))
    foreach ($c in $candidates) {
        if (-not $c) { continue }
        if (Test-Path -LiteralPath $c -PathType Container) {
            if (Test-Path -LiteralPath (Join-Path $c 'ATAS.Indicators.dll')) {
                return (Resolve-Path -LiteralPath $c).Path
            }
        }
    }
    return $null
}

function Find-AtasFolder {
    param([string] $Hint)

    # 0. explicit hint wins
    if ($Hint) {
        $hit = Test-AtasFolder $Hint
        if ($hit) { Write-Ok "using your -AtasPlatformDir: $hit"; return $hit }
        Write-Warn2 "-AtasPlatformDir '$Hint' does not contain ATAS.Indicators.dll; still looking"
    }

    # 1. a running ATAS process is the most reliable hint of all
    try {
        $procs = Get-Process -ErrorAction SilentlyContinue |
                 Where-Object { $_.Path -and ($_.ProcessName -match 'ATAS|OFT') }
        foreach ($p in $procs) {
            $hit = Test-AtasFolder (Split-Path -Parent $p.Path)
            if ($hit) { Write-Ok "found via running process '$($p.ProcessName)': $hit"; return $hit }
        }
    }
    catch { }

    # 2. registry uninstall entries
    $regRoots = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
    )
    foreach ($root in $regRoots) {
        try {
            foreach ($item in (Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
                $props = Get-ItemProperty -LiteralPath $item.PSPath -ErrorAction SilentlyContinue
                if (-not $props -or -not $props.DisplayName) { continue }
                if ($props.DisplayName -notmatch 'ATAS') { continue }

                foreach ($cand in @($props.InstallLocation, $props.DisplayIcon)) {
                    if (-not $cand) { continue }
                    $dir = $cand.Trim('"')
                    if (Test-Path -LiteralPath $dir -PathType Leaf) { $dir = Split-Path -Parent $dir }
                    $hit = Test-AtasFolder $dir
                    if ($hit) { Write-Ok "found via registry ('$($props.DisplayName)'): $hit"; return $hit }
                }
            }
        }
        catch { }
    }

    # 3. the usual places
    $candidates = New-Object System.Collections.ArrayList
    [void]$candidates.Add('C:\Program Files (x86)\ATAS Platform')
    [void]$candidates.Add('C:\Program Files\ATAS Platform')
    [void]$candidates.Add('C:\Program Files\ATAS X')
    [void]$candidates.Add('C:\ATAS Platform')
    [void]$candidates.Add('C:\ATAS X')
    if ($env:LOCALAPPDATA) {
        [void]$candidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\ATAS Platform'))
        [void]$candidates.Add((Join-Path $env:LOCALAPPDATA 'ATAS Platform'))
        [void]$candidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\ATAS X'))
        [void]$candidates.Add((Join-Path $env:LOCALAPPDATA 'ATAS X'))
    }
    if ($env:USERPROFILE) {
        # ATAS X is sometimes kept as a portable folder in Downloads
        [void]$candidates.Add((Join-Path $env:USERPROFILE 'Downloads\ATAS X'))
        [void]$candidates.Add((Join-Path $env:USERPROFILE 'Downloads\ATAS Platform'))
    }

    # every filesystem drive root, in case the platform was put somewhere unusual
    try {
        foreach ($drive in (Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
            if ($drive.Root) {
                [void]$candidates.Add((Join-Path $drive.Root 'ATAS Platform'))
                [void]$candidates.Add((Join-Path $drive.Root 'Program Files\ATAS Platform'))
            }
        }
    }
    catch { }

    foreach ($c in $candidates) {
        $hit = Test-AtasFolder $c
        if ($hit) { Write-Ok "found in a standard location: $hit"; return $hit }
    }

    # 3b. the user's own folders. An ATAS folder inside Downloads or on the
    #     Desktop is not standard, but it happens with a manually placed install.
    $userRoots = New-Object System.Collections.ArrayList
    if ($env:USERPROFILE) {
        [void]$userRoots.Add($env:USERPROFILE)
        foreach ($sub in @('Downloads', 'Desktop', 'Documents', 'Programs', 'Apps')) {
            [void]$userRoots.Add((Join-Path $env:USERPROFILE $sub))
        }
    }

    foreach ($root in $userRoots) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }

        $hit = Test-AtasFolder $root
        if ($hit) { Write-Ok "found under your user folder: $hit"; return $hit }

        Write-Warn2 "searching $root ..."
        try {
            $found = Get-ChildItem -LiteralPath $root -Filter 'ATAS.Indicators.dll' -Recurse -ErrorAction SilentlyContinue -Force
            $file = $found | Select-Object -First 1
            if ($file) {
                $hit = Split-Path -Parent $file.FullName
                Write-Ok "found by searching: $hit"
                return $hit
            }
        }
        catch { }
    }

    # 4. bounded recursive search of the big application folders
    $searchRoots = New-Object System.Collections.ArrayList
    if ($env:ProgramFiles) { [void]$searchRoots.Add($env:ProgramFiles) }
    if (${env:ProgramFiles(x86)}) { [void]$searchRoots.Add(${env:ProgramFiles(x86)}) }
    if ($env:LOCALAPPDATA) { [void]$searchRoots.Add((Join-Path $env:LOCALAPPDATA 'Programs')) }

    foreach ($root in $searchRoots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        Write-Warn2 "searching $root (may take a moment)..."
        try {
            $found = Get-ChildItem -LiteralPath $root -Filter 'ATAS.Indicators.dll' -Recurse -ErrorAction SilentlyContinue -Force
            $file = $found | Select-Object -First 1
            if ($file) {
                $hit = Split-Path -Parent $file.FullName
                Write-Ok "found by searching: $hit"
                return $hit
            }
        }
        catch { }
    }

    return $null
}

# --------------------------------------------------------------------- 1. ATAS
Write-Step 'Locating ATAS'

$atas = Find-AtasFolder -Hint $AtasPlatformDir

if (-not $atas) {
    Write-Host ''
    Write-Host 'Could not find your ATAS install automatically.' -ForegroundColor Red

    # common mix-up: the downloaded installer is present, but the platform itself
    # has not been installed yet
    $installer = $null
    try {
        $roots = New-Object System.Collections.ArrayList
        if ($env:USERPROFILE) { [void]$roots.Add($env:USERPROFILE) }
        foreach ($drive in (Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
            if ($drive.Root) { [void]$roots.Add($drive.Root) }
        }
        foreach ($root in $roots) {
            if (-not (Test-Path -LiteralPath $root)) { continue }
            $installer = Get-ChildItem -LiteralPath $root -Filter 'ATASPlatform*.exe' -Recurse -ErrorAction SilentlyContinue -Force | Select-Object -First 1
            if ($installer) { break }
        }
    }
    catch { }

    if ($installer) {
        Write-Host ''
        Write-Host 'Found the ATAS INSTALLER, not the platform:' -ForegroundColor Yellow
        Write-Host "    $($installer.FullName)" -ForegroundColor White
        Write-Host ''
        Write-Host 'That file only installs ATAS. Run it, finish the installation,' -ForegroundColor White
        Write-Host 'then run this script again.' -ForegroundColor White
        Write-Host ''
        exit 1
    }

    Write-Host ''
    Write-Host 'Fastest way to find it - if ATAS is currently running:' -ForegroundColor White
    Write-Host '    1. press Ctrl+Shift+Esc to open Task Manager'
    Write-Host '    2. find the ATAS process, right-click it'
    Write-Host '    3. choose "Open file location"'
    Write-Host '    4. copy that folder from the Explorer address bar'
    Write-Host ''
    Write-Host 'Or run this in PowerShell to print the folder:' -ForegroundColor White
    Write-Host '    Get-Process | Where-Object { $_.ProcessName -match "ATAS|OFT" } | Select-Object ProcessName, Path'
    Write-Host ''
    Write-Host 'Or search for the file itself in Explorer (paste into the search box on This PC):' -ForegroundColor White
    Write-Host '    ATAS.Indicators.dll'
    Write-Host ''
    Write-Host 'Then build with:' -ForegroundColor White
    Write-Host '    build.bat -AtasPlatformDir "C:\folder\you\found"' -ForegroundColor White
    Write-Host ''
    exit 1
}

$isAtasX = $false
try {
    if (Test-Path -LiteralPath (Join-Path $atas 'OFT.PlatformX.runtimeconfig.json')) { $isAtasX = $true }
    elseif ($atas -match 'ATAS X') { $isAtasX = $true }
}
catch { }

if ($isAtasX) { Write-Ok "platform: ATAS X" } else { Write-Ok "platform: classic ATAS" }
Write-Ok "ATAS folder: $atas"

if ($FindOnly) { exit 0 }

# ------------------------------------------------------------- 2. framework
Write-Step 'Checking your platform .NET version'

$tfm = 'net8.0-windows'

# Classic ATAS ships OFT.Platform.runtimeconfig.json; ATAS X ships
# OFT.PlatformX.runtimeconfig.json. Check both.
$rcCandidates = @(
    (Join-Path $atas 'OFT.PlatformX.runtimeconfig.json'),
    (Join-Path $atas 'OFT.Platform.runtimeconfig.json')
)
$rcPath = $null
foreach ($cand in $rcCandidates) {
    if (Test-Path -LiteralPath $cand) { $rcPath = $cand; break }
}

if ($rcPath) {
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
            Write-Ok "runtimeconfig says '$raw' - targeting $tfm"
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
    Write-Warn2 "no OFT.Platform*.runtimeconfig.json found; defaulting to $tfm"
}

# ---------------------------------------------------------------- 3. check SDK
Write-Step 'Checking for the .NET SDK'

# Get-Command first: if dotnet is absent entirely, invoking it would throw a
# terminating error and the readable message below would never print.
$dotnetCmd = Get-Command dotnet -ErrorAction SilentlyContinue

$sdks = $null
if ($dotnetCmd) {
    try { $sdks = & dotnet --list-sdks 2>$null } catch { $sdks = $null }
}

if (-not $dotnetCmd -or -not $sdks) {
    Write-Host ''
    Write-Host 'No .NET SDK found - you only have the .NET runtime.' -ForegroundColor Red
    Write-Host 'The runtime runs programs; the SDK is what compiles them.' -ForegroundColor Red
    Write-Host ''
    Write-Host 'Install one, then open a NEW terminal window:' -ForegroundColor White
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

# An SDK can target its own framework version or lower, never higher. ATAS X
# runs .NET 10, so a .NET 8 SDK cannot build for it - catch that here rather than
# letting the user meet NETSDK1045 from the compiler.
$tfmMajor = 0
if ($tfm -match '^net(\d+)\.') { $tfmMajor = [int]$Matches[1] }

$maxSdkMajor = 0
foreach ($line in $sdks) {
    if ($line -match '^\s*(\d+)\.') {
        $m = [int]$Matches[1]
        if ($m -gt $maxSdkMajor) { $maxSdkMajor = $m }
    }
}

if ($tfmMajor -gt 0 -and $maxSdkMajor -gt 0 -and $maxSdkMajor -lt $tfmMajor) {
    Write-Host ''
    Write-Host "Your platform runs .NET $tfmMajor, but the newest SDK installed is .NET $maxSdkMajor." -ForegroundColor Red
    Write-Host "An SDK can only build for its own version or older, so this would fail." -ForegroundColor Red
    Write-Host ''
    Write-Host 'Install the matching SDK, then open a NEW terminal window:' -ForegroundColor White
    Write-Host ''
    Write-Host "    winget install Microsoft.DotNet.SDK.$tfmMajor" -ForegroundColor White
    Write-Host ''
    Write-Host 'Then run this script again.' -ForegroundColor White
    Write-Host ''
    exit 1
}

# ------------------------------------------------------------------ 4. build
Write-Step 'Building'

$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
$proj = Join-Path $root 'src\SsDrawOnLiquidity\SsDrawOnLiquidity.csproj'

if (-not (Test-Path $proj)) {
    Write-Host "Cannot find $proj" -ForegroundColor Red
    Write-Host 'Run this script from inside the extracted repository folder.' -ForegroundColor Red
    exit 1
}

# Report exactly what is about to be compiled. A stale download previously caused
# the same compiler error twice with no way to tell which source was on disk.
$srcFile = Join-Path $root 'src\SsDrawOnLiquidity\SsDrawOnLiquidity.cs'
if (Test-Path $srcFile) {
    $srcInfo = Get-Item $srcFile
    $lineCount = (Get-Content $srcFile).Count
    $hash = (Get-FileHash $srcFile -Algorithm SHA256).Hash.Substring(0, 12)
    $modified = $srcInfo.LastWriteTime.ToString('yyyy-MM-dd HH:mm')
    Write-Ok ("source: {0} lines, sha256 {1}, modified {2}" -f $lineCount, $hash, $modified)

    # cheap sanity check so a truncated or stale file is caught before compiling
    $hasStatus = Select-String -Path $srcFile -Pattern 'private void DrawStatus' -Quiet
    $hasSubscribe = Select-String -Path $srcFile -Pattern 'SubscribeToDrawingEvents' -Quiet
    if (-not $hasStatus -or -not $hasSubscribe) {
        Write-Warn2 "this source looks incomplete or out of date:"
        if (-not $hasStatus) { Write-Warn2 "  - DrawStatus is missing" }
        if (-not $hasSubscribe) { Write-Warn2 "  - SubscribeToDrawingEvents is missing" }
        Write-Warn2 "re-download it, then run this script again"
        exit 1
    }
}

$buildArgs = @(
    'build', $proj,
    '-c', 'Release',
    "-p:AtasPlatformDir=$atas",
    "-p:AtasTfm=$tfm"
)
& dotnet @buildArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'Build failed. The errors above are the useful part - paste them back' -ForegroundColor Red
    Write-Host 'and they can be fixed. Most common cause: wrong .NET target for your' -ForegroundColor Red
    Write-Host 'platform. Force it with:  build.bat -AtasTfm net10.0-windows' -ForegroundColor Red
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
    Write-Host 'Install with: indicators window -> Add custom indicator -> pick the DLL above.'
    exit 0
}

Write-Step 'Installing into ATAS'

$targets = New-Object System.Collections.ArrayList
if ($env:APPDATA) {
    if ($isAtasX) {
        [void]$targets.Add((Join-Path $env:APPDATA 'ATAS X\Indicators'))
        [void]$targets.Add((Join-Path $env:APPDATA 'ATAS\Indicators'))
    }
    else {
        [void]$targets.Add((Join-Path $env:APPDATA 'ATAS\Indicators'))
        [void]$targets.Add((Join-Path $env:APPDATA 'ATAS X\Indicators'))
    }
}
$docs = [Environment]::GetFolderPath('MyDocuments')
if ($docs) { [void]$targets.Add((Join-Path $docs 'ATAS\Indicators')) }

# prefer a folder that already exists, so we do not invent the wrong one
$dest = $null
foreach ($t in $targets) {
    if (Test-Path -LiteralPath $t -PathType Container) { $dest = $t; break }
}
if (-not $dest -and $targets.Count -gt 0) {
    $dest = $targets[0]
    try {
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
        Write-Warn2 "created $dest"
    }
    catch {
        Write-Host "Could not create $dest" -ForegroundColor Yellow
        Write-Host "Copy this file there by hand: $dll"
        exit 0
    }
}

if (-not $dest) {
    Write-Host 'Could not determine the ATAS indicators folder.' -ForegroundColor Yellow
    Write-Host "Copy this file there by hand: $dll"
    exit 0
}

Copy-Item -LiteralPath $dll -Destination $dest -Force
Write-Ok "installed to: $dest"

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
Write-Host 'In ATAS: open a chart, press Ctrl+I, and add "SS Draw On Liquidity" (category SS).'
Write-Host 'If it is not listed, use the "Add custom indicator" button and pick the DLL above.'
