"""Live semantic scope regression cases; separate from the historical evaluation caches.

python eval/eval_generalization.py [--workers 4]
This calls the configured LLM only for uncached questions; no document/gold values enter the prompt.
"""
import argparse
import json
import sys
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rag"))
from plan_query import plan
from llm import settings
from understand_query import PROMPT_VERSION, cache_fingerprint, cache_path, resolve_cached, understand


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--provider", choices=["openai", "ollama"])
    p.add_argument("--local-only", action="store_true", help="Require loopback Ollama; reject external API destinations")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    config = settings(args.provider)
    if args.local_only and (config["provider"] != "ollama" or urlsplit(config["base_url"]).hostname not in ("localhost", "127.0.0.1", "::1")):
        raise ValueError("local-only evaluation requires Ollama on the loopback interface")
    spec = json.loads((ROOT / "eval/generalization_cases.json").read_text(encoding="utf-8"))
    path = cache_path(args.provider)
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    todo = [c for c in spec["cases"] if cache.get(c["id"], {}).get("cache_fingerprint") != cache_fingerprint(c["question"])]
    errors = []
    with ThreadPoolExecutor(args.workers) as pool:
        jobs = {pool.submit(understand, c["question"], provider=args.provider): c for c in todo}
        for future in as_completed(jobs):
            c = jobs[future]
            try:
                cache[c["id"]] = future.result()
                save(path, cache)
                print(f"understood {c['id']}", flush=True)
            except Exception as exc:
                errors.append({"id": c["id"], "error_type": type(exc).__name__})
    rows = []
    for c in spec["cases"]:
        u = cache.get(c["id"])
        if not u or u.get("cache_fingerprint") != cache_fingerprint(c["question"]):
            continue
        u = resolve_cached(c["question"], u)
        checks = {key: set(u[key]) == set(c[key]) for key in ("products", "studies", "jurisdictions")}
        checks["latin_terms_preserved"] = not u["missing"]
        rows.append({"id": c["id"], "question": c["question"], "expected": {k: c[k] for k in ("products", "studies", "jurisdictions")},
                     "checks": checks, "passed": all(checks.values()), "understanding": u, "plan": plan(u)})
    summary = {"prompt_version": PROMPT_VERSION, "provider": config["provider"], "model": config["model"],
               "local_only": args.local_only, "label_status": spec["review_status"], "n": len(spec["cases"]),
               "completed": len(rows), "passed": sum(r["passed"] for r in rows), "called_now": len(todo), "errors": errors,
               "prompt_tokens": sum(r["understanding"]["prompt_tokens"] for r in rows),
               "completion_tokens": sum(r["understanding"]["completion_tokens"] for r in rows)}
    summary["fallback_questions"] = [r["id"] for r in rows if r["expected"]["studies"] and not r["understanding"]["studies"]]
    summary["wrong_narrowed_studies"] = [r["id"] for r in rows if r["understanding"]["studies"] and not r["checks"]["studies"]]
    save(ROOT / f"data/rag/eval/generalization_{PROMPT_VERSION}_2026-10-06.json", {"summary": summary, "rows": rows})
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for r in rows:
        if not r["passed"]:
            print("miss", r["id"], r["checks"], {k: r["understanding"][k] for k in ("products", "studies", "jurisdictions")})
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
