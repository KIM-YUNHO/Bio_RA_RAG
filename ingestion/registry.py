"""Name registry and resolver: what did the user ask for? (plan P2, section 5)

build()    fills registry.entity_names from the EMA medicines list (every status), the Purple Book, Health Canada's DPD for the
           ingredients in targets.json, and the registered products (targets.json and the loaded catalog).
resolve()  turns a query (ingredient, brand in any jurisdiction, US proper name, development code, typo) into candidates with
           evidence: exact name -> on-demand lookup (development code: ClinicalTrials.gov + GSRS; brand: HC DPD) -> trigram.
           Code decides; nothing here calls a language model.

Usage: uv run python -m ingestion.registry build
       uv run python -m ingestion.registry resolve <query>
"""
import json
import os
import re
import sys
from functools import lru_cache

from psycopg.types.json import Jsonb

from ingestion import db, sources
from ingestion.contracts import ROOT, Candidate, Evidence, Resolution, load_targets
from ingestion.sources import norm

ACTIVE_RELEASE = os.environ.get("BIOSIM_ACTIVE_RELEASE", "data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2")  # scripts/start_ra_service.ps1
TRIGRAM_MIN = 0.45   # similarity floor for typo matches; set with eval/resolver_cases (ingestion.eval_resolver)
TRIGRAM_BAND = 0.08  # keep matches this close to the best one
CODE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9 -]{1,14}$")
COLUMNS = ("name", "name_norm", "name_type", "jurisdiction", "ingredient", "product_key", "product_kind", "licence_id", "status",
           "source", "source_url", "snapshot_id", "origin")


def first_word(brand):
    """Canadian DPD brands carry presentation words ('STELARA I.V.'); the first word names the product."""
    return norm(brand.split()[0]) if brand.split() else norm(brand)


