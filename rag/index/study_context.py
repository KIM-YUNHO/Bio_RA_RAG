#!/usr/bin/env python3
"""Attach to each chunk the clinical studies it is about (docs/temp/rag/RAG_STUDY_CONTEXT_PLAN.md, step 2).

Regulatory reviews describe several studies in one document, and most pages never repeat the study
number. The chunks of a document are read in order and the study in force is carried along:

- A strong mention names the study as the subject: near the start of a sentence or line ("Study
  CT-P43 1.2 Methods", "In Study 20190230, ..."), or in a table or figure title. It sets the study
  carried to the next chunk.
  A short heading-like line that names a study counts as strong too.
- A weak mention sits inside a sentence ("... as in the second study (Study CT-P43 1.2)"). The study is
  added to the chunk but does not change the carried study.
- A new section heading clears the carried study; a heading that names a study starts with it.
  Sections that summarise the whole application (why approved, studies submitted, conclusions on
  biosimilarity, ...) take every study the document names.
- A chunk that names no study is checked against descriptions: healthy subjects, single dose, PK
  similarity point to phase 1 studies; plaque psoriasis, PASI, re-randomisation to phase 3; and each
  study's own cues from the registry ("Japanese" for CT-P43 1.2). Descriptions that clearly fit
  another study replace the carried one; descriptions that also fit add their studies.
- OCR'd table regions take the study named in their caption, else the studies of their page.

Keys come from the study registry (rag/retrieval/study_registry.py). This module does not change chunks.jsonl;
it returns the attribution, and --eval compares it with the manual page labels.

    uv run python -m rag.index.study_context --eval
"""
import argparse
import collections
import csv
import functools
import json
import re
import sys
from pathlib import Path

from rag.retrieval import study_registry as sr  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CAPTION = re.compile(r"^\s*(?:Table|Figure)\s+\d+", re.I)
SENTENCE_START = re.compile(r"(?:^|[.;:!?]\s+|\n)")
# Words allowed before a study number for the mention to count as the subject of its sentence.
MAX_PREFIX_WORDS = 5
# A line this short that names a study is a heading or a title.
MAX_TITLE_WORDS = 12
# Distinct description cues needed before descriptions decide.
MIN_CUES = 2
OVERVIEW_SECTION = re.compile(r"why .{0,40}approved|about the product|studies submitted|tabular overview|overview of (?:the )?clinical|"
                              r"results supporting biosimilarity|discussion on biosimilarity|conclusions on biosimilarity|benefit.{0,10}risk|"
                              r"summary of evidence|executive summary|recommended regulatory action|extrapolation|uncertainties|"
                              r"conclusions? on (?:the )?clinical|discussion on clinical safety", re.I)
# Text that opens a table or list of all the studies; the rest of its section is treated as an overview.
OVERVIEW_TEXT = re.compile(r"tabular overview|overview of (?:the )?clinical (?:studies|development|program)|clinical studies submitted|"
                           r"listing of (?:all )?(?:relevant )?(?:submitted )?clinical studies", re.I)
# Words between two study numbers that make them one list ("A, B and C").
LIST_GAP = re.compile(r"^[\s,;/&()]*(?:and|or|as well as|versus|vs\.?)?[\s,;/&()]*(?:stud(?:y|ies)\s*)?$", re.I)
PHASE_CUES = {
    "1": [r"healthy (?:adult |male |japanese |female )*(?:subjects|volunteers|men|males|participants)", r"single[- ]dose", r"\bPK similarity\b|\b(?:PK|pharmacokinetic) (?:similarity |equivalence )?stud(?:y|ies)\b",
          r"\bAUC\s?(?:inf|0-inf|last)", r"\bCmax\b"],
    "3": [r"plaque psoriasis|\bPsO\b|\bPs\b(?= patients| study)", r"\bPASI\b|\bsPGA\b|\bDLQI\b", r"re-?randomi[sz]",
          r"transition period|treatment period (?:I|II|1|2)\b|\bTP0?[12]\b|dose intensif", r"\bweek (?:12|16|28|40|52)\b"],
}
NONE_SECTION = re.compile(r"quality|non-?clinical|toxicolog|pharmacovigilance|risk management|risk minimi[sz]ation|safety concerns|product information|"
                          r"labell?ing|user consultation|manufactur|chemistry|stability|specification|adventitious|excipient|container|"
                          r"financial disclosure|pediatric|paediatric|signature|reviewers|glossary|abbreviation|table of contents|"
                          r"submission of the dossier|legal basis|steps taken|post-?market|REMS|inspection", re.I)
