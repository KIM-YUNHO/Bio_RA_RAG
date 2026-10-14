"""Bounded paired source-selection evaluation over identical saved contexts.

No retrieval, OCR, embeddings or live service mutations. No gold is sent to the
model. Model calls: one question contract + two selectors per case, maximum 24.
Failed requests are never automatically retried; completed exact calls are cached.
"""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag import contract_query, contract_candidate, source_selection
from rag.openai_chat import EvaluationAPIChat, ApiTokenizer, KST

RUN = ROOT / "data/runs/rag_contract_stage2_2026-10-11_v4"
PILOT = ROOT / "data/runs/rag_contract_stage2_2026-10-11_v1"
PRIOR = ROOT / "data/runs/rag_contract_stage2_2026-10-11_v2"
GROUP_PILOT = ROOT / "data/runs/rag_contract_stage2_2026-10-11_v3"
BASE = ROOT / "data/runs/rag_full_suite_2026-10-10_v1"
RELEASE = ROOT / "data/rag/releases/rag_finalization_2026-10-10_v2"
IDS = ["D08", "T05", "Q038", "Q057", "Q012", "Q028"]
CAP = 18
MODULES = ["rag/contract_terms.py", "rag/request_contract.py", "rag/contract_query.py", "rag/contract_match.py",
           "rag/contract_relations.py", "rag/contract_candidate.py", "rag/grounded_table_graph.py", "rag/eval_contract_stage2.py"]
TESTS = ["tests.test_contract_relations", "tests.test_contract_candidate", "tests.test_request_contract",
         "tests.test_grounded_table_graph", "tests.test_contract_query", "tests.test_source_selection",
         "tests.test_source_context", "tests.test_table_evidence"]


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def ledger(out=None):
    values = [a for p in (ROOT / "data/runs/model_quota").glob("*.json") for a in read(p)["attempts"]]
    return [a for a in values if a.get("evaluation_out") == str(((out or RUN) / "api_eval").resolve())]


def stage2_attempts():
    return sum(len(ledger(out)) for out in [PILOT, PRIOR, GROUP_PILOT, RUN])


def frozen_check(prepared):
    assert all(sha(ROOT / n) == h for n, h in prepared["candidate_code_sha256"].items()), "candidate code changed after preparation"
    assert all(sha(ROOT / n) == h for n, h in prepared["production_code_sha256"].items()), "production baseline changed"
    assert sha(RELEASE / "release_manifest.json") == prepared["release_manifest_sha256"]
    assert sha(RELEASE / "candidate/chunks.jsonl") == prepared["chunks_sha256"]
    assert sha(RUN / "inputs.json") == prepared["inputs_sha256"]


def prepare():
    if (RUN / "prepared.json").exists():
        frozen_check(read(RUN / "prepared.json")); return
    RUN.mkdir(parents=True, exist_ok=True)
    baseline = read(BASE / "prepared.json")
    with (RUN / "tests.txt").open("w", encoding="utf-8") as stream:
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(m) for m in TESTS)
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    assert result.wasSuccessful()
    source_hashes = {}; inputs = []
    protected = set(baseline["protected_excluded"])
    for cid in IDS:
        path = BASE / "openai_eval/responses" / (cid + ".json"); r = read(path); source_hashes[cid] = sha(path)
        assert len(r["results"]) == 1
        unit = r["results"][0]
        assert all(b["jurisdiction"] in ["EU", "US", "CA"] and b["version_id"] not in protected for b in unit["evidence"].values())
        inputs.append({"id": cid, "question": r["question"], "understanding": r["understanding"],
                       "unit": unit["unit"], "evidence": unit["evidence"]})
    write(RUN / "inputs.json", inputs)
    tokenizer = ApiTokenizer(); preflight = []
    for case in inputs:
        candidates = contract_candidate.catalog(case["question"], case["evidence"])
        messages = contract_candidate.selector_prompt(case["question"], case["understanding"], case["unit"], case["evidence"], candidates)
        estimated = tokenizer.messages(messages) + tokenizer.one(json.dumps(source_selection.schema(candidates), ensure_ascii=False, separators=(",", ":")))
        query_tokens = tokenizer.messages(contract_query.messages(case["question"])) + tokenizer.one(json.dumps(contract_query.schema(), ensure_ascii=False, separators=(",", ":")))
        preflight.append({"id": case["id"], "baseline_prompt_estimate": estimated, "query_prompt_estimate": query_tokens,
                          "evidence_blocks": len(case["evidence"]), "value_ids": len(source_selection.value_ids(candidates))})
    day = datetime.now(KST).date().isoformat(); daily = ROOT / "data/runs/model_quota" / (day + ".json")
    usage = len(read(daily)["attempts"]) if daily.exists() else 0
    prepared = {"version": "contract-stage2-eval-1", "cases": IDS, "test_count": result.testsRun, "tests_passed": True,
        "candidate_code_sha256": {n: sha(ROOT / n) for n in MODULES}, "production_code_sha256": baseline["code_sha256"],
        "release_manifest_sha256": baseline["release_manifest_sha256"], "chunks_sha256": baseline["chunks_sha256"],
        "inputs_sha256": sha(RUN / "inputs.json"), "saved_responses_sha256": source_hashes,
        "protected_excluded": baseline["protected_excluded"], "fresh_call_cap": CAP, "date_kst": day,
        "project_daily_attempts_before": usage, "project_daily_attempts_limit_for_this_run": usage + CAP,
        "account_daily_quota_known": False, "preflight": preflight, "context_tokens": 24576,
        "pilot_path": str(PILOT), "pilot_attempts": read(PILOT / "progress.json")["attempts"], "overall_pilot_plus_main_cap": 24,
        "prior_schema_failure_path": str(PRIOR), "prior_schema_failure_attempts": len(ledger(PRIOR)),
        "prior_stage2_attempts": stage2_attempts(), "api_cases_reduced_to_preserve_overall_cap": True,
        "group_pilot_path": str(GROUP_PILOT), "group_pilot_attempts": len(ledger(GROUP_PILOT)),
        "assay_case_Q033_offline_only": True, "compact_request_axes_expanded_in_code": True,
        "query_output_tokens": 4500, "answer_output_tokens": 3000,
        "same_evidence_for_both_selectors": True, "gold_in_generation": False,
        "baseline_is_current_selector_not_full_production_pipeline": True,
        "retrieval_remeasured": False, "new_ocr_calls": 0, "new_embedding_calls": 0}
    for name in MODULES + [m.replace(".", "/") + ".py" for m in TESTS]:
        dest = RUN / "code_snapshot" / name; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes((ROOT / name).read_bytes())
    write(RUN / "prepared.json", prepared); frozen_check(prepared)
    print(json.dumps({"prepared": True, "tests": result.testsRun, "call_cap": CAP, "daily_before": usage, "preflight": preflight}), flush=True)


