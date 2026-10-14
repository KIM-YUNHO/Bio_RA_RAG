#!/usr/bin/env python3
"""Compare study attribution: current rules (rag/study_context.py) vs a registry-constrained LLM schema.

Experiment only; nothing in data/rag/chunks.jsonl changes. Units are the active chunks of one document,
so the only difference between the two methods is how a chunk is attributed. The LLM sees the registry,
the section heading, the last sentences before the chunk that name a study, the tail of the previous
chunk and the chunk itself. It returns each study with a role (subject / comparison / mentioned /
excluded / pooled_component) and a verbatim quote; quotes are checked against the text it was given.

Scored against eval/study_pages_v0.1.csv (page-level labels of the studies a page is about; passing
mentions are not labelled). Rules were tuned on SB17 and ABP654, so CT-P43 is the less-fitted product.

    uv run python eval/compare_attribution.py                 # local Ollama (no external API)
    uv run python eval/compare_attribution.py --score-only    # re-score the saved LLM outputs
"""
import argparse
import collections
import csv
import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "rag"))
import study_context  # noqa: E402
import study_registry as sr  # noqa: E402
from evidence import contains_quote  # noqa: E402
from llm import chat  # noqa: E402

DOC = "EMA:64792"
PRODUCT = "CTP43"
# Mixed-study clinical pharmacology (29-56) plus quality/non-clinical (26-28) and RMP (121-124) as negatives.
PAGES = list(range(26, 57)) + list(range(121, 125))
OUT = ROOT / "data/rag/eval"
RAW = OUT / "attribution_llm_raw_2026-10-06.jsonl"
REPORT = OUT / "attribution_compare_2026-10-06.json"
PROMPT_VERSION = "attrib-2"
SENT = re.compile(r"(?<=[.;:])\s+|\n")
ROLES = ["subject", "comparison", "mentioned", "excluded", "pooled_component"]

SYSTEM = """You attribute one passage of a regulatory assessment report to the clinical studies it is about.
Use only the registry keys given. Judge from the passage and the context; do not guess.

CRITICAL RULES:
- Only list clinical studies that are actually discussed, evaluated, or named in this passage (or its preceding context).
- If the passage is NOT about clinical studies (e.g. non-clinical/animal studies, toxicology, quality/CMC, legal, RMP, general labeling), "studies" MUST be an empty array [] and "scope" must be "none". Do NOT list clinical studies as "excluded" when they are merely unmentioned.
- "quote" MUST be very short (3 to 15 words) snippet proving the study's presence. Never copy entire sentences or paragraphs.
- "excluded" role is strictly for when the text explicitly states a clinical study is excluded or disregarded.

For each study involved, give a role:
- subject: the passage reports this study's design, conduct, results, or the assessor's discussion of it. A passage that continues the description of a study named earlier (preceding context) still has that study as subject.
- comparison: named only to contrast or compare with the subject ("unlike Study X", "similar to Study X").
- mentioned: named in passing, as a cross-reference, or in a list, without its content being reported here.
- excluded: explicitly stated not to apply or not discussed in this clinical context.
- pooled_component: a study whose data feed a pooled analysis that is the subject.
A part (e.g. Part 2) is the subject when the passage is about that part only; use the parent study when it covers the whole study.
When the passage summarises or tabulates the whole clinical programme, list each study it covers as subject and set scope "overview".

basis: "named_in_passage" (the passage names it), "preceding_context" (only the context identifies it), or "description" (the passage describes it without naming it, e.g. "Japanese healthy subjects").
quote: copy 3 to 15 consecutive words exactly from the passage (or from the context when basis is preceding_context) that support the attribution.
scope: single | multi (several studies reported) | overview | none (no clinical study: quality, non-clinical, procedure, risk management, labelling) | unknown.
analysis: primary | sensitivity_or_post_hoc | pooled | not_applicable | unknown.
endpoints: PK/efficacy/safety endpoints the passage reports values for, copied as written (e.g. "AUC0-inf", "Cmax"); [] if none.
comparator: the reference product as written (e.g. "EU-Stelara"), or "" if none."""


