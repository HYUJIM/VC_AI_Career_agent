"""직무 비교·커리큘럼: BE ↔ AI 서비스 연동.

AI 서비스(../ai의 src.server.app)를 같은 프로세스에 띄우고 BE의 실제 HTTP 클라이언트·헤더·형식 검사를 그대로
거친다(소켓만 ASGI로 대체). DXWorks는 conftest의 FakeProvider가 대신한다. 공고·자료는 가상 테스트 값이다.
"""
import json
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

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
    preview = client.post("/api/v1/research/job-postings/preview", json={"raw_text": "Python 또는 Scala 과정"},
                          headers=researcher["headers"])
    assert preview.status_code == 200 and preview.json()["data"]["candidates"][0]["match"] == "any"


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


def test_ai_outage_is_503_and_nothing_is_saved(client, ai_app, researcher, register, db, monkeypatch):
    from app.integrations import ai_service as module

    student = register("학생")
    verify_badge(client, student)
    import_posting(client, researcher)

    def refuse(request):
        raise httpx.ConnectError("AI host down")

    monkeypatch.setattr(module.ai_service, "transport", httpx.MockTransport(refuse))
    res = client.post("/api/v1/career-analyses", json={}, headers=student["headers"])
    assert res.status_code == 503 and res.json()["detail"] == "AI service temporarily unavailable"
    count = db.execute("SELECT COUNT(*) AS n FROM career_analyses WHERE user_id = %s", (student["id"],)).fetchone()
    assert count["n"] == 0


def test_ai_answer_citing_unsupplied_evidence_is_rejected(client, ai_app, researcher, register, monkeypatch):
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


def test_expired_analysis_and_llm_allowlist_block_curriculum(client, ai_app, researcher, register, db):
    student = register("학생")
    verify_badge(client, student)
    import_posting(client, researcher)
    analysis = client.post("/api/v1/career-analyses", json={}, headers=student["headers"]).json()["data"]
    request = {"analysis_id": analysis["analysis_id"], "idempotency_key": str(uuid.uuid4())}
    llm = client.post("/api/v1/curricula", json={**request, "mode": "llm", "allow_external_processing": True},
                      headers=student["headers"])
    assert llm.status_code == 403
    no_consent = client.post("/api/v1/curricula", json={**request, "mode": "llm"}, headers=student["headers"])
    assert no_consent.status_code == 422
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    db.execute("UPDATE career_analyses SET analysis = jsonb_set(analysis, '{valid_until}', %s::jsonb) WHERE id = %s",
               (json.dumps(past), analysis["analysis_id"]))
    expired = client.post("/api/v1/curricula", json=request, headers=student["headers"])
    assert expired.status_code == 409


def test_tampered_job_version_fails_closed(client, ai_app, researcher, db):
    posting = import_posting(client, researcher)
    db.execute("UPDATE job_posting_versions SET payload = jsonb_set(payload, '{posting,title}', '\"tampered\"')"
               " WHERE id = %s", (posting["version_id"],))
    res = client.get(f"/api/v1/job-postings/{posting['posting_id']}", headers=researcher["headers"])
    assert res.status_code == 409
