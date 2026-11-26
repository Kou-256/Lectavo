"""Keep ions and chemical formulas out of the translator's hands.

Small models in particular read "Eu3+" as "EU" (Europe) or turn "TiO2" into
words. These tokens are swapped for markers like ⟦0⟧ before translating and
put back afterwards. Words with sub- or superscripts (``Eu<sub>2</sub>O<sub>3</sub>``,
citation marks ``<sup>35)</sup>``) are kept the same way, so their tags survive.
"""

from __future__ import annotations

import re

from movie_slide_notes.pdf_translate.markup import SCRIPTED_WORD

_ELEMENT = r"[A-Z][a-z]?"
# Ions need a charge sign (Eu3+, O2-, Ce³⁺); formulas need at least one count (TiO2, Y3Al5O12).
_ION = rf"(?:{_ELEMENT}\d*){{1,6}}(?:\d*|[²³¹⁰⁴-⁹]*)(?:[+\-−]|[⁺⁻])"
_FORMULA = rf"(?:{_ELEMENT}\d*)*{_ELEMENT}\d+(?:{_ELEMENT}\d*)*"
_TOKEN = re.compile(rf"(?<![\w⟦])(?:{_ION}|{_FORMULA})(?![\w⟧])")
_MARKER = re.compile(r"⟦(\d+)⟧")


def protect(text: str) -> tuple[str, list[str]]:
    tokens: list[str] = []

    def replace(match: re.Match) -> str:
        tokens.append(match.group(0))
        return f"⟦{len(tokens) - 1}⟧"

    text = SCRIPTED_WORD.sub(replace, text)
    return _TOKEN.sub(replace, text), tokens


def restore(text: str, tokens: list[str]) -> str:
    if not tokens:
        return text
    used: set[int] = set()

    def replace(match: re.Match) -> str:
        index = int(match.group(1))
        if index < len(tokens):
            used.add(index)
            return tokens[index]
        return ""

    restored = _MARKER.sub(replace, text)
    missing = [tokens[i] for i in range(len(tokens)) if i not in used]
    if missing:
        # The translator dropped a marker; keep the term rather than lose it.
        restored = restored.rstrip() + " (" + ", ".join(dict.fromkeys(missing)) + ")"
    return restored
