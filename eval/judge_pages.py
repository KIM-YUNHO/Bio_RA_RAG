#!/usr/bin/env python3
"""Judge whether each pooled page answers its question, per agency (first pass by an LLM).

Grades: 2 = the page itself states the requested information for this study (and analysis or
comparison when the question names one); 1 = same study or topic but not the requested information
(another analysis, another comparator, a mention without the fact); 0 = unrelated.
Every grade 2 must come with a verbatim quote; check_judgments() verifies quotes and numbers against
the page text in code. These are machine judgments to be spot-checked, not reviewed gold.
"""
import argparse
import csv
import json
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag"))
from llm import chat, settings  # noqa: E402

# judge-1 accepted phase 3 descriptions as answers to phase 1 questions and paraphrased quotes (smoke
# test of 40 pairs, 2026-10-05); judge-2 requires the page to tie the fact to the named study.
PROMPT_VERSION = "judge-2"
STUDIES = {
    "SB17": "SB17-1001 = phase 1 PK similarity study in healthy subjects; SB17-3001 = phase 3 study in patients with plaque psoriasis.",
    "ABP654": "20190230 = phase 1 PK similarity study in healthy subjects; 20190232 = phase 3 study in plaque psoriasis; 20200417 = interchangeability (switching) study.",
    "CTP43": "CT-P43 1.1 (Part 2) and CT-P43 1.2 = two phase 1 PK similarity studies in healthy subjects (different studies); CT-P43 3.1 = phase 3 study in plaque psoriasis.",
}
MAX_CHARS = 9000
SCHEMA = {"name": "judgment", "schema": {
    "type": "object",
    "properties": {
        "grade": {"type": "integer", "enum": [0, 1, 2]},
        "partial": {"type": "boolean"},
        "quote": {"type": "string"},
        "value": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["grade", "partial", "quote", "value", "note"], "additionalProperties": False}}

SYSTEM = """You judge whether one page of a regulatory document answers a question. Read only the page given. Do not use outside knowledge.

Grades:
2 = The page itself states the requested information for the named study, and for the named analysis and comparison when the question specifies them. Numbers, a design statement, criteria, a conclusion: whatever the question asks. If it answers only part (for example AUCinf but not Cmax, or one of three comparisons), still give 2 and set partial to true.
1 = The page is about the same product, study or topic but does not state the requested information: it only mentions the study, gives results of a different analysis, a different comparator or a different study, or describes the item without the requested fact.
0 = Unrelated.

Strict rules for grade 2:
- The page must make clear that the information belongs to the named study: the study number, or an unambiguous description of that study, appears in the same passage, table title or section heading on this page. Other studies of the same product (for example the phase 3 study) are different studies: grade 1.
- A generic statement that does not answer the question (for example "PK similarity was demonstrated" when the question asks for ratios) is grade 1.
- quote must be copied character for character from the page, with no rewording, no ellipsis and no added words. Choose a passage of up to 300 characters that contains the answer.

Text marked [OCR] was machine-read from an image table and may contain reading errors and broken table layout.

For grade 2: value = the answer as written on the page, short. For grades 0 and 1: quote and value are empty strings.
note = up to 150 characters explaining the grade (for example: "different analysis: ANCOVA with weight", "only states CIs within margin, no numbers")."""


def page_texts(db, keys):
    """Native text plus OCR regions for each 'document_id|page' key."""
    out = {}
    for key in keys:
        doc, pg = key.rsplit("|", 1)
        pg = int(pg)
        r = db.execute("select text from pages where document_id=? and pdf_page=?", (doc, pg)).fetchone()
        native = (r[0] or "") if r else ""
        native = re.sub(r"[ \t]{2,}", "  ", native)
        ocr = [t for (t,) in db.execute("select text from ocr_regions where document_id=? and pdf_page=? order by region", (doc, pg)) if t and len(t.strip()) >= 20]
        text = native.strip() + "".join(f"\n\n[OCR]\n{t.strip()}" for t in ocr)
        out[key] = text[:MAX_CHARS]
    return out


def describe(q):
    return (f"Question: {q['en_ref']}\n"
            f"Studies of this product: {STUDIES[q['product_id']]}\n"
            f"Product: {q['product_id']}. Study: {q['study_key'].replace('ST_', '')}. Item: {q['topic']}"
            + (f", analysis {q['analysis_id']}" if q["analysis_id"] != "NA" else "")
            + (f", comparisons {q['comparisons'].replace(';', ', ')}" if q["comparisons"] else ""))


def norm(s):
    # Letters and digits only: table cells come out with varying spaces, line breaks and separators.
    return re.sub(r"[^a-z0-9]", "", s.lower())


def check_judgments(items, texts):
    """Mark grade-2 judgments whose quote or numbers are not on the page."""
    for key, j in items.items():
        if j.get("grade") != 2:
            continue
        raw = texts[key.split("@", 1)[1]]
        page = norm(raw)
        quote = norm(j["quote"])
        words = [w for w in re.findall(r"[a-z0-9.]+", j["quote"].lower()) if len(w) > 2]
        coverage = sum(w.strip(".") in raw.lower() for w in words) / len(words) if words else 0
        # Exact (ignoring spacing and punctuation), or at least 90% of the quote's words on the page.
        j["quote_on_page"] = bool(quote) and (quote in page or coverage >= 0.9)
        nums = re.findall(r"\d+(?:\.\d+)?", j["value"])
        j["numbers_on_page"] = all(n in raw for n in nums) if nums else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, help="Judge only the first N pairs (smoke test)")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    s = settings()
    name = re.sub(r"[^A-Za-z0-9.]+", "-", f"{s['provider']}_{s['model']}")
    path = ROOT / "data/rag/eval" / f"judgments_v0.1_{name}.json"
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": {}}
    queries = {q["query_id"]: q for q in csv.DictReader(open(ROOT / "eval/queries_v0.1.csv", encoding="utf-8-sig")) if q["type"] == "retrieval"}
    pools = json.loads((ROOT / "data/rag/eval/pools_v0.1.json").read_text(encoding="utf-8"))
    db = sqlite3.connect(f"file:{(ROOT / 'data/collection.sqlite').as_posix()}?mode=ro", uri=True)
    pairs = [(qa, pg) for qa, pages in pools.items() for pg in pages]
    texts = page_texts(db, {pg for _, pg in pairs})
    todo = [(qa, pg) for qa, pg in pairs if cache["items"].get(f"{qa}@{pg}", {}).get("prompt_version") != PROMPT_VERSION]
    if a.limit:
        todo = todo[:a.limit]

    # Questions are judged one after another so that, if the run stops, finished questions are complete.
    todo.sort(key=lambda pair: pair[0])
    stop = {"reason": None}

    def run(pair):
        if stop["reason"]:
            return pair, {"error": "skipped after stop"}
        qa, pg = pair
        q = queries[qa.split("|")[0]]
        doc, page = pg.rsplit("|", 1)
        user = f"{describe(q)}\n\nPage: {doc}, page {page}\n---\n{texts[pg]}\n---"
        try:
            r = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], json_schema=SCHEMA)
            return pair, {**json.loads(r["text"]), "model": r["model_reported"], "prompt_tokens": r["prompt_tokens"],
                          "completion_tokens": r["completion_tokens"], "prompt_version": PROMPT_VERSION}
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            # A daily request limit does not clear by retrying; stop instead of spending the remaining quota on retries.
            if "per day" in msg:
                stop["reason"] = msg[:300]
            return pair, {"error": msg[:300]}

    t0, failed = time.time(), 0
    with ThreadPoolExecutor(a.workers) as pool:
        for n, ((qa, pg), j) in enumerate(pool.map(run, todo), 1):
            if "error" in j:
                failed += 1
            else:
                cache["items"][f"{qa}@{pg}"] = j
            if n % 250 == 0:
                print(f"  {n}/{len(todo)} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
                check_judgments(cache["items"], texts)
                path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    if stop["reason"]:
        print(f"stopped: {stop['reason']}", file=sys.stderr)
    check_judgments(cache["items"], texts)
    cache.update({"model_requested": s["model"], "prompt_version": PROMPT_VERSION, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %z")})
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    items = cache["items"]
    g2 = [j for j in items.values() if j.get("grade") == 2]
    print(json.dumps({"judged_now": len(todo) - failed, "failed": failed, "total": len(items), "seconds": round(time.time() - t0),
                      "grades": {g: sum(j.get("grade") == g for j in items.values()) for g in (0, 1, 2)},
                      "grade2_quote_not_on_page": sum(not j.get("quote_on_page") for j in g2),
                      "grade2_numbers_not_on_page": sum(j.get("numbers_on_page") is False for j in g2),
                      "prompt_tokens": sum(j.get("prompt_tokens") or 0 for j in items.values()),
                      "completion_tokens": sum(j.get("completion_tokens") or 0 for j in items.values())}, indent=1))


if __name__ == "__main__":
    main()
