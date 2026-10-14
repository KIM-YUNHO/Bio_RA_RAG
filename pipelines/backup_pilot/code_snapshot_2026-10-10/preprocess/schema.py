"""Versioned inventory contract and validation without new dependencies."""
import math

SCHEMA_VERSION = "ra-inventory-1"
STRING = {"type": "string"}
NULL_STRING = {"type": ["string", "null"]}
INTEGER = {"type": "integer", "minimum": 0}
BBOX = {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 4, "maxItems": 4}


def record(kind, fields):
    fields = {"schema_version": {"const": SCHEMA_VERSION}, "record_type": {"const": kind}, **fields}
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


COMMON = {"document_id": STRING, "version_id": STRING}
SOURCE_REF = {"type": "object", "required": ["kind", "path", "sha256"], "properties": {
    "kind": {"enum": ["pdf_region", "pdf_block", "html_node"]}, "path": STRING,
    "sha256": STRING, "bbox_pdf": BBOX, "pdf_page": INTEGER, "block_id": STRING,
    "start": INTEGER, "end": INTEGER, "node_path": STRING,
}, "additionalProperties": False}
SCHEMAS = {
    "document": record("document", {**COMMON, "kind": {"enum": ["pdf", "html"]},
        "raw_path": STRING, "text_path": STRING, "raw_sha256": NULL_STRING, "text_sha256": NULL_STRING,
        "source_paths": {"type": "array", "items": STRING}, "metadata": {"type": "object"},
        "status": {"enum": ["heuristic_inventory_complete", "partial", "failed"]},
        "coverage_status": {"enum": ["layout_unassessed", "dom_inventory_only", "partial", "failed"]},
        "units_total": INTEGER, "units_assessed": INTEGER, "candidate_count": INTEGER,
        "has_table_candidate": {"type": ["boolean", "null"]},
        "has_figure_candidate": {"type": ["boolean", "null"]},
        "has_unknown_visual": {"type": ["boolean", "null"]},
        "needs_ocr": {"type": ["boolean", "null"]}, "needs_layout_check": {"type": "boolean"},
        "warnings": {"type": "array", "items": STRING}}),
    "page": record("page", {**COMMON, "page_id": STRING,
        "locator_type": {"enum": ["pdf_page", "html_document"]},
        "pdf_page": {"type": ["integer", "null"], "minimum": 1}, "pdf_page_label": NULL_STRING,
        "page_geometry": {"type": ["object", "null"]}, "modality": STRING,
        "text_chars": INTEGER, "needs_ocr": {"type": "boolean"}, "text_quality": {"type": "object"},
        "signals": {"type": "object"}, "candidate_count": INTEGER,
        "detection_status": {"enum": ["candidate_found", "no_candidate_by_heuristics", "failed"]},
        "coverage_status": {"enum": ["layout_unassessed", "dom_inventory_only", "failed"]},
        "needs_layout_check": {"type": "boolean"}, "warnings": {"type": "array", "items": STRING}}),
    "asset": record("asset", {**COMMON, "asset_id": STRING, "page_id": STRING,
        "asset_type": {"enum": ["table", "figure", "unknown_visual"]},
        "modality": {"enum": ["native", "raster", "vector", "mixed", "html"]},
        "locator_type": {"enum": ["pdf_page", "html_document"]},
        "pdf_page": {"type": ["integer", "null"], "minimum": 1}, "bbox_pdf": BBOX,
        "bbox_status": {"enum": ["region_candidate", "caption_only", "dom_node"]},
        "source_refs": {"type": "array", "items": SOURCE_REF, "minItems": 1},
        "detector": STRING, "detection_revision": STRING, "signals": {"type": "array", "items": STRING},
        "caption_refs": {"type": "array", "items": {"type": "object"}},
        "structure_hint": {"type": "object"}, "continuation_status": STRING,
        "next_step": {"enum": ["native_table_extract", "ocr_table_structure", "figure_labels_and_description", "layout_classify", "html_dom_extract"]},
        "legacy_artifact_candidates": {"type": "array", "items": {"type": "object"}},
        "extraction_status": {"const": "not_run"}, "validation_status": {"const": "unverified_candidate"},
        "numeric_evidence_eligible": {"const": False}, "logical_table_id": {"type": "null"},
        "crop_path": {"type": "null"}, "warnings": {"type": "array", "items": STRING}}),
}
JSON_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "RA preprocessing inventory",
               "oneOf": [{"$ref": f"#/$defs/{key}"} for key in SCHEMAS], "$defs": SCHEMAS}


def _validate(value, spec, path):
    allowed = spec.get("type", [])
    allowed = [allowed] if isinstance(allowed, str) else allowed
    types = {"string": str, "object": dict, "array": list, "boolean": bool,
             "integer": int, "number": (int, float), "null": type(None)}
    if allowed and not any(isinstance(value, types[t]) and not (isinstance(value, bool) and t in ("integer", "number")) for t in allowed):
        raise ValueError(f"{path}: invalid type")
    if "const" in spec and (type(value) is not type(spec["const"]) or value != spec["const"]):
        raise ValueError(f"{path}: invalid constant")
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError(f"{path}: invalid enum")
    if isinstance(value, dict):
        if set(spec.get("required", [])) - value.keys():
            raise ValueError(f"{path}: required fields missing")
        properties = spec.get("properties", {})
        if spec.get("additionalProperties") is False and value.keys() - properties.keys():
            raise ValueError(f"{path}: unexpected fields")
        for key, item in value.items():
            _validate(item, properties.get(key, {}), f"{path}.{key}")
    elif isinstance(value, list):
        if not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", math.inf):
            raise ValueError(f"{path}: invalid length")
        for index, item in enumerate(value):
            _validate(item, spec.get("items", {}), f"{path}[{index}]")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value) or not spec.get("minimum", -math.inf) <= value <= spec.get("maximum", math.inf):
            raise ValueError(f"{path}: invalid number")


def validate_record(value):
    kind = value.get("record_type")
    if kind not in SCHEMAS:
        raise ValueError("Unknown inventory record type")
    _validate(value, SCHEMAS[kind], kind)
    if kind == "asset":
        box = value["bbox_pdf"]
        if value["locator_type"] == "pdf_page":
            if value["pdf_page"] is None or box is None or box[0] >= box[2] or box[1] >= box[3]:
                raise ValueError("PDF candidate needs a positive region and physical page")
        elif box is not None or value["pdf_page"] is not None:
            raise ValueError("HTML candidates must not fabricate PDF locations")
        for ref in value["source_refs"]:
            if ref["kind"] == "html_node" and (ref.get("end", 0) <= ref.get("start", -1)):
                raise ValueError("Invalid HTML source range")
    return value
