# Downloads the pinned llama.cpp Windows build (Vulkan, with CPU fallbacks)
# used by Lectavo's built-in local LLM, verifies its SHA-256, and copies only
# llama-server and the libraries it needs to vendor\llama.cpp.
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$Tag = "b12445"
$Asset = "llama-$Tag-bin-win-vulkan-x64.zip"
$Sha256 = "d6c15162dbeb03470a65ab095f9a8ee1b8dfe5125352f9bc64296a5ac5dabe11"
$Url = "https://github.com/ggml-org/llama.cpp/releases/download/$Tag/$Asset"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Target = Join-Path $Root "vendor\llama.cpp"
$Stamp = Join-Path $Target "VERSION.txt"

if (-not $Force -and (Test-Path $Stamp) -and ((Get-Content $Stamp -Raw).Trim() -eq "$Tag $Sha256")) {
    Write-Host "llama.cpp $Tag is already in vendor\llama.cpp."
    exit 0
}

$Work = Join-Path ([System.IO.Path]::GetTempPath()) ("lectavo-llama-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $Work | Out-Null
try {
    $Zip = Join-Path $Work $Asset
    Write-Host "Downloading $Url"
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Zip).Hash.ToLowerInvariant()
    if ($Actual -ne $Sha256) {
        throw "llama.cpp archive hash mismatch: expected $Sha256, got $Actual"
    }
    $Extracted = Join-Path $Work "x"
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $Extracted)

    if (Test-Path $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
    # The server plus shared libraries; the other tools and their *-impl.dll are not needed.
    Get-ChildItem -LiteralPath $Extracted -File | Where-Object {
        $_.Name -eq "llama-server.exe" -or
        $_.Name -eq "llama-server-impl.dll" -or
        ($_.Extension -eq ".dll" -and $_.Name -notlike "*-impl.dll") -or
        $_.Name -like "LICENSE*"
    } | Copy-Item -Destination $Target
    Set-Content -LiteralPath $Stamp -Value "$Tag $Sha256" -Encoding ASCII
    Write-Host "llama.cpp $Tag copied to vendor\llama.cpp."
}
finally {
    Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
}
