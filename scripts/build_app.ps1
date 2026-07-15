param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SpecPath = Join-Path $Root "packaging\Lectavo.spec"
$IconPath = Join-Path $Root "assets\app_icon.ico"
$VenvPython = Join-Path $Root "movie_slide_notes\.venv\Scripts\python.exe"

function Resolve-Python {
    if ($PythonPath) {
        if (-not (Test-Path $PythonPath)) {
            throw "Python not found: $PythonPath"
        }
        return (Resolve-Path $PythonPath).Path
    }
    if (Test-Path $VenvPython) {
        return (Resolve-Path $VenvPython).Path
    }
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }
    throw "Python was not found. Developer builds require Python."
}

$Python = Resolve-Python
$env:PYTHONUTF8 = "1"

if (-not (Test-Path $SpecPath)) {
    throw "PyInstaller spec was not found: $SpecPath"
}
if (-not (Test-Path $IconPath)) {
    throw "Application icon was not found: $IconPath"
}

Push-Location $Root
try {
    $Version = & $Python -c "from movie_slide_notes.version import __version__; print(__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read application version."
    }
    Write-Host "Building Lectavo $Version with PyInstaller onedir."

    foreach ($Path in @("build", "dist")) {
        $Target = Join-Path $Root $Path
        if (Test-Path $Target) {
            Remove-Item -LiteralPath $Target -Recurse -Force
        }
    }

    & $Python -m PyInstaller $SpecPath --noconfirm --clean
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }

    $ExePath = Join-Path $Root "dist\Lectavo\Lectavo.exe"
    if (-not (Test-Path $ExePath)) {
        throw "Lectavo.exe was not generated: $ExePath"
    }

    Write-Host "PyInstaller build completed: $ExePath"
}
finally {
    Pop-Location
}
