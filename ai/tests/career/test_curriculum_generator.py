"""Generation semantics formerly asserted through BE's worker DB tests."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.career.contracts.api import GenerationInput
from src.career.contracts.curriculum import CurriculumRequest
from src.career.curriculum_generator import generate
from src.career.curriculum_service import planning_context
from src.career.errors import Rejection
from src.career.llm_client import LLMConfig, LLMReply, ProviderFailure


class ScriptedClient:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = 0

    async def generate(self, context, request, errors=None):
        self.calls += 1
        response = self.replies.pop(0) if self.replies else context.baseline.model_dump_json()
        if isinstance(response, Exception):
            raise response
        return LLMReply(response, usage={"prompt_tokens": 12, "completion_tokens": 8})


@pytest.fixture
def job(make_analysis, make_resource_version):
    def make(mode="template", generator=None):
        analysis = make_analysis([{"quote": "Spark 실무 경험", "skill_ids": ["spark"]}])
        request = CurriculumRequest(
            analysis_id=analysis.analysis_id,
            idempotency_key=uuid4(),
            budget_krw=0,
            learning_choices=[{"skill_id": "spark", "action": "learn"}],
            mode=mode,
            allow_external_processing=mode == "llm",
        )
        resource = make_resource_version()
        context = planning_context(analysis, [resource], request, datetime.now(timezone.utc))
        return GenerationInput(
            job_id=uuid4(),
            analysis_id=analysis.analysis_id,
            analysis_valid_until=analysis.valid_until,
            request=request,
            context=context,
            generator=generator or {"mode": "template"},
        ), resource

    return make


def run(job, client=None):
    return asyncio.run(generate(job, (lambda _: client) if client else None))


def test_template_uses_only_retrieved_resource(job):
    current, resource = job()
    outcome = run(current)
    assert outcome.state == "completed" and outcome.audit == []
    result = outcome.result
    assert result.generation_method == "template" and result.model is None
    assert result.total_hours == 3 and result.known_cost_krw == 0
    assert result.steps[0].resource.version_id == resource.version_id
    assert current.context.valid_until <= current.analysis_valid_until


def test_model_repair_is_bounded_and_audited(job, llm_settings):
    current, _ = job("llm", LLMConfig.from_env().public_metadata())
    client = ScriptedClient(['{"steps":[],"invented_claim":"expert"}'])
    outcome = run(current, client)
    assert outcome.result.generation_method == "llm" and client.calls == 2
    assert outcome.result.validation_errors == []
    assert len(outcome.audit) == 2 and outcome.audit[0]["validation_errors"]


def test_repeated_invalid_output_becomes_explicit_fallback(job, llm_settings):
    current, _ = job("llm", LLMConfig.from_env().public_metadata())
    hostile = '{"steps":[],"url":"https://invented.example","facts":"Spark expert"}'
    outcome = run(current, ScriptedClient([hostile, hostile]))
    assert outcome.result.status == "partial"
    assert outcome.result.generation_method == "llm_fallback"
    assert "invented.example" not in outcome.result.model_dump_json()
    assert "UNTRUSTED_MODEL_OUTPUT_DISCARDED" in outcome.result.warnings


def test_invalid_unicode_output_does_not_break_audit(job, llm_settings):
    current, _ = job("llm", LLMConfig.from_env().public_metadata())
    outcome = run(current, ScriptedClient([chr(0xD800)] * 2))
    assert outcome.result.generation_method == "llm_fallback"
    assert outcome.audit[0]["output_encoding"] == "utf8-backslashreplace"


def test_provider_failure_is_retryable_failure_without_result(job, llm_settings):
    current, _ = job("llm", LLMConfig.from_env().public_metadata())
    client = ScriptedClient([ProviderFailure("LLM_PROVIDER_UNAVAILABLE", True)])
    outcome = run(current, client)
    assert (outcome.state, outcome.error_code, outcome.retryable) == (
        "failed",
        "LLM_PROVIDER_UNAVAILABLE",
        True,
    )
    assert outcome.result is None and outcome.audit[0]["provider_error"]


def test_changed_or_missing_generator_configuration_never_calls_model(
    job, llm_settings, monkeypatch
):
    current, _ = job("llm", {"model": "frozen-at-enqueue"})
    client = ScriptedClient()
    assert run(current, client).error_code == "UNTRUSTED_OR_UNSUPPORTED_JOB_INPUT"
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "false")
    assert run(current, client).error_code == "GENERATOR_CONFIGURATION_REQUIRED"
    assert client.calls == 0


def test_context_rejects_mismatched_or_stale_analysis(make_analysis):
    analysis = make_analysis()
    request = CurriculumRequest(analysis_id=uuid4(), idempotency_key=uuid4())
    with pytest.raises(Rejection) as exc:
        planning_context(analysis, [], request, datetime.now(timezone.utc))
    assert exc.value.status == 409
