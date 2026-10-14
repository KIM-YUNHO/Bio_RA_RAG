"""Materialize section/table parents and exact source units for scoped attribution.

Retrieval chunks keep their own text. The parent index groups existing source
units, including across pages, and records which character ranges each child
uses. Parent metadata is never copied blindly across a whole section.
"""
import collections
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from context_schema import VERSION as SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
VERSION = "context-units-2"


def identity(document, version, page, kind, region, span):
    # A caption prepended to an OCR chunk still comes from the native block.
    if span["block_id"] is not None:
        kind, region = "native", None
    source = [document, version, page, kind, region, span["block_id"], span["line_start"], span["line_end"], span["joiner"]]
    return "u" + hashlib.sha256(json.dumps(source, ensure_ascii=False).encode()).hexdigest()[:24], kind, region


def materialize(chunks, root=ROOT):
    """Read the exact existing parsed/OCR sources; do not use VLM table files."""
    if chunks and all(c.get('schema_version') == 'rag-chunk-3' for c in chunks):
        from preprocessed_chunks import materialize as representations
        return representations(chunks, root)
    from build_chunks import clean_line
    units, parents, membership, pages = {}, {}, {}, {}
    with closing(sqlite3.connect(f"file:{(root / 'data/collection.sqlite').as_posix()}?mode=ro", uri=True)) as db:
        ocr = {(d, v, p, r): text or "" for d, v, p, r, text in db.execute(
            "select document_id, version_id, pdf_page, region, text from ocr_regions")}
    for chunk in chunks:
        path = chunk["provenance"]["parsed_path"]
        if path not in pages:
            raw = (root / path).read_bytes()
            if hashlib.sha256(raw).hexdigest() != chunk["provenance"]["parsed_sha256"]:
                raise ValueError(f"Changed parsed source: {path}")
            pages[path] = {p["pdf_page"]: p for p in map(json.loads, raw.decode("utf-8").splitlines()) if p}
        page = pages[path][chunk["pdf_page"]]
        if chunk["source_kind"] != "native":
            current_ocr = ocr[(chunk["document_id"], chunk["version_id"], chunk["pdf_page"], chunk["region"])]
            if hashlib.sha256(current_ocr.encode()).hexdigest() != chunk["provenance"]["ocr_text_sha256"]:
                raise ValueError(f"Changed OCR source: {chunk['chunk_id']}")
        blocks = {b["block_id"]: b for b in page["blocks"]}
        structure = chunk["structure"]
        for anchor in structure["heading_anchors"]:
            node_id = anchor["section_id"]
            node = {"context_id": node_id, "document_id": chunk["document_id"],
                    "version_id": chunk["version_id"], "product_id": chunk["product_id"], **anchor,
                    "kind": "section", "heading_kind": anchor["kind"],
                    "status": "structural_candidate", "unit_ids": [], "chunk_ids": []}
            if node_id in parents and parents[node_id]["heading_location"] != anchor["heading_location"]:
                raise ValueError(f"Conflicting heading anchor: {node_id}")
            parents.setdefault(node_id, node)
        context_ids = list(structure["ancestor_section_ids"])
        if not context_ids:
            fallback = structure["parent_id"] + ":unheaded"
            parents.setdefault(fallback, {"context_id": fallback, "kind": "page", "document_id": chunk["document_id"],
                "version_id": chunk["version_id"], "product_id": chunk["product_id"], "title": "",
                "parent_section_id": None, "status": "no_heading_fallback", "unit_ids": [], "chunk_ids": []})
            context_ids.append(fallback)
        table = structure["table_id"]
        if table:
            parents.setdefault(table, {"context_id": table, "kind": "table", "document_id": chunk["document_id"],
                "version_id": chunk["version_id"], "product_id": chunk["product_id"], "title": structure["caption"],
                "parent_section_id": structure["section_id"], "status": "table_structure_unverified",
                "unit_ids": [], "chunk_ids": []})
            context_ids.append(table)
        references = []
        for span in chunk["provenance"]["source_spans"]:
            uid, kind, region = identity(chunk["document_id"], chunk["version_id"], chunk["pdf_page"],
                                         chunk["source_kind"], chunk["region"], span)
            raw = blocks[span["block_id"]]["text"] if span["block_id"] else page["text"] if kind == "native" else ocr[
                (chunk["document_id"], chunk["version_id"], chunk["pdf_page"], region)]
            text = span["joiner"].join(t for line in raw.split("\n")[span["line_start"]:span["line_end"] + 1] if (t := clean_line(line)))
            if uid in units and units[uid]["text"] != text:
                raise ValueError(f"Conflicting source unit: {uid}")
            units.setdefault(uid, {"unit_id": uid, "document_id": chunk["document_id"], "version_id": chunk["version_id"],
                "product_id": chunk["product_id"], "pdf_page": chunk["pdf_page"], "source_kind": kind, "region": region,
                "source_span": {**span, "normalized_start": 0, "normalized_end": len(text)}, "text": text,
                "section_id": structure["section_id"], "heading_studies": structure["heading_studies"]})
            if not 0 <= span["normalized_start"] < span["normalized_end"] <= len(text):
                raise ValueError(f"Invalid child range: {chunk['chunk_id']}")
            references.append({"unit_id": uid, "start": span["normalized_start"], "end": span["normalized_end"]})
            for cid in context_ids:
                parents[cid]["unit_ids"].append(uid)
        membership[chunk["chunk_id"]] = {"chunk_id": chunk["chunk_id"], "context_ids": context_ids, "source_ranges": references}
        for cid in context_ids:
            parents[cid]["chunk_ids"].append(chunk["chunk_id"])
    for parent in parents.values():
        parent["unit_ids"] = list(dict.fromkeys(parent["unit_ids"]))
        parent["chunk_ids"] = list(dict.fromkeys(parent["chunk_ids"]))
        pnums = [units[u]["pdf_page"] for u in parent["unit_ids"]]
        parent["page_from"], parent["page_to"] = min(pnums), max(pnums)
    return units, parents, membership


