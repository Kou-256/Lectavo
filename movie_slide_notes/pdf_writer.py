from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas


def write_pdf(image_paths: Iterable[Path], output_pdf: Path, margin: float = 0.0) -> Path:
    images = [Path(path) for path in image_paths]
    if not images:
        raise ValueError("PDFにする画像がありません。")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    document = Canvas(str(output_pdf), pagesize=(1, 1), pageCompression=1)
    try:
        for image_path in images:
            with Image.open(image_path) as image:
                width, height = image.size

            page_width = float(width) + margin * 2
            page_height = float(height) + margin * 2
            document.setPageSize((page_width, page_height))
            document.drawImage(
                ImageReader(str(image_path)),
                margin,
                margin,
                width=float(width),
                height=float(height),
                preserveAspectRatio=True,
                mask="auto",
            )
            document.showPage()
    finally:
        document.save()

    return output_pdf
