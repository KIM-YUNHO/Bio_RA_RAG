"""From a chosen product to a runnable IngestionRequest (plan P3, section 7.4 plan_ingestion).

product_search.discover groups the product's licences (reading the development code from its EPAR) and finds its trials. Its output
is the tool result the request is checked against: every identifier in the request must appear in it (IngestionRequest validator).
"""
import datetime as dt
import json

from ingestion.contracts import ROOT, Evidence, IngestionRequest, ProductTarget, load_targets
from ingestion.sources import norm

TARGET_FIELDS = ("product_id", "development_code", "kind", "reference_product_id", "ingredient", "ema_name", "us_brand", "ca_brand",
                 "aliases", "study_ids", "pmda_general_list_ids", "licences", "company")


class PlanError(ValueError):
    pass


def staged_products():
    path = ROOT / "product_search/data/catalog/products.jsonl"
    return {json.loads(l)["product_id"] for l in path.read_text(encoding="utf-8").splitlines() if l.strip()} if path.exists() else set()


def find_entry(candidate):
    """The discover entry for the candidate: a licence name, alias or development code in common."""
    from product_search import discover  # heavy import; sets collect.ROOT to product_search/ for this process
    wanted = {norm(n) for n in candidate.names.values()} | {norm(e.matched_text) for e in candidate.evidence}
    if candidate.key.startswith("CODE:"):
        wanted.add(norm(candidate.key[5:]))
    result = discover.search(candidate.ingredient)
    entries = [p for p in result["products"] if wanted & ({norm(l["name"]) for l in p.get("licences", [])} | {norm(a) for a in p.get("aliases", [])})]
    if len(entries) != 1:
        found = ", ".join(sorted(p["product_id"] for p in entries)) or "none"
        raise PlanError(f"discover found {len(entries)} matching products for {candidate.key} ({found}); "
                        "a Canadian-only name or a code without an EU/US licence cannot be grouped automatically")
    return entries[0]


def target_of(entry):
    data = {k: entry.get(k) for k in TARGET_FIELDS}
    for k in ("aliases", "study_ids", "pmda_general_list_ids", "licences"):
        data[k] = data[k] or []
    data["licences"] = [{k: l.get(k) for k in ("jurisdiction", "licence_id", "name", "holder", "authorised", "primary", "collected")} for l in data["licences"]]
    data["kind"] = data["kind"] or "biosimilar"
    if data["kind"] == "biosimilar" and not data["reference_product_id"]:
        refs = [p.product_id for p in load_targets().products if p.kind == "reference" and load_targets().ingredient_of(p) == entry["ingredient"]]
        data["reference_product_id"] = refs[0] if len(refs) == 1 else None
    data["added"] = dt.date.today().isoformat()
    return ProductTarget.model_validate(data)


def plan(candidate, user_texts):
    entry = find_entry(candidate)
    if entry["product_id"] in {p.product_id for p in load_targets().products}:
        raise PlanError(f"{entry['product_id']} is already a project product; it is updated by collect.py (refresh), not added again")
    target = target_of(entry)
    tool_text = json.dumps(entry, ensure_ascii=False)
    today = dt.date.today().isoformat()
    provenance = {}
    if target.development_code:
        provenance[f"{target.product_id}.development_code"] = Evidence(source="EMA EPAR text via product_search.discover", url=None,
                                                                       matched_text=target.development_code, as_of=today, snapshot_sha256=None)
    if target.study_ids:
        provenance[f"{target.product_id}.study_ids"] = Evidence(source="ClinicalTrials.gov via product_search.discover", url=None,
                                                                matched_text=" ".join(target.study_ids), as_of=today, snapshot_sha256=None)
    jurisdictions = [j for j, name in (("EU", target.ema_name), ("US", target.us_brand), ("CA", target.ca_brand)) if name]
    request = IngestionRequest.model_validate(
        {"kind": "new_product", "ingredient": entry["ingredient"], "products": [target.model_dump()], "include_reference": target.kind == "reference",
         "jurisdictions": jurisdictions, "provenance": {k: v.model_dump() for k, v in provenance.items()}},
        context={"evidence_text": [tool_text] + list(user_texts)})
    return {"entry": entry, "request": request, "already_staged": target.product_id in staged_products()}