KIND_ORDER = {"native": 0, "ocr_page": 1, "ocr_region": 2}


def chunk_order(c):
    m = re.search(r":(\d{4}):([nro])(\d+)(?:_(\d+))?$", c["chunk_id"])
    return (c["pdf_page"], KIND_ORDER[c["source_kind"]], int(m.group(3)) if m else 0, int(m.group(4) or 0) if m else 0)


def mentions(text, product):
    """[(position, key, strong)] for the study identifiers in a chunk's text."""
    out, prev_end = [], None
    for s, e, k in sr.finditer(text, product):
        line_start = text.rfind("\n", 0, s) + 1
        line = text[line_start:text.find("\n", s) if text.find("\n", s) >= 0 else len(text)]
        if CAPTION.match(line):
            strong = True
        elif out and out[-1][2] and LIST_GAP.match(text[prev_end:s]):
            strong = True  # listed together with a study named as the subject
        else:
            starts = [m.end() for m in SENTENCE_START.finditer(text, 0, s)]
            prefix = text[max(starts) if starts else 0:s]
            strong = len(prefix.split()) <= MAX_PREFIX_WORDS or len(line.split()) <= MAX_TITLE_WORDS
        out.append((s, k, strong))
        prev_end = e
    return out


@functools.lru_cache(maxsize=None)
def cue_patterns(product):
    """{study key: ([own cues], [phase cues])}: registry cues of the study, and the cues shared by its phase.
    Study parts get only their own cues, so a phase-wide description never picks a part."""
    out = {}
    for k, st in sr.load().items():
        if st["product_id"] != product or st["kind"] not in ("study", "study_part"):
            continue
        own = [re.compile(re.escape(x), re.I) for x in st.get("cues", [])]
        phase = [] if st["kind"] == "study_part" else [re.compile(x, re.I) for x in PHASE_CUES.get(st.get("phase", ""), [])]
        out[k] = (own, phase)
    return out


def cue_candidates(text, product, allowed, own_only=False):
    """Studies (among those the document names) whose descriptions match at least MIN_CUES distinct cues.
    With own_only, a study also needs one cue of its own (one that tells it apart from its phase)."""
    reg = sr.load()
    phases = collections.Counter(reg[k].get("phase") for k in allowed if reg[k]["kind"] == "study")
    out = set()
    for k, (own, phase) in cue_patterns(product).items():
        if k not in allowed:
            continue
        n_own = sum(bool(p.search(text)) for p in own)
        n_phase = sum(bool(p.search(text)) for p in phase)
        # When a study is the only one of its phase in the document, its phase's cues tell it apart too.
        distinct = n_own or (n_phase and phases[reg[k].get("phase")] == 1)
        if n_own + n_phase >= MIN_CUES and (distinct or not own_only):
            out.add(k)
    return out


