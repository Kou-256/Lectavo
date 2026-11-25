param(
    [string]$PythonPath = "",
    [string]$IsccPath = "",
    [switch]$SkipInstallerSmoke
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ReleaseDir = Join-Path $Root "release"
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
    throw "Python was not found. Developer release builds require Python."
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$ErrorMessage
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $ErrorMessage
    }
}

function Get-LectavoVersion {
    $VersionFile = Join-Path $Root "movie_slide_notes\version.py"
    $Text = Get-Content $VersionFile -Encoding UTF8 -Raw
    if ($Text -match '__version__\s*=\s*"([^"]+)"') {
        return $Matches[1]
    }
    throw "Could not read application version: $VersionFile"
}

function New-PortableZip {
    param(
        [string]$SourceDirectory,
        [string]$DestinationPath
    )

    # Compress-Archive draws a per-file progress bar; with ~500 files and a captured,
    # non-interactive host that has been a source of silent aborts, and it also writes
    # backslash entry names. Stream with System.IO.Compression instead.
    $ProgressPreference = "SilentlyContinue"
    Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        try {
            if (Test-Path $DestinationPath) {
                Remove-Item -LiteralPath $DestinationPath -Force
            }
            # Entry names use "/" as the zip format requires (CreateFromDirectory writes backslashes here).
            $Base = (Get-Item -LiteralPath $SourceDirectory).Parent.FullName.TrimEnd("\") + "\"
            $Archive = [System.IO.Compression.ZipFile]::Open($DestinationPath, [System.IO.Compression.ZipArchiveMode]::Create)
            try {
                foreach ($File in Get-ChildItem -LiteralPath $SourceDirectory -Recurse -File) {
                    $EntryName = $File.FullName.Substring($Base.Length).Replace("\", "/")
                    [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                        $Archive, $File.FullName, $EntryName, [System.IO.Compression.CompressionLevel]::Optimal)
                }
            }
            finally {
                $Archive.Dispose()
            }
            if ((Get-Item -LiteralPath $DestinationPath).Length -lt 1MB) {
                throw "Portable zip is unexpectedly small."
            }
            return $true
        }
        catch {
            Write-Warning "Portable zip attempt $Attempt failed: $($_.Exception.Message)"
            Start-Sleep -Seconds 2
        }
    }

    $Tar = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($Tar) {
        try {
            if (Test-Path $DestinationPath) {
                Remove-Item -LiteralPath $DestinationPath -Force
            }
            $Parent = Split-Path -Parent $SourceDirectory
            $Leaf = Split-Path -Leaf $SourceDirectory
            & $Tar.Source -a -c -f $DestinationPath -C $Parent $Leaf
            if ($LASTEXITCODE -eq 0 -and (Test-Path $DestinationPath)) {
                return $true
            }
            Write-Warning "tar.exe portable zip fallback failed. ExitCode=$LASTEXITCODE"
        }
        catch {
            Write-Warning "tar.exe portable zip fallback failed: $($_.Exception.Message)"
        }
    }
    return $false
}

Push-Location $Root
try {
    $Python = Resolve-Python
    $env:PYTHONUTF8 = "1"
    $Version = Get-LectavoVersion

    if (Test-Path $ReleaseDir) {
        Remove-Item -LiteralPath $ReleaseDir -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

    Write-Host "Running Python syntax check."
    Invoke-Checked $Python @("-m", "compileall", "-q", "movie_slide_notes", "app.py") "Python syntax check failed."

    Write-Host "Refreshing dependency and media runtime reports."
    Invoke-Checked $Python @(
        "-m", "piplicenses",
        "--format=plain",
        "--with-urls",
        "--output-file", (Join-Path $Root "licenses\DEPENDENCY_LICENSES.txt")
    ) "Dependency license report generation failed."
    Invoke-Checked $Python @("-m", "scripts.generate_media_build_info") "Media runtime report generation failed."

    Write-Host "Running public release audit, lint, and unit tests."
    & (Join-Path $Root "scripts\audit_public_release.ps1") -PythonPath $Python

    Write-Host "Running GUI smoke test."
    $env:QT_QPA_PLATFORM = "offscreen"
    Invoke-Checked $Python @("app.py", "--smoke") "GUI smoke test failed."

    & (Join-Path $Root "scripts\build_app.ps1") -PythonPath $Python

    Write-Host "Running built Lectavo.exe smoke test."
    $ExePath = Join-Path $Root "dist\Lectavo\Lectavo.exe"
    $Process = Start-Process -FilePath $ExePath -ArgumentList "--smoke" -Wait -PassThru -WindowStyle Hidden
    if ($Process.ExitCode -ne 0) {
        throw "Lectavo.exe smoke test failed. ExitCode=$($Process.ExitCode)"
    }

    & (Join-Path $Root "scripts\sign_release.ps1") -Stage App
    if ($IsccPath) {
        & (Join-Path $Root "scripts\build_installer.ps1") -IsccPath $IsccPath
    }
    else {
        & (Join-Path $Root "scripts\build_installer.ps1")
    }
    & (Join-Path $Root "scripts\sign_release.ps1") -Stage Installer

    Write-Host "Creating portable zip."
    $PortableZip = Join-Path $ReleaseDir "Lectavo-portable.zip"
    $PortableOk = New-PortableZip -SourceDirectory (Join-Path $Root "dist\Lectavo") -DestinationPath $PortableZip
    if (-not $PortableOk) {
        Write-Warning "Portable zip generation failed after retries. Continuing because Lectavo-Setup.exe is the primary artifact."
    }

    & (Join-Path $Root "scripts\build_msi.ps1")

    Set-Content -Path (Join-Path $ReleaseDir "VERSION.txt") -Value $Version -Encoding UTF8
    $ReleaseNotes = @"
Lectavo $Version

- 0.5.0: PDF translation (17 languages, layout kept, bilingual PDF, OCR, built-in local LLM, full-screen comparison).
- 0.4.5: Startup splash for faster-feeling launch; fixed clipped installer checkboxes.
- 0.4.4: Save next to the input by default; aligned settings layout; smooth disclosure sections.
- 0.4.3: Frame and one-second stepping in the crop dialog, with exact frame seeking.
- 0.4.2: Per-video crop timelines for layouts that change mid-video.
- 0.4.2: Option for slides without page numbers; maximizable crop dialog.
- 0.4.2: Combined output goes into one folder per run.
- 0.4.1: Lectavo artwork in the installer instead of the default box-and-disc images.
- 0.4.1: Sidebar app icon, highlighted active icons, file rows with type icons and colored status.
- 0.4.1: Thinner separators, aligned settings labels, and chevron disclosure icons.
- 0.4.0: Checkpoints completed slide analysis and transcripts; resume interrupted or failed jobs.
- 0.4.0: Pre-run check for inputs, audio tracks, free space, model readiness, and GPU.
- 0.4.0: Review slides to remove, reorder, and add missed scenes before rebuilding the PDF and notes.
- 0.4.0: Videos without an audio track skip transcription instead of failing.
- 0.3.1: Fixed 4K website layout, separated Home and Process, and added multi-select file tabs.
- 0.3.1: Distinguishes silence, no detected speech, and model loading errors; repairs unreadable model files.
- 0.3.1: Versioned x64 installer filenames, with a legacy alias for the 0.3.0 updater.
- 0.3.0: MP3 / M4A / WAV transcription with TXT, SRT, and Markdown output.
- 0.3.0: Select slide crop and page-number regions on a video preview.
- 0.3.0: Check updates in the background; ask before download and installation; verify SHA-256.
- 0.3.0: Faster startup through deferred processing libraries and GPU diagnostics.
- 0.3.0: Single-instance activation and SVG navigation icons.

- Bundles the official NVIDIA CUDA 12 cuBLAS runtime so supported NVIDIA GPUs can run faster-whisper.
- Diagnoses missing CUDA 12 cuBLAS and cuDNN runtime DLLs before transcription and explains CPU fallback.
- Adds Japanese and English application UI; Japanese remains the default.
- Adds individual and clear-all controls for recent processing history without deleting generated files.
- Enables desktop shortcut creation by default in the installer.
- Decodes media directly with PyAV; no standalone ffmpeg.exe is included or launched.
- Replaces PyMuPDF with BSD-licensed ReportLab for PDF generation.
- Adds an MIT license, dependency inventory, and public release checklist.
- Keeps cancellation confirmation and stops PyAV audio decoding cooperatively.
- Processes multiple videos in natural filename order across PDF, transcript, SRT, and lecture-note outputs.
- Added a combined output mode that creates one PDF, transcript, SRT, and lecture-notes file for all selected videos.
- Removes duplicate slides across different videos when combined output is selected.
- Keeps the existing per-video folder output as an option.
- Set the application publisher to Kou Hashizume.
- Removed the redundant icon from the sidebar header.
- Fixed missed slides caused by pHash collisions between different slides with similar layouts.
- Added automatic slide-surface detection so page-number regions follow inset or side-by-side slides.
- Changed the default minimum save interval from 5 seconds to 1 second to retain consecutive slides.
- Migrates an unchanged legacy Fast preset to the new Standard extraction defaults once.
- Refined checkbox and combo-box visuals with scalable vector icons.
- Combined transcription and lecture notes into one output option.
- Added CPU/GPU selection and a processing-settings summary to the main workflow.
- Changed the default transcription language to automatic natural detection.
- Reduced the minimum window size and improved compact layouts.
- PyInstaller onedir output is packaged with Inno Setup.
- faster-whisper models are not bundled; they are downloaded on first transcription use.
- This build is self-signed when LECTAVO_CERT_SHA1 points to a self-signed certificate, and unsigned when no certificate is configured.
"@
    Set-Content -Path (Join-Path $ReleaseDir "release-notes.txt") -Value $ReleaseNotes -Encoding UTF8

    $HashTargets = Get-ChildItem $ReleaseDir -File |
        Where-Object { $_.Name -like "Lectavo-setup-*-x64.exe" -or $_.Name -in @("Lectavo-Setup.exe", "Lectavo-x64.msi", "Lectavo-portable.zip", "VERSION.txt", "release-notes.txt") }
    $HashLines = foreach ($File in $HashTargets) {
        $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $File.FullName
        "$($Hash.Hash.ToLowerInvariant())  $($File.Name)"
    }
    Set-Content -Path (Join-Path $ReleaseDir "SHA256SUMS.txt") -Value $HashLines -Encoding ASCII

    if (-not $SkipInstallerSmoke) {
        $SetupPath = Join-Path $ReleaseDir "Lectavo-Setup.exe"
        $InstallDir = Join-Path $env:TEMP ("LectavoInstallSmoke-" + [guid]::NewGuid().ToString("N"))
        Write-Host "Running installer smoke test: $InstallDir"
        $InstallDirArgument = '/DIR="' + $InstallDir + '"'
        $LogArgument = '/LOG="' + $InstallDir + '-install.log"'
        $Installer = Start-Process -FilePath $SetupPath -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", $InstallDirArgument, $LogArgument) -Wait -PassThru
        if ($Installer.ExitCode -ne 0) {
            throw "Installer smoke install failed. ExitCode=$($Installer.ExitCode)"
        }
        $InstalledExe = Join-Path $InstallDir "Lectavo.exe"
        if (-not (Test-Path $InstalledExe)) {
            throw "Installed Lectavo.exe was not found: $InstalledExe"
        }
        $InstalledProcess = Start-Process -FilePath $InstalledExe -ArgumentList "--smoke" -Wait -PassThru -WindowStyle Hidden
        if ($InstalledProcess.ExitCode -ne 0) {
            throw "Installed Lectavo.exe smoke test failed. ExitCode=$($InstalledProcess.ExitCode)"
        }
        $Uninstaller = Get-ChildItem -LiteralPath $InstallDir -Filter "unins*.exe" | Select-Object -First 1
        if ($Uninstaller) {
            $UninstallProcess = Start-Process -FilePath $Uninstaller.FullName -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") -Wait -PassThru
            if ($UninstallProcess.ExitCode -ne 0) {
                throw "Uninstall smoke test failed. ExitCode=$($UninstallProcess.ExitCode)"
            }
        }
    }

    Write-Host "Release build completed: $ReleaseDir"
}
finally {
    Pop-Location
}
