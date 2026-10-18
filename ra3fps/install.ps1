<#
.SYNOPSIS
    Installs the Red Alert 3 FPS Mod (90 or 60 FPS) into ra3_1.12.game, or restores the original from the .bak.

.DESCRIPTION
    Without parameters (for example when Install.cmd is double-clicked) a menu asks what to do.

    -Fps builds the patched file from your own original game file with ra3_fps_patch.py (next to this script) and
    installs it. The patcher runs on an installed Python 3.8 or newer. If there is none, the official portable
    ("embeddable") Python is downloaded from python.org into a temporary folder, checked against its published
    SHA-256 and deleted afterwards; nothing is installed on the system.

    -Build installs a file built earlier with ra3_fps_patch.py. -Restore puts the original back from the .bak.

    The game's Data folder is normally under Program Files, so the copy step relaunches the script elevated (UAC
    prompt) when the folder is not writable.

    Safety:
      * ra3_1.12.game.bak must be the original v1.12 file
        (SHA-256 07951DA773B7A6D77ACA5ABD7F27EF8FC9647623C0BC92BB7DDF367455CDBB28). If there is no .bak yet and
        the live ra3_1.12.game is still the original, the .bak is created from it first.
      * The .bak is never modified or deleted.
      * Nothing is written while Red Alert 3 runs from that folder.
      * Afterwards the live file's SHA-256 is printed and checked against the source.

.PARAMETER Fps
    Build and install the 90 or 60 FPS version.

.PARAMETER Build
    Install a patched ra3_1.12.game built with ra3_fps_patch.py, e.g. build\ra3_1.12_90fps.game.

.PARAMETER Restore
    Restore the original game file from the .bak.

.PARAMETER GameDir
    The game's Data folder, the game folder above it, or ra3_1.12.game itself. Default: found automatically (the
    folders above this script, the install folder recorded in the registry, then the EA App default
    C:\Program Files\EA Games\Command and Conquer Red Alert 3\Red Alert 3\Data).

.PARAMETER Python
    Python executable for the patcher (default: py -3, python or python3, whichever is Python 3.8 or newer).

.PARAMETER PortablePython
    Use a temporary portable Python even if Python is installed.

.PARAMETER NoElevate
    Fail instead of asking for administrator rights.

.EXAMPLE
    .\Install.cmd

.EXAMPLE
    .\ra3fps\install.ps1 -Fps 90

.EXAMPLE
    .\ra3fps\install.ps1 -Build build\ra3_1.12_90fps.game

.EXAMPLE
    .\ra3fps\install.ps1 -Restore
#>
[CmdletBinding(DefaultParameterSetName = 'Menu')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Fps')]
    [ValidateSet('90', '60')]
    [string]$Fps,

    [Parameter(Mandatory, ParameterSetName = 'Build', Position = 0)]
    [string]$Build,

    [Parameter(Mandatory, ParameterSetName = 'Restore')]
    [switch]$Restore,

    [string]$GameDir,

    [string]$Python,

    [switch]$PortablePython,

    [switch]$NoElevate,

    # Set by the script itself when it relaunches elevated.
    [Parameter(DontShow)]
    [string]$LogFile
)

$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -ne 'Core') {
    # Started from PowerShell 7 (e.g. Install.cmd in a pwsh terminal), PSModulePath lists its module folders first;
    # Windows PowerShell would then fail to load cmdlets such as Get-FileHash from them.
    $env:PSModulePath = ((@("$PSHOME\Modules") + ($env:PSModulePath -split ';')) |
        Where-Object { $_ -and $_ -notmatch '\\PowerShell\\' } | Select-Object -Unique) -join ';'
}
$OrigSha = '07951DA773B7A6D77ACA5ABD7F27EF8FC9647623C0BC92BB7DDF367455CDBB28'
$OrigSize = 9402712
$GameFileName = 'ra3_1.12.game'
$DefaultDataDir = 'C:\Program Files\EA Games\Command and Conquer Red Alert 3\Red Alert 3\Data'
$RegistryKeys = @(
    'HKLM:\SOFTWARE\WOW6432Node\Electronic Arts\Electronic Arts\Red Alert 3',
    'HKLM:\SOFTWARE\Electronic Arts\Electronic Arts\Red Alert 3'
)
# Portable Python: the python.org "Windows embeddable package" per OS architecture, with its published SHA-256.
$PortablePythonVersion = '3.14.8'
$PortablePythonUrl = "https://www.python.org/ftp/python/$PortablePythonVersion/"
$PortablePythonPackages = @{
    AMD64 = @('python-3.14.8-embed-amd64.zip', 'A93ABE456AB01BD96D7A085B3CDB6566B3063F4241360D114142FBDB07F0A310')
    ARM64 = @('python-3.14.8-embed-arm64.zip', '155BE84CCB57C6331CF0E39001C78A1DFAC3BE62F403F3FF5F2E29B80DDA7EBE')
    x86   = @('python-3.14.8-embed-win32.zip', 'FF153F0044ABF190D6483C02D5ADE7D9666D6220FC0E2828A5E90E99BE047C4C')
}

