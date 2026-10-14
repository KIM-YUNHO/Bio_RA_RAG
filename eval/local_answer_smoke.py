"""Two EMA answer-generation smoke tests using only existing local models and artifacts.

Uses BM25/B with two pages, not the full hybrid benchmark. Requires the local generalization cache.
"""
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rag"))
from answer import PROMPT_VERSION, Corpus, answer_unit, cache_path as answer_cache_path, retrieve
from llm import settings
from understand_query import cache_path as understanding_cache_path, resolve_cached


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    config = settings("ollama")
    if urlsplit(config["base_url"]).hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Smoke tests require loopback Ollama")
    upath = understanding_cache_path("ollama")
    uc = json.loads(upath.read_text(encoding="utf-8"))
    corpus = Corpus()
    cpath = answer_cache_path("ollama")
    cache = json.loads(cpath.read_text(encoding="utf-8")) if cpath.exists() else {}
    rows = []
    for key in ("G06", "G09"):
        raw = uc[key]
        u = resolve_cached(raw["source"], raw)
        units = retrieve(corpus, u, None, use_dense=False, k=2, policy="legacy")
        unit, pages = next((unit, pages) for unit, pages in units if unit["jurisdiction"] == "EU")
        res = answer_unit(raw["source"], u, unit, pages, corpus, cache, provider="ollama")
        cpath.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        rows.append({"id": key, "question": raw["source"], "result": res})
        print(json.dumps({"id": key, "status": res["status"], "items_kept": len(res["items"]),
                          "items_rejected": len(res["validation"]["rejected_items"]), "answer_ko": res["answer_ko"]}, ensure_ascii=False), flush=True)
    result = {"prompt_version": PROMPT_VERSION, "provider": "ollama", "model": config["model"], "local_only": True,
              "method": "BM25/B, top2, EU subset; smoke test, not full hybrid evaluation", "rows": rows}
    path = ROOT / "data/rag/eval/local_answer_smoke_2026-10-06.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
