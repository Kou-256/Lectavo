from __future__ import annotations

from pathlib import Path

import av
import cv2
from av import _core

from movie_slide_notes.version import __version__

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "licenses" / "PYAV-FFMPEG-BUILD-INFO.txt"


def main() -> None:
    metadata = _core.library_meta
    first = next(iter(metadata.values()))
    lines = [
        f"Lectavo {__version__} uses PyAV {av.__version__} binary wheels on Windows x64.",
        "",
        "The installed wheel reports dynamically linked FFmpeg libraries:",
        "",
    ]
    for name, details in metadata.items():
        version = ".".join(str(part) for part in details["version"])
        lines.append(f"- {name} {version}: {details['license']}")
    lines.extend(
        [
            "",
            "Reported configure options:",
            "",
            str(first["configuration"]),
            "",
            "OpenCV video I/O build lines:",
            "",
        ]
    )
    build_lines = [
        line.strip()
        for line in cv2.getBuildInformation().splitlines()
        if any(
            marker in line for marker in ("FFMPEG:", "avcodec:", "avformat:", "avutil:", "swscale:")
        )
    ]
    lines.extend(f"- {line}" for line in build_lines)
    lines.extend(
        [
            "",
            "Upstream source references:",
            "",
            "- PyAV: https://github.com/PyAV-Org/PyAV",
            "- PyAV FFmpeg builds: https://github.com/PyAV-Org/pyav-ffmpeg",
            f"- FFmpeg: https://ffmpeg.org/releases/ffmpeg-{av.ffmpeg_version_info}.tar.xz",
            "",
            "Before public binary distribution, provide the exact corresponding source and",
            "other materials required by the applicable licenses. See",
            "PUBLIC_RELEASE_CHECKLIST.md.",
        ]
    )
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
