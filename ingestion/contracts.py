"""Pydantic contracts for ingestion (plan P0, docs/collection/INGESTION_AGENT_DB_PLAN_2026-10-14.md section 7.5).

One set of models serves every consumer: targets.json and guidelines.json are validated with them, the request agent fills
IngestionRequest, the task runner (later an Airflow DAG) receives it as its parameters, and the database stores it.
Rules that verify.py checks on targets.json are repeated here so a bad entry is refused before collection starts.
"""
import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[1]
VERSION = "ingestion-contracts-2"  # 2: TurnParse.confirm intent, Mention.english_guess

Jurisdiction = Literal["EU", "US", "CA", "JP", "KR"]
Date = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
NonBlank = Annotated[str, Field(min_length=1)]
NCT = Annotated[str, Field(pattern=r"^NCT\d{8}$")]


def code_key(value):
    """Development code compared without case or punctuation (verify.py's rule): 'ABP 654' == 'ABP654'."""
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


# ---- registration files ---------------------------------------------------------------------------

class Licence(Contract):
    jurisdiction: Jurisdiction
    licence_id: str | None
    name: NonBlank
    holder: str | None
    authorised: Date | None = None
    primary: bool
    collected: bool


class ProductTarget(Contract):
    """One targets.json product: one development code, its licences per jurisdiction (INGESTION_PIPELINE.md 4.3)."""
    product_id: Annotated[str, Field(pattern=r"^[A-Z0-9]+$")]
    development_code: str | None
    kind: Literal["reference", "biosimilar"]
    reference_product_id: str | None = None
    ingredient: NonBlank | None = None  # only when it differs from the file's ingredient
    ema_name: str | None
    us_brand: str | None
    ca_brand: str | None
    aliases: Annotated[list[NonBlank], Field(min_length=1)]
    study_ids: list[NCT]
    pmda_general_list_ids: list[Annotated[str, Field(pattern=r"^\d+$")]]
    licences: list[Licence]
    company: str | None
    added: Date
    notes: str | None = None

    @model_validator(mode="after")
    def product_rules(self):
        if self.kind == "reference" and self.reference_product_id:
            raise ValueError(f"{self.product_id}: a reference product points at no other product")
        if self.kind == "biosimilar" and not self.reference_product_id:
            raise ValueError(f"{self.product_id}: a biosimilar names its reference product")
        if self.development_code and code_key(self.development_code).upper() != self.product_id:
            raise ValueError(f"{self.product_id}: product_id is the development code without punctuation ({self.development_code})")
        primary_eu = [l.name for l in self.licences if l.jurisdiction == "EU" and l.primary]
        if self.licences and primary_eu != ([self.ema_name] if self.ema_name else []):
            raise ValueError(f"{self.product_id}: the primary EU licence must be the collected EU name")
        if len(set(self.study_ids)) != len(self.study_ids):
            raise ValueError(f"{self.product_id}: duplicate study id")
        return self


class TargetsFile(BaseModel):
    """targets.json. Keys starting with '_' are notes; the source URLs and step settings are kept as they are."""
    model_config = ConfigDict(strict=True, extra="allow")
    ingredient: NonBlank
    ingredient_ja: str = ""
    products: Annotated[list[ProductTarget], Field(min_length=1)]
    sources: dict[str, NonBlank]
    ema_document_roles: dict[str, NonBlank]
    fda_review_roles: dict[str, NonBlank]
    fda_review_skip: list[str]
    review_sample_pages: dict[str, list[int]] = {}

    @model_validator(mode="after")
    def file_rules(self):
        ids = [p.product_id for p in self.products]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate product_id")
        codes = [code_key(p.development_code or p.product_id) for p in self.products]
        if len(set(codes)) != len(codes):
            raise ValueError("two products share a development code")
        owners = {}
        for p in self.products:
            for l in p.licences:
                if owners.setdefault((l.jurisdiction, l.name), p.product_id) != p.product_id:
                    raise ValueError(f"licence {l.jurisdiction} {l.name} listed under two products")
        kinds = {p.product_id: p for p in self.products}
        for p in self.products:
            if p.kind == "biosimilar":
                ref = kinds.get(p.reference_product_id)
                if not ref or ref.kind != "reference" or self.ingredient_of(ref) != self.ingredient_of(p):
                    raise ValueError(f"{p.product_id}: reference_product_id must name a reference product of the same ingredient")
        return self

    def ingredient_of(self, product):
        return product.ingredient or self.ingredient


class GuidelineSeed(Contract):
    id: Annotated[str, Field(pattern=r"^[A-Z0-9-]+$")]
    source: Literal["EMA", "FDA", "Health Canada"]
    document_type: NonBlank
    jurisdiction: Literal["EU", "US", "CA"]
    title: NonBlank
    landing: str | None
    url: str | None = None
    pages: list[str] | None = None
    status: str | None = None
    topics: list[NonBlank]

    @model_validator(mode="after")
    def one_way_to_fetch(self):
        if bool(self.url) == bool(self.pages):
            raise ValueError(f"{self.id}: give either url (one PDF) or pages (sections of the landing page)")
        if self.pages and not self.landing:
            raise ValueError(f"{self.id}: pages are read from the landing page")
        return self


