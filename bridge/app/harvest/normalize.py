"""The mechanical gate (DESIGN §4).

The *only* automated filtering. We do not classify whether a string is a name; we
normalize it into a candidate and let a human triage validity in the viewer.

Steps:
  1. Unicode-normalize (NFC) — never transliterate (preserve non-Latin scripts).
  2. Split into letter runs (digits/punct/underscore are separators), but keep an apostrophe
     that joins two letters so names like O'Dell / D'Angelo / O'Brien stay whole. The curly
     apostrophe (U+2019) is normalized to a straight one.
  3. Drop tokens shorter than 2 characters.
  4. Join surviving tokens with a single space.

An input that yields no surviving tokens produces no candidate (returns None).
"""

import re
import unicodedata

# A run of Unicode letters (word-chars that are neither digits nor underscore — matches é, Ж, 李),
# optionally continued across an apostrophe that sits between letters (O'Dell, D'Angelo).
_LETTER = r"[^\W\d_]+"
_LETTER_RUN = re.compile(rf"{_LETTER}(?:['’]{_LETTER})*")

MIN_TOKEN_LEN = 2


def tokenize(raw: str) -> list[str]:
    """Letter runs (apostrophe-joined kept), length >= MIN_TOKEN_LEN, from an NFC string."""
    if not raw:
        return []
    text = unicodedata.normalize("NFC", raw)
    out: list[str] = []
    for t in _LETTER_RUN.findall(text):
        t = t.replace("’", "'")  # normalize curly apostrophe -> straight
        if len(t) >= MIN_TOKEN_LEN:
            out.append(t)
    return out


def candidate(raw: str) -> str | None:
    """Return the gated candidate string for `raw`, or None if nothing survives."""
    tokens = tokenize(raw)
    if not tokens:
        return None
    return " ".join(tokens)


# A trailing "(qualifier)" — source folders are named "<name> (Abbreviated School)", e.g.
# "Jane Doe (Alabama)"; the qualifier is not part of the name.
_TRAILING_PAREN = re.compile(r"\(([^()]*)\)\s*$")


def split_disambiguation(raw: str) -> tuple[str, str]:
    """Peel a trailing parenthesized qualifier off `raw`: ("Jane Doe ", "Alabama")."""
    m = _TRAILING_PAREN.search(raw or "")
    if not m:
        return raw or "", ""
    return raw[: m.start()], m.group(1).strip()


def candidate_parts(raw: str) -> tuple[str, str] | None:
    """(gated name, disambiguation) for `raw`, or None if no name survives the gate.

    The disambiguation is the trailing parenthesized qualifier, verbatim (trimmed) — it
    flows to `names.disambiguation`, never into the name.
    """
    base, disambiguation = split_disambiguation(raw)
    name = candidate(base)
    return (name, disambiguation) if name else None
