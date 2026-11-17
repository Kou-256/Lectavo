"""Generate Lectavo-branded Inno Setup wizard images.

Inno Setup shows a default box-and-disc picture unless WizardImageFile is set.
Several sizes are written so the installer can pick a sharp image at high
display scaling. The small header image is hidden in packaging/lectavo.iss.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "assets" / "app_icon.png"
OUT = ROOT / "packaging" / "wizard"

LARGE_SIZES = ((164, 314), (246, 471), (328, 628), (410, 785))
TOP = (15, 143, 138)
BOTTOM = (9, 17, 19)


def font(size: int) -> ImageFont.ImageFont:
    for name in ("seguisb.ttf", "segoeui.ttf", "arial.ttf"):
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def large_image(width: int, height: int) -> Image.Image:
    canvas = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(canvas)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    icon_size = round(width * 0.52)
    icon = Image.open(ICON).convert("RGBA").resize((icon_size, icon_size), Image.LANCZOS)
    top = round(height * 0.22)
    canvas.paste(icon, ((width - icon_size) // 2, top), icon)
    title = font(round(width * 0.16))
    text = "Lectavo"
    box = draw.textbbox((0, 0), text, font=title)
    draw.text(
        ((width - (box[2] - box[0])) / 2, top + icon_size + round(height * 0.05)),
        text,
        font=title,
        fill=(255, 255, 255),
    )
    return canvas


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for width, height in LARGE_SIZES:
        large_image(width, height).save(OUT / f"large-{width}.bmp")
    print(f"Wrote wizard images to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
