"""Built-in local LLM: downloadable GGUF models served by a bundled llama.cpp.

Models are fetched from Hugging Face at a pinned revision and checked against a
pinned SHA-256 before use. The bundled ``llama-server.exe`` (llama.cpp, Vulkan
build with CPU fallback) serves an OpenAI-compatible API on 127.0.0.1 only.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from movie_slide_notes.paths import user_cache_dir


@dataclass(frozen=True)
class LocalModel:
    key: str
    name: str
    tier: str  # shown as a badge
    description: str
    repo: str
    revision: str
    filename: str
    sha256: str
    size_bytes: int
    min_ram_gb: int

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{self.filename}"

    @property
    def size_gb(self) -> float:
        return self.size_bytes / 1_000_000_000


# All Apache-2.0. Sizes and hashes are from the Hugging Face file metadata at
# the pinned revision.
CATALOG: list[LocalModel] = [
    LocalModel(
        key="gemma-4-e2b",
        name="Gemma 4 E2B",
        tier="軽量",
        description="メモリの少ないPC向け。短い資料をすばやく訳します。",
        repo="ggml-org/gemma-4-E2B-it-GGUF",
        revision="2a8538c2e2d4687953a86c422c7c623cfd022880",
        filename="gemma-4-E2B-it-Q4_0.gguf",
        sha256="8e30dff3ac4c8434c49a7036fa15564bdbb6044e42bf04550bf1a096ad7e6a52",
        size_bytes=2_840_000_000,
        min_ram_gb=8,
    ),
    LocalModel(
        key="qwen3-4b-2507",
        name="Qwen3 4B Instruct",
        tier="バランス",
        description="速さと品質のバランスが良く、多言語に強いモデル。",
        repo="unsloth/Qwen3-4B-Instruct-2507-GGUF",
        revision="a06e946bb6b655725eafa393f4a9745d460374c9",
        filename="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        sha256="3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
        size_bytes=2_500_000_000,
        min_ram_gb=8,
    ),
    LocalModel(
        key="gemma-4-e4b",
        name="Gemma 4 E4B",
        tier="高品質",
        description="より自然な訳文。16GB以上のメモリを推奨します。",
        repo="ggml-org/gemma-4-E4B-it-GGUF",
        revision="8903cb408c7df39cde2ed15fac397e6d7be92951",
        filename="gemma-4-E4B-it-Q4_0.gguf",
        sha256="a555b900214b477d8880e7832e0b8925e139b0159640036b09fe472b6f2097f2",
        size_bytes=4_590_000_000,
        min_ram_gb=16,
    ),
    LocalModel(
        key="gemma-4-12b",
        name="Gemma 4 12B",
        tier="おすすめ",
        description="最も正確で自然な訳文。専門的な論文向け。GPU（VRAM 8GB以上）か32GBメモリを推奨します。",
        repo="ggml-org/gemma-4-12B-it-GGUF",
        revision="501d13dba70e3c02b1b12c78fd9e6b08ad413960",
        filename="gemma-4-12B-it-Q4_0.gguf",
        sha256="3712b9bd32cae83a22f67ee7a4466d8d7a4f21646ac8a07d19bf9418e8767a70",
        size_bytes=7_220_000_000,
        min_ram_gb=24,
    ),
]
DEFAULT_MODEL_KEY = "gemma-4-12b"
CONTEXT_TOKENS = 8192
STARTUP_TIMEOUT = 240.0


class LocalLLMError(RuntimeError):
    pass


class DownloadCancelled(Exception):
    pass


def model_by_key(key: str) -> LocalModel:
    for model in CATALOG:
        if model.key == key:
            return model
    return next(m for m in CATALOG if m.key == DEFAULT_MODEL_KEY)


def models_dir() -> Path:
    return user_cache_dir() / "local_llm"


def model_path(model: LocalModel) -> Path:
    return models_dir() / model.filename


def _verified_marker(model: LocalModel) -> Path:
    return models_dir() / f"{model.filename}.verified"


def is_installed(model: LocalModel) -> bool:
    path = model_path(model)
    marker = _verified_marker(model)
    return path.is_file() and marker.is_file() and marker.read_text().strip() == model.sha256


def delete_model(model: LocalModel) -> None:
    stop_server()
    for path in (
        model_path(model),
        _verified_marker(model),
        model_path(model).with_suffix(".part"),
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def download_model(
    model: LocalModel,
    progress: Callable[[int, int], None] | None = None,
    cancel_event: Event | None = None,
) -> Path:
    """Download (resuming a partial file) and verify ``model``; returns its path."""
    import httpx

    folder = models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = model_path(model)
    partial = target.with_suffix(".part")
    digest = hashlib.sha256()
    done = 0
    if partial.exists():
        with partial.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
                done += len(chunk)
    headers = {"User-Agent": "Lectavo"}
    if done:
        headers["Range"] = f"bytes={done}-"
    try:
        with httpx.stream(
            "GET",
            model.url,
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=120.0),
        ) as response:
            if response.status_code == 416:  # already complete
                pass
            elif response.status_code == 200 and done:
                # The server ignored the range: start over.
                done, digest = 0, hashlib.sha256()
                partial.unlink(missing_ok=True)
            elif response.status_code not in (200, 206):
                raise LocalLLMError(
                    f"モデルをダウンロードできませんでした（{response.status_code}）。"
                )
            total = done + int(response.headers.get("content-length", 0) or 0) or model.size_bytes
            if response.status_code in (200, 206):
                with partial.open("ab") as handle:
                    last = 0.0
                    for chunk in response.iter_bytes(1 << 20):
                        if cancel_event is not None and cancel_event.is_set():
                            raise DownloadCancelled()
                        handle.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        now = time.monotonic()
                        if progress is not None and now - last > 0.2:
                            progress(done, total)
                            last = now
    except httpx.HTTPError as exc:
        raise LocalLLMError(
            "モデルのダウンロード中に接続が切れました。再開すると続きからダウンロードします。"
        ) from exc
    if digest.hexdigest() != model.sha256:
        partial.unlink(missing_ok=True)
        raise LocalLLMError(
            "ダウンロードしたモデルが破損していました。もう一度ダウンロードしてください。"
        )
    partial.replace(target)
    _verified_marker(model).write_text(model.sha256)
    if progress is not None:
        progress(done, done)
    return target


# --- runtime -----------------------------------------------------------------


def runtime_dir() -> Path | None:
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "llama.cpp")
        candidates.append(Path(sys.executable).parent / "llama.cpp")
    root = Path(__file__).resolve().parents[2]
    candidates.append(root / "vendor" / "llama.cpp")
    for candidate in candidates:
        if (candidate / "llama-server.exe").is_file():
            return candidate
    return None


def runtime_available() -> bool:
    return runtime_dir() is not None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalServer:
    """One llama-server process, restarted when another model or GPU setting is needed."""

    def __init__(self):
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._key: tuple[str, bool] | None = None
        self.port = 0
        self.api_key = ""
        self.log_path = user_cache_dir() / "local_llm" / "server.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def ensure(self, model: LocalModel, use_gpu: bool, cancel_event: Event | None = None) -> str:
        with self._lock:
            if self.running() and self._key == (model.key, use_gpu):
                return self.base_url
            self._stop_locked()
            if not is_installed(model):
                raise LocalLLMError(
                    f"{model.name} がダウンロードされていません。設定ページでダウンロードしてください。"
                )
            try:
                self._start_locked(model, use_gpu, cancel_event)
            except LocalLLMError:
                if not use_gpu:
                    raise
                # The GPU path failed (driver, memory): fall back to CPU once.
                self._stop_locked()
                self._start_locked(model, False, cancel_event)
            self._key = (model.key, use_gpu)
            return self.base_url

    def _start_locked(self, model: LocalModel, use_gpu: bool, cancel_event: Event | None) -> None:
        import httpx

        folder = runtime_dir()
        if folder is None:
            raise LocalLLMError(
                "内蔵のローカルLLMが見つかりません。Lectavoを再インストールしてください。"
            )
        self.port = _free_port()
        # A fresh key per launch, so other programs (and web pages in a browser)
        # cannot use the local server.
        self.api_key = secrets.token_urlsafe(24)
        args = [
            str(folder / "llama-server.exe"),
            "--model",
            str(model_path(model)),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--ctx-size",
            str(CONTEXT_TOKENS),
            "--parallel",
            "1",
            "--jinja",
            "--no-ui",
            "--api-key",
            self.api_key,
            "--n-gpu-layers",
            "999" if use_gpu else "0",
        ]
        if not use_gpu:
            args += ["--device", "none"]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("wb")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(
            args, cwd=str(folder), stdout=log, stderr=subprocess.STDOUT, creationflags=flags
        )
        log.close()
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                self._stop_locked()
                raise LocalLLMError("キャンセルされました。")
            if self._process.poll() is not None:
                raise LocalLLMError(
                    "ローカルLLMを起動できませんでした。メモリが足りない可能性があります。"
                    "より軽いモデルを選んでください。\n\n" + self.log_tail()
                )
            try:
                if (
                    httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=2.0).status_code
                    == 200
                ):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise LocalLLMError("ローカルLLMの起動に時間がかかりすぎました。")

    def log_tail(self, lines: int = 12) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.strip().splitlines()[-lines:])

    def _stop_locked(self) -> None:
        process, self._process = self._process, None
        self._key = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()


_server = LocalServer()


def server() -> LocalServer:
    return _server


def stop_server() -> None:
    _server.stop()


atexit.register(stop_server)
