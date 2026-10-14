"""Shared coverage of per-field obligations by individual source units.

Role extraction and source relationships are upstream concerns. This kernel
only combines their recorded tri-state checks. It cannot join partial conditions
from different cells into a fully supported fact or interpret a new condition.
"""

VERSION = "request-coverage-1"


def effective_status(check):
    """A parent 'match' cannot override unresolved or contradictory children."""
    status = check.get("status", "unknown")
    children = check.get("checks", [])
    states = {c.get("status", "unknown") for c in children}
    if status == "mismatch" or "mismatch" in states:
        return "mismatch"
    if status != "match" or not children or states != {"match"}:
        return "unknown"
    return "match"


def classify(checks):
    supported = [c for c in checks if effective_status(c) == "match"]
    viable = [c for c in checks if effective_status(c) != "mismatch"]
    return {"supported": supported, "viable": viable,
            "status": "supported" if supported else "mismatch" if checks and not viable else "unknown"}


def coverage(contract, checks_by_source):
    """Cover each request only with one source unit satisfying its entire AND.

    A whole cell may legitimately support multiple fields. The conditions for
    those fields remain independent. Completion is limited to the supplied
    contract, whose exhaustive interpretation is never certified here.
    """
    requested = [r["id"] for r in contract["requests"]]
    if len(set(requested)) != len(requested):
        raise ValueError("request IDs must be unique")
    support = {request_id: [] for request_id in requested}
    unknown = []
    rejected = []
    for source_id, checks in checks_by_source.items():
        if any(c["request_id"] not in support for c in checks):
            raise ValueError("source check refers to an unknown request")
        result = classify(checks)
        for check in result["supported"]:
            if source_id not in support[check["request_id"]]:
                support[check["request_id"]].append(source_id)
        if result["status"] == "mismatch":
            rejected.append(source_id)
        elif result["status"] == "unknown":
            unknown.append(source_id)
    covered = sorted(request_id for request_id, sources in support.items() if sources)
    missing = [request_id for request_id in requested if not support[request_id]]
    return {"version": VERSION, "requested": len(requested), "covered": covered,
            "missing": missing, "source_support": support,
            "unknown_selected_ids": unknown, "rejected_selected_ids": rejected,
            "compiled_requests_complete": bool(requested) and not missing,
            "complete_question_interpretation": False, "semantic_approval": False}
