from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

RELEASE_API = "https://api.github.com/repos/Kou-256/Lectavo/releases/latest"
RELEASE_PAGE = "https://github.com/Kou-256/Lectavo/releases/latest"


def version_tuple(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise ValueError("Invalid stable release version")
    return tuple(int(value) for value in match.groups())


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    size: int
    sha256: str


def trusted_asset_url(url: str, version: str, name: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and parsed.path == f"/Kou-256/Lectavo/releases/download/v{version}/{name}"
        and not parsed.query
        and not parsed.fragment
    )


def parse_release(data: dict, current: str) -> UpdateInfo | None:
    if not isinstance(data, dict) or not isinstance(data.get("assets", []), list):
        raise ValueError("更新情報の形式が不正です。")
    version = str(data.get("tag_name", "")).removeprefix("v")
    if (
        data.get("draft")
        or data.get("prerelease")
        or version_tuple(version) <= version_tuple(current)
    ):
        return None
    matches = [
        asset
        for asset in data.get("assets", [])
        if isinstance(asset, dict) and asset.get("name") == "Lectavo-Setup.exe"
    ]
    if len(matches) != 1:
        raise ValueError("更新用Setupが見つかりません。")
    asset = matches[0]
    digest = str(asset.get("digest", ""))
    if not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        raise ValueError("更新ファイルの検証情報を取得できません。")
    url = str(asset.get("browser_download_url", ""))
    if not trusted_asset_url(url, version, "Lectavo-Setup.exe"):
        raise ValueError("更新ファイルの配布元を確認できません。")
    size = asset.get("size", 0)
    if not isinstance(size, int) or not 0 < size < 2_000_000_000:
        raise ValueError("更新ファイルのサイズが不正です。")
    return UpdateInfo(version, url, size, digest[7:].lower())
