"""직무 비교·커리큘럼: BE ↔ AI 서비스 연동.

AI 서비스(../ai의 src.server.app)를 같은 프로세스에 띄우고 BE의 실제 HTTP 클라이언트·헤더·형식 검사를 그대로
거친다(소켓만 ASGI로 대체). DXWorks는 conftest의 FakeProvider가 대신한다. 공고·자료는 가상 테스트 값이다.
"""
import asyncio
import json
import logging
import os
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.integrations.ports import ProviderError
from tests.conftest import make_vc_jwt


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
    # 다시 하면 될 수 있는 실패라 기다릴 시간을 알려 준다 (예전에는 Retry-After가 없어 클라이언트가 바로 다시 몰아 보냈다).
    assert res.headers["retry-after"] == "5"
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
    ("LLM_BUSY", True, 503),  # AI의 동시 LLM 생성 상한(CURRICULUM_LLM_MAX_CONCURRENCY)이 10초 동안 찼다
    ("LLM_PROVIDER_REJECTED", True, 503),  # Gemini 429 RESOURCE_EXHAUSTED·5xx
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
    # 다시 시도할 수 있는 실패의 503에는 Retry-After가 붙는다 (FE·클라이언트가 바로 되풀이하지 않게). 그 밖에는 없다.
    assert res.headers.get("retry-after") == ("5" if retryable else None)
    [log] = ai_error_logs(caplog)
    assert f"path=/api/v1/curricula status={status} code={error_code} retryable={retryable}" in log.getMessage()
    count = db.execute("SELECT COUNT(*) AS n FROM curricula WHERE user_id = %s", (student["id"],)).fetchone()
    assert count["n"] == 0


# ── AI로 보내는 묶음 요청의 동시 수 상한 (AI_MAX_CONCURRENCY) ─────────────

class GatedTransport(httpx.AsyncBaseTransport):
    """AI 앱 앞에 두는 테스트용 transport: 묶음 요청(paths)이 동시에 몇 개 가 있는지 세고, hold가 켜져 있으면 붙잡아 둔다
    (요청마다 max_hold초까지만: 상한이 빠져 요청이 그대로 나가도 테스트가 멈추지 않고 실패한다)."""

    def __init__(self, inner, paths: set[str], delay: float = 0.0, max_hold: float = 3.0):
        self.inner, self.paths, self.delay, self.max_hold = inner, paths, delay, max_hold
        self.hold = threading.Event()  # 켜져 있는 동안 묶음 요청은 AI에 닿은 채 응답하지 않는다
        self.lock = threading.Lock()
        self.calls: list[str] = []
        self.in_flight = self.peak = 0

    async def handle_async_request(self, request):
        gated = request.url.path in self.paths
        with self.lock:
            self.calls.append(request.url.path)
            if gated:
                self.in_flight += 1
                self.peak = max(self.peak, self.in_flight)
        try:
            if gated:
                await asyncio.sleep(self.delay)
                waited = 0.0
                while self.hold.is_set() and waited < self.max_hold:
                    await asyncio.sleep(0.01)
                    waited += 0.01
            return await self.inner.handle_async_request(request)
        finally:
            if gated:
                with self.lock:
                    self.in_flight -= 1


def use_ai_limit(monkeypatch, limit: int, wait_seconds: float | None = None) -> None:
    from app.core.config import get_settings
    from app.integrations import ai_service as module

    monkeypatch.setenv("AI_MAX_CONCURRENCY", str(limit))
    if wait_seconds is not None:
        monkeypatch.setattr(module, "AI_SLOT_WAIT_SECONDS", wait_seconds)
    get_settings.cache_clear()