def write(chunks, folder, root=ROOT, materialized=None):
    units, parents, membership = materialized if materialized is not None else materialize(chunks, root)
    folder = Path(folder)
    for filename, records in (("source_units.jsonl", units), ("contexts.jsonl", parents), ("chunk_contexts.jsonl", membership)):
        (folder / filename).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records.values()), encoding="utf-8")
    return {"version": VERSION, "source_units": len(units), "section_parents": sum(p["kind"] == "section" for p in parents.values()),
            "unheaded_page_parents": sum(p["kind"] == "page" for p in parents.values()),
            "table_parents": sum(p["kind"] == "table" for p in parents.values()),
            "figure_parents": sum(p["kind"] == "figure" for p in parents.values()), "child_mappings": len(membership)}


def load(folder):
    folder = Path(folder)
    def records(name, id_field):
        return {r[id_field]: r for r in map(json.loads, (folder / name).read_text(encoding="utf-8").splitlines())}
    return records("source_units.jsonl", "unit_id"), records("contexts.jsonl", "context_id"), records("chunk_contexts.jsonl", "chunk_id")


def coalesce(selected, counter, max_tokens=400):
    """Rejoin contiguous lines of a source block, retaining every original range.

    This does not infer OCR table rows, nor merge across pages or source blocks.
    These temporary passage groups are expanded back to original units on output.
    """
    import document_structure as ds
    groups, pending = [], []

    def flush():
        if not pending:
            return
        text = "\n".join(u["text"] for u in pending)
        spans = [{"unit_id": u["unit_id"], "start": 0, "end": len(u["text"])} for u in pending]
        group = {**pending[0], "text": text, "source_ranges": spans}
        if len(pending) > 1:
            group["unit_id"] = "g" + hashlib.sha256(json.dumps(spans, sort_keys=True).encode()).hexdigest()[:24]
            group["source_span"] = {**pending[0]["source_span"], "line_end": pending[-1]["source_span"]["line_end"],
                                    "normalized_start": 0, "normalized_end": len(text)}
        groups.append(group)
        pending.clear()

    for unit in selected:
        previous = pending[-1] if pending else None
        span, old = unit.get("source_span", {}), previous.get("source_span", {}) if previous else {}
        continuous = previous and all(unit.get(k) == previous.get(k) for k in
            ("document_id", "version_id", "pdf_page", "source_kind", "region", "section_id")) and span and old and (
            span.get("block_id") == old.get("block_id") and span.get("line_start") == old.get("line_end", -2) + 1)
        heading = ds.identify(unit["text"], unit.get("product_id"))
        old_heading = previous and ds.identify(previous["text"], previous.get("product_id"))
        if pending and (not continuous or heading or old_heading or
                counter.one("\n".join(u["text"] for u in pending + [unit])) > max_tokens):
            flush()
        pending.append(unit)
    flush()
    return groups


