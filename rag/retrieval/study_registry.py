#!/usr/bin/env python3
"""Study registry: the clinical studies, study parts and cross-study analyses of each product.

The registry itself is data/reviewed/studies.json (checked against the source documents). This module
loads it and finds study identifiers in text. Keys are the gold set's study_key values.

    uv run python -m rag.retrieval.study_registry                 # list the registry
    uv run python -m rag.retrieval.study_registry --text "..." --product CTP43
"""
import argparse
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "data/reviewed/studies.json"


@lru_cache(maxsize=1)
def load():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {s["study_key"]: s for s in data["studies"]}


@lru_cache(maxsize=1)
def products():
    """{product_id: {"names": [...], "ko_names": [...], "developer": ...}}."""
    return json.loads(REGISTRY.read_text(encoding="utf-8"))["products"]


@lru_cache(maxsize=None)
def _patterns(product_id):
    return [(k, re.compile(a, re.I)) for k, s in load().items() if s["product_id"] == product_id for a in s.get("aliases", [])]


def studies_of(product_id, kinds=("study", "study_part", "cross_study_analysis", "planned_study")):
    return [k for k, s in load().items() if s["product_id"] == product_id and s["kind"] in kinds]


def identifier_key(identifier, product_id):
    """Resolve a complete recorded identifier, never a semantic description. Ambiguity returns None."""
    text = re.sub(r"\s+", " ", identifier or "").strip()
    candidates = set()
    for key, study in load().items():
        if study["product_id"] != product_id:
            continue
        identifiers = [key, study.get("internal_id", ""), *study.get("registry_ids", {}).values()]
        if any(text.casefold() == re.sub(r"\s+", " ", value or "").strip().casefold() for value in identifiers if value):
            candidates.add(key)
    candidates |= {k for k, pat in _patterns(product_id) if pat.fullmatch(text)}
    return next(iter(candidates)) if len(candidates) == 1 else None


def find(text, product_id):
    """Study keys whose identifiers appear in the text, with match counts. A part's identifier also
    counts for its parent study, so "CT-P43 1.1 Part 2" gives both ST_CTP43_1_1_P2 and ST_CTP43_1_1."""
    found = {}
    for key, pat in _patterns(product_id):
        n = len(pat.findall(text or ""))
        if n:
            found[key] = found.get(key, 0) + n
    return found


def finditer(text, product_id):
    """(start, end, key) for each identifier in the text, in order. Where a part's identifier
    ("CT-P43 1.1 Part 2") overlaps its study's ("CT-P43 1.1"), only the longer, part-level match is kept."""
    found = sorted({(m.start(), m.end(), k) for k, pat in _patterns(product_id) for m in pat.finditer(text or "")},
                   key=lambda x: (x[0], -(x[1] - x[0])))
    out = []
    for s, e, k in found:
        if out and s < out[-1][1]:
            continue
        out.append((s, e, k))
    return out


def top(key):
    """The study a part belongs to; other keys unchanged."""
    s = load()[key]
    return s["parent"] if s["kind"] == "study_part" else key


def family(key):
    """Keys that answer for the given key: a study with its parts, or a part with its parent study."""
    reg = load()
    s = reg[key]
    if s["kind"] == "study_part":
        return {key, s["parent"]}
    return {key} | {k for k, x in reg.items() if x.get("parent") == key}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--text")
    p.add_argument("--product")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if a.text:
        print(find(a.text, a.product))
        return
    for k, s in load().items():
        print(f"{k:18s} {s['product_id']:7s} {s['kind']:21s} phase {s.get('phase', '-'):2s} {s.get('internal_id', '')}")


if __name__ == "__main__":
    main()
