"""Read-only validation of inventory contracts, lineage, and live source hashes."""
from collections import Counter
import json
from pathlib import Path

from .ingest import sha256
from .schema import validate_record


def read_records(path):
    return [validate_record(json.loads(line)) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_run(directory):
    directory = Path(directory)
    documents = read_records(directory / "documents.jsonl")
    pages = read_records(directory / "pages.jsonl")
    assets = read_records(directory / "assets.jsonl")
    docs = {(d["document_id"], d["version_id"]): d for d in documents}
    page_map = {p["page_id"]: p for p in pages}
    if len(docs) != len(documents) or len(page_map) != len(pages) or len({a["asset_id"] for a in assets}) != len(assets):
        raise ValueError("Duplicate document/page/asset identity")
    hashes = {}

    def check_hash(path, expected):
        if not expected:
            return
        if path not in hashes:
            hashes[path] = sha256(path)
        if hashes[path] != expected:
            raise ValueError(f"Source checksum changed: {path}")

    for doc in documents:
        check_hash(doc["raw_path"], doc["raw_sha256"])
        check_hash(doc["text_path"], doc["text_sha256"])
        subset = [p for p in pages if (p["document_id"], p["version_id"]) == (doc["document_id"], doc["version_id"])]
        successful = sum(p["detection_status"] != "failed" for p in subset)
        if successful != doc["units_assessed"]:
            raise ValueError("Incorrect assessed page count")
        if doc["status"] == "heuristic_inventory_complete" and len(subset) != doc["units_total"]:
            raise ValueError("Complete inventory has missing pages")
        if doc["kind"] == "pdf" and doc["status"] == "heuristic_inventory_complete":
            if {p["pdf_page"] for p in subset} != set(range(1, doc["units_total"] + 1)):
                raise ValueError("Physical PDF page coverage is not contiguous")
    counts = Counter()
    for asset in assets:
        doc = docs[(asset["document_id"], asset["version_id"])]
        parent = page_map[asset["page_id"]]
        if (parent["document_id"], parent["version_id"], parent["pdf_page"]) != (asset["document_id"], asset["version_id"], asset["pdf_page"]):
            raise ValueError("Asset crosses document/page boundary")
        counts[asset["page_id"]] += 1
        if asset["bbox_pdf"]:
            bounds = parent["page_geometry"]["bounds"]
            box = asset["bbox_pdf"]
            if box[0] < bounds[0] - 0.01 or box[1] < bounds[1] - 0.01 or box[2] > bounds[2] + 0.01 or box[3] > bounds[3] + 0.01:
                raise ValueError("Asset region exceeds physical page")
        for ref in asset["source_refs"]:
            if ref["path"] != doc["text_path"] or ref["sha256"] != doc["text_sha256"]:
                raise ValueError("Asset source is not its registered document version")
            check_hash(ref["path"], ref["sha256"])
            if ref["kind"] == "html_node":
                length = len(Path(ref["path"]).read_bytes().decode("utf-8-sig", errors="replace"))
                if ref["end"] > length:
                    raise ValueError("HTML range extends beyond source")
    if any(counts[p["page_id"]] != p["candidate_count"] for p in pages):
        raise ValueError("Incorrect page candidate count")
    for doc in documents:
        count = sum(a["document_id"] == doc["document_id"] and a["version_id"] == doc["version_id"] for a in assets)
        if count != doc["candidate_count"]:
            raise ValueError("Incorrect document candidate count")
    summary = json.loads((directory / "inventory_summary.json").read_text(encoding="utf-8"))
    if summary["documents"] != len(documents) or summary["candidate_regions"] != len(assets):
        raise ValueError("Summary disagrees with inventory")
    return {"status": "passed", "documents": len(documents), "pages_or_html_files": len(pages),
            "assets": len(assets), "source_files_hash_checked": len(hashes),
            "not_checked": ["detection precision/recall", "table/figure extraction accuracy", "RA semantic correctness"]}
