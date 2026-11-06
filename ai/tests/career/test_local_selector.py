"""Local Qwen selector adapter (no GPU needed: the research model is faked)."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.career.contracts.curriculum import CurriculumRequest
from src.career.curriculum_planner import build_context
from src.career.errors import ConfigurationError
from src.career.llm_client import ProviderFailure, generator_from_env
from src.career.local_selector import LocalSelectorClient, LocalSelectorConfig
from src.llm.transformers_llm import MODEL_ID, REVISION


class FakeModel:
    def __init__(self, text="", finish="eos", error=None):
        self.text, self.finish, self.error = text, finish, error
        self.messages = None
        self.last_generation = None

    def generate(self, messages):
        self.messages = messages
        if self.error:
            raise self.error
        self.last_generation = {
            "finish_reason": self.finish,
            "token_usage": {"input_tokens": 900, "output_tokens": 40, "total_tokens": 940},
        }
        return self.text


@pytest.fixture
def context(make_analysis):
    analysis = make_analysis()
    request = CurriculumRequest(analysis_id=analysis.analysis_id, idempotency_key=uuid4())
    return build_context(analysis, [], request, datetime.now(timezone.utc)), request


def test_provider_is_explicit_and_pins_the_research_revision(monkeypatch):
    monkeypatch.setenv("CURRICULUM_LLM_PROVIDER", "local-transformers")
    with pytest.raises(ConfigurationError):
        generator_from_env()
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    metadata = generator_from_env().public_metadata()
    assert metadata["model"] == MODEL_ID and metadata["revision"] == REVISION
    assert metadata["max_output_tokens"] == 256
    monkeypatch.setenv("CURRICULUM_LLM_PROVIDER", "guess")
    with pytest.raises(ConfigurationError):
        generator_from_env()


def test_uses_system_user_messages_and_maps_usage(context):
    current, request = context
    model = FakeModel(current.baseline.model_dump_json())
    reply = asyncio.run(LocalSelectorClient(lambda: model).generate(current, request))
    assert [item["role"] for item in model.messages] == ["system", "user"]
    assert reply.complete and reply.text == current.baseline.model_dump_json()
    assert reply.usage == {"prompt_tokens": 900, "completion_tokens": 40, "total_tokens": 940}


def test_length_cutoff_is_incomplete_not_trusted(context):
    current, request = context
    reply = asyncio.run(
        LocalSelectorClient(lambda: FakeModel("{", finish="length")).generate(current, request)
    )
    assert not reply.complete


@pytest.mark.parametrize(
    "error,code",
    [
        (ValueError("Input has 4000 tokens"), "LLM_CONTEXT_TOO_LARGE"),
        (RuntimeError("CUDA is required"), "LOCAL_MODEL_UNAVAILABLE"),
    ],
)
def test_model_errors_are_classified_without_details(context, error, code):
    current, request = context
    with pytest.raises(ProviderFailure) as exc:
        asyncio.run(LocalSelectorClient(lambda: FakeModel(error=error)).generate(current, request))
    assert exc.value.code == code and not exc.value.retryable
    assert "tokens" not in str(exc.value) and "CUDA" not in str(exc.value)


def test_config_never_loads_the_model_until_used():
    assert LocalSelectorConfig().create_client() is not None
