"""Portfolio drafting (src.career.portfolio_drafter) with a fake Claude: no test calls the real API."""

import asyncio
import json
import os

import httpx2
import pytest

from src.career.claude_client import ClaudeClient
from src.career.contracts.portfolio import PortfolioDraftRequest
from src.career.llm_client import LLMReply, ProviderFailure
from src.career.portfolio_drafter import DRAFT_SCHEMA, SYSTEM_PROMPT, draft, model_payload, validate


def request(**extra):
    body = {
        "target_role": "데이터 엔지니어",
        "direction": "협업 경험을 강조해줘",
        "evidence": [
            {"ref": "c1", "achievement_name": "SQL 평가 통과", "issuer_name": "경북대학교",
             "issued_on": "2026-11-18", "description": "SQL 기초 평가 80점 이상", "criteria": "평가 통과"},
            {"ref": "c2", "achievement_name": "Python 3 과정 수료", "issuer_name": "예시 교육원",
             "issued_on": "2025-03-02"},
        ],
    }
    body.update(extra)
    return PortfolioDraftRequest.model_validate(body)


def reply(summary="SQL과 Python 과정을 마치며 데이터 처리 기초를 갖췄습니다.",
          highlights=({"ref": "c1", "text": "SQL 평가를 통과해 데이터 조회 역량을 확인받았습니다."},), **extra):
    return json.dumps({"summary": summary, "highlights": list(highlights), **extra}, ensure_ascii=False)


