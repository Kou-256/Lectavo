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
$StoreCert = $null
if (-not $Signtool) {
    # No Windows SDK: sign with PowerShell's built-in Authenticode support instead.
    if ($CertSha1) {
        $StoreCert = Get-Item "Cert:\CurrentUser\My\$CertSha1" -ErrorAction SilentlyContinue
    }
    else {
        $StoreCert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($CertPath, $CertPassword)
    }
    if (-not $StoreCert) {
        throw "signtool.exe was not found and the certificate could not be loaded."
    }
}

$Targets = @()
if ($Stage -in @("App", "All")) {
    $Targets += Join-Path $Root "dist\Lectavo\Lectavo.exe"
}
if ($Stage -in @("Installer", "All")) {
    $Targets += Get-ChildItem -LiteralPath (Join-Path $Root "release") -Filter "Lectavo-*setup*.exe" | Select-Object -ExpandProperty FullName
    $Targets += Join-Path $Root "release\Lectavo-x64.msi"
}

foreach ($Target in $Targets) {
    if (-not (Test-Path $Target)) {
        continue
    }

    if ($StoreCert) {
        Write-Host "Signing: $Target"
        $sig = $null
        foreach ($server in @($TimestampUrl, "http://timestamp.sectigo.com", $null)) {
            try {
                if ($server) {
                    $sig = Set-AuthenticodeSignature -FilePath $Target -Certificate $StoreCert -HashAlgorithm SHA256 -TimestampServer $server -ErrorAction Stop
                }
                else {
                    Write-Warning "Timestamp servers unreachable; signing without a timestamp."
                    $sig = Set-AuthenticodeSignature -FilePath $Target -Certificate $StoreCert -HashAlgorithm SHA256 -ErrorAction Stop
                }
                if ($sig.SignerCertificate) { break }
            }
            catch { Write-Warning "Signing attempt failed: $($_.Exception.Message)" }
        }
        # A self-signed certificate reports UnknownError (untrusted root) until it is trusted; only require the signature itself.
        $check = Get-AuthenticodeSignature -FilePath $Target
        if (-not $check.SignerCertificate -or $check.SignerCertificate.Thumbprint -ne $StoreCert.Thumbprint) {
            throw "Signature verification failed: $Target"
        }
        Write-Host "Signed ($($check.Status)): $Target"
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
