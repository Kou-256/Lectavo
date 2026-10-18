from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from movie_slide_notes.core.output_naming import (
    DEFAULT_DATE_STYLE,
    DEFAULT_NAMING_MODE,
    OutputNamingConfig,
)
from movie_slide_notes.core.processor import (
    OperationOptions,
    ProcessingConfigurationError,
    ProcessingOptions,
    ProgressEvent,
    describe_exception,
    discover_videos,
    process_batch,
)
from movie_slide_notes.duplicate_filter import DuplicateConfig
from movie_slide_notes.slide_detector import VIDEO_EXTENSIONS, SlideDetectionConfig
from movie_slide_notes.transcriber import TranscriptionConfig


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = args.input.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()
    videos = discover_videos(input_path, recursive=args.recursive)
    if not videos:
        supported = ", ".join(sorted(VIDEO_EXTENSIONS))
        print(f"処理対象の動画が見つかりませんでした。対応形式: {supported}")
        return 1

    options = build_processing_options(args)
    print(f"Lectavo: {len(videos)} 件の動画を処理します。")

    try:
        result = process_batch(
            videos,
            output_dir,
            options,
            progress_callback=None if args.no_progress else cli_progress,
            log_callback=print,
        )
    except ProcessingConfigurationError as exc:
        print(describe_exception(exc))
        return 1
    except ImportError as exc:
        missing = exc.name or str(exc)
        print(f"必要な依存ライブラリが未導入です: {missing}")
        print("セットアップ例: pip install -r movie_slide_notes/requirements.txt")
        return 1

    print_summary(result)
    return 0 if result.failed_count == 0 and result.cancelled_count == 0 else 1


def build_processing_options(args: argparse.Namespace) -> ProcessingOptions:
    output_mode = "per_video" if args.per_video_dirs else args.output_mode.replace("-", "_")
    generate_pdf = not args.no_pdf and not (output_mode == "combined" and args.no_combined_pdf)
    operations = OperationOptions(
        extract_slides=not args.no_slides,
        generate_pdf=generate_pdf,
        transcribe_audio=not args.no_transcribe,
        generate_markdown=not args.no_markdown,
        generate_combined_pdf=output_mode == "combined" and generate_pdf,
    ).normalized()

    return ProcessingOptions(
        operations=operations,
        slide_config=SlideDetectionConfig(
            interval=args.interval,
            min_gap=args.min_gap,
            compare_width=args.compare_width,
            diff_threshold=args.diff_threshold,
            min_component_area=args.min_component_area,
            diff_ratio_threshold=args.diff_ratio_threshold,
            ssim_threshold=args.ssim_threshold,
            page_region=args.page_region,
            page_diff_ratio_threshold=args.page_diff_ratio_threshold,
            page_min_component_area=args.page_min_component_area,
        ),
        duplicate_config=DuplicateConfig(
            phash_threshold=args.phash_threshold,
            ssim_threshold=args.duplicate_ssim_threshold,
            compare_width=args.compare_width,
        ),
        transcription_config=TranscriptionConfig(
            model_size=args.whisper_model,
            language=None if args.language == "auto" else args.language,
            device=args.whisper_device,
            compute_type=args.whisper_compute_type,
            keep_audio=args.keep_audio,
            ffmpeg=args.ffmpeg,
        ),
        pdf_margin=args.pdf_margin,
        per_video_dirs=True,
        output_mode=output_mode,
        show_progress=not args.no_progress,
        output_naming=OutputNamingConfig(
            mode=args.name_mode, custom_name=args.output_name, date_style=args.name_date
        ),
    )


