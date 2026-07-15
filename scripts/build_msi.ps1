$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ReleaseDir = Join-Path $Root "release"
$Wix = Get-Command wix.exe -ErrorAction SilentlyContinue

if (-not $Wix) {
    Write-Warning "WiX Toolset was not found. MSI generation is skipped. Use Lectavo-Setup.exe for distribution."
    exit 0
}

Write-Warning "MSI generation is not enabled in this initial release. Detected WiX: $($Wix.Source)"
Write-Warning "Setup.exe is the verified distribution path. Add packaging/lectavo.wxs later to enable MSI output."
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
exit 0
