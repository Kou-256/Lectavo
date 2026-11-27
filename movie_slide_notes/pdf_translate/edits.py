"""Editable layout of a translated PDF.

Every translated PDF is written from a list of items per page drawn over the
original page: text boxes (the translations, with the patch that hides the
original text), images (inserted, or figures cut out of the page) and erased
areas. The list and a copy of the original pages are kept in the app's cache,
so the translated PDF can be edited and written again later.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from threading import Lock

from movie_slide_notes.pdf_translate.models import PageInfo, TextBlock

EDIT_VERSION = 1
KEEP_DOCUMENTS = 40
DOCUMENT_FILE = "document.json"
ORIGINAL_FILE = "original.pdf"
IMAGES_DIR = "images"

_index_lock = Lock()


@dataclass
class EditItem:
    """One thing drawn over a page, in PDF points (origin bottom-left)."""

    kind: str  # "text", "image" or "erase"
    x0: float
    y0: float
    x1: float
    y1: float
    text: str = ""
    font_size: float = 10.0
    auto_size: bool = True  # shrink the text to fit the box
    bold: bool = False
    serif: bool = False
    centered: bool = False
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # Painted under the item; for translations it hides the original text.
    background: tuple[float, float, float] | None = None
    image: str = ""  # file name in the document's image folder

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def normalized(self) -> EditItem:
        x0, x1 = sorted((self.x0, self.x1))
        y0, y1 = sorted((self.y0, self.y1))
        self.x0, self.x1, self.y0, self.y1 = x0, x1, y0, y1
        return self

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return (
            self.x0 - margin <= x <= self.x1 + margin and self.y0 - margin <= y <= self.y1 + margin
        )

    def covers(self) -> bool:
        """True when the item hides what is under it."""
        return self.kind in ("image", "erase") or self.background is not None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> EditItem:
        names = {f.name for f in fields(cls)}
        values = {key: value for key, value in data.items() if key in names}
        for key in ("color", "background"):
            if isinstance(values.get(key), list):
                values[key] = tuple(values[key])
        return cls(**values)


@dataclass
class EditPage:
    index: int  # page index in the original PDF
    width: float
    height: float
    items: list[EditItem] = field(default_factory=list)


@dataclass
class EditDocument:
    folder: Path
    source: str
    script: str = "ja"
    japanese: str = ""
    bilingual: str = ""
    pages: list[EditPage] = field(default_factory=list)

    @property
    def original_path(self) -> Path:
        return self.folder / ORIGINAL_FILE

    @property
    def images_dir(self) -> Path:
        return self.folder / IMAGES_DIR

    def outputs(self) -> list[Path]:
        return [Path(p) for p in (self.japanese, self.bilingual) if p]

    def save(self) -> None:
        data = {
            "version": EDIT_VERSION,
            "source": self.source,
            "script": self.script,
            "japanese": self.japanese,
            "bilingual": self.bilingual,
            "pages": [
                {
                    "index": page.index,
                    "width": page.width,
                    "height": page.height,
                    "items": [item.to_dict() for item in page.items],
                }
                for page in self.pages
            ],
        }
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.folder / (DOCUMENT_FILE + ".part")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.folder / DOCUMENT_FILE)

    @classmethod
    def load(cls, folder: Path) -> EditDocument:
        data = json.loads((folder / DOCUMENT_FILE).read_text(encoding="utf-8"))
        pages = [
            EditPage(
                index=int(page["index"]),
                width=float(page["width"]),
                height=float(page["height"]),
                items=[EditItem.from_dict(item) for item in page["items"]],
            )
            for page in data["pages"]
        ]
        return cls(
            folder=folder,
            source=data.get("source", ""),
            script=data.get("script", "ja"),
            japanese=data.get("japanese", ""),
            bilingual=data.get("bilingual", ""),
            pages=pages,
        )

    def add_image(self, image) -> str:
        """Store a PIL image in the document and return its name."""
        self.images_dir.mkdir(parents=True, exist_ok=True)
        name = f"{int(time.time() * 1000)}-{len(list(self.images_dir.iterdir()))}.png"
        image.save(self.images_dir / name)
        return name

    def import_image(self, path: Path) -> str:
        from PIL import Image

        with Image.open(path) as image:
            image.load()
            return self.add_image(image.convert("RGBA") if image.mode in ("P", "LA") else image)


# --- building from a translation --------------------------------------------


def from_translation(
    pages: list[PageInfo],
    colors: dict[int, list[tuple[tuple, tuple]]],
    script: str,
    folder: Path | None = None,
    source: str = "",
) -> EditDocument:
    """Items for the translated blocks: the patch hiding the original, then the text."""
    document = EditDocument(folder=folder or Path(), source=source, script=script)
    for page in pages:
        items = []
        page_colors = colors.get(page.index, [])
        for block, (background, text_color) in zip(page.blocks, page_colors, strict=False):
            if not block.translated.strip():
                continue
            items.append(
                EditItem(
                    "text",
                    block.x0,
                    block.y0,
                    block.x1,
                    block.y1,
                    text=block.translated,
                    font_size=block.font_size,
                    bold=block.bold,
                    serif=block.serif,
                    centered=block.centered,
                    color=tuple(text_color),
                    background=tuple(background),
                )
            )
        document.pages.append(EditPage(page.index, page.width, page.height, items))
    return document


# --- drawing ------------------------------------------------------------------

COVER_PAD = 1.0


def _item_block(item: EditItem, page_index: int) -> TextBlock:
    return TextBlock(
        page_index,
        item.x0,
        item.y0,
        item.x1,
        item.y1,
        item.text,
        item.font_size,
        bold=item.bold,
        serif=item.serif,
        color=item.color,
        centered=item.centered,
    )


def draw_page(canvas, document: EditDocument, page: EditPage) -> None:
    """Patches first, then images, then text, so text stays readable on top."""
    from reportlab.lib.colors import Color

    from movie_slide_notes.pdf_translate import writer

    canvas.setPageSize((page.width, page.height))
    for item in page.items:
        background = item.background
        if item.kind == "erase" and background is None:
            background = (1.0, 1.0, 1.0)
        if background is None or item.kind == "image":
            continue
        pad = COVER_PAD if item.kind == "text" else 0.0
        canvas.setFillColor(Color(*background))
        canvas.setStrokeColor(Color(*background))
        canvas.rect(item.x0 - pad, item.y0 - pad, item.width + 2 * pad, item.height + 2 * pad, 0, 1)
    for item in page.items:
        if item.kind != "image" or not item.image:
            continue
        path = document.images_dir / item.image
        if not path.exists():
            continue
        canvas.drawImage(str(path), item.x0, item.y0, item.width, item.height, mask="auto")
    for item in page.items:
        if item.kind == "text" and item.text.strip():
            writer.draw_block_text(
                canvas,
                _item_block(item, page.index),
                item.text,
                item.color,
                document.script,
                fixed_size=None if item.auto_size else item.font_size,
            )


def build_overlay(document: EditDocument, positions: list[int] | None = None) -> bytes:
    """One overlay page per document page (or per position in ``positions``)."""
    from reportlab.pdfgen.canvas import Canvas

    buffer = io.BytesIO()
    canvas = Canvas(buffer, pageCompression=1)
    chosen = range(len(document.pages)) if positions is None else positions
    for position in chosen:
        draw_page(canvas, document, document.pages[position])
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def write(document: EditDocument, original: bytes | None = None) -> None:
    """Write the translated (and bilingual) PDF from the document."""
    from movie_slide_notes.pdf_translate import writer

    if original is None:
        original = document.original_path.read_bytes()
    japanese = Path(document.japanese) if document.japanese else None
    bilingual = Path(document.bilingual) if document.bilingual else None
    writer.write_outputs(
        original,
        build_overlay(document),
        [page.index for page in document.pages],
        japanese,
        bilingual,
    )


class PageComposer:
    """Renders single edited pages quickly, for the editor's preview."""

    def __init__(self, document: EditDocument):
        from pypdf import PdfReader

        self.document = document
        self.original = document.original_path.read_bytes()
        self.reader = PdfReader(io.BytesIO(self.original))

    def compose(self, position: int) -> bytes:
        from pypdf import PdfReader, PdfWriter

        overlay = PdfReader(io.BytesIO(build_overlay(self.document, [position])))
        output = PdfWriter()
        page = output.add_page(self.reader.pages[self.document.pages[position].index])
        page.merge_page(overlay.pages[0])
        buffer = io.BytesIO()
        output.write(buffer)
        return buffer.getvalue()

    def render(self, position: int, scale: float, with_items: bool = True):
        """The page as a PIL image; without items it is the untouched original."""
        import pypdfium2 as pdfium

        from movie_slide_notes.pdf_translate.layout import PDFIUM_LOCK

        with PDFIUM_LOCK:
            if with_items:
                document = pdfium.PdfDocument(self.compose(position))
                index = 0
            else:
                document = pdfium.PdfDocument(self.original)
                index = self.document.pages[position].index
            try:
                page = document[index]
                image = page.render(scale=scale).to_pil().convert("RGB")
                page.close()
            finally:
                document.close()
        return image


