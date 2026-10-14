"""Offline inventory runner, with source-bound per-page checkpoints."""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stdout
import io
import hashlib
import json
import math
from pathlib import Path
import re
import time
import tempfile

import fitz

from . import VERSION
from .detect import overlap, scan_page
from .html_inventory import inspect_html
from .ingest import Document, asset_identity, sha256, stable_hash
from .schema import JSON_SCHEMA, SCHEMA_VERSION, validate_record

ROUTING_VERSION = "inventory-route-1"


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Windows readers/virus scanners can briefly hold the destination open.
    # A private, closed temp file avoids colliding writers and preserves the
    # previous complete checkpoint until an atomic replacement succeeds.
    payload=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
            prefix=path.name+'.',suffix='.tmp',delete=False) as stream:
        stream.write(payload)
        temporary=Path(stream.name)
    try:
        for attempt in range(8):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt==7:raise
                time.sleep(min(.05*(2**attempt),.5))
    finally:
        temporary.unlink(missing_ok=True)


def write_jsonl(path, rows):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            validate_record(row)
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def legacy_artifacts(doc):
    """Only point to saved outputs; their correctness/configuration is not accepted."""
    if not doc.legacy_dir or not Path(doc.legacy_dir).is_dir():
        return [], [], []
    files = sorted(Path(doc.legacy_dir).glob("*.json"))
    bounds, records, fingerprint, warnings = {}, [], [], []
    for path in files:
        if not re.match(r"p\d+_r\d+(?:\.table)?\.json$", path.name) and path.name != "native_tables.json":
            continue
        body = path.read_bytes()
        fingerprint.append([path.name, hashlib.sha256(body).hexdigest()])
        try:
            item = json.loads(body)
        except (ValueError, UnicodeError):
            warnings.append(f"legacy_json_invalid: {path.name}")
            continue
        if not isinstance(item, dict) or item.get("version_id") != doc.version_id or item.get("document_id", doc.document_id) != doc.document_id:
            continue
        if path.name == "native_tables.json":
            for target in item.get("targets", []):
                if isinstance(target, dict) and "pdf_page" in target and "region" in target:
                    bounds[(target["pdf_page"], target["region"])] = target.get("bbox_pdf")
        elif not path.name.endswith(".table.json"):
            if "pdf_page" in item and "region" in item:
                bounds[(item["pdf_page"], item["region"])] = item.get("bbox_pdf")
        if path.name == "native_tables.json" or "pdf_page" in item and "region" in item:
            records.append((path, item))
    candidates = []
    for path, item in records:
        if path.name == "native_tables.json":
            continue
        key = (item["pdf_page"], item["region"])
        box = item.get("bbox_pdf") or bounds.get(key)
        if not isinstance(box, list) or len(box) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
            box = None
        candidates.append({"path": str(path), "sha256": sha256(path), "pdf_page": key[0],
            "region": key[1], "bbox_pdf": box,
            "artifact_type": "vlm_table" if path.name.endswith(".table.json") else "ocr",
            "stored_status": item.get("status") or item.get("processing_status"),
            "rules_version": item.get("rules_version"),
            "reuse_status": "source_version_matches_recheck_required"})
    return candidates, fingerprint, warnings


def route(candidate):
    if candidate["bbox_status"] == "caption_only":
        return "layout_classify"
    if candidate["asset_type"] == "table":
        if candidate["modality"] == "html":
            return "html_dom_extract"
        if "native_grid_text" in candidate["signals"] and "native_grid_lines" not in candidate["signals"]:
            return "layout_classify"
        has_native_grid = any(s.startswith("native_grid_") for s in candidate["signals"])
        return "native_table_extract" if (candidate["modality"] == "native" or has_native_grid) and "needs_ocr" not in candidate["signals"] else "ocr_table_structure"
    if candidate["asset_type"] == "figure":
        return "figure_labels_and_description"
    return "layout_classify"


