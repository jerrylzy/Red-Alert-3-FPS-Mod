<#
.SYNOPSIS
    Installs a patched ra3_1.12.game built with ra3_fps_patch.py, or restores the original from the .bak.

.DESCRIPTION
    Copies a build into the game's Data folder. That folder is under Program Files, so the script relaunches
    itself elevated (UAC prompt) when it is not already running as administrator.

    Safety:
      * Before anything is copied, ra3_1.12.game.bak must be the original v1.12 file
        (SHA-256 07951DA773B7A6D77ACA5ABD7F27EF8FC9647623C0BC92BB7DDF367455CDBB28). If there is no .bak yet and
        the live ra3_1.12.game is still the original, the .bak is created from it first.
      * The .bak is never modified or deleted.
      * Afterwards the live file's SHA-256 is printed and checked against the source.

.PARAMETER Build
    Path to a patched ra3_1.12.game, e.g. build\ra3_1.12_90fps.game.

.PARAMETER Restore
    Restore the original game file from the .bak.

.PARAMETER GameDir
    Folder containing ra3_1.12.game (default: the EA App install's Data folder).

.EXAMPLE
    .\ra3fps\install.ps1 -Build build\ra3_1.12_90fps.game

.EXAMPLE
    .\ra3fps\install.ps1 -Restore
#>
[CmdletBinding(DefaultParameterSetName = 'Install')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Install', Position = 0)]
    [string]$Build,

    [Parameter(Mandatory, ParameterSetName = 'Restore')]
    [switch]$Restore,

    [string]$GameDir = 'C:\Program Files\EA Games\Command and Conquer Red Alert 3\Red Alert 3\Data',

    # Set by the script itself when it relaunches elevated.
    [Parameter(DontShow)]
    [string]$LogFile
)

$ErrorActionPreference = 'Stop'
$OrigSha = '07951DA773B7A6D77ACA5ABD7F27EF8FC9647623C0BC92BB7DDF367455CDBB28'
$OrigSize = 9402712
$GameDir = $GameDir.TrimEnd('\')
$game = Join-Path $GameDir 'ra3_1.12.game'
$bak = "$game.bak"

function Get-Sha([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Write-Log([string]$Text) {
    Write-Host $Text
    if ($LogFile) { Add-Content -LiteralPath $LogFile -Value $Text }
}

function Test-Admin {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Read-only checks; run before elevating and again in the elevated process.
function Assert-Ready([string]$Source) {
    if (-not (Test-Path -LiteralPath $game -PathType Leaf)) {
        throw "ra3_1.12.game not found in '$GameDir' (use -GameDir)."
    }
    if (Test-Path -LiteralPath $bak) {
        if ((Get-Sha $bak) -ne $OrigSha) { throw "The backup '$bak' is not the original v1.12 file; aborting." }
    }
    elseif ((Get-Sha $game) -ne $OrigSha) {
        throw "There is no backup '$bak' and the game file is not the original v1.12 file; aborting."
    }
    if (-not $Restore) {
        if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { throw "Build file not found: $Source" }
        if ((Get-Item -LiteralPath $Source).Length -ne $OrigSize) {
            throw "'$Source' does not look like a ra3_1.12.game build (unexpected size)."
        }
    }
    if (Get-CimInstance -ClassName Win32_Process -Filter "Name = 'ra3_1.12.game'" -ErrorAction SilentlyContinue) {
        throw 'Red Alert 3 is running; exit the game first.'
    }
}

try {
    if ($Restore) {
        $src = $bak
        $want = $OrigSha
    }
    else {
        $src = (Resolve-Path -LiteralPath $Build).ProviderPath
        $want = Get-Sha $src
    }
    Assert-Ready $src

    if (((Get-Sha $game) -eq $want) -and ($Restore -or (Test-Path -LiteralPath $bak))) {
        Write-Log 'The game file already matches the source; nothing to copy.'
    }
    elseif (Test-Admin) {
        if (-not (Test-Path -LiteralPath $bak)) {
            Copy-Item -LiteralPath $game -Destination $bak
            if ((Get-Sha $bak) -ne $OrigSha) { throw "The new backup '$bak' does not verify; aborting." }
            Write-Log "Created backup $bak"
        }
        Copy-Item -LiteralPath $src -Destination $game -Force
        Write-Log "Copied $src -> $game"
    }
    else {
        $logDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'build'
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $log = Join-Path $logDir 'install_log.txt'
        Remove-Item -LiteralPath $log -ErrorAction SilentlyContinue
        $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"",
                     '-GameDir', "`"$GameDir`"", '-LogFile', "`"$log`"")
        if ($Restore) {
            $argList += '-Restore'
        }
        else {
            $argList += @('-Build', "`"$src`"")
        }
        Write-Host 'Requesting administrator rights to write to the game folder...'
        $hostExe = Join-Path $PSHOME 'powershell.exe'
        if ($PSVersionTable.PSEdition -eq 'Core') { $hostExe = Join-Path $PSHOME 'pwsh.exe' }
        Start-Process -FilePath $hostExe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList $argList
        if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log | ForEach-Object { Write-Host $_ } }
    }
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    exit 1
}

if ($LogFile) { exit 0 }   # elevated child: the parent verifies and reports

$live = Get-Sha $game
$bakState = if ((Test-Path -LiteralPath $bak) -and (Get-Sha $bak) -eq $OrigSha) { 'original v1.12, verified' } else { 'MISSING OR MODIFIED' }
Write-Host "backup      : $bak ($bakState)"
if ($live -eq $want) {
    if ($Restore) { Write-Host 'OK: the original game file is restored.' } else { Write-Host "OK: installed $src" }
    Write-Host "live sha256 : $live (verified)"
}
else {
    Write-Host "FAILED: the live file does not match the source (live sha256 $live, expected $want)" -ForegroundColor Red
    exit 1
}
