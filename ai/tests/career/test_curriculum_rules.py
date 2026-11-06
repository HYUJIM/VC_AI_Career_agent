"""Moved from BE tests/test_curriculum_rules.py (logic unchanged)."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from src.career.contracts.curriculum import (
    CurriculumDraft,
    CurriculumRequest,
    LearningResourceInput,
)
from src.career.curriculum_planner import build_context, comparison_ref
from src.career.curriculum_generator import parse_draft
from src.career.learning_retrieval import retrieve_resources, validate_resource
from src.career.llm_client import selector_payload
from src.career.output_validator import render_steps, validate_draft
from src.career.errors import EvidenceError
from pydantic import ValidationError


def request_for(analysis, **overrides):
    return CurriculumRequest(
        analysis_id=analysis.analysis_id,
        idempotency_key=uuid4(),
        **overrides,
    )


def test_unselected_or_is_a_question_not_both_learning_tracks(make_analysis):
    analysis = make_analysis()
    request = request_for(analysis)
    context = build_context(analysis, [], request, datetime.now(timezone.utc))
    assert any(item.code == "SELECT_ONE_ALTERNATIVE" for item in context.questions)
    assert not {"python", "scala"} & {item.skill_id for item in context.options}
    assert validate_draft(context.baseline, context, request) == []


def test_explicit_or_choice_only_learns_one_language(make_analysis):
    analysis = make_analysis()
    alternative = next(c for c in analysis.comparisons if c.requirement.match == "any")
    request = request_for(
        analysis,
        alternative_choices=[
            {
                "comparison_ref": comparison_ref(alternative),
                "skill_id": "python",
            }
        ],
    )
    context = build_context(analysis, [], request, datetime.now(timezone.utc))
    assert "python" in {item.skill_id for item in context.options}
    assert "scala" not in {item.skill_id for item in context.options}


def test_out_of_scope_learning_choice_is_rejected(make_analysis):
    analysis = make_analysis()
    request = request_for(analysis, learning_choices=[{"skill_id": "docker", "action": "learn"}])
    with pytest.raises(EvidenceError, match="OUTSIDE_ACTIONABLE"):
        build_context(analysis, [], request, datetime.now(timezone.utc))


def test_unknown_conditions_remain_manual_questions(make_analysis):
    analysis = make_analysis([{"quote": "SQL 실무 3년", "skill_ids": ["sql"]}])
    context = build_context(analysis, [], request_for(analysis), datetime.now(timezone.utc))
    assert not context.options
    assert context.questions[0].code == "MANUAL_ASSESSMENT_REQUIRED"


def test_prerequisites_and_weekly_budget(make_analysis, make_resource, make_resource_version):
    analysis = make_analysis([{"quote": "Spark 경험", "skill_ids": ["spark"]}])
    request = request_for(analysis, weeks=4, hours_per_week=2, budget_krw=0)
    resource = make_resource_version(make_resource(prerequisites=["python"]))
    context = build_context(analysis, [resource], request, datetime.now(timezone.utc))
    assert validate_draft(context.baseline, context, request) == []
    steps, cost, unknown = render_steps(context.baseline, context, request)
    assert cost == 0 and not unknown
    assert any(
        step.skill_id == "python" and step.purpose == "resource_prerequisite" for step in steps
    )
    assert any(step.conditional for step in steps)
    allocations = {}
    for step in steps:
        for slot in step.allocations:
            allocations[slot.week] = allocations.get(slot.week, 0) + slot.hours
    assert max(allocations) <= 4
    assert all(value <= 2 for value in allocations.values())
    reversed_draft = CurriculumDraft(steps=list(reversed(context.baseline.steps)))
    assert "PREREQUISITE_ORDER" in validate_draft(reversed_draft, context, request)


def test_budget_too_small_defers_whole_track_without_shrinking_estimates(make_analysis):
    analysis = make_analysis([{"quote": "Spark 경험", "skill_ids": ["spark"]}])
    request = request_for(analysis, weeks=1, hours_per_week=1)
    context = build_context(analysis, [], request, datetime.now(timezone.utc))
    assert not context.options
    assert context.questions[0].code == "TIME_BUDGET_EXCEEDED"


def test_shared_prerequisite_keeps_direct_learning_choice(
    make_analysis, make_resource, make_resource_version
):
    analysis = make_analysis(
        [
            {"quote": "Spark 실무 경험", "skill_ids": ["spark"]},
            {"quote": "SQL 실무 경험", "skill_ids": ["sql"]},
        ]
    )
    request = request_for(analysis, learning_choices=[{"skill_id": "sql", "action": "learn"}])
    source = make_resource_version(make_resource(prerequisites=["sql"]))
    context = build_context(analysis, [source], request, datetime.now(timezone.utc))
    sql = next(
        item for item in context.options if item.skill_id == "sql" and item.kind == "practice"
    )
    assert sql.purpose == "gap_followup"
    assert sql.conditional is False
    assert len(sql.comparison_refs) == 2
    assert validate_draft(context.baseline, context, request) == []


def test_cyclic_course_prerequisites_are_not_ignored(
    make_analysis, make_resource, make_resource_version
):
    analysis = make_analysis([{"quote": "Spark 경험", "skill_ids": ["spark"]}])
    resources = [
        make_resource_version(make_resource("spark", ["python"])),
        make_resource_version(make_resource("python", ["spark"])),
    ]
    context = build_context(analysis, resources, request_for(analysis), datetime.now(timezone.utc))
    assert not context.options
    assert context.questions[0].code == "CYCLIC_RESOURCE_PREREQUISITES"


def test_retrieval_excludes_unknown_fees_under_strict_budget_and_stale_fixtures(
    make_resource, make_resource_version
):
    now = datetime.now(timezone.utc)
    valid = make_resource_version(make_resource())
    sources = [
        valid,
        make_resource_version(make_resource(cost=None)),
        make_resource_version(make_resource(cost=10000)),
        make_resource_version(make_resource(dataset_kind="synthetic")),
        make_resource_version(make_resource(captured_at=now - timedelta(days=31))),
        make_resource_version(make_resource(available=False)),
    ]
    assert retrieve_resources(sources, "spark", 0, now) == [valid]


def test_resource_annotations_and_prices_require_exact_citations(make_resource):
    source = make_resource()
    validate_resource(source, datetime.now(timezone.utc))
    data = source.model_dump(mode="json")
    data["cost_krw"] = 5000
    with pytest.raises(EvidenceError):
        validate_resource(LearningResourceInput.model_validate(data), datetime.now(timezone.utc))
    data["cost_annotation"]["quote"] = "invented"
    with pytest.raises(ValidationError):
        LearningResourceInput.model_validate(data)


def test_prerequisite_review_cannot_be_omitted_or_assumed(make_resource):
    data = make_resource().model_dump(mode="json")
    data.pop("prerequisites_reviewed")
    with pytest.raises(ValidationError):
        LearningResourceInput.model_validate(data)
    data["prerequisites_reviewed"] = False
    with pytest.raises(ValidationError):
        LearningResourceInput.model_validate(data)


@pytest.mark.parametrize(
    "change",
    [
        {"hours_per_week": True},
        {"weeks": 0},
        {"budget_krw": -1},
        {"mode": "llm", "allow_external_processing": False},
    ],
)
def test_request_constraints_and_explicit_consent(change):
    with pytest.raises(ValidationError):
        CurriculumRequest.model_validate(
            {
                "analysis_id": str(uuid4()),
                "idempotency_key": str(uuid4()),
                **change,
            }
        )


@pytest.mark.parametrize(
    "text",
    [
        '{"steps":[],"user_claim":"Spark expert"}',
        '{"steps":[],"url":"https://invented.example"}',
        '{"steps":[],"steps":[]}',
        '```json\n{"steps":[]}\n```',
        '{"steps":[{"step_id":"not-a-uuid","hours":2}]}',
    ],
)
def test_extra_prose_urls_duplicate_json_and_unknown_schema_are_rejected(text):
    with pytest.raises((ValueError, ValidationError)):
        parse_draft(text)


def test_unknown_resources_steps_and_hours_are_blocked(make_analysis):
    analysis = make_analysis()
    request = request_for(analysis)
    context = build_context(analysis, [], request, datetime.now(timezone.utc))
    draft = context.baseline.model_copy(deep=True)
    draft.steps[0].resource_version_id = uuid4()
    assert "UNRETRIEVED_RESOURCE" in validate_draft(draft, context, request)
    draft.steps[0].step_id = uuid4()
    assert validate_draft(draft, context, request) == ["STEP_SET_MISMATCH"]


def test_external_prompt_contains_no_vc_identity_or_job_documents(make_analysis, mapped_snapshot):
    analysis = make_analysis()
    request = request_for(analysis)
    context = build_context(analysis, [], request, datetime.now(timezone.utc))
    prompt = str(selector_payload(context, request))
    assert mapped_snapshot.subject_did not in prompt
    assert "SQL exam passed" not in prompt
    assert analysis.sources[0].source_url not in prompt
    assert analysis.sources[0].company not in prompt