class BoundedChat(EvaluationAPIChat):
    def _reserve(self, key, request_path, name, output):
        p = read(RUN / "prepared.json")
        if len(ledger()) >= CAP:
            raise RuntimeError("stage2 total call cap reached")
        if stage2_attempts() >= p["overall_pilot_plus_main_cap"]:
            raise RuntimeError("pilot plus main evaluation cap reached")
        if datetime.now(KST).date().isoformat() != p["date_kst"]:
            raise RuntimeError("KST date changed; new daily usage review required")
        daily = self.ledger_path
        if daily.exists() and len(read(daily)["attempts"]) >= p["project_daily_attempts_limit_for_this_run"]:
            raise RuntimeError("project daily evaluation envelope reached")
        return super()._reserve(key, request_path, name, output)


def run(limit=None):
    prepared = read(RUN / "prepared.json"); frozen_check(prepared)
    client = BoundedChat(RUN / "api_eval", max_new_calls=CAP)
    client.config.num_ctx = prepared["context_tokens"]
    try:
        for case in read(RUN / "inputs.json")[:limit]:
            cid = case["id"]; folder = RUN / "results" / cid; folder.mkdir(parents=True, exist_ok=True)
            if (folder / "complete.json").exists():
                continue
            start = time.monotonic(); client.case = cid + ":contract"; client.config.num_predict = prepared["query_output_tokens"]
            try:
                interpretation = contract_query.interpret(case["question"], chat_client=client)
            except ValueError as exc:
                write(folder / "contract_invalid.json", {"error": str(exc), "model_repair_calls": 0,
                    "automatic_retry": False, "source_selection_skipped": True})
                print(json.dumps({"case": cid, "contract_invalid": str(exc), "attempts": len(ledger())}, ensure_ascii=False), flush=True)
                continue
            write(folder / "interpretation.json", interpretation)
            # Both selectors see the same fresh understanding and identical original
            # evidence. The independent contract/local checker is the intervention.
            u = interpretation["understanding"]; evidence = case["evidence"]; unit = case["unit"]
            candidates, audits = contract_candidate.inventory(case["question"], interpretation, unit, evidence)
            write(folder / "source_audits.json", audits)
            client.config.num_predict = prepared["answer_output_tokens"]
            for mode in ["baseline", "candidate"]:
                client.case = cid + ":" + mode
                messages = contract_candidate.selector_prompt(case["question"], u, unit, evidence, candidates) if mode == "baseline" else contract_candidate.prompt(case["question"], interpretation, unit, evidence, candidates, audits)
                reply = client(messages, json_schema=source_selection.schema(candidates)); raw = json.loads(reply["text"])
                literal = source_selection.resolve(raw, candidates, evidence, case["question"], u)
                guarded = contract_candidate.validate_selection(raw, candidates, audits, evidence, interpretation, case["question"])
                write(folder / (mode + ".json"), {"raw_selection": raw, "literal_resolved": literal,
                    "contract_validated": guarded, "model": reply["model_reported"], "fresh_usage": {
                        "prompt_tokens": reply["prompt_tokens"], "completion_tokens": reply["completion_tokens"]}})
            write(folder / "complete.json", {"seconds": round(time.monotonic()-start, 2), "source_values_rewritten": False})
            print(json.dumps({"case": cid, "completed": True, "seconds": round(time.monotonic()-start, 2), "attempts": len(ledger()),
                "contract_status": interpretation["requirements"]["status"]}, ensure_ascii=False), flush=True)
    finally:
        client.close(); frozen_check(prepared)
        attempts = ledger()
        completed = [cid for cid in IDS if (RUN / "results" / cid / "complete.json").exists()]
        write(RUN / "progress.json", {"completed_cases": completed, "attempts": len(attempts),
            "statuses": {s: sum(a.get("status") == s for a in attempts) for s in sorted({a.get("status") for a in attempts})},
            "prompt_tokens": sum(a.get("prompt_tokens") or 0 for a in attempts), "completion_tokens": sum(a.get("completion_tokens") or 0 for a in attempts),
            "fresh_call_cap": CAP, "production_code_unchanged": True, "same_context_evaluation": True,
            "prior_stage2_attempts": prepared["prior_stage2_attempts"], "all_stage2_attempts": stage2_attempts(),
            "invalid_contract_cases": [cid for cid in IDS if (RUN / "results" / cid / "contract_invalid.json").exists()],
            "end_to_end_accuracy_measured": False, "api_retries": 0})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--prepare", action="store_true"); parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    prepare() if args.prepare else run(args.limit)
