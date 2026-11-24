"""Languages offered for PDF translation."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    label: str  # Japanese UI label (translated by the UI layer)
    native: str  # used in output file names
    prompt_name: str  # English name for the translation prompt
    deepl_target: str | None
    deepl_source: str | None
    script: str  # font group: ja, zh-hans, zh-hant, ko, latin


LANGUAGES: list[Language] = [
    Language("ja", "日本語", "日本語", "Japanese", "JA", "JA", "ja"),
    Language("en", "英語", "English", "English", "EN-US", "EN", "latin"),
    Language(
        "zh-Hans", "中国語（簡体字）", "简体中文", "Simplified Chinese", "ZH-HANS", "ZH", "zh-hans"
    ),
    Language(
        "zh-Hant", "中国語（繁体字）", "繁體中文", "Traditional Chinese", "ZH-HANT", "ZH", "zh-hant"
    ),
    Language("ko", "韓国語", "한국어", "Korean", "KO", "KO", "ko"),
    Language("es", "スペイン語", "Español", "Spanish", "ES", "ES", "latin"),
    Language("fr", "フランス語", "Français", "French", "FR", "FR", "latin"),
    Language("de", "ドイツ語", "Deutsch", "German", "DE", "DE", "latin"),
    Language("it", "イタリア語", "Italiano", "Italian", "IT", "IT", "latin"),
    Language("pt", "ポルトガル語", "Português", "Portuguese", "PT-BR", "PT", "latin"),
    Language("ru", "ロシア語", "Русский", "Russian", "RU", "RU", "latin"),
    Language("uk", "ウクライナ語", "Українська", "Ukrainian", "UK", "UK", "latin"),
    Language("nl", "オランダ語", "Nederlands", "Dutch", "NL", "NL", "latin"),
    Language("pl", "ポーランド語", "Polski", "Polish", "PL", "PL", "latin"),
    Language("tr", "トルコ語", "Türkçe", "Turkish", "TR", "TR", "latin"),
    Language("id", "インドネシア語", "Bahasa Indonesia", "Indonesian", "ID", "ID", "latin"),
    Language("vi", "ベトナム語", "Tiếng Việt", "Vietnamese", None, None, "latin"),
]
AUTO = "auto"
DEFAULT_TARGET = "ja"
DEFAULT_SOURCE = AUTO

_BY_CODE = {language.code: language for language in LANGUAGES}

_KANA = re.compile(r"[぀-ヿ]")
_HAN = re.compile(r"[㐀-鿿豈-﫿]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def language(code: str) -> Language:
    return _BY_CODE.get(code, _BY_CODE[DEFAULT_TARGET])


def is_known(code: str) -> bool:
    return code in _BY_CODE


def already_in_target(text: str, target: str) -> bool:
    """True when ``text`` is clearly written in the target language's script.

    Only scripts that identify a language are used (kana for Japanese, Hangul
    for Korean, Han without kana for Chinese). Latin-script text is always sent,
    since it may be any of several source languages.
    """
    letters = sum(1 for char in text if char.isalpha())
    if not letters:
        return False
    kana = len(_KANA.findall(text))
    han = len(_HAN.findall(text))
    hangul = len(_HANGUL.findall(text))
    if target == "ja":
        return kana > 0 and (kana + han) / letters > 0.5
    if target in ("zh-Hans", "zh-Hant"):
        return kana == 0 and han / letters > 0.6
    if target == "ko":
        return hangul / letters > 0.5
    if target in ("ru", "uk"):
        return False  # Russian and Ukrainian share the script; let the engine decide.
    return False