def schema(keys):
    return {"name": "attribution", "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["studies", "scope", "analysis", "endpoints", "comparator"],
        "properties": {
            "studies": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["study_key", "role", "basis", "quote"],
                "properties": {
                    "study_key": {"type": "string", "enum": keys},
                    "role": {"type": "string", "enum": ROLES},
                    "basis": {"type": "string", "enum": ["named_in_passage", "preceding_context", "description"]},
                    "quote": {"type": "string"},
                }}},
            "scope": {"type": "string", "enum": ["single", "multi", "overview", "none", "unknown"]},
            "analysis": {"type": "string", "enum": ["primary", "sensitivity_or_post_hoc", "pooled", "not_applicable", "unknown"]},
            "endpoints": {"type": "array", "items": {"type": "string"}},
            "comparator": {"type": "string"},
        }}}


def registry_block():
    lines = []
    for k in sr.studies_of(PRODUCT):
        s = sr.load()[k]
        bits = [f"{k}: {s['kind']}", s.get("internal_id", "")]
        if s.get("phase"):
            bits.append(f"phase {s['phase']}")
        if s.get("parent"):
            bits.append(f"part of {s['parent']}")
        if s.get("covers"):
            bits.append("pools " + " + ".join(s["covers"]))
        for f in ("population", "purpose"):
            if s.get(f):
                bits.append(s[f])
        lines.append(" | ".join(bits))
    return "\n".join(lines)


def build_units(chunks):
    """Units in document order with the context the LLM sees."""
    chunks = sorted(chunks, key=study_context.chunk_order)
    named_sents, units, prev = [], [], None
    for c in chunks:
        if c["pdf_page"] in PAGES:
            units.append({"chunk": c, "section": c.get("section") or "",
                          "preceding": named_sents[-3:], "prev_tail": (prev["text"][-500:] if prev else "")})
        if c["source_kind"] == "native":
            for s in SENT.split(c["text"]):
                if sr.finditer(s, PRODUCT) and len(s.split()) >= 3:
                    named_sents.append(f"p.{c['pdf_page']}: {s.strip()[:300]}")
            prev = c
    return units


def user_message(u, registry, text=None, page=None, kind=None):
    c = u.get("chunk") or {}
    pre = "\n".join(u["preceding"]) or "(none)"
    return (f"Registry ({PRODUCT}):\n{registry}\n\nSection heading: {u['section'] or '(none)'}\n\n"
            f"Last sentences before this passage that name a study:\n{pre}\n\n"
            f"End of the previous passage:\n{u['prev_tail'] or '(none)'}\n\n"
            f"Passage (p.{page or c.get('pdf_page')}, {kind or c.get('source_kind')}):\n{text or c['text']}")


def validate(item, u, text):
    context = "\n".join([u["section"], *u["preceding"], u["prev_tail"]])
    src = context if item["basis"] == "preceding_context" else text
    return contains_quote(src, item["quote"])


def run_llm(units, registry, keys, done):
    out = []
    with open(RAW, "a", encoding="utf-8") as f:
        for n, u in enumerate(units, 1):
            cid = u["chunk"]["chunk_id"]
            msg = user_message(u, registry)
            fp = hashlib.sha256((PROMPT_VERSION + SYSTEM + msg).encode()).hexdigest()[:16]
            if done.get(cid, {}).get("fingerprint") == fp:
                out.append(done[cid]); continue
            t = time.time()
            r, parsed, err = {}, None, None
            try:
                r = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": msg}], json_schema=schema(keys), provider="ollama")
                parsed = json.loads(r["text"])
            except Exception as e:
                err = repr(e)
            rec = {"chunk_id": cid, "fingerprint": fp, "prompt_version": PROMPT_VERSION, "parsed": parsed, "error": err,
                   "model": r.get("model_reported"), "prompt_tokens": r.get("prompt_tokens"),
                   "completion_tokens": r.get("completion_tokens"), "seconds": round(time.time() - t, 1)}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
            out.append(rec)
            print(f"[{n}/{len(units)}] {cid} {rec['seconds']}s {'ERR ' + err if err else ''}", flush=True)
            if len(out) >= 3 and all(x.get("error") for x in out[-3:]):
                raise SystemExit("3 consecutive LLM failures; stopping.")
    return out


