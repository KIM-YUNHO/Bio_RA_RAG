#!/usr/bin/env python3
"""Summarize saved scoped attribution runs; not a semantic accuracy evaluation."""
import argparse
import collections
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folders", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"scope": "Bounded existing-text execution samples. Literal grounding and runtime only; semantic accuracy is not established.", "samples": []}
    for folder in args.folders:
        raw = [json.loads(line) for line in (folder / "llm_raw.jsonl").read_text(encoding="utf-8").splitlines()]
        assignments = [json.loads(line) for line in (folder / "assignments.jsonl").read_text(encoding="utf-8").splitlines()]
        routing = [json.loads(line) for line in (folder / "routing.jsonl").read_text(encoding="utf-8").splitlines()]
        # Raw log is append-only. Count only latest records used in this saved plan.
        latest = {r["fingerprint"]: r for r in raw}
        used = [latest[r["llm_fingerprint"]] for r in routing if r.get("llm_fingerprint") in latest]
        llm = [a for a in assignments if a["method"] == "local_llm"]
        times = sorted(r["seconds"] for r in used)
        sample = {"folder": str(folder.resolve()), "saved_windows": len(routing), "llm_windows": len(used),
            "json_or_runtime_failures": sum(bool(r["error"]) for r in used),
            "unfinished_outputs": sum(r.get("done_reason") != "stop" for r in used),
            "seconds_total": round(sum(times), 2), "seconds_min": min(times, default=None), "seconds_max": max(times, default=None),
            "prompt_tokens": [r.get("prompt_eval_count") for r in used], "output_tokens": [r.get("eval_count") for r in used],
            "retained_literal_runs": len(llm), "rejected_runs": sum(r.get("llm_rejected", 0) for r in routing),
            "removed_fields_or_proofs": dict(collections.Counter(i["field"] for a in llm for i in a["validation_issues"])),
            "roles_unverified": dict(collections.Counter(a["role"] for a in llm)),
            "assignment_status": dict(collections.Counter(a["status"] for a in assignments)),
            "candidate_chunks": json.loads((folder / "summary.json").read_text(encoding="utf-8"))["child_coverage"]}
        report["samples"].append(sample)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
