param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DefaultPython = Join-Path $Root "movie_slide_notes\.venv\Scripts\python.exe"

if (-not $PythonPath) {
    $PythonPath = $DefaultPython
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python was not found: $PythonPath"
}
$Python = (Resolve-Path -LiteralPath $PythonPath).Path
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"

Push-Location $Root
try {
    Write-Host "Scanning publishable files for private paths, lecture names, and secrets."
    $ScanArguments = @(
        "--hidden", "-n",
        "C:\\Users\\|ILIAS サービス情報学|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|HF_TOKEN\s*=\s*[^\s#]+",
        ".",
        "--glob", "!.git/**",
        "--glob", "!.venv/**",
        "--glob", "!movie_slide_notes/.venv/**",
        "--glob", "!build/**",
        "--glob", "!dist/**",
        "--glob", "!release/**",
        "--glob", "!videos/**",
        "--glob", "!output*/**",
        "--glob", "!tmp/**",
        "--glob", "!scripts/audit_public_release.ps1",
        "--glob", "!.env"
    )
    $Matches = & rg @ScanArguments
    if ($LASTEXITCODE -eq 0) {
        $Matches | ForEach-Object { Write-Error $_ }
        throw "Potential private or secret data was found."
    }
    if ($LASTEXITCODE -ne 1) {
        throw "Source scan failed. Ensure ripgrep (rg) is installed."
    }

    if (Test-Path -LiteralPath "resources\ffmpeg\ffmpeg.exe") {
        throw "A standalone ffmpeg.exe must not be included in the public package."
    }
    if (Select-String -Path "requirements*.txt", "movie_slide_notes\requirements.txt" -Pattern "PyMuPDF|fitz" -Quiet) {
        throw "PyMuPDF remains in the dependency files."
    }
    if (Select-String -Path "licenses\DEPENDENCY_LICENSES.txt" -Pattern "PyMuPDF|AGPL|UNKNOWN" -Quiet) {
        throw "The dependency license report contains PyMuPDF, AGPL, or an unknown license. Use a clean build environment and review the report."
    }

    & $Python -m ruff format --check movie_slide_notes tests app.py
    if ($LASTEXITCODE -ne 0) { throw "Ruff format check failed." }
    & $Python -m ruff check movie_slide_notes tests app.py
    if ($LASTEXITCODE -ne 0) { throw "Ruff lint check failed." }
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "Unit tests failed." }

    Write-Host "Public release audit passed. Review PUBLIC_RELEASE_CHECKLIST.md manually."
}
finally {
    Pop-Location
}