def attribute(chunks):
    """{chunk_id: {"studies": [...], "basis": ...}} for chunks of one product's documents, any order."""
    by_doc = collections.defaultdict(list)
    for c in chunks:
        by_doc[(c["document_id"], c.get("version_id"))].append(c)
    out = {}
    for doc, cs in by_doc.items():
        cs.sort(key=chunk_order)
        product = cs[0]["product_id"]
        reg = sr.load()
        named_in_doc = {k for c in cs for _, _, k in sr.finditer(c["text"], product)}
        named_in_doc |= {reg[k]["parent"] for k in named_in_doc if reg[k]["kind"] == "study_part"}
        main_studies = {k for k in named_in_doc if reg[k]["kind"] in ("study", "cross_study_analysis")}
        carry, last_section, overview = set(), None, False
        page_studies = collections.defaultdict(set)
        for c in cs:
            sec = c.get("section")
            if sec and sec != last_section:
                # A new section starts without a carried study, unless its heading names one.
                carry = {k for _, _, k in sr.finditer(sec, product)}
                overview = bool(OVERVIEW_SECTION.search(sec)) and not NONE_SECTION.search(sec)
            last_section = sec or last_section
            ms = mentions(c["text"], product)
            strong = [(p, k) for p, k, st in ms if st]
            weak = {k for _, k, st in ms if not st}
            if c["source_kind"] == "ocr_region":
                # Table regions are read after the page text, so they never change the carried study.
                # A region with no study in its caption belongs to the studies of its page.
                named = {k for _, k, _ in ms}
                studies = named or set(page_studies[c["pdf_page"]]) or set(carry)
                out[c["chunk_id"]] = {"studies": sorted(studies), "basis": "named" if named else ("page" if studies else "none")}
                continue
            if OVERVIEW_TEXT.search(c["text"]):
                overview = True
            studies, basis = set(), []
            first = strong[0][0] if strong else len(c["text"])
            if overview:
                studies |= main_studies; basis.append("overview")
            elif not strong:
                cand = cue_candidates(c["text"], product, named_in_doc)
                specific = cue_candidates(c["text"], product, named_in_doc, own_only=True)
                if carry and cand and not (carry & cand):
                    # The descriptions fit another study and not the carried one: switch.
                    carry = set(specific or cand); basis.append("described")
                elif carry and specific - carry:
                    # The carried study fits; another study is added only on cues of its own.
                    studies |= specific; basis.append("described")
                elif not carry and cand:
                    studies |= specific or cand; basis.append("described")
            elif first > 150:
                # Only the text before the line that names the next study can speak for the carried one.
                head = c["text"][:c["text"].rfind("\n", 0, first) + 1]
                cand = cue_candidates(head, product, named_in_doc)
                specific = cue_candidates(head, product, named_in_doc, own_only=True)
                if cand and not (carry & cand):
                    if carry:
                        carry = set(specific or cand)
                    else:
                        studies |= specific or cand
                    basis.append("described")
            if carry and first > 150:
                studies |= carry; basis.append("carried")
            if strong:
                studies |= {k for _, k in strong}; basis.append("named")
                # The studies named in the last sentence that names any are carried on.
                last_pos = strong[-1][0]
                sent_start = max([m.end() for m in SENTENCE_START.finditer(c["text"], 0, last_pos)] or [0])
                carry = {k for p, k in strong if p >= sent_start}
            if weak - studies:
                studies |= weak; basis.append("mentioned")
            out[c["chunk_id"]] = {"studies": sorted(studies), "basis": "+".join(basis) or "none"}
            page_studies[c["pdf_page"]] |= studies
    return out


def label(keys):
    """Header text for a chunk's studies, e.g. "Study CT-P43 1.2 (phase 1)"; parts replace their study."""
    reg = sr.load()
    keys = set(keys)
    keys -= {reg[k]["parent"] for k in keys if reg[k]["kind"] == "study_part"}
    names = []
    for k in sorted(keys, key=lambda k: (reg[k]["kind"] == "cross_study_analysis", reg[k].get("internal_id", k))):
        s = reg[k]
        if s["kind"] == "cross_study_analysis":
            names.append("pooled analysis of " + " and ".join(reg[c]["internal_id"] for c in s["covers"]))
        else:
            names.append(f"{s['internal_id']} (phase {s['phase']})" if s.get("phase") else s["internal_id"])
    if not names:
        return None
    return ("Study " if len(names) == 1 else "Studies ") + "; ".join(names)


def annotate(chunks):
    """Add heuristic study candidates. No-match means unknown, not proven absence of a study."""
    att = attribute(chunks)
    reg = sr.load()
    for c in chunks:
        a = att[c["chunk_id"]]
        tops = {sr.top(k) for k in a["studies"]}
        c["studies"] = a["studies"]
        c["study_basis"] = a["basis"]
        c["study_scope"] = "unknown" if not tops else "overview" if "overview" in a["basis"] else "multi" if len(tops) > 1 else "single"
        c["study_attribution_status"] = "heuristic_unverified"
        c["study_label"] = label(a["studies"])
    return chunks


def load_gold():
    gold = {}
    for r in csv.DictReader(open(ROOT / "eval/study_pages_v0.1.csv", encoding="utf-8")):
        st = set(filter(None, r["studies"].split(";")))
        for n in range(int(r["page_from"]), int(r["page_to"]) + 1):
            gold[(r["document_id"], n)] = (r["scope"], st)
    return gold


