"""Stored scoped-attribution contract, separate from document-level chunk role."""
from chunk_schema import obj, STR, STRINGS, OPT_STR, validate

VERSION = "rag-context-1"
ROLES = ["subject", "comparison", "mentioned", "excluded", "pooled_component", "unknown", "no_study"]
ANALYSES = ["primary", "sensitivity_or_post_hoc", "pooled", "not_applicable", "unknown"]
ANALYSIS_DETAILS = obj({
    "aggregation": {"type": "string", "enum": ["single_study", "pooled", "unknown"]},
    "timing": {"type": "string", "enum": ["prespecified", "post_hoc", "unknown"]},
    "intent": {"type": "string", "enum": ["primary", "sensitivity", "unknown"]},
    "labels": {"type": "array", "items": STR},
})
RANGE = obj({"unit_id": STR, "start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 1}})
EVIDENCE = {"type": "object", "required": ["quote", "fields"], "properties": {
    "quote": {"type": "string", "minLength": 1}, "fields": {"type": "array", "minItems": 1, "items": STR},
    "source_ranges": {"type": "array", "items": RANGE}, "document_id": STR, "version_id": STR,
    "source_id": STR, "unit_id": STR, "pdf_page": {"type": "integer", "minimum": 1}, "source_kind": STR,
    "location": {"type": "object"}, "context_id": STR, "region": {"type": ["integer", "null"]},
    "source_span": {"type": "object"}}, "additionalProperties": True}
ASSIGNMENT = obj({
    "assignment_id": STR, "study_key": OPT_STR, "role": {"type": "string", "enum": ROLES},
    "scope": {"type": "string", "enum": ["single", "multi", "overview", "none", "unknown"]},
    "analysis": {"type": "string", "enum": ANALYSES}, "analysis_details": ANALYSIS_DETAILS,
    "endpoints": STRINGS, "comparator": STR,
    "status": {"type": "string", "enum": ["heading_rule_unverified", "literal_grounded_semantics_unverified", "source_reviewed_by_assistant"]},
    "method": {"type": "string", "enum": ["heading_rule", "local_llm", "source_example_review"]},
    "evidence": {"type": "array", "minItems": 1, "items": EVIDENCE},
    "applies_to": {"type": "array", "minItems": 1, "items": RANGE},
})
# Method-specific audit fields and context identifiers remain attached.
ASSIGNMENT["additionalProperties"] = True
SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Scoped child chunk metadata", **obj({
    "schema_version": {"type": "string", "const": VERSION}, "chunk_id": STR,
    "contexts": {"type": "array", "items": ASSIGNMENT},
    "unresolved_ranges": {"type": "array", "items": RANGE},
    "conflicts": {"type": "array", "items": obj({"unit_id": STR, "start": RANGE["properties"]["start"],
        "end": RANGE["properties"]["end"], "fields": STRINGS, "assignment_ids": STRINGS})},
    "status": {"type": "string", "enum": ["candidate", "partial_candidate", "unresolved", "conflict"]},
})}


def validate_metadata(metadata, units):
    """Schema and source bounds only; never a semantic accuracy assertion."""
    validate(metadata, SCHEMA, "chunk_metadata")
    ranges = metadata["unresolved_ranges"] + metadata["conflicts"]
    for item in metadata["contexts"]:
        if item["role"] == "no_study" and (item["study_key"] is not None or item["scope"] != "none"):
            raise ValueError("no_study must have null study_key and none scope")
        ranges += item["applies_to"]
        for evidence in item["evidence"]:
            ranges += evidence.get("source_ranges", [])
    for r in ranges:
        if r["unit_id"] not in units or not 0 <= r["start"] < r["end"] <= len(units[r["unit_id"]]["text"]):
            raise ValueError(f"Unknown/out-of-bounds source range: {r}")
