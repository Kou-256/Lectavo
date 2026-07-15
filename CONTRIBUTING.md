# Contributing to Lectavo

Lectavo welcomes focused bug reports and improvements. Please keep uploaded
examples free of lecture recordings, slides, names, paths, or transcripts that
you do not have permission to share.

## Development setup

Lectavo is developed on Windows. The locally verified Python version is listed
in `.python-version`.

```powershell
py -3.14 -m venv movie_slide_notes\.venv
.\movie_slide_notes\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Run the checks before opening a pull request:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\movie_slide_notes\.venv\Scripts\python.exe -m ruff format --check movie_slide_notes tests app.py
.\movie_slide_notes\.venv\Scripts\python.exe -m ruff check movie_slide_notes tests app.py
.\movie_slide_notes\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\scripts\audit_public_release.ps1
```

Keep GUI code under `movie_slide_notes/gui/` and reusable processing logic under
`movie_slide_notes/core/` or the corresponding processing module. Do not commit
videos, generated outputs, model caches, credentials, signing certificates,
`build/`, `dist/`, or `release/`.
