"""Anthropic Claude provider (src.career.claude_client) against a fake Messages API (httpx2.MockTransport).

No test calls the real API: every client gets an injected transport, and the key is a fresh fake.
"""

import asyncio
import importlib.util
import io
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest

from src.career import claude_client, llm_client
from src.career.claude_client import (
    DEFAULT_MODEL,
    DRAFT_RESPONSE_SCHEMA,
    FALLBACK_BETA,
    MAX_OUTPUT_TOKENS,
    ClaudeClient,
    ClaudeConfig,
)
from src.career.contracts.api import GenerationInput
from src.career.contracts.curriculum import PROMPT_VERSION, CurriculumDraft, CurriculumRequest
from src.career.curriculum_generator import generate
from src.career.curriculum_service import planning_context
from src.career.errors import ConfigurationError
from src.career.llm_client import ProviderFailure, generator_from_env, selector_payload

AI_DIR = Path(__file__).resolve().parents[2]


def message(text=None, stop_reason="end_turn", content=None, usage=None, model=DEFAULT_MODEL, **extra):
    """Message object as the Messages API returns it."""
    if content is None:
        content = [
            {"type": "thinking", "thinking": "", "signature": "opaque"},
            {"type": "text", "text": text},
        ]
    body = {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage if usage is not None else {"input_tokens": 1200, "output_tokens": 90},
    }
    body.update(extra)
    return body


def api_error(status, kind="rate_limit_error", text="private provider diagnostics"):
    return httpx2.Response(
        status, json={"type": "error", "error": {"type": kind, "message": text}}
    )


class FakeClaude:
    """Records every request the client sends; answers with `respond(request)`."""

    def __init__(self, respond):
        self.respond = respond
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.respond(request)

    @property
    def transport(self):
        return httpx2.MockTransport(self)


def ok(text):
    return FakeClaude(lambda _: httpx2.Response(200, json=message(text)))


@pytest.fixture
def planning(make_analysis, make_resource_version):
    analysis = make_analysis([{"quote": "Spark 실무 경험", "skill_ids": ["spark"]}])
    request = CurriculumRequest(
        analysis_id=analysis.analysis_id,
        idempotency_key=uuid4(),
        budget_krw=0,
        learning_choices=[{"skill_id": "spark", "action": "learn"}],
        mode="llm",
        allow_external_processing=True,
    )
    context = planning_context(
        analysis, [make_resource_version()], request, datetime.now(timezone.utc)
    )
    return analysis, request, context


def job_for(planning, generator):
    analysis, request, context = planning
    return GenerationInput(
        job_id=uuid4(),
        analysis_id=analysis.analysis_id,
        analysis_valid_until=analysis.valid_until,
        request=request,
        context=context,
        generator=generator,
    )


def run_job(job, fake):
    return asyncio.run(
        generate(job, lambda config: ClaudeClient(config, transport=fake.transport))
    )


def call(fake, planning):
    _, request, context = planning
    client = ClaudeClient(transport=fake.transport)
    return asyncio.run(client.generate(context, request))


# --- configuration --------------------------------------------------------------------------


