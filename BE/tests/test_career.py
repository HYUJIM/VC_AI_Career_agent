"""직무 비교·커리큘럼: BE ↔ AI 서비스 연동.

AI 서비스(../ai의 src.server.app)를 같은 프로세스에 띄우고 BE의 실제 HTTP 클라이언트·헤더·형식 검사를 그대로
거친다(소켓만 ASGI로 대체). DXWorks는 conftest의 FakeProvider가 대신한다. 공고·자료는 가상 테스트 값이다.
"""
import json
import logging
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.integrations.ports import ProviderError
from tests.conftest import make_vc_jwt

AI_DIR = Path(__file__).resolve().parents[2] / "ai"


@pytest.fixture
def ai_app(monkeypatch):
    from app.core.config import get_settings
    from app.integrations import ai_service as module

    if str(AI_DIR) not in sys.path:
        sys.path.append(str(AI_DIR))  # 뒤에 붙여 BE 패키지가 우선한다.
    monkeypatch.setenv("CAREER_AI_SKIP_DOTENV", "true")
    from src.server.app import create_app

    app = create_app()
    # 같은 프로세스이므로 BE 클라이언트와 AI 서버가 같은 토큰 변수를 읽는다.
    monkeypatch.setenv("AI_SERVICE_URL", "http://127.0.0.1:8100")
    monkeypatch.setenv("AI_SERVICE_TOKEN", secrets.token_urlsafe(40))
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "false")
    monkeypatch.setattr(module.ai_service, "transport", httpx.ASGITransport(app=app))
    get_settings.cache_clear()
    yield app
    get_settings.cache_clear()


@pytest.fixture
def researcher(register, monkeypatch):
    from app.core.config import get_settings

    user = register("연구자")
    monkeypatch.setenv("RESEARCHER_USER_IDS", user["id"])
    get_settings.cache_clear()
    return user


@pytest.fixture(autouse=True)
def isolated_career_tables(db):
    """세션 DB를 모든 테스트가 함께 쓰므로 테스트 앞뒤로 직무 비교 테이블을 비워 실행 순서와 무관하게 한다."""
    def wipe():
        # 08_career_curriculum.sql의 ON DELETE CASCADE로 curricula와 공고·자료 버전 행도 함께 지워진다.
        for table in ("career_analyses", "job_postings", "learning_resources"):
            db.execute(f"DELETE FROM {table}")

    wipe()
    yield
    wipe()


class OutageVerifier:
    """검증기(DXWorks) 장애·거절을 흉내 내는 테스트용 검증기. conftest의 가짜 검증기를 감싼다.

    tokens가 None이면 모든 서명 원본에, 아니면 그 토큰들에만 error를 던지고 나머지는 원래 가짜에 맡긴다.
    """

    provider_name = "outage_fake"

    def __init__(self, inner, error: Exception, tokens: set[str] | None = None):
        self.inner, self.error, self.tokens = inner, error, tokens

    async def verify_badge(self, vc_jwt: str):
        if self.tokens is None or vc_jwt in self.tokens:
            raise self.error
        return await self.inner.verify_badge(vc_jwt)

    async def verify_presentation(self, vp_token: str):
        return await self.inner.verify_presentation(vp_token)


def use_verifier(verifier) -> None:
    """이 테스트에서만 검증기 의존성을 바꾼다 (client 픽스처가 끝날 때 원래대로 지운다)."""
    from app.integrations import get_verifier
    from main import app

    app.dependency_overrides[get_verifier] = lambda: verifier


def ai_error_logs(caplog) -> list[logging.LogRecord]:
    """main.py의 AI 연동 오류 로그 줄."""
    return [r for r in caplog.records if r.name == "vc-be" and r.getMessage().startswith("AI service error:")]


