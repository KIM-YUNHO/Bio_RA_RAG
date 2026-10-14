#!/usr/bin/env python3
"""Answer-stage evaluation (docs/temp/rag/RAG_STUDY_CONTEXT_PLAN.md 8.2 마, RAG plan 6.3).

Each Korean question goes through rag/answer.py: understanding (cached, re-resolved with the current
rules as in eval/eval_pipeline.py) -> plan -> retrieval (method B) -> one answer per agency. Each
(question, agency) answer is scored against the per-agency labels (data/rag/eval/qrels_v0.1.json).

- Value match: numbers in the answer against the numbers of each answer-page label value (the `value`
  column of eval/labels_manual_v0.1.csv and `value_ca` of eval/labels_ca_v0.1.csv). Parameter names
  (AUC0-inf, Cmax), "90% CI", phases and study numbers are removed first. A label row is matched when
  all its numbers are in the answer; the best row counts. Rows without numbers (design, endpoint names)
  are left to manual review.
- Unsupported values: answer values whose numbers do not occur together (within 400 characters) on any
  evidence page given to the model. Numbers found only scattered over the pages do not count.
- Citations: whether each answer item cites a page labelled as answering (grade 2), and whether a cited
  page is about other studies only (eval/study_pages_v0.1.csv); items naming another study.
- Not reported: for agencies whose documents do not answer the question, any value given is an error.
- Retrieval hit: a grade-2 page among the evidence pages, so answer errors can be told from search misses.

The verdicts here are a first pass. Rows marked for review go to data/rag/eval/answers_review_{tag}.csv
and are judged by reading the answer and the page (not by an LLM).

    uv run python eval/eval_answers.py --sets base
"""
import argparse
import csv
import json
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag"))
sys.path.insert(0, str(ROOT / "eval"))
import study_registry as sr  # noqa: E402
from answer import PROMPT_VERSION, Corpus, answer_unit, cache_path, retrieve  # noqa: E402
from embed_index import RAG  # noqa: E402
from eval_study_context import load_page_studies, page_class, study_family  # noqa: E402
from understand_query import cache_fingerprint, cache_path as understanding_cache_path, resolve_cached, understand  # noqa: E402

# Parameter names and fixed phrases whose digits are not values.
NOT_VALUES = re.compile(
    r"AUC\s?[\(\[]?[\w\-–∞]*[\)\]]?|\bC\s?max\b|\bt\s?max\b|\bt\s?1/2\b|"
    r"\b(?:90|95)\s?%\s?(?:CIs?\b|confidence|신뢰)|\b(?:90|95)\s?%\s?CI|"
    r"\bphase\s?(?:[1-3]|I{1,3})\b|\b[1-3]\s?상\b|\bE\d+\b|\bIgG\d\b", re.I)
