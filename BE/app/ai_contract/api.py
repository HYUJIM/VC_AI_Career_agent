"""Request/response envelopes of the AI service (BE is the only intended client).

The AI service is stateless: BE sends evidence it has already re-verified (VC snapshot,
job and resource versions) and stores the results. Nothing here carries VC originals,
BE keys, passwords or database identifiers beyond the UUIDs needed to cite evidence.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .career import (
    CareerAnalysis,
    CareerAnalysisRequest,
    DemandStatistic,
    JobPostingInput,
    JobReference,
    JobVersion,
    RequirementCandidate,
    SkillCatalog,
    SkillID,
)
from .curriculum import (
    CurriculumRequest,
    CurriculumResult,
    LearningResourceInput,
    LearningResourceVersion,
    PlanningContext,
)
from .verification import EvidenceModel, VerificationSnapshot

class ValidationResult(EvidenceModel):
    valid: Literal[True] = True


# --- Job requirements and evidence comparison ----------------------------------------


class JobValidationRequest(EvidenceModel):
    posting: JobPostingInput


class RequirementPreview(EvidenceModel):
    candidates: list[RequirementCandidate]


class JobSearchRequest(EvidenceModel):
    request: CareerAnalysisRequest
    candidates: list[JobVersion] = Field(max_length=1000)


class JobSearchResult(EvidenceModel):
    as_of: AwareDatetime
    lookback_days: Literal[30, 90]
    posting_count: int
    duplicate_posting_count: int
    sources: list[JobReference]
    market_demand: list[DemandStatistic]
    warnings: list[str]


class CareerAnalysisInput(EvidenceModel):
    snapshot: VerificationSnapshot = Field(repr=False)
    request: CareerAnalysisRequest
    candidates: list[JobVersion] = Field(max_length=1000)
    target: JobVersion | None = None


class CareerAnalysisOutput(EvidenceModel):
    analysis: CareerAnalysis
    catalog: SkillCatalog


# --- Learning resources and curriculum ------------------------------------------------


class ResourceValidationRequest(EvidenceModel):
    resource: LearningResourceInput


class ResourceSearchRequest(EvidenceModel):
    resources: list[LearningResourceVersion] = Field(max_length=500)
    skill_id: SkillID | None = None
    budget_krw: int | None = Field(default=None, ge=0, le=100000000)


class ResourceSearchResult(EvidenceModel):
    sources: list[LearningResourceVersion]


class GeneratorInfo(EvidenceModel):
    generator: dict[str, Any]


class PlanningInput(EvidenceModel):
    analysis: CareerAnalysis
    resources: list[LearningResourceVersion] = Field(max_length=500)
    request: CurriculumRequest


class GenerationInput(EvidenceModel):
    job_id: UUID
    analysis_id: UUID
    analysis_valid_until: AwareDatetime
    request: CurriculumRequest
    context: PlanningContext
    generator: dict[str, Any]


class GenerationOutcome(EvidenceModel):
    state: Literal["completed", "failed"]
    result: CurriculumResult | None = None
    error_code: str | None = None
    retryable: bool = False
    # Per-call LLM audit, never logged by the AI service. BE keeps only prompt_hash, provider_error,
    # usage, validation_errors and output_encoding (BE _audit_summary) as plain JSONB; it drops raw_output.
    audit: list[dict[str, Any]] = Field(default_factory=list, repr=False)

    @model_validator(mode="after")
    def consistent(self):
        if (self.state == "completed") != (self.result is not None):
            raise ValueError("Completed outcomes carry a result; failures do not")
        if (self.state == "failed") != (self.error_code is not None):
            raise ValueError("Failures carry an error code")
        return self
