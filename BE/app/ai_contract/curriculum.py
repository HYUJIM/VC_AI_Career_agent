from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, HttpUrl, StrictBool, field_validator, model_validator

from .career import SkillID
from .verification import EvidenceModel

PLANNER_VERSION = "grounded-curriculum/1"
RETRIEVAL_VERSION = "reviewed-tags-keywords/1"
PROMPT_VERSION = "constrained-selector/1"


class SourceSpan(EvidenceModel):
    quote: str = Field(min_length=1, max_length=2000)
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)


class ResourceSkill(SourceSpan):
    skill_id: SkillID


class LearningResourceInput(EvidenceModel):
    source: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_.-]+$")
    external_id: str = Field(min_length=1, max_length=255)
    source_url: HttpUrl
    title: str = Field(min_length=1, max_length=255)
    provider: str = Field(min_length=1, max_length=200)
    raw_text: str = Field(min_length=1, max_length=50000)
    skill_annotations: list[ResourceSkill] = Field(min_length=1, max_length=12)
    prerequisite_annotations: list[ResourceSkill] = Field(default_factory=list, max_length=12)
    prerequisites_reviewed: StrictBool
    level: Literal["beginner", "intermediate", "advanced"]
    minimum_study_hours: int = Field(ge=1, le=40, strict=True)
    cost_krw: int | None = Field(default=None, ge=0, le=100000000, strict=True)
    cost_annotation: SourceSpan | None = None
    captured_at: AwareDatetime
    available: StrictBool = True
    dataset_kind: Literal["real", "synthetic"]
    rights_basis: Literal["permission", "public_api_license", "research_authorization"]
    review_note: str = Field(min_length=1, max_length=2000)

    @field_validator("title", "provider", "raw_text", "review_note", "external_id")
    @classmethod
    def valid_text(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("Source text must be nonblank and JSONB-compatible")
        value.encode("utf-8")
        return value

    @model_validator(mode="after")
    def validate_citations(self):
        if not self.prerequisites_reviewed:
            raise ValueError("Explicit prerequisite review is required, including an empty list")
        if (
            self.source_url.scheme != "https"
            or self.source_url.username
            or self.source_url.password
        ):
            raise ValueError("Resource URL requires HTTPS without credentials")
        for annotations in (self.skill_annotations, self.prerequisite_annotations):
            if len({item.skill_id for item in annotations}) != len(annotations):
                raise ValueError("Duplicate skill annotations")
        citations = [*self.skill_annotations, *self.prerequisite_annotations]
        if self.cost_annotation:
            citations.append(self.cost_annotation)
        if (self.cost_krw is None) != (self.cost_annotation is None):
            raise ValueError("Known fees require a cited source; unknown fees remain unknown")
        for item in citations:
            if item.end > len(self.raw_text) or self.raw_text[item.start : item.end] != item.quote:
                raise ValueError("Resource citations must match exact original source spans")
        return self


class LearningResourceVersion(EvidenceModel):
    resource_id: UUID
    version_id: UUID
    imported_at: AwareDatetime
    reviewed_by: UUID
    content_hash: str
    resource: LearningResourceInput


class LearningChoice(EvidenceModel):
    skill_id: SkillID
    action: Literal["learn", "collect_evidence", "assess"] = "assess"


class AlternativeChoice(EvidenceModel):
    comparison_ref: str = Field(min_length=1, max_length=130)
    skill_id: SkillID


class CurriculumRequest(EvidenceModel):
    analysis_id: UUID
    idempotency_key: UUID
    weeks: int = Field(default=8, ge=1, le=24, strict=True)
    hours_per_week: int = Field(default=6, ge=1, le=40, strict=True)
    budget_krw: int | None = Field(default=None, ge=0, le=100000000, strict=True)
    mode: Literal["template", "llm"] = "template"
    allow_external_processing: StrictBool = False
    learning_choices: list[LearningChoice] = Field(default_factory=list, max_length=12)
    alternative_choices: list[AlternativeChoice] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_choices(self):
        if len({item.skill_id for item in self.learning_choices}) != len(self.learning_choices):
            raise ValueError("Learning choices must be unique")
        if len({item.comparison_ref for item in self.alternative_choices}) != len(
            self.alternative_choices
        ):
            raise ValueError("Alternative choices must be unique")
        if self.mode == "llm" and not self.allow_external_processing:
            raise ValueError("External LLM processing requires explicit request consent")
        return self


class PlanningQuestion(EvidenceModel):
    comparison_ref: str
    code: str
    skill_options: list[SkillID] = Field(default_factory=list)


class PlanOption(EvidenceModel):
    step_id: UUID
    skill_id: SkillID
    kind: Literal["self_assessment", "practice", "collect_evidence"]
    comparison_refs: list[str]
    depends_on: list[UUID]
    resource_options: list[LearningResourceVersion]
    minimum_hours: int
    conditional: bool
    purpose: Literal["gap_followup", "resource_prerequisite"] = "gap_followup"


class DraftStep(EvidenceModel):
    step_id: UUID
    resource_version_id: UUID | None = None
    hours: int = Field(ge=1, le=40, strict=True)


class CurriculumDraft(EvidenceModel):
    steps: list[DraftStep] = Field(max_length=48)


class PlanningContext(EvidenceModel):
    planner_version: str = PLANNER_VERSION
    retrieval_version: str = RETRIEVAL_VERSION
    options: list[PlanOption]
    baseline: CurriculumDraft
    questions: list[PlanningQuestion]
    warnings: list[str]
    # Earliest expiry of the analysis or any retrieved resource option. BE re-checks it
    # before and after generation. None only appears in the planner's internal provisional
    # context; BE rejects a context without it.
    valid_until: AwareDatetime | None = None


class WeekAllocation(EvidenceModel):
    week: int
    hours: int


class CurriculumStep(EvidenceModel):
    step_id: UUID
    skill_id: SkillID
    kind: Literal["self_assessment", "practice", "collect_evidence"]
    comparison_refs: list[str]
    depends_on: list[UUID]
    conditional: bool
    hours: int
    purpose: Literal["gap_followup", "resource_prerequisite"]
    allocations: list[WeekAllocation]
    goal: str
    activity: str
    completion_criterion: str
    resource: LearningResourceVersion | None = None


class CurriculumResult(EvidenceModel):
    job_id: UUID
    analysis_id: UUID
    as_of: AwareDatetime
    valid_until: AwareDatetime
    planner_version: str = PLANNER_VERSION
    prompt_version: str = PROMPT_VERSION
    generation_method: Literal["template", "llm", "llm_fallback"]
    model: str | None = None
    status: Literal["ready", "partial"]
    steps: list[CurriculumStep]
    total_hours: int
    known_cost_krw: int
    has_unknown_costs: bool
    questions: list[PlanningQuestion]
    warnings: list[str]
    validation_errors: list[str]