def evaluate(chunks, att):
    """Compare page-level attribution (union over a page's chunks) with the manual page labels."""
    gold = load_gold()
    pred = collections.defaultdict(set)
    for c in chunks:
        if (c["document_id"], c["pdf_page"]) in gold:
            pred[(c["document_id"], c["pdf_page"])] |= set(att[c["chunk_id"]]["studies"])
    T = lambda keys: {sr.top(k) for k in keys}
    prod = {c["document_id"]: c["product_id"] for c in chunks}
    res = collections.defaultdict(collections.Counter)
    errors = []
    for key, (scope, gst) in gold.items():
        if key not in pred and key[0] not in prod:
            continue
        p, g = T(pred.get(key, set())), T(gst)
        r = res[prod.get(key[0], "?")]
        r["pages"] += 1
        if not g:
            r["gold_none"] += 1
            r["gold_none_pred_none"] += not p
            continue
        r["gold_study"] += 1
        if p == g:
            r["exact"] += 1
        elif p & g:
            r["overlap"] += 1
        elif not p:
            r["missed"] += 1; errors.append((key, "missed", sorted(g), sorted(p)))
        else:
            r["wrong"] += 1; errors.append((key, "wrong", sorted(g), sorted(p)))

    # Answer pages: the page must carry the asked study (or its parent / parts).
    Q = {q["query_id"]: q for q in csv.DictReader(open(ROOT / "eval/queries_v0.1.csv", encoding="utf-8-sig")) if q["type"] == "retrieval"}
    qrels = json.loads((ROOT / "data/rag/eval/qrels_v0.1.json").read_text(encoding="utf-8"))["qrels"]
    ans = collections.Counter()
    ans_err = []
    for qa, rel in qrels.items():
        qid, ag = qa.split("|")
        if qid not in Q:
            continue
        sk = Q[qid]["study_key"]
        want = T(sr.family(sk) | set(sr.load()[sk].get("covers", [])))
        for page, v in rel.items():
            if v["grade"] != 2 or not v.get("from_manual"):
                continue
            d, n = page.rsplit("|", 1)
            key = (d, int(n))
            if key not in gold:
                continue
            p = T(pred.get(key, set()))
            ans["pages"] += 1
            if p & want:
                ans["ok"] += 1
            elif not p:
                ans["unattributed"] += 1; ans_err.append((qa, key, "unattributed"))
            else:
                ans["other_only"] += 1; ans_err.append((qa, key, f"other: {sorted(p)}"))
    return res, ans, errors, ans_err


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--eval", action="store_true")
    p.add_argument("--show", type=int, default=40, help="Errors to print")
    p.add_argument("--products", nargs="*", help="Print errors only for these products (rules are tuned on SB17 and ABP654)")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    chunks = [json.loads(x) for x in open(ROOT / "data/rag/chunks.jsonl", encoding="utf-8")]
    chunks = [c for c in chunks if c["product_id"] in {s["product_id"] for s in sr.load().values()}]
    att = attribute(chunks)
    basis = collections.Counter(v["basis"] for v in att.values())
    print("chunks:", len(att), dict(basis.most_common()))
    if not a.eval:
        return
    res, ans, errors, ans_err = evaluate(chunks, att)
    for prod, r in sorted(res.items()):
        g = r["gold_study"]
        print(f"{prod:7s} pages {r['pages']:4d} | study pages {g:4d}: exact {r['exact']:4d} ({r['exact']/g:.0%}) overlap {r['overlap']:3d} "
              f"wrong {r['wrong']:3d} missed {r['missed']:3d} | no-study pages {r['gold_none']:4d}, predicted none {r['gold_none_pred_none']:4d}")
    print(f"answer pages {ans['pages']}: ok {ans['ok']} ({ans['ok']/ans['pages']:.1%}), unattributed {ans['unattributed']}, other study only {ans['other_only']}")
    show = lambda key: not a.products or any(key[0].startswith(x) or True for x in []) or True
    prods = {c["document_id"]: c["product_id"] for c in chunks}
    sel = [e for e in errors if not a.products or prods.get(e[0][0]) in a.products]
    for e in sel[: a.show]:
        print("  page", e)
    for e in [x for x in ans_err if not a.products or prods.get(x[1][0]) in a.products][: a.show]:
        print("  answer", e)


if __name__ == "__main__":
    main()
