"""Sidecar of original table cells and local context candidates.

Logical spans describe a representation, not PDF accuracy or clinical semantics.
All offsets address the supplied evidence text; source_refs/source_spans retain
the route back to the source representation. No page-wide semantic inheritance.
"""
from copy import deepcopy
import hashlib
import re
from html.parser import HTMLParser
from preprocess.table_quality import inspect_table
from rag.table_evidence import cells
from rag.contract_terms import atoms
from rag.chunk_schema import obj, validate

VERSION = "grounded-table-graph-2"
TABLES = re.compile(r"<table\b[^>]*>.*?</table\s*>", re.I | re.S)
STR = {"type": "string"}
INT = {"type": "integer", "minimum": 0}
STRINGS = {"type": "array", "items": STR}
RANGE = {"type": "array", "items": INT, "minItems": 2, "maxItems": 2}
CELL_SCHEMA = obj({"id": STR, "row": INT, "column": INT, "rowspan": {"type": "integer", "minimum": 1},
    "colspan": {"type": "integer", "minimum": 1}, "explicit_header": {"type": "boolean"}, "value": STR,
    "quote": STR, "source_range": RANGE, "content_range": RANGE})
OBSERVATION_SCHEMA = obj({"dimension": STR, "value": STR, "quote": STR, "start": INT, "end": INT,
    "node_id": STR, "basis": {"type": "string", "const": "own_cell_context"},
    "offset_basis": {"type": "string", "const": "node_value"}, "semantic_status": {"type": "string", "const": "unverified"}})
FACT_SCHEMA = obj({"value_node": STR, "relations": {"type": "array", "items": obj({"node_id": STR,
    "kind": {"type": "string", "enum": ["row_context", "column_context", "row_block_context"]},
    "status": {"type": "string", "enum": ["unknown", "structural_candidate"]}})},
    "observations": {"type": "array", "items": OBSERVATION_SCHEMA},
    "relationship_status": {"type": "string", "enum": ["unknown", "structural_candidate"]},
    "semantic_approval": {"type": "boolean", "const": False}})
GRAPH_SCHEMA = obj({"version": {"type": "string", "const": VERSION}, "text_sha256": STR,
    "offset_basis": {"type": "string", "const": "evidence_text"},
    "source": obj({**{k: {"type": ["string", "null"]} for k in ("document_id", "version_id", "chunk_id", "locator_type")},
                   "pdf_page": {"type": ["integer", "null"], "minimum": 0}}),
    "source_refs": {"type": "array"}, "source_spans": {"type": "array"},
    "tables": {"type": "array", "items": obj({"id": STR, "source_range": RANGE, "issues": STRINGS,
        "relationship_status": {"type": "string", "enum": ["unknown", "structural_candidate"]},
        "body_start_row": {"type": ["integer", "null"], "minimum": 0},
        "cells": {"type": "array", "items": CELL_SCHEMA}, "facts": {"type": "array", "items": FACT_SCHEMA}})},
    "semantic_approval": {"type": "boolean", "const": False}, "unresolved_relations": STRINGS, "representation_issues": STRINGS})


def _plain(value):
    # Already decoded text must not be parsed as HTML a second time: literal
    # <60 and <NO DATA> are source content, not formatting tags.
    return re.sub(r"\s+", " ", value).strip().casefold()


class _CellText(HTMLParser):
    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.parts = []; self.feed(raw); self.close()

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")


def _render(raw):
    return re.sub(r"\s+", " ", "".join(_CellText(raw).parts)).strip()


def _number(cell):
    value = cell["value"]
    from rag.categorical_roles import weight_category
    if weight_category(value):return False
    # Dose/CI headings contain numbers but are not observations.
    if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*(?:mg|mcg|µg|μg)\s*", value, re.I):
        return False
    if any(a["dimension"] == "statistic" for a in atoms(value)):
        return False
    return bool(re.match(r"\s*[($±+−-]*\s*\d", value))


def _overlap(a, b, axis):
    span = "rowspan" if axis == "row" else "colspan"
    return max(a[axis], b[axis]) < min(a[axis] + a[span], b[axis] + b[span])