class Builder:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []
        targets = load_targets()
        self.targets = targets
        # Registered licence names -> product_id (all jurisdictions, any licence of the product, e.g. Usrenty -> BMAB1200).
        self.registered = {}
        for p in targets.products:
            for j, name in [("EU", p.ema_name), ("US", p.us_brand), ("CA", p.ca_brand)] + [(l.jurisdiction, l.name) for l in p.licences]:
                if name:
                    self.registered[(j, norm(name))] = p.product_id
        self.kind_of = {p.product_id: p.kind for p in targets.products}
        # Products collected into staging were grouped by product_search.discover (EPAR development code): their licence names
        # share one key, CODE:<product_id>, so Uzpruvo, Usgena, Selarsdi and AVT04 are one product. Registered products win.
        self.staged = []
        staged_path = ROOT / "product_search/data/catalog/products.jsonl"
        for line in (staged_path.read_text(encoding="utf-8").splitlines() if staged_path.exists() else []):
            p = json.loads(line) if line.strip() else None
            if not p or p["product_id"] in self.kind_of:
                continue
            key = "CODE:" + p["product_id"]
            self.staged.append(p)
            self.kind_of[key] = p.get("kind") or "biosimilar"
            for j, name in [("EU", p.get("ema_name")), ("US", p.get("us_brand")), ("CA", p.get("ca_brand"))] + [(l["jurisdiction"], l["name"]) for l in p.get("licences", [])]:
                if name:
                    self.registered.setdefault((j, norm(name)), key)

    def add(self, **row):
        row.setdefault("snapshot_id", None); row.setdefault("source_url", None); row.setdefault("licence_id", None)
        row.setdefault("status", None); row.setdefault("jurisdiction", None); row.setdefault("product_key", None)
        row.setdefault("product_kind", None); row.setdefault("origin", "build")
        row["name_norm"] = norm(row["name"])
        if row["name_norm"]:
            self.rows.append(row)

    def build(self, refresh=False):
        ema_snap, ema = sources.ema_medicines(self.conn, refresh)
        purple_snap, purple = sources.purple_book(self.conn, refresh)
        biosimilar_inns = {p["active_substance"].strip().lower() for p in ema if p.get("biosimilar") == "Yes"} \
            | {sources.purple_ingredient(r) for r in purple if "351(k)" in r.get("License Type", "")}

        # The reference product is the originator: a US 351(a) licence of an ingredient that has biosimilars. An EU product without
        # the biosimilar flag is the reference only under the same brand (Stelara); others are 'other' (Upstelda, a withdrawn application).
        originators = {(sources.purple_ingredient(r), norm(r["Proprietary Name"])) for r in purple
                       if "351(a)" in r.get("License Type", "") and sources.purple_ingredient(r) in biosimilar_inns}

        def kind(ingredient, biosimilar_flag, brand):
            return "biosimilar" if biosimilar_flag else "reference" if (ingredient, norm(brand)) in originators else "other"

        for p in ema:
            inn = p["active_substance"].strip().lower()
            key = self.registered.get(("EU", norm(p["name_of_medicine"]))) or "EU:" + p["ema_product_number"]
            self.add(name=p["name_of_medicine"], name_type="brand", jurisdiction="EU", ingredient=inn, product_key=key,
                     product_kind=self.kind_of.get(key) or kind(inn, p.get("biosimilar") == "Yes", p["name_of_medicine"]), licence_id=p["ema_product_number"],
                     status=p.get("medicine_status"), source="EMA medicines JSON", source_url=p.get("medicine_url"), snapshot_id=ema_snap.snapshot_id)
            for name in {p["active_substance"], p.get("international_non_proprietary_name_common_name") or ""}:
                if name.strip():
                    self.add(name=name.strip(), name_type="inn", ingredient=inn, source="EMA medicines JSON", snapshot_id=ema_snap.snapshot_id)

        by_licence = {}
        for r in purple:
            by_licence.setdefault((r["Proprietary Name"].strip(), r["BLA Number"].strip()), []).append(r)
        for (brand, bla), rows in by_licence.items():
            r = rows[0]
            inn = sources.purple_ingredient(r)
            key = self.registered.get(("US", norm(brand))) or f"US:BLA{bla}"
            biosimilar = "351(k)" in r.get("License Type", "")
            types = sorted({x["License Type"].strip() for x in rows})
            self.add(name=brand, name_type="brand", jurisdiction="US", ingredient=inn, product_key=key,
                     product_kind=self.kind_of.get(key) or kind(inn, biosimilar, brand), licence_id=f"BLA {bla}", status="; ".join(types),
                     source="Purple Book", source_url=sources.PURPLE_DOWNLOADS, snapshot_id=purple_snap.snapshot_id)
            proper = r["Proper Name"].strip()
            if biosimilar and proper.lower() != inn:  # ustekinumab-auub names one licence
                self.add(name=proper, name_type="proper_name", jurisdiction="US", ingredient=inn, product_key=key,
                         product_kind="biosimilar", licence_id=f"BLA {bla}", source="Purple Book", snapshot_id=purple_snap.snapshot_id)
            else:
                self.add(name=proper, name_type="inn", ingredient=inn, source="Purple Book", snapshot_id=purple_snap.snapshot_id)

        self.unify()
        for inn in sorted({self.targets.ingredient_of(p) for p in self.targets.products}):
            self.canadian(inn)

        for p in self.targets.products:
            inn = self.targets.ingredient_of(p)
            for alias in [p.product_id, p.development_code] + p.aliases:
                if alias:
                    self.add(name=alias, name_type="dev_code" if re.search(r"\d", alias) else "brand", ingredient=inn,
                             product_key=p.product_id, product_kind=p.kind, source="targets.json", origin="catalog")
        for p in self.staged:
            for alias in [p["product_id"], p.get("development_code")] + p.get("aliases", []):
                if alias:
                    self.add(name=alias, name_type="dev_code" if re.search(r"\d", alias) else "brand", ingredient=p["ingredient"],
                             product_key="CODE:" + p["product_id"], product_kind=p.get("kind") or "biosimilar",
                             source="product_search staging (discover)", origin="catalog")
        self.write()
        return {"names": len(self.rows), "ema_as_of": ema_snap.fetched_at.date().isoformat(), "purple_book_month": purple_snap.as_of}

    def canon(self):
        """(ingredient, brand) -> product key: a registered product first, then the EU licence, then the US one. The whole
        brand is compared, so 'Adalimumab Viatris' and 'Adalimumab Mylan' stay apart."""
        keys = {}
        for registered_first in (True, False):
            for r in self.rows:
                if r["name_type"] == "brand" and r["product_key"] and (":" not in r["product_key"]) == registered_first:
                    keys.setdefault((r["ingredient"], r["name_norm"]), r["product_key"])
        return keys

    def unify(self):
        """One product per brand across EU and US for products not registered in targets.json (Humira EU + US)."""
        keys, remap = self.canon(), {}
        for r in self.rows:
            if r["name_type"] == "brand" and r["product_key"] and ":" in r["product_key"]:
                key = keys[(r["ingredient"], r["name_norm"])]
                if key != r["product_key"]:
                    remap[r["product_key"]] = key
        for r in self.rows:
            r["product_key"] = remap.get(r["product_key"], r["product_key"])

    def canadian(self, inn, origin="build"):
        """Canadian brands of an ingredient (DPD). A brand is the reference product when it starts like the ingredient's EU/US
        reference brand; otherwise, for an ingredient that has biosimilars, it is taken as a biosimilar (DPD has no flag)."""
        snap, products = sources.dpd_by_ingredient(self.conn, inn)
        reference = {first_word(r["name"]) for r in self.rows if r["ingredient"] == inn and r["product_kind"] == "reference" and r["name_type"] == "brand"}
        has_biosimilars = any(r["ingredient"] == inn and r["product_kind"] == "biosimilar" for r in self.rows) or inn in {
            row[0] for row in self.conn.execute("SELECT DISTINCT ingredient FROM registry.entity_names WHERE product_kind = 'biosimilar'")}
        keys, seen = self.canon(), set()
        for product_snap, record in products:
            if not record or record.get("class_name") != "Human":
                continue
            word = first_word(record["brand_name"])
            if (word, record["drug_code"]) in seen:
                continue
            seen.add((word, record["drug_code"]))
            key = self.registered.get(("CA", word)) or next((v for (j, n), v in self.registered.items() if n == word), None) \
                or keys.get((inn, word)) or "CA:" + word.upper()
            kind_ = self.kind_of.get(key) or ("reference" if word in reference else "biosimilar" if has_biosimilars else "other")
            self.add(name=record["brand_name"], name_type="brand", jurisdiction="CA", ingredient=inn, product_key=key, product_kind=kind_,
                     licence_id="DIN " + record["drug_identification_number"], status=None, source="HC DPD",
                     source_url=product_snap.url, snapshot_id=product_snap.snapshot_id, origin=origin)
        return snap

    def write(self, replace=("build", "catalog")):
        with self.conn.transaction():
            if replace:
                self.conn.execute("DELETE FROM registry.entity_names WHERE origin = ANY(%s)", (list(replace),))
            unique = {tuple(r[c] for c in COLUMNS[:9] + ("source",)): r for r in self.rows}
            with self.conn.cursor().copy(f"COPY registry.entity_names ({', '.join(COLUMNS)}) FROM STDIN") as copy:
                for r in unique.values():
                    copy.write_row(tuple(r[c] for c in COLUMNS))
        self.rows = list(unique.values())