def test_corpus_requests_to_ai_never_exceed_the_concurrency_limit(client, ai_app, researcher, register, monkeypatch):
    from app.integrations import ai_service as module

    import_posting(client, researcher)
    # 학생 여섯 명이 두 개씩 (한 사용자가 동시에 쥘 수 있는 자리는 CORPUS_IN_FLIGHT_PER_USER = 2개다).
    students = [register("학생") for _ in range(6)]
    gate = GatedTransport(module.ai_service.transport, {"/v1/job-postings/search"}, delay=0.05)
    monkeypatch.setattr(module.ai_service, "transport", gate)
    use_ai_limit(monkeypatch, 2)

    with ThreadPoolExecutor(12) as pool:
        statuses = Counter(pool.map(
            lambda index: client.get("/api/v1/job-postings", headers=students[index // 2]["headers"]).status_code,
            range(12)))
    # 기다린 요청도 자리가 나면 그대로 처리된다 (오류 없음). AI에는 한 번에 두 개까지만 가 있었다.
    assert statuses == {200: 12}
    assert gate.calls.count("/v1/job-postings/search") == 12 and gate.peak == 2


def test_ai_busy_after_waiting_is_503_with_code_and_retry_after(client, ai_app, researcher, register, monkeypatch,
                                                                 caplog, db):
    from app.integrations import ai_service as module

    posting = import_posting(client, researcher)
    resource = client.post("/api/v1/research/learning-resources", json=resource_body(), headers=researcher["headers"])
    assert resource.status_code == 201, resource.text
    student = register("학생")
    verify_badge(client, student)
    analysis = client.post("/api/v1/career-analyses", json={"target_posting_id": posting["posting_id"]},
                           headers=student["headers"])
    assert analysis.status_code == 201, analysis.text
    curriculum = {"analysis_id": analysis.json()["data"]["analysis_id"], "idempotency_key": str(uuid.uuid4()),
                  "budget_krw": 0, "learning_choices": [{"skill_id": "spark", "action": "learn"}]}
    gate = GatedTransport(module.ai_service.transport, {"/v1/job-postings/search", "/v1/career-analyses",
                                                        "/v1/learning-resources/search", "/v1/curricula/context"})
    monkeypatch.setattr(module.ai_service, "transport", gate)
    use_ai_limit(monkeypatch, 1, wait_seconds=0.2)
    busy = {"detail": "AI service is busy. Try again later.", "code": "AI_SERVICE_BUSY"}

    gate.hold.set()
    with ThreadPoolExecutor(1) as pool, caplog.at_level(logging.WARNING):
        first = pool.submit(client.get, "/api/v1/job-postings", headers=student["headers"])
        for _ in range(500):  # 첫 요청이 하나뿐인 자리를 잡고 AI에 가 있을 때까지
            if gate.in_flight:
                break
            threading.Event().wait(0.01)
        assert gate.in_flight == 1
        try:
            # 공고 검색·직무 비교·학습자료 검색·커리큘럼 계획은 같은 자리를 쓴다: 기다려도 자리가 없으면 AI를 부르지 않고 503.
            for method, path, body in (("GET", "/api/v1/job-postings", None), ("POST", "/api/v1/career-analyses", {}),
                                       ("GET", "/api/v1/learning-resources", None),
                                       ("POST", "/api/v1/curricula", curriculum)):
                res = client.request(method, path, headers=student["headers"], json=body)
                assert res.status_code == 503 and res.json() == busy and res.headers["retry-after"] == "5", res.text
            # 묶음을 싣지 않는 요청(기술 목록)은 이 상한과 관계없이 바로 처리된다.
            assert client.get("/api/v1/skills", headers=student["headers"]).status_code == 200
        finally:
            gate.hold.clear()
        assert first.result(timeout=10).status_code == 200
    assert sorted(gate.calls) == ["/v1/job-postings/search", "/v1/skills"]  # 503이 된 요청은 AI에 닿지 않았다
    messages = [record.getMessage() for record in ai_error_logs(caplog)]
    assert messages[0] == (
        "AI service error: method=GET path=/api/v1/job-postings status=503 code=AI_SERVICE_BUSY retryable=True "
        "upstream=POST /v1/job-postings/search upstream_status=None cause=None")
    assert len(messages) == 4 and all("code=AI_SERVICE_BUSY" in message for message in messages)
    assert db.execute("SELECT count(*) AS n FROM curricula WHERE user_id = %s", (student["id"],)).fetchone()["n"] == 0
    # 기다리다 끝난 요청이 자리를 가져가지 않는다: 자리가 돌아오면 다음 요청이 바로 처리된다.
    assert client.get("/api/v1/job-postings", headers=student["headers"]).status_code == 200
    assert client.post("/api/v1/curricula", json=curriculum, headers=student["headers"]).status_code == 201


def hold_the_only_ai_slot(client, gate, headers):
    """자리가 하나뿐일 때 공고 검색 하나를 AI에 붙잡아 둔다. 돌려준 (pool, future)는 호출한 쪽이 gate.hold.clear() 뒤에 거둔다."""
    gate.hold.set()
    pool = ThreadPoolExecutor(1)
    first = pool.submit(client.get, "/api/v1/job-postings", headers=headers)
    for _ in range(500):  # 첫 요청이 하나뿐인 자리를 잡고 AI에 가 있을 때까지
        if gate.in_flight:
            break
        threading.Event().wait(0.01)
    assert gate.in_flight == 1
    return pool, first


class ParkedVerifier:
    """배지 검증 호출을 hold가 켜져 있는 동안 붙잡아 두는 테스트용 검증기 (느리거나 멈춘 협력사). 호출마다 max_hold초까지만
    붙잡는다: 회귀가 나도 테스트가 멈추지 않고 실패한다."""

    provider_name = "parked_fake"

    def __init__(self, inner, max_hold: float = 10.0):
        self.inner, self.max_hold = inner, max_hold
        self.hold = threading.Event()
        self.in_flight = 0

    async def verify_badge(self, vc_jwt: str):
        self.in_flight += 1
        try:
            waited = 0.0
            while self.hold.is_set() and waited < self.max_hold:
                await asyncio.sleep(0.01)
                waited += 0.01
            return await self.inner.verify_badge(vc_jwt)
        finally:
            self.in_flight -= 1

    async def verify_presentation(self, vp_token: str):
        return await self.inner.verify_presentation(vp_token)


def test_analyses_waiting_for_the_verifier_do_not_hold_ai_slots(client, ai_app, researcher, register, fake_dx,
                                                               monkeypatch):
    # 회귀: AI 묶음 요청 자리를 재검증 전에 잡아 분석 호출이 끝날 때까지 쥐었다. 협력사(검증기)가 느리거나 멈추면 분석
    # AI_MAX_CONCURRENCY건이 자리를 모두 쥔 채 검증기를 기다려, 그동안 다른 학생의 공고 검색·학습자료 검색이 자리를 기다리다
    # 503(AI_SERVICE_BUSY)이 됐다. 이제 재검증은 자리를 쥐지 않고 하고, 자리는 공고 묶음을 읽고 AI를 부르는 동안만 쥔다.
    from app.integrations import ai_service as module

    posting = import_posting(client, researcher)
    resource = client.post("/api/v1/research/learning-resources", json=resource_body(), headers=researcher["headers"])
    assert resource.status_code == 201, resource.text
    analysts = [register("학생") for _ in range(2)]
    for student in analysts:
        verify_badge(client, student)
    searcher = register("다른 학생")
    gate = GatedTransport(module.ai_service.transport, {"/v1/career-analyses"})
    monkeypatch.setattr(module.ai_service, "transport", gate)
    use_ai_limit(monkeypatch, 2, wait_seconds=0.2)  # 자리 수(AI_MAX_CONCURRENCY) = 검증기에 붙잡아 둘 분석 수
    verifier = ParkedVerifier(fake_dx)
    use_verifier(verifier)
    body = {"target_posting_id": posting["posting_id"]}

    verifier.hold.set()
    with ThreadPoolExecutor(2) as pool:
        parked = [pool.submit(client.post, "/api/v1/career-analyses", json=body, headers=student["headers"])
                  for student in analysts]
        try:
            for _ in range(500):  # 분석 두 건이 모두 검증기에 가 있을 때까지
                if verifier.in_flight == 2:
                    break
                threading.Event().wait(0.01)
            assert verifier.in_flight == 2
            # 분석이 검증기를 기다리는 동안에도 다른 묶음 요청은 자리를 기다리지 않고 바로 처리된다.
            for path in ("/api/v1/job-postings", "/api/v1/learning-resources"):
                res = client.get(path, headers=searcher["headers"])
                assert res.status_code == 200, res.text
            assert verifier.in_flight == 2 and not any(future.done() for future in parked)
            assert gate.calls.count("/v1/career-analyses") == 0  # 붙잡힌 분석은 아직 AI를 부르지 않았다
        finally:
            verifier.hold.clear()
        # 검증기가 답하면 붙잡혀 있던 분석도 그대로 끝난다 (AI에는 한 번에 자리 수만큼만 간다).
        assert [future.result(timeout=10).status_code for future in parked] == [201, 201]
    assert gate.calls.count("/v1/career-analyses") == 2 and gate.peak <= 2


def test_analysis_for_an_unknown_posting_is_404_before_any_verifier_call(client, ai_app, researcher, register, fake_dx,
                                                                         monkeypatch, db):
    # 회귀: 없는 공고를 가리킨 직무 비교도 모든 자격증명을 먼저 재검증한 뒤에 404였다 (자격증명 30개면 요청마다 30번).
    from app.integrations import ai_service as module
    from app.services.verifier_budget import verifier_budget

    import_posting(client, researcher)
    student = register("학생")
    verify_badge(client, student)
    verify_badge(client, student, name="다른 배지")
    verifier = CountingVerifier(fake_dx)
    use_verifier(verifier)
    gate = GatedTransport(module.ai_service.transport, {"/v1/career-analyses"})
    monkeypatch.setattr(module.ai_service, "transport", gate)
    verifier_budget.clear()

    for _ in range(3):
        res = client.post("/api/v1/career-analyses", json={"target_posting_id": str(uuid.uuid4())},
                          headers=student["headers"])
        assert res.status_code == 404 and res.json() == {"detail": "Job posting not found"}
    assert verifier.calls == [] and not verifier_budget._buckets  # 검증기를 부르지 않았고 호출 몫도 쓰지 않았다
    assert gate.calls == []  # AI도 부르지 않았다
    count = db.execute("SELECT COUNT(*) AS n FROM career_analyses WHERE user_id = %s", (student["id"],)).fetchone()
    assert count["n"] == 0


def test_requests_waiting_for_the_ai_slot_do_not_load_the_corpus(client, ai_app, researcher, register, monkeypatch):
    # 회귀: 공고·학습자료 묶음을 DB에서 읽은 뒤에 자리를 기다렸다. 기다리는 요청 수만큼 묶음이 BE 메모리에 올라가 있었다.
    # 이제 자리를 잡은 요청만 묶음을 읽는다.
    from app.integrations import ai_service as module
    from app.services.career_store import career_store

    posting = import_posting(client, researcher)
    resource = client.post("/api/v1/research/learning-resources", json=resource_body(), headers=researcher["headers"])
    assert resource.status_code == 201, resource.text
    student = register("학생")
    verify_badge(client, student)
    analysis = client.post("/api/v1/career-analyses", json={"target_posting_id": posting["posting_id"]},
                           headers=student["headers"])
    assert analysis.status_code == 201, analysis.text
    curriculum = {"analysis_id": analysis.json()["data"]["analysis_id"], "idempotency_key": str(uuid.uuid4()),
                  "budget_krw": 0, "learning_choices": [{"skill_id": "spark", "action": "learn"}]}
    loads = []
    for name in ("market_candidates", "resources"):
        original = getattr(career_store, name)
        monkeypatch.setattr(career_store, name,
                            lambda *args, _name=name, _original=original: (loads.append(_name), _original(*args))[1])
    gate = GatedTransport(module.ai_service.transport, {"/v1/job-postings/search"})
    monkeypatch.setattr(module.ai_service, "transport", gate)
    use_ai_limit(monkeypatch, 1, wait_seconds=0.2)

    pool, first = hold_the_only_ai_slot(client, gate, student["headers"])
    try:
        assert loads == ["market_candidates"]  # 자리를 잡은 첫 요청만 묶음을 읽었다
        for method, path, body in (("GET", "/api/v1/job-postings", None), ("POST", "/api/v1/career-analyses", {}),
                                   ("GET", "/api/v1/learning-resources", None),
                                   ("POST", "/api/v1/curricula", curriculum)):
            res = client.request(method, path, headers=student["headers"], json=body)
            assert res.status_code == 503 and res.json()["code"] == "AI_SERVICE_BUSY", res.text
        assert loads == ["market_candidates"]  # 기다리다 503이 된 요청은 묶음을 읽지 않았다
    finally:
        gate.hold.clear()
        assert first.result(timeout=10).status_code == 200
        pool.shutdown()
    assert client.get("/api/v1/learning-resources", headers=student["headers"]).status_code == 200
    assert client.post("/api/v1/curricula", json=curriculum, headers=student["headers"]).status_code == 201
    assert loads == ["market_candidates", "resources", "resources"]


@pytest.mark.parametrize("failure", ["verifier-outage", "verifier-budget", "ai-error"])
def test_ai_slot_is_released_when_the_analysis_fails(client, ai_app, researcher, register, fake_dx, monkeypatch,
                                                     failure):
    # 재검증이 실패하면 자리를 잡지 않고 끝나고, 자리를 쥔 채 분석이 실패해도 자리는 돌아온다: 자리가 하나뿐이어도 다음
    # 요청이 기다리지 않고 처리된다.
    from app.integrations import ai_service as module
    from app.services import verifier_budget as budget_module

    import_posting(client, researcher)
    student = register("학생")
    verify_badge(client, student)
    use_ai_limit(monkeypatch, 1, wait_seconds=0.2)
    if failure == "verifier-outage":
        use_verifier(OutageVerifier(fake_dx, ProviderError("DXWorks 오류 (500): boom", upstream_status=500)))
        expected = 502
    elif failure == "verifier-budget":
        budget_module.verifier_budget.clear()
        budget_module.verifier_budget.reserve(student["id"], 120)  # 이 사용자의 호출 몫을 다 썼다
        expected = 429
    else:
        original = module.AIServiceClient._send

        async def broken(self, method, path, *args, **kwargs):
            if path == "/v1/career-analyses":
                raise module.contract_violation()
            return await original(self, method, path, *args, **kwargs)

        monkeypatch.setattr(module.AIServiceClient, "_send", broken)
        expected = 502
    for _ in range(3):
        assert client.post("/api/v1/career-analyses", json={}, headers=student["headers"]).status_code == expected
        assert client.get("/api/v1/job-postings", headers=student["headers"]).status_code == 200


async def test_corpus_slot_is_reentrant_and_released_on_every_exit(monkeypatch):
    # 자리를 쥔 요청은 두 번째 자리를 기다리지 않고(겹쳐 써도 하나), 오류·취소로 끝나도 자리를 돌려준다.
    from app.integrations import ai_service as module

    use_ai_limit(monkeypatch, 1, wait_seconds=0.2)
    ai = module.AIServiceClient()

    def free_slots() -> int:
        return module._corpus_slots[asyncio.get_running_loop()][1]._value

    async def nested():
        async with ai.corpus_slot("outer"):
            assert module._holds_corpus_slot.get() is True and free_slots() == 0
            async with ai.corpus_slot("inner"):  # 자리가 하나뿐이어도 기다리지 않는다
                assert free_slots() == 0
            assert module._holds_corpus_slot.get() is True and free_slots() == 0  # 안쪽이 끝나도 바깥 자리는 그대로다

    await asyncio.wait_for(nested(), timeout=2)
    assert module._holds_corpus_slot.get() is False and free_slots() == 1

    with pytest.raises(RuntimeError):
        async with ai.corpus_slot("failing"):
            raise RuntimeError("boom")
    assert module._holds_corpus_slot.get() is False and free_slots() == 1

    entered = asyncio.Event()

    async def hold_forever():
        async with ai.corpus_slot("cancelled"):
            entered.set()
            await asyncio.sleep(3600)

    holder = asyncio.ensure_future(hold_forever())
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert free_slots() == 0
    # 자리가 찬 동안 다른 요청(다른 태스크)은 기다리다 503이 된다. 기다리던 요청이 취소돼도 자리 수는 그대로다.
    with pytest.raises(module.AIUnavailable) as busy:
        async with ai.corpus_slot("POST /v1/job-postings/search"):
            pass
    assert busy.value.code == "AI_SERVICE_BUSY" and busy.value.upstream == "POST /v1/job-postings/search"
    waiter = asyncio.ensure_future(hold_forever())
    await asyncio.sleep(0.01)
    waiter.cancel()
    holder.cancel()
    await asyncio.gather(holder, waiter, return_exceptions=True)
    assert free_slots() == 1
    async with ai.corpus_slot("after"):  # 취소된 요청의 자리가 돌아왔다
        assert free_slots() == 0
    assert free_slots() == 1


def test_closed_event_loops_are_dropped_from_the_corpus_slot_map(monkeypatch):
    # dxworks.py의 동시 호출 상한과 같다: 기다린 요청이 있던 세마포어는 자기 루프를 붙잡고 있어 닫힌 루프의 항목이 쌓였다.
    from app.integrations import ai_service as module

    use_ai_limit(monkeypatch, 1, wait_seconds=1.0)

    async def contend():
        first = await module._take_corpus_slot("t")
        waiter = asyncio.ensure_future(module._take_corpus_slot("t"))
        await asyncio.sleep(0.01)  # 두 번째 요청이 자리를 기다린다
        first.release()
        (await waiter).release()

    for _ in range(5):
        asyncio.run(contend())
    assert len([loop for loop in module._corpus_slots if loop.is_closed()]) <= 1


# ── 사용자별 AI 묶음 요청 한도 (보안 재검토 SEC-02) ────────────────────────

TOO_MANY_ANALYSIS_REQUESTS = {"detail": "Too many analysis requests. Try again later."}


def corpus_denials(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == "vc-be.audit" and r.getMessage().startswith("event=corpus_budget_denied")]


def test_one_account_cannot_take_every_ai_slot(client, ai_app, researcher, register, monkeypatch, caplog):
    """회귀: 묶음 요청 자리(AI_MAX_CONCURRENCY)는 프로세스 전체에 하나뿐이고 사용자별 한도가 없어, 무료 계정 하나가 공고
    목록을 병렬로 몰아 부르면(초당 10번) 다른 사용자의 학습자료 검색이 자리를 기다리다 503 AI_SERVICE_BUSY였다.
    이제 한 사용자는 자리를 동시에 두 개까지만 쥐고, 넘는 요청은 기다리지 않고 묶음도 읽지 않은 채 429 + Retry-After다."""
    from app.integrations import ai_service as module
    from app.services.career_store import career_store

    import_posting(client, researcher)
    resource = client.post("/api/v1/research/learning-resources", json=resource_body(), headers=researcher["headers"])
    assert resource.status_code == 201, resource.text
    attacker, victim = register("학생"), register("다른 학생")
    gate = GatedTransport(module.ai_service.transport, {"/v1/job-postings/search"}, max_hold=10.0)
    monkeypatch.setattr(module.ai_service, "transport", gate)
    use_ai_limit(monkeypatch, 4, wait_seconds=0.5)
    loads = []
    original = career_store.market_candidates
    monkeypatch.setattr(career_store, "market_candidates", lambda *args: (loads.append(1), original(*args))[1])

    gate.hold.set()
    with ThreadPoolExecutor(10) as pool, caplog.at_level(logging.INFO, logger="vc-be.audit"):
        futures = [pool.submit(client.get, "/api/v1/job-postings", headers=attacker["headers"]) for _ in range(10)]
        try:
            for _ in range(500):  # 여덟 건은 바로 거절되고 두 건이 AI에 붙잡혀 있을 때까지
                if sum(future.done() for future in futures) == 8 and gate.in_flight == 2:
                    break
                threading.Event().wait(0.01)
            refused = [future.result() for future in futures if future.done()]
            assert len(refused) == 8 and gate.in_flight == 2
            for res in refused:
                assert res.status_code == 429 and res.headers["retry-after"] == "2", res.text
                assert res.json() == TOO_MANY_ANALYSIS_REQUESTS
            assert len(loads) == 2  # 거절된 요청은 공고 묶음을 읽지 않았다
            # 다른 사용자는 기다리지 않고 바로 처리된다 (공격 계정은 자리 네 개 중 두 개만 쥐었다).
            res = client.get("/api/v1/learning-resources", headers=victim["headers"])
            assert res.status_code == 200, res.text
        finally:
            gate.hold.clear()
        assert sorted(future.result(timeout=10).status_code for future in futures) == [200, 200] + [429] * 8
    assert gate.calls.count("/v1/job-postings/search") == 2
    assert corpus_denials(caplog) == [f"event=corpus_budget_denied user={attacker['id']} "
                                      "route=/v1/job-postings/search reason=in_flight retry_after=2"] * 8
    # 붙잡혀 있던 요청이 끝나면 같은 계정도 다시 받는다 (동시 수는 오류·취소로 끝나도 돌아온다).
    assert client.get("/api/v1/job-postings", headers=attacker["headers"]).status_code == 200
    assert module._corpus_in_flight == {}


def test_corpus_requests_per_account_are_rate_limited(client, ai_app, researcher, register, monkeypatch, caplog):
    """사용자마다 묶음 요청 수를 분·시간 몫(토큰 버킷)으로 묶는다. 몫이 모자라면 DB·AI를 부르지 않고 429 + Retry-After.
    공고 목록·학습자료 검색·직무 비교·학습 계획이 같은 몫을 쓰고, 다른 사용자의 몫과는 따로 센다."""
    from app.integrations import ai_service as module
    from app.services.verifier_budget import VerifierBudget

    import_posting(client, researcher)
    student, other = register("학생"), register("다른 학생")
    clock = {"now": 1000.0}
    monkeypatch.setattr(module, "corpus_request_budget",
                        VerifierBudget(calls_per_minute=3, calls_per_hour=300, process_calls_per_minute=10 ** 6,
                                       clock=lambda: clock["now"]))
    gate = GatedTransport(module.ai_service.transport, set())
    monkeypatch.setattr(module.ai_service, "transport", gate)

    def search(user):
        return client.get("/api/v1/job-postings", headers=user["headers"])

    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        assert [search(student).status_code for _ in range(3)] == [200, 200, 200]
        res = search(student)
        assert res.status_code == 429 and res.headers["retry-after"] == "20" and res.json() == TOO_MANY_ANALYSIS_REQUESTS
        for method, path in (("GET", "/api/v1/learning-resources"), ("POST", "/api/v1/career-analyses")):
            denied = client.request(method, path, headers=student["headers"], json={} if method == "POST" else None)
            assert denied.status_code == 429 and denied.json() == TOO_MANY_ANALYSIS_REQUESTS, denied.text
        assert search(other).status_code == 200  # 다른 사용자의 몫은 그대로다
    assert gate.calls.count("/v1/job-postings/search") == 4 and "/v1/career-analyses" not in gate.calls
    assert corpus_denials(caplog)[0] == (f"event=corpus_budget_denied user={student['id']} "
                                         "route=/v1/job-postings/search reason=rate retry_after=20")
    clock["now"] += 20
    assert search(student).status_code == 200


def test_rate_limited_analysis_makes_no_dxworks_call(client, ai_app, researcher, register, fake_dx, monkeypatch,
                                                     caplog):
    """회귀(v-review VR-02): 사용자별 AI 묶음 요청 한도를 재검증(DXWorks) 뒤에 봐서, 한도를 넘어 429로 끝날 직무 비교도
    자격증명마다 검증기를 먼저 불렀다. 이제 한도는 재검증 전에 보므로 거절된 요청은 검증기 호출이 0번이다."""
    from app.integrations import ai_service as module
    from app.services.verifier_budget import VerifierBudget

    posting = import_posting(client, researcher)
    student = register("학생")
    verify_badge(client, student)
    verify_badge(client, student)
    verifier = CountingVerifier(fake_dx)
    use_verifier(verifier)
    monkeypatch.setattr(module, "corpus_request_budget",
                        VerifierBudget(calls_per_minute=1, calls_per_hour=300, process_calls_per_minute=10 ** 6,
                                       clock=lambda: 1000.0))
    body = {"target_posting_id": posting["posting_id"]}
    first = client.post("/api/v1/career-analyses", json=body, headers=student["headers"])
    assert first.status_code == 201, first.text
    calls = len(verifier.calls)
    assert calls == 2  # 자격증명 두 개를 다시 검증했다
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        denied = client.post("/api/v1/career-analyses", json=body, headers=student["headers"])
    assert denied.status_code == 429 and denied.json() == TOO_MANY_ANALYSIS_REQUESTS
    assert len(verifier.calls) == calls  # 거절된 요청은 검증기를 부르지 않았다
    assert corpus_denials(caplog)[0].startswith(
        f"event=corpus_budget_denied user={student['id']} route=/v1/career-analyses reason=rate retry_after=")
    assert module._corpus_in_flight == {}  # 사용자별 동시 수도 돌려받았다


# ── LLM 모드 학습 계획: 사용자별 24시간 한도 (보안 재검토 SEC-01) ────────────

LLM_LIMIT_REACHED = {"detail": "Daily LLM curriculum limit reached. Use the template mode or try again later."}


@pytest.fixture
def llm_planner(client, ai_app, researcher, register, monkeypatch):
    """허용 목록에 든 학생 한 명과 그 학생의 학습 계획 요청 함수. AI 서비스(같은 프로세스)가 실제 Gemini 클라이언트로 가짜
    Gemini(httpx.MockTransport)를 부른다 (키는 실행마다 만드는 가짜 값, 외부 호출 없음). 가짜는 받은 기준안(baseline)을
    그대로 돌려준다: 생성 방식은 "llm"이다."""
    from app.core.config import get_settings
    from src.career.gemini_client import GeminiClient

    student = register("학생")
    verify_badge(client, student)
    posting = import_posting(client, researcher)
    res = client.post("/api/v1/research/learning-resources", json=resource_body(), headers=researcher["headers"])
    assert res.status_code == 201, res.text
    analysis = client.post("/api/v1/career-analyses", json={"target_posting_id": posting["posting_id"]},
                           headers=student["headers"])
    assert analysis.status_code == 201, analysis.text
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    monkeypatch.delenv("CURRICULUM_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-" + uuid.uuid4().hex)
    monkeypatch.setenv("CURRICULUM_LLM_USER_IDS", student["id"])
    get_settings.cache_clear()
    gemini = {"requests": [], "status": 200, "delay": 0.0}

    async def handler(request):
        gemini["requests"].append(request)
        await asyncio.sleep(gemini["delay"])
        if gemini["status"] != 200:
            return httpx.Response(gemini["status"], json={"error": {"code": gemini["status"], "status": "FAKE"}})
        baseline = json.loads(json.loads(request.content)["input"])["baseline"]
        return httpx.Response(200, json={"id": "fake", "status": "completed", "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": json.dumps(baseline)}]}]})

    ai_app.state.selector_client_factory = lambda config: GeminiClient(config, transport=httpx.MockTransport(handler))

    def plan(mode: str = "llm"):
        body = {"analysis_id": analysis.json()["data"]["analysis_id"], "idempotency_key": str(uuid.uuid4()),
                "budget_krw": 0, "learning_choices": [{"skill_id": "spark", "action": "learn"}]}
        if mode == "llm":
            body.update(mode="llm", allow_external_processing=True)
        return client.post("/api/v1/curricula", json=body, headers=student["headers"])

    return student, gemini, plan


