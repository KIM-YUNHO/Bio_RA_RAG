"""Official lists the registry reads, each fetched through one snapshot store (plan P2, sections 5.2 and 6.1).

Every response is kept as data/registry/raw/<sha256>.<ext> with a registry.source_snapshots row (url, hash, fetch time, the
source's own date), so a name or a status shown to a user can be traced to the bytes it came from. A snapshot younger than
max_age is reused; lists change daily at most (Purple Book monthly). Collection (collect.py, data/raw) is not touched.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from ingestion.contracts import ROOT

STORE = ROOT / "data/registry/raw"
DAY = 86400
USER_AGENT = "BiosimilarCollectionPilot/0.1 (public regulatory research)"
EMA_MEDICINES = "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json"
PURPLE_DOWNLOADS = "https://purplebooksearch.fda.gov/downloads"
DPD = "https://health-products.canada.ca/api/drug/"
GSRS = "https://gsrs.ncats.nih.gov/api/v1/"
CTG = "https://clinicaltrials.gov/api/v2/studies"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]


def norm(value):
    """Lower case, letters and digits of any script. Same as collect.norm for ASCII; keeps Korean and Japanese."""
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


class Snapshot:
    def __init__(self, snapshot_id, url, sha256, body, fetched_at, as_of):
        self.snapshot_id, self.url, self.sha256, self.body, self.fetched_at, self.as_of = snapshot_id, url, sha256, body, fetched_at, as_of

    def evidence(self, source, matched_text):
        return {"source": source, "url": self.url, "matched_text": matched_text, "as_of": self.as_of or self.fetched_at.date().isoformat(),
                "snapshot_sha256": self.sha256}


def fetch(conn, source, url, ext, max_age=DAY, as_of=None, refresh=False):
    """Body of url from a snapshot younger than max_age, else fetched now (3 attempts; 404 raises at once)."""
    if not refresh:
        row = conn.execute("SELECT snapshot_id, sha256, uri, fetched_at, as_of FROM registry.source_snapshots WHERE url = %s "
                           "ORDER BY fetched_at DESC LIMIT 1", (url,)).fetchone()
        if row and (dt.datetime.now(dt.timezone.utc) - row[3]).total_seconds() < max_age and (ROOT / row[2]).exists():
            return Snapshot(row[0], url, row[1], (ROOT / row[2]).read_bytes(), row[3], row[4])
    error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=60) as response:
                body = response.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            error = exc
        except Exception as exc:  # network errors are retried
            error = exc
        time.sleep(attempt + 1)
    else:
        raise error
    digest = hashlib.sha256(body).hexdigest()
    path = STORE / f"{digest}.{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(body)
    snapshot_id, fetched_at = conn.execute(
        "INSERT INTO registry.source_snapshots (source, url, sha256, uri, bytes, as_of) VALUES (%s, %s, %s, %s, %s, %s) "
        "RETURNING snapshot_id, fetched_at", (source, url, digest, path.relative_to(ROOT).as_posix(), len(body), as_of)).fetchone()
    return Snapshot(snapshot_id, url, digest, body, fetched_at, as_of)


# ---- EMA ----------------------------------------------------------------------------------------------

def ema_medicines(conn, refresh=False):
    """All human medicines in the EMA list, every status (Authorised, Withdrawn, Refused, Opinion ...)."""
    snap = fetch(conn, "EMA medicines JSON", EMA_MEDICINES, "json", refresh=refresh)
    rows = [p for p in json.loads(snap.body)["data"] if p.get("category") == "Human"]
    return snap, rows


def ema_date(value):
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", (value or "").strip())
    return f"{m[3]}-{m[2]}-{m[1]}" if m else None


# ---- Purple Book ----------------------------------------------------------------------------------------

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a" and dict(attrs).get("href"):
            self.links.append(dict(attrs)["href"])


def purple_book(conn, refresh=False):
    """Rows of the newest monthly Purple Book CSV (its second, full section), with the month it describes."""
    page = fetch(conn, "Purple Book", PURPLE_DOWNLOADS, "html", refresh=refresh)
    parser = Links(); parser.feed(page.body.decode("utf-8", errors="replace"))
    dated = [(int(m[1]), MONTHS.index(m[2].lower()) + 1, urllib.parse.urljoin(PURPLE_DOWNLOADS, href)) for href in parser.links
             if (m := re.search(r"/PurpleBook/(\d{4})/purplebook-search-([A-Za-z]+)-data-download\.csv", href, re.I)) and m[2].lower() in MONTHS]
    if not dated:
        raise ValueError("Purple Book: no monthly CSV link on the downloads page")
    year, month, url = max(dated)
    snap = fetch(conn, "Purple Book", url, "csv", max_age=30 * DAY, as_of=f"{year}-{month:02}", refresh=refresh)
    rows = list(csv.reader(io.StringIO(snap.body.decode("utf-8-sig"))))
    starts = [i for i, r in enumerate(rows) if any(norm(c) == "blanumber" for c in r)]
    if len(starts) != 2:
        raise ValueError(f"Purple Book layout changed: expected 2 header rows, found {len(starts)}")
    header = rows[starts[1]]
    return snap, [dict(zip(header, r), source_csv_row=i + 1) for i, r in enumerate(rows[starts[1] + 1:], starts[1] + 1) if len(r) == len(header)]


def purple_ingredient(row):
    """INN of a Purple Book row: the proper name without a biosimilar's four-letter suffix (ustekinumab-auub -> ustekinumab)."""
    proper = row.get("Proper Name", "").strip().lower()
    return re.sub(r"-[a-z]{4}$", "", proper) if "351(k)" in row.get("License Type", "") else proper