# --- storage --------------------------------------------------------------------


def _root() -> Path:
    from movie_slide_notes.paths import user_cache_dir

    return user_cache_dir() / "edits"


def _key(path: Path) -> str:
    return hashlib.sha1(str(Path(path).resolve()).lower().encode("utf-8")).hexdigest()[:20]


def _index_path(root: Path) -> Path:
    return root / "index.json"


def _read_index(root: Path) -> dict[str, str]:
    try:
        return json.loads(_index_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def store(document: EditDocument, original: bytes, root: Path | None = None) -> EditDocument:
    """Keep the document and the original pages so the output can be edited later."""
    root = root or _root()
    outputs = document.outputs()
    if not outputs:
        return document
    folder = root / _key(outputs[0])
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    document.folder = folder
    (folder / ORIGINAL_FILE).write_bytes(original)
    document.save()
    with _index_lock:
        index = _read_index(root)
        for output in outputs:
            index[_key(output)] = folder.name
        _prune(root, index)
        temporary = _index_path(root).with_suffix(".part")
        temporary.write_text(json.dumps(index), encoding="utf-8")
        temporary.replace(_index_path(root))
    return document


def _prune(root: Path, index: dict[str, str]) -> None:
    folders = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old in folders[KEEP_DOCUMENTS:]:
        shutil.rmtree(old, ignore_errors=True)
        for key in [key for key, name in index.items() if name == old.name]:
            del index[key]


def find(output: Path, root: Path | None = None) -> EditDocument | None:
    """The editable document of a translated or bilingual PDF, if it was kept."""
    root = root or _root()
    name = _read_index(root).get(_key(output))
    if not name:
        return None
    folder = root / name
    try:
        document = EditDocument.load(folder)
    except (OSError, ValueError, KeyError):
        logging.getLogger("lectavo").warning("Edit data for %s could not be read", output)
        return None
    if not document.original_path.exists():
        return None
    return document
