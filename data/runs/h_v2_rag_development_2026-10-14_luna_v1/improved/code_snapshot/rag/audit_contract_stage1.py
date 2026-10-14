"""Reproducible offline sidecar build and saved-response diagnostics. No model calls.

Case IDs below select regression fixtures only. Runtime contract/graph/matcher
modules contain no case, product, page or expected-answer exceptions.
"""
import argparse
from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag.grounded_table_graph import build, selected_fact, validate_graph, GRAPH_SCHEMA
from rag.request_contract import ground, translation_omissions, PROPOSAL_SCHEMA
from rag.contract_match import condition, match_request

BASE = ROOT / "data/runs/rag_full_suite_2026-10-10_v1"
RELEASE = ROOT / "data/rag/releases/rag_finalization_2026-10-10_v2"
MODULES = ["rag/contract_terms.py", "rag/request_contract.py", "rag/contract_query.py",
           "rag/grounded_table_graph.py", "rag/contract_match.py", "rag/audit_contract_stage1.py",
           "tests/test_request_contract.py", "tests/test_grounded_table_graph.py", "tests/test_contract_query.py"]


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def field(dimension, value, quote):
    return {"dimension": dimension, "value": value, "anchor": {"quote": quote, "occurrence": 0}}


def regression_contract(question, endpoint, extra=None):
    conditions = [{"dimension": d, "operator": "eq", "values": [{"value": v, "anchor": {"quote": q, "occurrence": 0}}]}
                  for d, v, q in (extra or [])]
    # For diagnostics only: explicitly preserve all recognized mentions as separate
    # request fields. This is a question-only fixture, not a model interpretation.
    from rag.contract_terms import atoms
    primary = {"id": "selected_endpoint", "field": field("endpoint", endpoint, endpoint), "conditions": conditions}
    requests = [primary]
    for i, atom in enumerate(atoms(question)):
        if atom["dimension"] == "endpoint" and atom["value"] == endpoint:
            continue
        occurrences = question[:atom["start"]].count(atom["quote"])
        requests.append({"id": f"mention_{i}", "field": {"dimension": atom["dimension"], "value": atom["value"],
            "anchor": {"quote": atom["quote"], "occurrence": occurrences}},
            "conditions": deepcopy(conditions) if atom["dimension"] == "endpoint" else []})
    return ground(question, {"requests": requests, "non_request_mentions": []})


def saved_items(response):
    for unit_no, unit in enumerate(response["results"]):
        graphs = {eid: build(e) for eid, e in unit["evidence"].items()}
        for item_no, item in enumerate(unit["items"]):
            candidates = []
            for eid in item["evidence"]:
                selection = item.get("source_selection", {}).get("source_range")
                if selection:
                    found = selected_fact(graphs[eid], selection)
                    if found:
                        candidates.append((eid, found))
            # Ambiguous evidence lists never let the first matching numeric value win.
            yield unit_no, item_no, item, candidates[0] if len(candidates) == 1 else (None, None)


