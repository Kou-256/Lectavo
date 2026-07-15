# -*- mode: python ; coding: utf-8 -*-

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata


spec_dir = Path(SPECPATH)
root = spec_dir.parent
version_text = (root / "movie_slide_notes" / "version.py").read_text(encoding="utf-8")
version = re.search(r'__version__\s*=\s*"([^"]+)"', version_text).group(1)
publisher = re.search(r'APP_PUBLISHER\s*=\s*"([^"]+)"', version_text).group(1)
version_tuple = tuple(int(part) for part in version.split(".")) + (0,)
version_tuple = version_tuple[:4]

generated_dir = root / "build" / "generated"
generated_dir.mkdir(parents=True, exist_ok=True)
version_info = generated_dir / "version_info.txt"
version_info.write_text(
    f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', '{publisher}'),
          StringStruct('FileDescription', 'Lectavo'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'Lectavo'),
          StringStruct('OriginalFilename', 'Lectavo.exe'),
          StringStruct('ProductName', 'Lectavo'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
    encoding="utf-8",
)

datas = []
for folder in ["assets", "licenses"]:
    if (root / folder).exists():
        datas.append((str(root / folder), folder))
for file_name in ["THIRD_PARTY_NOTICES.txt"]:
    if (root / file_name).exists():
        datas.append((str(root / file_name), "."))
datas += collect_data_files("faster_whisper", includes=["assets/*"])
datas += copy_metadata("nvidia-cublas-cu12")
datas += copy_metadata("nvidia-cuda-nvrtc-cu12")

binaries = []
for package in ["ctranslate2", "nvidia.cublas", "nvidia.cuda_nvrtc"]:
    binaries += collect_dynamic_libs(package)


a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "cv2",
        "reportlab.pdfgen.canvas",
        "imagehash",
        "skimage.metrics",
        "faster_whisper",
        "ctranslate2",
        "nvidia.cublas",
        "nvidia.cuda_nvrtc",
        "av",
        "huggingface_hub",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lectavo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "assets" / "app_icon.ico"),
    version=str(version_info),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Lectavo",
)