@pytest.mark.parametrize("provider", [None, "", "  ", "claude", " claude "])
def test_claude_is_selected_by_default_or_explicitly(claude_settings, monkeypatch, provider):
    if provider is None:
        monkeypatch.delenv("CURRICULUM_LLM_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("CURRICULUM_LLM_PROVIDER", provider)
    config = generator_from_env()
    assert isinstance(config, ClaudeConfig)
    assert config.public_metadata() == {
        "provider": "claude",
        "api": "messages",
        "model": "claude-sonnet-5-5",
        "effort": "low",
        "refusal_fallback": True,
        "max_output_tokens": 16000,
        "prompt_version": PROMPT_VERSION,
    }
    assert claude_settings not in repr(config)
    assert claude_settings not in json.dumps(config.public_metadata())


def test_default_provider_is_disabled_by_default():
    assert "CURRICULUM_LLM_PROVIDER" not in os.environ
    with pytest.raises(ConfigurationError, match="CURRICULUM_LLM_ENABLED"):
        generator_from_env()


def test_env_example_blank_values_mean_the_documented_defaults(monkeypatch, claude_key):
    # ai/.env.example as copied to .env: only CURRICULUM_LLM_ENABLED and the key are filled in.
    from dotenv import dotenv_values

    values = dotenv_values(AI_DIR / ".env.example")
    assert values["CURRICULUM_LLM_ENABLED"] == "false" and values["ANTHROPIC_API_KEY"] == ""
    assert values["CURRICULUM_LLM_PROVIDER"] == "claude"
    for name, value in values.items():
        monkeypatch.setenv(name, value or "")
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", claude_key)
    config = generator_from_env()
    assert (config.model, config.effort) == (DEFAULT_MODEL, "low")
    assert llm_client.llm_max_concurrency() == 2


def test_missing_key_is_a_configuration_error(monkeypatch):
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY"):
        generator_from_env()


@pytest.mark.parametrize(
    "key", ["키" * 20, "ａｂｃｄｅｆ", "abc\x7fdef", "abc\x1bdef", "abc def", "abc\tdef", "abc\n", " abc"]
)
def test_key_must_be_printable_ascii_without_spaces(claude_settings, monkeypatch, key):
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    with pytest.raises(ConfigurationError) as exc:
        ClaudeConfig.from_env()
    assert key.strip() not in str(exc.value)


@pytest.mark.parametrize("name", ["GEMINI_API_KEY", "CURRICULUM_LLM_API_KEY", "ANTHROPIC_AUTH_TOKEN"])
def test_other_key_variables_are_not_used_for_claude(monkeypatch, name):
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    monkeypatch.setenv(name, "other-key-value")
    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY"):
        ClaudeConfig.from_env()


@pytest.mark.parametrize(
    "model", ["gemini-3.5-flash-lite", "gpt-5", "claude sonnet", "claude-" + "x" * 200, "../claude-x"]
)
def test_model_must_be_a_claude_model_id(claude_settings, monkeypatch, model):
    monkeypatch.setenv("CURRICULUM_LLM_MODEL", model)
    with pytest.raises(ConfigurationError, match="CURRICULUM_LLM_MODEL"):
        ClaudeConfig.from_env()


@pytest.mark.parametrize(
    "model,fallback", [("claude-haiku-5-5", False), ("claude-opus-5-5", True), ("claude-sonnet-5-5", True)]
)
def test_model_can_be_overridden_and_decides_the_refusal_fallback(claude_settings, monkeypatch, model, fallback):
    monkeypatch.setenv("CURRICULUM_LLM_MODEL", f" {model} ")
    config = ClaudeConfig.from_env()
    assert config.model == model and config.public_metadata()["refusal_fallback"] is fallback


@pytest.mark.parametrize("value,expected", [("", "low"), ("  ", "low"), ("MEDIUM", "medium"), ("max", "max")])
def test_effort_is_optional_and_normalized(claude_settings, monkeypatch, value, expected):
    monkeypatch.setenv("CURRICULUM_LLM_EFFORT", value)
    assert ClaudeConfig.from_env().effort == expected


@pytest.mark.parametrize("value", ["none", "minimal", "off", "0", "low high"])
def test_unknown_effort_is_a_configuration_error(claude_settings, monkeypatch, value):
    monkeypatch.setenv("CURRICULUM_LLM_EFFORT", value)
    with pytest.raises(ConfigurationError, match="CURRICULUM_LLM_EFFORT"):
        ClaudeConfig.from_env()


def test_settings_do_not_need_the_sdk(claude_key):
    # BE tests run this app in-process without the anthropic package: reading the settings and the
    # generator metadata must not import it.
    code = (
        "import sys; sys.modules['anthropic'] = None\n"
        "from src.career.llm_client import generator_from_env\n"
        "print(generator_from_env().public_metadata()['provider'])\n"
    )
    env = {**os.environ, "CURRICULUM_LLM_ENABLED": "true", "ANTHROPIC_API_KEY": claude_key}
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=AI_DIR, env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "claude"


def test_response_schema_matches_the_draft_model_with_supported_keywords():
    source = CurriculumDraft.model_json_schema()
    step = source["$defs"]["DraftStep"]
    sent = DRAFT_RESPONSE_SCHEMA
    assert sent["required"] == source["required"] and sent["additionalProperties"] is False
    assert sent["properties"].keys() == source["properties"].keys()
    items = sent["properties"]["steps"]["items"]
    assert items["additionalProperties"] is step["additionalProperties"] is False
    assert set(step["required"]) <= set(items["required"])
    assert items["properties"].keys() == step["properties"].keys() == set(items["required"])
    assert items["properties"]["hours"] == {"type": "integer"}
    assert items["properties"]["step_id"] == {"type": "string", "format": "uuid"}
    nullable = {option["type"] for option in step["properties"]["resource_version_id"]["anyOf"]}
    assert {option["type"] for option in items["properties"]["resource_version_id"]["anyOf"]} == nullable
    # Structured outputs reject numeric, string-length and complex array constraints.
    allowed = {"type", "properties", "required", "additionalProperties", "items", "anyOf", "format"}

    def keywords(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key != "properties":
                    yield key
                    yield from keywords(value)
                else:
                    for child in value.values():
                        yield from keywords(child)
        elif isinstance(node, list):
            for child in node:
                yield from keywords(child)

    assert set(keywords(sent)) <= allowed


# --- request ------------------------------------------------------------------------------------


def test_request_shape_key_header_and_payload(claude_settings, planning):
    _, request, context = planning
    fake = ok(context.baseline.model_dump_json())
    reply = call(fake, planning)
    assert reply.complete and reply.text == context.baseline.model_dump_json()
    [sent] = fake.requests
    assert sent.method == "POST"
    assert (sent.url.scheme, sent.url.host, sent.url.path) == ("https", "api.anthropic.com", "/v1/messages")
    assert sent.url.query == b"beta=true"  # the SDK's beta endpoint; nothing else in the URL
    assert sent.headers["x-api-key"] == claude_settings
    assert "authorization" not in sent.headers
    assert sent.headers["anthropic-beta"] == FALLBACK_BETA
    assert sent.headers["anthropic-version"]
    assert claude_settings not in str(sent.url) and claude_settings.encode() not in sent.content
    body = json.loads(sent.content)
    assert set(body) == {"model", "max_tokens", "system", "messages", "output_config", "fallbacks"}
    assert body["model"] == "claude-sonnet-5-5" and body["max_tokens"] == MAX_OUTPUT_TOKENS
    assert body["system"] == llm_client.SELECTOR_SYSTEM_PROMPT
    assert body["output_config"] == {
        "effort": "low", "format": {"type": "json_schema", "schema": DRAFT_RESPONSE_SCHEMA}
    }
    assert body["fallbacks"] == "default"
    [user] = body["messages"]
    assert user["role"] == "user"
    # The input is exactly the shared selector payload: no VC data, identity or URLs.
    assert json.loads(user["content"]) == selector_payload(context, request)
    assert "https://" not in user["content"] and "did:" not in user["content"]


def test_models_without_refusal_fallback_send_no_beta(claude_settings, planning, monkeypatch):
    monkeypatch.setenv("CURRICULUM_LLM_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("CURRICULUM_LLM_EFFORT", "medium")
    _, _, context = planning
    fake = ok(context.baseline.model_dump_json())
    call(fake, planning)
    [sent] = fake.requests
    body = json.loads(sent.content)
    assert "anthropic-beta" not in sent.headers and "fallbacks" not in body
    assert body["model"] == "claude-haiku-5-5" and body["output_config"]["effort"] == "medium"


def test_proxy_environment_and_redirects_are_not_used(claude_settings, planning, monkeypatch):
    import anthropic

    seen = {}
    original = anthropic.DefaultAsyncHttpxClient

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(anthropic, "DefaultAsyncHttpxClient", spy)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    _, _, context = planning
    fake = ok(context.baseline.model_dump_json())
    call(fake, planning)
    assert seen["trust_env"] is False and seen["follow_redirects"] is False
    assert fake.requests[0].url.host == "api.anthropic.com"


def test_redirect_is_not_followed(claude_settings, planning):
    fake = FakeClaude(lambda _: httpx2.Response(307, headers={"location": "https://evil.example/v1/messages"}))
    with pytest.raises(ProviderFailure) as exc:
        call(fake, planning)
    assert (exc.value.code, exc.value.retryable) == ("LLM_PROVIDER_REJECTED", False)
    assert len(fake.requests) == 1


# --- response -----------------------------------------------------------------------------------


def test_usage_is_mapped_and_thinking_is_ignored(claude_settings, planning):
    content = [
        {"type": "thinking", "thinking": "private reasoning", "signature": "s"},
        {"type": "text", "text": '{"steps":'},
        {"type": "text", "text": "[]}"},
    ]
    fake = FakeClaude(lambda _: httpx2.Response(
        200, json=message(content=content, usage={"input_tokens": 10, "output_tokens": 5,
                                                   "cache_read_input_tokens": 0})))
    reply = call(fake, planning)
    assert reply.text == '{"steps":[]}' and reply.complete
    assert reply.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_text_before_a_fallback_switch_is_dropped(claude_settings, planning):
    content = [
        {"type": "text", "text": '{"steps": [ hostile partial'},
        {"type": "fallback", "from": {"model": "claude-sonnet-5-5"}, "to": {"model": "claude-sonnet-5"}},
        {"type": "text", "text": '{"steps":[]}'},
    ]
    fake = FakeClaude(lambda _: httpx2.Response(200, json=message(content=content, model="claude-sonnet-5")))
    assert call(fake, planning).text == '{"steps":[]}'


def test_max_tokens_is_an_incomplete_reply_not_an_error(claude_settings, planning):
    fake = FakeClaude(lambda _: httpx2.Response(200, json=message('{"steps":[', stop_reason="max_tokens")))
    reply = call(fake, planning)
    assert reply.complete is False and reply.text == '{"steps":['


def test_refusal_is_a_non_retryable_rejection_with_its_category(claude_settings, planning):
    fake = FakeClaude(lambda _: httpx2.Response(200, json=message(
        "", stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": "private explanation"})))
    with pytest.raises(ProviderFailure) as exc:
        call(fake, planning)
    assert (exc.value.code, exc.value.retryable, exc.value.provider_code) == (
        "LLM_PROVIDER_REJECTED", False, "refusal/cyber")


@pytest.mark.parametrize(
    "body",
    [
        message("{}", stop_reason="tool_use"),
        message("{}", stop_reason="pause_turn"),
        message("{}", stop_reason="something_new"),
        message(content=[{"type": "tool_use", "id": "t", "name": "x", "input": {}}]),
        message(content=[]),
        message(content=[{"type": "thinking", "thinking": "", "signature": "s"}]),
    ],
)
def test_malformed_responses_are_classified(claude_settings, planning, body):
    fake = FakeClaude(lambda _: httpx2.Response(200, json=body))
    with pytest.raises(ProviderFailure) as exc:
        call(fake, planning)
    assert (exc.value.code, exc.value.retryable) == ("LLM_MALFORMED_RESPONSE", False)


@pytest.mark.parametrize("content", [b"not json", b"[]", b"[" * 100000])
def test_non_message_body_is_malformed(claude_settings, planning, content):
    fake = FakeClaude(lambda _: httpx2.Response(200, content=content, headers={"content-type": "application/json"}))
    with pytest.raises(ProviderFailure) as exc:
        call(fake, planning)
    assert (exc.value.code, exc.value.retryable) == ("LLM_MALFORMED_RESPONSE", False)


def test_oversized_text_is_refused(claude_settings, planning):
    fake = ok("x" * (claude_client.MAX_RESPONSE_BYTES + 1))
    with pytest.raises(ProviderFailure, match="LLM_RESPONSE_TOO_LARGE"):
        call(fake, planning)


@pytest.mark.parametrize(
    "status,retryable",
    [(400, False), (401, False), (403, False), (404, False), (413, False),
     (408, True), (429, True), (500, True), (502, True), (503, True), (504, True), (529, True)],
)
def test_http_errors_are_classified_by_status(claude_settings, planning, caplog, status, retryable):
    secret_message = "private provider diagnostics must not escape " + claude_settings
    fake = FakeClaude(lambda _: api_error(status, "overloaded_error", secret_message))
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderFailure) as exc:
        call(fake, planning)
    assert (exc.value.code, exc.value.retryable) == ("LLM_PROVIDER_REJECTED", retryable)
    assert (exc.value.http_status, exc.value.provider_code) == (status, "overloaded_error")
    assert len(fake.requests) == 1  # no SDK retries
    assert f"status={status}" in caplog.text and "type=overloaded_error" in caplog.text
    assert secret_message not in caplog.text and claude_settings not in caplog.text
    # The SDK error (with the provider's message) is not chained onto the failure.
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    assert claude_settings not in str(exc.value)


def test_error_type_that_is_not_an_identifier_is_not_logged(claude_settings, planning, caplog):
    fake = FakeClaude(lambda _: api_error(400, "bad type with spaces <script>"))
    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderFailure) as exc:
        call(fake, planning)
    assert exc.value.provider_code is None and "<script>" not in caplog.text


def test_connection_failure_is_retryable_unavailable(claude_settings, planning):
    def refuse(request):
        raise httpx2.ConnectError("connection refused", request=request)

    with pytest.raises(ProviderFailure) as exc:
        call(FakeClaude(refuse), planning)
    assert (exc.value.code, exc.value.retryable) == ("LLM_PROVIDER_UNAVAILABLE", True)


def test_overall_timeout_is_retryable_unavailable(claude_settings, planning, monkeypatch):
    monkeypatch.setattr(claude_client, "REQUEST_TIMEOUT_SECONDS", 0.05)

    class Slow(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(5)
            return httpx2.Response(200)

    _, request, context = planning
    client = ClaudeClient(transport=Slow())
    with pytest.raises(ProviderFailure) as exc:
        asyncio.run(client.generate(context, request))
    assert (exc.value.code, exc.value.retryable) == ("LLM_PROVIDER_UNAVAILABLE", True)


def test_context_over_the_payload_cap_is_not_sent(claude_settings, planning, monkeypatch):
    fake = ok("{}")
    monkeypatch.setattr(llm_client, "canonical_bytes", lambda payload: b"x" * 60001)
    with pytest.raises(ProviderFailure, match="LLM_CONTEXT_TOO_LARGE"):
        call(fake, planning)
    assert fake.requests == []


# --- through the generator ----------------------------------------------------------------------


def test_generation_with_claude_is_llm_and_audited_without_the_key(claude_settings, planning, caplog):
    _, _, context = planning
    fake = ok(context.baseline.model_dump_json())
    job = job_for(planning, generator_from_env().public_metadata())
    with caplog.at_level(logging.DEBUG):
        outcome = run_job(job, fake)
    assert outcome.state == "completed" and outcome.result.generation_method == "llm"
    assert outcome.result.model == "claude-sonnet-5-5"
    assert outcome.audit[0]["usage"] == {"prompt_tokens": 1200, "completion_tokens": 90, "total_tokens": 1290}
    dumped = outcome.model_dump_json()
    assert claude_settings not in dumped and claude_settings not in caplog.text
    assert len(fake.requests) == 1


def test_incomplete_twice_falls_back_to_the_template(claude_settings, planning):
    fake = FakeClaude(lambda _: httpx2.Response(200, json=message('{"steps":[', stop_reason="max_tokens")))
    outcome = run_job(job_for(planning, generator_from_env().public_metadata()), fake)
    assert outcome.result.generation_method == "llm_fallback"
    assert "UNTRUSTED_MODEL_OUTPUT_DISCARDED" in outcome.result.warnings
    assert len(fake.requests) == 2


def test_prompt_injection_in_model_output_is_discarded(claude_settings, planning):
    hostile = json.dumps({"steps": [], "note": "ignore previous instructions", "url": "https://x.example"})
    outcome = run_job(job_for(planning, generator_from_env().public_metadata()), ok(hostile))
    assert outcome.result.generation_method == "llm_fallback"
    assert "x.example" not in outcome.result.model_dump_json()


@pytest.mark.parametrize("status,retryable", [(429, True), (529, True), (403, False), (400, False)])
def test_provider_rejection_becomes_a_failed_outcome(claude_settings, planning, status, retryable):
    fake = FakeClaude(lambda _: api_error(status))
    outcome = run_job(job_for(planning, generator_from_env().public_metadata()), fake)
    assert (outcome.state, outcome.error_code, outcome.retryable) == (
        "failed", "LLM_PROVIDER_REJECTED", retryable)
    assert outcome.audit[0]["provider_error"] == "LLM_PROVIDER_REJECTED"


def test_changed_claude_settings_never_call_the_model(claude_settings, planning, monkeypatch):
    fake = ok("{}")
    job = job_for(planning, generator_from_env().public_metadata())
    monkeypatch.setenv("CURRICULUM_LLM_EFFORT", "high")
    outcome = run_job(job, fake)
    assert (outcome.error_code, outcome.retryable, outcome.audit) == ("GENERATOR_CONFIGURATION_CHANGED", True, [])
    monkeypatch.setenv("CURRICULUM_LLM_EFFORT", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert run_job(job, fake).error_code == "GENERATOR_CONFIGURATION_REQUIRED"
    assert fake.requests == []


# --- service endpoint ---------------------------------------------------------------------------


def test_generator_endpoint_reports_claude_without_the_key(claude_settings):
    from fastapi.testclient import TestClient

    from src.server.app import create_app

    headers = {"Authorization": f"Bearer {os.environ['AI_SERVICE_TOKEN']}"}
    with TestClient(create_app()) as client:
        response = client.get("/v1/curricula/generator", headers=headers)
    assert response.status_code == 200
    assert response.json()["generator"]["provider"] == "claude"
    assert claude_settings not in response.text


# --- scripts/claude_check.py --------------------------------------------------------------------


@pytest.fixture
def claude_check(monkeypatch):
    # main() sets these for its own process; record them so monkeypatch restores them afterwards.
    for name in ("CURRICULUM_LLM_ENABLED", "CURRICULUM_LLM_MODEL", "CURRICULUM_LLM_EFFORT"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    spec = importlib.util.spec_from_file_location("claude_check", AI_DIR / "scripts" / "claude_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def run(fake, *argv):
        out = io.StringIO()
        code = module.main(["--no-dotenv", *argv], transport=fake.transport if fake else None, out=out)
        return code, out.getvalue()

    return run


def test_check_script_success_prints_status_usage_and_validation_but_no_key(claude_check, monkeypatch, claude_key):
    monkeypatch.setenv("ANTHROPIC_API_KEY", claude_key)
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "false")  # the check does not need LLM mode on yet

    def respond(request):
        payload = json.loads(json.loads(request.content)["messages"][0]["content"])
        return httpx2.Response(200, json=message(json.dumps(payload["baseline"])))

    fake = FakeClaude(respond)
    code, output = claude_check(fake, "--effort", "medium")
    assert code == 0, output
    assert "model: claude-sonnet-5-5" in output and "effort: medium" in output
    assert "HTTP status: 200" in output and "served_by: claude-sonnet-5-5" in output
    assert "json_valid: yes" in output and "draft_valid: yes" in output and "result: OK" in output
    assert claude_key not in output
    assert json.loads(fake.requests[0].content)["output_config"]["effort"] == "medium"


def test_check_script_reports_rule_problems_and_invalid_json(claude_check, monkeypatch, claude_key):
    monkeypatch.setenv("ANTHROPIC_API_KEY", claude_key)
    code, output = claude_check(ok(json.dumps({"steps": []})))
    assert code == 0 and "draft_valid: no (STEPS_NOT_SELECTED_EXACTLY_ONCE)" in output
    code, output = claude_check(ok("not json"))
    assert code == 1 and "json_valid: no" in output and "result: FAILED" in output


def test_check_script_failure_prints_status_code_and_hint(claude_check, monkeypatch, claude_key):
    monkeypatch.setenv("ANTHROPIC_API_KEY", claude_key)
    code, output = claude_check(FakeClaude(lambda _: api_error(401, "authentication_error")))
    assert code == 1
    assert "HTTP status: 401" in output and "provider code=authentication_error" in output
    assert "hint: key rejected" in output and claude_key not in output


def test_check_script_configuration_error_exits_2_without_calling(claude_check, monkeypatch):
    fake = ok("{}")
    code, output = claude_check(fake)
    assert code == 2 and "ANTHROPIC_API_KEY" in output and fake.requests == []
