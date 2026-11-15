from __future__ import annotations

import hashlib
import json

from PySide6.QtCore import QIODevice, QObject, QSaveFile, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from movie_slide_notes.paths import user_cache_dir
from movie_slide_notes.updates import (
    RELEASE_API,
    UpdateInfo,
    parse_release,
    trusted_asset_url,
    version_tuple,
)
from movie_slide_notes.version import __version__


class UpdateManager(QObject):
    available = Signal(object)
    checked = Signal()
    failed = Signal(str)
    progress = Signal(int, int)
    downloaded = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.reply = None
        self.file = None
        self.was_cancelled = False

    @property
    def busy(self):
        return self.reply is not None

    def _request(self, url):
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", f"Lectavo/{__version__}".encode("ascii"))
        request.setTransferTimeout(30000)
        request.setAttribute(
            QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy
        )
        self.reply = self.network.get(request)
        return self.reply

    def check(self):
        if self.busy:
            return
        self.buffer = bytearray()
        reply = self._request(RELEASE_API)
        reply.readyRead.connect(self._read_metadata)
        reply.finished.connect(self._checked)

    def _read_metadata(self):
        self.buffer.extend(bytes(self.reply.readAll()))
        if len(self.buffer) > 1_000_000:
            self.reply.abort()

    def _checked(self):
        reply, self.reply = self.reply, None
        try:
            if reply.error() != QNetworkReply.NoError:
                raise ValueError("更新を確認できませんでした。ネットワーク接続を確認してください。")
            self.buffer.extend(bytes(reply.readAll()))
            if len(self.buffer) > 1_000_000:
                raise ValueError("更新情報が大きすぎます。")
            info = parse_release(json.loads(self.buffer), __version__)
            if info:
                self.available.emit(info)
            else:
                self.checked.emit()
        except (ValueError, TypeError, KeyError) as exc:
            self.failed.emit(str(exc))
        finally:
            reply.deleteLater()

    def download(self, info: UpdateInfo):
        if self.busy:
            return
        try:
            version_tuple(info.version)
        except ValueError:
            self.failed.emit("更新情報の形式が不正です。")
            return
        if not trusted_asset_url(info.url, info.version, "Lectavo-Setup.exe"):
            self.failed.emit("更新ファイルの配布元を確認できません。")
            return
        folder = user_cache_dir() / "updates" / info.version
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.failed.emit("更新ファイルを保存できません。空き容量を確認してください。")
            return
        self.path = folder / "Lectavo-Setup.exe"
        self.file = QSaveFile(str(self.path))
        if not self.file.open(QIODevice.WriteOnly):
            self.failed.emit("更新ファイルを保存できません。空き容量を確認してください。")
            return
        self.info = info
        self.hasher = hashlib.sha256()
        self.received = 0
        self.was_cancelled = False
        self.write_failed = False
        reply = self._request(info.url)
        reply.readyRead.connect(self._read_download)
        reply.finished.connect(self._download_finished)

    def _read_download(self):
        chunk = bytes(self.reply.readAll())
        self.received += len(chunk)
        if self.received > self.info.size or self.file.write(chunk) != len(chunk):
            self.write_failed = True
            self.reply.abort()
            return
        self.hasher.update(chunk)
        self.progress.emit(self.received, self.info.size)

    def cancel(self):
        if self.reply is not None:
            self.was_cancelled = True
            self.reply.abort()

    def _download_finished(self):
        reply = self.reply
        if not self.was_cancelled and reply.error() == QNetworkReply.NoError:
            self._read_download()
        self.reply = None
        try:
            if self.was_cancelled:
                self.file.cancelWriting()
                self.cancelled.emit()
            elif (
                reply.error() != QNetworkReply.NoError
                or self.write_failed
                or self.received != self.info.size
                or self.hasher.hexdigest() != self.info.sha256
            ):
                self.file.cancelWriting()
                self.failed.emit(
                    "更新ファイルの検証に失敗しました。ダウンロードをやり直してください。"
                )
            elif not self.file.commit():
                self.failed.emit("更新ファイルを保存できませんでした。")
            else:
                self.downloaded.emit(str(self.path))
        finally:
            self.file = None
            reply.deleteLater()
