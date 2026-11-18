"""AI(LLM) 모드 포트폴리오: BE → 같은 프로세스의 AI 서비스 → 가짜 Claude 클라이언트 (실제 API·SDK를 부르지 않는다).

BE 테스트 환경에는 anthropic SDK가 없다: AI 앱의 portfolio_client_factory에 SDK를 쓰지 않는 가짜를 넣는다.
"""
import json

import pytest

from tests.test_features import create_portfolio, store_badge

SUMMARY = "SQL 평가를 통과해 데이터 조회 기초를 갖췄습니다."
HIGHLIGHT = "데이터 엔지니어에게 필요한 SQL 기초를 보여 줍니다."


class FakeClaude:
    def __init__(self):
        self.calls, self.texts, self.error = [], [], None
        self.last_model = "claude-sonnet-5-5"

    def answer(self, summary=SUMMARY, highlights=None):
        highlights = [{"ref": "c1", "text": HIGHLIGHT}] if highlights is None else highlights
        self.texts.append(json.dumps({"summary": summary, "highlights": highlights}, ensure_ascii=False))

    async def call(self, *, system, payload, schema):
        from src.career.llm_client import LLMReply

        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        text = self.texts.pop(0) if self.texts else json.dumps(
            {"summary": SUMMARY, "highlights": [{"ref": "c1", "text": HIGHLIGHT}]}, ensure_ascii=False)
        return LLMReply(text=text, complete=True, usage={"prompt_tokens": 800, "completion_tokens": 90})


