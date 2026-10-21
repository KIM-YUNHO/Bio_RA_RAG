"""The versioned chunk contract; exported as JSON Schema with each build.

Validation covers the subset used by this schema, without a new dependency.
Source/neighbor integrity and tokenizer counts are checked separately.
"""
import hashlib
import json


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


STR = {"type": "string"}
OPT_STR = {"type": ["string", "null"]}
INT = {"type": "integer", "minimum": 0}
STRINGS = {"type": "array", "items": STR}
SPAN = obj({
    "unit_index": INT, "normalized_start": INT, "normalized_end": INT,
    "block_id": OPT_STR, "bbox_pdf": {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
    "line_start": INT, "line_end": INT,
    "joiner": {"enum": ["\n", " "], "type": "string"},
})
FIELDS = {
    **{k: STR for k in ("chunk_id", "document_id", "version_id", "product_id", "source", "role", "document_type", "doc_group",
                        "jurisdiction", "language", "title", "header", "text", "content_hash", "page_hash")},
    **{k: OPT_STR for k in ("source_url", "doc_date", "pdf_page_label", "section", "ocr_status", "study_label")},
    **{k: INT for k in ("pdf_page", "n_tokens", "n_tokens_with_header", "redaction_markers")},
    "schema_version": {"const": "rag-chunk-2", "type": "string"},
    "locator_type": {"enum": ["pdf_page", "html_section"], "type": "string"},
    "source_kind": {"enum": ["native", "ocr_region", "ocr_page"], "type": "string"},
    "region": {"type": ["integer", "null"]},
    "studies": STRINGS, "study_basis": STR,
    "study_scope": {"enum": ["unknown", "overview", "multi", "single"], "type": "string"},
    "study_attribution_status": {"const": "heuristic_unverified", "type": "string"},
    "provenance": obj({
        "parsed_path": STR, "parsed_sha256": STR, "parser": OPT_STR, "parser_version": OPT_STR,
        "offset_basis": {"const": "cleaned_source_unit", "type": "string"},
        "source_spans": {"type": "array", "items": SPAN, "minItems": 1},
        "ocr_image_path": OPT_STR, "ocr_text_sha256": OPT_STR, "ocr_engine": OPT_STR, "ocr_config_version": OPT_STR,
    }),
    "structure": obj({
        "kind": {"enum": ["text", "table_candidate", "figure_candidate"], "type": "string"},
        "section_path": STRINGS, "parent_id": STR, "previous_chunk_id": OPT_STR, "next_chunk_id": OPT_STR,
        "section_id": OPT_STR, "ancestor_section_ids": STRINGS, "heading_studies": STRINGS,
        "heading_anchors": {"type": "array", "items": obj({
            "section_id": STR, "parent_section_id": OPT_STR, "title": STR, "kind": STR, "basis": STR,
            "study_keys": STRINGS, "depth": INT,
            "heading_location": obj({"pdf_page": INT, "source_kind": STR, "region": {"type": ["integer", "null"]},
                "block_id": OPT_STR, "bbox_pdf": SPAN["properties"]["bbox_pdf"], "line_start": INT, "line_end": INT,
                "joiner": SPAN["properties"]["joiner"]}),
        })},
        "caption": OPT_STR, "table_id": OPT_STR,
        "part_index": INT, "part_count": {"type": "integer", "minimum": 1},
        "repeated_context": {"type": "boolean"},
    }),
    "quality": obj({
        "evidence_status": {"const": "unreviewed", "type": "string"},
        "table_status": OPT_STR, "warnings": STRINGS,
    }),
    "analysis_context": obj({
        "status": {"const": "unresolved", "type": "string"},
        **{k: {"type": "null"} for k in ("analysis_id", "population", "comparison", "endpoint", "timepoint")},
    }),
    "chunking": obj({
        "config_version": STR, "profile_id": STR, "tokenizer": STR,
        "body_limit": {"type": "integer", "minimum": 1}, "embedding_limit": {"type": "integer", "minimum": 1},
        "split_reason": STR, "overlap_tokens": INT,
    }),
}
SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Biosimilar retrieval chunk",
          "description": "Offsets address cleaned parsed/OCR units. Study tags and analysis context are not verified facts.",
          **obj(FIELDS)}


def validate(value, schema=None, path="chunk"):
    if schema is None:
        if isinstance(value, dict) and value.get('schema_version') == 'rag-chunk-3':
            from rag.index.preprocessing_chunk_schema import SCHEMA as schema
        else:
            schema = SCHEMA
    types = {"string": str, "object": dict, "array": list, "integer": int, "number": (int, float),
             "boolean": bool, "null": type(None)}
    allowed = schema.get("type", [])
    allowed = [allowed] if isinstance(allowed, str) else allowed
    if allowed and not any(isinstance(value, types[t]) and not (isinstance(value, bool) and t in ("integer", "number")) for t in allowed):
        raise ValueError(f"{path}: expected {allowed}, got {type(value).__name__}")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path}: unexpected constant")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: unexpected enum value {value!r}")
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - value.keys()
        unknown = value.keys() - schema.get("properties", {}).keys()
        if missing or (unknown and schema.get("additionalProperties") is False):
            raise ValueError(f"{path}: missing={sorted(missing)}, unknown={sorted(unknown)}")
        for k, v in value.items():
            if k in schema.get("properties", {}):
                validate(v, schema["properties"][k], f"{path}.{k}")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise ValueError(f"{path}: unexpected array size")
        for i, v in enumerate(value):
            validate(v, schema.get("items", {}), f"{path}[{i}]")
    elif isinstance(value, (int, float)) and value < schema.get("minimum", float("-inf")):
        raise ValueError(f"{path}: value below minimum")


def validate_dataset(chunks, counter):
    ids = {c["chunk_id"] for c in chunks}
    if len(ids) != len(chunks):
        raise ValueError("Duplicate chunk IDs")
    by_id = {c["chunk_id"]: c for c in chunks}
    for c in chunks:
        validate(c)
        if c["n_tokens"] != counter.one(c["text"]) or c["n_tokens_with_header"] != counter.embedding(c["header"], c["text"]):
            raise ValueError(f"Incorrect token count: {c['chunk_id']}")
        if c["n_tokens"] > c["chunking"]["body_limit"] or c["n_tokens_with_header"] > c["chunking"]["embedding_limit"]:
            raise ValueError(f"Token budget exceeded: {c['chunk_id']}")
        for span in c["provenance"]["source_spans"]:
            invalid = (span['representation_end'] <= span['representation_start']) if c['schema_version'] == 'rag-chunk-3' else (
                span["normalized_end"] <= span["normalized_start"] or span["line_end"] < span["line_start"])
            if invalid:
                raise ValueError(f"Invalid source span: {c['chunk_id']}")
        for field, inverse in (("previous_chunk_id", "next_chunk_id"), ("next_chunk_id", "previous_chunk_id")):
            neighbor = c["structure"][field]
            if neighbor is None:
                continue
            if neighbor not in ids or by_id[neighbor]["structure"][inverse] != c["chunk_id"]:
                raise ValueError(f"Invalid neighbor: {c['chunk_id']}")
            other = by_id[neighbor]
            if any(c[k] != other[k] for k in ("document_id", "version_id", "pdf_page", "source_kind", "region", "section")):
                raise ValueError(f"Neighbor crosses source/section boundary: {c['chunk_id']}")
            if c['schema_version'] == 'rag-chunk-3' and c['structure']['parent_id'] != other['structure']['parent_id']:
                raise ValueError(f"Neighbor crosses representation parent: {c['chunk_id']}")


def profile_id(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
