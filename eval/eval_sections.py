#!/usr/bin/env python3
"""Audit materialized source parents and scoped routing, without new model calls."""
import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rag"))
sys.path.insert(0, str(ROOT / "eval"))
import context_units as cu
from attribute_contexts import rule
from build_chunks import Counter, CONFIG
from eval_chunking import verify_sources
from compare_attribution import load_gold, score, DOC, PAGES


def audit(folder, refresh=False):
    chunks = [json.loads(x) for x in (folder / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    build_summary = json.loads((folder / "chunks_summary.json").read_text(encoding="utf-8"))
    if refresh:
        build_summary["context_index"] = cu.write(chunks, folder)
        (folder / "chunks_summary.json").write_text(json.dumps(build_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    units, parents, membership = cu.load(folder)
    issues, checked = [], 0
    for chunk in chunks:
        child = membership[chunk["chunk_id"]]
        parts = []
        for span in child["source_ranges"]:
            u = units[span["unit_id"]]
            parts.append(u["text"][span["start"]:span["end"]])
            checked += 1
            if u["document_id"] != chunk["document_id"] or u["version_id"] != chunk["version_id"] or u["pdf_page"] != chunk["pdf_page"]:
                issues.append({"chunk_id": chunk["chunk_id"], "error": "source_identity_mismatch"})
            if chunk["source_kind"] in ("native", "ocr_page") and u["section_id"] != chunk["structure"]["section_id"]:
                issues.append({"chunk_id": chunk["chunk_id"], "error": "crossed_section_boundary"})
        if "\n".join(parts) != chunk["text"]:
            issues.append({"chunk_id": chunk["chunk_id"], "error": "child_text_mismatch"})
        for cid in child["context_ids"]:
            if chunk["chunk_id"] not in parents[cid]["chunk_ids"]:
                issues.append({"chunk_id": chunk["chunk_id"], "error": "missing_parent_child"})
    counter = Counter(CONFIG["token_counter"], local_only=True)
    windows = list(cu.windows(units, parents, counter, 1800))
    routed = collections.Counter()
    doc_groups = {(c["document_id"], c["version_id"]): c["doc_group"] for c in chunks}
    grouped_routes = collections.defaultdict(collections.Counter)
    for window in windows:
        route = "heading_rule" if window["status"] == "ready" and rule(window) else "llm_required"
        routed[route] += 1
        u = window["units"][0]
        grouped_routes[doc_groups[(u["document_id"], u["version_id"]) ]][route] += 1
    pred = {name: collections.defaultdict(set) for name in ("active_rules", "candidate_rules", "heading_only")}
    active = [json.loads(x) for x in (ROOT / "data/rag/chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    for name, cs in (("active_rules", active), ("candidate_rules", chunks)):
        for c in cs:
            if c["document_id"] == DOC and c["pdf_page"] in PAGES:
                pred[name][c["pdf_page"]].update(c["studies"])
                if name == "candidate_rules":
                    pred["heading_only"][c["pdf_page"]].update(c["structure"]["heading_studies"])
    gold = load_gold()
    report = {"rag_dir": str(folder.resolve()), "source_check": verify_sources(chunks), "context_index": build_summary["context_index"],
        "child_spans_checked": checked, "parent_integrity_issues": issues, "routing_windows": len(windows), "routes": dict(routed),
        "routes_by_document_group": {group: dict(counts) for group, counts in grouped_routes.items()},
        "windows_by_status": dict(collections.Counter(w["status"] for w in windows)),
        "heading_kinds": dict(collections.Counter(p.get("heading_kind") for p in parents.values() if p["kind"] == "section")),
        "CTP43_page_gold": {name: {level: score(pages, gold, level) for level in ("study", "part")} for name, pages in pred.items()},
        "scope_note": "Existing 35 page labels measure page-union study attribution only. Roles, endpoints and analysis correctness are unscored.",
        "boundary_examples": [{"chunk_id": c["chunk_id"], "page": c["pdf_page"], "section_path": c["structure"]["section_path"],
                               "heading_studies": c["structure"]["heading_studies"], "text_start": c["text"][:180]}
                              for c in chunks if c["document_id"] == DOC and c["pdf_page"] in (31, 39) and c["source_kind"] == "native"],
        "html_examples": [{"context_id": p["context_id"], "title": p["title"], "page_from": p["page_from"], "page_to": p["page_to"],
                           "heading_location": p["heading_location"]} for p in parents.values() if p["kind"] == "section" and
                          p["document_id"] == "HC:SBD1721675750511" and p["heading_location"]["pdf_page"] == 13]}
    (folder / "section_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if issues:
        raise ValueError(f"{len(issues)} parent/source integrity issues; see section_audit.json")
    print(json.dumps({k: report[k] for k in ("rag_dir", "context_index", "child_spans_checked", "parent_integrity_issues", "routing_windows", "routes", "windows_by_status", "heading_kinds", "CTP43_page_gold")}, ensure_ascii=False, indent=2), flush=True)
    return build_summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rag-dir", type=Path, nargs="+", required=True)
    p.add_argument("--refresh-contexts", action="store_true")
    p.add_argument("--comparison", type=Path, help="Synchronize context summary after a sidecar-only refresh")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    summaries = {str(folder.resolve()): audit(folder, args.refresh_contexts) for folder in args.rag_dir}
    if args.comparison:
        result = json.loads(args.comparison.read_text(encoding="utf-8"))
        for candidate in result["candidates"]:
            if str(Path(candidate["rag_dir"]).resolve()) in summaries:
                candidate["build_summary"] = summaries[str(Path(candidate["rag_dir"]).resolve())]
        args.comparison.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