@pytest.fixture
def claude(ai_app, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    monkeypatch.setenv("CURRICULUM_LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-for-tests")
    monkeypatch.setenv("CURRICULUM_LLM_USER_IDS", "*")
    get_settings.cache_clear()
    fake = FakeClaude()
    ai_app.state.portfolio_client_factory = lambda config: fake
    yield fake
    get_settings.cache_clear()


def llm(**extra):
    return {"mode": "llm", "allow_external_processing": True, "direction": "협업 경험을 강조해줘", **extra}


def test_llm_portfolio_puts_labelled_ai_sentences_next_to_verified_facts(client, register, claude):
    user = register("홍길동")
    record_id, _ = store_badge(client, user, name="SQL 평가 통과")
    res = create_portfolio(client, user, [record_id], **llm())
    assert res.status_code == 201, res.text
    p = res.json()["data"]
    assert (p["generator"], p["model"], p["warnings"]) == ("llm", "claude-sonnet-5-5", [])
    body = p["content_markdown"]
    assert "(AI 작성:" in body and SUMMARY in body
    assert f"- 관련성(AI 작성): {HIGHLIGHT}" in body
    # 사실 근거는 그대로 서버 틀이 쓴다.
    assert "### SQL 평가 통과" in body and "- 검증: 서명·발급기관·폐기 여부 확인 완료" in body
    assert "- 이름: 홍길동" in body
    # AI로는 ref와 자격증명 문구, 희망 직무, 작성 방향만 간다 (ID·이름·이메일·DID 없음).
    [payload] = claude.calls
    sent = json.dumps(payload, ensure_ascii=False)
    assert payload["credentials"][0]["ref"] == "c1" and payload["direction"] == "협업 경험을 강조해줘"
    assert payload["target_role"] == "백엔드 개발자"
    for private in (record_id, "홍길동", user["email"], user["did"]):
        assert private not in sent


def test_template_mode_never_calls_the_model(client, register, claude):
    user = register()
    record_id, _ = store_badge(client, user)
    res = create_portfolio(client, user, [record_id], direction="무시되는 방향")
    assert res.status_code == 201 and res.json()["data"]["generator"] == "template"
    assert "(AI 작성:" not in res.json()["data"]["content_markdown"] and claude.calls == []


def test_llm_mode_needs_consent_and_an_allowed_account(client, register, claude, monkeypatch):
    from app.core.config import get_settings

    user = register()
    record_id, _ = store_badge(client, user)
    res = create_portfolio(client, user, [record_id], **llm(allow_external_processing=False))
    assert res.status_code == 422
    monkeypatch.setenv("CURRICULUM_LLM_USER_IDS", "")
    get_settings.cache_clear()
    res = create_portfolio(client, user, [record_id], **llm())
    assert res.status_code == 403
    assert claude.calls == [] and client.get("/api/v1/portfolios", headers=user["headers"]).json()["data"] == []


@pytest.mark.parametrize("direction", ["첫 줄\n## 가짜 섹션", "가" * 201])
def test_direction_must_be_one_short_line(client, register, claude, direction):
    user = register()
    record_id, _ = store_badge(client, user)
    assert create_portfolio(client, user, [record_id], **llm(direction=direction)).status_code == 422
    assert claude.calls == []


def test_rejected_ai_text_falls_back_to_the_template(client, register, claude):
    user = register()
    record_id, _ = store_badge(client, user)
    claude.answer(summary="TOEIC 9999점과 37년 경력을 갖췄습니다.")
    claude.answer(summary="연락처 https://evil.example")
    res = create_portfolio(client, user, [record_id], **llm())
    assert res.status_code == 201, res.text
    p = res.json()["data"]
    assert (p["generator"], p["warnings"]) == ("llm_fallback", ["UNTRUSTED_MODEL_OUTPUT_DISCARDED"])
    assert "9999" not in p["content_markdown"] and "evil.example" not in p["content_markdown"]
    assert "검증된 자격증명을 근거로 역량을 증명합니다" in p["content_markdown"]
    assert len(claude.calls) == 2


@pytest.mark.parametrize("code, retryable, status", [
    ("LLM_PROVIDER_UNAVAILABLE", True, 503),
    ("LLM_PROVIDER_REJECTED", True, 503),
    ("LLM_PROVIDER_REJECTED", False, 422),
])
def test_provider_failures_save_nothing(client, register, claude, code, retryable, status):
    from src.career.llm_client import ProviderFailure

    user = register()
    record_id, _ = store_badge(client, user)
    claude.error = ProviderFailure(code, retryable)
    res = create_portfolio(client, user, [record_id], **llm())
    assert res.status_code == status and res.json()["code"] == code
    assert ("Retry-After" in res.headers) == retryable
    assert client.get("/api/v1/portfolios", headers=user["headers"]).json()["data"] == []


def test_daily_limit_per_account(client, register, claude, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("PORTFOLIO_LLM_DAILY_LIMIT", "1")
    get_settings.cache_clear()
    user = register()
    record_id, _ = store_badge(client, user)
    assert create_portfolio(client, user, [record_id], **llm()).status_code == 201
    res = create_portfolio(client, user, [record_id], **llm())
    assert res.status_code == 429 and int(res.headers["Retry-After"]) > 0
    assert "Daily LLM portfolio limit" in res.json()["detail"] and len(claude.calls) == 1
    # 템플릿 모드는 그대로 된다.
    assert create_portfolio(client, user, [record_id]).status_code == 201


def test_service_wide_daily_limit(client, register, claude, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("PORTFOLIO_LLM_GLOBAL_DAILY_LIMIT", "1")
    get_settings.cache_clear()
    alice, bob = register(), register()
    alice_record, _ = store_badge(client, alice)
    bob_record, _ = store_badge(client, bob)
    assert create_portfolio(client, alice, [alice_record], **llm()).status_code == 201
    res = create_portfolio(client, bob, [bob_record], **llm())
    assert res.status_code == 429 and "service limit" in res.json()["detail"]


def test_attempt_is_returned_when_the_model_was_not_called(client, register, claude, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("PORTFOLIO_LLM_DAILY_LIMIT", "1")
    get_settings.cache_clear()
    user = register()
    record_id, _ = store_badge(client, user)
    monkeypatch.delenv("ANTHROPIC_API_KEY")  # AI: GENERATOR_CONFIGURATION_REQUIRED, the model is not called
    res = create_portfolio(client, user, [record_id], **llm())
    assert res.status_code == 503 and res.json()["code"] == "GENERATOR_CONFIGURATION_REQUIRED"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-for-tests")
    assert create_portfolio(client, user, [record_id], **llm()).status_code == 201


def test_regenerate_rewrites_the_same_portfolio_body(client, register, claude, fake_dx):
    user = register()
    ok_id, _ = store_badge(client, user, name="정상 배지")
    gone_id, gone_token = store_badge(client, user, name="폐기될 배지")
    created = create_portfolio(client, user, [ok_id, gone_id]).json()["data"]
    assert created["generator"] == "template"
    fake_dx.set_badge_result(gone_token, valid=False, credential_status="REVOKED")
    url = f"/api/v1/portfolios/{created['id']}/regenerate"
    res = client.post(url, json={"record_ids": [ok_id, gone_id], "source": "VC_ONLY", **llm()},
                      headers=user["headers"])
    assert res.status_code == 200, res.text
    p = res.json()["data"]
    assert p["id"] == created["id"] and p["title"] == created["title"] and p["generator"] == "llm"
    assert p["referenced_record_ids"] == [ok_id] and p["excluded"][0]["record_id"] == gone_id
    assert SUMMARY in p["content_markdown"] and "폐기될 배지" not in p["content_markdown"]
    listed = client.get("/api/v1/portfolios", headers=user["headers"]).json()["data"]
    assert len(listed) == 1


def test_regenerate_checks_ownership_and_free_text(client, register, claude):
    alice, bob = register(), register()
    record_id, _ = store_badge(client, alice)
    pid = create_portfolio(client, alice, [record_id]).json()["data"]["id"]
    body = {"record_ids": [record_id], "source": "VC_ONLY"}
    assert client.post(f"/api/v1/portfolios/{pid}/regenerate", json=body, headers=bob["headers"]).status_code == 404
    assert client.post(f"/api/v1/portfolios/{pid}/regenerate", json={**body, "content_markdown": "조작"},
                       headers=alice["headers"]).status_code == 422
    assert client.post(f"/api/v1/portfolios/{pid}/regenerate", json=body,
                       headers=alice["headers"]).json()["data"]["generator"] == "template"


def test_one_llm_portfolio_at_a_time_per_account(claude):
    from fastapi import HTTPException

    from app.services import portfolio_llm

    portfolio_llm.reserve("user-1")
    try:
        with pytest.raises(HTTPException) as exc:
            portfolio_llm.reserve("user-1")
        assert exc.value.status_code == 429 and exc.value.headers["Retry-After"] == "5"
        portfolio_llm.reserve("user-2")  # 다른 계정은 된다
        portfolio_llm.leave("user-2")
    finally:
        portfolio_llm.leave("user-1")
