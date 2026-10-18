from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QWidget

DEFAULT_UI_LANGUAGE = "ja"
SUPPORTED_UI_LANGUAGES = ("ja", "en")
_SOURCE_TEXT_PROPERTY = "lectavo_source_text"
_SOURCE_TOOLTIP_PROPERTY = "lectavo_source_tooltip"
_SOURCE_ACCESSIBLE_PROPERTY = "lectavo_source_accessible"
_SOURCE_PLACEHOLDER_PROPERTY = "lectavo_source_placeholder"
_COMBO_SOURCE_ROLE = Qt.UserRole + 101
_current_language = DEFAULT_UI_LANGUAGE


ENGLISH: dict[str, str] = {
    "ファイル名": "File names",
    "名前の付け方": "Naming",
    "従来の名前": "Original names",
    "動画名の共通部分": "Common video name",
    "自由に入力": "Custom name",
    "例: 第3回 講義資料": "Example: Lecture 3 notes",
    "出力名": "Output name",
    "出力名の例": "Filename preview",
    "出力名を入力してください。": "Enter an output name.",
    "日付・日時": "Date / time",
    "付けない": "None",
    "日付を付ける": "Append date",
    "日時を付ける": "Append date and time",
    "ホーム": "Home",
    "履歴": "History",
    "設定": "Settings",
    "ヘルプ": "Help",
    "新しい処理": "New job",
    "講義動画からスライドPDFと文字起こしを作成します。": "Create slide PDFs and transcripts from lecture videos.",
    "動画をここにドロップ": "Drop videos here",
    "MP4、MOV、MKV、AVI、WEBM、M4V に対応": "Supports MP4, MOV, MKV, AVI, WEBM, and M4V",
    "動画を選択": "Choose videos",
    "フォルダを選択": "Choose folder",
    "最近の処理": "Recent jobs",
    "出力内容を選んでから処理を開始します。": "Choose the outputs, then start processing.",
    "動画": "Videos",
    "すべて削除": "Remove all",
    "出力方法": "Output mode",
    "まとめて出力（1フォルダ）": "Combined output (one folder)",
    "動画ごとに出力": "Separate output per video",
    "まとめて出力では、全動画の重複スライドを除去してPDF、文字起こし、講義ノートを1つにまとめます。": "Combined output removes duplicate slides across all videos and creates one PDF, transcript, and lecture note.",
    "すべての動画を1フォルダにまとめ、動画をまたぐ重複スライドも除去します。": "Store all videos in one folder and remove duplicate slides across videos.",
    "動画ごとのフォルダにPDF、文字起こし、講義ノートを保存します。": "Save PDFs, transcripts, and lecture notes in a folder for each video.",
    "出力内容": "Outputs",
    "スライドPDF": "Slide PDF",
    "文字起こし＋講義ノート": "Transcript + lecture notes",
    "画像ファイル": "Slide images",
    "保存先": "Save to",
    "出力先": "Output folder",
    "変更": "Change",
    "処理品質": "Processing quality",
    "品質": "Quality",
    "高速": "Fast",
    "軽量": "Lightweight",
    "標準": "Standard",
    "高精度": "High accuracy",
    "最高精度": "Highest accuracy",
    "文字起こし処理": "Transcription device",
    "CPU（GPUから自動切替）": "CPU (automatic GPU fallback)",
    "GPU（CUDA）": "GPU (CUDA)",
    "GPU（現在利用不可）": "GPU (currently unavailable)",
    "文字起こしに使用する処理デバイスです。GPUが利用できない場合は理由を表示してCPUへ自動で切り替わります。": "Select the transcription device. If the GPU is unavailable, Lectavo explains why and automatically continues on the CPU.",
    "詳細設定": "Advanced settings",
    "処理を開始": "Start processing",
    "処理しています": "Processing",
    "準備中": "Preparing",
    "待機中": "Waiting",
    "経過時間 00:00": "Elapsed 00:00",
    "現在の処理設定": "Current settings",
    "キャンセル": "Cancel",
    "キャンセル中": "Cancelling",
    "実行中の処理を停止しています。": "Stopping the active operation.",
    "詳細ログを表示": "Show detailed log",
    "詳細ログを閉じる": "Hide detailed log",
    "処理が完了しました": "Processing complete",
    "出力フォルダを開く": "Open output folder",
    "PDFを開く": "Open PDF",
    "文字起こしを開く": "Open transcript",
    "処理に問題がありました": "A processing problem occurred",
    "再試行するか、詳細ログを確認してください。": "Try again or review the detailed log.",
    "詳細を表示": "Show details",
    "再試行": "Try again",
    "診断情報をコピー": "Copy diagnostics",
    "ログフォルダを開く": "Open log folder",
    "最近処理した動画と出力先です。": "Recently processed videos and their output folders.",
    "履歴をすべて削除": "Clear all history",
    "すべての処理履歴を削除しますか？\n\n生成したPDFや文字起こし、入力動画は削除されません。": "Clear all processing history?\n\nGenerated PDFs, transcripts, and input videos will not be deleted.",
    "この履歴だけを削除": "Remove only this history entry",
    "通常は標準設定のままで利用できます。専門的な設定は必要なときだけ変更してください。": "The standard settings work for most videos. Change advanced options only when needed.",
    "一般": "General",
    "デフォルト出力先": "Default output folder",
    "表示言語": "Display language",
    "日本語": "Japanese",
    "英語": "English",
    "Lectavoの基本操作と、困ったときの確認先です。": "Basic Lectavo usage and troubleshooting resources.",
    "はじめに": "Getting started",
    "1. 動画を追加": "1. Add videos",
    "2. 出力内容を選択": "2. Choose outputs",
    "3. 処理を開始": "3. Start processing",
    "よくある問題": "Common issues",
    "スライドが多く抽出される: 処理品質を高速にするか、詳細設定で最小差分領域を上げます。": "Too many slides: choose Fast quality or increase the minimum changed area in Advanced settings.",
    "スライドが抜ける: 処理品質を高精度にするか、フレーム確認間隔を短くします。": "Missing slides: choose High accuracy or shorten the frame sampling interval.",
    "同じスライドが残る: 重複判定距離や重複SSIMしきい値を調整します。": "Duplicate slides remain: adjust the pHash distance or duplicate SSIM threshold.",
    "文字起こしが開始されない: モデル準備のログを確認します。": "Transcription does not start: review the model preparation log.",
    "処理が途中で停止する: 出力先の空き容量と動画ファイルの状態を確認します。": "Processing stops: check free disk space and the condition of the video file.",
    "トラブルシューティング": "Troubleshooting",
    "Lectavoについて": "About Lectavo",
    "発行元": "Publisher",
    "講義動画からスライドと文字起こしを生成するアプリ": "An app that creates slides and transcripts from lecture videos",
    "第三者ライセンス": "Third-party licenses",
    "閉じる": "Close",
    "文字起こしの言語": "Transcription language",
    "自然に検出": "Detect automatically",
    "文字起こし品質": "Transcription quality",
    "さらに細かい検出しきい値、CUDA、計算精度は設定ページの開発者向けから変更できます。": "More detailed detection thresholds, CUDA, and compute precision are available under Developer settings.",
    "少なくとも1つの出力内容が必要です。": "Select at least one output.",
    "スライドPDFにはスライド画像の抽出が必要です。": "Slide PDF requires slide image extraction.",
    "文字起こし＋講義ノートにはスライド画像が必要なため、自動で有効にしました。": "Slide images were enabled because transcript + lecture notes requires them.",
    "動画ファイル": "Video files",
    "すべて": "All files",
    "動画フォルダを選択": "Choose a video folder",
    "出力先を選択": "Choose an output folder",
    "出力先がありません": "No output folder",
    "出力先フォルダを選択してください。": "Choose an output folder.",
    "処理をキャンセル": "Cancel processing",
    "本当にキャンセルしていいですか？\n\n実行中の処理を中断してホーム画面に戻ります。": "Cancel processing?\n\nThe active operation will stop and Lectavo will return to Home.",
    "エラー": "Error",
    "完了": "Complete",
    "完了（警告あり）": "Complete (with warnings)",
    "削除": "Remove",
    "動画はまだ追加されていません。": "No videos have been added.",
    "まだ履歴はありません。": "No history yet.",
    "処理中です": "Processing is active",
    "処理をキャンセルして終了しますか？": "Cancel processing and exit?",
    "処理開始": "Starting",
    "スライド解析中": "Detecting slides",
    "PDF生成中": "Creating PDF",
    "音声抽出中": "Decoding audio",
    "モデル準備中": "Preparing model",
    "文字起こし中": "Transcribing",
    "Markdown生成中": "Creating Markdown",
    "統合PDF生成中": "Creating combined PDF",
    "処理": "Processing",
    "通常は標準で十分です。高精度は短時間だけ表示されたスライドを拾いやすくなります。": "Standard works for most videos. High accuracy is more likely to capture briefly displayed slides.",
    "スライド検出の詳細": "Slide detection details",
    "値を小さくすると短時間だけ表示されたスライドを拾いやすくなりますが、処理時間が増加します。": "Lower values capture briefly displayed slides more easily but increase processing time.",
    "フレーム確認間隔": "Frame sampling interval",
    "スライド保存後に次の保存を抑制する秒数です。アニメーション途中の連続保存を減らします。": "Seconds to wait after saving a slide. This reduces captures during animations.",
    "最小保存間隔": "Minimum save interval",
    "画像差分のために縮小する幅です。大きいほど精密ですが処理時間が増えます。": "Width used for image comparison. Larger values are more precise but slower.",
    "比較画像幅": "Comparison width",
    "ピクセル単位で変化とみなす明るさ差です。上げると小さな色変化を無視します。": "Brightness difference treated as a changed pixel. Higher values ignore small color changes.",
    "ピクセル差分しきい値": "Pixel difference threshold",
    "小さい差分領域を無視する面積です。上げるとポインターやカーソルを誤検出しにくくなります。": "Minimum changed area. Higher values reduce pointer and cursor false positives.",
    "最小差分領域": "Minimum changed area",
    "画面全体のうち、どれだけ変化したら新しいスライドとみなすかです。": "Changed proportion required to detect a new slide.",
    "全体差分率しきい値": "Overall difference ratio",
    "画像構造の類似度です。上げると似たスライドも検出しやすくなります。": "Structural similarity threshold. Higher values detect more similar slides as changes.",
    "SSIMしきい値": "SSIM threshold",
    "幅": "Width",
    "高さ": "Height",
    "画面内のスライド領域を自動検出し、その内側でページ番号などがある領域を比率指定します。初期値は 0.75, 0.80, 0.25, 0.20 です。": "Lectavo detects the slide surface, then uses these ratios for its page-number region. Default: 0.75, 0.80, 0.25, 0.20.",
    "ページ番号領域": "Page-number region",
    "ページ番号領域だけを見た差分率です。下げるとページ番号変化を拾いやすくなります。": "Difference ratio for the page-number region. Lower values detect smaller number changes.",
    "ページ領域差分率": "Page-region difference ratio",
    "ページ番号領域内で無視しない差分の最小面積です。": "Minimum changed area inside the page-number region.",
    "ページ領域最小成分面積": "Page-region minimum component",
    "小さいほど重複判定が厳しくなります。pHashだけで除外せず、画像構造も似ている場合だけ重複とみなします。": "Lower values make duplicate matching stricter. Lectavo also checks image structure before removing a slide.",
    "pHash重複判定距離": "Duplicate pHash distance",
    "大きいほど、ほぼ完全一致に近い画像だけを重複とみなします。": "Higher values require a closer visual match before treating slides as duplicates.",
    "重複SSIMしきい値": "Duplicate SSIM threshold",
    "文字起こし": "Transcription",
    "言語": "Language",
    "外観": "Appearance",
    "システム設定に追従": "Follow system setting",
    "ライト": "Light",
    "ダーク": "Dark",
    "テーマ": "Theme",
    "開発者向け": "Developer options",
    "処理デバイス": "Processing device",
    "計算精度": "Compute type",
    "適用": "Apply",
}