NUMBER = re.compile(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?")


def numbers(text, product):
    spans = [(s, e) for s, e, _ in sr.finditer(text, product)]
    for s, e in sorted(spans, reverse=True):
        text = text[:s] + " " + text[e:]
    for p in sr.products()[product]["names"]:
        text = re.sub(re.escape(re.sub(r"\s*\(.*\)$", "", p)), " ", text, flags=re.I)
    text = NOT_VALUES.sub(" ", text)
    return {round(float(x), 4) for x in NUMBER.findall(text)}


def _positions(text, product):
    """(number, position) for the values in a text, after the same removals as numbers()."""
    spans = [(s, e) for s, e, _ in sr.finditer(text, product)]
    for s, e in sorted(spans, reverse=True):
        text = text[:s] + " " * (e - s) + text[e:]
    text = NOT_VALUES.sub(lambda m: " " * len(m.group()), text)
    return [(round(float(m.group()), 4), m.start()) for m in NUMBER.finditer(text)]


def supported(value, texts, product, window=400):
    """True when every number of the value occurs within one window of one evidence text (a value copied
    from the page has its numbers close together; scattered matches mean the value is not on the page)."""
    want = numbers(value, product)
    if not want:
        return True
    for t in texts:
        pos = [(n, i) for n, i in _positions(t, product) if n in want]
        for _, start in pos:
            if want <= {n for n, i in pos if start <= i <= start + window}:
                return True
    return False


def grade(ev, rel):
    keys = [(ev["document_id"], ev["pdf_page"]), *map(tuple, ev["also_in"])]
    return max(rel.get(f"{d}|{p}", {}).get("grade", 0) for d, p in keys)


def score(res, comp, ag, base, Q, lab, product, items_filter=None):
    """Scores for one (question component, agency) answer."""
    status = Q["status"][f"{comp}|{ag}"]["status"]
    rel = Q["qrels"].get(f"{comp}|{ag}", {})
    wanted = study_family(base[comp]["study_key"])
    items = [it for it in res["items"] if items_filter is None or items_filter(it)]
    # answer-2 items say whether they are exactly what was asked; related values given as context are not the answer.
    exact = [it for it in items if it.get("exact", True)]
    if "exact" in (items[0] if items else {}):
        ans_text = " ".join(it["value"] for it in exact)
    else:
        ans_text = " ".join(it["value"] for it in items) + " " + (res["answer_ko"] if items_filter is None else "")
    A = numbers(ans_text, product)
    ev_nums = set()
    for ev in res["evidence"].values():
        ev_nums |= numbers(ev["text"], product)
    rows = [(v["partial"], numbers(v["value"], product), v["value"]) for v in rel.values() if v.get("grade") == 2 and v.get("value")]
    num_rows = [r for r in rows if r[1]]
    pool = [r for r in num_rows if not r[0]] or num_rows
    best = max(pool, key=lambda r: len(r[1] & A) / len(r[1]), default=None)
    recall = round(len(best[1] & A) / len(best[1]), 3) if best else None
    hit = any(grade(ev, rel) == 2 for ev in res["evidence"].values())
    cites = []
    for it in items:
        evs = [res["evidence"][e] for e in it["evidence"] if e in res["evidence"]]
        g = max((grade(ev, rel) for ev in evs), default=0)
        cls = [page_class(ev, wanted, lab) for ev in evs]
        cites.append({"grade": g, "other_study_page": bool(cls) and all(c == "other" for c in cls),
                      "item_study_other": it["study"] not in ("none", *wanted)})
    gave_values = bool(exact) and res["status"] != "not_found"
    if status == "not_reported":
        verdict = "review_not_reported" if gave_values else "ok_not_found"
    elif not gave_values:
        verdict = "abstained_hit" if hit else "abstained_miss"
    elif best is None:
        verdict = "review_text"
    elif recall == 1:
        verdict = "value_match"
    elif recall > 0:
        verdict = "review_partial_match"
    else:
        verdict = "review_no_match"
    return {"gold_status": status, "retrieval_hit": hit, "answer_status": res["status"], "verdict": verdict,
            "value_recall": recall, "gold_value": best[2] if best else " || ".join(r[2] for r in rows[:3]),
            "unsupported_numbers": sorted(A - ev_nums),
            "unsupported_items": [it["value"] for it in items
                                  if not supported(it["value"], [ev["text"] for ev in res["evidence"].values()], product)],
            "n_items": len(items),
            "items_cite_grade2": sum(c["grade"] == 2 for c in cites),
            "items_cite_other_study_page": sum(c["other_study_page"] for c in cites),
            "items_other_study": sum(c["item_study_other"] for c in cites)}


def summarise_judgments(tag):
    """Manual judgments (eval/answer_judgments_{tag}.csv, one row per question x agency) joined with the run."""
    path = ROOT / f"eval/answer_judgments_{tag}.csv"
    rows = {(r["question"].split("|")[0], r["agency"]): r
            for r in json.loads((RAG / f"eval/answers_{tag}.json").read_text(encoding="utf-8"))["rows"] if r["set"] == "base"}
    J = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    kinds = ["correct", "partial", "wrong", "missed", "abstain_no_evidence", "nr_ok", "nr_violated"]
    print(f"\n== Manual judgments ({path.name}) ==")
    print(f"{'ag':3s} {'n':>3s} " + " ".join(f"{k[:11]:>11s}" for k in kinds) + "   wrong|hit  wrong|miss")
    out = {}
    for ag in ("EU", "US", "CA"):
        sub = [j for j in J if j["agency"] == ag]
        c = Counter(j["judgment"] for j in sub)
        bad = [j for j in sub if j["judgment"] in ("wrong", "nr_violated")]
        hit = sum(rows[(j["question"], ag)]["retrieval_hit"] for j in bad)
        out[ag] = {**{k: c[k] for k in kinds}, "n": len(sub), "wrong_with_answer_page": hit, "wrong_without_answer_page": len(bad) - hit,
                   "error_types": dict(Counter(j["error_type"] for j in sub if j["error_type"]))}
        print(f"{ag:3s} {len(sub):3d} " + " ".join(f"{c[k]:11d}" for k in kinds) + f"   {hit:9d}  {len(bad) - hit:10d}")
    et = Counter(j["error_type"] for j in J if j["judgment"] in ("wrong", "nr_violated"))
    print("error types of wrong + nr_violated:", dict(et.most_common()))
    (RAG / f"eval/answers_judged_summary_{tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--judged", action="store_true", help="Only summarise eval/answer_judgments_{tag}.csv against the saved run")
    p.add_argument("--sets", nargs="*", default=["base"], choices=["base", "indirect", "product"])
    p.add_argument("--limit", type=int, help="First N questions per set (for a trial run)")
    p.add_argument("--only", nargs="*", help="Only these question ids (e.g. Q013 V05)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--tag", default=PROMPT_VERSION)
    p.add_argument("--retrieval-policy", choices=["legacy", "general"], default="legacy")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if a.judged:
        summarise_judgments(a.tag)
        return
    base = {q["query_id"]: q for q in csv.DictReader(open(ROOT / "eval/queries_v0.1.csv", encoding="utf-8-sig")) if q["type"] == "retrieval"}
    extra = list(csv.DictReader(open(ROOT / "eval/queries_study_v0.1.csv", encoding="utf-8-sig")))
    Q = json.loads((ROOT / "data/rag/eval/qrels_v0.1.json").read_text(encoding="utf-8"))
    lab = load_page_studies()
    upath = understanding_cache_path()
    ucache = json.loads(upath.read_text(encoding="utf-8")) if upath.exists() else {}

    # (cache key, set, Korean question, product, components)
    items = []
    if "base" in a.sets:
        items += [(f"{qid}|ko_a", "base", q["ko_a"], q["product_id"], [qid]) for qid, q in base.items()][: a.limit]
    for kind in ("indirect", "product"):
        if kind in a.sets:
            items += [(f"{q['query_id']}|ko", kind, q["ko"], q["product_id"], q["components"].split(";")) for q in extra if q["kind"] == kind][: a.limit]

    if a.only:
        items = [it for it in items if it[0].split("|")[0] in a.only]
    corpus = Corpus("bge-m3")
    us = []
    for key, _, ko, _, _ in items:
        if ucache.get(key, {}).get("cache_fingerprint") != cache_fingerprint(ko):
            ucache[key] = understand(ko)
        u = ucache[key]
        us.append(resolve_cached(ko, u))
    upath.parent.mkdir(parents=True, exist_ok=True)
    upath.write_text(json.dumps(ucache, ensure_ascii=False, indent=1), encoding="utf-8")
    qv = corpus.encode([u["english_query"] for u in us])
    jobs = []  # (item index, unit, pages)
    for i, u in enumerate(us):
        for unit, pages in retrieve(corpus, u, qv[i], policy=a.retrieval_policy):
            if unit["product"] == items[i][3]:
                jobs.append((i, unit, pages))

    cpath = cache_path()
    cache = json.loads(cpath.read_text(encoding="utf-8")) if cpath.exists() else {}
    lock = threading.Lock()
    errors = []

    def run(job):
        i, unit, pages = job
        try:
            with lock:
                local = dict(cache)
            res = answer_unit(items[i][2], us[i], unit, pages, corpus, local, qv[i])
            with lock:
                cache.update({k: v for k, v in local.items() if k not in cache})
            return res
        except Exception as e:  # rate limits: keep what is done, report the rest
            errors.append(f"{items[i][0]} {unit['jurisdiction']}: {type(e).__name__}: {e}"[:300])
            return None

    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as pool:
        results = list(pool.map(run, jobs))
    cpath.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    rows = []
    for (i, unit, pages), res in zip(jobs, results):
        key, kind, ko, prod, comps = items[i]
        ag = unit["jurisdiction"]
        if res is None:
            continue
        for c in comps:
            if f"{c}|{ag}" not in Q["status"]:
                continue
            filt = None
            if kind == "product":
                fam = study_family(base[c]["study_key"])
                filt = lambda it, fam=fam: it["study"] in fam  # noqa: E731
            s = score(res, c, ag, base, Q, lab, prod, filt)
            rows.append({"question": key, "set": kind, "component": c, "agency": ag, "product": prod,
                         "study": base[c]["study_key"], "topic": base[c]["topic"], "ko": ko, **s,
                         "items": [{k: it[k] for k in ("study", "item", "source_label", "value", "exact", "evidence") if k in it} for it in res["items"]],
                         "answer_ko": res["answer_ko"],
                         "evidence": {e: f"{ev['document_id']}|{ev['pdf_page']}" for e, ev in res["evidence"].items()},
                         "prompt_tokens": res["prompt_tokens"], "completion_tokens": res["completion_tokens"]})

    out = RAG / f"eval/answers_{a.tag}.json"
    out.write_text(json.dumps({"prompt_version": PROMPT_VERSION, "errors": errors, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    review = RAG / f"eval/answers_review_{a.tag}.csv"
    with open(review, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question", "component", "agency", "gold_status", "retrieval_hit", "verdict", "value_recall", "gold_value",
                    "answer_values", "unsupported_items", "answer_ko", "evidence"])
        for r in rows:
            w.writerow([r["question"], r["component"], r["agency"], r["gold_status"], r["retrieval_hit"], r["verdict"], r["value_recall"],
                        r["gold_value"], " | ".join(f"{it['item']} [{it.get('source_label', '')}{'' if it.get('exact', True) else ', context'}]: {it['value']} {it['evidence']}" for it in r["items"]),
                        " || ".join(r["unsupported_items"]), r["answer_ko"], json.dumps(r["evidence"])])

    tok = (sum(r.get("prompt_tokens") or 0 for r in results if r), sum(r.get("completion_tokens") or 0 for r in results if r))
    print(f"units {len(jobs)}, answered {sum(r is not None for r in results)}, errors {len(errors)}, "
          f"{time.time() - t0:.0f}s, tokens in this run's results {tok}")
    for e in errors[:5]:
        print("  error:", e)
    print(f"\n{'set':9s} {'ag':3s} {'n':>3s} {'hit':>4s} {'match':>5s} {'part':>4s} {'nomat':>5s} {'text':>4s} {'abst+':>5s} {'abst-':>5s} "
          f"{'NR ok':>5s} {'NR val':>6s} {'unsup':>5s} {'cite2':>7s} {'othpg':>5s} {'othst':>5s}")
    g = defaultdict(list)
    for r in rows:
        g[(r["set"], r["agency"])].append(r)
    summary = []
    for k in sorted(g):
        sub = g[k]
        v = Counter(r["verdict"] for r in sub)
        n_items = sum(r["n_items"] for r in sub)
        s = {"set": k[0], "agency": k[1], "n": len(sub), "retrieval_hit": sum(r["retrieval_hit"] for r in sub), **v,
             "answers_with_unsupported_items": sum(bool(r["unsupported_items"]) for r in sub),
             "items": n_items, "items_cite_grade2": sum(r["items_cite_grade2"] for r in sub),
             "items_cite_other_study_page": sum(r["items_cite_other_study_page"] for r in sub),
             "items_other_study": sum(r["items_other_study"] for r in sub)}
        summary.append(s)
        print(f"{k[0]:9s} {k[1]:3s} {len(sub):3d} {s['retrieval_hit']:4d} {v['value_match']:5d} {v['review_partial_match']:4d} "
              f"{v['review_no_match']:5d} {v['review_text']:4d} {v['abstained_hit']:5d} {v['abstained_miss']:5d} {v['ok_not_found']:5d} "
              f"{v['review_not_reported']:6d} {s['answers_with_unsupported_items']:5d} {s['items_cite_grade2']:3d}/{n_items:<3d} "
              f"{s['items_cite_other_study_page']:5d} {s['items_other_study']:5d}")
    (RAG / f"eval/answers_summary_{a.tag}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nhit = grade-2 page among the evidence; match/part/nomat = value numbers all/some/none found; text = no numbers in the "
          "labels (manual); abst+/abst- = answered not found with/without a grade-2 page retrieved; NR = agency does not report it; "
          "unsup = answers with a value whose numbers do not occur together on any evidence page; cite2 = items citing a grade-2 page; othpg/othst = items citing "
          "other-study pages only / naming another study")


if __name__ == "__main__":
    main()