# ---- Health Canada DPD ------------------------------------------------------------------------------------

def dpd_by_brand(conn, brand):
    snap = fetch(conn, "HC DPD", f"{DPD}drugproduct/?brandname={urllib.parse.quote(brand.lower())}&type=json", "json")
    return snap, json.loads(snap.body) or []


def dpd_by_ingredient(conn, ingredient):
    """Canadian products of an ingredient: drug codes from the active-ingredient list, then each product record."""
    snap = fetch(conn, "HC DPD", f"{DPD}activeingredient/?lang=en&type=json&ingredientname={urllib.parse.quote(ingredient)}", "json")
    codes = sorted({r["drug_code"] for r in json.loads(snap.body) or [] if norm(r.get("ingredient_name")) == norm(ingredient)})
    products = []
    for code in codes:
        product = fetch(conn, "HC DPD", f"{DPD}drugproduct/?id={code}&type=json", "json")
        record = json.loads(product.body)
        products.append((product, record[0] if isinstance(record, list) else record))
    return snap, products


def dpd_status(conn, drug_code):
    snap = fetch(conn, "HC DPD", f"{DPD}status/?id={drug_code}&lang=en&type=json", "json")
    record = json.loads(snap.body)
    return snap, (record[0] if isinstance(record, list) else record)


# ---- development codes ---------------------------------------------------------------------------------------

def gsrs_exact(conn, name):
    """GSRS substances whose name is exactly `name` (e.g. 'ABP 654' -> Ustekinumab-AUUB). Free-text search is too loose to use."""
    q = urllib.parse.urlencode({"q": f'root_names_name:"{name}"', "top": 5})
    snap = fetch(conn, "GSRS", f"{GSRS}substances/search?{q}", "json", max_age=30 * DAY)
    return snap, [c.get("_name") for c in json.loads(snap.body).get("content", []) if c.get("_name")]


def trials_naming(conn, code):
    """ClinicalTrials.gov studies whose title or interventions name the code, with the text to look for the ingredient in."""
    q = urllib.parse.urlencode({"query.term": f'"{code}"', "pageSize": 20, "fields": "NCTId,BriefTitle,OfficialTitle,InterventionName,LeadSponsorName"})
    snap = fetch(conn, "ClinicalTrials.gov", f"{CTG}?{q}", "json", max_age=7 * DAY)
    out = []
    for s in json.loads(snap.body).get("studies", []):
        p = s["protocolSection"]
        text = " ".join([p["identificationModule"].get("briefTitle", ""), p["identificationModule"].get("officialTitle", "")]
                        + [i.get("name", "") for i in p.get("armsInterventionsModule", {}).get("interventions", [])])
        if norm(code) in norm(text):
            out.append({"nct_id": p["identificationModule"]["nctId"], "text": text,
                        "sponsor": p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name")})
    return snap, out
