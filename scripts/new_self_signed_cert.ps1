# Creates a self-signed code signing certificate for Lectavo builds.
#
# A self-signed certificate does NOT remove Windows SmartScreen warnings for other
# people's PCs: it proves the files were not changed after signing and gives them a
# consistent publisher name, nothing more. Only a certificate from a trusted CA
# (or an established reputation) avoids the warning.
param(
    [string]$Subject = "CN=Lectavo, O=Kou Hashizume",
    [int]$Years = 3,
    [string]$OutDir = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "signing")
)

$ErrorActionPreference = "Stop"

$existing = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object { $_.Subject -eq $Subject -and $_.NotAfter -gt (Get-Date).AddDays(30) } |
    Sort-Object NotAfter -Descending | Select-Object -First 1
if ($existing) {
    $cert = $existing
    Write-Host "Reusing existing certificate."
}
else {
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $Subject `
        -KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 `
        -KeyExportPolicy NonExportable -NotAfter (Get-Date).AddYears($Years) `
        -CertStoreLocation Cert:\CurrentUser\My
    Write-Host "Created certificate."
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$cer = Join-Path $OutDir "Lectavo-code-signing.cer"
Export-Certificate -Cert $cert -FilePath $cer -Type CERT | Out-Null

[Environment]::SetEnvironmentVariable("LECTAVO_CERT_SHA1", $cert.Thumbprint, "User")
$env:LECTAVO_CERT_SHA1 = $cert.Thumbprint

Write-Host "Subject    : $($cert.Subject)"
Write-Host "Thumbprint : $($cert.Thumbprint)"
Write-Host "Expires    : $($cert.NotAfter)"
Write-Host "Public cert: $cer"
Write-Host "LECTAVO_CERT_SHA1 was saved to your user environment; builds sign automatically."