def asset_record(doc, page_id, page_number, candidate, text_hash, legacy):
    source = {"kind": "html_node" if doc.kind == "html" else "pdf_region", "path": doc.text_path, "sha256": text_hash}
    if doc.kind == "html":
        source.update(candidate["html_ref"])
    else:
        source.update({"pdf_page": page_number, "bbox_pdf": candidate["bbox_pdf"]})
    previous = []
    if candidate["bbox_pdf"]:
        for item in legacy:
            if item["pdf_page"] == page_number and item["bbox_pdf"] and overlap(candidate["bbox_pdf"], item["bbox_pdf"]) >= 0.8:
                previous.append(item)
    return validate_record({"schema_version": SCHEMA_VERSION, "record_type": "asset",
        "document_id": doc.document_id, "version_id": doc.version_id,
        "asset_id": asset_identity(doc.document_id, doc.version_id, page_id, VERSION, source, candidate["asset_type"], candidate["bbox_status"]), "page_id": page_id,
        "asset_type": candidate["asset_type"], "modality": candidate["modality"],
        "locator_type": "pdf_page" if doc.kind == "pdf" else "html_document", "pdf_page": page_number,
        "bbox_pdf": candidate["bbox_pdf"], "bbox_status": candidate["bbox_status"], "source_refs": [source],
        "detector": "native_geometry_and_captions" if doc.kind == "pdf" else "html_dom",
        "detection_revision": VERSION, "signals": candidate["signals"], "caption_refs": candidate["caption_refs"],
        "structure_hint": candidate["structure_hint"],
        "continuation_status": "hint_only" if any(c.get("continued") for c in candidate["caption_refs"]) else "unassessed",
        "next_step": route(candidate), "legacy_artifact_candidates": previous,
        "extraction_status": "not_run", "validation_status": "unverified_candidate", "numeric_evidence_eligible": False,
        "logical_table_id": None, "crop_path": None, "warnings": candidate["warnings"]})


def page_record(doc, number, result, candidates, text_hash, legacy, label=None):
    page_id = f"{doc.document_id}:{doc.version_id}:" + (f"p{number:04}" if number else "html")
    assets = [asset_record(doc, page_id, number, candidate, text_hash, legacy) for candidate in candidates]
    page = {"schema_version": SCHEMA_VERSION, "record_type": "page", "document_id": doc.document_id,
        "version_id": doc.version_id, "page_id": page_id, "locator_type": "pdf_page" if number else "html_document",
        "pdf_page": number, "pdf_page_label": label, **result, "candidate_count": len(assets),
        "detection_status": "candidate_found" if assets else "no_candidate_by_heuristics",
        "coverage_status": "layout_unassessed" if number else "dom_inventory_only", "needs_layout_check": bool(number)}
    return validate_record(page), assets


def _write_checkpoint(path, page, assets):
    payload = {"page": page, "assets": assets}
    write_json(path, {**payload, "payload_sha256": stable_hash(payload)})


def _read_checkpoint(path, doc, number, text_hash):
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored.get("payload_sha256") != stable_hash({k: stored[k] for k in ("page", "assets")}):
            return None
        validate_record(stored["page"])
        page = stored["page"]
        if (page["document_id"], page["version_id"], page["pdf_page"]) != (doc.document_id, doc.version_id, number):
            return None
        if page["candidate_count"] != len(stored["assets"]):
            return None
        for asset in stored["assets"]:
            validate_record(asset)
            if asset["page_id"] != page["page_id"] or any(ref["sha256"] != text_hash for ref in asset["source_refs"]):
                return None
        return stored
    except (OSError, ValueError, KeyError, TypeError):
        return None


