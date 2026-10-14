"""Inventory known document exposure before claiming document transfer.

This is an offline evaluation tool. Absence from saved artifacts is not proof of
being unseen. Conservative format groups are split boundaries, not a claim that
all documents in a group share a template. It never reads raw held-out PDFs,
calls models, edits an index or makes an automatic train/test split.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

VERSION = "document-transfer-audit-1"
SOURCE_TEXT_KEYS = ("text", "native_text", "quote")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def walk(value, trail=()):
    """Yield mappings with structural paths, without flattening their context."""
    if isinstance(value, dict):
        yield trail, value
        for name, child in value.items():
            if isinstance(child, (dict, list)):
                yield from walk(child, (*trail, str(name)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (dict, list)):
                yield from walk(child, (*trail, str(index)))


def source_mentions(value, approved_by_document, approved_by_version):
    """Find locally identified text, not IDs in an index/allowlist manifest.

    A prior version of the same document is still development exposure. Missing
    version IDs are explicitly unresolved, rather than assigned to the latest.
    Saved answer/replay evidence is development context; this does not prove
    that every character was sent in an actual HTTP request.
    """
    found = []
    for trail, node in walk(value):
        text = next((node[k] for k in SOURCE_TEXT_KEYS
                     if isinstance(node.get(k), str) and node[k].strip()), None)
        if text is None:
            continue
        document = node.get("document_id")
        version = node.get("version_id")
        # A bare artifact hash is not a source version.
        targets = set(approved_by_document.get(document, ()))
        targets.update(approved_by_version.get(version, ()))
        if not targets:
            continue
        if "evidence" in trail and "results" in trail:
            kind = "saved_answer_or_replay_context"
        elif "references" in trail or "native_text" in node:
            kind = "reference_or_source_review"
        else:
            kind = "identified_source_context"
        for target in sorted(targets):
            found.append({
                "target_document_id": target,
                "source_document_id": document,
                "source_version_id": version,
                "kind": kind,
                "json_path": "/" + "/".join(trail),
                "pdf_page": node.get("pdf_page"),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            })
    return found


def format_group(document):
    # Product is intentionally excluded: relabelled prescribing information
    # for the same ingredient must not be treated as independent new formats.
    return "|".join(str(document.get(key) or "unknown") for key in
                    ("ingredient", "source", "family"))


def assemble(documents, mentions, protected_versions):
    """Build conservative eligibility flags; never certify an unseen cohort."""
    if any(d["version_id"] in protected_versions for d in documents):
        raise ValueError("protected document version present in approved corpus")
    by_document = defaultdict(list)
    for mention in mentions:
        by_document[mention["target_document_id"]].append(mention)
    exposed_groups = {format_group(d) for d in documents
                      if by_document[d["document_id"]]}
    rows = []
    for document in sorted(documents, key=lambda d: d["document_id"]):
        entries = by_document[document["document_id"]]
        group = format_group(document)
        contexts = {(m["source_version_id"], m["pdf_page"], m["text_sha256"])
                    for m in entries}
        rows.append({
            **{k: document.get(k) for k in ("document_id", "version_id", "product_id",
                                           "source", "family", "title")},
            "conservative_format_group": group,
            "known_development_source_exposure": bool(entries),
            "exposure_kinds": dict(sorted(Counter(m["kind"] for m in entries).items())),
            "unique_recorded_contexts": len(contexts),
            "source_version_unresolved_records": sum(m["source_version_id"] is None for m in entries),
            "prior_version_records": sum(m["source_version_id"] is not None and
                                         m["source_version_id"] != document["version_id"] for m in entries),
            "format_group_has_known_exposure": group in exposed_groups,
            "requires_manual_exposure_review": not bool(entries),
            "certified_unseen_document": False,
            "unseen_document_eval_eligible": False,
        })
    return rows


def run(root, chunks_path, catalog_path, protected_path, artifact_roots, output):
    root = Path(root).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("audit output already exists; choose a new run directory")
    chunks = [json.loads(line) for line in Path(chunks_path).read_text(encoding="utf-8-sig").splitlines()
              if line.strip()]
    identities = {(c["document_id"], c["version_id"]) for c in chunks}
    if len({i[0] for i in identities}) != len(identities):
        raise ValueError("multiple versions of one document require an explicit version policy")
    catalog = [json.loads(line) for line in Path(catalog_path).read_text(encoding="utf-8-sig").splitlines()
               if line.strip()]
    records = {(d["document_id"], d["version_id"]): d for d in catalog}
    documents = [records[i] for i in sorted(identities)]
    protected = set(read_json(protected_path)["protected_excluded"])
    if not protected:
        raise ValueError("protected exclusion inventory is empty")
    if any(c["version_id"] in protected for c in chunks):
        raise ValueError("protected document version present in approved corpus")
    by_document = {d["document_id"]: {d["document_id"]} for d in documents}
    by_version = defaultdict(set)
    for d in documents:
        by_version[d["version_id"]].add(d["document_id"])
    paths = sorted({p.resolve() for directory in artifact_roots
                    for extension in ("*.json", "*.jsonl")
                    for p in Path(directory).rglob(extension)
                    if "code_snapshot" not in p.parts and "__pycache__" not in p.parts
                    and not p.resolve().is_relative_to(output)})
    mentions = []
    inputs = []
    errors = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            raw = path.read_bytes()
            decoded = raw.decode("utf-8-sig")
            values = [json.loads(line) for line in decoded.splitlines() if line.strip()] \
                if path.suffix == ".jsonl" else [json.loads(decoded)]
            inputs.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
            for line_number, value in enumerate(values, 1):
                for mention in source_mentions(value, by_document, by_version):
                    mentions.append({**mention, "artifact": relative,
                                     "jsonl_line": line_number if path.suffix == ".jsonl" else None})
        except (UnicodeError, json.JSONDecodeError) as error:
            errors.append({"path": relative, "error_type": type(error).__name__})
    rows = assemble(documents, mentions, protected)
    known = [r for r in rows if r["known_development_source_exposure"]]
    unknown = [r for r in rows if not r["known_development_source_exposure"]]
    groups = sorted({r["conservative_format_group"] for r in rows})
    summary = {
        "version": VERSION, "created_utc": datetime.now(timezone.utc).isoformat(),
        "approved_documents": len(rows), "products": sorted({r["product_id"] for r in rows}),
        "known_development_exposed_documents": len(known),
        "no_identified_exposure_in_scanned_artifacts": len(unknown),
        "unseen_document_count_is_certified": False,
        "certified_unseen_document_eval_candidates": 0,
        "conservative_format_groups": len(groups),
        "groups_with_known_exposure": len({r["conservative_format_group"] for r in known}),
        "no_identified_exposure_but_group_exposed": sum(r["format_group_has_known_exposure"] for r in unknown),
        "protected_excluded_count": len(protected), "protected_in_approved_chunks": 0,
        "scanned_artifacts": len(paths), "unreadable_artifacts": errors,
        "scan_complete": not bool(errors),
        "inputs": {"chunks_sha256": sha(chunks_path), "catalog_sha256": sha(catalog_path),
                   "protected_inventory_sha256": sha(protected_path),
                   "audit_code_sha256": sha(Path(__file__))},
        "scope_limitations": [
            "Saved identified source contexts only; not a complete history of developer or model exposure.",
            "Raw PDF/OCR/indexing exposure is not audited; original held-out PDFs are not opened.",
            "Saved replay context does not prove a fresh API call or unchanged model payload.",
            "No exposure match is unresolved, never automatically unseen.",
            "Same ingredient/agency/family is a conservative split boundary, not measured template similarity.",
        ],
        "new_http_calls": 0, "index_changed": False, "live_service_switched": False,
    }
    output.mkdir(parents=True)
    for name, value in (("summary.json", summary), ("documents.json", rows), ("artifact_inputs.json", inputs)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "exposure_records.jsonl").write_text(
        "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in mentions), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--protected-inventory", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(Path.cwd(), args.chunks, args.catalog, args.protected_inventory,
                 args.artifacts, args.output)
    print(json.dumps({k: v for k, v in result.items() if k not in ("inputs", "scope_limitations")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
