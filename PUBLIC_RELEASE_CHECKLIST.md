# Lectavo public release checklist

This file records what was actually checked for version 0.2.1. It is not a
claim of legal compliance or a substitute for professional legal advice.

## Completed locally

- [x] No committed Git history exists yet, so no secret is present in Git history.
- [x] Source scan excludes personal absolute paths, API keys, and private lecture names.
- [x] Runtime and build dependencies are pinned.
- [x] Lectavo source code has an MIT `LICENSE`.
- [x] PyMuPDF was replaced by ReportLab to avoid an AGPL/commercial dependency.
- [x] The separately bundled GPL `ffmpeg.exe` was removed.
- [x] Audio decoding uses PyAV and its dynamically linked FFmpeg libraries.
- [x] Whisper model weights are downloaded to the user cache and are not shipped.
- [x] Third-party package inventory and key license texts are included.
- [x] Ruff formatting, linting, unit tests, GUI smoke, executable smoke, and installer
  smoke are part of the release workflow.
- [x] Test media names are generic and no third-party lecture footage is included.
- [x] The exact `pip-licenses` report was regenerated and reviewed on 2026-07-15;
  it contains no unknown or AGPL runtime dependency and no stale PyMuPDF entry.
- [x] A rights-safe synthetic MP4 completed faster-whisper `tiny` transcription on
  both CPU (`int8`) and an NVIDIA RTX 5060 Laptop GPU (`CUDA`, `float16`). TXT and
  SRT outputs were created on both devices without fallback.
- [x] The same synthetic MP4 completed the CLI workflow on CUDA and created a slide
  PDF, transcript, SRT, and Markdown lecture notes.
- [x] The PyInstaller `0.2.1` executable completed the same synthetic video from the
  GUI on CUDA and produced a one-page PDF, PNG, TXT, SRT, and Markdown file without
  opening a console window.
- [x] The packaged GUI visibly exposes per-entry deletion on Home and History plus a
  clear-all history action. Unit tests confirm persistence and the clear-all prompt.
- [x] Exact-name checks on 2026-07-15 found no `lectavo` project at PyPI and no
  GitHub account at `/Lectavo`. This is a point-in-time check, not a reservation.

## Must be checked before publishing each binary release

- [ ] Publish or otherwise provide the corresponding source and build information
  required by LGPL/GPL components, including Qt and the FFmpeg libraries in the
  PyAV and OpenCV wheels.
- [ ] Confirm that the selected PyAV wheel's FFmpeg build and enabled codecs are suitable
  for the planned distribution and jurisdictions.
- [ ] Test install, processing, and uninstall in a clean Windows 10/11 x64 VM without
  Python, FFmpeg, Git, or developer tools.
- [ ] Add only rights-safe screenshots or demo videos.
- [ ] Configure Authenticode signing and verify both `Lectavo.exe` and
  `Lectavo-Setup.exe`; do not describe an unsigned build as signed.
- [ ] Replace placeholder release/support URLs only after the public repository exists.

## Environment-limited checks on 2026-07-15

- The regular installer smoke test covers silent install, packaged app startup, and
  uninstall, but it is not a clean-PC test. This PC runs Windows 11 Home build 26200,
  where Windows Sandbox is unavailable; use a separate Windows 10/11 x64 VM before
  publishing.
- No Authenticode certificate is configured in the user or machine certificate
  stores, and `signtool.exe` is not installed. The release therefore remains unsigned.
- WiX is installed, but the MSI authoring file is not implemented. Setup.exe remains
  the supported distribution path.
- PyAV 18.0.0 reports FFmpeg 8.1.2, dynamic linking, and LGPL version 3 or later.
  Its exact build configuration is recorded in `licenses/PYAV-FFMPEG-BUILD-INFO.txt`;
  corresponding-source distribution and final legal review are still required.

## Git publication

The repository currently has no commits. Before the first commit, review `git status`,
run `scripts/audit_public_release.ps1`, and stage only source, documentation, packaging,
and rights-safe assets. Do not commit `.venv`, models, private videos, generated output,
certificate files, `build`, `dist`, or `release`.