def posting_body(specs=None, **overrides) -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    specs = specs or [
        {"quote": "SQL 평가 통과", "skill_ids": ["sql"], "minimum_evidence": "assessment"},
        {"quote": "Spark 실무 경험", "skill_ids": ["spark"]},
    ]
    text, requirements = "", []
    for index, spec in enumerate(specs):
        requirements.append({"key": f"r{index}", "kind": "skill", "importance": "required", **spec,
                             "start": len(text), "end": len(text) + len(spec["quote"])})
        text += spec["quote"] + "\n"
    external_id = str(uuid.uuid4())
    return {
        "source": "fixture", "external_id": external_id, "source_url": f"https://jobs.example.test/{external_id}",
        "company": "가상 데이터 회사", "title": "Data Engineer", "region": "서울", "seniority": "junior",
        "role_id": "data_engineer", "posted_at": (now - timedelta(days=3)).isoformat(),
        "captured_at": (now - timedelta(hours=1)).isoformat(), "dataset_kind": "real",
        "rights_basis": "research_authorization", "review_note": "격리된 테스트용 가상 공고 (실제 시장 자료 아님).",
        "raw_text": text, "requirements": requirements, **overrides,
    }


def resource_body(skill_label="Apache Spark", skill_id="spark") -> dict:
    text = f"{skill_label}\n무료"
    return {
        "source": "fixture", "external_id": str(uuid.uuid4()),
        "source_url": "https://learning.example.test/spark", "title": f"{skill_label} 학습자료",
        "provider": "가상 교육기관", "raw_text": text,
        "skill_annotations": [{"skill_id": skill_id, "quote": skill_label, "start": 0, "end": len(skill_label)}],
        "prerequisite_annotations": [], "prerequisites_reviewed": True, "level": "beginner",
        "minimum_study_hours": 3, "cost_krw": 0,
        "cost_annotation": {"quote": "무료", "start": len(skill_label) + 1, "end": len(text)},
        "captured_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), "available": True,
        "dataset_kind": "real", "rights_basis": "research_authorization", "review_note": "격리된 테스트용 가상 자료.",
    }


def verify_badge(client, user, name="SQL 평가 통과") -> str:
    token = make_vc_jwt(user["did"], name=name)
    res = client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"])
    assert res.status_code == 200 and res.json()["success"], res.text
    return token


def import_posting(client, researcher, **kwargs) -> dict:
    res = client.post("/api/v1/research/job-postings", json=posting_body(**kwargs), headers=researcher["headers"])
    assert res.status_code == 201, res.text
    return res.json()["data"]


def test_full_flow_verify_compare_and_plan(client, ai_app, researcher, register):
    student = register("학생")
    verify_badge(client, student)
    posting = import_posting(client, researcher)
    resource = client.post("/api/v1/research/learning-resources", json=resource_body(), headers=researcher["headers"])
    assert resource.status_code == 201, resource.text

    market = client.get("/api/v1/job-postings", headers=student["headers"])
    assert market.status_code == 200 and market.json()["data"]["posting_count"] == 1

    res = client.post("/api/v1/career-analyses", json={"target_posting_id": posting["posting_id"]},
                      headers=student["headers"])
    assert res.status_code == 201, res.text
    analysis = res.json()["data"]
    statuses = {c["requirement"]["key"]: c["status"] for c in analysis["comparisons"]}
    assert statuses == {"r0": "supported", "r1": "not_evidenced"}
    assert analysis["skill_evidence"][0]["evidence_type"] == "assessment"
    assert analysis["subject_did"] == student["did"]

    request = {"analysis_id": analysis["analysis_id"], "idempotency_key": str(uuid.uuid4()), "budget_krw": 0,
               "learning_choices": [{"skill_id": "spark", "action": "learn"}]}
    res = client.post("/api/v1/curricula", json=request, headers=student["headers"])
    assert res.status_code == 201, res.text
    plan = res.json()["data"]
    result = plan["result"]
    assert result["generation_method"] == "template" and result["model"] is None
    assert result["total_hours"] == 3 and result["known_cost_krw"] == 0
    assert result["steps"][0]["resource"]["version_id"] == resource.json()["data"]["version_id"]

    again = client.post("/api/v1/curricula", json=request, headers=student["headers"])
    assert again.status_code == 201 and again.json()["data"]["curriculum_id"] == plan["curriculum_id"]
    conflict = client.post("/api/v1/curricula", json={**request, "weeks": 12}, headers=student["headers"])
    assert conflict.status_code == 409

    saved = client.get(f"/api/v1/career-analyses/{analysis['analysis_id']}", headers=student["headers"])
    assert saved.status_code == 200 and saved.json()["data"]["historical_only"] is True
    other = register("다른 사용자")
    assert client.get(f"/api/v1/career-analyses/{analysis['analysis_id']}",
                      headers=other["headers"]).status_code == 404
    assert client.get(f"/api/v1/curricula/{plan['curriculum_id']}", headers=other["headers"]).status_code == 404
    assert client.get(f"/api/v1/curricula/{plan['curriculum_id']}",
                      headers=student["headers"]).json()["data"]["result"] == result