def scan_document(document, output_dir, configuration):
    doc = Document(**document)
    started = time.perf_counter()
    pages, assets, warnings, raw_hash, text_hash, total, reused = [], [], [], None, None, 0, 0
    failed = False
    try:
        raw_hash = sha256(doc.raw_path)
        if doc.expected_sha256 and raw_hash != doc.expected_sha256:
            raise ValueError("Original checksum differs from registered version")
        if not Path(doc.text_path).is_file():
            warnings.append("derived_text_pdf_missing_using_original")
            doc.text_path = doc.raw_path
        text_hash = raw_hash if doc.text_path == doc.raw_path else sha256(doc.text_path)
        try:
            legacy, legacy_fingerprint, legacy_warnings = legacy_artifacts(doc)
        except (OSError, ValueError, KeyError, TypeError) as error:
            legacy, legacy_fingerprint, legacy_warnings = [], [], [f"legacy_artifacts_unavailable: {error}"]
        warnings.extend(legacy_warnings)
        fingerprint = stable_hash({"document": doc.to_dict(), "raw_hash": raw_hash, "text_hash": text_hash,
                                   "legacy": legacy_fingerprint, "configuration": configuration})
        checkpoint_dir = Path(output_dir) / "checkpoints" / fingerprint
        if doc.kind == "html":
            total = 1
            cache_path = checkpoint_dir / "html.json"
            cached = _read_checkpoint(cache_path, doc, None, text_hash)
            if cached:
                pages.append(cached["page"]); assets.extend(cached["assets"]); reused += 1
            else:
                text = Path(doc.raw_path).read_bytes().decode("utf-8-sig", errors="replace")
                candidates = inspect_html(text)
                result = {"page_geometry": None, "modality": "html", "text_chars": len(text), "needs_ocr": False,
                    "text_quality": {"offset_basis": "decoded_unicode_html", "encoding": "utf-8-sig"},
                    "signals": {"table_nodes": sum(c["asset_type"] == "table" for c in candidates),
                                "image_nodes": sum(c["asset_type"] == "unknown_visual" for c in candidates)},
                    "warnings": ["html_encoding_replacement"] if "\ufffd" in text else []}
                page, found = page_record(doc, None, result, candidates, text_hash, legacy)
                pages.append(page); assets.extend(found)
                _write_checkpoint(cache_path, page, found)
        else:
            with fitz.open(doc.text_path) as pdf:
                if pdf.needs_pass:
                    raise ValueError("Password-protected PDF is not supported")
                total = len(pdf)
                for index in range(total):
                    cache_path = checkpoint_dir / f"p{index + 1:05}.json"
                    cached = _read_checkpoint(cache_path, doc, index + 1, text_hash)
                    if cached:
                        pages.append(cached["page"]); assets.extend(cached["assets"]); reused += 1
                        continue
                    try:
                        with redirect_stdout(io.StringIO()):
                            result, candidates = scan_page(pdf[index], configuration["table_strategies"])
                        page, found = page_record(doc, index + 1, result, candidates, text_hash, legacy, pdf[index].get_label() or str(index + 1))
                    except Exception as error:
                        result = {"page_geometry": None, "modality": "unassessed", "text_chars": 0, "needs_ocr": False,
                            "text_quality": {}, "signals": {}, "warnings": [f"page_failed: {type(error).__name__}: {error}"]}
                        page, found = page_record(doc, index + 1, result, [], text_hash, legacy)
                        page.update(detection_status="failed", coverage_status="failed")
                        # Failed pages are retried, rather than checkpointed as complete.
                    pages.append(page); assets.extend(found)
                    if page["detection_status"] != "failed":
                        _write_checkpoint(cache_path, page, found)
                    if (index + 1) % 100 == 0:
                        print(f"{doc.document_id}: {index + 1}/{total} pages", flush=True)
        if sha256(doc.raw_path) != raw_hash or sha256(doc.text_path) != text_hash:
            pages.clear(); assets.clear()
            raise ValueError("Source changed during inventory")
    except Exception as error:
        failed = True
        warnings.append(f"document_failed: {type(error).__name__}: {error}")
    failures = sum(p["detection_status"] == "failed" for p in pages)
    kinds = Counter(a["asset_type"] for a in assets)
    complete = not failed and not failures and len(pages) == total
    # Absence of heuristically detected assets is NOT evidence of absence.
    present = lambda kind: True if kinds[kind] else None
    document_result = validate_record({"schema_version": SCHEMA_VERSION, "record_type": "document",
        "document_id": doc.document_id, "version_id": doc.version_id, "kind": doc.kind,
        "raw_path": doc.raw_path, "text_path": doc.text_path, "raw_sha256": raw_hash, "text_sha256": text_hash,
        "source_paths": doc.source_paths, "metadata": doc.metadata,
        "status": "heuristic_inventory_complete" if complete else "partial" if pages else "failed",
        "coverage_status": ("layout_unassessed" if doc.kind == "pdf" else "dom_inventory_only") if complete else "partial" if pages else "failed",
        "units_total": total, "units_assessed": len(pages) - failures, "candidate_count": len(assets),
        "has_table_candidate": present("table"), "has_figure_candidate": present("figure"),
        "has_unknown_visual": present("unknown_visual"),
        "needs_ocr": True if any(p["needs_ocr"] for p in pages) else False if complete else None,
        "needs_layout_check": doc.kind == "pdf", "warnings": warnings})
    return {"document": document_result, "pages": pages, "assets": assets, "reused": reused,
            "seconds": round(time.perf_counter() - started, 3)}


