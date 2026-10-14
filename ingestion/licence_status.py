"""Current licence status of a product from the official lists, compared with what was recorded at collection (plan P2, section 6).

EU: EMA medicines list (status, dates). US: Purple Book, newest month (licence type, approval, interchangeability, marketing
status, exclusivity). CA: Health Canada DPD (product status per DIN). JP and KR are reported as not checked (MFDS is deferred).
Every row carries the snapshot it came from and is kept in registry.licence_status_snapshots. No language model is involved.

Usage: uv run python -m ingestion.licence_status <query>      (any name the resolver understands, e.g. Wezlana, SB17)
"""
import datetime as dt
import json
import sys

from psycopg.types.json import Jsonb

from ingestion import db, registry, sources
from ingestion.contracts import Evidence, LicenceStatusReport, LicenceStatusRow
from ingestion.sources import norm

PURPLE_EVENT = ["BLA Number", "License Type", "Submission Type", "Supplement Number", "Approval Date", "Inter. Approval Date",
                "First Interchangeable Exclusivity Exp. Date", "Ref. Product Proprietary Name", "Marketing Status"]  # collect.PURPLE_FIELDS


def us_date(value):
    try:
        return dt.datetime.strptime(value.strip(), "%B %d, %Y").date().isoformat()
    except (ValueError, AttributeError):
        return None


def names_of(conn, key):
    rows = conn.execute("SELECT jurisdiction, name, licence_id, ingredient FROM registry.entity_names WHERE product_key = %s AND name_type = 'brand' "
                        "AND jurisdiction IS NOT NULL", (key,)).fetchall()
    if not rows:
        raise ValueError(f"no licence names for {key}")
    out = {"EU": set(), "US": set(), "CA": set(), "EU_ids": set()}
    for j, name, licence_id, _ in rows:
        out[j].add(name)
        if j == "EU" and licence_id:
            out["EU_ids"].add(licence_id)
    return out, rows[0][3]


def check(conn, key, refresh=False):
    names, ingredient = names_of(conn, key)
    rows = []

    ema_snap, ema = sources.ema_medicines(conn, refresh)
    for p in ema:
        if p["ema_product_number"] in names["EU_ids"]:
            rows.append(LicenceStatusRow(
                jurisdiction="EU", name=p["name_of_medicine"], status=p["medicine_status"], authorised=sources.ema_date(p.get("marketing_authorisation_date")),
                details={k: v for k, v in {"licence": p["ema_product_number"], "holder": p.get("marketing_authorisation_developer_applicant_holder"),
                                           "biosimilar": p.get("biosimilar"), "opinion_adopted": sources.ema_date(p.get("opinion_adopted_date")),
                                           "withdrawal_or_expiry": sources.ema_date(p.get("withdrawal_expiry_revocation_lapse_of_marketing_authorisation_date")),
                                           "latest_procedure": p.get("latest_procedure_affecting_product_information"),
                                           "last_updated": sources.ema_date(p.get("last_updated_date"))}.items() if v},
                evidence=Evidence(**ema_snap.evidence("EMA medicines JSON", p["name_of_medicine"]))))

    purple_snap, purple = sources.purple_book(conn, refresh)
    us_rows = [r for r in purple if r["Proprietary Name"].strip() in names["US"]]
    for bla in sorted({r["BLA Number"].strip() for r in us_rows}):
        group = [r for r in us_rows if r["BLA Number"].strip() == bla]
        events = {tuple(r.get(k, "").strip() for k in PURPLE_EVENT) for r in group}
        first = min((us_date(r.get("Date of First Licensure", "")) or us_date(r["Approval Date"]) or "9999") for r in group)
        interchangeable = sorted({us_date(r["Inter. Approval Date"]) for r in group if us_date(r["Inter. Approval Date"])})
        rows.append(LicenceStatusRow(
            jurisdiction="US", name=group[0]["Proprietary Name"].strip(), status="; ".join(sorted({r["License Type"].strip() for r in group})),
            authorised=None if first == "9999" else first,
            details={k: v for k, v in {"licence": f"BLA {bla}", "applicant": group[0]["Applicant"].strip(), "proper_name": group[0]["Proper Name"].strip(),
                                       "reference_product": group[0].get("Ref. Product Proprietary Name", "").strip(),
                                       "interchangeable_approved": ", ".join(interchangeable),
                                       "marketing_status": ", ".join(sorted({r["Marketing Status"].strip() for r in group})),
                                       "first_interchangeable_exclusivity_ends": ", ".join(sorted({us_date(r["First Interchangeable Exclusivity Exp. Date"]) or "" for r in group} - {""})),
                                       "licensure_events": str(len(events)), "purple_book_month": purple_snap.as_of}.items() if v},
            evidence=Evidence(**purple_snap.evidence("Purple Book", group[0]["Proprietary Name"].strip()))))

    for word in sorted({registry.first_word(n) for n in names["CA"]}):
        dpd_snap, products = sources.dpd_by_brand(conn, word)
        for record in products:
            if registry.first_word(record["brand_name"]) != word or record.get("class_name") != "Human":
                continue
            status_snap, status = sources.dpd_status(conn, record["drug_code"])
            rows.append(LicenceStatusRow(
                jurisdiction="CA", name=f"{record['brand_name']} ({record.get('descriptor') or 'DIN ' + record['drug_identification_number']})",
                status=status.get("status") or "unknown", authorised=None,
                details={k: str(v) for k, v in {"din": record["drug_identification_number"], "company": record.get("company_name"),
                                                "status_since": status.get("history_date"), "first_marketed": status.get("original_market_date")}.items() if v},
                evidence=Evidence(**status_snap.evidence("HC DPD", record["brand_name"]))))

    report = LicenceStatusReport(product_id=None if ":" in key else key, ingredient=ingredient, rows=rows,
                                 not_checked=[j for j in ("EU", "US", "CA") if not names[j]] + ["JP", "KR"],
                                 changed_since_collection=changes(conn, key, rows, purple, names))
    with conn.transaction():
        for r in rows:
            conn.execute("INSERT INTO registry.licence_status_snapshots (product_key, ingredient, jurisdiction, name, status, authorised, details, evidence) "
                         "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (key, ingredient, r.jurisdiction, r.name, r.status, r.authorised,
                                                                      Jsonb(r.details), Jsonb(r.evidence.model_dump())))
    return report