def use_llm_daily_limit(monkeypatch, limit: int) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("CURRICULUM_LLM_DAILY_LIMIT", str(limit))
    get_settings.cache_clear()


def test_llm_curricula_per_account_are_capped_per_day(llm_planner, monkeypatch, caplog):
    """회귀: 허용된 계정 하나(또는 그 토큰을 훔친 쪽)가 LLM 모드 계획을 한도 없이 만들 수 있었다 (동시 60건 → Gemini 호출
    60건). 이제 24시간에 CURRICULUM_LLM_DAILY_LIMIT건이고, 넘으면 Gemini를 부르지 않고 429 + Retry-After다.
    저장된 LLM 계획은 DB로 세므로 프로세스가 다시 시작돼도(메모리의 시도 창이 비어도) 한도는 그대로다."""
    from app.services.verifier_budget import llm_curriculum_attempts

    student, gemini, plan = llm_planner
    use_llm_daily_limit(monkeypatch, 2)
    for _ in range(2):
        res = plan()
        assert res.status_code == 201 and res.json()["data"]["result"]["generation_method"] == "llm", res.text
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        res = plan()
    assert res.status_code == 429 and res.json() == LLM_LIMIT_REACHED
    assert 86000 < int(res.headers["retry-after"]) <= 86400
    assert len(gemini["requests"]) == 2
    assert [r.getMessage() for r in caplog.records if r.name == "vc-be.audit"
            and "daily_limit_denied" in r.getMessage()][0].startswith(
        f"event=daily_limit_denied user={student['id']} route=curriculum_llm retry_after=")
    template = plan("template")  # 템플릿 모드는 이 한도와 관계없다
    assert template.status_code == 201 and template.json()["data"]["result"]["generation_method"] == "template"

    llm_curriculum_attempts.clear()  # 재시작을 흉내 낸다: 메모리의 시도 창은 비지만 DB에 저장된 LLM 계획 두 건이 있다
    res = plan()
    assert res.status_code == 429 and res.json() == LLM_LIMIT_REACHED and len(gemini["requests"]) == 2