class GuidelinesFile(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")
    checked: Date
    guidelines: list[GuidelineSeed]

    @field_validator("guidelines")
    @classmethod
    def unique_ids(cls, value):
        ids = [g.id for g in value]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate guideline id")
        return value


def load_targets(path=None):
    return TargetsFile.model_validate(json.loads(Path(path or ROOT / "configs/collection/targets.json").read_text(encoding="utf-8")))


def load_guidelines(path=None):
    return GuidelinesFile.model_validate(json.loads(Path(path or ROOT / "configs/collection/guidelines.json").read_text(encoding="utf-8")))


# ---- request agent ---------------------------------------------------------------------------------

class Mention(Contract):
    text: NonBlank  # as the user wrote it
    guessed_type: Literal["inn", "brand", "dev_code", "company", "proper_name", "korean", "unknown"]
    english_guess: str  # the model's Latin-script spelling for a Korean or transliterated name, "" if none; used only if the registry knows it


class TurnParse(Contract):
    """The only thing the language model returns for a user turn; code decides what runs next."""
    intent: Literal["licence_status", "ingest", "refresh", "add_guideline", "coverage", "select", "confirm", "cancel", "other"]
    mentions: Annotated[list[Mention], Field(max_length=8)]
    jurisdictions: list[Jurisdiction]
    selected_keys: Annotated[list[str], Field(max_length=20)]  # keys of candidates shown to the user


class Evidence(Contract):
    """Where a fact came from: an official list or page, or the user's own words."""
    source: NonBlank  # e.g. "EMA medicines JSON", "Purple Book", "user"
    url: str | None
    matched_text: NonBlank
    as_of: NonBlank
    snapshot_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None


class Candidate(Contract):
    key: NonBlank
    level: Literal["ingredient", "reference_product", "biosimilar_product", "other_product", "licence"]
    ingredient: NonBlank
    product_id: str | None
    names: dict[Jurisdiction, str]
    statuses: dict[Jurisdiction, str] = {}  # the source's wording per jurisdiction (Authorised, Application withdrawn, 351(k) Biosimilar ...)
    collected: bool
    searchable: bool
    evidence: Annotated[list[Evidence], Field(min_length=1)]


class Resolution(Contract):
    query: NonBlank
    candidates: list[Candidate]
    searched: list[NonBlank]  # which lists were looked at, shown when nothing is found

    @property
    def status(self):
        return "not_found" if not self.candidates else "unique" if len(self.candidates) == 1 else "ambiguous"


class LicenceStatusRow(Contract):
    jurisdiction: Jurisdiction
    name: NonBlank
    status: NonBlank  # the source's own wording: Authorised, Withdrawn, 351(k) Interchangeable, Marketed ...
    authorised: Date | None
    details: dict[str, str]
    evidence: Evidence


class LicenceStatusReport(Contract):
    product_id: str | None
    ingredient: NonBlank
    rows: list[LicenceStatusRow]
    not_checked: list[Jurisdiction]
    changed_since_collection: list[str]


IDENTIFIER_FIELDS = ("development_code", "study_ids", "pmda_general_list_ids")


class IngestionRequest(Contract):
    """What the agent may run once it validates (plan 7.5). Validate with
    IngestionRequest.model_validate(data, context={"evidence_text": [...]}), where evidence_text holds the tool results and the
    user's words of this conversation: an identifier that appears in none of them was made up and is refused."""
    kind: Literal["new_product", "refresh", "new_guideline"]
    ingredient: NonBlank
    products: list[ProductTarget] = []
    guideline: GuidelineSeed | None = None
    include_reference: bool
    jurisdictions: Annotated[list[Literal["EU", "US", "CA", "JP"]], Field(min_length=1)]
    provenance: dict[str, Evidence]

    @model_validator(mode="after")
    def runnable(self, info: ValidationInfo):
        if self.kind == "new_guideline":
            if not self.guideline or self.products:
                raise ValueError("new_guideline carries one guideline and no products")
        elif not self.products or self.guideline:
            raise ValueError(f"{self.kind} carries products and no guideline")
        if "JP" in self.jurisdictions and any(not p.pmda_general_list_ids for p in self.products):
            raise ValueError("JP needs PMDA product pages a person has checked (pmda_general_list_ids)")
        if any(p.kind == "reference" for p in self.products) and not self.include_reference:
            raise ValueError("a reference product is included only when the user chose it (include_reference)")
        context = (info.context or {}) if info else {}
        if "evidence_text" not in context:
            raise ValueError("validate with context={'evidence_text': [...]} (tool results and user text)")
        corpus = " ".join(context["evidence_text"]).lower()
        for p in self.products:
            for value in [p.development_code] + p.study_ids + p.pmda_general_list_ids:
                if value and value.lower() not in corpus:
                    raise ValueError(f"{p.product_id}: identifier {value!r} is not in any tool result or user message")
            for field in IDENTIFIER_FIELDS:
                if getattr(p, field) and field not in self.provenance and f"{p.product_id}.{field}" not in self.provenance:
                    raise ValueError(f"{p.product_id}: {field} has no provenance entry")
        return self


# ---- task runner ------------------------------------------------------------------------------------

class TaskKey(Contract):
    """Idempotency key of one task: the same key run twice gives the same rows."""
    run_id: Annotated[str, Field(pattern=r"^\d{8}T\d{6}Z$")]
    step: NonBlank
    product_id: str | None = None
    document_version: str | None = None  # "<document_id>@<version_id>" for per-document tasks such as parse


class TaskResult(Contract):
    key: TaskKey
    status: Literal["succeeded", "failed", "skipped"]
    documents: list[str]  # document_ids produced
    errors: list[dict[str, str]]
    metrics: dict[str, int | float | str]


if __name__ == "__main__":
    targets, guidelines = load_targets(), load_guidelines()
    print(json.dumps({"contracts": VERSION, "products": [p.product_id for p in targets.products], "guidelines": len(guidelines.guidelines)}))
