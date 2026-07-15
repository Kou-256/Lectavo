param(
    [string]$IsccPath = ""
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ReleaseDir = Join-Path $Root "release"
$SourceDir = Join-Path $Root "dist\Lectavo"
$IssPath = Join-Path $Root "packaging\lectavo.iss"
$IconPath = Join-Path $Root "assets\app_icon.ico"

function Resolve-Iscc {
    if ($IsccPath) {
        if (-not (Test-Path $IsccPath)) {
            throw "ISCC.exe not found: $IsccPath"
        }
        return (Resolve-Path $IsccPath).Path
    }

    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 5\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return (Resolve-Path $candidate).Path
        }
    }

    throw "Inno Setup Compiler (ISCC.exe) was not found. Install it with: winget install --id JRSoftware.InnoSetup -e"
}

function Get-LectavoVersion {
    $VersionFile = Join-Path $Root "movie_slide_notes\version.py"
    $Text = Get-Content $VersionFile -Encoding UTF8 -Raw
    if ($Text -match '__version__\s*=\s*"([^"]+)"') {
        return $Matches[1]
    }
    throw "Could not read application version: $VersionFile"
}

function Get-LectavoPublisher {
    $VersionFile = Join-Path $Root "movie_slide_notes\version.py"
    $Text = Get-Content $VersionFile -Encoding UTF8 -Raw
    if ($Text -match 'APP_PUBLISHER\s*=\s*"([^"]+)"') {
        return $Matches[1]
    }
    throw "Could not read application publisher: $VersionFile"
}

if (-not (Test-Path $SourceDir)) {
    throw "PyInstaller output was not found. Run scripts\build_app.ps1 first: $SourceDir"
}
if (-not (Test-Path (Join-Path $SourceDir "Lectavo.exe"))) {
    throw "Lectavo.exe was not found: $SourceDir"
}
if (-not (Test-Path $IssPath)) {
    throw "Inno Setup script was not found: $IssPath"
}
if (-not (Test-Path $IconPath)) {
    throw "Application icon was not found: $IconPath"
}

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
$Version = Get-LectavoVersion
$Publisher = Get-LectavoPublisher
$Iscc = Resolve-Iscc

Write-Host "Building Lectavo-Setup.exe with Inno Setup."
& $Iscc `
    "/DAppVersion=$Version" `
    "/DAppPublisher=$Publisher" `
    "/DSourceDir=$SourceDir" `
    "/DOutputDir=$ReleaseDir" `
    "/DIconFile=$IconPath" `
    $IssPath
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup build failed."
}

$SetupPath = Join-Path $ReleaseDir "Lectavo-Setup.exe"
if (-not (Test-Path $SetupPath)) {
    throw "Lectavo-Setup.exe was not generated: $SetupPath"
}

Write-Host "Setup.exe completed: $SetupPath"
