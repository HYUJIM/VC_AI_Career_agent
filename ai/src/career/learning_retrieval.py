import re
from datetime import datetime, timedelta

from src.career.contracts.curriculum import LearningResourceInput, LearningResourceVersion
from src.career.job_requirements import OR_PATTERN, infer_logic_normalized
from src.career.errors import EvidenceError
from src.career.skill_catalog import (
    bounded_normalized_text,
    contains_skill,
    mentions_skill,
    normalized_text,
)

RESOURCE_FRESHNESS_DAYS = 30
MAX_RESOURCES = 500
# 선수지식 주석 하나는 필수 기술 하나만 가리켜야 한다. 공고 요구사항의 "또는" 어휘(OR_PATTERN)에
# "/"만 더해 그대로 써서 두 검사가 다시 어긋나지 않게 한다.
AMBIGUOUS_PREREQUISITE_PATTERN = OR_PATTERN + r"|/"


def validate_resource(resource: LearningResourceInput, now: datetime):
    if resource.captured_at > now:
        raise EvidenceError("RESOURCE_CAPTURE_IS_IN_THE_FUTURE")
    texts = []  # 주석 인용마다 한 번만 정규화하고, 아래 선수지식 검사에서 그 결과를 다시 쓴다.
    for annotation in [*resource.skill_annotations, *resource.prerequisite_annotations]:
        text = bounded_normalized_text(annotation.quote)
        if not mentions_skill(text, annotation.skill_id):
            raise EvidenceError("RESOURCE_SKILL_NOT_IN_CITED_TEXT")
        texts.append(text)
    for text in texts[len(resource.skill_annotations) :]:  # 선수지식 인용
        if infer_logic_normalized(text) in {"any", "unspecified"}:
            # Single prerequisite text is fine; disjunctions need a richer model.
            if re.search(AMBIGUOUS_PREREQUISITE_PATTERN, text):
                raise EvidenceError("AMBIGUOUS_RESOURCE_PREREQUISITES")
    if resource.cost_krw == 0 and not re.search(
        r"\bfree\b|무료", normalized_text(resource.cost_annotation.quote)
    ):
        raise EvidenceError("FREE_RESOURCE_REQUIRES_EXPLICIT_FREE_CITATION")
    if resource.cost_krw:
        amount = re.escape(str(resource.cost_krw))
        quote = re.sub(r"[,\s]", "", resource.cost_annotation.quote)
        if not re.search(r"(?<!\d)" + amount + r"(?!\d)", quote):
            raise EvidenceError("RESOURCE_FEE_NOT_IN_CITED_TEXT")


def eligible_resource(version: LearningResourceVersion, now: datetime) -> bool:
    source = version.resource
    return (
        source.dataset_kind == "real"
        and source.available
        and now - timedelta(days=RESOURCE_FRESHNESS_DAYS) < source.captured_at <= now
    )


def retrieve_resources(versions, skill_id, budget_krw, now):
    """Grounded structured/keyword retrieval, not vector similarity or web search."""
    candidates = [
        version
        for version in versions
        if eligible_resource(version, now)
        and any(item.skill_id == skill_id for item in version.resource.skill_annotations)
        and (
            budget_krw is None
            or version.resource.cost_krw is not None
            and version.resource.cost_krw <= budget_krw
        )
    ]
    # Prefer known, low-cost introductory resources; deterministic ties.
    levels = {"beginner": 0, "intermediate": 1, "advanced": 2}
    return sorted(
        candidates,
        key=lambda item: (
            item.resource.cost_krw is None,
            item.resource.cost_krw or 0,
            levels[item.resource.level],
            -int(contains_skill(item.resource.title, skill_id)),
            item.resource.minimum_study_hours,
            str(item.version_id),
        ),
    )