def test_concurrent_llm_curricula_cannot_overshoot_the_daily_limit(llm_planner, monkeypatch):
    from app.integrations import ai_service as module

    from app.services import career as service

    _, gemini, plan = llm_planner
    use_llm_daily_limit(monkeypatch, 3)
    monkeypatch.setattr(module, "CORPUS_IN_FLIGHT_PER_USER", 10)  # 이 테스트는 하루 LLM 한도만 본다
    monkeypatch.setattr(service, "LLM_IN_FLIGHT_PER_USER", 10)
    gemini["delay"] = 0.3  # 모두가 DB에 저장되기 전에 한도를 확인한다
    with ThreadPoolExecutor(8) as pool:
        responses = list(pool.map(lambda _: plan(), range(8)))
    assert Counter(res.status_code for res in responses) == {201: 3, 429: 5}
    assert all(res.json() == LLM_LIMIT_REACHED for res in responses if res.status_code == 429)
    assert len(gemini["requests"]) == 3


def test_llm_attempts_that_never_reached_the_model_are_not_counted(llm_planner, monkeypatch):
    """모델을 부르지 않고 끝난 시도(생성기 설정 없음, AI의 동시 생성 자리 없음 LLM_BUSY)는 한도에 세지 않는다.
    모델까지 간 실패(Gemini 400 → 422)는 저장된 계획이 없어도 센다."""
    from app.ai_contract.api import GenerationOutcome
    from app.integrations import ai_service as module

    _, gemini, plan = llm_planner
    use_llm_daily_limit(monkeypatch, 1)
    key = os.environ["GEMINI_API_KEY"]
    monkeypatch.delenv("GEMINI_API_KEY")
    res = plan()
    assert res.status_code == 503 and res.json()["code"] == "GENERATOR_CONFIGURATION_REQUIRED"
    monkeypatch.setenv("GEMINI_API_KEY", key)

    busy = {"on": True}
    original = module.ai_service.generate_curriculum

    async def generate(body):
        if busy["on"]:
            return GenerationOutcome(state="failed", error_code="LLM_BUSY", retryable=True)
        return await original(body)

    monkeypatch.setattr(module.ai_service, "generate_curriculum", generate)
    res = plan()
    assert res.status_code == 503 and res.json()["code"] == "LLM_BUSY" and res.headers["retry-after"] == "5"
    assert gemini["requests"] == []

    busy["on"] = False
    gemini["status"] = 400
    res = plan()
    assert res.status_code == 422 and res.json()["code"] == "LLM_PROVIDER_REJECTED"
    assert len(gemini["requests"]) == 1
    res = plan()  # 위 시도는 Gemini까지 갔다: 저장된 계획은 없지만 한도(1)를 썼다
    assert res.status_code == 429 and res.json() == LLM_LIMIT_REACHED and len(gemini["requests"]) == 1


