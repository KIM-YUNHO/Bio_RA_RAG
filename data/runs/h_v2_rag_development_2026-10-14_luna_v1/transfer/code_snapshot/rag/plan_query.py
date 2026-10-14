#!/usr/bin/env python3
"""Turn an understood question (rag/understand_query.py) into searches.

One search per product x agency x document groups. Agencies the question does not name are each
searched on their own, so one agency's documents do not crowd out another's (docs/rag/RAG_POC_RESULTS.md 6.6).
Each search carries the studies it is about:
- the studies the question names or describes;
- else an unrestricted study scope. The topic is a retrieval hint, never a phase filter.
All registered studies, parts and analyses remain candidates, including planned and interchangeability studies.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study_registry as sr  # noqa: E402

AGENCIES = ["EU", "US", "CA"]


def main_studies(product):
    """Registered main studies, including interchangeability studies."""
    return sr.studies_of(product, kinds=("study", "planned_study"))


def studies_for(product, topic, named):
    reg = sr.load()
    named = [k for k in named if k in reg and reg[k]["product_id"] == product]
    if named:
        return named, False
    return [], False


def plan(u, agencies=None, products=None):
    """[{product, jurisdiction, doc_groups, studies, decomposed}] for an understanding result."""
    if u["intent"] == "out_of_scope" or u.get('request_scope',{}).get('status') in ('conflict','empty'):
        return []
    selected_products = u["products"] or (products if products is not None else list(sr.products()))
    jurisdictions = u["jurisdictions"] or list(AGENCIES if agencies is None else agencies)
    if products is not None:
        selected_products = [p for p in selected_products if p in products]
    if agencies is not None:
        jurisdictions = [j for j in jurisdictions if j in agencies]
    doc_groups = u["doc_groups"] or ["assessment"]
    out = []
    for prod in selected_products:
        studies, decomposed = studies_for(prod, u["topic"], u["studies"])
        for j in jurisdictions:
            out.append({"product": prod, "jurisdiction": j, "doc_groups": doc_groups, "studies": studies,
                        "study_candidates": sr.studies_of(prod),
                        "study_scope": "specified" if studies else u.get("study_scope", "unspecified"),
                        "decomposed": decomposed, "topic": u["topic"]})
    return out
