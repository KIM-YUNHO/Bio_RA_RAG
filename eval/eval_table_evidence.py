#!/usr/bin/env python3
"""Do checked table structures fix answer errors on PK tables? (docs/temp/rag/RAG_STUDY_CONTEXT_PLAN.md 10.3 1번)

The answer-stage errors of 2026-10-06 (answers_v0.2.json, judged in eval/answer_judgments_v0.2.csv) were
mostly values attached to the wrong comparison or analysis, on tables whose text lists cells one per line.
The OCR session now writes table grids checked against the source (data/ocr/<doc>/pNNN_rK.table.json,
status table_checked; row labels carry "[comparison: ...]" where a ratio cell is shared by two rows).

This experiment keeps everything but the page text fixed: the same questions, understanding, units and
evidence pages as answers_v0.2.json, and the current answer prompt and schema of rag/answer.py. For every
answer unit whose evidence includes a page with a checked table it asks twice:
  A  page text as rag/answer.py builds it
  B  the checked table(s) of the page, marked as verified, followed by the same page text
The answers are printed side by side for manual judgment (eval/answer_judgments_tables_*.csv).

    uv run python eval/eval_table_evidence.py
"""
import argparse
import csv
import glob
import hashlib
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag"))
import answer  # noqa: E402
from embed_index import RAG  # noqa: E402
from llm import chat, settings  # noqa: E402


def checked_tables():
    """{(document_id, pdf_page): [grid, ...]} for tables the OCR session marked table_checked."""
    out = {}
    for f in sorted(glob.glob(str(ROOT / "data/ocr/*/p*_r*.table.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        if d.get("status") == "table_checked" and d.get("tables"):
            out.setdefault((d["document_id"], d["pdf_page"]), []).extend((Path(f).name, t) for t in d["tables"])
    return out


def markdown(grid):
    rows = [[c.replace("|", "/").replace("\n", " ") for c in r] for r in grid]
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return "\n".join(["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width] + ["| " + " | ".join(r) + " |" for r in rows[1:]])


def table_block(tables):
    parts = []
    for name, grid in tables:
        parts.append(f"<checked table {name}: rows and columns verified against the source page; read comparisons, "
                     f"arms and analyses from this grid rather than from the flattened text below>\n{markdown(grid)}")
    return "\n\n".join(parts)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run", default=str(RAG / "eval/answers_v0.2.json"), help="Answer run whose evidence pages are reused")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--tag", default="2026-10-06")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    tables = checked_tables()
    rows = [r for r in json.loads(Path(a.run).read_text(encoding="utf-8"))["rows"] if r["set"] == "base"]
    ucache = json.loads((ROOT / "data/rag/cache/understanding_v0.1_openai-gpt-4o-mini.json").read_text(encoding="utf-8"))
    corpus = answer.Corpus("bge-m3")

    def page_meta(doc, page):
        keys = [k for k in corpus.by_page if k[0] == doc and k[2] == page]
        c = corpus.chunks[corpus.by_page[keys[0]][0]]
        return {"document_id": doc, "version_id": keys[0][1], "pdf_page": page, "title": c["title"], "document_type": c["document_type"]}

    units = []
    for r in rows:
        ev = [(e, k.rsplit("|", 1)[0], int(k.rsplit("|", 1)[1])) for e, k in sorted(r["evidence"].items(), key=lambda x: int(x[0][1:]))]
        if not any((d, pg) in tables for _, d, pg in ev):
            continue
        u = ucache[r["question"]]
        unit = {"product": r["product"], "jurisdiction": r["agency"], "doc_groups": ["assessment"], "studies": [r["study"]], "decomposed": False}
        units.append({"row": r, "u": u, "unit": unit, "pages": [page_meta(d, pg) for _, d, pg in ev]})
    qv = corpus.encode([x["u"]["english_query"] for x in units])

    s = settings()
    cpath = ROOT / "data/rag/cache" / f"answers_table_experiment_{re.sub(r'[^A-Za-z0-9.]+', '-', s['provider'] + '_' + s['model'])}.json"
    cache = json.loads(cpath.read_text(encoding="utf-8")) if cpath.exists() else {}
    lock = threading.Lock()
    errors = []

    def ask(i, arm):
        x = units[i]
        texts = [corpus.page_text(pg["document_id"], pg["pdf_page"], qv[i], pg["version_id"]) for pg in x["pages"]]
        if arm == "B":
            texts = [table_block(tables[(pg["document_id"], pg["pdf_page"])]) + "\n\n" + t if (pg["document_id"], pg["pdf_page"]) in tables else t
                     for pg, t in zip(x["pages"], texts)]
        key = hashlib.sha256(json.dumps([answer.PROMPT_VERSION, answer.SYSTEM, x["row"]["ko"], x["unit"],
                                         [(pg["document_id"], pg["pdf_page"]) for pg in x["pages"]], texts],
                                        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        with lock:
            if key in cache:
                return cache[key]
        try:
            r = chat(answer.messages_for(x["row"]["ko"], x["u"], x["unit"], x["pages"], texts), json_schema=answer.SCHEMA)
        except Exception as e:  # rate limits: keep what is done
            errors.append(f"{x['row']['question']} {x['unit']['jurisdiction']} {arm}: {type(e).__name__}: {e}"[:300])
            return None
        out = answer.validate_answer(json.loads(r["text"]), {f"E{n}": {"text": t} for n, t in enumerate(texts, 1)})
        res = {**out, "prompt_tokens": r["prompt_tokens"], "completion_tokens": r["completion_tokens"], "model": r["model_reported"]}
        with lock:
            cache[key] = res
        return res

    jobs = [(i, arm) for i in range(len(units)) for arm in ("A", "B")]
    with ThreadPoolExecutor(a.workers) as pool:
        results = list(pool.map(lambda j: ask(*j), jobs))
    cpath.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    got = {j: r for j, r in zip(jobs, results)}

    out = []
    for i, x in enumerate(units):
        r = x["row"]
        rec = {"question": r["question"].split("|")[0], "agency": r["agency"], "study": r["study"], "ko": r["ko"],
               "gold_value": r["gold_value"], "evidence": r["evidence"],
               "table_pages": [f"{pg['document_id']}|{pg['pdf_page']}" for pg in x["pages"] if (pg["document_id"], pg["pdf_page"]) in tables]}
        for arm in ("A", "B"):
            res = got[(i, arm)]
            rec[arm] = None if res is None else {k: res[k] for k in ("status", "items", "answer_ko", "validation", "prompt_tokens")}
        out.append(rec)
    path = RAG / f"eval/table_evidence_{a.tag}.json"
    path.write_text(json.dumps({"prompt_version": answer.PROMPT_VERSION, "errors": errors, "units": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    tok = sum((res or {}).get("prompt_tokens") or 0 for res in results)
    print(f"units {len(units)}, calls {len(jobs)}, errors {len(errors)}, prompt tokens (incl. cached) {tok}")
    for e in errors[:5]:
        print("  error:", e)
    print(f"written {path}")


if __name__ == "__main__":
    main()