def clip_ranges(unit, start, end):
    """Map a group substring back to original source units, skipping joiners."""
    if not 0 <= start < end <= len(unit["text"]):
        raise ValueError("Invalid passage substring")
    spans = unit.get("source_ranges", [{"unit_id": unit["unit_id"], "start": 0, "end": len(unit["text"])}])
    out, cursor = [], 0
    for span in spans:
        length = span["end"] - span["start"]
        a, b = max(start, cursor), min(end, cursor + length)
        if a < b:
            out.append({"unit_id": span["unit_id"], "start": span["start"] + a - cursor, "end": span["start"] + b - cursor})
        cursor += length + 1
    if cursor - 1 != len(unit["text"]):
        raise ValueError("Group/source range length mismatch")
    return out


def windows(units, parents, counter, max_tokens=2600):
    """Smallest source sections plus complete OCR table parents.

    Heading chains accompany each window. Long sections split at source-unit
    boundaries, not retrieval-chunk boundaries. No window spans a study heading.
    """
    visual_kinds = {'table', 'figure'}
    table_units = {u for p in parents.values() if p["kind"] in visual_kinds for u in p["unit_ids"]}
    for parent in parents.values():
        selected = [units[u] for u in parent["unit_ids"] if parent["kind"] in visual_kinds or (u not in table_units and
                    (parent["kind"] == "page" or units[u]["section_id"] == parent["context_id"]))]
        if not selected:
            continue
        ancestors, current = [], parent
        while current:
            if current.get("heading_location"):
                ancestors.insert(0, {"title": current["title"], "location": current["heading_location"], "context_id": current["context_id"]})
            current = parents.get(current.get("parent_section_id"))
        import study_registry as sr
        # Keep table line units until a reviewed row structure is available.
        selected = coalesce(selected, counter) if parent["kind"] not in visual_kinds else selected
        enclosing = parent
        while enclosing and enclosing.get("heading_kind") != "study":
            enclosing = parents.get(enclosing.get("parent_section_id"))
        related = []
        if enclosing:
            targets = {s["unit_id"] for u in selected for s in u.get("source_ranges", [{"unit_id": u["unit_id"]}])}
            seen_sections, budget = set(), 0
            for uid in enclosing["unit_ids"]:
                u = units[uid]
                sibling = parents.get(u["section_id"], {})
                if uid in targets or uid in table_units or sibling.get("title", "").casefold() not in ("methods", "results", "study design", "statistical analysis"):
                    continue
                if u["section_id"] in seen_sections or len(u["text"].split()) < 8:
                    continue
                cost = counter.one(u["text"])
                if budget + cost > 400:
                    continue
                related.append(u)
                budget += cost
                seen_sections.add(u["section_id"])
        batch, size, part, history = [], 0, 0, []

        def window_record(status):
            # Only preceding units in this same leaf section/table. They are
            # witnesses, not targets; their original locations remain attached.
            named = [u for u in history if sr.finditer(u["text"], u["product_id"])]
            preceding = list({u["unit_id"]: u for u in named[-2:] + history[-2:]}.values())
            return {"context_id": parent["context_id"], "part": part, "headings": ancestors,
                    "preceding": preceding, "related": related, "units": list(batch), "status": status}

        for unit in selected:
            tokens = counter.one(unit["text"]) + 12
            if tokens > max_tokens:
                # Explicitly unresolved, rather than silently cutting evidence.
                if batch:
                    yield window_record("ready")
                    history.extend(batch)
                    batch, size, part = [], 0, part + 1
                batch = [unit]
                yield window_record("oversized_source_unit")
                history.extend(batch)
                batch = []
                part += 1
                continue
            if batch and size + tokens > max_tokens:
                yield window_record("ready")
                history.extend(batch)
                batch, size, part = [], 0, part + 1
            batch.append(unit)
            size += tokens
        if batch:
            yield window_record("ready")