def test_user_views_hide_reviewer_id_and_internal_source_fields(client, ai_app, researcher, register, db):
    """반입한 연구자 계정 ID·검토 메모·권리 근거·원문 전체는 사용자 조회 응답에 나가지 않는다.

    연구자 반입 응답과 DB 저장본(content_hash 대조 대상)은 지금처럼 버전 전체다. 조회하는 사람이 연구자여도 같다.
    """
    internal = {"rights_basis", "review_note", "raw_text"}

    def public(version: dict, inner: str) -> dict:
        view = {key: value for key, value in version.items() if key != "reviewed_by"}
        view[inner] = {key: value for key, value in version[inner].items() if key not in internal}
        return view

    student = register("학생")
    verify_badge(client, student)
    posting = import_posting(client, researcher)
    res = client.post("/api/v1/research/learning-resources", json=resource_body(), headers=researcher["headers"])
    assert res.status_code == 201, res.text
    resource = res.json()["data"]
    assert posting["reviewed_by"] == resource["reviewed_by"] == researcher["id"]
    assert internal <= posting["posting"].keys() and internal <= resource["resource"].keys()

    path = f"/api/v1/job-postings/{posting['posting_id']}"
    for user in (student, researcher):
        latest = client.get(path, headers=user["headers"])
        pinned = client.get(f"{path}?version_id={posting['version_id']}", headers=user["headers"])
        resources = client.get("/api/v1/learning-resources", headers=user["headers"])
        for res in (latest, pinned, resources):
            assert res.status_code == 200, res.text
            assert researcher["id"] not in res.text
        assert latest.json()["data"] == pinned.json()["data"] == public(posting, "posting")
        assert resources.json()["data"] == [public(resource, "resource")]
    # 빼는 필드를 적는 방식이라 계약에 필드가 늘면 그대로 나간다: 공개 여부를 정하도록 내보내는 키를 고정해 둔다.
    job, [item] = latest.json()["data"], resources.json()["data"]
    assert set(job) == {"posting_id", "version_id", "imported_at", "annotation_method", "content_hash", "posting"}
    assert set(job["posting"]) == {"source", "external_id", "source_url", "company", "title", "role_id", "region",
                                   "seniority", "posted_at", "captured_at", "closes_at", "is_open", "dataset_kind",
                                   "requirements"}
    assert set(item) == {"resource_id", "version_id", "imported_at", "content_hash", "resource"}
    assert set(item["resource"]) == {"source", "external_id", "source_url", "title", "provider", "skill_annotations",
                                     "prerequisite_annotations", "prerequisites_reviewed", "level",
                                     "minimum_study_hours", "cost_krw", "cost_annotation", "captured_at", "available",
                                     "dataset_kind"}

    analysis = client.post("/api/v1/career-analyses", json={"target_posting_id": posting["posting_id"]},
                           headers=student["headers"])
    assert analysis.status_code == 201, analysis.text
    request = {"analysis_id": analysis.json()["data"]["analysis_id"], "idempotency_key": str(uuid.uuid4()),
               "budget_krw": 0, "learning_choices": [{"skill_id": "spark", "action": "learn"}]}
    created = client.post("/api/v1/curricula", json=request, headers=student["headers"])
    replayed = client.post("/api/v1/curricula", json=request, headers=student["headers"])
    assert created.status_code == replayed.status_code == 201, created.text
    curriculum_id = created.json()["data"]["curriculum_id"]
    assert replayed.json()["data"]["curriculum_id"] == curriculum_id
    stored = client.get(f"/api/v1/curricula/{curriculum_id}", headers=student["headers"])
    assert stored.status_code == 200, stored.text
    for res in (created, replayed, stored):
        assert researcher["id"] not in res.text
        # FE(CareerPlanPage)가 쓰는 자료 필드(source_url·title·provider·cost_krw 등)는 그대로 남는다.
        assert [step["resource"] for step in res.json()["data"]["result"]["steps"]] == [public(resource, "resource")]

    # 응답으로 내보낼 때만 뺀다: DB 저장본은 반입 응답과 같은 전체 버전이다.
    row = db.execute("SELECT payload FROM job_posting_versions WHERE id = %s", (posting["version_id"],)).fetchone()
    assert row["payload"] == posting
    row = db.execute("SELECT result FROM curricula WHERE id = %s", (curriculum_id,)).fetchone()
    assert [step["resource"] for step in row["result"]["steps"]] == [resource]