def changes(conn, key, rows, purple, names):
    """For a registered product: what differs from the record written when it was collected (registry.products.record)."""
    row = conn.execute("SELECT record FROM registry.products WHERE product_id = %s", (key,)).fetchone()
    if not row or not row[0]:
        return []
    record, out = row[0], []
    then = (record.get("ema_metadata") or {}).get("medicine_status")
    now = next((r.status for r in rows if r.jurisdiction == "EU" and r.details.get("licence") == (record.get("ema_metadata") or {}).get("ema_product_number")), None)
    if then and now and then != now:
        out.append(f"EU status {then} -> {now}")
    lic = record.get("us_licensure") or {}
    if lic:
        brand = record.get("us_brand")
        events_now = {json.dumps({k: r.get(k, "").strip() for k in PURPLE_EVENT}, sort_keys=True) for r in purple if norm(r["Proprietary Name"]) == norm(brand)}
        events_then = {json.dumps(e, sort_keys=True) for e in lic.get("events", [])}  # jsonb reorders keys
        added = len(events_now - events_then)
        month_now = next((r.details.get("purple_book_month") for r in rows if r.jurisdiction == "US"), None)
        if added:
            out.append(f"US Purple Book: {added} licensure event(s) not in the collection record (record month {lic.get('source_month')}, now {month_now})")
        now_ic = any("Interchangeable" in r.status for r in rows if r.jurisdiction == "US")
        if now_ic != bool(lic.get("interchangeable")):
            out.append(f"US interchangeable {lic.get('interchangeable')} -> {now_ic}")
    return out


def show(report):
    lines = [f"product={report.product_id} ingredient={report.ingredient} not_checked={report.not_checked}"]
    for r in report.rows:
        lines.append(f"  {r.jurisdiction} {r.name}: {r.status} (authorised {r.authorised}) {r.details}  <- {r.evidence.source} as of {r.evidence.as_of}")
    lines.append(f"  changed since collection: {report.changed_since_collection or 'none'}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with db.connect() as conn:
        resolution = registry.resolve(conn, " ".join(sys.argv[1:]))
        products = [c for c in resolution.candidates if c.level != "ingredient"]
        if len(products) != 1:
            print(registry.show(resolution)); print("-> choose one product")
        elif not products[0].names:
            print(registry.show(resolution)); print("-> no licence name known for this product yet (development code only)")
        else:
            print(show(check(conn, products[0].key)))
