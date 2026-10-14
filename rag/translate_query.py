#!/usr/bin/env python3
"""Turn a Korean question into an English search query.

Two modes:
  plain    - the LLM translates with no help.
  glossary - glossary entries found in the question are given to the LLM, abbreviations, numbers,
             units and study numbers must come through unchanged, and the result is checked.
The output is a search query, not an answer: nothing here looks at the documents.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm import chat  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# translate-1 asked the glossary mode for a compact keyword query; it dropped the parts of a question the
# glossary did not cover (measured 2026-10-03), so translate-2 asks for the whole question.
# translate-3: same instructions as translate-2; the glossary no longer lists answer values (0.8 to 1.25) as variants.
PROMPT_VERSION = {"plain": "translate-1", "glossary": "translate-3"}
# Latin-script terms (abbreviations, product codes, study numbers) and numbers with their units.
PROTECTED = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*|\d+(?:[.,]\d+)*\s?(?:%|mg|mL|kg|ng/mL)?")

SCHEMA = {"name": "search_query", "schema": {
    "type": "object",
    "properties": {"english_query": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}}},
    "required": ["english_query", "keywords"], "additionalProperties": False}}

SYSTEM_PLAIN = (
    "You translate a Korean question about biosimilar regulatory documents into one English search query. "
    "Return the query in english_query and its 3 to 8 most important search terms in keywords. Do not answer the question.")

SYSTEM_GLOSSARY = (
    "You turn a Korean question about biosimilar regulatory documents (EMA and FDA assessment reports, study protocols) "
    "into one English question used for search. Rules:\n"
    "1. Translate the whole question into one natural English question. Keep every part of what is asked; do not drop or summarise anything, and do not answer it.\n"
    "2. Copy every Latin-script term, number, unit, product code and study number exactly as written. Do not expand, translate or reformat them.\n"
    "3. Use the glossary's English term for each Korean term it lists.\n"
    "4. Put 3 to 8 search terms in keywords: the terms that name what is asked, plus the glossary variants that documents may use instead.")


def load_glossary():
    with (ROOT / "rag/glossary.csv").open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    # Longest Korean term first, so a phrase wins over a word it contains.
    return sorted(rows, key=lambda r: -len(r["ko"]))


def glossary_hits(question, glossary):
    hits, rest = [], question
    for r in glossary:
        if r["ko"] in rest:
            hits.append(r)
            rest = rest.replace(r["ko"], " ")
    return hits


def protected_terms(question):
    out = []
    for m in PROTECTED.finditer(question):
        t = m.group(0).strip()
        if len(t) >= 2 and t not in out:
            out.append(t)
    return out


def missing_terms(terms, english):
    squeeze = lambda s: re.sub(r"[\s]", "", s).lower()
    text = squeeze(english)
    return [t for t in terms if squeeze(t) not in text]


def translate(question, mode="glossary", glossary=None, provider=None):
    """Return {english_query, keywords, protected, missing, glossary_hits, attempts, model, tokens}."""
    terms = protected_terms(question)
    if mode == "plain":
        r = chat([{"role": "system", "content": SYSTEM_PLAIN}, {"role": "user", "content": question}], json_schema=SCHEMA, provider=provider)
        out = json.loads(r["text"])
        return {**out, "protected": terms, "missing": missing_terms(terms, out["english_query"]), "glossary_hits": [], "attempts": 1,
                "model": r["model_reported"], "prompt_tokens": r["prompt_tokens"], "completion_tokens": r["completion_tokens"]}
    hits = glossary_hits(question, glossary if glossary is not None else load_glossary())
    lines = [f"- {h['ko']} = {h['en_canonical']}" + (f" (variants: {h['en_variants'].replace(';', ', ')})" if h["en_variants"] else "") for h in hits]
    user = (f"Question: {question}\n\nCopy exactly: {', '.join(terms) if terms else '(none)'}\n\n"
            f"Glossary:\n{chr(10).join(lines) if lines else '(no entries matched)'}")
    messages = [{"role": "system", "content": SYSTEM_GLOSSARY}, {"role": "user", "content": user}]
    tokens = [0, 0]
    for attempt in (1, 2):
        r = chat(messages, json_schema=SCHEMA, provider=provider)
        tokens[0] += r["prompt_tokens"] or 0
        tokens[1] += r["completion_tokens"] or 0
        out = json.loads(r["text"])
        missing = missing_terms(terms, out["english_query"])
        if not missing:
            break
        messages += [{"role": "assistant", "content": r["text"]},
                     {"role": "user", "content": f"These terms must appear exactly as written: {', '.join(missing)}. Return the corrected JSON."}]
    return {**out, "protected": terms, "missing": missing, "glossary_hits": [h["ko"] for h in hits], "attempts": attempt,
            "model": r["model_reported"], "prompt_tokens": tokens[0], "completion_tokens": tokens[1]}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--query", required=True, help="Korean question")
    p.add_argument("--mode", choices=["plain", "glossary"], default="glossary")
    p.add_argument("--provider", choices=["openai", "ollama"], help="Override LLM_PROVIDER for this run")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(translate(a.query, a.mode, provider=a.provider), ensure_ascii=False, indent=2))