def test_one_account_generates_one_llm_curriculum_at_a_time(llm_planner, monkeypatch, caplog):
    """회귀(v-review VR-06): AI의 동시 LLM 생성 자리(CURRICULUM_LLM_MAX_CONCURRENCY, 기본 2)를 허용된 계정 하나가 병렬 요청으로
    모두 쥐면, 허용된 다른 계정은 10초 기다린 뒤 503 LLM_BUSY였다. 이제 계정마다 한 번에 한 건이고, 넘는 요청은 Gemini도
    하루 몫도 쓰지 않고 바로 429 + Retry-After다."""
    from app.integrations import ai_service as module

    student, gemini, plan = llm_planner
    use_llm_daily_limit(monkeypatch, 5)
    monkeypatch.setattr(module, "CORPUS_IN_FLIGHT_PER_USER", 10)  # 이 테스트는 LLM 동시 수만 본다
    gemini["delay"] = 1.0  # 첫 요청이 Gemini를 기다리는 동안 나머지가 도착한다
    with caplog.at_level(logging.INFO, logger="vc-be.audit"), ThreadPoolExecutor(4) as pool:
        responses = list(pool.map(lambda _: plan(), range(4)))
    assert Counter(res.status_code for res in responses) == {201: 1, 429: 3}
    for res in responses:
        if res.status_code == 429:
            assert res.json() == {"detail": "An LLM curriculum is already being generated for this account. "
                                            "Try again later."}
            assert res.headers["retry-after"] == "5"
    assert len(gemini["requests"]) == 1
    assert f"event=llm_in_flight_denied user={student['id']}" in [r.getMessage() for r in caplog.records]
    gemini["delay"] = 0.0
    # 거절된 세 건은 하루 몫(5)을 쓰지 않았다: 4건 더 만들 수 있고 그다음이 한도다.
    assert [plan().status_code for _ in range(5)] == [201, 201, 201, 201, 429]