def run_inventory(documents, out, strategies=("lines", "text"), workers=1):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    configuration = {"inventory_version": VERSION, "schema_version": SCHEMA_VERSION,
                     "routing_version": ROUTING_VERSION,
                     "pymupdf_version": fitz.VersionBind, "table_strategies": list(strategies),
                     "text_layer_version": __import__("collection.text_layer", fromlist=["VERSION"]).VERSION}
    write_json(out / "manifest.json", {"configuration": configuration, "documents": [d.to_dict() for d in documents],
                                      "workers": workers, "status": "running"})
    started = time.perf_counter()
    results = []
    if workers == 1:
        for doc in documents:
            result = scan_document(doc.to_dict(), str(out), configuration)
            results.append(result)
            print(f"{doc.document_id}: {result['document']['status']}, {len(result['pages'])} units, {len(result['assets'])} candidates", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(scan_document, d.to_dict(), str(out), configuration): d.document_id for d in documents}
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print(f"{futures[future]}: {result['document']['status']}, {len(result['pages'])} units, {len(result['assets'])} candidates", flush=True)
    order = {(d.document_id, d.version_id): i for i, d in enumerate(documents)}
    results.sort(key=lambda r: order[(r["document"]["document_id"], r["document"]["version_id"])])
    document_rows = [r["document"] for r in results]
    pages = [p for r in results for p in r["pages"]]
    assets = [a for r in results for a in r["assets"]]
    page_lookup = {p["page_id"]: p for p in pages}
    if len(page_lookup) != len(pages) or len({a["asset_id"] for a in assets}) != len(assets):
        raise ValueError("Duplicate page or asset identity")
    for asset in assets:
        parent = page_lookup[asset["page_id"]]
        if (asset["document_id"], asset["version_id"]) != (parent["document_id"], parent["version_id"]):
            raise ValueError("Asset crosses document/version boundary")
    for name, rows in (("documents", document_rows), ("pages", pages), ("assets", assets)):
        write_jsonl(out / f"{name}.jsonl", rows)
    review = [{"document_id": d["document_id"], "version_id": d["version_id"], "reason": "document_incomplete", "warnings": d["warnings"]}
              for d in document_rows if d["status"] != "heuristic_inventory_complete"]
    review.extend({"document_id": a["document_id"], "asset_id": a["asset_id"], "reason": "candidate_classification_pending"}
                  for a in assets if a["next_step"] == "layout_classify")
    with (out / "review_queue.jsonl").open("w", encoding="utf-8") as stream:
        for row in review:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {"configuration": configuration, "documents": len(document_rows),
        "document_statuses": dict(Counter(d["status"] for d in document_rows)),
        "pdf_pages": sum(p["locator_type"] == "pdf_page" for p in pages),
        "html_documents_assessed": sum(p["locator_type"] == "html_document" for p in pages),
        "failed_pages": sum(p["detection_status"] == "failed" for p in pages),
        "pdf_pages_needing_ocr": sum(p["needs_ocr"] and p["locator_type"] == "pdf_page" for p in pages),
        "candidate_regions": len(assets), "candidates_by_type": dict(Counter(a["asset_type"] for a in assets)),
        "candidates_by_modality": dict(Counter(a["modality"] for a in assets)),
        "next_steps": dict(Counter(a["next_step"] for a in assets)),
        "assets_with_legacy_candidates": sum(bool(a["legacy_artifact_candidates"]) for a in assets),
        "distinct_legacy_artifact_candidates": len({c["path"] for a in assets for c in a["legacy_artifact_candidates"]}),
        "checkpoint_units_reused": sum(r["reused"] for r in results),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "vlm_calls": 0, "ocr_calls": 0, "layout_model_calls": 0,
        "scope_notice": "Heuristic candidates only; not unique logical tables, validated extraction, or exhaustive visual coverage."}
    write_json(out / "inventory_summary.json", summary)
    write_json(out / "inventory.schema.json", JSON_SCHEMA)
    report = ["# RA 문서 전처리 인벤토리", "", "이 결과는 휴리스틱 후보 조사다. 표·그림 총수나 정확도 측정 결과가 아니다.",
              "모든 PDF 페이지의 모델 기반 레이아웃 검사와 OCR·VLM 추출은 아직 수행하지 않았다.", "",
              f"- 문서: {len(document_rows)}, PDF 페이지: {summary['pdf_pages']}, HTML 문서: {summary['html_documents_assessed']}",
              f"- 후보 영역: {len(assets)}, OCR 필요 PDF 페이지: {summary['pdf_pages_needing_ocr']}",
              f"- 재사용 checkpoint: {summary['checkpoint_units_reused']}, VLM 호출: 0", "",
              "| 문서 | 단위 조사 | 표 후보 | 그림 후보 | 불확실 영역 | OCR 필요 | 상태 |", "|---|---:|---|---|---|---|---|"]
    mark = lambda value: "있음" if value is True else "없음" if value is False else "미확정"
    for d in document_rows:
        report.append(f"| {d['document_id'].replace('|', '/')} | {d['units_assessed']}/{d['units_total']} | {mark(d['has_table_candidate'])} | {mark(d['has_figure_candidate'])} | {mark(d['has_unknown_visual'])} | {mark(d['needs_ocr'])} | {d['status']} |")
    report += ["", "HTML 조사 단위는 저장된 파일 전체다. 기존 HTML 섹션 번호를 PDF 페이지로 바꾸지 않았다.",
               "기존 OCR/VLM 파일 연결은 버전·영역 일치 후보이며 새 설정에서 재사용 승인을 의미하지 않는다.",
               "청킹·인덱스는 변경하지 않았다. 수치 근거로 사용 가능한 자산은 이 단계에서 0개다."]
    (out / "inventory_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    write_json(out / "manifest.json", {"configuration": configuration, "documents": [d.to_dict() for d in documents],
        "workers": workers, "status": "inventory_complete", "summary_path": str(out / "inventory_summary.json")})
    return summary
