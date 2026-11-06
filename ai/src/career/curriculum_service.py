"""Learning-resource retrieval and planning entry points (moved from BE curriculum_service).

BE keeps imports, idempotency, LLM account allowlists and the job queue. The functions
here receive BE's signed resource versions and the stored analysis and only compute.
"""

from datetime import datetime

from src.career.canonical import digest_json
from src.career.contracts.career import CareerAnalysis
from src.career.contracts.curriculum import CurriculumRequest, LearningResourceVersion
from src.career.curriculum_planner import build_context
from src.career.errors import EvidenceError, Rejection
from src.career.learning_retrieval import eligible_resource, retrieve_resources, validate_resource
from src.career.llm_client import ProviderFailure, selector_payload


def check_resources(versions: list[LearningResourceVersion]):
    """Defense in depth: BE verified the signatures; re-check content and citations."""
    for version in versions:
        try:
            if version.content_hash != digest_json(version.resource.model_dump(mode="json")):
                raise EvidenceError("RESOURCE_BINDING_MISMATCH")
            validate_resource(version.resource, version.imported_at)
        except EvidenceError as exc:
            raise Rejection(
                409, "LEARNING_SOURCE_INVALID", "Learning source integrity check failed"
            ) from exc


def search_resources(versions, skill_id, budget_krw, now: datetime):
    check_resources(versions)
    if skill_id:
        return retrieve_resources(versions, skill_id, budget_krw, now)
    return [
        item
        for item in versions
        if eligible_resource(item, now)
        and (
            budget_krw is None
            or item.resource.cost_krw is not None
            and item.resource.cost_krw <= budget_krw
        )
    ]


def planning_context(
    analysis: CareerAnalysis,
    resources: list[LearningResourceVersion],
    request: CurriculumRequest,
    now: datetime,
):
    if request.analysis_id != analysis.analysis_id:
        raise Rejection(409, "ANALYSIS_MISMATCH", "Curriculum request does not match the analysis")
    if analysis.valid_until <= now:
        raise Rejection(409, "STALE_ANALYSIS", "Create a fresh full-profile career analysis first")
    check_resources(resources)
    try:
        context = build_context(analysis, resources, request, now)
        if request.mode == "llm":
            selector_payload(context, request)  # Fail fast before queuing an oversized prompt.
    except (EvidenceError, ProviderFailure) as exc:
        raise Rejection(422, str(exc), str(exc)) from exc
    return context