def test_changed_generator_settings_are_503_and_not_counted(llm_planner, monkeypatch):
    """회귀(v-review VR-01): BE가 생성기 정보를 받은 뒤 AI 설정(모델 등)이 바뀌면(배포 중 태스크가 섞인 경우) AI가 이를
    UNTRUSTED_OR_UNSUPPORTED_JOB_INPUT(422)로 답해, 모델을 부르지 않았는데도 하루 LLM 몫을 썼다. 이제 다시 하면 되는
    503 GENERATOR_CONFIGURATION_CHANGED + Retry-After이고 몫을 돌려준다."""
    from app.integrations import ai_service as module

    _, gemini, plan = llm_planner
    use_llm_daily_limit(monkeypatch, 1)
    original = module.ai_service.generator_info

    async def stale_generator_info():
        info = await original()
        return {**info, "model": "gemini-3.1-flash-lite"}  # AI의 지금 설정과 다르다

    monkeypatch.setattr(module.ai_service, "generator_info", stale_generator_info)
    res = plan()
    assert res.status_code == 503 and res.json()["code"] == "GENERATOR_CONFIGURATION_CHANGED", res.text
    assert res.headers["retry-after"] == "5" and gemini["requests"] == []
    monkeypatch.setattr(module.ai_service, "generator_info", original)
    res = plan()  # 몫(1)을 돌려받았으므로 만들 수 있다
    assert res.status_code == 201 and res.json()["data"]["result"]["generation_method"] == "llm", res.text


