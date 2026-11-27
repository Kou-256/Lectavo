from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from movie_slide_notes.pdf_translate import local_llm
from movie_slide_notes.pdf_translate.engines import (
    BuiltinEngine,
    Engine,
    EngineConfig,
    TranslationError,
    supports_language,
    system_prompt,
)
from movie_slide_notes.pdf_translate.languages import already_in_target
from movie_slide_notes.pdf_translate.models import PdfTranslationOptions, style_label
from movie_slide_notes.pdf_translate.pipeline import output_paths, translate_pdf
from movie_slide_notes.pdf_translate.writer import font_name, wrap_text


def make_pdf(path: Path, lines: list[tuple[str, str]], pages: int = 1) -> None:
    """``lines`` is a list of (font, text); "ja" uses a Japanese font."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    japanese = font_name("LectavoGothic")
    canvas = Canvas(str(path), pagesize=A4)
    for _ in range(pages):
        y = A4[1] - 80
        for kind, text in lines:
            canvas.setFont(japanese if kind == "ja" else "Helvetica", 11)
            canvas.drawString(60, y, text)
            y -= 40
        canvas.showPage()
    canvas.save()


class RecordingEngine(Engine):
    def __init__(self, reply: str):
        super().__init__(EngineConfig("local"))
        self.reply = reply
        self.sent: list[str] = []
        self.targets: list[str] = []
        self.prepared = 0

    def prepare(self, cancel_event=None) -> None:
        self.prepared += 1

    def translate(self, texts, style, context="", source="auto", target="ja"):
        self.sent.extend(texts)
        self.targets.append(target)
        return [self.reply for _ in texts]


class LanguageTests(unittest.TestCase):
    def test_text_already_in_the_target_language_is_recognized(self) -> None:
        self.assertTrue(already_in_target("希土類添加蛍光体の発光特性を調べた。", "ja"))
        self.assertFalse(already_in_target("Luminescence properties", "ja"))
        self.assertTrue(already_in_target("我们测量了发光特性。", "zh-Hans"))
        self.assertFalse(already_in_target("発光特性を調べた。", "zh-Hans"))  # kana: Japanese
        self.assertTrue(already_in_target("발광 특성을 측정했다.", "ko"))
        self.assertFalse(already_in_target("Bonjour tout le monde", "en"))

    def test_prompt_and_style_follow_the_target_language(self) -> None:
        self.assertIn("into Japanese", system_prompt("natural", "auto", "ja"))
        self.assertIn("やさしい日本語", system_prompt("plain", "en", "ja"))
        english = system_prompt("plain", "ja", "en")
        self.assertIn("plain, easy English", english)
        self.assertIn("The source language is Japanese", english)
        self.assertEqual(style_label("plain", "ja"), "やさしい日本語")
        self.assertEqual(style_label("plain", "fr"), "やさしい表現")

    def test_deepl_language_support(self) -> None:
        self.assertTrue(supports_language("deepl", "auto", "ja"))
        self.assertFalse(supports_language("deepl", "auto", "vi"))
        self.assertTrue(supports_language("builtin", "vi", "ja"))

    def test_space_separated_languages_wrap_by_word(self) -> None:
        font = font_name("LectavoSans")
        text = "Les propriétés de luminescence ont été mesurées à température ambiante."
        lines = wrap_text(text, font, 10, 120)
        words = set(text.split())
        for line in lines:
            for word in line.split():
                self.assertIn(word, words)

    def test_ions_and_formulas_are_protected_from_translation(self) -> None:
        from movie_slide_notes.pdf_translate.protect import protect, restore

        text = "Eu3+ doped TiO2 and Y3Al5O12:Ce3+ in H2O, Fig. 1 and I = I0"
        masked, tokens = protect(text)
        self.assertEqual(tokens[:4], ["Eu3+", "TiO2", "Y3Al5O12", "Ce3+"])
        self.assertNotIn("Eu3+", masked)
        self.assertNotIn("Fig", "".join(tokens))
        self.assertEqual(restore(masked, tokens), text)
        # A marker the translator dropped is kept rather than lost.
        self.assertIn("TiO2", restore("⟦0⟧を添加した", ["Eu3+", "TiO2"]))

    def test_missing_glyphs_use_a_fallback_font(self) -> None:
        from movie_slide_notes.pdf_translate.writer import font_runs

        mincho = font_name("LectavoMincho")
        runs = font_runs("Eu³⁺ドープ", mincho)
        self.assertEqual("".join(text for _, text in runs), "Eu³⁺ドープ")
        plus = next(run_font for run_font, text in runs if "⁺" in text)
        self.assertNotEqual(plus, mincho)

    def test_output_names_use_the_target_language(self) -> None:
        source = Path("paper.pdf")
        ja = output_paths(source, Path("out"), PdfTranslationOptions())
        self.assertEqual([p.name for p in ja], ["paper_日本語.pdf", "paper_日英対訳.pdf"])
        en = output_paths(source, Path("out"), PdfTranslationOptions(target_lang="en"))
        self.assertEqual([p.name for p in en], ["paper_English.pdf", "paper_対訳_English.pdf"])


class MultilingualPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_japanese_pdf_to_english(self) -> None:
        from pypdf import PdfReader

        source = self.root / "jp.pdf"
        make_pdf(
            source,
            [
                ("ja", "希土類添加蛍光体の発光特性を調べた。"),
                ("ja", "試料は固相反応法で作製した。"),
            ],
        )
        engine = RecordingEngine("Luminescence was measured.")
        options = PdfTranslationOptions(source_lang="ja", target_lang="en")
        result = translate_pdf(source, self.root / "out", options, engine, "fake")
        self.assertEqual(result.japanese_pdf.name, "jp_English.pdf")
        self.assertEqual(set(engine.targets), {"en"})
        self.assertEqual(engine.prepared, 1)
        self.assertIn(
            "Luminescence was measured.",
            PdfReader(str(result.japanese_pdf)).pages[0].extract_text(),
        )

    def test_text_already_in_the_target_language_is_not_sent(self) -> None:
        source = self.root / "mixed.pdf"
        make_pdf(
            source,
            [
                ("ja", "これは既に日本語で書かれた段落です。"),
                ("en", "This paragraph is written in English."),
            ],
        )
        engine = RecordingEngine("この段落は英語で書かれています。")
        translate_pdf(source, self.root / "out", PdfTranslationOptions(), engine, "fake")
        self.assertEqual(engine.sent, ["This paragraph is written in English."])

    def test_chinese_output_uses_a_chinese_font(self) -> None:
        from pypdf import PdfReader

        source = self.root / "en.pdf"
        make_pdf(source, [("en", "Luminescence properties were measured carefully.")])
        engine = RecordingEngine("我们仔细测量了发光特性。")
        options = PdfTranslationOptions(target_lang="zh-Hans", make_bilingual=False)
        result = translate_pdf(source, self.root / "out", options, engine, "fake")
        fonts = PdfReader(str(result.japanese_pdf)).pages[0]["/Resources"]["/Font"]
        names = " ".join(str(font.get_object().get("/BaseFont")) for font in fonts.values())
        self.assertRegex(names, "YaHei|SimSun|LectavoHans")


@dataclass(frozen=True)
class _TestModel(local_llm.LocalModel):
    test_url: str = ""

    @property
    def url(self) -> str:
        return self.test_url


class _RangeHandler(BaseHTTPRequestHandler):
    payload = b""

    def do_GET(self):  # noqa: N802 - http.server API
        data = self.payload
        start = 0
        if self.headers.get("Range"):
            start = int(self.headers["Range"].split("=")[1].split("-")[0])
            self.send_response(206)
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class LocalModelDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.payload = os.urandom(3 * 1024 * 1024 + 123)
        _RangeHandler.payload = self.payload
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/model.gguf"
        patcher = patch.object(local_llm, "models_dir", return_value=self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.temp.cleanup()

    def model(self, sha: str | None = None) -> _TestModel:
        return _TestModel(
            key="test",
            name="Test",
            tier="",
            description="",
            repo="",
            revision="",
            filename="test.gguf",
            sha256=sha or hashlib.sha256(self.payload).hexdigest(),
            size_bytes=len(self.payload),
            min_ram_gb=1,
            test_url=self.url,
        )

    def test_download_resumes_and_verifies(self) -> None:
        model = self.model()
        partial = local_llm.model_path(model).with_suffix(".part")
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(self.payload[:1_000_000])
        seen = []
        path = local_llm.download_model(model, lambda done, total: seen.append((done, total)))
        self.assertEqual(path.read_bytes(), self.payload)
        self.assertTrue(local_llm.is_installed(model))
        self.assertEqual(seen[-1][0], len(self.payload))
        local_llm.delete_model(model)
        self.assertFalse(local_llm.is_installed(model))

    def test_corrupt_download_is_rejected(self) -> None:
        model = self.model(sha="0" * 64)
        with self.assertRaises(local_llm.LocalLLMError):
            local_llm.download_model(model)
        self.assertFalse(local_llm.is_installed(model))
        self.assertFalse(local_llm.model_path(model).with_suffix(".part").exists())

    def test_download_can_be_cancelled(self) -> None:
        cancel = Event()
        cancel.set()
        with self.assertRaises(local_llm.DownloadCancelled):
            local_llm.download_model(self.model(), cancel_event=cancel)

    def test_server_needs_a_downloaded_model(self) -> None:
        with self.assertRaises(local_llm.LocalLLMError):
            local_llm.LocalServer().ensure(self.model(), use_gpu=False)


class _ChatHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self):  # noqa: N802 - http.server API
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _ChatHandler.requests.append(body)
        items = json.loads(body["messages"][1]["content"].split("\n", 1)[1])["items"]
        content = json.dumps(
            {"translations": [{"id": item["id"], "text": "訳文"} for item in items]},
            ensure_ascii=False,
        )
        reply = json.dumps(
            {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


class BuiltinEngineTests(unittest.TestCase):
    def test_requests_are_constrained_to_the_translation_schema(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ChatHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}/v1"
        try:
            with patch.object(local_llm.LocalServer, "ensure", return_value=base) as ensure:
                engine = BuiltinEngine(EngineConfig("builtin", "qwen3-4b-2507", use_gpu=False))
                engine.prepare()
                result = engine.translate(["Hello", "World"], "natural", target="ja")
            ensure.assert_called_once()
            self.assertEqual(result, ["訳文", "訳文"])
            request = _ChatHandler.requests[-1]
            self.assertEqual(request["response_format"]["type"], "json_schema")
            self.assertFalse(request["chat_template_kwargs"]["enable_thinking"])
            self.assertEqual(engine.parallel, 1)
        finally:
            server.shutdown()

    def test_missing_model_is_reported_as_a_translation_error(self) -> None:
        engine = BuiltinEngine(EngineConfig("builtin", "gemma-4-12b"))
        with patch.object(
            local_llm.LocalServer, "ensure", side_effect=local_llm.LocalLLMError("missing")
        ):
            with self.assertRaises(TranslationError):
                engine.prepare()


class ViewerAndPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_compare_viewer_pages_and_zoom(self) -> None:
        from movie_slide_notes.gui.compare_viewer import CompareViewer, PdfSide

        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "a.pdf"
            second = Path(folder) / "b.pdf"
            make_pdf(first, [("en", "Page text")], pages=3)
            make_pdf(second, [("en", "Page text")], pages=3)
            viewer = CompareViewer(PdfSide(first), PdfSide(second, 0, "right"), 3)
            viewer.resize(1200, 800)
            viewer.show()
            self.app.processEvents()
            viewer._render_visible()
            self.assertIsNotNone(viewer.panes[0].pages[0].pixmap())
            left = viewer.panes[0].pages[0].pixmap().width()
            right = viewer.panes[1].pages[0].pixmap().width()
            self.assertAlmostEqual(left / 2, right, delta=2)  # right half of the same size
            viewer.next_page()
            self.assertEqual(viewer.index, 1)
            viewer.go_to(10)
            self.assertEqual(viewer.index, 2)
            before = viewer._effective_zoom()
            viewer.zoom_in()
            self.assertGreater(viewer._effective_zoom(), before)
            viewer.set_fit("page")
            self.assertEqual(viewer.fit_mode, "page")
            viewer.close()

    def test_language_choice_updates_labels(self) -> None:
        from PySide6.QtCore import QSettings

        from movie_slide_notes.gui.pdf_translate_page import EngineSettings, PdfTranslatePage

        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "s.ini"), QSettings.IniFormat)
            engine_settings = EngineSettings(settings)
            self.assertEqual(engine_settings.provider, "builtin")
            page = PdfTranslatePage(settings, engine_settings)
            self.assertEqual(page.style_buttons["plain"].text(), "やさしい日本語")
            page.target_combo.setCurrentIndex(page.target_combo.findData("fr"))
            self.assertEqual(page.style_buttons["plain"].text(), "やさしい表現")
            self.assertIn("フランス語", page.japanese_check.text())
            page.source_combo.setCurrentIndex(page.source_combo.findData("en"))
            page._swap_languages()
            self.assertEqual((page.source_lang(), page.target_lang()), ("fr", "en"))
            del settings


if __name__ == "__main__":
    unittest.main()
