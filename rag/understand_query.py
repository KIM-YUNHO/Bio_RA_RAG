#!/usr/bin/env python3
"""Interpret a Korean question before retrieval: intent, search scope and an English search query.

This is the single entry point for questions (docs/temp/rag/RAG_STUDY_CONTEXT_PLAN.md 8.2 다). One LLM call
returns the question type, the products, studies, jurisdictions and document groups to search, the
topic, and the English query (glossary rules as in translate_query.py, which is kept only as a
baseline). Products and studies come from the study registry (data/reviewed/studies.json): the model
picks registry keys and quotes the question to explain each entity's role. Code checks identifiers
and quotations without replacing semantic choices with keyword cues. rag/plan_query.py turns the result into searches. Nothing here reads the documents, so it
cannot tell whether an answer exists.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study_registry as sr  # noqa: E402
from llm import chat, settings  # noqa: E402
from rag.evidence import contains_quote  # noqa: E402
from translate_query import glossary_hits, load_glossary, missing_terms, protected_terms  # noqa: E402

# understand-2: glossary without answer values, and variants added by code (search_terms).
# understand-3: products and studies from the study registry; studies returned as registry keys; Korean
#   product names and study numbers in the question are also resolved in code.
PROMPT_VERSION = "understand-7"
INTENTS = ["fact_lookup", "compare_products", "compare_jurisdictions", "regulatory_reasoning", "document_info", "out_of_scope"]
JURISDICTIONS = ["EU", "US", "CA", "JP", "sponsor"]
DOC_GROUPS = ["assessment", "decision_summary", "label", "quality", "other_review", "correspondence", "rmp", "protocol", "sap"]
TOPICS = ["design", "population", "dosing", "endpoints", "equivalence_margin", "assay", "n_subjects", "immunogenicity",
          "pk_results", "equivalence_conclusion", "efficacy", "safety", "quality", "labeling", "regulatory_history", "other"]


def _product_lines():
    out = []
    for pid, p in sr.products().items():
        out.append(f"- {pid} = {' = '.join(p['names'])} ({', '.join(p['ko_names'])}), {p['developer']}")
    return "\n".join(out)


def _study_lines():
    out = []
    for k, s in sr.load().items():
        desc = "; ".join(x for x in (f"phase {s['phase']}" if s.get("phase") else "", s.get("population", ""), s.get("purpose", "")) if x)
        ko = f" [{', '.join(s['ko_names'])}]" if s.get("ko_names") else ""
        out.append(f"- {k}: {s['product_id']} {s.get('internal_id', '')} ({s['kind']}) - {desc}{ko}")
    return "\n".join(out)


SCHEMA = {"name": "query_understanding", "schema": {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "products": {"type": "array", "items": {"type": "string", "enum": list(sr.products())}},
        "studies": {"type": "array", "items": {"type": "string", "enum": list(sr.load())}},
        "study_scope": {"type": "string", "enum": ["specified", "unspecified", "ambiguous"]},
        "study_mentions": {"type": "array", "items": {
            "type": "object", "properties": {
                "study_key": {"type": "string", "enum": list(sr.load())},
                "role": {"type": "string", "enum": ["target", "component", "context", "excluded"]},
                "quote": {"type": "string"}},
            "required": ["study_key", "role", "quote"], "additionalProperties": False}},
        "jurisdictions": {"type": "array", "items": {"type": "string", "enum": JURISDICTIONS}},
        "jurisdiction_mentions": {"type": "array", "items": {
            "type": "object", "properties": {
                "jurisdiction": {"type": "string", "enum": JURISDICTIONS},
                "role": {"type": "string", "enum": ["regulator", "comparator", "context"]},
                "quote": {"type": "string"}},
            "required": ["jurisdiction", "role", "quote"], "additionalProperties": False}},
        "doc_groups": {"type": "array", "items": {"type": "string", "enum": DOC_GROUPS}},
        "topic": {"type": "string", "enum": TOPICS},
        "english_query": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intent", "products", "studies", "study_scope", "study_mentions", "jurisdictions", "jurisdiction_mentions", "doc_groups", "topic", "english_query", "keywords"],
    "additionalProperties": False}}

SYSTEM = f"""You interpret a Korean question for a search system over public regulatory documents on the registered products below. Do not answer the question. Registry descriptions identify studies; never use their numerical results as answers or query terms.

Products (use the code):
{_product_lines()}
Stelara (스텔라라, ustekinumab) is the reference product, not one of the products.