def build(evidence):
    text = evidence["text"]; digest = hashlib.sha256(text.encode()).hexdigest()
    identity = {k: evidence.get(k) for k in ("document_id", "version_id", "chunk_id", "pdf_page", "locator_type")}
    stable = hashlib.sha256((repr(sorted(identity.items())) + digest).encode()).hexdigest()[:24]
    result = {"version": VERSION, "text_sha256": digest, "offset_basis": "evidence_text", "source": identity,
              "source_refs": deepcopy(evidence.get("source_refs", [])), "source_spans": deepcopy(evidence.get("source_spans", [])),
              "tables": [], "semantic_approval": False, "representation_issues": [],
              "unresolved_relations": ["footnote_applicability", "heading_inheritance", "cross_table_analysis", "clinical_semantics"]}
    matches = list(TABLES.finditer(text))
    if len(re.findall(r"<table\b", text, re.I)) != len(matches) or len(re.findall(r"</table\s*>", text, re.I)) != len(matches):
        result["representation_issues"].append("unmatched_or_nested_table_markup")
    for table_no, match in enumerate(matches):
        quality = inspect_table(match.group()); physical = cells(match.group()); logical = quality["cells"]
        aligned = len(physical) == len(logical) and all(_plain(_render(p["raw"])) == _plain(l["text"]) for p, l in zip(physical, logical))
        issues = list(quality["issues"]) + result["representation_issues"]
        if not aligned:
            issues.append("logical_source_alignment_failed")
        table = {"id": f"{stable}:t{table_no}", "source_range": [match.start(), match.end()], "issues": issues,
                 "relationship_status": "unknown" if issues else "structural_candidate", "body_start_row": None, "cells": [], "facts": []}
        result["tables"].append(table)
        if not aligned:
            continue
        for i, (original, position) in enumerate(zip(physical, logical)):
            a, b = match.start() + original["start"], match.start() + original["end"]
            table["cells"].append({"id": f"{table['id']}:c{i}", "row": position["row"], "column": position["column"],
                "rowspan": position["row_span"], "colspan": position["col_span"], "explicit_header": position["is_header"],
                "value": _render(original["raw"]), "quote": text[a:b], "source_range": [a, b],
                "content_range": [match.start() + original["content_start"], match.start() + original["content_end"]]})
        own = table["cells"]
        from rag.table_blocks import blocks,heading_for
        closed_blocks=blocks(own)
        body = next((c["row"] for c in own if not c["explicit_header"] and _number(c)), None)
        table["body_start_row"] = body
        for value in own:
            if not _number(value):
                continue
            row = [c for c in own if c["id"] != value["id"] and _overlap(c, value, "row") and
                   c["column"] + c["colspan"] <= value["column"] and not _number(c) and c["value"].strip()]
            column = [c for c in own if c["row"] < value["row"] and _overlap(c, value, "column") and
                      ((body is not None and c["row"] < body) or c["explicit_header"]) and c["value"].strip() and not _number(c)]
            # Non-rectangular body values/headers need explicit later interpretation.
            relation_status = "unknown" if issues or body is None else "structural_candidate"
            relations = [{"node_id": c["id"], "kind": "row_context", "status": relation_status} for c in row]
            relations += [{"node_id": c["id"], "kind": "column_context", "status": relation_status} for c in column]
            block_id=heading_for(value,own,closed_blocks)
            block=[c for c in own if c['id']==block_id]
            relations += [{'node_id':c['id'],'kind':'row_block_context','status':relation_status} for c in block]
            observations = []
            for c in row + column + block:
                observations.extend({**a, "node_id": c["id"], "basis": "own_cell_context", "offset_basis": "node_value",
                                     "semantic_status": "unverified"} for a in atoms(c["value"]))
            table["facts"].append({"value_node": value["id"], "relations": relations, "observations": observations,
                "relationship_status": "unknown" if issues or body is None else "structural_candidate", "semantic_approval": False})
    return result


def validate_graph(graph, text):
    """Check schema, representation identity, source bounds and link integrity."""
    validate(graph, GRAPH_SCHEMA, "table_graph")
    if graph["text_sha256"] != hashlib.sha256(text.encode()).hexdigest():
        raise ValueError("graph belongs to a different evidence representation")
    ids = set()
    for table in graph["tables"]:
        from rag.table_blocks import blocks,heading_for
        # Independently recomputed for validation, not trusted from the graph.
        closed_blocks=blocks(table['cells'])
        ta, tb = table["source_range"]
        if not 0 <= ta < tb <= len(text):
            raise ValueError("table range outside evidence")
        nodes = {}
        for node in table["cells"]:
            if node["id"] in ids:
                raise ValueError("duplicate graph node ID")
            ids.add(node["id"]); nodes[node["id"]] = node
            a, b = node["source_range"]; ca, cb = node["content_range"]
            if not ta <= a <= ca <= cb <= b <= tb or text[a:b] != node["quote"]:
                raise ValueError("cell range or original quote mismatch")
        for fact in table["facts"]:
            if fact["value_node"] not in nodes or any(r["node_id"] not in nodes for r in fact["relations"]):
                raise ValueError("foreign or missing local node")
            linked = {r["node_id"] for r in fact["relations"]}
            value = nodes[fact["value_node"]]
            for relation in fact["relations"]:
                node = nodes[relation["node_id"]]
                if relation["kind"] == "row_context":
                    valid = _overlap(node, value, "row") and node["column"] + node["colspan"] <= value["column"]
                elif relation['kind']=='column_context':
                    valid = node["row"] < value["row"] and _overlap(node, value, "column") and (
                        node["explicit_header"] or (table["body_start_row"] is not None and node["row"] < table["body_start_row"]))
                else:
                    valid=heading_for(value,table['cells'],closed_blocks)==node['id']
                if not valid:
                    raise ValueError("relationship violates logical row/column locality")
            for observation in fact["observations"]:
                if observation["node_id"] not in linked:
                    raise ValueError("observation is not locally connected")
                node = nodes[observation["node_id"]]; a, b = observation["start"], observation["end"]
                if not 0 <= a < b <= len(node["value"]) or node["value"][a:b] != observation["quote"]:
                    raise ValueError("observation quote mismatch")
    return True


def selected_fact(graph, source_range):
    """Resolve a saved literal or entire cell range to its own enclosing cell."""
    if len(source_range) != 2 or not all(isinstance(n, int) and not isinstance(n, bool) for n in source_range):
        raise ValueError("invalid selection range")
    a, b = source_range
    if a < 0 or b <= a:
        raise ValueError("invalid selection range")
    found = []
    for table in graph["tables"]:
        by_id = {c["id"]: c for c in table["cells"]}
        for fact in table["facts"]:
            cell = by_id[fact["value_node"]]; x, y = cell["source_range"]
            if x <= a and b <= y:
                whole = source_range == cell["source_range"] or source_range == cell["content_range"]
                found.append({**deepcopy(fact), "cell": deepcopy(cell), "table_id": table["id"],
                              "selected_source_range": list(source_range),
                              "value_binding_status": "whole_cell" if whole else "subcell_unresolved"})
    return found[0] if len(found) == 1 else None
