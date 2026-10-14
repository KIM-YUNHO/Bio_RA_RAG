#!/usr/bin/env python3
"""Translate the Korean forms of eval/queries_v0.1.csv once and keep the results.

Retrieval comparisons read this file instead of calling the LLM again, so translation variance
does not leak into the comparison of retrievers. One file per provider and model.
"""
import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag"))
from llm import settings  # noqa: E402
from translate_query import PROMPT_VERSION, load_glossary, translate  # noqa: E402

FORMS = ["ko_a", "ko_b"]
MODES = ["plain", "glossary"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--provider", choices=["openai", "ollama"], help="Override LLM_PROVIDER for this run")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--queries", default="eval/queries_v0.1.csv", help="Question file; translations of all files share one cache")
    p.add_argument("--forms", nargs="*", default=FORMS, help="Korean columns to translate")
    p.add_argument("--modes", nargs="*", default=MODES, choices=MODES)
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    s = settings(a.provider)
    name = re.sub(r"[^A-Za-z0-9.]+", "-", f"{s['provider']}_{s['model']}")
    path = ROOT / "data/rag/cache" / f"translations_v0.1_{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": {}}
    queries = [q for q in csv.DictReader(open(ROOT / a.queries, encoding="utf-8-sig")) if q.get("type", "retrieval") == "retrieval"]
    glossary = load_glossary()
    todo = [(q, form, mode) for q in queries for form in a.forms for mode in a.modes
            if cache["items"].get(f"{q['query_id']}|{form}|{mode}", {}).get("prompt_version") != PROMPT_VERSION[mode]]

    def run(job):
        q, form, mode = job
        try:
            r = translate(q[form], mode, glossary, provider=a.provider)
            return job, {**r, "source": q[form], "prompt_version": PROMPT_VERSION[mode]}
        except Exception as e:  # recorded and retried on the next run
            return job, {"error": f"{type(e).__name__}: {e}"[:300]}

    t0 = time.time()
    failed = 0
    with ThreadPoolExecutor(a.workers) as pool:
        for n, ((q, form, mode), r) in enumerate(pool.map(run, todo), 1):
            if "error" in r:
                failed += 1
                print(f"  failed {q['query_id']} {form} {mode}: {r['error']}", file=sys.stderr)
            else:
                cache["items"][f"{q['query_id']}|{form}|{mode}"] = r
            if n % 40 == 0:
                print(f"  {n}/{len(todo)}  {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    cache.update({"provider": s["provider"], "model_requested": s["model"], "prompt_version": PROMPT_VERSION,
                  "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %z")})
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    items = cache["items"]
    summary = {"file": str(path.relative_to(ROOT)).replace("\\", "/"), "translated_now": len(todo) - failed, "failed": failed,
               "total_items": len(items), "seconds": round(time.time() - t0, 1),
               "models_reported": sorted({v["model"] for v in items.values()}),
               "prompt_tokens": sum(v["prompt_tokens"] or 0 for v in items.values()),
               "completion_tokens": sum(v["completion_tokens"] or 0 for v in items.values())}
    for form in a.forms:
        for mode in a.modes:
            sub = [v for k, v in items.items() if k.endswith(f"|{form}|{mode}")]
            terms = sum(len(v["protected"]) for v in sub)
            lost = sum(len(v["missing"]) for v in sub)
            summary[f"{form}|{mode}"] = {"queries": len(sub), "protected_terms": terms, "terms_lost": lost,
                                         "queries_with_loss": sum(bool(v["missing"]) for v in sub),
                                         "second_attempts": sum(v["attempts"] > 1 for v in sub)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