def load_gold():
    gold = {}
    for r in csv.DictReader(open(ROOT / "eval/study_pages_v0.1.csv", encoding="utf-8")):
        if r["document_id"] != DOC:
            continue
        for p in range(int(r["page_from"]), int(r["page_to"]) + 1):
            gold[p] = (r["scope"], set(filter(None, r["studies"].split(";"))), r["note"])
    return gold


def score(pred_by_page, gold, level):
    T = (lambda ks: {sr.top(k) for k in ks}) if level == "study" else (lambda ks: set(ks))
    r = collections.Counter()
    for p in PAGES:
        scope, g, _ = gold[p]
        pr, gg = T(pred_by_page.get(p, set())), T(g)
        if not gg:
            r["none_pages"] += 1; r["none_pred_none"] += not pr; continue
        r["study_pages"] += 1
        r["exact"] += pr == gg
        r["overlap_only"] += bool(pr & gg) and pr != gg
        r["missed"] += not pr
        r["wrong_only"] += bool(pr) and not (pr & gg)
        r["extra_keys"] += len(pr - gg)
        r["missing_keys"] += len(gg - pr)
    return dict(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score-only", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    chunks = [json.loads(x) for x in open(ROOT / "data/rag/chunks.jsonl", encoding="utf-8")]
    doc_chunks = [c for c in chunks if c["document_id"] == DOC]
    rule = study_context.attribute(doc_chunks)  # current rule code over the whole document (carry needs order)
    units = build_units(doc_chunks)
    registry, keys = registry_block(), sr.studies_of(PRODUCT)
    done = {}
    if RAW.exists():
        for line in RAW.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("parsed") is not None:
                done[rec["chunk_id"]] = rec
    llm = [done.get(u["chunk"]["chunk_id"]) for u in units] if a.score_only else run_llm(units, registry, keys, done)

    gold = load_gold()
    pages = {"rule": collections.defaultdict(set), "llm_subject": collections.defaultdict(set),
             "llm_subject_valid": collections.defaultdict(set), "llm_any_role": collections.defaultdict(set)}
    rows, quote_ok, quote_n, roles = [], 0, 0, collections.Counter()
    for u, rec in zip(units, llm):
        c = u["chunk"]; p = c["pdf_page"]
        rs = set(rule[c["chunk_id"]]["studies"])
        pages["rule"][p] |= rs
        items = (rec or {}).get("parsed", {}) or {}
        studies = items.get("studies", [])
        for it in studies:
            it["quote_valid"] = validate(it, u, c["text"])
            quote_n += 1; quote_ok += it["quote_valid"]; roles[it["role"]] += 1
            pages["llm_any_role"][p].add(it["study_key"])
            if it["role"] == "subject":
                pages["llm_subject"][p].add(it["study_key"])
                if it["quote_valid"]:
                    pages["llm_subject_valid"][p].add(it["study_key"])
        subj = sorted({it["study_key"] for it in studies if it["role"] == "subject"})
        rows.append({"chunk_id": c["chunk_id"], "page": p, "kind": c["source_kind"], "section": u["section"],
                     "gold_page": sorted(gold[p][1]), "gold_note": gold[p][2],
                     "rule": sorted(rs), "rule_basis": rule[c["chunk_id"]]["basis"], "llm_subject": subj,
                     "llm": items, "error": (rec or {}).get("error"), "text_head": c["text"][:240]})

    metrics = {m: {lvl: score(pp, gold, lvl) for lvl in ("study", "part")} for m, pp in pages.items()}
    secs = [r["seconds"] for r in llm if r and r.get("seconds")]
    report = {"prompt_version": PROMPT_VERSION, "document": DOC, "pages": PAGES, "units": len(units),
              "llm_failures": sum(1 for r in llm if not r or r.get("parsed") is None),
              "quote_valid": f"{quote_ok}/{quote_n}", "roles": dict(roles),
              "seconds_total": round(sum(secs), 1), "metrics": metrics,
              "page_view": {p: {"gold": sorted(gold[p][1]), "note": gold[p][2],
                                **{m: sorted(pp.get(p, set())) for m, pp in pages.items()}} for p in PAGES},
              "chunks": rows}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("units", "llm_failures", "quote_valid", "roles", "seconds_total", "metrics")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