Studies (use the key):
{_study_lines()}
Put a study in studies only when the question names it or describes it clearly enough to pick it out using the registry. Consider the entire sentence, including negation, comparisons, ordinal descriptions, parts and analyses spanning studies. A study number may be a component of a pooled analysis or contextual comparison rather than the target. Do not replace a pooled target with its components. Do not collapse a specifically requested part into its parent.
study_scope: specified for identifiable targets, unspecified when no particular study is requested, ambiguous when several interpretations remain. For ambiguous or unspecified scope, leave studies empty and search broadly.
study_mentions: identify the relevant studies and their roles (target, component of an analysis, context, excluded). These roles are the authoritative semantic selection. studies must list exactly the target keys, never component or context keys. quote must be a verbatim phrase from the Korean question, including enough surrounding text to support the role; it may describe a study without naming its identifier. Do not translate, rewrite or expand an abbreviated study identifier inside a quote. When a phase applies to multiple products, consider every requested product independently; do not select only the product with a unique phase.

Jurisdictions: EU (EMA), US (FDA), CA (Health Canada), JP (PMDA), sponsor (ClinicalTrials.gov or sponsor protocols/SAP). Select only document sources the question requests. A reference product's sourcing region is a comparator, not a document jurisdiction. jurisdiction_mentions assigns regulator/comparator/context roles, with verbatim quotations from the question; regulator means requested document source, including sponsor. Every selected jurisdiction requires a regulator mention. Leave jurisdictions empty when no source is specified. Distinguish an agency from a study's recruitment country.

Document groups to search:
- assessment: regulators' assessment reports (EMA EPAR, FDA multi-discipline review, Health Canada Summary Basis of Decision). Default for questions about study design, PK, immunogenicity, efficacy, safety results and regulators' conclusions.
- decision_summary: short decision summaries and post-authorisation procedural steps.
- label: product information, prescribing information, product monograph (indications, posology, label text).
- quality: manufacturing and product quality reviews.
- other_review: other FDA reviews (labeling, human factors, pediatric).
- correspondence: approval letters, other action letters, administrative correspondence (approval dates, provisional determinations).
- rmp: EU risk management plans.
- protocol, sap: sponsor study protocols and statistical analysis plans (planned design, planned statistical methods).
Choose every group that could contain the answer.

Intent:
- fact_lookup: one product, one fact or result.
- compare_products: the same item across two or more products.
- compare_jurisdictions: how agencies differ for a product.
- regulatory_reasoning: why a regulator concluded or required something.
- document_info: dates, versions, which documents exist.
- out_of_scope: not about these products or their public documents, asks for predictions or recommendations, or asks to reveal redacted content.
Questions about a document's tables or figures, including their printed title,
axes, units, legend, caption and footnotes, are in scope. Use fact_lookup for
their printed contents, or document_info for source identification. A visual
question does not require digitising plotted curves and is not out_of_scope.

english_query: translate the whole question into one natural English question. Keep every part of what is asked. Write product names in English with their code (for example "Steqeyma (CT-P43)"). Copy Latin-script terms, numbers, units and study numbers exactly. Use the glossary's English terms.
keywords: 3 to 8 search terms that name what is asked, plus glossary variants documents may use instead."""


def studies_in_question(question, product, model_studies):
    """Compatibility helper: validate registry membership, preserving the model's semantic choices."""
    return list(dict.fromkeys(k for k in model_studies if k in sr.load() and sr.load()[k]["product_id"] == product))


def resolve_jurisdictions(question, jurisdictions, mentions=None):
    """Validate agency codes and, for new outputs, grounded regulator roles."""
    valid = [j for j in dict.fromkeys(jurisdictions) if j in JURISDICTIONS]
    if mentions is None:  # old evaluation caches remain readable without claiming new grounding
        return valid
    grounded = {m.get("jurisdiction") for m in mentions if m.get("role") == "regulator"
                and contains_quote(question, m.get("quote"))}
    return [j for j in valid if j in grounded]