def cli_progress(event: ProgressEvent) -> None:
    if event.stage in {"slide_detection", "transcribe"} and event.total:
        print(f"{event.message} ({event.overall_percent}%)", end="\r")
    elif event.stage not in {"slide_detection"}:
        print(event.message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Lectavo",
        description="講義動画からスライドPDF、文字起こし、スライド別Markdownを生成します。",
    )
    parser.add_argument("--input", required=True, type=Path, help="動画ファイルまたは動画フォルダ")
    parser.add_argument("--output", default=Path("output"), type=Path, help="出力先フォルダ")
    parser.add_argument("--name-mode", choices=["original", "common", "custom"], default=DEFAULT_NAMING_MODE, help="出力名の決め方")
    parser.add_argument("--output-name", default="", help="--name-mode custom で使う出力名")
    parser.add_argument("--name-date", choices=["none", "date", "datetime"], default=DEFAULT_DATE_STYLE, help="出力名に付ける日付・日時")
    parser.add_argument(
        "--recursive", action="store_true", help="フォルダ入力時にサブフォルダも処理"
    )
    parser.add_argument(
        "--output-mode",
        choices=["combined", "per-video"],
        default="combined",
        help="combined: 全動画を1フォルダへ統合、per-video: 動画ごとのフォルダへ出力",
    )
    parser.add_argument(
        "--per-video-dirs",
        action="store_true",
        help="互換オプション: --output-mode per-video と同じ",
    )

    parser.add_argument("--interval", type=positive_float, default=1.0, help="確認間隔秒")
    parser.add_argument("--min-gap", type=positive_float, default=1.0, help="保存後の抑制秒")
    parser.add_argument("--compare-width", type=positive_int, default=320, help="比較画像の幅")
    parser.add_argument(
        "--diff-threshold", type=positive_int, default=30, help="ピクセル差分しきい値"
    )
    parser.add_argument(
        "--min-component-area",
        type=positive_int,
        default=800,
        help="無視しない差分連結成分の最小面積",
    )
    parser.add_argument(
        "--diff-ratio-threshold",
        type=positive_float,
        default=0.025,
        help="全体差分面積率しきい値",
    )
    parser.add_argument("--ssim-threshold", type=positive_float, default=0.92, help="SSIMしきい値")
    parser.add_argument(
        "--page-region",
        type=parse_page_region,
        default=(0.75, 0.80, 0.25, 0.20),
        help="自動検出したスライド内のページ番号領域 x,y,w,h。例: 0.75,0.80,0.25,0.20",
    )
    parser.add_argument(
        "--page-diff-ratio-threshold",
        type=positive_float,
        default=0.012,
        help="ページ番号領域の差分面積率しきい値",
    )
    parser.add_argument(
        "--page-min-component-area",
        type=positive_int,
        default=8,
        help="ページ番号領域で無視しない差分連結成分の最小面積",
    )

    parser.add_argument("--phash-threshold", type=non_negative_int, default=8, help="pHash重複距離")
    parser.add_argument(
        "--duplicate-ssim-threshold",
        type=positive_float,
        default=0.97,
        help="SSIM重複しきい値",
    )
    parser.add_argument("--pdf-margin", type=non_negative_float, default=0.0, help="PDFページ余白")

    parser.add_argument("--no-slides", action="store_true", help="スライド抽出を無効化")
    parser.add_argument("--no-pdf", action="store_true", help="PDF生成を無効化")
    parser.add_argument("--no-transcribe", action="store_true", help="文字起こしを実行しない")
    parser.add_argument("--no-markdown", action="store_true", help="スライド別Markdown生成を無効化")
    parser.add_argument(
        "--no-combined-pdf", action="store_true", help="フォルダ全体の統合PDF生成を無効化"
    )
    parser.add_argument(
        "--language", default="auto", choices=["ja", "en", "auto"], help="文字起こし言語"
    )
    parser.add_argument("--whisper-model", default="small", help="faster-whisperモデル名")
    parser.add_argument(
        "--whisper-device", default="cpu", choices=["cpu", "cuda"], help="cpu または cuda"
    )
    parser.add_argument(
        "--whisper-compute-type", default="int8", help="int8, float16, float32 など"
    )
    parser.add_argument("--keep-audio", action="store_true", help="抽出したwav音声を残す")
    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        help="互換性のため残している非推奨オプション（現在はPyAVを使用）",
    )
    parser.add_argument("--no-progress", action="store_true", help="進捗表示を無効化")
    return parser


def parse_page_region(value: str) -> tuple[float, float, float, float]:
    try:
        parts = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "page-region は x,y,w,h の数値で指定してください。"
        ) from exc
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("page-region は x,y,w,h の4値で指定してください。")
    x, y, width, height = parts
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise argparse.ArgumentTypeError(
            "page-region は画面内に収まる 0.0-1.0 の比率で指定してください。"
        )
    return parts


def positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("0より大きい数値を指定してください。")
    return number


def non_negative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("0以上の数値を指定してください。")
    return number


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("0より大きい整数を指定してください。")
    return number


def non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("0以上の整数を指定してください。")
    return number


def print_summary(batch_result: object) -> None:
    print("")
    print("完了")
    print(f"処理動画数: {len(batch_result.results)}")
    print(f"成功: {batch_result.success_count}")
    print(f"失敗: {batch_result.failed_count}")
    print(f"キャンセル: {batch_result.cancelled_count}")
    for result in batch_result.results:
        status = "完了" if result.succeeded else "キャンセル" if result.cancelled else "エラー"
        print(f"- {result.video_path.name}: {status}")
        if result.error:
            print(f"  {result.error}")
        for warning in result.warnings:
            print(f"  警告: {warning}")
    if batch_result.combined_pdf_path:
        print(f"全動画PDF: {batch_result.combined_pdf_path}")
    if batch_result.combined_transcript_path:
        print(f"全動画文字起こし: {batch_result.combined_transcript_path}")
    if batch_result.combined_srt_path:
        print(f"全動画SRT: {batch_result.combined_srt_path}")
    if batch_result.combined_markdown_path:
        print(f"全動画講義ノート: {batch_result.combined_markdown_path}")
    if batch_result.output_mode == "combined":
        print(f"動画間の重複除去数: {batch_result.combined_duplicate_count}")


if __name__ == "__main__":
    sys.exit(main())