# ── 직무 비교·학습 계획 저장 수: 사용자별 24시간 한도 (보안 재검토 SEC-06) ─────

def test_analyses_and_curricula_per_account_are_capped_per_day(client, ai_app, researcher, register, fake_dx,
                                                                 monkeypatch, caplog):
    """회귀: 계정 하나가 직무 비교·학습 계획을 끝없이 저장할 수 있었다 (템플릿 계획 40건/1.6초, 자격증명이 없는 계정의
    직무 비교는 검증기 몫에도 묶이지 않았다). 이제 24시간에 각각 MAX_ANALYSES_PER_DAY·MAX_CURRICULA_PER_DAY건이고,
    넘는 직무 비교는 검증기(DXWorks)를 부르기 전에 429 + Retry-After다. 같은 요청 식별자로 다시 받는 계획은 세지 않는다."""
    from app.services import career as service

    posting = import_posting(client, researcher)
    resource = client.post("/api/v1/research/learning-resources", json=resource_body(), headers=researcher["headers"])
    assert resource.status_code == 201, resource.text
    student, other = register("학생"), register("다른 학생")
    verify_badge(client, student)
    verifier = CountingVerifier(fake_dx)
    use_verifier(verifier)
    monkeypatch.setattr(service, "MAX_ANALYSES_PER_DAY", 2)
    monkeypatch.setattr(service, "MAX_CURRICULA_PER_DAY", 1)
    body = {"target_posting_id": posting["posting_id"]}

    analyses = [client.post("/api/v1/career-analyses", json=body, headers=student["headers"]) for _ in range(2)]
    assert [res.status_code for res in analyses] == [201, 201]
    calls = len(verifier.calls)
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        res = client.post("/api/v1/career-analyses", json=body, headers=student["headers"])
    assert res.status_code == 429 and res.json() == {"detail": "Daily career analysis limit reached. Try again later."}
    assert 86000 < int(res.headers["retry-after"]) <= 86400
    assert len(verifier.calls) == calls  # 검증기를 부르지 않았다
    assert client.post("/api/v1/career-analyses", json=body, headers=other["headers"]).status_code == 201

    request = {"analysis_id": analyses[0].json()["data"]["analysis_id"], "idempotency_key": str(uuid.uuid4()),
               "budget_krw": 0, "learning_choices": [{"skill_id": "spark", "action": "learn"}]}
    first = client.post("/api/v1/curricula", json=request, headers=student["headers"])
    assert first.status_code == 201, first.text
    again = client.post("/api/v1/curricula", json=request, headers=student["headers"])
    assert again.status_code == 201 and again.json()["data"]["curriculum_id"] == first.json()["data"]["curriculum_id"]
    res = client.post("/api/v1/curricula", json={**request, "idempotency_key": str(uuid.uuid4())},
                      headers=student["headers"])
    assert res.status_code == 429 and res.json() == {"detail": "Daily curriculum limit reached. Try again later."}
    denied = [r.getMessage() for r in caplog.records if r.name == "vc-be.audit" and "daily_limit_denied" in r.getMessage()]
    assert denied[0].startswith(f"event=daily_limit_denied user={student['id']} route=career_analysis retry_after=")


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