def normalize_ui_language(value: Any) -> str:
    language = str(value or DEFAULT_UI_LANGUAGE).lower()
    return language if language in SUPPORTED_UI_LANGUAGES else DEFAULT_UI_LANGUAGE


def set_ui_language(language: str) -> str:
    global _current_language
    _current_language = normalize_ui_language(language)
    return _current_language


def current_ui_language() -> str:
    return _current_language


def tr(source: str) -> str:
    if _current_language == DEFAULT_UI_LANGUAGE or not source:
        return source
    return _translate_english(source)


def _translate_english(source: str) -> str:
    prefix_match = re.fullmatch(r"([>v] )(.*)", source, flags=re.DOTALL)
    if prefix_match:
        return prefix_match.group(1) + _translate_english(prefix_match.group(2))
    direct = ENGLISH.get(source)
    if direct is not None:
        return direct
    patterns = [
        (r"(.+) の履歴を削除", r"Remove history for \1"),
        (r"(.+) を処理しています", r"Processing \1"),
        (r"経過時間 (.+)", r"Elapsed \1"),
        (r"(\d+)本中 (\d+)本成功、(\d+)本失敗しました。", r"\2 of \1 succeeded; \3 failed."),
        (r"スライドPDF: (\d+) 件", r"Slide PDFs: \1"),
        (r"抽出スライド: (\d+) 枚", r"Extracted slides: \1"),
        (r"文字起こし: (\d+) 件", r"Transcripts: \1"),
        (r"講義ノート: (\d+) 件", r"Lecture notes: \1"),
        (r"動画間の重複除去: (\d+) 枚", r"Cross-video duplicates removed: \1"),
        (r"エラー: (.+)", r"Error: \1"),
        (r"出力方法: (.+)", r"Output mode: \1"),
        (r"出力: (.+)", r"Outputs: \1"),
        (r"処理品質: (.+)", r"Processing quality: \1"),
        (r"言語: (.+)", r"Language: \1"),
        (r"文字起こし品質: (.+)", r"Transcription quality: \1"),
        (r"処理デバイス: (.+)", r"Processing device: \1"),
    ]
    for pattern, replacement in patterns:
        if re.fullmatch(pattern, source, flags=re.DOTALL):
            return re.sub(pattern, replacement, source, flags=re.DOTALL)
    return source


