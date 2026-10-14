"""Score ingestion.registry.resolve on the truth set (eval/resolver_cases_v0.1.json). Exit 1 on any miss.

A unique case passes when the resolver returns exactly one candidate with the expected level, ingredient and product_id (or key
prefix for products not registered in targets.json). Usage: uv run python -m ingestion.eval_resolver [cases.json]
"""
import json
import sys

from ingestion import db, registry
from ingestion.contracts import ROOT

CASES = ROOT / "eval/resolver_cases_v0.1.json"


def score(conn, cases):
    results = []
    for case in cases:
        r = registry.resolve(conn, case["query"])
        got = {"status": r.status, "candidates": [(c.level, c.ingredient, c.key) for c in r.candidates]}
        ok = r.status == case["status"]
        if ok and case["status"] == "unique":
            c = r.candidates[0]
            ok = c.level == case["level"] and c.ingredient == case["ingredient"] and (
                c.product_id == case["product_id"] if "product_id" in case else c.key.startswith(case.get("key_prefix", "")))
        results.append({"query": case["query"], "type": case["type"], "ok": ok, **({} if ok else {"expected": case, "got": got})})
    return results


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cases = json.loads((ROOT / sys.argv[1] if len(sys.argv) > 1 else CASES).read_text(encoding="utf-8"))["cases"]
    with db.connect() as conn:
        results = score(conn, cases)
    misses = [r for r in results if not r["ok"]]
    print(json.dumps({"cases": len(results), "passed": len(results) - len(misses), "misses": misses}, ensure_ascii=False, indent=2))
    sys.exit(1 if misses else 0)
