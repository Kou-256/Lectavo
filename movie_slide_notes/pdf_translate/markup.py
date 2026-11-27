"""Subscripts and superscripts inside block text.

They are written as ``<sub>…</sub>`` and ``<sup>…</sup>`` (``Eu<sub>2</sub>O<sub>3</sub>``,
``Eu<sup>3+</sup>``, ``process.<sup>22)</sup>``), kept intact through translation,
and drawn small and raised or lowered by the writer.
"""

from __future__ import annotations

import re

SCRIPT = re.compile(r"<(sub|sup)>(.*?)</\1>", re.DOTALL)
_TAG = re.compile(r"</?(?:sub|sup)>")
_TAGGED = r"(?:<sub>[^<]*</sub>|<sup>[^<]*</sup>)"
# A word made of scripted parts: "Eu<sub>x</sub>Y<sub>2−x</sub>O<sub>3</sub>",
# "y<sub>N</sub>", "10<sup>−3</sup>", or a bare citation mark "<sup>22,23)</sup>".
# The parts around the tags are element symbols, single letters (variables),
# numbers and brackets -- never an ordinary word, which still has to be translated.
_PART = r"(?:[A-Z][a-z]?|[^\W\d_](?![^\W\d_])|\d+(?:\.\d+)?|[()\[\]+\-−′'])"
SCRIPTED_WORD = re.compile(
    rf"(?<![\w⟦]){_PART}{{0,10}}{_TAGGED}(?:{_PART}{{0,6}}{_TAGGED})*[\d)′]{{0,2}}"
    rf"|{_TAGGED}+"
)


def has_scripts(text: str) -> bool:
    return "<su" in text and bool(SCRIPT.search(text))


def strip_tags(text: str) -> str:
    """The text as plain characters, for checks such as language detection."""
    return _TAG.sub("", text)


def to_xml(text: str) -> str:
    """Escape everything but the script tags, for translators that handle XML."""
    parts = []
    last = 0
    for match in SCRIPT.finditer(text):
        parts.append(_escape(text[last : match.start()]))
        parts.append(f"<{match.group(1)}>{_escape(match.group(2))}</{match.group(1)}>")
        last = match.end()
    parts.append(_escape(text[last:]))
    return "".join(parts)


def from_xml(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