function Get-Sha([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Write-Log([string]$Text) {
    Write-Host $Text
    if ($LogFile) { Add-Content -LiteralPath $LogFile -Value $Text }
}

function Find-GameFile([string]$Hint) {
    if ($Hint) {
        # A given location is used on its own: the Data folder, the game folder above it, or the file itself.
        $h = $Hint.Trim().Trim('"').TrimEnd('\')
        if (-not $h) { return $null }
        $candidates = @($h, (Join-Path $h $GameFileName), (Join-Path $h "Data\$GameFileName"),
                        (Join-Path $h "Red Alert 3\Data\$GameFileName"))
    }
    else {
        $candidates = @()
        $dir = $PSScriptRoot
        for ($i = 0; $i -lt 4 -and $dir; $i++) {
            $candidates += (Join-Path $dir $GameFileName), (Join-Path $dir "Data\$GameFileName")
            $dir = Split-Path -Parent $dir
        }
        foreach ($key in $RegistryKeys) {
            $root = (Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue).'Install Dir'
            if ($root) {
                $candidates += (Join-Path $root "Data\$GameFileName"), (Join-Path $root "Red Alert 3\Data\$GameFileName")
            }
        }
        $candidates += Join-Path $DefaultDataDir $GameFileName
    }
    foreach ($c in $candidates) {
        if ((Split-Path -Leaf $c) -eq $GameFileName -and (Test-Path -LiteralPath $c -PathType Leaf)) {
            return (Get-Item -LiteralPath $c).FullName
        }
    }
    $null
}

function Get-State {
    if ((Get-Sha $game) -eq $OrigSha) { return 'original v1.12' }
    if ((Test-Path -LiteralPath $bak) -and (Get-Sha $bak) -eq $OrigSha) { return 'modified (a backup of the original exists)' }
    'modified, and there is no backup of the original'
}

function Test-GameRunning {
    foreach ($p in @(Get-CimInstance -ClassName Win32_Process -Filter "Name = '$GameFileName'" -ErrorAction SilentlyContinue)) {
        # A path that cannot be read (an elevated game) counts as running from this folder.
        if (-not $p.ExecutablePath -or $p.ExecutablePath -eq $game) { return $true }
    }
    $false
}

# Read-only checks; run before building, before elevating and again in the elevated process.
function Assert-Ready([string]$Source) {
    if (Test-Path -LiteralPath $bak) {
        if ((Get-Sha $bak) -ne $OrigSha) { throw "The backup '$bak' is not the original v1.12 file; aborting." }
    }
    elseif ((Get-Sha $game) -ne $OrigSha) {
        throw "There is no backup '$bak' and the game file is not the original v1.12 file; aborting."
    }
    if ($Source -and -not $Restore) {
        if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { throw "Build file not found: $Source" }
        if ((Get-Item -LiteralPath $Source).Length -ne $OrigSize) {
            throw "'$Source' does not look like a ra3_1.12.game build (unexpected size)."
        }
    }
    if (Test-GameRunning) { throw 'Red Alert 3 is running from this folder; exit the game first.' }
}

function Test-Writable([string]$Dir) {
    $probe = Join-Path $Dir ('ra3fps-write-test-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllBytes($probe, [byte[]]@())
        Remove-Item -LiteralPath $probe -Force
        $true
    }
    catch { $false }
}

function New-PythonCommand([string]$Exe, [string[]]$PrefixArgs = @()) {
    @{ Exe = $Exe; Args = $PrefixArgs }
}

function Test-Python($Py) {
    $ErrorActionPreference = 'Continue'
    try {
        $prefix = @($Py.Args)
        $out = & $Py.Exe @prefix -c 'import sys; print(sys.version_info >= (3, 8))' 2>$null
        ($LASTEXITCODE -eq 0) -and ("$out".Trim() -eq 'True')
    }
    catch { $false }
}

function Find-Python {
    foreach ($py in @((New-PythonCommand 'py' @('-3')), (New-PythonCommand 'python'), (New-PythonCommand 'python3'))) {
        if ((Get-Command $py.Exe -CommandType Application -ErrorAction SilentlyContinue) -and (Test-Python $py)) {
            return $py
        }
    }
    $null
}

function Get-PortablePython([string]$WorkDir) {
    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    $package = $PortablePythonPackages[$arch]
    if (-not $package) { throw "No portable Python for the '$arch' architecture; install Python 3.8 or newer and run again." }
    $name, $sha = $package
    $zip = Join-Path $WorkDir $name
    Write-Log "Downloading the portable Python $PortablePythonVersion from python.org (about 12 MB)..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $oldProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'   # the progress bar makes Invoke-WebRequest very slow in Windows PowerShell
    try { Invoke-WebRequest -UseBasicParsing -Uri ($PortablePythonUrl + $name) -OutFile $zip }
    finally { $ProgressPreference = $oldProgress }
    if ((Get-Sha $zip) -ne $sha) { throw 'The downloaded Python does not match its published SHA-256; aborting.' }
    $dir = Join-Path $WorkDir 'python'
    Expand-Archive -LiteralPath $zip -DestinationPath $dir
    $py = New-PythonCommand (Join-Path $dir 'python.exe')
    if (-not (Test-Python $py)) { throw 'The portable Python does not start.' }
    $py
}

function Invoke-Patcher($Py, [string]$Out) {
    $patcher = Join-Path $PSScriptRoot 'ra3_fps_patch.py'
    if (-not (Test-Path -LiteralPath $patcher -PathType Leaf)) { throw "ra3_fps_patch.py was not found next to this script." }
    Write-Log "Building the $Fps FPS file with $($Py.Exe)"
    $ErrorActionPreference = 'Continue'
    $prefix = @($Py.Args)
    $output = & $Py.Exe @prefix $patcher --fps $Fps --game $game --out $Out 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $output) { Write-Log "  $line" }
    if ($code -ne 0 -or -not (Test-Path -LiteralPath $Out -PathType Leaf)) {
        throw "The patcher failed (exit code $code); nothing was changed."
    }
}

function Install-File([string]$Source) {
    if (-not (Test-Path -LiteralPath $bak)) {
        Copy-Item -LiteralPath $game -Destination $bak
        if ((Get-Sha $bak) -ne $OrigSha) { throw "The new backup '$bak' does not verify; aborting." }
        Write-Log "Created backup $bak"
    }
    Copy-Item -LiteralPath $Source -Destination $game -Force
    Write-Log "Copied $Source -> $game"
}

function Invoke-Elevated([string]$Source) {
    $log = Join-Path ([IO.Path]::GetTempPath()) ('RA3-FPS-Mod-' + [guid]::NewGuid().ToString('N') + '.log')
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"",
                 '-GameDir', "`"$dataDir`"", '-LogFile', "`"$log`"")
    if ($Restore) { $argList += '-Restore' } else { $argList += @('-Build', "`"$Source`"") }
    Write-Host 'Requesting administrator rights to write to the game folder...'
    $hostExe = Join-Path $PSHOME 'powershell.exe'
    if ($PSVersionTable.PSEdition -eq 'Core') { $hostExe = Join-Path $PSHOME 'pwsh.exe' }
    try { Start-Process -FilePath $hostExe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList $argList }
    catch { throw 'Administrator rights were not granted; nothing was changed.' }
    if (Test-Path -LiteralPath $log) {
        Get-Content -LiteralPath $log | ForEach-Object { Write-Host $_ }
        Remove-Item -LiteralPath $log -ErrorAction SilentlyContinue
    }
}

function Show-Menu {
    Write-Host ''
    Write-Host 'Red Alert 3 FPS Mod'
    Write-Host "Game file : $game"
    Write-Host "Status    : $(Get-State)"
    Write-Host ''
    Write-Host '  1  Install 90 FPS (recommended)'
    Write-Host '  2  Install 60 FPS'
    Write-Host '  3  Restore the original game file'
    Write-Host '  Q  Quit'
    switch ((Read-Host 'Choose [1]').Trim().ToUpperInvariant()) {
        ''      { return '90' }
        '1'     { return '90' }
        '2'     { return '60' }
        '3'     { return 'restore' }
        default { return 'quit' }
    }
}

$work = $null
$failed = $false
try {
    $game = Find-GameFile $GameDir
    if (-not $game -and -not $GameDir -and $PSCmdlet.ParameterSetName -eq 'Menu') {
        Write-Host 'Red Alert 3 was not found automatically.'
        $game = Find-GameFile (Read-Host 'Enter the Red Alert 3 folder (the one that contains the Data folder)')
    }
    if (-not $game) {
        if ($GameDir) { throw "$GameFileName was not found at '$GameDir'." }
        throw "Red Alert 3 ($GameFileName) was not found; pass -GameDir with the game's Data folder."
    }
    $dataDir = Split-Path -Parent $game
    $bak = "$game.bak"

    if ($PSCmdlet.ParameterSetName -eq 'Menu') {
        $choice = Show-Menu
        if ($choice -eq 'quit') { exit 0 }
        if ($choice -eq 'restore') { $Restore = $true } else { $Fps = $choice }
    }
    Write-Log "Game file : $game"

    if ($Restore) {
        $src = $bak
        $want = $OrigSha
    }
    elseif ($Fps) {
        Assert-Ready $null
        $work = Join-Path ([IO.Path]::GetTempPath()) ('RA3-FPS-Mod-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $work | Out-Null
        $py = $null
        if ($Python) {
            $py = New-PythonCommand $Python
            if (-not (Test-Python $py)) { throw "'$Python' is not Python 3.8 or newer." }
        }
        elseif (-not $PortablePython) {
            $py = Find-Python
        }
        if (-not $py) { $py = Get-PortablePython $work }
        $src = Join-Path $work "ra3_1.12_${Fps}fps.game"
        Invoke-Patcher $py $src
        $want = Get-Sha $src
    }
    else {
        $src = (Resolve-Path -LiteralPath $Build).ProviderPath
        $want = Get-Sha $src
    }
    Assert-Ready $src

    if (((Get-Sha $game) -eq $want) -and ($Restore -or (Test-Path -LiteralPath $bak))) {
        Write-Log 'The game file already matches; nothing to do.'
    }
    elseif (Test-Writable $dataDir) {
        Install-File $src
    }
    elseif ($LogFile -or $NoElevate) {
        throw "No write access to '$dataDir'."
    }
    else {
        Invoke-Elevated $src
    }
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    $failed = $true
}
finally {
    if ($work) { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}
if ($failed) { exit 1 }
if ($LogFile) { exit 0 }   # elevated child: the parent verifies and reports

$live = Get-Sha $game
$bakState = 'none'
if (Test-Path -LiteralPath $bak) {
    $bakState = if ((Get-Sha $bak) -eq $OrigSha) { 'original v1.12, verified' } else { 'MODIFIED' }
}
Write-Host "backup      : $bak ($bakState)"
if ($live -eq $want) {
    if ($Restore) { Write-Host 'OK: the original game file is in place.' }
    elseif ($Fps) { Write-Host "OK: the $Fps FPS mod is installed." }
    else { Write-Host "OK: installed $src" }
    Write-Host "live sha256 : $live (verified)"
}
else {
    Write-Host "FAILED: the live file does not match (live sha256 $live, expected $want)" -ForegroundColor Red
    exit 1
}
