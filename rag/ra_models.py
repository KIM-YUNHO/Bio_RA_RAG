"""Strict contracts at LLM boundaries; source truth stays in source checks."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator
import re

VERSION = 'ra-structured-contracts-2-request-facets'
MAX_PROPOSED_CLAIMS = 16
MAX_REVIEW_CLAUSES = 12
Kind = Literal['source_fact', 'applicant_position', 'regulator_position', 'interpretation']
Basis = Literal['source_statement', 'comparison_of_sources', 'absence_of_evidence']
Verdict = Literal['supported', 'uncertain', 'unsupported']
Support = Literal['supported', 'insufficient', 'conflicting']
Status = Literal['complete', 'partial', 'missing']


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class TaskIntent(Contract):
    intent: Literal['fact_lookup', 'compare_products', 'compare_sources', 'regulatory_reasoning', 'document_info',
                    'out_of_scope']
    request_quote: Annotated[str, Field(max_length=1600)]


class ResearchTask(Contract):
    request: Annotated[str, Field(min_length=1, max_length=1600)]
    queries: Annotated[list[str], Field(min_length=1, max_length=2)]
    source_roles: Annotated[list[Literal['assessment', 'label', 'guideline', 'correspondence',
        'quality', 'rmp', 'decision_summary', 'other_review', 'protocol', 'sap']], Field(min_length=1, max_length=2)]
    guideline_request_quote: str = ''
    intent: TaskIntent

    @field_validator('request')
    @classmethod
    def request_not_blank(cls, value):
        if not value.strip(): raise ValueError('Blank task request')
        return value

    @field_validator('queries')
    @classmethod
    def queries_not_blank(cls, value):
        if any(not q.strip() or len(q) > 800 for q in value): raise ValueError('Invalid retrieval query')
        return value


class JurisdictionMention(Contract):
    jurisdiction: str
    role: Literal['regulator', 'comparator', 'context']
    quote: str


class ResearchPlan(Contract):
    in_scope: bool
    products: list[str]
    jurisdictions: list[str]
    jurisdiction_mentions: list[JurisdictionMention]
    # Kept for the record; RA retrieval uses each task's queries, so an empty value is not an error.
    english_query: str
    tasks: Annotated[list[ResearchTask], Field(min_length=1, max_length=4)]


class FollowUpResearchPlan(ResearchPlan):
    standalone_question: Annotated[str, Field(min_length=1, max_length=2000)]


class Claim(Contract):
    text: Annotated[str, Field(min_length=1, max_length=2500)]
    kind: Kind
    products: list[str]
    claim_basis: Basis
    task_ids: Annotated[list[str], Field(min_length=1)]

    @model_validator(mode='after')
    def scope_references(self, info: ValidationInfo):
        if not self.text.strip(): raise ValueError('Blank claim')
        if len(set(self.task_ids)) != len(self.task_ids): raise ValueError('Duplicate task reference')
        if len(set(self.products)) != len(self.products): raise ValueError('Duplicate product reference')
        context = info.context or {}
        if 'task_ids' in context and not set(self.task_ids) <= set(context['task_ids']):
            raise ValueError('Unknown task reference')
        return self


class SpanClaim(Claim):
    citation_span_ids: Annotated[list[str], Field(min_length=1, max_length=40)]


class Quote(Contract):
    evidence: str
    quote: Annotated[str, Field(min_length=1, max_length=12000)]


class QuoteClaim(Claim):
    sources: Annotated[list[Quote], Field(min_length=1)]


class SpanDraft(Contract):
    claims: Annotated[list[SpanClaim], Field(max_length=MAX_PROPOSED_CLAIMS)]
    gaps: list[Annotated[str, Field(max_length=2000)]]


class QuoteDraft(Contract):
    claims: Annotated[list[QuoteClaim], Field(max_length=MAX_PROPOSED_CLAIMS)]
    gaps: list[Annotated[str, Field(max_length=2000)]]


def request_items(question):
    segments = [s.strip() for s in re.split(r'(?<=[?!。])\s*|(?<=\.)\s+', question) if s.strip()]
    segments = segments or [question]
    if len(segments) > 4:
        segments = segments[:3] + [' '.join(segments[3:])]
    return {f'R{i}': text for i, text in enumerate(segments, 1)}


class RequestDraft(Contract):
    claims: Annotated[list[SpanClaim], Field(max_length=MAX_PROPOSED_CLAIMS)]
    gaps: list[Annotated[str, Field(max_length=2000)]]


class Derived(Contract):
    label: Annotated[str, Field(min_length=1, max_length=200)]
    operation: Literal['date_difference', 'difference', 'ratio', 'percent_change']
    operand_span_ids: Annotated[list[str], Field(min_length=2, max_length=2)]
    operand_values: Annotated[list[Annotated[str, Field(max_length=100)]], Field(min_length=2, max_length=2)] | None = None


class RequestedDraft(Contract):
    request_results: dict[str, RequestDraft]
    derived: Annotated[list[Derived], Field(max_length=16)] = []

    @model_validator(mode='after')
    def requested_keys(self, info: ValidationInfo):
        if set(self.request_results) != set((info.context or {}).get('request_ids', self.request_results)):
            raise ValueError('Every original request item must be present exactly once')
        return self


class ObligationDraft(Contract):
    requirement_results: dict[str, RequestDraft]
    derived: Annotated[list[Derived], Field(max_length=16)] = []

    @model_validator(mode='after')
    def exact_requirements(self, info: ValidationInfo):
        expected=(info.context or {}).get('requirements',{})
        if set(self.requirement_results)!=set(expected):
            raise ValueError('Every question requirement must be present exactly once')
        for key,result in self.requirement_results.items():
            if len(result.claims)>1 or (not result.claims and not any(g.strip() for g in result.gaps)):
                raise ValueError('One focused claim or an explicit gap per requirement')
            product=expected[key]['product']
            for claim in result.claims:
                if product is not None and claim.products!=[product]:
                    raise ValueError('Requirement claim must retain its own product')
        return self


class ReviewedClause(Contract):
    text: str
    kind: Kind
    products: list[str]
    claim_basis: Basis
    verdict: Verdict
    final_span_ids: Annotated[list[str], Field(max_length=40)]
    reason: str


class ClaimReview(Contract):
    selected_support: Support
    context_support: Support
    clauses: Annotated[list[ReviewedClause], Field(min_length=1, max_length=MAX_REVIEW_CLAUSES)]


class TaskCoverage(Contract):
    status: Status
    reason: str


class ReviewResponse(Contract):
    claims: dict[str, ClaimReview]
    coverage: dict[str, TaskCoverage]
    gaps: list[str]

    @model_validator(mode='after')
    def complete_references(self, info: ValidationInfo):
        context = info.context or {}
        if set(self.claims) != set(context.get('claim_ids', self.claims)) or set(self.coverage) != set(context.get('task_ids', self.coverage)):
            raise ValueError('Bound review must cover every original claim and task exactly once')
        return self


class ClaimTruthResponse(Contract):
    claims: dict[str, ClaimReview]

    @model_validator(mode='after')
    def complete_claims(self, info: ValidationInfo):
        if set(self.claims) != set((info.context or {}).get('claim_ids', self.claims)):
            raise ValueError('Truth review must cover every original claim exactly once')
        return self


class ResultState(Contract):
    status: Literal['answered', 'partial', 'not_found']
    claim_ids: list[str]
    rejected_ids: list[str]
    gaps: list[str]
    blocking_rejected_ids: list[str] | None = None

    @model_validator(mode='after')
    def consistent_completion(self):
        if len(set(self.claim_ids)) != len(self.claim_ids): raise ValueError('Duplicate final claim ID')
        if set(self.claim_ids) & set(self.rejected_ids): raise ValueError('Claim both kept and rejected')
        blocking = self.rejected_ids if self.blocking_rejected_ids is None else self.blocking_rejected_ids
        if not set(blocking) <= set(self.rejected_ids): raise ValueError('Unknown blocking rejection')
        if self.status == 'answered' and (not self.claim_ids or self.gaps or blocking):
            raise ValueError('Answered state has missing or rejected requirements')
        return self