def test_roles_and_authentication(client, ai_app, register):
    student = register("학생")
    assert client.post("/api/v1/career-analyses", json={}).status_code == 401
    assert client.get("/api/v1/skills").status_code == 401
    res = client.post("/api/v1/research/job-postings", json=posting_body(), headers=student["headers"])
    assert res.status_code == 403
    catalog = client.get("/api/v1/skills", headers=student["headers"])
    assert catalog.status_code == 200 and len(catalog.json()["data"]["skills"]) == 12


def test_annotation_rules_come_back_as_422(client, ai_app, researcher):
    res = client.post("/api/v1/research/job-postings",
                      json=posting_body([{"quote": "SQL 필수", "skill_ids": ["spark"]}]),
                      headers=researcher["headers"])
    assert res.status_code == 422 and res.json()["detail"] == "ANNOTATED_SKILL_NOT_MENTIONED_IN_QUOTE"
    assert res.json()["code"] == "ANNOTATED_SKILL_NOT_MENTIONED_IN_QUOTE"  # AI 거절 코드도 그대로 전달
    preview = client.post("/api/v1/research/job-postings/preview", json={"raw_text": "Python 또는 Scala 과정"},
                          headers=researcher["headers"])
    assert preview.status_code == 200 and preview.json()["data"]["candidates"][0]["match"] == "any"


def test_text_that_explodes_under_normalization_is_refused(client, ai_app, researcher):
    """NFKC 정규화로 크게 늘어나는 원문·인용은 AI가 422로 거절하고(BE는 그대로 전달) 아무것도 저장하지 않는다."""
    bomb = "ﷺ" * 1000  # NFKC: 1,000자 → 18,000자
    preview = client.post("/api/v1/research/job-postings/preview", json={"raw_text": bomb},
                          headers=researcher["headers"])
    assert preview.status_code == 422 and preview.json()["code"] == "TEXT_EXPANDS_TOO_MUCH_WHEN_NORMALIZED"
    res = client.post("/api/v1/research/job-postings",
                      json=posting_body([{"quote": "SQL " + "ﷺ" * 100, "skill_ids": ["sql"]}]),
                      headers=researcher["headers"])
    assert res.status_code == 422 and res.json() == {"detail": "TEXT_EXPANDS_TOO_MUCH_WHEN_NORMALIZED",
                                                     "code": "TEXT_EXPANDS_TOO_MUCH_WHEN_NORMALIZED"}
    assert client.get("/api/v1/job-postings", headers=researcher["headers"]).json()["data"]["posting_count"] == 0


