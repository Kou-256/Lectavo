"""Generate real sample outputs and step-by-step app screenshots for the website.

Everything is rights-safe: slides are drawn here and the narration is produced
by the Windows speech synthesizer. The sample is processed by the real Lectavo
pipeline (slide detection, cross-video de-duplication, faster-whisper).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
# Render at 2x so screenshots stay sharp on high-DPI displays.
os.environ["QT_SCALE_FACTOR"] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from scripts.capture_website_screenshots import (  # noqa: E402
    TEMP_ROOT,
    capture,
    drawn_slide,
)

SLIDES = [
    (
        "研究の進め方",
        ("01  問いを立てる", "02  根拠を集める", "03  結果を比べる"),
        "#0f8f8a",
        "今日は研究の進め方を三つの段階に分けて説明します。",
    ),
    (
        "問いを立てる",
        ("範囲と前提を決める", "何が分かれば答えになるか"),
        "#2563eb",
        "まず問いを立てます。調べる範囲と前提をはっきりさせましょう。",
    ),
    (
        "根拠を集める",
        ("アンケートとインタビュー", "記録と測定"),
        "#7c3aed",
        "次に根拠を集めます。アンケートや記録など、複数の方法を組み合わせます。",
    ),
    (
        "結果を比べる",
        ("効果の大きさ", "信頼区間"),
        "#d97706",
        "集めた結果は、効果の大きさと信頼区間で比べます。",
    ),
    (
        "まとめ",
        ("問い → 根拠 → 結果", "来週はケーススタディ"),
        "#0f8f8a",
        "最後にまとめです。来週は実際のケーススタディを扱います。",
    ),
]
PARTS = [(0, 1, 2), (2, 3, 4)]  # slide 3 appears in both parts
SAMPLES = ROOT / "website" / "public" / "samples"
SHOTS = ROOT / "website" / "public" / "screenshots"


def speak(text: str, path: Path) -> None:
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$s.SelectVoice('Microsoft Haruka Desktop');"
        "$s.Rate = -1;"
        f"$s.SetOutputToWaveFile('{path}');"
        f"$s.Speak('{text}');"
        "$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)


def make_part(index: int, slide_ids: tuple[int, ...]) -> Path:
    import av
    import numpy as np

    clips = []
    for slide_id in slide_ids:
        wav = TEMP_ROOT / f"narration-{slide_id}.wav"
        if not wav.exists():
            speak(SLIDES[slide_id][3], wav)
        with wave.open(str(wav)) as handle:
            rate = handle.getframerate()
            pcm = np.frombuffer(handle.readframes(handle.getnframes()), np.int16)
        seconds = max(5, int(len(pcm) / rate) + 2)
        padded = np.zeros(seconds * rate, np.int16)
        padded[: len(pcm)] = pcm
        clips.append((slide_id, seconds, padded, rate))

    path = TEMP_ROOT / f"講義サンプル-{index:02d}.mp4"
    container = av.open(str(path), "w")
    video = container.add_stream("libx264", rate=5)
    video.width, video.height, video.pix_fmt = 1280, 720, "yuv420p"
    rate = clips[0][3]
    audio = container.add_stream("aac", rate=rate)
    audio.layout = "mono"
    for slide_id, seconds, _pcm, _rate in clips:
        title, lines, accent, _ = SLIDES[slide_id]
        rgb = drawn_slide(title, lines, accent)
        for _ in range(seconds * 5):
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            for packet in video.encode(frame):
                container.mux(packet)
    for packet in video.encode():
        container.mux(packet)
    samples = np.concatenate([clip[2] for clip in clips]).reshape(1, -1)
    frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
    frame.sample_rate = rate
    for packet in audio.encode(frame):
        container.mux(packet)
    for packet in audio.encode():
        container.mux(packet)
    container.close()
    return path


def widget_rect(widget, window) -> QRect:
    top_left = widget.mapTo(window, QPoint(0, 0))
    return QRect(top_left, widget.size())


def crop(window, rect: QRect, destination: Path) -> None:
    window.repaint()
    QApplication.processEvents()
    QTest.qWait(300)
    image = window.grab().toImage()
    ratio = image.devicePixelRatio()
    image = image.copy(
        QRect(
            round(rect.x() * ratio),
            round(rect.y() * ratio),
            round(rect.width() * ratio),
            round(rect.height() * ratio),
        )
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(destination), "PNG"):
        raise RuntimeError(f"Could not save {destination}")


def main() -> int:
    import cv2

    from movie_slide_notes.core.processor import (
        OperationOptions,
        ProcessingOptions,
        process_batch,
    )
    from movie_slide_notes.gui.i18n import set_ui_language
    from movie_slide_notes.gui.main_window import MainWindow
    from movie_slide_notes.transcriber import TranscriptionConfig, inspect_cuda_runtime

    app = QApplication.instance() or QApplication([])
    for font_name in ("YuGothR.ttc", "meiryo.ttc"):
        QFontDatabase.addApplicationFont(
            str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font_name)
        )
    app.setFont(QFont("Yu Gothic UI", 10))
    set_ui_language("ja")

    videos = [make_part(index, ids) for index, ids in enumerate(PARTS, start=1)]
    cuda = inspect_cuda_runtime().available
    options = ProcessingOptions(
        operations=OperationOptions(True, True, True, True, True),
        transcription_config=TranscriptionConfig(
            model_size="small",
            language="ja",
            device="cuda" if cuda else "cpu",
            compute_type="float16" if cuda else "int8",
        ),
        output_mode="combined",
    )
    output = TEMP_ROOT / "Lectavo_output"
    batch = process_batch(videos, output, options, log_callback=print)
    if batch.failed_count:
        raise RuntimeError("Sample processing failed")

    if SAMPLES.exists():
        shutil.rmtree(SAMPLES)
    SAMPLES.mkdir(parents=True)
    for position, slide in enumerate(batch.combined_slides, start=1):
        image = cv2.imdecode(
            __import__("numpy").fromfile(str(slide.path), dtype="uint8"), cv2.IMREAD_COLOR
        )
        small = cv2.resize(image, (640, 360), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".png", small)
        encoded.tofile(str(SAMPLES / f"slide-{position}.png"))
    shutil.copy2(batch.combined_transcript_path, SAMPLES / "transcript.txt")
    shutil.copy2(batch.combined_markdown_path, SAMPLES / "lecture_notes.md")
    print("slides", batch.combined_slide_count, "duplicates", batch.combined_duplicate_count)

    icon_path = ROOT / "assets" / "app_icon.png"
    display = [Path(r"C:\講義動画") / video.name for video in videos]

    # Step 1: Home
    home = MainWindow(icon_path=icon_path)
    home.history = []
    home._refresh_recent()
    home.resize(1280, 800)
    home.show()
    home._set_workflow_state("empty")
    QTest.qWait(500)
    capture(home, SHOTS / "howto-1-add.png")
    home.close()

    # Step 2: Ready page with two parts
    window = MainWindow(icon_path=icon_path)
    window.history = []
    window.videos = display
    window.video_statuses = {path: "待機中" for path in display}
    window._set_output_path(Path(r"C:\講義動画\Lectavo_output"))
    window._refresh_video_list()
    window._update_naming_preview()
    window.resize(1280, 860)
    window.show()
    window._set_workflow_state("ready")
    window.ready_page.verticalScrollBar().setValue(0)
    QTest.qWait(500)
    capture(window, SHOTS / "howto-2-ready.png")
    window.resize(1280, 1500)
    QTest.qWait(300)
    top = widget_rect(window.output_pdf, window).top() - 70
    bottom = widget_rect(window.naming_preview, window).bottom() + 16
    left = widget_rect(window.home_stack, window).left()
    crop(
        window, QRect(left, top, window.width() - left, bottom - top), SHOTS / "detail-outputs.png"
    )
    rail = widget_rect(window.file_panel, window)
    mode_bottom = widget_rect(window.output_mode_hint, window).bottom() + 16
    crop(window, QRect(rail.left(), 0, 760, mode_bottom), SHOTS / "detail-merge.png")
    window.resize(1280, 860)
    QTest.qWait(300)

    # Step 3: Running
    window._set_workflow_state("running")
    window.video_statuses = {display[0]: "完了", display[1]: "文字起こし中"}
    window._refresh_video_list()
    window.running_title.setText(f"{display[1].name} を処理しています")
    window.running_stage.setText("文字起こし中")
    window.overall_progress.setValue(78)
    window.running_detail.setText("文字起こし中: 00:12 / 00:21")
    window.elapsed_label.setText("経過時間 00:47")
    window._running_device_text = "GPU（CUDA）" if cuda else "CPU"
    window._refresh_running_settings()
    QTest.qWait(400)
    crop(window, QRect(0, 0, 1280, 450), SHOTS / "howto-3-running.png")

    # Step 4: Complete
    for path in display:
        window.video_statuses[path] = "完了"
    window._refresh_video_list()
    window.last_batch = batch
    window._populate_complete_summary(batch)
    window._set_workflow_state("complete")
    QTest.qWait(400)
    crop(window, QRect(0, 0, 1280, 390), SHOTS / "howto-4-complete.png")
    window.close()
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
