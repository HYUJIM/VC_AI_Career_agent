from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, HttpUrl, StrictBool, field_validator, model_validator

from .verification import EvidenceModel

CATALOG_VERSION = "local-data-engineer/1"
MAPPING_VERSION = "exact-claim-mapping/1"
# 2: a verified credential that no mapping rule covers only adds a warning; it no longer turns
# every unevidenced requirement into "unknown" (version 1 did).
GAP_POLICY_VERSION = "evidence-gap/2"
SkillID = Literal[
    "sql",
    "python",
    "scala",
    "spark",
    "kafka",
    "airflow",
    "etl",
    "data_modeling",
    "git",
    "linux",
    "aws",
    "docker",
]
EvidenceType = Literal["alignment", "completion", "assessment", "project", "experience"]
Seniority = Literal["entry", "junior", "mid", "senior", "unspecified"]


class SkillDefinition(EvidenceModel):
    skill_id: SkillID
    label: str
    aliases: list[str]


class SkillCatalog(EvidenceModel):
    catalog_version: str = CATALOG_VERSION
    taxonomy_source: Literal["project-curated"] = "project-curated"
    skills: list[SkillDefinition]


class Requirement(EvidenceModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    kind: Literal["skill", "experience", "education", "certification", "portfolio", "other"]
    importance: Literal["required", "preferred"]
    quote: str = Field(min_length=1, max_length=4000)
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    skill_ids: list[SkillID] = Field(default_factory=list, max_length=12)
    match: Literal["all", "any", "unspecified"] = "all"
    minimum_evidence: EvidenceType = "experience"

    @model_validator(mode="after")
    def validate_structure(self):
        if self.end <= self.start or len(self.skill_ids) != len(set(self.skill_ids)):
            raise ValueError("Invalid source span or duplicate skill IDs")
        if self.kind == "skill" and not self.skill_ids:
            raise ValueError("Skill requirements need catalog skill IDs")
        if self.kind != "skill" and (self.skill_ids or self.match != "all"):
            raise ValueError("Non-skill conditions are retained for manual assessment")
        return self


class JobPostingInput(EvidenceModel):
    source: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_.-]+$")
    external_id: str = Field(min_length=1, max_length=255)
    source_url: HttpUrl
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=255)
    role_id: Literal["data_engineer"] = "data_engineer"
    region: str = Field(min_length=1, max_length=80)
    seniority: Seniority
    posted_at: AwareDatetime | None
    captured_at: AwareDatetime
    closes_at: AwareDatetime | None = None
    is_open: StrictBool = True
    dataset_kind: Literal["real", "synthetic"]
    rights_basis: Literal["permission", "public_api_license", "research_authorization"]
    review_note: str = Field(min_length=1, max_length=2000)
    raw_text: str = Field(min_length=1, max_length=100000)
    requirements: list[Requirement] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_source(self):
        if (
            self.source_url.scheme != "https"
            or self.source_url.username
            or self.source_url.password
        ):
            raise ValueError("Source URL must be HTTPS without credentials")
        if len({item.key for item in self.requirements}) != len(self.requirements):
            raise ValueError("Requirement keys must be unique")
        if self.posted_at and self.posted_at > self.captured_at:
            raise ValueError("Posting date cannot follow capture date")
        if self.posted_at and self.closes_at and self.closes_at <= self.posted_at:
            raise ValueError("Closing date must follow posting date")
        for item in self.requirements:
            if item.end > len(self.raw_text) or self.raw_text[item.start : item.end] != item.quote:
                raise ValueError("Every annotation must cite its exact original source span")
        return self

    @field_validator("company", "title", "region", "review_note", "external_id")
    @classmethod
    def nonblank_metadata(cls, value):
        if not value.strip():
            raise ValueError("Metadata cannot be blank")
        if "\x00" in value:
            raise ValueError("NUL characters cannot be preserved in JSONB")
        value.encode("utf-8")
        return value.strip()

    @field_validator("raw_text")
    @classmethod
    def jsonb_text(cls, value):
        if "\x00" in value:
            raise ValueError("NUL characters cannot be preserved in JSONB")
        value.encode("utf-8")
        return value


class JobVersion(EvidenceModel):
    posting_id: UUID
    version_id: UUID
    imported_at: AwareDatetime
    reviewed_by: UUID
    annotation_method: Literal["human_reviewed"] = "human_reviewed"
    content_hash: str
    posting: JobPostingInput


class PreviewRequest(EvidenceModel):
    raw_text: str = Field(min_length=1, max_length=100000)


class RequirementCandidate(EvidenceModel):
    quote: str
    start: int
    end: int
    skill_ids: list[SkillID]
    match: Literal["all", "any", "unspecified"]
    requires_review: Literal[True] = True


class EvidenceFragment(EvidenceModel):
    pointer: str
    quote: str


class SkillEvidence(EvidenceModel):
    evidence_id: UUID
    skill_id: SkillID
    record_id: UUID
    verification_snapshot_id: UUID
    evidence_type: EvidenceType
    rule_id: str
    mapping_version: str = MAPPING_VERSION
    interpretation: Literal["exact_claim_rule", "explicit_alignment"]
    source_fragments: list[EvidenceFragment]


class RequirementComparison(EvidenceModel):
    posting_id: UUID
    version_id: UUID
    requirement: Requirement
    status: Literal["supported", "partially_supported", "not_evidenced", "unknown"]
    reason_code: str
    evidence_ids: list[UUID]
    priority: Literal["high", "medium", "none"]
    next_action: Literal["none", "confirm_or_provide_evidence", "manual_assessment"]


class DemandStatistic(EvidenceModel):
    skill_id: SkillID
    mentioned_postings: int
    required_postings: int
    preferred_postings: int
    alternative_postings: int
    ambiguous_postings: int
    denominator: int


class JobReference(EvidenceModel):
    posting_id: UUID
    version_id: UUID
    content_hash: str
    source_url: str
    company: str
    title: str
    posted_at: AwareDatetime | None
    captured_at: AwareDatetime
    closes_at: AwareDatetime | None
    is_target: bool


class CareerAnalysisRequest(EvidenceModel):
    target_role_id: Literal["data_engineer"] = "data_engineer"
    target_posting_id: UUID | None = None
    region: str | None = Field(default=None, min_length=1, max_length=80)
    seniority: Seniority | None = None


class CareerAnalysis(EvidenceModel):
    analysis_id: UUID
    verification_snapshot_id: UUID
    subject_did: str
    as_of: AwareDatetime
    valid_until: AwareDatetime
    request: CareerAnalysisRequest
    catalog_version: str = CATALOG_VERSION
    mapping_version: str = MAPPING_VERSION
    policy_version: str = GAP_POLICY_VERSION
    method: Literal["deterministic_evidence_comparison"] = "deterministic_evidence_comparison"
    lookback_days: Literal[30, 90]
    market_posting_count: int
    duplicate_posting_count: int
    comparison_scope: Literal["target_posting", "market_postings"]
    sources: list[JobReference]
    skill_evidence: list[SkillEvidence]
    comparisons: list[RequirementComparison]
    market_demand: list[DemandStatistic]
    unmapped_record_ids: list[UUID]
    excluded_record_ids: list[UUID]
    warnings: list[str]