def test_revoked_credential_is_excluded_not_treated_as_missing_skill(client, ai_app, researcher, register, fake_dx):
    student = register("학생")
    token = verify_badge(client, student)
    posting = import_posting(client, researcher)
    fake_dx.set_badge_result(token, credential_status="REVOKED")
    res = client.post("/api/v1/career-analyses", json={"target_posting_id": posting["posting_id"]},
                      headers=student["headers"])
    assert res.status_code == 201, res.text
    analysis = res.json()["data"]
    assert len(analysis["excluded_record_ids"]) == 1 and analysis["skill_evidence"] == []
    assert {c["status"] for c in analysis["comparisons"]} == {"unknown"}
    assert "EXCLUDED_CREDENTIALS_LIMIT_PROFILE_COVERAGE" in analysis["warnings"]


def test_ai_outage_is_503_and_nothing_is_saved(client, ai_app, researcher, register, db, monkeypatch, caplog):
    from app.integrations import ai_service as module

    student = register("학생")
    verify_badge(client, student)
    import_posting(client, researcher)

    def refuse(request):
        raise httpx.ConnectError("AI host down")

    monkeypatch.setattr(module.ai_service, "transport", httpx.MockTransport(refuse))
    res = client.post("/api/v1/career-analyses", json={}, headers=student["headers"])
    assert res.status_code == 503
    assert res.json() == {"detail": "AI service temporarily unavailable", "code": "AI_SERVICE_UNAVAILABLE"}
    count = db.execute("SELECT COUNT(*) AS n FROM career_analyses WHERE user_id = %s", (student["id"],)).fetchone()
    assert count["n"] == 0

    # 실패마다 분류 정보만 담은 경고 한 줄. 예외 메시지·토큰은 남기지 않는다.
    [log] = ai_error_logs(caplog)
    assert log.levelno == logging.WARNING
    assert log.getMessage() == (
        "AI service error: method=POST path=/api/v1/career-analyses status=503 code=AI_SERVICE_UNAVAILABLE "
        "retryable=True upstream=POST /v1/career-analyses upstream_status=None cause=ConnectError")
    assert "AI host down" not in caplog.text
    assert os.environ["AI_SERVICE_TOKEN"] not in caplog.text


def test_ai_answer_citing_unsupplied_evidence_is_rejected(client, ai_app, researcher, register, monkeypatch, caplog):
    from app.integrations import ai_service as module

    student = register("학생")
    verify_badge(client, student)
    import_posting(client, researcher)
    original = module.ai_service.analyze

    async def misbehaving(*args):
        output = await original(*args)
        output.analysis.skill_evidence[0].record_id = uuid.uuid4()
        return output

    monkeypatch.setattr(module.ai_service, "analyze", misbehaving)
    res = client.post("/api/v1/career-analyses", json={}, headers=student["headers"])
    assert res.status_code == 502
    assert res.json() == {"detail": "AI service response failed contract checks",
                          "code": "AI_ANALYSIS_INVARIANT_FAILED"}
    [log] = ai_error_logs(caplog)
    assert "status=502 code=AI_ANALYSIS_INVARIANT_FAILED" in log.getMessage()


def test_expired_analysis_and_llm_allowlist_block_curriculum(client, ai_app, researcher, register, db):
    student = register("학생")
    verify_badge(client, student)
    import_posting(client, researcher)
    analysis = client.post("/api/v1/career-analyses", json={}, headers=student["headers"]).json()["data"]
    request = {"analysis_id": analysis["analysis_id"], "idempotency_key": str(uuid.uuid4())}
    llm = client.post("/api/v1/curricula", json={**request, "mode": "llm", "allow_external_processing": True},
                      headers=student["headers"])
    assert llm.status_code == 403
    # BE가 직접 거절한 오류는 AI 연동 오류가 아니므로 code 없이 예전 본문 그대로다.
    assert llm.json() == {"detail": "External LLM access is not enabled for this account"}
    no_consent = client.post("/api/v1/curricula", json={**request, "mode": "llm"}, headers=student["headers"])
    assert no_consent.status_code == 422
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    db.execute("UPDATE career_analyses SET analysis = jsonb_set(analysis, '{valid_until}', %s::jsonb) WHERE id = %s",
               (json.dumps(past), analysis["analysis_id"]))
    expired = client.post("/api/v1/curricula", json=request, headers=student["headers"])
    assert expired.status_code == 409
    assert expired.json() == {"detail": "Career analysis expired; create a new analysis first"}


