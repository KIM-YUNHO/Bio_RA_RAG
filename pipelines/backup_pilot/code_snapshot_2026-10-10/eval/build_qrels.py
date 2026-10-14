#!/usr/bin/env python3
"""Build per-agency relevance labels (qrels) and per-question answer status from the manual labels.

All labels were judged by reading the documents (not by an LLM):
- EU and US: eval/labels_manual_v0.1.csv, one row per question x page (grade 2 = answers, 1 = related,
  partial = answers only part of the question). Pages with the same page_hash (e.g. the FDA Orig1 and
  Orig2 reviews of the same product) get the same label unless labelled themselves.
- CA: eval/labels_ca_v0.1.csv, one row per question with the answering and related SBD sections.
Pooled pages without a label count as grade 0. The earlier gpt-4o-mini judgments are not used.

Status per question and agency: "reported" if a page answers it in full (or the answer is split over
pages, see STATUS_OVERRIDES), "partial" if pages answer only part of it, "not_reported" otherwise.
"""
import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENCIES = ["EU", "US", "CA"]
# The answer is complete only when two partial pages are read together.
STATUS_OVERRIDES = {"Q006|US": ("reported", "ADA assay on p54, NAb assay on p55")}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    queries = {q["query_id"]: q for q in csv.DictReader(open(ROOT / "eval/queries_v0.1.csv", encoding="utf-8-sig")) if q["type"] == "retrieval"}
    hash_of, pages_of = {}, collections.defaultdict(set)
    for line in open(ROOT / "data/rag/chunks.jsonl", encoding="utf-8"):
        c = json.loads(line)
        key = f"{c['document_id']}|{c['pdf_page']}"
        if c.get("page_hash") and key not in hash_of:
            hash_of[key] = c["page_hash"]
            pages_of[c["page_hash"]].add(key)

    qrels = collections.defaultdict(dict)
    for r in csv.DictReader(open(ROOT / "eval/labels_manual_v0.1.csv", encoding="utf-8")):
        page = f"{r['document_id']}|{r['pdf_page']}"
        qrels[f"{r['query_id']}|{r['agency']}"][page] = {"grade": int(r["grade"]), "partial": r["partial"] == "1",
                                                          "value": r["value"], "note": r["note"], "from_manual": True}
    twins = 0
    for qa, rel in qrels.items():
        for page, v in list(rel.items()):
            for twin in pages_of.get(hash_of.get(page), ()):
                if twin not in rel:
                    rel[twin] = {**v, "from_manual": False, "twin_of": page}
                    twins += 1

    # Canada: the three SBDs are short, so every section was read (one row per question).
    status = {}
    for r in csv.DictReader(open(ROOT / "eval/labels_ca_v0.1.csv", encoding="utf-8-sig")):
        qa = f"{r['query_id']}|CA"
        for grade, field in ((2, "sections_answer"), (1, "sections_related")):
            for sec in filter(None, r[field].split(";")):
                qrels[qa][f"{r['document_id']}|{sec}"] = {"grade": grade, "partial": r["status"] == "partial",
                                                          "value": r["value_ca"] if grade == 2 else "", "note": r["note"], "from_manual": True}
        status[qa] = {"status": r["status"], "note": r["note"]}

    for qid in queries:
        for a in ("EU", "US"):
            qa = f"{qid}|{a}"
            rel = qrels.get(qa, {}).values()
            st = "reported" if any(v["grade"] == 2 and not v["partial"] for v in rel) else "partial" if any(v["grade"] == 2 for v in rel) else "not_reported"
            status[qa] = {"status": st}
            if qa in STATUS_OVERRIDES:
                status[qa] = {"status": STATUS_OVERRIDES[qa][0], "note": STATUS_OVERRIDES[qa][1]}
    for qa, v in status.items():
        v["relevant_pages"] = sum(1 for x in qrels.get(qa, {}).values() if x["grade"] == 2)

    # Pages of the earlier EU gold set (eval/build_queries.py) that the manual labels do not grade 2.
    gold_diff = []
    for qid, q in queries.items():
        for pg in json.loads(q["acceptable_pages"]):
            page = f"{q['document_id']}|{pg}"
            g = qrels.get(f"{qid}|EU", {}).get(page, {}).get("grade", 0)
            if g != 2:
                gold_diff.append({"query_id": qid, "page": page, "manual_grade": g})

    summary = {"labelled_pairs": sum(1 for rel in qrels.values() for v in rel.values() if v["from_manual"]),
               "twin_pages_added": twins, "eu_gold_pages_not_grade2": len(gold_diff)}
    for a in AGENCIES:
        st = [v for k, v in status.items() if k.endswith("|" + a)]
        summary[a] = dict(collections.Counter(v["status"] for v in st))
        ans = [v["relevant_pages"] for v in st if v["status"] in ("reported", "partial")]
        summary[a]["grade2_pages_per_answered_question"] = round(sum(ans) / len(ans), 1) if ans else 0
    out = {"summary": summary, "qrels": qrels, "status": status, "eu_gold_diff": gold_diff}
    (ROOT / "data/rag/eval/qrels_v0.1.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for d in gold_diff:
        print("gold page not graded 2:", d)


if __name__ == "__main__":
    main()
