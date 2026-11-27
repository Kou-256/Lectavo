"""PDF translation pipeline: analyze pages, translate blocks, write PDFs."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock

from movie_slide_notes.pdf_translate import edits, layout, ocr
from movie_slide_notes.pdf_translate.engines import Engine, TranslationError
from movie_slide_notes.pdf_translate.languages import already_in_target, language
from movie_slide_notes.pdf_translate.markup import strip_tags
from movie_slide_notes.pdf_translate.models import (
    PageInfo,
    PdfTranslationOptions,
    PdfTranslationResult,
    TextBlock,
)
from movie_slide_notes.pdf_translate.protect import protect, restore

BATCH_CHARS = 3500
BATCH_ITEMS = 40
PARALLEL_REQUESTS = 3
COLOR_SCALE = 1.0


class TranslationCancelled(Exception):
    pass


@dataclass
class TranslateProgress:
    stage: str  # "analyze", "ocr", "translate", "write", "done"
    fraction: float
    message: str


def output_paths(
    source: Path, output_dir: Path, options: PdfTranslationOptions
) -> tuple[Path | None, Path | None]:
    stem = source.stem
    target = options.target_lang
    if target == "ja":
        translated_name, bilingual_name = f"{stem}_日本語.pdf", f"{stem}_日英対訳.pdf"
    else:
        native = language(target).native
        translated_name, bilingual_name = f"{stem}_{native}.pdf", f"{stem}_対訳_{native}.pdf"
    japanese = output_dir / translated_name if options.make_japanese else None
    bilingual = output_dir / bilingual_name if options.make_bilingual else None
    return japanese, bilingual


class TranslationCache:
    """Remembers translations by engine, style and source text."""

    def __init__(self, path: Path | None):
        self._lock = Lock()
        self._db = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(path), check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS t (k TEXT PRIMARY KEY, v TEXT NOT NULL)")

    @staticmethod
    def key(engine_key: str, style: str, text: str) -> str:
        return hashlib.sha256(f"{engine_key}\0{style}\0{text}".encode()).hexdigest()

    def get(self, key: str) -> str | None:
        if self._db is None:
            return None
        with self._lock:
            row = self._db.execute("SELECT v FROM t WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None

    def put_many(self, items: list[tuple[str, str]]) -> None:
        if self._db is None or not items:
            return
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO t (k, v) VALUES (?, ?)", items)
            self._db.commit()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()


def make_batches(blocks: list[TextBlock], batch_chars: int = BATCH_CHARS) -> list[list[TextBlock]]:
    batches: list[list[TextBlock]] = []
    current: list[TextBlock] = []
    size = 0
    for block in blocks:
        if current and (size + len(block.text) > batch_chars or len(current) >= BATCH_ITEMS):
            batches.append(current)
            current, size = [], 0
        current.append(block)
        size += len(block.text)
    if current:
        batches.append(current)
    return batches


LONG_SOURCE = 400
_MARKER = re.compile(r"⟦\d+⟧")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z⟦(\[])")


def misaligned(sources: list[str], results: list[str]) -> list[int]:
    """Positions whose answer looks like it belongs to another item, or lost text.

    A long paragraph answered by a few characters, two different sources
    answered with the same text, or most of the protected terms missing means
    the engine mixed up the items or translated only part of the paragraph.
    """
    suspicious: set[int] = set()
    seen: dict[str, int] = {}
    for position, (source, result) in enumerate(zip(sources, results, strict=True)):
        length = len(result.strip())
        if len(source) >= 60 and length < len(source) * 0.12:
            suspicious.add(position)
        if len(source) >= LONG_SOURCE and length < len(source) * 0.2:
            suspicious.add(position)
        markers = set(_MARKER.findall(source))
        lost = markers - set(_MARKER.findall(result))
        if len(markers) >= 3 and len(lost) * 2 > len(markers):
            suspicious.add(position)
        key = result.strip()
        if key in seen and sources[seen[key]].strip() != source.strip():
            suspicious.update((seen[key], position))
        seen.setdefault(key, position)
    return sorted(suspicious)


def sentence_chunks(text: str, size: int = LONG_SOURCE) -> list[str]:
    """A paragraph cut at sentence ends into pieces of about ``size`` characters."""
    chunks: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        if chunks and len(chunks[-1]) + len(sentence) < size:
            chunks[-1] += " " + sentence
        else:
            chunks.append(sentence)
    return chunks


_SECTION_HEADING = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[IVX]+\.)\s")


def document_context(pages: list[PageInfo]) -> str:
    """The document title, given to the engine for consistent terminology.

    Only text set clearly larger than the body counts. A numbered section
    heading such as "2. Experimental methods" is not a title, and small models
    copy it into the translations.
    """
    for page in pages[:2]:
        sizes = sorted(block.font_size for block in page.blocks)
        if not sizes:
            continue
        body = sizes[len(sizes) // 2]
        for block in sorted(page.blocks, key=lambda b: -b.font_size):
            if block.font_size < body * 1.2:
                break
            text = strip_tags(block.text)
            if 3 <= len(text.split()) <= 25 and not _SECTION_HEADING.match(text):
                return text
    return ""


def translate_pdf(
    source: Path,
    output_dir: Path,
    options: PdfTranslationOptions,
    engine: Engine,
    engine_key: str,
    progress: Callable[[TranslateProgress], None] | None = None,
    log: Callable[[str], None] | None = None,
    cancel_event: Event | None = None,
    cache_path: Path | None = None,
    edit_store: bool = False,
) -> PdfTranslationResult:
    def report(stage: str, fraction: float, message: str) -> None:
        if progress is not None:
            progress(TranslateProgress(stage, max(0.0, min(1.0, fraction)), message))

    def say(message: str) -> None:
        if log is not None:
            log(message)

    def check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise TranslationCancelled()

    if not (options.make_japanese or options.make_bilingual):
        raise ValueError("出力するPDFを1つ以上選んでください。")

    report("analyze", 0.0, "PDFを読み込んでいます")
    normalized = layout.normalize_pdf(source)
    sizes = layout.page_sizes(normalized)
    indexes = layout.page_list(len(sizes), options.first_page, options.last_page)
    pages = layout.build_pages(sizes, indexes)
    say(f"{source.name}: {len(indexes)}ページを翻訳します")

    # 1. Text layer, then OCR where the page has no usable text.
    text_pages = (
        {} if options.ocr_mode == "always" else layout.extract_text_pages(normalized, indexes)
    )
    check_cancel()
    background_colors: dict[int, list[tuple[tuple, tuple]]] = {}
    ocr_pages = 0
    with layout.PageRenderer(normalized) as renderer:
        for position, page in enumerate(pages):
            check_cancel()
            blocks, chars, coverage = text_pages.get(page.index, ([], 0, 1.0))
            use_ocr = options.ocr_mode == "always" or (
                options.ocr_mode == "auto" and layout.is_scanned(chars, coverage)
            )
            if use_ocr:
                report(
                    "ocr",
                    0.3 * position / len(pages),
                    f"文字を読み取っています（{position + 1}/{len(pages)}ページ）",
                )
                scale = ocr.ocr_scale(page.width, page.height)
                image = renderer.render(page.index, scale)
                blocks = ocr.ocr_blocks(image, scale, page.index, page.width, page.height)
                ocr_pages += 1
                page.ocr = True
                color_image, color_scale = image, scale
            else:
                report(
                    "analyze",
                    0.3 * position / len(pages),
                    f"レイアウトを解析しています（{position + 1}/{len(pages)}ページ）",
                )
                color_scale = COLOR_SCALE
                color_image = renderer.render(page.index, color_scale) if blocks else None
            page.blocks = blocks
            background_colors[page.index] = [
                layout.sample_colors(color_image, color_scale, page.height, block)
                for block in blocks
            ]
            for block, (background, _text) in zip(
                blocks, background_colors[page.index], strict=True
            ):
                layout.extend_to_ink(color_image, color_scale, page.height, block, background)
    if ocr_pages:
        say(f"{ocr_pages}ページをOCRで読み取りました")

    # 2. Translate, reusing cached results.
    # Text that is already in the target language stays as it is.
    for page in pages:
        keep = [
            (block, colors)
            for block, colors in zip(page.blocks, background_colors[page.index], strict=True)
            if not already_in_target(strip_tags(block.text), options.target_lang)
        ]
        page.blocks = [block for block, _ in keep]
        background_colors[page.index] = [colors for _, colors in keep]
    all_blocks = [block for page in pages for block in page.blocks]
    if not all_blocks:
        raise TranslationError("翻訳する文章が見つかりませんでした。")
    cache = TranslationCache(cache_path)
    style_key = f"{options.style}|{options.source_lang}>{options.target_lang}"
    try:
        pending: list[TextBlock] = []
        cached = 0
        for block in all_blocks:
            hit = cache.get(cache.key(engine_key, style_key, block.text))
            # Answers saved before the misalignment check may belong to another item.
            if hit and not misaligned([block.text], [hit]):
                block.translated = hit
                cached += 1
            else:
                pending.append(block)
        if cached:
            say(f"{cached}件は以前の翻訳結果を再利用しました")
        if pending:
            report("translate", 0.3, "翻訳エンジンを準備しています")
            engine.prepare(cancel_event)
        batches = make_batches(pending, engine.batch_chars)
        context = document_context(pages)
        done = 0
        report("translate", 0.3, f"翻訳しています（0/{len(batches)}）")

        def run(batch: list[TextBlock]) -> list[str]:
            check_cancel()
            if not engine.protect_terms:
                texts, saved = [block.text for block in batch], [[] for _ in batch]
            else:
                texts, saved = map(
                    list, zip(*(protect(block.text) for block in batch), strict=True)
                )

            def ask(items: list[str]) -> list[str]:
                return engine.translate(
                    items,
                    options.style,
                    context,
                    source=options.source_lang,
                    target=options.target_lang,
                )

            results = ask(texts)
            # Small models sometimes shift answers by one item or drop part of a
            # long paragraph; ask again one by one, then sentence by sentence.
            for position in misaligned(texts, results):
                check_cancel()
                text = texts[position]
                answer = ask([text])[0]
                if misaligned([text], [answer]):
                    pieces = sentence_chunks(text)
                    if len(pieces) > 1:
                        check_cancel()
                        answer = " ".join(part.strip() for part in ask(pieces))
                results[position] = answer
            return [restore(text, tokens) for text, tokens in zip(results, saved, strict=True)]

        with ThreadPoolExecutor(
            max_workers=max(1, min(PARALLEL_REQUESTS, engine.parallel))
        ) as pool:
            futures = {pool.submit(run, batch): batch for batch in batches}
            try:
                for future in as_completed(futures):
                    batch = futures[future]
                    results = future.result()
                    for block, text in zip(batch, results, strict=True):
                        block.translated = text
                    cache.put_many(
                        [
                            (cache.key(engine_key, style_key, block.text), block.translated)
                            for block in batch
                        ]
                    )
                    done += 1
                    report(
                        "translate",
                        0.3 + 0.6 * done / max(1, len(batches)),
                        f"翻訳しています（{done}/{len(batches)}）",
                    )
                    check_cancel()
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
    finally:
        cache.close()

    # 3. Write.
    check_cancel()
    report("write", 0.92, "PDFを書き出しています")
    document = edits.from_translation(
        pages, background_colors, language(options.target_lang).script, source=str(source)
    )
    japanese, bilingual = output_paths(source, output_dir, options)
    document.japanese = str(japanese) if japanese else ""
    document.bilingual = str(bilingual) if bilingual else ""
    edits.write(document, normalized)
    if edit_store:
        # Kept so the translated PDF can be edited later; never fails the translation.
        try:
            edits.store(document, normalized)
        except Exception:
            logging.getLogger("lectavo").exception("Could not keep the edit data")
    report("done", 1.0, "完了しました")
    say("翻訳PDFを保存しました")
    return PdfTranslationResult(
        source=source,
        japanese_pdf=japanese,
        bilingual_pdf=bilingual,
        pages=len(pages),
        ocr_pages=ocr_pages,
        translated_blocks=len(all_blocks),
        cached_blocks=cached,
    )


def inspect_pdf(source: Path) -> dict:
    """Quick facts shown before translating: page count and whether pages look scanned."""
    normalized = layout.normalize_pdf(source)
    sizes = layout.page_sizes(normalized)
    sample = list(range(min(3, len(sizes))))
    pages = layout.extract_text_pages(normalized, sample) if sample else {}
    scanned = sum(
        1 for blocks, chars, coverage in pages.values() if layout.is_scanned(chars, coverage)
    )
    return {
        "pages": len(sizes),
        "scanned": bool(sample) and scanned == len(sample),
        "partly_scanned": 0 < scanned < len(sample),
    }
