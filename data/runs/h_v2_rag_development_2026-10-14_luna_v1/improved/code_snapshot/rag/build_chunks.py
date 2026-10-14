#!/usr/bin/env python3
"""Build retrieval chunks from parsed pages and OCR regions.

Every page of every in-scope document is chunked; nothing is dropped as a duplicate here.
Identical content is merged at search time through content_hash (see docs/temp/rag/RAG_POC_PLAN.md 2.5).
"""
import argparse
import collections
import csv
import hashlib
import json
import re
import sqlite3
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study_context  # noqa: E402
import study_registry as sr  # noqa: E402
import document_structure as ds  # noqa: E402
from chunking import pack_units  # noqa: E402
from chunk_schema import SCHEMA, profile_id, validate_dataset  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "rag"
CONFIG = {
    "config_version": "chunks-7-sections",
    "schema_version": "rag-chunk-2",
    "structure_version": ds.COMPAT_VERSION,
    "embedding_model": "bge-m3",
    "include_study_in_header": False,
    "token_counter": "BAAI/bge-m3",
    # Retrieval budgets are distinct from BGE-M3's model context capacity.
    "embedding_max_tokens": 8192,
    "target_tokens": 768,
    "max_tokens": 1024,
    "table_max_tokens": 2048,
    "overlap_tokens": 96,
    "min_tokens": 1,
    "min_ocr_chars": 20,
    "boilerplate_page_share": 0.5,
    "boilerplate_min_pages": 6,
    # A page with this many heading-like lines is a table of contents; its lines are not used as sections.
    "toc_min_headings": 6,
    # A heading stops being attached to chunks this many pages after it was last seen (limits stale labels).
    "section_max_pages": 8,
}

# Only multi-level numbers ("2.6.2. Clinical pharmacology"): single-level "1. ..." lines are mostly numbered list items.
HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,4})\.?\s+([A-Z].{2,110})$")
CAPTION = re.compile(r"^\s*(Table|Figure)\s+\d+", re.I)
REDACTION = re.compile(r"\(b\)\s*\(\d\)|\bCCI\b")
PIPES = re.compile(r"\s*\|\s*")
# Symbol-font glyphs come through as private-use characters; most are bullets.
CHAR_MAP = {0xF0B7: chr(0x2022), 0x02F9: chr(0x00B7), 0x02C2: "<", 0x02C3: ">", 0x00A0: " ", 0x00AD: ""}
PRIVATE_USE = re.compile("[" + chr(0xE000) + "-" + chr(0xF8FF) + chr(0xFFFD) + "]")
NL = "\n"


def clean_line(line):
    line = line.translate(CHAR_MAP)
    line = PRIVATE_USE.sub("", line)
    return re.sub(r"[ \t]+", " ", line).strip()


def mask_digits(line):
    return re.sub(r"\d+", "#", line)


