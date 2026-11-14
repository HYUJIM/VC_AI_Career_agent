"""Moved from BE tests/test_llm_client.py (logic unchanged)."""

import asyncio
import json
from uuid import uuid4
from datetime import datetime, timezone

import httpx
import pytest
from src.career.contracts.curriculum import CurriculumRequest
from src.career.curriculum_planner import build_context
from src.career.llm_client import CompatibleLLMClient, LLMConfig, ProviderFailure
from src.career.errors import ConfigurationError


def request_for(analysis, **overrides):
    return CurriculumRequest(analysis_id=analysis.analysis_id, idempotency_key=uuid4(), **overrides)


def test_provider_configuration_is_not_inferred():
    with pytest.raises(ConfigurationError):
        LLMConfig.from_env()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://[bad",
        "https://llm.example.test:not-a-port/v1",
        "https://user:password@llm.example.test/v1",
        "http://llm.example.test/v1",
    ],
)
def test_bad_endpoint_is_configuration_error(llm_settings, monkeypatch, endpoint):
    monkeypatch.setenv("CURRICULUM_LLM_BASE_URL", endpoint)
    with pytest.raises(ConfigurationError):
        LLMConfig.from_env()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"message": []}]},
    ],
)
def test_malformed_response_envelopes_are_safely_classified(llm_settings, make_analysis, payload):
    analysis = make_analysis()
    request = request_for(analysis)
    context = build_context(analysis, [], request, datetime.now(timezone.utc))
    client = CompatibleLLMClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    with pytest.raises(ProviderFailure, match="LLM_MALFORMED_RESPONSE"):
        asyncio.run(client.generate(context, request))


def test_compatible_request_has_no_tools_and_bounded_json_output(llm_settings, make_analysis):
    analysis = make_analysis()
    request = request_for(analysis)
    context = build_context(analysis, [], request, datetime.now(timezone.utc))

    def respond(http_request):
        payload = json.loads(http_request.content)
        assert http_request.url.path == "/v1/chat/completions"
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["max_completion_tokens"] == 4096
        assert payload["store"] is False
        assert "tools" not in payload
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": context.baseline.model_dump_json(),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )

    client = CompatibleLLMClient(transport=httpx.MockTransport(respond))
    result = asyncio.run(client.generate(context, request))
    assert result.complete and result.usage["prompt_tokens"] == 12


@pytest.mark.parametrize("status,retryable", [(429, True), (503, True), (401, False), (400, False)])
def test_provider_errors_are_redacted_and_classified(
    llm_settings, make_analysis, status, retryable
):
    analysis = make_analysis()
    request = request_for(analysis)
    context = build_context(analysis, [], request, datetime.now(timezone.utc))
    client = CompatibleLLMClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, text="private provider diagnostics must not escape")
        )
    )
    with pytest.raises(ProviderFailure) as exc:
        asyncio.run(client.generate(context, request))
    assert exc.value.retryable is retryable
    assert "private" not in str(exc.value)


def test_provider_tool_calls_are_not_executed(llm_settings, make_analysis):
    analysis = make_analysis()
    request = request_for(analysis)
    context = build_context(analysis, [], request, datetime.now(timezone.utc))
    client = CompatibleLLMClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": "{}",
                                "tool_calls": [{"function": {"name": "execute"}}],
                            },
                        }
                    ],
                },
            )
        )
    )
    with pytest.raises(ProviderFailure, match="MALFORMED_RESPONSE"):
        asyncio.run(client.generate(context, request))