def resolve(question, out):
    """Check scope integrity; never decide sentence meaning from substrings or phase/topic rules."""
    products = [p for p in dict.fromkeys(out["products"]) if p in sr.products()]
    studies = [k for k in dict.fromkeys(out["studies"]) if k in sr.load()]
    warnings = []
    scope = out.get("study_scope", "specified" if studies else "unspecified")
    if "study_mentions" in out:
        targets = [m for m in out["study_mentions"] if m.get("role") == "target"]
        grounded = list(dict.fromkeys(m.get("study_key") for m in targets if m.get("study_key") in sr.load()
                                    and contains_quote(question, m.get("quote"))))
        excluded = {m.get("study_key") for m in out["study_mentions"] if m.get("role") == "excluded"
                    and contains_quote(question, m.get("quote"))}
        if any(m.get("study_key") not in sr.load() or not contains_quote(question, m.get("quote")) for m in targets) or set(grounded) & excluded:
            warnings.append("ungrounded_study_scope: broad search retained")
            studies, scope = [], "ambiguous"
        else:
            if studies != grounded:
                warnings.append("study_selection_aligned_with_grounded_target_roles")
            studies = grounded
        if scope != "specified":
            studies = []
    for k in studies:
        prod = sr.load()[k]["product_id"]
        if prod not in products:
            products.append(prod)
    jurisdictions = resolve_jurisdictions(question, out["jurisdictions"], out.get("jurisdiction_mentions"))
    if jurisdictions != list(dict.fromkeys(out["jurisdictions"])):
        warnings.append("ungrounded_jurisdiction_scope: broad search retained")
    return {**out, "products": products, "studies": studies, "study_scope": scope,
            "jurisdictions": jurisdictions, "scope_warnings": warnings,
            "scope_validation": "grounded_roles" if "study_mentions" in out else "legacy_unverified",
            "scope_resolver_version": "scope-3"}


def resolve_cached(question, cached):
    raw = cached.get("model_interpretation", {**cached, "studies": cached.get("studies_from_model", cached["studies"])})
    return {**cached, **resolve(question, raw)}


def cache_fingerprint(question):
    payload = [PROMPT_VERSION, SYSTEM, SCHEMA, sr.REGISTRY.read_text(encoding="utf-8"),
               (sr.ROOT / "rag/glossary.csv").read_text(encoding="utf-8"), question]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def cache_path(provider=None):
    s = settings(provider)
    name = re.sub(r"[^A-Za-z0-9.]+", "-", f"{s['provider']}_{s['model']}")
    return sr.ROOT / "data/rag/cache" / f"understanding_{PROMPT_VERSION}_{name}.json"


def understand(question, glossary=None, provider=None, chat_client=None):
    terms = protected_terms(question)
    hits = glossary_hits(question, glossary if glossary is not None else load_glossary())
    lines = [f"- {h['ko']} = {h['en_canonical']}" + (f" (variants: {h['en_variants'].replace(';', ', ')})" if h["en_variants"] else "") for h in hits]
    user = (f"Question: {question}\n\nCopy exactly: {', '.join(terms) if terms else '(none)'}\n\n"
            f"Glossary:\n{chr(10).join(lines) if lines else '(no entries matched)'}")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    tokens = [0, 0]
    for attempt in (1, 2):
        r = (chat_client or chat)(messages, json_schema=SCHEMA, provider=provider)
        tokens[0] += r["prompt_tokens"] or 0
        tokens[1] += r["completion_tokens"] or 0
        out = json.loads(r["text"])
        missing = missing_terms(terms, out["english_query"])
        bad_quotes = [m.get("quote", "") for m in [*out["study_mentions"], *out["jurisdiction_mentions"]]
                      if not contains_quote(question, m.get("quote"))]
        if not missing and not bad_quotes:
            break
        corrections = []
        if missing:
            corrections.append(f"These terms must appear exactly as written in english_query: {', '.join(missing)}.")
        if bad_quotes:
            corrections.append("These entity quotes do not occur verbatim in the question: " + json.dumps(bad_quotes, ensure_ascii=False) +
                               ". Replace them with exact passages copied from the Korean question; do not translate or expand identifiers.")
        messages += [{"role": "assistant", "content": r["text"]},
                     {"role": "user", "content": " ".join(corrections) + " Return the corrected JSON."}]
    model_studies = list(out["studies"])
    model_interpretation = dict(out)
    out = resolve(question, out)
    # Spelling variants come from the glossary in code, not from the LLM, so every run adds the same ones.
    expansion = []
    for h in hits:
        for t in [h["en_canonical"], *h["en_variants"].split(";")]:
            if t and t not in expansion:
                expansion.append(t)
    search_terms = " ".join([out["english_query"], *out["keywords"], *expansion])
    return {**out, "studies_from_model": model_studies, "model_interpretation": model_interpretation,
            "missing_role_quotes": bad_quotes, "glossary_expansion": expansion, "search_terms": search_terms,
            "protected": terms, "missing": missing, "attempts": attempt, "model": r["model_reported"],
            "prompt_tokens": tokens[0], "completion_tokens": tokens[1], "prompt_version": PROMPT_VERSION,
            "source": question, "cache_fingerprint": cache_fingerprint(question)}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--query", required=True, help="Korean question")
    p.add_argument("--provider", choices=["openai", "ollama"], help="Override LLM_PROVIDER for this run")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(understand(a.query, provider=a.provider), ensure_ascii=False, indent=2))