def test_tampered_job_version_fails_closed(client, ai_app, researcher, db):
    posting = import_posting(client, researcher)
    try:
        db.execute("UPDATE job_posting_versions SET payload = jsonb_set(payload, '{posting,title}', '\"tampered\"')"
                   " WHERE id = %s", (posting["version_id"],))
        res = client.get(f"/api/v1/job-postings/{posting['posting_id']}", headers=researcher["headers"])
        assert res.status_code == 409
    finally:
        # 세션 DB를 함께 쓴다: 변조된 최신 버전이 남으면 이후 모든 공고 조회·직무 비교가 409가 된다.
        db.execute("DELETE FROM job_postings WHERE id = %s", (posting["posting_id"],))  # 버전 행은 CASCADE로 삭제


# ── AI 연동 오류: 응답 code와 로그 ──────────────────────────────────

def test_contract_mismatch_and_invalid_answer_get_distinct_codes(client, ai_app, register, monkeypatch, caplog):
    from app.ai_contract import CONTRACT_HEADER, CONTRACT_ID
    from app.integrations import ai_service as module

    student = register("학생")
    answers = iter([
        httpx.Response(200, json={"skills": []}, headers={CONTRACT_HEADER: CONTRACT_ID + "+stale"}),
        httpx.Response(200, json={"catalog_version": "SENTINEL-AI-VALUE"}, headers={CONTRACT_HEADER: CONTRACT_ID}),
    ])
    monkeypatch.setattr(module.ai_service, "transport", httpx.MockTransport(lambda request: next(answers)))
    stale = client.get("/api/v1/skills", headers=student["headers"])
    invalid = client.get("/api/v1/skills", headers=student["headers"])

    detail = "AI service response failed contract checks"
    assert stale.status_code == invalid.status_code == 502
    assert stale.json() == {"detail": detail, "code": "AI_CONTRACT_MISMATCH"}
    assert invalid.json() == {"detail": detail, "code": "AI_RESPONSE_INVALID"}
    first, second = (record.getMessage() for record in ai_error_logs(caplog))
    assert "code=AI_CONTRACT_MISMATCH" in first and "upstream=GET /v1/skills upstream_status=200 cause=None" in first
    assert "code=AI_RESPONSE_INVALID" in second and "cause=ValidationError" in second
    assert "SENTINEL" not in caplog.text  # 형식 검증 오류 메시지에 든 AI 응답 값은 남기지 않는다


def test_ai_auth_failures_are_told_apart_by_upstream_status(client, ai_app, register, monkeypatch, caplog):
    from app.ai_contract import CONTRACT_HEADER, CONTRACT_ID
    from app.integrations import ai_service as module

    student = register("학생")
    headers = {CONTRACT_HEADER: CONTRACT_ID}
    answers = iter([
        httpx.Response(401, json={"detail": "Service token required", "code": "SERVICE_AUTH_REQUIRED"},
                       headers=headers),
        httpx.Response(503, json={"detail": "AI token missing", "code": "AI_SERVICE_TOKEN_NOT_CONFIGURED"},
                       headers=headers),
    ])
    monkeypatch.setattr(module.ai_service, "transport", httpx.MockTransport(lambda request: next(answers)))
    for _ in range(2):
        res = client.get("/api/v1/skills", headers=student["headers"])
        assert res.status_code == 503
        assert res.json() == {"detail": "AI service authentication failed", "code": "AI_SERVICE_AUTH_FAILED"}
    first, second = (record.getMessage() for record in ai_error_logs(caplog))
    assert "upstream_status=401" in first and "upstream_status=503" in second  # 토큰 불일치 vs AI 쪽 토큰 누락