# ---- on-demand lookups ------------------------------------------------------------------------------------

def known_ingredients(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT ingredient FROM registry.entity_names WHERE name_type = 'inn' AND ingredient !~ '[/,]'")]


def lookup_code(conn, code):
    """A development code the registry does not know: GSRS exact name, then ClinicalTrials.gov studies that name it. The
    ingredient is taken only from text that names it (an INN, or a brand the registry knows, e.g. 'vs Stelara')."""
    searched, rows = [], []
    gsrs_snap, gsrs = sources.gsrs_exact(conn, code)
    searched.append("GSRS exact name")
    for name in gsrs:
        hit = conn.execute("SELECT ingredient, product_key, product_kind FROM registry.entity_names WHERE name_norm = %s AND name_type IN ('proper_name', 'inn') LIMIT 1",
                           (norm(name),)).fetchone()
        if hit:
            rows.append(dict(name=code, name_type="dev_code", ingredient=hit[0], product_key=hit[1] or "CODE:" + norm(code).upper(),
                             product_kind=hit[2] or "other", source="GSRS", source_url=gsrs_snap.url, snapshot_id=gsrs_snap.snapshot_id,
                             status=f"GSRS name of {name}"))
    if not rows:
        ctg_snap, studies = sources.trials_naming(conn, code)
        searched.append("ClinicalTrials.gov")
        inns = known_ingredients(conn)
        brands = conn.execute("SELECT name_norm, ingredient FROM registry.entity_names WHERE name_type = 'brand' AND product_kind = 'reference' AND length(name_norm) >= 5").fetchall()
        for s in studies:
            text = s["text"].lower()
            found = {i for i in inns if len(i) >= 5 and re.search(r"\b" + re.escape(i) + r"\b", text)}
            words = {norm(w) for w in re.findall(r"[A-Za-z][A-Za-z0-9-]+", s["text"])}
            found |= {ing for brand, ing in brands if brand in words}
            for inn in sorted(found):
                rows.append(dict(name=code, name_type="dev_code", ingredient=inn, product_key="CODE:" + norm(code).upper(),
                                 product_kind="biosimilar" if ("biosimilar" in text or any(b in words for b, i in brands if i == inn)) else "other",
                                 source="ClinicalTrials.gov", source_url=f"https://clinicaltrials.gov/study/{s['nct_id']}",
                                 snapshot_id=ctg_snap.snapshot_id, status=s["text"][:200]))
    write_lookup(conn, rows)
    return searched


def lookup_brand(conn, brand):
    """A word the registry does not know, tried as a Canadian brand; its ingredient comes from the DPD record."""
    snap, products = sources.dpd_by_brand(conn, brand)
    rows = []
    for record in products:
        if first_word(record["brand_name"]) != norm(brand) or record.get("class_name") != "Human":
            continue
        ing_snap = sources.fetch(conn, "HC DPD", f"{sources.DPD}activeingredient/?lang=en&type=json&id={record['drug_code']}", "json")
        ingredients = sorted({i["ingredient_name"].strip().lower() for i in json.loads(ing_snap.body) or []})
        if len(ingredients) != 1:
            continue
        builder = Builder(conn)
        builder.rows = [dict(zip(COLUMNS, r)) for r in conn.execute(f"SELECT {', '.join(COLUMNS)} FROM registry.entity_names WHERE ingredient = %s", (ingredients[0],))]
        before = len(builder.rows)
        builder.canadian(ingredients[0], origin="lookup")
        rows = [r for r in builder.rows[before:] if first_word(r["name"]) == norm(brand)]
        break
    write_lookup(conn, rows)
    return ["HC DPD brand"]


def write_lookup(conn, rows):
    if not rows:
        return
    for r in rows:
        r.update(name_norm=norm(r["name"]), origin="lookup", jurisdiction=r.get("jurisdiction"), licence_id=r.get("licence_id"))
    have = {tuple(x) for x in conn.execute("SELECT name_norm, product_key, source FROM registry.entity_names WHERE origin = 'lookup'")}
    with conn.transaction(), conn.cursor().copy(f"COPY registry.entity_names ({', '.join(COLUMNS)}) FROM STDIN") as copy:
        for r in rows:
            if (r["name_norm"], r["product_key"], r["source"]) not in have:
                copy.write_row(tuple(r.get(c) for c in COLUMNS))
                have.add((r["name_norm"], r["product_key"], r["source"]))


# ---- resolver -------------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def searchable_documents():
    path = ROOT / ACTIVE_RELEASE / "candidate/chunks.jsonl"
    if not path.exists():
        return frozenset()
    with path.open(encoding="utf-8") as stream:
        return frozenset(json.loads(line)["document_id"] for line in stream if line.strip())


SELECT = """SELECT n.name, n.name_type, n.jurisdiction, n.ingredient, n.product_key, n.product_kind, n.source, n.source_url, n.status,
                   s.sha256, coalesce(s.as_of, to_char(s.fetched_at, 'YYYY-MM-DD')) AS as_of {extra}
            FROM registry.entity_names n LEFT JOIN registry.source_snapshots s USING (snapshot_id) """


def matches(conn, q):
    return [dict(zip(("name", "name_type", "jurisdiction", "ingredient", "product_key", "product_kind", "source", "source_url", "status", "sha256", "as_of"), r))
            for r in conn.execute(SELECT.format(extra="") + "WHERE n.name_norm = %s AND n.name_type <> 'company'", (q,))]


def fuzzy(conn, q):
    rows = conn.execute(SELECT.format(extra=", similarity(n.name_norm, %s) AS score") +
                        "WHERE n.name_norm %% %s AND n.name_type IN ('brand', 'inn', 'proper_name', 'dev_code') ORDER BY score DESC LIMIT 30", (q, q)).fetchall()
    if not rows or rows[0][-1] < TRIGRAM_MIN:
        return []
    best = rows[0][-1]
    keys = ("name", "name_type", "jurisdiction", "ingredient", "product_key", "product_kind", "source", "source_url", "status", "sha256", "as_of", "score")
    return [dict(zip(keys, r)) for r in rows if r[-1] >= best - TRIGRAM_BAND]


@lru_cache(maxsize=1)
def registered_on():
    return {p.product_id: p.added for p in load_targets().products}


def skeleton(value):
    """Consonants only: a transliteration keeps them and changes the vowels ('Wazlena' and 'Wezlana' are both 'wzln')."""
    return re.sub(r"[aeiouy]", "", norm(value))


def consonant_match(conn, q):
    s = skeleton(q)
    if len(s) < 4 or not s.isascii():
        return []
    keys = ("name", "name_type", "jurisdiction", "ingredient", "product_key", "product_kind", "source", "source_url", "status", "sha256", "as_of")
    return [dict(zip(keys, r)) for r in conn.execute(SELECT.format(extra="") + "WHERE regexp_replace(n.name_norm, '[aeiouy]', '', 'g') = %s "
                                                     "AND n.name_type IN ('brand', 'inn')", (s,))]


# ---- phonetic key (Hangul and Latin to the same consonant classes) ----------------------------------------------
# A Korean transliteration of a foreign brand keeps its consonants roughly but not letter for letter (Pyzchiva -> 피즈치바:
# z -> ㅈ, v -> ㅂ). Both scripts are reduced to classes: B (b p f v), D (d t), G (g k q, hard c), S (s z j ch sh, soft c), L (l r),
# M, N; vowels, h, w and y are dropped and repeats collapse. Pyzchiva and 피즈치바 are both BSB.
HANGUL_INITIAL = ["G", "G", "N", "D", "D", "L", "M", "B", "B", "S", "S", "", "S", "S", "S", "G", "D", "B", ""]  # ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ
HANGUL_FINAL = ["", "G", "G", "GS", "N", "NS", "N", "D", "L", "LG", "LM", "LB", "LS", "LD", "LB", "L", "M", "B", "BS", "S", "S", "N", "S", "S", "G", "D", "B", ""]
LATIN = [("ph", "B"), ("th", "D"), ("sh", "S"), ("ch", "S"), ("ck", "G"), ("x", "GS"), ("qu", "G")]
LATIN_ONE = {"b": "B", "p": "B", "f": "B", "v": "B", "d": "D", "t": "D", "g": "G", "k": "G", "q": "G", "s": "S", "z": "S", "j": "S",
             "l": "L", "r": "L", "m": "M", "n": "N"}


def phonetic(value):
    out = []
    for ch in str(value or ""):
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out.append(HANGUL_INITIAL[code // 588] + HANGUL_FINAL[code % 28])
        elif ch.isascii() and ch.isalpha():
            out.append(ch.lower())
        else:
            out.append(" ")
    text = "".join(out)
    # Latin letters are still lower case here; Hangul already became class letters (upper case).
    for pair, cls in LATIN:
        text = text.replace(pair, cls)
    text = re.sub(r"c(?=[eiy])", "S", text)
    text = text.replace("c", "G")
    key = "".join(LATIN_ONE.get(ch, ch if ch.isupper() else "") for ch in text)
    return re.sub(r"(.)\1+", r"\1", key)


@lru_cache(maxsize=1)
def phonetic_index():
    with db.connect() as conn:
        # Only the biosimilar domain: reference and biosimilar products, and the INNs of ingredients that have biosimilars. Short
        # keys would otherwise hit unrelated medicines (DLB: Otulfi but also DuoTrav, Talvey).
        rows = conn.execute("SELECT DISTINCT name, name_norm FROM registry.entity_names WHERE (name_type = 'brand' AND product_kind IN ('reference', 'biosimilar')) "
                            "OR (name_type = 'inn' AND ingredient IN (SELECT ingredient FROM registry.entity_names WHERE product_kind = 'biosimilar'))").fetchall()
    index = {}
    for name, name_norm in rows:
        index.setdefault(phonetic(name), set()).add(name_norm)
    return index


RR_INITIAL = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
RR_MEDIAL = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
RR_FINAL = ["", "k", "k", "ks", "n", "nj", "nh", "t", "l", "lk", "lm", "lb", "ls", "lt", "lp", "lh", "m", "p", "ps", "t", "t", "ng", "t", "t", "k", "t", "p", "t"]


def romanize(value):
    """Revised Romanization of Hangul syllables (other characters kept, lower case); used only to break phonetic-key ties."""
    out = []
    for ch in str(value or ""):
        code = ord(ch) - 0xAC00
        out.append(RR_INITIAL[code // 588] + RR_MEDIAL[(code % 588) // 28] + RR_FINAL[code % 28] if 0 <= code < 11172 else ch.lower())
    return norm("".join(out))


def closest(query, rows, margin=0.1):
    """Among phonetic matches, the names whose spelling is clearly closest to the romanized query (SequenceMatcher ratio)."""
    from difflib import SequenceMatcher
    target = romanize(query)
    scored = [(SequenceMatcher(None, target, norm(r["name"])).ratio(), r) for r in rows]
    best = max(s for s, _ in scored)
    keep = {norm(r["name"]) for s, r in scored if s >= best - margin}
    return [r for r in rows if norm(r["name"]) in keep]


def phonetic_match(conn, query):
    """Names whose phonetic key equals the query's; used for Hangul (and other non-matching spellings) with a key of 2+ classes."""
    key = phonetic(query)
    if len(key) < 2:
        return []
    norms = sorted(phonetic_index().get(key, ()))
    keys = ("name", "name_type", "jurisdiction", "ingredient", "product_key", "product_kind", "source", "source_url", "status", "sha256", "as_of")
    rows = [dict(zip(keys, r)) for r in conn.execute(SELECT.format(extra="") + "WHERE n.name_norm = ANY(%s) AND ((n.name_type = 'brand' AND n.product_kind IN "
                                                     "('reference', 'biosimilar')) OR n.name_type = 'inn')", (norms,))] if norms else []
    return closest(query, rows) if rows else []


def evidence(row):
    # targets.json entries have no snapshot; their date is the day the product was registered.
    return Evidence(source=row["source"], url=row["source_url"], matched_text=row["name"],
                    as_of=row["as_of"] or registered_on().get(row["product_key"], "unknown"), snapshot_sha256=row["sha256"])


def candidates(conn, rows):
    out = {}
    collected = {r[0] for r in conn.execute("SELECT DISTINCT product_id FROM corpus.documents WHERE product_id IS NOT NULL")}
    in_release = searchable_documents()
    searchable = {r[0] for r in conn.execute("SELECT DISTINCT product_id FROM corpus.documents WHERE document_id = ANY(%s)", (list(in_release),))}
    for row in rows:
        if not row["ingredient"]:
            continue  # a list row without an ingredient cannot be placed
        if row["name_type"] == "inn":
            key = "INN:" + row["ingredient"]
            products = {r[0] for r in conn.execute("SELECT product_id FROM registry.products WHERE ingredient = %s", (row["ingredient"],))}
            c = out.setdefault(key, {"key": key, "level": "ingredient", "ingredient": row["ingredient"], "product_id": None, "names": {},
                                     "collected": bool(products & collected), "searchable": bool(products & searchable), "evidence": []})
        else:
            key = row["product_key"]
            registered = ":" not in key
            c = out.get(key)
            if not c:
                names, statuses = {}, {}
                for j, n, status in conn.execute("SELECT jurisdiction, name, status FROM registry.entity_names WHERE product_key = %s AND name_type = 'brand' "
                                                 "AND jurisdiction IS NOT NULL ORDER BY origin, licence_id, name", (key,)):  # earliest EU number = original application
                    names.setdefault(j, n)
                    if status:
                        statuses.setdefault(j, status)
                level = {"reference": "reference_product", "biosimilar": "biosimilar_product"}.get(row["product_kind"], "other_product")
                c = out[key] = {"key": key, "level": level, "ingredient": row["ingredient"], "product_id": key if registered else None, "names": names,
                                "statuses": statuses,
                                "collected": registered and key in collected, "searchable": registered and key in searchable, "evidence": []}
        if len(c["evidence"]) < 3:
            c["evidence"].append(evidence(row))
    return [Candidate(**c) for c in out.values()]


def resolve(conn, query, lookups=True):
    q = norm(query)
    searched = ["registry exact name"]
    rows = matches(conn, q) if q else []
    if not rows and q and lookups and query.strip().isascii():  # Hangul is never a code or a Canadian brand
        searched += lookup_code(conn, query.strip()) if CODE.match(query.strip()) else lookup_brand(conn, query.strip())
        rows = matches(conn, q)
    if not rows and q:
        searched.append("registry similar names (trigram)")
        rows = fuzzy(conn, q)
    if not rows and q:
        searched.append("registry consonant skeleton")
        rows = consonant_match(conn, query)
    if not rows and q:
        searched.append("registry phonetic key (Hangul/Latin)")
        rows = phonetic_match(conn, query)
    return Resolution(query=query, candidates=candidates(conn, rows), searched=searched)


def products_of(conn, ingredient):
    """Every product of an ingredient the registry knows (reference first, then registered, then by name), one candidate each.
    Products with no licence name in EU/US/CA (withdrawn or refused only, development codes only) come last."""
    rows = [dict(zip(("name", "name_type", "jurisdiction", "ingredient", "product_key", "product_kind", "source", "source_url", "status", "sha256", "as_of"), r))
            for r in conn.execute(SELECT.format(extra="") + "WHERE n.ingredient = %s AND n.name_type IN ('brand', 'dev_code') AND n.product_key IS NOT NULL "
                                  "ORDER BY n.origin, n.jurisdiction, n.name", (ingredient,))]
    found = candidates(conn, rows)
    order = {"reference_product": 0, "biosimilar_product": 1, "other_product": 2}
    return sorted(found, key=lambda c: (order[c.level], c.product_id is None, not c.names, next(iter(c.names.values()), c.key).lower()))


def show(resolution):
    lines = [f"query={resolution.query!r} status={resolution.status} searched={resolution.searched}"]
    for c in resolution.candidates:
        ev = c.evidence[0]
        lines.append(f"  [{c.level}] {c.key} ingredient={c.ingredient} names={c.names} collected={c.collected} searchable={c.searchable}"
                     f"  <- {ev.source} {ev.matched_text!r} ({ev.as_of})")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with db.connect() as conn:
        if sys.argv[1:2] == ["build"]:
            print(Builder(conn).build(refresh="--refresh" in sys.argv))
        else:
            print(show(resolve(conn, " ".join(sys.argv[2:]))))
