param(
    [ValidateSet("App", "Installer", "All")]
    [string]$Stage = "All"
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CertPath = $env:LECTAVO_CERT_PATH
$CertPassword = $env:LECTAVO_CERT_PASSWORD
$CertSha1 = ($env:LECTAVO_CERT_SHA1 -replace '\s', '')
$TimestampUrl = if ($env:LECTAVO_TIMESTAMP_URL) { $env:LECTAVO_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }

if (-not $CertSha1 -and (-not $CertPath -or -not (Test-Path $CertPath))) {
    Write-Warning "Code signing certificate is not configured. Signing is skipped. Set LECTAVO_CERT_SHA1 or LECTAVO_CERT_PATH to enable signing."
    exit 0
}

$Signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $Signtool) {
    $kits = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "$env:ProgramFiles\Windows Kits\10\bin"
    )
    foreach ($kit in $kits) {
        if (Test-Path $kit) {
            $found = Get-ChildItem $kit -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending |
                Select-Object -First 1
            if ($found) {
                $Signtool = $found
                break
            }
        }
    }
}
if (-not $Signtool) {
    throw "signtool.exe was not found. Install the Windows SDK to sign binaries."
}

$Targets = @()
if ($Stage -in @("App", "All")) {
    $Targets += Join-Path $Root "dist\Lectavo\Lectavo.exe"
}
if ($Stage -in @("Installer", "All")) {
    $Targets += Join-Path $Root "release\Lectavo-Setup.exe"
    $Targets += Join-Path $Root "release\Lectavo-x64.msi"
}

foreach ($Target in $Targets) {
    if (-not (Test-Path $Target)) {
        continue
    }

    $Args = @("sign", "/fd", "SHA256", "/tr", $TimestampUrl, "/td", "SHA256")
    if ($CertSha1) {
        $Args += @("/sha1", $CertSha1)
    }
    else {
        $Args += @("/f", $CertPath)
        if ($CertPassword) {
            $Args += @("/p", $CertPassword)
        }
    }
    $Args += $Target

    Write-Host "Signing: $Target"
    & $Signtool.Source @Args
    if ($LASTEXITCODE -ne 0) {
        throw "Signing failed: $Target"
    }

    Write-Host "Verifying signature: $Target"
    & $Signtool.Source verify /pa /v $Target
    if ($LASTEXITCODE -ne 0) {
        throw "Signature verification failed: $Target"
    }
}