def translate_widget_tree(root: QWidget) -> None:
    for widget in [root, *root.findChildren(QWidget)]:
        if isinstance(widget, (QLabel, QAbstractButton)):
            _translate_text_property(widget, "text", "setText", _SOURCE_TEXT_PROPERTY)
        if isinstance(widget, QGroupBox):
            _translate_text_property(widget, "title", "setTitle", _SOURCE_TEXT_PROPERTY + "_title")
        if isinstance(widget, QLineEdit):
            _translate_text_property(
                widget,
                "placeholderText",
                "setPlaceholderText",
                _SOURCE_PLACEHOLDER_PROPERTY,
            )
        _translate_text_property(widget, "toolTip", "setToolTip", _SOURCE_TOOLTIP_PROPERTY)
        _translate_text_property(
            widget,
            "accessibleName",
            "setAccessibleName",
            _SOURCE_ACCESSIBLE_PROPERTY,
        )
        if isinstance(widget, QComboBox):
            for index in range(widget.count()):
                source = widget.itemData(index, _COMBO_SOURCE_ROLE)
                current = widget.itemText(index)
                if source is None:
                    source = current
                    widget.setItemData(index, source, _COMBO_SOURCE_ROLE)
                elif current not in {str(source), _translate_english(str(source))}:
                    source = current
                    widget.setItemData(index, source, _COMBO_SOURCE_ROLE)
                widget.setItemText(index, tr(str(source)))


def _translate_text_property(
    widget: QWidget, getter_name: str, setter_name: str, property_name: str
) -> None:
    getter = getattr(widget, getter_name, None)
    setter = getattr(widget, setter_name, None)
    if getter is None or setter is None:
        return
    current = str(getter())
    source = widget.property(property_name)
    if source is None:
        source = current
        widget.setProperty(property_name, source)
    elif current not in {str(source), _translate_english(str(source))}:
        source = current
        widget.setProperty(property_name, source)
    setter(tr(str(source)))
