"""Standalone extraction subset; original algorithms retained. See SOURCE_MAP.json."""

from __future__ import annotations

from dataclasses import dataclass

@dataclass
class Selection:
    document_id: int
    sha256: str
    path: str                   # local file (resolved from the stored key)
    agency: str
    doc_type: str
    program_id: str | None
    action: str                 # process | skip
    reason: str = ""
    duplicate_of: int | None = None


