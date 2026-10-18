# Lectavo

[日本語](README.md) | [English](README.en.md) | [公式サイト](https://lectavo.vercel.app) | [最新版をダウンロード](https://github.com/Kou-256/Lectavo/releases/latest)

Lectavoは、講義・プレゼンテーション動画から重複のないスライドPDF、タイムスタンプ付き文字起こし、SRT、スライド別Markdown講義ノートを生成するWindowsデスクトップアプリです。

![Lectavoのホーム画面](docs/images/lectavo-home.png)

## 主な機能

- MP4、MOV、MKV、AVI、WEBM、M4Vのファイル・フォルダ追加とドラッグ＆ドロップ
- OpenCV、SSIM、pHashによるスライド変化検出と重複除去
- レーザーポインター、カーソル、小さな局所変化の除外
- ページ番号領域と大きな画面変化の両方を考慮した検出
- 複数動画を自然なファイル名順で一括処理
- 全動画の重複スライドを除去して一つのPDF、文字起こし、SRT、講義ノートへ統合
- 従来どおり動画ごとに分けて出力するモード
- faster-whisperによる日本語、英語、自動言語検出
- CUDA対応と、実行環境に問題がある場合のCPU自動フォールバック
- 日本語・英語UI、処理履歴、キャンセル、詳細ログ、ライト・ダーク・システムテーマ

![Lectavoの処理設定画面](docs/images/lectavo-ready.png)

## ダウンロード

一般ユーザーは[最新のGitHub Release](https://github.com/Kou-256/Lectavo/releases/latest)から`Lectavo-Setup.exe`を取得してください。インストール後の利用にPython、pip、Git、仮想環境、外部`ffmpeg.exe`は不要です。

- 対応: Windows 10 / 11 x64
- 初期UI言語: 日本語
- 初期文字起こし言語: 自動検出
- 初期モデル: faster-whisper `small`
- GPU: 任意。CPUのみでも利用可能
- デスクトップショートカット: 既定で作成

現在の`0.2.1`配布物は未署名です。Windows SmartScreenが警告する場合があります。ダウンロード後はReleaseに掲載されたSHA-256と照合してください。

## 使い方

1. `Lectavo-Setup.exe`を実行します。
2. デスクトップまたはスタートメニューからLectavoを起動します。
3. 「動画を選択」、フォルダ選択、またはドラッグ＆ドロップで動画を追加します。
4. 「まとめて出力」または「動画ごとに出力」を選びます。
5. 出力内容、保存先、処理品質、CPU/GPUを確認して「処理を開始」を押します。

初回の文字起こし時は選択したWhisperモデルを取得します。モデルは`%LOCALAPPDATA%\Lectavo\Cache`へ保存され、次回以降はキャッシュを利用します。

設定のプルダウンや数値入力は、マウスホイールを回しても値が変わりません。画面をスクロール中の誤変更を防ぎます。設定はクリック、キーボード、数値の直接入力で変更できます。

ホイール操作では入力欄へフォーカスも移動しないため、スクロール中にフォーカス枠が表示されることもありません。Tabキーやクリックによるフォーカス表示は利用できます。

### 出力ファイルの名前

動画追加後の「ファイル名」で、次の命名方法を選べます。実際に保存する名前の例を画面で確認できます。

初期値は「動画名の共通部分」＋「日時を付ける」です。旧初期値の「従来の名前」＋「付けない」は初回のみ新しい初期値へ移行し、その後の変更は保存します。CLIも同じ初期値です。

- **従来の名前**: 統合出力は`all_slides.pdf`など、個別出力は元の動画名。
- **動画名の共通部分**: `講義-13-2.mp4`と`講義-13-10.mp4`なら`講義-13_slides.pdf`。共通部分がない場合は`Lectavo`を使います。
- **自由に入力**: `復習資料`など好きな名前を指定。個別出力では元の動画名も付け、区別できるようにします。
- **日付・日時**: 任意で`_2026-10-18`や`_2026-10-18_15-04-05`を追加。処理開始時刻を全成果物で共通に使います。

PDF・TXT・SRT・Markdownに同じベース名を適用します。統合画像のフォルダにも対応する名前を使い、Markdown内の画像リンクも自動調整します。Windowsで使えない文字は安全な文字へ置換します。命名方法は自動保存されます。同じ名前で再実行すると既存成果物を更新するため、別の処理として残す場合は日時を付けてください。

CLIでも`--name-mode common --name-date datetime`、または`--name-mode custom --output-name "復習資料"`で指定できます。

## 出力例

以下は「従来の名前」＋「付けない」を選んだ場合の例です。初期設定では`講義-13_2026-10-18_15-04-05_slides.pdf`のような名前になります。

複数動画をまとめて出力する場合:

```text
Lectavo_output/
  slides/
    slide_001_00m12s.png
    slide_002_01m35s.png
  all_slides.pdf
  all_transcript.txt
  all_transcript.srt
  all_lecture_notes.md
```

動画ごとに出力する場合:

```text
Lectavo_output/
  lecture-01/
    slides/
    lecture-01_slides.pdf
    lecture-01_transcript.txt
    lecture-01_transcript.srt
    lecture-01_slides.md
```

## 検出の仕組み

Lectavoは一定間隔で動画フレームを確認し、縮小グレースケール画像の差分面積率、連結成分、SSIM、ページ番号領域を総合してスライド変更候補を作ります。保存候補はpHashとSSIMで既出スライド全体と比較し、前のページへ戻った場面や動画をまたいだ再表示を除外します。

プリセットは「高速」「標準」「高精度」を用意しています。詳細設定では確認間隔、最小差分領域、SSIMしきい値、pHash距離などを調整できます。

## 文字起こしとGPU

Lectavoは[faster-whisper](https://github.com/SYSTRAN/faster-whisper)を使用します。メディアはPyAVで直接デコードするため、外部`ffmpeg.exe`を起動しません。

CUDAを選んだ場合は必要なNVIDIAランタイムを事前診断します。GPUやDLLを利用できない場合は理由をログへ残し、「CUDAが使えないためCPUで続行しました」と表示してCPU `int8`へ切り替えます。

## プライバシー

動画、抽出画像、PDF、文字起こし、講義ノートをLectavoのサーバーへアップロードする処理はありません。文字起こしモデルの初回取得時のみHugging Faceへ接続します。入力動画や第三者のスライドをIssueやPull Requestへ添付する場合は、公開権限があることを確認してください。

## 開発環境

このチェックアウトで検証したPythonバージョンは`.python-version`に記録しています。

```powershell
py -3.14 -m venv movie_slide_notes\.venv
.\movie_slide_notes\.venv\Scripts\python.exe -m pip install -U pip
.\movie_slide_notes\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\movie_slide_notes\.venv\Scripts\python.exe app.py
```

CLI:

```powershell
.\movie_slide_notes\.venv\Scripts\python.exe movie_slide_notes\main.py `
  --input "C:\Lectavo Demo\videos" `
  --output "C:\Lectavo Demo\output" `
  --language auto `
  --model small `
  --device cpu
```

テストと公開前監査:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\movie_slide_notes\.venv\Scripts\python.exe -m ruff format --check movie_slide_notes tests app.py
.\movie_slide_notes\.venv\Scripts\python.exe -m ruff check movie_slide_notes tests app.py
.\movie_slide_notes\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\scripts\audit_public_release.ps1
```

## Windows配布版のビルド

PyInstaller `onedir`とInno Setupを利用します。

```powershell
.\scripts\build_release.ps1
```

生成物:

```text
release/
  Lectavo-Setup.exe
  Lectavo-portable.zip
  SHA256SUMS.txt
  VERSION.txt
  release-notes.txt
```

署名証明書はリポジトリへ保存しないでください。`LECTAVO_SIGN_PFX`、`LECTAVO_SIGN_PASSWORD`などを安全な環境変数またはCI Secretとして設定し、`scripts/sign_release.ps1`を利用します。

## 既知の制限

- 段階的アニメーションを多数含むスライドは複数ページとして検出される場合があります。
- カメラ映像とスライドが頻繁に入れ替わる動画はしきい値調整が必要です。
- 見た目が極端に似た連続ページでは、重複しきい値の調整が必要な場合があります。
- Windows ARM64は未検証です。
- Authenticode署名とクリーンWindows VMでの最終配布検証は未完了です。

検証済み・未確認事項の詳細は[PUBLIC_RELEASE_CHECKLIST.md](PUBLIC_RELEASE_CHECKLIST.md)を参照してください。

## コントリビューション

開発手順とPull Requestの方針は[CONTRIBUTING.md](CONTRIBUTING.md)、脆弱性の報告方法は[SECURITY.md](SECURITY.md)を参照してください。

## ライセンス

Lectavoのソースコードは[MIT License](LICENSE)です。依存ライブラリには個別のライセンスが適用されます。[THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)と`licenses/`を確認してください。特にQt、PyAV、OpenCV、同梱されるFFmpegライブラリの条件は配布者自身で確認する必要があります。