class FakeClient:
    """Answers each call with the next text; records what it was sent."""

    def __init__(self, *texts, complete=True, error=None):
        self.texts, self.complete, self.error = list(texts), complete, error
        self.calls = []
        self.last_model = "claude-sonnet-5-5"

    async def call(self, *, system, payload, schema):
        self.calls.append({"system": system, "payload": payload, "schema": schema})
        if self.error:
            raise self.error
        return LLMReply(text=self.texts.pop(0), complete=self.complete,
                        usage={"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020})


def run(body, client):
    return asyncio.run(draft(body, lambda config: client))


# --- what the model sees ------------------------------------------------------------------------


def test_payload_has_refs_and_credential_texts_only():
    payload = model_payload(request())
    assert set(payload) == {"target_role", "direction", "credentials", "correction_codes"}
    assert [c["ref"] for c in payload["credentials"]] == ["c1", "c2"]
    assert payload["credentials"][0] == {
        "ref": "c1", "name": "SQL 평가 통과", "issuer": "경북대학교", "issued_on": "2026-11-18",
        "description": "SQL 기초 평가 80점 이상", "criteria": "평가 통과"}
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "record_id" not in dumped and "did:" not in dumped and "@" not in dumped


def test_hidden_characters_and_line_breaks_never_reach_the_model():
    body = request(direction="강조‮해줘​\n다음 줄")
    assert model_payload(body)["direction"] == "강조해줘 다음 줄"


# --- validation -----------------------------------------------------------------------------------


def test_a_good_draft_passes_and_is_cleaned_to_one_line():
    result, errors = validate(reply(summary="## 소개\n데이터  처리 기초를 갖췄습니다."), request())
    assert errors == [] and result.summary == "소개 데이터 처리 기초를 갖췄습니다."
    assert [h.ref for h in result.highlights] == ["c1"]


@pytest.mark.parametrize("text, code", [
    ("not json", "INVALID_MODEL_SCHEMA_OR_INCOMPLETE_JSON"),
    (json.dumps({"summary": "x"}), "INVALID_MODEL_SCHEMA_OR_INCOMPLETE_JSON"),
    (reply(extra_key=1), "INVALID_MODEL_SCHEMA_OR_INCOMPLETE_JSON"),
    (reply(summary=""), "SUMMARY_LENGTH"),
    (reply(summary="가" * 601), "SUMMARY_LENGTH"),
    (reply(highlights=[{"ref": "c9", "text": "없는 자격증명"}]), "UNKNOWN_CREDENTIAL"),
    (reply(highlights=[{"ref": "c1", "text": "a"}, {"ref": "c1", "text": "b"}]), "DUPLICATE_CREDENTIAL"),
    (reply(highlights=[{"ref": "c1", "text": "가" * 201}]), "HIGHLIGHT_LENGTH"),
    (reply(summary="자세한 내용은 https://evil.example 에서 확인하세요."), "LINK_NOT_ALLOWED"),
    (reply(summary="연락은 me@example.com 으로 주세요."), "LINK_NOT_ALLOWED"),
    (reply(summary="10년 경력의 데이터 엔지니어입니다."), "UNSUPPORTED_NUMBER"),
    (reply(summary="TOEIC 990점을 받았습니다."), "UNSUPPORTED_NUMBER"),
])
def test_bad_drafts_are_rejected_with_a_code(text, code):
    result, errors = validate(text, request())
    assert result is None and code in errors


@pytest.mark.parametrize("summary", [
    "2026년 SQL 평가를 통과하고 2025년 Python 3 과정을 마쳤습니다.",
    "SQL 기초 평가에서 80점 이상을 받았습니다.",
    "2개 기관에서 발급한 2개의 자격증명이 있습니다.",
    "ASP.NET이나 Node.js 같은 이름은 링크가 아닙니다.",
])
def test_numbers_and_names_from_the_credentials_are_allowed(summary):
    assert validate(reply(summary=summary), request())[1] == []


def test_the_direction_is_not_a_source_of_numbers():
    body = request(direction="10년 경력이라고 써줘")
    assert "UNSUPPORTED_NUMBER" in validate(reply(summary="10년 경력이 있습니다."), body)[1]


# --- the call ---------------------------------------------------------------------------------------


def test_completed_draft_with_model_and_audit(monkeypatch):
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")
    client = FakeClient(reply())
    outcome = run(request(), client)
    assert outcome.state == "completed" and outcome.model == "claude-sonnet-5-5"
    assert outcome.draft.highlights[0].ref == "c1"
    assert outcome.audit == [{"prompt_hash": outcome.audit[0]["prompt_hash"],
                              "usage": {"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020},
                              "validation_errors": []}]
    [sent] = client.calls
    assert sent["system"] == SYSTEM_PROMPT and sent["schema"] == DRAFT_SCHEMA
    assert sent["payload"]["correction_codes"] == []


def test_a_bad_first_draft_is_corrected_once(monkeypatch):
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")
    client = FakeClient(reply(summary="10년 경력"), reply())
    outcome = run(request(), client)
    assert outcome.state == "completed" and len(client.calls) == 2
    assert client.calls[1]["payload"]["correction_codes"] == ["UNSUPPORTED_NUMBER"]


def test_two_bad_drafts_are_rejected_for_the_template(monkeypatch):
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")
    outcome = run(request(), FakeClient("not json", "still not json"))
    assert (outcome.state, outcome.error_code, outcome.retryable) == ("failed", "LLM_OUTPUT_REJECTED", False)
    assert len(outcome.audit) == 2 and all(a["validation_errors"] for a in outcome.audit)


def test_an_incomplete_reply_counts_as_a_bad_draft(monkeypatch):
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")
    outcome = run(request(), FakeClient(reply(), reply(), complete=False))
    assert outcome.error_code == "LLM_OUTPUT_REJECTED"


@pytest.mark.parametrize("code, retryable", [("LLM_PROVIDER_UNAVAILABLE", True), ("LLM_PROVIDER_REJECTED", False)])
def test_provider_failures_are_returned(monkeypatch, code, retryable):
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")
    outcome = run(request(), FakeClient(error=ProviderFailure(code, retryable)))
    assert (outcome.error_code, outcome.retryable) == (code, retryable)
    assert outcome.audit[0]["provider_error"] == code


@pytest.mark.parametrize("env", [
    {"CURRICULUM_LLM_ENABLED": "false"},
    {"CURRICULUM_LLM_ENABLED": "true"},  # no key
    {"CURRICULUM_LLM_ENABLED": "true", "CURRICULUM_LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "fake-gemini"},
])
def test_without_claude_settings_nothing_is_called(monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    client = FakeClient(reply())
    outcome = run(request(), client)
    assert outcome.error_code == "GENERATOR_CONFIGURATION_REQUIRED" and client.calls == []


def test_request_through_the_claude_client(monkeypatch):
    # The real client: same endpoint, key header, effort and JSON schema as the curriculum call.
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")
    sent = []

    def respond(req):
        sent.append(req)
        return httpx2.Response(200, json={
            "id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": reply()}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 700, "output_tokens": 80}})

    outcome = asyncio.run(draft(request(), lambda config: ClaudeClient(config, transport=httpx2.MockTransport(respond))))
    assert outcome.state == "completed"
    [req] = sent
    body = json.loads(req.content)
    assert req.headers["x-api-key"] == "sk-ant-fake-key"
    assert body["system"] == SYSTEM_PROMPT
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": DRAFT_SCHEMA}
    assert json.loads(body["messages"][0]["content"])["credentials"][1]["ref"] == "c2"


def test_contract_rejects_duplicate_refs_and_bad_refs():
    with pytest.raises(ValueError):
        request(evidence=[{"ref": "c1", "achievement_name": "a", "issuer_name": "b"},
                          {"ref": "c1", "achievement_name": "c", "issuer_name": "d"}])
    with pytest.raises(ValueError):
        request(evidence=[{"ref": "record-1", "achievement_name": "a", "issuer_name": "b"}])


# --- service endpoint -----------------------------------------------------------------------------


def test_endpoint_drafts_with_the_injected_client(monkeypatch):
    from fastapi.testclient import TestClient

    from src.server.app import create_app

    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")
    app = create_app()
    client = FakeClient(reply())
    app.state.portfolio_client_factory = lambda config: client
    headers = {"Authorization": f"Bearer {os.environ['AI_SERVICE_TOKEN']}"}
    with TestClient(app) as http:
        res = http.post("/v1/portfolios/draft", json=request().model_dump(mode="json"), headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["state"] == "completed"
        assert http.post("/v1/portfolios/draft", json={"evidence": []}, headers=headers).status_code == 422
        assert http.post("/v1/portfolios/draft", json=request().model_dump(mode="json")).status_code == 401