def inherit(assignments, membership):
    """Attach only intersecting source ranges, never all metadata of a parent.

    An assignment addresses a precise normalized character range in an existing
    source unit. Its evidence and review status stay attached on inheritance.
    """
    by_unit = collections.defaultdict(list)
    for item in assignments:
        for span in item["applies_to"]:
            by_unit[span["unit_id"]].append((item, span))
    out = {}
    for cid, child in membership.items():
        values, unresolved, conflicts = {}, [], []
        for used in child["source_ranges"]:
            covered = []
            for item, applicable in by_unit[used["unit_id"]]:
                start, end = max(used["start"], applicable["start"]), min(used["end"], applicable["end"])
                if start >= end:
                    continue
                value = values.setdefault(item["assignment_id"], {**item, "applies_to": []})
                value["applies_to"].append({"unit_id": used["unit_id"], "start": start, "end": end})
                covered.append((start, end))
            cursor = used["start"]
            applicable_values = [(item, max(used["start"], span["start"]), min(used["end"], span["end"]))
                                 for item, span in by_unit[used["unit_id"]] if max(used["start"], span["start"]) < min(used["end"], span["end"])]
            for i, (left, ls, le) in enumerate(applicable_values):
                for right, rs, re in applicable_values[i + 1:]:
                    start, end = max(ls, rs), min(le, re)
                    if start >= end or left["assignment_id"] == right["assignment_id"]:
                        continue
                    fields = []
                    if left.get("study_key") == right.get("study_key"):
                        ld, rd = left.get("analysis_details", {}), right.get("analysis_details", {})
                        structured = all(any(d.get(f) not in (None, "unknown") for f in ("aggregation", "timing", "intent")) for d in (ld, rd))
                        fields = [f for f in (("role", "scope") if structured else ("role", "analysis", "scope")) if left.get(f) not in (None, "unknown")
                                  and right.get(f) not in (None, "unknown") and left[f] != right[f]]
                        fields.extend("analysis_details." + f for f in ("aggregation", "timing", "intent")
                                      if ld.get(f) not in (None, "unknown") and rd.get(f) not in (None, "unknown") and ld[f] != rd[f])
                    elif (left.get("role") == "no_study" or right.get("role") == "no_study") and (
                            left.get("study_key") or right.get("study_key")):
                        fields = ["study_key", "role"]
                    if fields:
                        conflicts.append({"unit_id": used["unit_id"], "start": start, "end": end, "fields": fields,
                                          "assignment_ids": [left["assignment_id"], right["assignment_id"]]})
            for start, end in sorted(covered):
                if cursor < start:
                    unresolved.append({"unit_id": used["unit_id"], "start": cursor, "end": start})
                cursor = max(cursor, end)
            if cursor < used["end"]:
                unresolved.append({"unit_id": used["unit_id"], "start": cursor, "end": used["end"]})
        out[cid] = {"schema_version": SCHEMA_VERSION, "chunk_id": cid, "contexts": list(values.values()), "unresolved_ranges": unresolved, "conflicts": conflicts,
                    "status": "conflict" if conflicts else "partial_candidate" if values and unresolved else "candidate" if values else "unresolved"}
    return out
