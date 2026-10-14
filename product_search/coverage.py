"""Which evidence each collected product has, per jurisdiction and document family, and what is missing.

Reads the project corpus (data/, read only) and product_search/data/. A jurisdiction where the product has no name
(not authorised there, or not found by discovery) is "n/a", not a gap. Writes product_search/data/catalog/coverage.json.
Usage: uv run python -m product_search.coverage
"""
import json
import re
import sys
from pathlib import Path

from product_search import discover

HERE = discover.HERE
KINDS = json.loads((HERE.parent / "configs/collection/document_kinds.json").read_text(encoding="utf-8"))["kinds"]
ROOTS = {"project": HERE.parent, "product_search": HERE}
# Families a reviewer expects for an authorised biosimilar in each jurisdiction (docs/collection/temp/COLLECTION_GAP_REVIEW_2026-10-07.md).
EXPECTED = {
    "EU": ("ema_name", "EMA", ["assessment_report", "plain_summary", "post_authorisation_history", "product_information", "risk_management_plan"]),
    "US": ("us_brand", "FDA", ["assessment_report", "quality_review", "product_information", "decision_letter"]),
    "CA": ("ca_brand", "Health Canada", ["assessment_report", "decision_summary", "product_information"]),
    "JP": ("pmda_general_list_ids", "PMDA", ["assessment_report", "product_information"]),
}
PK = re.compile(r"\bAUC|\bC ?max\b|pharmacokinetic", re.I)


def family(doc):
    return doc.get("family") or (KINDS.get(doc["source"], {}).get(doc["document_type"]) or ["unclassified"])[0]


def licensure(root, product):
    if product.get("us_licensure"):
        return product["us_licensure"]
    rows = [r for r in discover.read_jsonl(root / "data/catalog/purple_full_selected.jsonl") if discover.norm(r.get("Proprietary Name", "")) == discover.norm(product.get("us_brand") or "-")]
    return {"interchangeable": any("Interchangeable" in r.get("License Type", "") for r in rows), "events": rows} if rows else None


def study_row(root, study, docs):
    record = json.loads((root / study["api_source"]["raw_path"]).read_text(encoding="utf-8"))
    outcomes = record.get("resultsSection", {}).get("outcomeMeasuresModule", {}).get("outcomeMeasures", [])
    attached = sorted({d["document_type"] for d in docs if d.get("nct_id") == study["nct_id"]})
    return {"nct_id": study["nct_id"], "phases": study["phases"], "attachments": attached, "has_results": bool(record.get("hasResults")),
            "outcomes": len(outcomes), "pk_outcomes": sum(bool(PK.search(o.get("title", ""))) for o in outcomes)}


def product_coverage(where, root, product, docs, studies):
    mine = [d for d in docs if d["product_id"] == product["product_id"]]
    out = {"product_id": product["product_id"], "corpus": where, "jurisdictions": {}, "gaps": []}
    for code, (field, source, families) in EXPECTED.items():
        if not product.get(field):
            out["jurisdictions"][code] = "n/a"
            continue
        have = {}
        for d in mine:
            if d["source"] == source:
                have.setdefault(family(d), []).append(d["document_id"])
        out["jurisdictions"][code] = {f: len(have.get(f, [])) for f in families}
        out["gaps"] += [f"{code}:{f}" for f in families if not have.get(f)]
        if code == "US":
            out["us_supplement_letters"] = sum(d.get("step") == "fda_letters" and d["document_type"] == "Approval Letter" for d in mine)
            out["us_licensure"] = licensure(root, product)
            if not out["us_licensure"]:
                out["gaps"].append("US:purple_book_status")
    out["studies"] = [study_row(root, s, mine) for s in studies if s["product_id"] == product["product_id"]]
    for s in out["studies"]:
        if "PHASE1" in s["phases"] and not s["has_results"] and not s["attachments"]:
            out["gaps"].append(f"trial:{s['nct_id']}:phase1_pk_only_in_reviews")
    return out


def guideline_layer():
    rows = discover.read_jsonl(ROOTS["project"] / "data/catalog/guidelines.jsonl") or discover.read_jsonl(HERE / "data/catalog/guidelines.jsonl")
    by = {}
    for r in rows:
        by.setdefault(r["jurisdiction"], []).append({k: r.get(k) for k in ("guideline_id", "status", "issued", "first_published", "date_modified", "reference_number") if r.get(k)})
    return by


def main():
    report = {"products": [], "guidelines": guideline_layer()}
    for where, root in ROOTS.items():
        cat = root / "data/catalog"
        docs = discover.read_jsonl(cat / "documents.jsonl")
        studies = discover.read_jsonl(cat / "studies.jsonl")
        # The project's products.jsonl predates the Health Canada and PMDA steps; targets.json has the names those steps used.
        seeds = {t["product_id"]: t for t in discover.PROJECT_CFG["products"]} if where == "project" else {}
        for p in discover.read_jsonl(cat / "products.jsonl"):
            report["products"].append(product_coverage(where, root, dict(seeds.get(p["product_id"], {}), **{k: v for k, v in p.items() if v}), docs, studies))
    (HERE / "data/catalog/coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("| corpus | product | EU | US | CA | JP | US status | trials (phase: attachments/results/PK outcomes) |")
    print("|---|---|---|---|---|---|---|---|")
    cell = lambda j: j if j == "n/a" else " ".join(f"{k.split('_')[0]}{v}" for k, v in j.items())
    for r in report["products"]:
        lic = r.get("us_licensure")
        status = "n/a" if r["jurisdictions"]["US"] == "n/a" else ("interchangeable" if lic and lic["interchangeable"] else "biosimilar" if lic else "missing")
        trials = "; ".join(f"{s['nct_id']} {'/'.join(p[-1] for p in s['phases'])}: {len(s['attachments'])}/{'R' if s['has_results'] else '-'}/{s['pk_outcomes']}" for s in r["studies"])
        print(f"| {r['corpus']} | {r['product_id']} | " + " | ".join(cell(r["jurisdictions"][c]) for c in EXPECTED) + f" | {status} | {trials} |")
    print()
    for r in report["products"]:
        if r["gaps"]:
            print(r["corpus"], r["product_id"], "gaps:", ", ".join(r["gaps"]))
    print("guidelines:", {k: len(v) for k, v in report["guidelines"].items()} or "none collected")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
