"""Literal source checks shared by query interpretation and answer validation.

These checks verify provenance, not the meaning or correctness of an interpretation.
"""
import re
import unicodedata


def normalize(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()


def contains_quote(source, quote):
    quote = normalize(quote)
    return bool(quote) and quote in normalize(source)


def contains_value(source, value):
    """Literal value match that cannot take 45 from 145 or 0.45, or 99 from 99.4."""
    source, value = normalize(source), normalize(value)
    if not value:
        return False
    for match in re.finditer(re.escape(value), source):
        before = source[match.start() - 1] if match.start() else ""
        after = source[match.end():match.end() + 2]
        if value[0].isdigit() and before and ((before.isascii() and before.isalnum()) or before == "."):
            continue
        if value[-1].isdigit() and after and (after[0].isdigit() or (after[0] == "." and len(after) > 1 and after[1].isdigit())):
            continue
        return True
    return False