def norm_for_hash(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def md5(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def doc_date(doc):
    m = doc.get("source_metadata") or {}
    value = (m.get("application_doc") or {}).get("ApplicationDocsDate") or m.get("last_updated_date") or m.get("first_published_date") or m.get("date")
    return value[:10] if value else None


class Counter:
    def __init__(self, name, local_only=False):
        from transformers import AutoTokenizer, logging

        logging.set_verbosity_error()  # long inputs are only counted here, never fed to a model
        self.tok = AutoTokenizer.from_pretrained(name, local_files_only=local_only)

    def many(self, texts):
        if not texts:
            return []
        return [self.one(x) for x in texts]

    @lru_cache(maxsize=32768)
    def one(self, text):
        return len(self.tok(text, add_special_tokens=False, truncation=False)["input_ids"])

    def embedding(self, header, text):
        return len(self.tok(header + NL + text, add_special_tokens=True, truncation=False)["input_ids"])


def split_long(text, limit, counter):
    """Compatibility helper; packing now preserves offsets and checks exact budgets."""
    from chunking import Fragment, split_fragment
    return [f.text for f in split_fragment(Fragment(text, 0), limit, counter)]


def pack(units, counter, max_tokens, breaks=()):
    """Compatibility interface for callers that do not need source spans."""
    mapped_breaks = {i for i, u in enumerate(units) if u[2] in breaks}
    chunks = pack_units([u[0] for u in units], counter, max_tokens,
                        min(CONFIG["target_tokens"], max_tokens), CONFIG["overlap_tokens"], mapped_breaks)
    return [(c.text, counter.one(c.text), units[c.fragments[0].uid][2]) for c in chunks]


def margin_lines(page):
    """Only the first/last two nonempty lines can be running headers/footers.

    Recurrence elsewhere (notably column headings) is not sufficient to drop a line.
    """
    lines = [(bi, li, clean_line(line)) for bi, b in enumerate(page["blocks"])
             for li, line in enumerate(b["text"].split(NL)) if clean_line(line)]
    return {(bi, li): text for bi, li, text in lines[:2] + lines[-2:] if len(text) <= 160}


def boilerplate_lines(pages):
    """Recurring short margin lines only; preserve repeated body/table content."""
    with_text = [p for p in pages if p["blocks"]]
    if len(with_text) < CONFIG["boilerplate_min_pages"]:
        return set()
    seen = collections.Counter()
    for p in with_text:
        lines = {mask_digits(x) for x in margin_lines(p).values()}
        seen.update(x for x in lines if len(x) >= 4)
    return {x for x, n in seen.items() if n >= CONFIG["boilerplate_page_share"] * len(with_text)}


def scan_sections(units, section, strip_pipes=False, product=None):
    """Find headings among the units.

    Returns (unit indices where a new chunk may start, section in force at each unit,
    section after the last unit, whether a heading was found).
    """
    found = []
    for i, u in enumerate(units):
        first = u.split(NL)[0]
        if strip_pipes:
            first = PIPES.sub(" ", first)
        m = HEADING.match(first)
        # A study number broken over two lines ("... Study CT-P43" / "1.1 Part 2, ...") is not a heading.
        continues_id = False
        if m and i > 0:
            prev = units[i - 1].split(NL)[-1]
            joined = prev + NL + first
            continues_id = any(s < len(prev) and e > len(prev) for pid in ([product] if product else sr.products())
                               for s, e, _ in sr.finditer(joined, pid))
        # Dotted leaders mark a table-of-contents line, not a heading.
        if m and not continues_id and ".." not in first and chr(0x2026) not in first and NL not in u.strip():
            found.append((i, f"{m.group(1)} {m.group(2)}"[:100]))
    if len(found) >= CONFIG["toc_min_headings"]:
        found = []
    headings = dict(found)
    breaks, section_at = set(), []
    for i, u in enumerate(units):
        if i in headings:
            section = headings[i]; breaks.add(i)
        elif CAPTION.match(u.split(NL)[0]):
            breaks.add(i)
        section_at.append(section)
    return breaks, section_at, section, bool(headings)


def caption_for(region_bbox, blocks):
    """Nearest 'Table N' / 'Figure N' block above the image region (or just below it, for figures)."""
    x0, y0, x1, y1 = region_bbox
    best = None
    for b in blocks:
        lines = b["text"].split(NL)
        if not CAPTION.match(clean_line(" ".join(lines[:3]))):
            continue
        bx0, by0, bx1, by1 = b["bbox"]
        above = by1 <= y0 + 5
        gap = y0 - by1 if above else (by0 - y1 if by0 >= y1 - 5 else None)
        if gap is not None and gap <= (120 if above else 60) and (best is None or gap < best[0]):
            best = (gap, " ".join(x for x in (clean_line(y) for y in lines) if x))
    return best[1] if best else None


def source_units(page, boiler, structural=True):
    """Clean blocks, retain source locators, and expose headings inside a block."""
    texts, refs = [], []
    margins = margin_lines(page)
    blocks = page["blocks"] or [{"text": page["text"]}]
    for bi, b in enumerate(blocks):
        pending = []

        def flush():
            if pending:
                texts.append(NL.join(t for _, t in pending))
                refs.append({"block_id": b.get("block_id"), "bbox_pdf": b.get("bbox"),
                             "line_start": pending[0][0], "line_end": pending[-1][0], "joiner": NL})
                pending.clear()

        for li, line in enumerate(b["text"].split(NL)):
            text = clean_line(line)
            if not text:
                flush()
                continue
            if (bi, li) in margins and mask_digits(text) in boiler:
                # A removed line may occur inside a block. Keep each recorded
                # line range contiguous so it can reconstruct the exact unit.
                flush()
                continue
            if HEADING.match(text) or CAPTION.match(text) or (structural and ds.identify(text)):
                flush()
                pending.append((li, text))
                flush()
            else:
                pending.append((li, text))
        flush()
    return texts, refs


def line_units(text, bbox=None):
    pairs = [(i, clean_line(t)) for i, t in enumerate(text.split(NL)) if clean_line(t)]
    return ([t for _, t in pairs], [{"block_id": None, "bbox_pdf": bbox, "line_start": i, "line_end": i, "joiner": NL} for i, _ in pairs])


def section_paths(labels, path):
    paths = []
    for label in labels:
        if label is None:
            path = []
        elif not path or path[-1] != label:
            match = HEADING.match(label)
            if match:
                code = match.group(1)
                path = [s for s in path if HEADING.match(s) and code.startswith(HEADING.match(s).group(1) + ".")]
                path.append(label)
            else:
                path = [label]
        paths.append(list(path))
    return paths, path


def build_document(doc, groups, counter, db, cfg, stats):
    doc_group = groups["doc_group"][doc["source"]][doc["document_type"]]
    parsed = (ROOT / doc["parsed_path"]).read_bytes()
    pages = [json.loads(x) for x in parsed.decode("utf-8").splitlines() if x.strip()]
    boiler = boilerplate_lines(pages)
    tracker = ds.Tracker(doc["document_id"], doc["version_id"], doc["product_id"])
    html_headings = {}
    if cfg.get("structure_version") and any(p.get("locator_type") == "html_section" for p in pages) and doc.get("raw_path"):
        html_headings = ds.html_heading_map((ROOT / doc["raw_path"]).read_text(encoding="utf-8", errors="replace"), pages, clean_line)
    base = {
        "document_id": doc["document_id"], "version_id": doc["version_id"], "product_id": doc["product_id"],
        "source_url": doc.get("final_url") or doc.get("url"),
        "source": doc["source"], "role": doc["role"], "document_type": doc["document_type"], "doc_group": doc_group,
        "jurisdiction": groups["jurisdiction"][doc["source"]], "language": doc.get("language") or "en",
        "doc_date": doc_date(doc), "title": doc["title"], "schema_version": cfg["schema_version"],
    }
    head = f"{groups['product_label'][doc['product_id']]} | {doc['source']} | {doc['title']}"
    pid = profile_id(cfg)
    ocr_by_page = collections.defaultdict(list)
    for r in db.execute("select pdf_page, region, mode, text, processing_status, metadata_json from ocr_regions where document_id=? and version_id=? order by pdf_page, region", (doc["document_id"], doc["version_id"])):
        ocr_by_page[r["pdf_page"]].append(dict(r))
    chunks, section, section_page, path = [], None, 0, []
    stats["documents"] += 1
    for page in pages:
        page_no = page["pdf_page"]
        stats["pages"] += 1
        is_html = page.get("locator_type") == "html_section"
        locator = f"section {page_no}" if is_html else f"p.{page_no}"
        if not cfg.get("structure_version") and section and page_no - section_page > cfg["section_max_pages"]:
            section, path = None, []
        if is_html:
            section = page.get("pdf_page_label") or None
            raw_units, refs = line_units(page["text"])
        else:
            raw_units, refs = source_units(page, boiler, structural=bool(cfg.get("structure_version")))
        page_section, page_path = section, list(path)
        if cfg.get("structure_version"):
            explicit = {line: heading for (p, line), heading in html_headings.items() if p == page_no}
            page_state = tracker.state()
            breaks, section_at, paths_at, states_at = tracker.scan(raw_units, refs, page_no, explicit=explicit)
            state = tracker.state()
            section, path, seen = " > ".join(state["path"]) or None, state["path"], bool(breaks)
            page_section, page_path = " > ".join(page_state["path"]) or None, page_state["path"]
        else:
            breaks, section_at, section, seen = scan_sections(raw_units, section, product=doc["product_id"])
            paths_at, path = section_paths(section_at, path)
            states_at = [None] * len(raw_units)
            page_state = None
        if seen or is_html:
            section_page = page_no
        page_hash = md5(norm_for_hash(NL.join(raw_units)))

        def emit_parts(texts, source_refs, labels, paths, kind, limit, split_at=(), r=None, cap=None,
                       prefix="", prefix_spans=(), content_kind="text", metadata=None, unit_index_base=0, states=None):
            metadata = metadata or {}
            # Reserve model space using the actual longest header on this page/region.
            headers = [" | ".join(x for x in (head, sec, locator) if x) for sec in labels] or [head + " | " + locator]
            safe_limit = min(limit, cfg["embedding_max_tokens"] - max(counter.embedding(h, "") for h in headers) - 8)
            # A table/figure fitting its budget stays whole, irrespective of the prose target.
            packed = pack_units(texts, counter, safe_limit,
                                safe_limit if content_kind != "text" else min(cfg["target_tokens"], safe_limit),
                                0 if content_kind != "text" else cfg["overlap_tokens"], split_at, prefix=prefix)
            for i, part in enumerate(packed):
                uid = part.fragments[0].uid
                text = NL.join(x for x in (prefix, part.text) if x)
                sec = labels[uid]
                code = {"native": "n", "ocr_region": "r", "ocr_page": "o"}[kind]
                index = str(i) if r is None else f"{r['region']}_{i}"
                header = " | ".join(x for x in (head, sec, locator) if x)
                spans = list(prefix_spans) + [{"unit_index": f.uid + unit_index_base, "normalized_start": f.start,
                                             "normalized_end": f.end, **source_refs[f.uid]} for f in part.fragments]
                warnings = []
                if any(f.hard_split for f in part.fragments):
                    warnings.append("forced_character_split")
                table_status = (metadata.get("table") or {}).get("status")
                if table_status:
                    warnings.append("table_structure_unverified")
                if r and "disagree" in (r["processing_status"] or ""):
                    warnings.append("ocr_numeric_disagreement")
                if counter.one(text) < 8:
                    warnings.append("short_structural_chunk")
                region = r["region"] if r else None
                parent = f"{doc['document_id']}:{doc['version_id']}:{page_no:04d}"
                table_id = f"{parent}:r{region}" if content_kind == "table_candidate" else None
                scope = (states[uid] if states else None) or {"section_id": None, "ancestor_section_ids": [],
                                                             "heading_studies": [], "heading_anchors": []}
                chunks.append({
                    **base, "chunk_id": f"{doc['document_id']}:{doc['version_id']}:{pid}:{page_no:04d}:{code}{index}",
                    "pdf_page": page_no, "locator_type": page.get("locator_type") or "pdf_page",
                    "pdf_page_label": page.get("pdf_page_label"), "section": sec, "source_kind": kind, "region": region,
                    "ocr_status": r["processing_status"] if r else None,
                    "n_tokens": counter.one(text), "n_tokens_with_header": counter.embedding(header, text),
                    "redaction_markers": len(REDACTION.findall(text)),
                    "content_hash": md5(norm_for_hash(text)), "page_hash": page_hash, "header": header, "text": text,
                    "provenance": {
                        "parsed_path": doc["parsed_path"], "parsed_sha256": hashlib.sha256(parsed).hexdigest(),
                        "parser": page.get("parser"), "parser_version": page.get("parser_version"),
                        "offset_basis": "cleaned_source_unit", "source_spans": spans,
                        "ocr_image_path": metadata.get("image_path"),
                        "ocr_text_sha256": hashlib.sha256((r["text"] or "").encode("utf-8")).hexdigest() if r else None,
                        "ocr_engine": metadata.get("text_engine"), "ocr_config_version": (metadata.get("config") or {}).get("config_version"),
                    },
                    "structure": {"kind": content_kind, "section_path": paths[uid], "parent_id": parent,
                                  **{k: scope[k] for k in ("section_id", "ancestor_section_ids", "heading_studies", "heading_anchors")},
                                  "previous_chunk_id": None, "next_chunk_id": None, "caption": cap,
                                  "table_id": table_id, "part_index": i, "part_count": len(packed),
                                  "repeated_context": bool(prefix and i > 0)},
                    "quality": {"evidence_status": "unreviewed", "table_status": table_status, "warnings": warnings},
                    "analysis_context": {"status": "unresolved", **{k: None for k in ("analysis_id", "population", "comparison", "endpoint", "timepoint")}},
                    "chunking": {"config_version": cfg["config_version"], "profile_id": pid, "tokenizer": cfg["token_counter"],
                                 "body_limit": safe_limit, "embedding_limit": cfg["embedding_max_tokens"],
                                 "split_reason": part.reason, "overlap_tokens": part.overlap_tokens},
                })
                stats[f"chunks_{kind}"] += 1

        if raw_units:
            emit_parts(raw_units, refs, section_at, paths_at, "native", cfg["max_tokens"], breaks, states=states_at)
        else:
            stats["pages_without_text"] += 1
        for r in ocr_by_page.get(page_no, []):
            metadata = json.loads(r["metadata_json"] or "{}") or {}
            bbox = metadata.get("bbox_pdf")
            lines, ocr_refs = line_units(r["text"] or "", bbox)
            if len(NL.join(lines)) < cfg["min_ocr_chars"]:
                stats["skipped_ocr_small"] += 1
                continue
            if r["mode"] != "region":
                if cfg.get("structure_version"):
                    ocr_breaks, sec_at, ocr_paths, ocr_states = tracker.scan(lines, ocr_refs, page_no, "ocr_page", r["region"])
                    state = tracker.state()
                    section, path, seen = " > ".join(state["path"]) or None, state["path"], bool(ocr_breaks)
                else:
                    ocr_breaks, sec_at, section, seen = scan_sections(lines, section, strip_pipes=True, product=doc["product_id"])
                    ocr_paths, path = section_paths(sec_at, path)
                    ocr_states = None
                if seen:
                    section_page = page_no
                emit_parts(lines, ocr_refs, sec_at, ocr_paths, "ocr_page", cfg["max_tokens"], ocr_breaks, r=r, metadata=metadata, states=ocr_states)
                continue
            cap = caption_for(bbox, page["blocks"]) if bbox else None
            stats["ocr_regions_with_caption"] += bool(cap)
            sec, region_path = page_section, page_path
            region_state = page_state
            if bbox:
                preceding = [j for j, ref in enumerate(refs) if ref["bbox_pdf"] and ref["bbox_pdf"][1] <= bbox[1]]
                if preceding:
                    sec, region_path = section_at[preceding[-1]], paths_at[preceding[-1]]
                    region_state = states_at[preceding[-1]]
            if not region_state and is_html:
                region_state = tracker.state()
            content_kind = "figure_candidate" if cap and cap.lower().startswith("figure") else "table_candidate" if metadata.get("table") or (cap and cap.lower().startswith("table")) or any("|" in t for t in lines[:2]) else "text"
            context, context_spans = [], []
            if cap:
                context.append(cap)
                cap_block = next((b for b in page["blocks"] if clean_line(" ".join(b["text"].split(NL))) == cap), {})
                context_spans.append({"unit_index": len(lines), "normalized_start": 0, "normalized_end": len(cap),
                                      "block_id": cap_block.get("block_id"), "bbox_pdf": cap_block.get("bbox"),
                                      "line_start": 0, "line_end": max(0, len(cap_block.get("text", "").split(NL)) - 1), "joiner": " "})
            # Preserve original textual column headers; unreviewed HTML grids are not promoted to facts.
            header_count = 0
            if content_kind == "table_candidate":
                for j, line in enumerate(lines[:2]):
                    if line.count("|") < 2 or counter.one(NL.join(context + [line])) > cfg["table_max_tokens"] // 4:
                        break
                    context.append(line)
                    context_spans.append({"unit_index": j, "normalized_start": 0, "normalized_end": len(line), **ocr_refs[j]})
                    header_count += 1
            remaining = lines[header_count:]
            if not remaining:
                remaining, header_count = lines, 0
                context, context_spans = ([cap] if cap else []), context_spans[:1] if cap else []
            # Keep original OCR unit indices even after extracting repeated headers.
            selected_refs = ocr_refs[header_count:]
            emit_parts(remaining, selected_refs, [sec] * len(remaining), [region_path] * len(remaining),
                       "ocr_region", cfg["table_max_tokens"] if content_kind != "text" else cfg["max_tokens"],
                       r=r, cap=cap, prefix=NL.join(context), prefix_spans=context_spans, content_kind=content_kind,
                       metadata=metadata, unit_index_base=header_count, states=[region_state] * len(remaining))
    return chunks


def quantiles(values):
    values = sorted(values)
    return {f"p{int(f * 100)}": values[min(len(values) - 1, int(len(values) * f))] for f in (0.1, 0.5, 0.9)} | {"max": values[-1]} if values else {}


def build(cfg, document=None, local_only=False, documents=None):
    groups = json.loads((ROOT / "rag/doc_groups.json").read_text(encoding="utf-8"))
    counter = Counter(cfg["token_counter"], local_only=local_only)
    docs = [json.loads(x) for x in (ROOT / "data/catalog/documents.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    chunks, stats = [], collections.Counter()
    with sqlite3.connect(f"file:{(ROOT / 'data/collection.sqlite').as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for doc in docs:
            # excluded_roles: guidelines (no product) stay out until the chunking side decides how to label them.
            if doc["source"] in groups["excluded_sources"] or doc.get("role") in groups.get("excluded_roles", []) or (document and doc["document_id"] != document) or (documents is not None and doc["document_id"] not in documents):
                continue
            chunks.extend(build_document(doc, groups, counter, db, cfg, stats))
    if not chunks:
        raise ValueError("No chunks produced; check the document filter")
    study_context.annotate(chunks)
    neighbors = collections.defaultdict(list)
    for c in chunks:
        neighbors[(c["document_id"], c["version_id"], c["pdf_page"], c["source_kind"], c["region"], c["section"])].append(c)
    for members in neighbors.values():
        for i, c in enumerate(members):
            c["structure"]["previous_chunk_id"] = members[i - 1]["chunk_id"] if i else None
            c["structure"]["next_chunk_id"] = members[i + 1]["chunk_id"] if i + 1 < len(members) else None
    validate_dataset(chunks, counter)
    summary = {
        "config": cfg, "profile_id": profile_id(cfg), **dict(stats), "chunks": len(chunks), "schema_validation": "passed",
        "selected_documents": sorted(documents) if documents is not None else None,
        "tokens": quantiles(c["n_tokens"] for c in chunks),
        "embedding_tokens": quantiles(c["n_tokens_with_header"] for c in chunks),
        "over_embedding_limit": sum(c["n_tokens_with_header"] > cfg["embedding_max_tokens"] for c in chunks),
        "by_source": dict(collections.Counter(c["source"] for c in chunks)),
        "by_doc_group": dict(collections.Counter(c["doc_group"] for c in chunks)),
        "by_product": dict(collections.Counter(c["product_id"] for c in chunks)),
        "by_content_kind": dict(collections.Counter(c["structure"]["kind"] for c in chunks)),
        "chunks_with_section": sum(bool(c["section"]) for c in chunks),
        "materialized_section_ids": len({s for c in chunks for s in c["structure"]["ancestor_section_ids"]}),
        "chunks_with_study_heading": sum(bool(c["structure"]["heading_studies"]) for c in chunks),
        "chunks_with_study": sum(bool(c["studies"]) for c in chunks),
        "by_study_scope": dict(collections.Counter(c["study_scope"] for c in chunks)),
        "quality_warnings": dict(collections.Counter(w for c in chunks for w in c["quality"]["warnings"])),
        "chunks_with_redaction": sum(c["redaction_markers"] > 0 for c in chunks),
        "identical_content_chunks": len(chunks) - len({(c["source"], c["product_id"], c["content_hash"]) for c in chunks}),
    }
    if not document:
        gold_src = {"SRC_EPAR_PYZ": "EMA:63563", "SRC_EPAR_WEZ": "EMA:64196", "SRC_EPAR_STQ": "EMA:64792"}
        have = collections.defaultdict(set)
        for c in chunks:
            have[(c["document_id"], c["pdf_page"])].add(c["source_kind"])
        with open(ROOT / "gold/gold_values_v0.2.csv", encoding="utf-8-sig") as f:
            gold = [r for r in csv.DictReader(f) if r["pdf_page"]]
        key = lambda r: (gold_src[r["source_id"]], int(r["pdf_page"]))
        summary["gold_pages"] = len({key(r) for r in gold})
        summary["gold_pages_without_chunk"] = sorted({key(r) for r in gold if not have.get(key(r))})
        summary["gold_image_pages_without_ocr_chunk"] = sorted({key(r) for r in gold if r["modality"] == "IMAGE" and "ocr_region" not in have.get(key(r), ())})
    return chunks, summary


def write_build(chunks, summary, out, context_data=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(c, ensure_ascii=False) + NL for c in chunks).encode("utf-8")
    (out / "chunks.jsonl").write_bytes(payload)
    summary["chunks_sha256"] = hashlib.sha256(payload).hexdigest()
    if summary["config"].get("structure_version"):
        from context_units import write as write_contexts
        summary["context_index"] = write_contexts(chunks, out, ROOT, materialized=context_data)
    (out / "chunks_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    schema = SCHEMA
    if chunks and all(c.get('schema_version') == 'rag-chunk-3' for c in chunks):
        from preprocessing_chunk_schema import SCHEMA as schema
    (out / "chunk.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--document", help="Only this document_id; summary only, writes nothing")
    p.add_argument("--output-dir", type=Path, help="Output directory; defaults to a versioned candidate, not the active index")
    p.add_argument("--max-tokens", type=int, default=CONFIG["max_tokens"], help="Body hard limit (BGE-M3 tokens)")
    p.add_argument("--target-tokens", type=int, help="Preferred body size; default 75 percent of max-tokens")
    p.add_argument("--table-max-tokens", type=int, default=CONFIG["table_max_tokens"])
    p.add_argument("--overlap-tokens", type=int, default=CONFIG["overlap_tokens"])
    p.add_argument("--local-only", action="store_true", help="Use only cached tokenizer files")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = {**CONFIG, "max_tokens": a.max_tokens, "target_tokens": a.target_tokens or int(a.max_tokens * 0.75),
           "table_max_tokens": a.table_max_tokens, "overlap_tokens": a.overlap_tokens}
    if not 0 < cfg["target_tokens"] <= cfg["max_tokens"] < cfg["embedding_max_tokens"] or not 0 < cfg["table_max_tokens"] < cfg["embedding_max_tokens"] or not 0 <= cfg["overlap_tokens"] < cfg["target_tokens"]:
        p.error("Expected 0 <= overlap < target <= max < model capacity and 0 < table max < model capacity")
    chunks, summary = build(cfg, a.document, a.local_only)
    if not a.document:
        out = a.output_dir or OUT / "candidates" / f"{cfg['config_version']}-{profile_id(cfg)}"
        write_build(chunks, summary, out)
        summary["output_dir"] = str(out.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
