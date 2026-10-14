"""Standalone extraction subset; original algorithms retained. See SOURCE_MAP.json."""

from __future__ import annotations

import tempfile

import time

from pathlib import Path

from .extract import Extractor

from .qa import document_qa

from .route import route_document

from .sanitize import remove_watermarks

from .select import Selection

from .signals import document_signals

from .structure import structure

def process_pdf(path: str, doc_type: str | None, extractor: Extractor,
                workdir: str | None = None) -> dict:
    """Extraction only (no post-processing). Returns {"routes", "blocks", "engine",
    "timing", "watermarks_removed"}."""
    t0 = time.time()
    tmpdir = None
    if workdir is None:
        tmpdir = tempfile.TemporaryDirectory(prefix="ra-prep-")
        workdir = tmpdir.name
    try:
        clean = str(Path(workdir) / (Path(path).stem + ".clean.pdf"))
        removed = remove_watermarks(path, clean)
        src = clean if removed else path
        routes = route_document(document_signals(src), doc_type)
        res = extractor.extract(src, routes)
        res["routes"] = routes
        res["watermarks_removed"] = removed
        res["timing"]["total"] = time.time() - t0
        return res
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()



def process_document(sel: Selection, extractor: Extractor) -> dict:
    """Extraction + RA post-processing + QA for one selected document."""
    t0 = time.time()
    if sel.path.lower().endswith((".html", ".htm")):
        res = extractor.extract_html(sel.path)
        res["routes"] = [{"page": 1, "route": "HTML", "reason": "html document", "signals": {}}]
        res["watermarks_removed"] = 0
        sizes: dict = {}
    else:
        res = process_pdf(sel.path, sel.doc_type, extractor)
        sizes = {r["page"]: (r["signals"]["width"], r["signals"]["height"]) for r in res["routes"]}
    blocks = structure(res["blocks"], sel.agency, sel.doc_type, sizes)
    return {
        "document_id": sel.document_id, "sha256": sel.sha256, "agency": sel.agency,
        "doc_type": sel.doc_type, "program_id": sel.program_id, "engine": res["engine"],
        "watermarks_removed": res["watermarks_removed"],
        "routes": [{k: r[k] for k in ("page", "route", "reason")} for r in res["routes"]],
        "blocks": blocks,
        "qa": document_qa(blocks, res["routes"]),
        "timing": {**res["timing"], "total": time.time() - t0},
        "_signals": {r["page"]: r["signals"] for r in res["routes"]},
    }