def fixture_diagnostics(responses):
    diagnostics = {}
    r = responses["T05"]
    records = []
    for unit_no, item_no, item, (eid, fact) in saved_items(r):
        records.append({"item": item_no, "value": item["value"], "source": eid,
                        "endpoint": condition(fact, "endpoint", "eq", ["tmax"]),
                        "unit": condition(fact, "unit", "eq", ["day"])})
    assert len(records) == 5
    assert all(x["endpoint"]["status"] == x["unit"]["status"] == "match" for x in records[:4])
    assert records[4]["unit"]["status"] == "mismatch"
    diagnostics["T05"] = {"normal_values_preserved": 4, "foreign_unit_value_flagged": 1, "items": records}
    for case_id in ["Q057", "Q058"]:
        r = responses[case_id]; values = []
        for _, i, item, (eid, fact) in saved_items(r):
            values.append({"item": i, "value": item["value"], "source": eid,
                           "endpoint": condition(fact, "endpoint", "eq", ["AUClast"])})
        assert values and any(v["endpoint"]["status"] == "mismatch" for v in values)
        diagnostics[case_id] = {"endpoint_substitution_detected": True, "items": values}
    r = responses["Q038"]
    c = regression_contract(r["question"], "AUCinf", [
        ("covariate", "protein_adjusted", "단백질 함량 보정"), ("analysis_set", "ADA_negative", "ADA 음성")])
    selected = []
    endpoint_requests = [request for request in c["requests"] if request["field"]["dimension"] == "endpoint"]
    for _, i, item, (eid, fact) in saved_items(r):
        selected.append({"item": i, "value": item["value"], "source": eid,
                         "checks": [match_request(c, request, fact) for request in endpoint_requests]})
    assert selected and all(all(check["status"] != "match" for check in s["checks"]) for s in selected)
    # Find complete local conjunctions among supplied cells without answer values
    # or gold pages. This is a relationship diagnostic, not an answer replacement.
    matching_rows = []
    for unit in r["results"]:
        for eid, e in unit["evidence"].items():
            graph = build(e)
            for table in graph["tables"]:
                for f in table["facts"]:
                    checks = [match_request(c, request, f) for request in endpoint_requests]
                    if any(check["status"] == "match" for check in checks):
                        cell = next(n for n in table["cells"] if n["id"] == f["value_node"])
                        matching_rows.append({"evidence": eid, "value": cell["value"], "row": cell["row"]})
    assert matching_rows
    diagnostics["Q038"] = {"selected_missing_conjunction_not_certified": True, "selected": selected,
        "complete_protein_and_ADA_context_candidates": matching_rows,
        "weight_range_and_comparison_direction": "unresolved; not a complete answer check",
        "fixture_contract": c}
    r = responses["D08"]
    missing = translation_omissions(r["question"], r["understanding"]["english_query"])
    assert any(a["value"] == "Cmax" for a in missing)
    diagnostics["D08"] = {"dropped_cmax_detected": True, "original_question": r["question"],
        "english_query": r["understanding"]["english_query"], "missing_known_mentions": missing}
    return diagnostics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/runs/rag_contract_stage1_2026-10-11_v1")
    args = parser.parse_args(); out = (ROOT / args.output).resolve()
    if not out.is_relative_to(ROOT / "data/runs"):
        raise ValueError("output must stay inside workspace data/runs")
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic(); prepared = read(BASE / "prepared.json"); manifest = read(RELEASE / "release_manifest.json")
    frozen_before = {name: sha(ROOT / name) for name in prepared["code_sha256"]}
    assert frozen_before == prepared["code_sha256"], "production core differs from frozen baseline"
    release_before = sha(RELEASE / "release_manifest.json")
    assert release_before == prepared["release_manifest_sha256"]
    candidate = RELEASE / "candidate"
    candidate_before = {name: sha(candidate / name) for name in manifest["candidate_hashes"]}
    assert candidate_before == manifest["candidate_hashes"]
    assert candidate_before["chunks.jsonl"] == prepared["chunks_sha256"]
    write(out / "request_proposal.schema.json", PROPOSAL_SCHEMA); write(out / "table_graph.schema.json", GRAPH_SCHEMA)
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(module) for module in [
        "tests.test_request_contract", "tests.test_grounded_table_graph", "tests.test_contract_query",
        "tests.test_table_evidence", "tests.test_source_selection", "tests.test_source_context"])
    with (out / "tests.txt").open("w", encoding="utf-8") as stream:
        tests = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    assert tests.wasSuccessful(), "offline regression tests failed"
    counts = Counter(); issues = Counter(); products = Counter(); agencies = Counter(); document_ids = set()
    with (out / "table_graphs.jsonl").open("w", encoding="utf-8") as destination:
        with (candidate / "chunks.jsonl").open(encoding="utf-8-sig") as chunks:
            for line in chunks:
                chunk = json.loads(line); counts["chunks"] += 1
                assert chunk["version_id"] not in prepared["protected_excluded"]
                assert chunk["jurisdiction"] in ["EU", "US", "CA"]
                document_ids.add(chunk["document_id"]); products[chunk["product_id"]] += 1; agencies[chunk["jurisdiction"]] += 1
                if "<table" not in chunk["text"].lower():
                    continue
                graph = build(chunk); validate_graph(graph, chunk["text"])
                counts["chunks_with_table_markup"] += 1; counts["tables"] += len(graph["tables"])
                counts["representation_issues"] += len(graph["representation_issues"])
                for table in graph["tables"]:
                    counts["cells"] += len(table["cells"]); counts["facts"] += len(table["facts"])
                    counts["tables_with_issues"] += bool(table["issues"]); issues.update(table["issues"])
                    counts["structural_candidate_facts"] += sum(f["relationship_status"] == "structural_candidate" for f in table["facts"])
                destination.write(json.dumps(graph, ensure_ascii=False, separators=(",", ":")) + "\n")
                if counts["chunks_with_table_markup"] % 250 == 0:
                    print(json.dumps({"table_chunks_processed": counts["chunks_with_table_markup"], "seconds": round(time.monotonic() - started, 1)}), flush=True)
    cases = read(BASE / "cases.json")
    assert sha(BASE / "cases.json") == prepared["cases_sha256"]
    responses = {c["id"]: read(BASE / "openai_eval/responses" / (c["id"] + ".json")) for c in cases}
    assert len(responses) == prepared["case_count"] == 77
    omissions = []; selected_audit = []; claim_counts = Counter()
    for case_id, r in responses.items():
        omissions.append({"case_id": case_id, "missing_known_mentions": translation_omissions(r["question"], r["understanding"]["english_query"])})
        for unit_no, item_no, item, (eid, fact) in saved_items(r):
            checks = []
            from rag.contract_terms import atoms
            for c in item.get("conditions", []):
                # Diagnose saved lexical role claims only. A wrong semantic assignment
                # that has no known atom is unknown, never automatically approved.
                terms = [a for a in atoms(c["value"]) if a["dimension"] == c["dimension"]]
                for a in terms:
                    checked = condition(fact, a["dimension"], "eq", [a["value"]]); checks.append(checked); claim_counts[checked["status"]] += 1
            selected_audit.append({"case_id": case_id, "unit": unit_no, "item": item_no, "value": item["value"],
                "source": eid, "has_local_table_fact": fact is not None, "checks": checks, "semantic_approval": False})
    write(out / "translation_coverage.json", omissions); write(out / "saved_selection_audit.json", selected_audit)
    write(out / "regression_diagnostics.json", fixture_diagnostics(responses))
    assert frozen_before == {name: sha(ROOT / name) for name in frozen_before}
    assert release_before == sha(RELEASE / "release_manifest.json")
    assert candidate_before == {name: sha(candidate / name) for name in candidate_before}
    summary = {"version": "rag-contract-stage1-audit-1", "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "offline implementation and saved-response diagnostics; not end-to-end accuracy",
        "tests_run": tests.testsRun, "tests_passed": tests.wasSuccessful(), "corpus": dict(counts),
        "documents": len(document_ids), "products": dict(products), "jurisdictions": dict(agencies), "table_issues": dict(issues),
        "saved_response_cases": len(responses), "saved_items": len(selected_audit),
        "items_with_local_table_fact": sum(i["has_local_table_fact"] for i in selected_audit),
        "saved_known_role_claims": dict(claim_counts), "translation_cases_with_lexical_alarms": sum(bool(i["missing_known_mentions"]) for i in omissions),
        "frozen_core_files_checked": len(frozen_before), "frozen_core_unchanged": True,
        "release_manifest_sha256": release_before, "candidate_hashes_unchanged": True, "protected_excluded": prepared["protected_excluded"],
        "inputs_sha256": {"prepared.json": sha(BASE / "prepared.json"), "cases.json": sha(BASE / "cases.json"),
            **{f"openai_eval/responses/{case_id}.json": sha(BASE / "openai_eval/responses" / (case_id + ".json")) for case_id in responses}},
        "api_calls": 0, "ocr_calls": 0, "qwen_calls": 0, "embedding_calls": 0, "server_mutations": 0,
        "gold_in_contract_or_graph": False, "question_fixtures_are_model_outputs": False,
        "clinical_semantics_certified": False, "end_to_end_accuracy_measured": False,
        "new_code_sha256": {name: sha(ROOT / name) for name in MODULES},
        "artifacts_sha256": {name: sha(out / name) for name in ["table_graphs.jsonl", "tests.txt", "regression_diagnostics.json", "translation_coverage.json", "saved_selection_audit.json", "request_proposal.schema.json", "table_graph.schema.json"]},
        "seconds": round(time.monotonic() - started, 2)}
    write(out / "summary.json", summary); print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
