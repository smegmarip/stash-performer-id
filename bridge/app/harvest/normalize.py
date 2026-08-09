"""The mechanical gate (DESIGN §4).

The *only* automated filtering. We do not classify whether a string is a name; we
normalize it into a candidate and let a human triage validity in the viewer.

Steps:
  1. Unicode-normalize (NFC) — never transliterate (preserve non-Latin scripts).
  2. Split on any non-alphabetic run (letters only; digits/punct/underscore are separators).
  3. Drop tokens shorter than 2 characters.
  4. Join surviving tokens with a single space.

An input that yields no surviving tokens produces no candidate (returns None).
"""

import re
import unicodedata

# A run of Unicode letters: word-chars that are neither digits nor underscore.
# `str` patterns are Unicode-aware by default, so this matches é, Ж, 李, etc.
_LETTER_RUN = re.compile(r"[^\W\d_]+")

MIN_TOKEN_LEN = 2


def tokenize(raw: str) -> list[str]:
    """Maximal runs of Unicode letters, length >= MIN_TOKEN_LEN, from an NFC-normalized string."""
    if not raw:
        return []
    text = unicodedata.normalize("NFC", raw)
    return [t for t in _LETTER_RUN.findall(text) if len(t) >= MIN_TOKEN_LEN]


def candidate(raw: str) -> str | None:
    """Return the gated candidate string for `raw`, or None if nothing survives."""
    tokens = tokenize(raw)
    if not tokens:
        return None
    return " ".join(tokens)
