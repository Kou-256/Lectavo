"""API keys encrypted for the current Windows user (DPAPI).

Only the same Windows account on the same PC can decrypt a stored key, so the
settings file never contains a usable key.
"""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes

_ENTROPY = b"Lectavo translation key"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob._buffer = buffer  # keep the buffer alive
    return blob


def _crypt(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source, entropy, output = _blob(data), _blob(_ENTROPY), _Blob()
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = function(
        ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x01, ctypes.byref(output)
    )
    if not ok:
        raise OSError("APIキーの暗号化・復号に失敗しました。")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def protect(secret: str) -> str:
    if not secret:
        return ""
    if os.name != "nt":  # pragma: no cover - the app ships for Windows only
        raise OSError("APIキーの保存はWindowsでのみ利用できます。")
    return base64.b64encode(_crypt(secret.encode("utf-8"), True)).decode("ascii")


def unprotect(stored: str) -> str:
    if not stored:
        return ""
    if os.name != "nt":  # pragma: no cover
        return ""
    try:
        return _crypt(base64.b64decode(stored), False).decode("utf-8")
    except (OSError, ValueError):
        return ""