def test_ai_misconfiguration_is_503_without_logging_the_token(client, ai_app, register, monkeypatch, caplog):
    from app.core.config import get_settings

    student = register("학생")
    monkeypatch.setenv("AI_SERVICE_TOKEN", "short-token-value")
    get_settings.cache_clear()
    res = client.get("/api/v1/skills", headers=student["headers"])
    assert res.status_code == 503
    assert res.json() == {"detail": "AI service is not configured", "code": "AI_SERVICE_NOT_CONFIGURED"}
    [log] = ai_error_logs(caplog)
    assert ("code=AI_SERVICE_NOT_CONFIGURED retryable=False upstream=None upstream_status=None cause=None"
            in log.getMessage())
    assert "short-token-value" not in caplog.text


@pytest.mark.parametrize("error_code,retryable,status", [
    ("LLM_CONTEXT_TOO_LARGE", False, 422),
    ("GENERATOR_CONFIGURATION_REQUIRED", False, 503),
    ("LLM_PROVIDER_UNAVAILABLE", True, 503),
])
def test_curriculum_generation_failure_keeps_status_gains_code_and_saves_nothing(
        client, ai_app, researcher, register, db, monkeypatch, caplog, error_code, retryable, status):
    from app.ai_contract.api import GenerationOutcome
    from app.integrations import ai_service as module

    student = register("학생")
    verify_badge(client, student)
    posting = import_posting(client, researcher)
    resource = client.post("/api/v1/research/learning-resources", json=resource_body(), headers=researcher["headers"])
    assert resource.status_code == 201, resource.text
    analysis = client.post("/api/v1/career-analyses", json={"target_posting_id": posting["posting_id"]},
                           headers=student["headers"])
    assert analysis.status_code == 201, analysis.text

    async def failing(body):
        return GenerationOutcome(state="failed", error_code=error_code, retryable=retryable)

    monkeypatch.setattr(module.ai_service, "generate_curriculum", failing)
    request = {"analysis_id": analysis.json()["data"]["analysis_id"], "idempotency_key": str(uuid.uuid4()),
               "budget_krw": 0, "learning_choices": [{"skill_id": "spark", "action": "learn"}]}
    res = client.post("/api/v1/curricula", json=request, headers=student["headers"])
    assert res.status_code == status and res.json() == {"detail": error_code, "code": error_code}
    [log] = ai_error_logs(caplog)
    assert f"path=/api/v1/curricula status={status} code={error_code} retryable={retryable}" in log.getMessage()
    count = db.execute("SELECT COUNT(*) AS n FROM curricula WHERE user_id = %s", (student["id"],)).fetchone()
    assert count["n"] == 0


# ── 재검증 중 검증기(DXWorks) 장애 ─────────────────────────────────

@pytest.mark.parametrize("error,status", [
    (ProviderError("DXWORKS_API_HOST가 설정되지 않았습니다", unavailable=True), 503),
    (ProviderError("DXWorks 연결 실패: ConnectError"), 502),
    (ProviderError("DXWorks 오류 (INTERNAL): upstream down", upstream_status=500), 502),
    (ProviderError("DXWorks 오류 (429): Too Many Requests", upstream_status=429), 502),
])
def test_verifier_outage_fails_analysis_and_saves_nothing(client, ai_app, researcher, register, db, fake_dx,
                                                          caplog, error, status):
    student = register("학생")
    token = verify_badge(client, student)
    import_posting(client, researcher)
    use_verifier(OutageVerifier(fake_dx, error))  # 저장은 정상 검증기로, 비교할 때만 장애

    res = client.post("/api/v1/career-analyses", json={}, headers=student["headers"])
    # 협력사 오류 문구·설정 이름은 응답에 싣지 않는다 (main.py가 고정 문구와 code로 답하고 원문은 로그에만).
    assert res.status_code == status
    assert res.json() == ({"detail": "Credential service is not configured", "code": "PROVIDER_NOT_CONFIGURED"}
                          if status == 503 else {"detail": "Credential service error", "code": "PROVIDER_ERROR"})
    count = db.execute("SELECT COUNT(*) AS n FROM career_analyses WHERE user_id = %s", (student["id"],)).fetchone()
    assert count["n"] == 0
    record_id = db.execute("SELECT record_id FROM credentials WHERE raw_token = %s", (token,)).fetchone()["record_id"]
    logs = [r.getMessage() for r in caplog.records if r.name == "app.services.reverify"]
    assert any(str(record_id) in message for message in logs)
    assert not any(token in message or student["did"] in message for message in logs)  # 서명 원본·DID는 남기지 않는다