@pytest.mark.parametrize("status", [401, 403, 302, 400, 404])
def test_blanket_rejection_by_the_verifier_fails_analysis_and_saves_nothing(client, ai_app, researcher, register, db,
                                                                            fake_dx, status):
    # 회귀: 협력사가 우리 호출을 통째로 거절하는 상태(키·IP 차단 401/403, 차단 페이지·옮겨진 주소 3xx/404)를 자격증명마다의
    # 거절로 읽어, 근거가 하나도 없는 분석을 201로 만들어 저장했다. 이제 401·403·3xx는 장애이고, 서명 원본이 둘 이상인데
    # 하나도 빠짐없이 검증기 오류면(400·404 포함) 장애로 본다.
    student = register("학생")
    verify_badge(client, student)
    verify_badge(client, student, name="다른 배지")
    import_posting(client, researcher)
    use_verifier(OutageVerifier(fake_dx, ProviderError(f"DXWorks 오류 ({status}): refused", upstream_status=status)))

    res = client.post("/api/v1/career-analyses", json={}, headers=student["headers"])
    assert res.status_code == 502 and res.json() == {"detail": "Credential service error", "code": "PROVIDER_ERROR"}
    count = db.execute("SELECT COUNT(*) AS n FROM career_analyses WHERE user_id = %s", (student["id"],)).fetchone()
    assert count["n"] == 0


class CountingVerifier:
    """가짜 검증기에 들어온 배지 검증 호출을 세는 테스트용 검증기."""

    provider_name = "counting_fake"

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    async def verify_badge(self, vc_jwt: str):
        self.calls.append(vc_jwt)
        return await self.inner.verify_badge(vc_jwt)

    async def verify_presentation(self, vp_token: str):
        return await self.inner.verify_presentation(vp_token)


def test_analysis_without_ai_configuration_is_503_before_any_verifier_call(client, register, fake_dx, monkeypatch,
                                                                           db):
    # 회귀: AI 서비스 설정이 없어 어차피 503으로 끝날 요청도 자격증명 수만큼 검증기(DXWorks)를 먼저 불렀다
    # (요청 10번이면 저장되는 것 없이 10 × N번).
    from app.core.config import get_settings
    from app.services.verifier_budget import verifier_budget

    student = register("학생")
    verify_badge(client, student)
    verify_badge(client, student, name="다른 배지")
    monkeypatch.setenv("AI_SERVICE_TOKEN", "")
    get_settings.cache_clear()
    verifier = CountingVerifier(fake_dx)
    use_verifier(verifier)
    verifier_budget.clear()

    for _ in range(3):
        res = client.post("/api/v1/career-analyses", json={}, headers=student["headers"])
        assert res.status_code == 503
        assert res.json() == {"detail": "AI service is not configured", "code": "AI_SERVICE_NOT_CONFIGURED"}
    assert verifier.calls == [] and not verifier_budget._buckets  # 검증기를 부르지 않았고 호출 몫도 쓰지 않았다
    count = db.execute("SELECT COUNT(*) AS n FROM career_analyses WHERE user_id = %s", (student["id"],)).fetchone()
    assert count["n"] == 0


def test_analysis_beyond_the_verifier_budget_is_429_without_calls(client, ai_app, researcher, register, db, fake_dx,
                                                                 monkeypatch, caplog):
    # 직무 비교는 요청마다 모든 자격증명을 다시 검증한다: 부르기 전에 호출 몫을 한꺼번에 가져가고, 모자라면 검증기도 AI도
    # 부르지 않고 429 + Retry-After로 답한다.
    from app.services import verifier_budget as module

    student = register("학생")
    verify_badge(client, student)
    verify_badge(client, student, name="다른 배지")
    posting = import_posting(client, researcher)
    verifier = CountingVerifier(fake_dx)
    use_verifier(verifier)
    clock = {"now": 1000.0}
    monkeypatch.setattr(module, "verifier_budget", module.VerifierBudget(calls_per_minute=3,
                                                                         clock=lambda: clock["now"]))
    body = {"target_posting_id": posting["posting_id"]}
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        first = client.post("/api/v1/career-analyses", json=body, headers=student["headers"])
        second = client.post("/api/v1/career-analyses", json=body, headers=student["headers"])
    assert first.status_code == 201, first.text
    assert second.status_code == 429 and second.headers["retry-after"] == "20"
    assert second.json() == {"detail": "Too many verification requests. Try again later."}
    assert len(verifier.calls) == 2
    count = db.execute("SELECT COUNT(*) AS n FROM career_analyses WHERE user_id = %s", (student["id"],)).fetchone()
    assert count["n"] == 1
    assert [r.getMessage() for r in caplog.records if r.name == "vc-be.audit"] == [
        f"event=verifier_calls user={student['id']} route=career_analysis calls=2 failed=0",
        f"event=verifier_budget_denied user={student['id']} route=career_analysis retry_after=20",
    ]
    clock["now"] += 20
    assert client.post("/api/v1/career-analyses", json=body, headers=student["headers"]).status_code == 201


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


def test_largest_preview_and_learning_resource_fit_under_the_research_body_limit(client, ai_app, researcher):
    """연구자 반입의 나머지 두 API도 기본 본문 상한(64 KiB)이 아니라 큰 상한(2 MiB)을 쓴다: 가장 긴 원문(공고 미리보기
    10만 자, 학습자료 5만 자)을 한글 \\u 이스케이프 JSON으로 보내도 처리된다."""
    from main import MAX_DEFAULT_BODY_BYTES, MAX_REQUEST_BODY_BYTES

    headers = {**researcher["headers"], "Content-Type": "application/json"}
    filler = "가나다라마바사아자차카타파하"
    payload = json.dumps({"raw_text": ("SQL " + filler * 8000)[:100000]}).encode()
    assert MAX_DEFAULT_BODY_BYTES < len(payload) <= MAX_REQUEST_BODY_BYTES
    preview = client.post("/api/v1/research/job-postings/preview", content=payload, headers=headers)
    assert preview.status_code == 200 and preview.json()["data"]["candidates"], preview.text[:300]

    resource = resource_body()
    resource["raw_text"] = (resource["raw_text"] + "\n" + filler * 4000)[:50000]  # 인용 위치(앞부분)는 그대로다
    payload = json.dumps(resource).encode()
    assert len(resource["raw_text"]) == 50000 and MAX_DEFAULT_BODY_BYTES < len(payload) <= MAX_REQUEST_BODY_BYTES
    res = client.post("/api/v1/research/learning-resources", content=payload, headers=headers)
    assert res.status_code == 201, res.text[:300]