def test_token_rejected_by_verifier_is_excluded_not_blocking(client, ai_app, researcher, register, db, fake_dx):
    student = register("학생")
    verify_badge(client, student)
    rejected = verify_badge(client, student, name="다른 배지")
    posting = import_posting(client, researcher)
    # 특정 서명 원본에 대한 4xx 거절은 장애가 아니다: 그 자격증명만 빼고 지금처럼 분석한다.
    use_verifier(OutageVerifier(fake_dx, ProviderError("DXWorks 오류 (400): invalid", upstream_status=400),
                                tokens={rejected}))

    res = client.post("/api/v1/career-analyses", json={"target_posting_id": posting["posting_id"]},
                      headers=student["headers"])
    assert res.status_code == 201, res.text
    rejected_id = db.execute("SELECT record_id FROM credentials WHERE raw_token = %s", (rejected,)).fetchone()
    assert res.json()["data"]["excluded_record_ids"] == [str(rejected_id["record_id"])]
    count = db.execute("SELECT COUNT(*) AS n FROM career_analyses WHERE user_id = %s", (student["id"],)).fetchone()
    assert count["n"] == 1


async def test_ai_error_handler_tolerates_scope_without_raw_path(caplog):
    # ASGI에서 raw_path는 선택 항목(None 가능)이다. 그래도 상태·detail·code를 그대로 답하고 로그를 남긴다.
    from starlette.requests import Request

    from app.integrations.ai_service import AIUnavailable
    from main import ai_service_error_handler

    scope = {"type": "http", "method": "POST", "path": "/api/v1/career-analyses", "raw_path": None,
             "query_string": b"", "headers": []}
    error = AIUnavailable(503, "AI service temporarily unavailable", "AI_SERVICE_UNAVAILABLE", True)
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = await ai_service_error_handler(Request(scope), error)
    assert res.status_code == 503
    assert json.loads(res.body) == {"detail": "AI service temporarily unavailable", "code": "AI_SERVICE_UNAVAILABLE"}
    assert any("path=/api/v1/career-analyses" in r.getMessage() for r in ai_error_logs(caplog))


def test_largest_allowed_job_posting_fits_under_body_limit(client, ai_app, researcher):
    """계약이 허용하는 가장 큰 공고(원문 10만 자, 4000자 인용 50개)를 한글 \\u 이스케이프 JSON으로 보내도 반입된다.

    BE 요청 본문 상한(main.MAX_REQUEST_BODY_BYTES, 2 MiB)이 정상 반입을 막지 않는지 확인한다.
    """
    from main import MAX_REQUEST_BODY_BYTES

    block = ("SQL " + "가나다라마바사아자차카타파하" * 300)[:4000]  # 4000자, 기술은 SQL만 언급
    requirements = [{"key": f"r{i}", "kind": "skill", "importance": "required", "quote": block,
                     "start": (i % 25) * 4000, "end": (i % 25) * 4000 + 4000, "skill_ids": ["sql"]}
                    for i in range(50)]
    payload = json.dumps(posting_body(raw_text=block * 25, requirements=requirements)).encode()  # ensure_ascii
    assert len(payload) <= MAX_REQUEST_BODY_BYTES
    res = client.post("/api/v1/research/job-postings", content=payload,
                      headers={**researcher["headers"], "Content-Type": "application/json"})
    assert res.status_code == 201, res.text[:300]
