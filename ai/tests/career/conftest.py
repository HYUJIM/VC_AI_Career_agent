"""Fixtures for the career engine (no DB, no keys, no network).

BE re-verifies credentials itself and sends a VerificationSnapshot; these fixtures build
that snapshot directly, with the same shape main BE produces.
"""

import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.career.canonical import digest_json
from src.career.contracts.verification import (
    CredentialOutcome,
    NormalizedCredential,
    VerificationCheck,
    VerificationSnapshot,
)


@pytest.fixture(autouse=True)
def generator_settings(monkeypatch):
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "false")
    monkeypatch.setenv("CURRICULUM_LLM_PROVIDER", "compatible")
    monkeypatch.setenv("CURRICULUM_LLM_API_KEY", "")
    monkeypatch.setenv("AI_SERVICE_TOKEN", secrets.token_urlsafe(40))


@pytest.fixture
def user():
    return {"id": uuid4(), "subject_did": "did:key:z6MkFixtureSubject" + secrets.token_hex(4)}


def accepted(subject_did, name="SQL assessment", description="SQL exam passed", **achievement):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return CredentialOutcome(
        record_id=uuid4(),
        status="verified",
        accepted=True,
        checks=[VerificationCheck(name="signature", result="pass", code="signature:pass")],
        credential=NormalizedCredential(
            external_id=f"urn:uuid:{uuid4()}",
            issuer_id="https://issuer.example.org/demo",
            issuer_name="Fixture Issuer",
            subject_did=subject_did,
            achievement_name=name,
            achievement={"name": name, "description": description, **achievement},
            issued_at=now - timedelta(days=1),
            valid_from=now - timedelta(days=1),
            expires_at=now + timedelta(days=30),
        ),
    )


@pytest.fixture
def make_snapshot(user):
    def make(outcomes=None, excluded=None, scope="all"):
        now = datetime.now(timezone.utc)
        return VerificationSnapshot(
            verification_snapshot_id=uuid4(),
            subject_did=user["subject_did"],
            scope=scope,
            as_of=now,
            valid_until=now + timedelta(minutes=5),
            accepted_credentials=outcomes if outcomes is not None else [accepted(user["subject_did"])],
            excluded_credentials=excluded or [],
        )

    return make


@pytest.fixture
def mapped_snapshot(make_snapshot):
    return make_snapshot()


@pytest.fixture
def make_posting():
    from src.career.contracts.career import JobPostingInput

    def make(specs=None, **overrides):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        external_id = str(uuid4())
        specs = specs or [
            {"quote": "SQL 평가 통과", "skill_ids": ["sql"], "minimum_evidence": "assessment"},
            {
                "quote": "Python 또는 Scala 과정 수료",
                "skill_ids": ["python", "scala"],
                "match": "any",
                "minimum_evidence": "completion",
            },
            {"quote": "Spark 실무 경험", "skill_ids": ["spark"]},
            {"quote": "Kafka 경험 우대", "skill_ids": ["kafka"], "importance": "preferred"},
        ]
        text, requirements = "", []
        for index, spec in enumerate(specs):
            quote = spec["quote"]
            requirements.append(
                {
                    "key": f"r{index}",
                    "kind": "skill",
                    "importance": "required",
                    **spec,
                    "start": len(text),
                    "end": len(text) + len(quote),
                }
            )
            text += quote + "\n"
        return JobPostingInput.model_validate(
            {
                "source": "fixture",
                "external_id": external_id,
                "source_url": f"https://jobs.example.test/{external_id}",
                "company": f"Fixture Company {external_id}",
                "title": "Data Engineer",
                "region": "서울",
                "seniority": "junior",
                "role_id": "data_engineer",
                "posted_at": now - timedelta(days=3),
                "captured_at": now - timedelta(hours=1),
                "dataset_kind": "real",
                "rights_basis": "research_authorization",
                "review_note": "Synthetic fixture used only in isolated tests, not real market evidence.",
                "raw_text": text,
                "requirements": requirements,
                **overrides,
            }
        )

    return make


@pytest.fixture
def make_version(user, make_posting):
    from src.career.contracts.career import JobVersion

    def make(posting=None, **overrides):
        posting = posting or make_posting()
        return JobVersion(
            posting_id=overrides.pop("posting_id", uuid4()),
            version_id=overrides.pop("version_id", uuid4()),
            imported_at=datetime.now(timezone.utc),
            reviewed_by=user["id"],
            content_hash=digest_json(posting.model_dump(mode="json")),
            posting=posting,
            **overrides,
        )

    return make


@pytest.fixture
def make_resource():
    from src.career.contracts.curriculum import LearningResourceInput
    from src.career.skill_catalog import CATALOG

    labels = {item.skill_id: item.label for item in CATALOG.skills}

    def make(skill="spark", prerequisites=None, cost=0, **overrides):
        identity = str(uuid4())
        text = labels[skill]
        skills = [{"skill_id": skill, "quote": text, "start": 0, "end": len(text)}]
        prerequisites_spans = []
        for value in prerequisites or []:
            text += "\n선수지식: "
            start = len(text)
            text += labels[value]
            prerequisites_spans.append(
                {"skill_id": value, "quote": labels[value], "start": start, "end": len(text)}
            )
        text += "\n"
        start = len(text)
        fee = "무료" if cost == 0 else f"{cost:,}원" if cost is not None else "요금 미확인"
        text += fee
        return LearningResourceInput.model_validate(
            {
                "source": "fixture",
                "external_id": identity,
                "source_url": f"https://learning.example.test/{identity}",
                "title": f"{labels[skill]} 학습자료",
                "provider": "Fixture provider",
                "raw_text": text,
                "skill_annotations": skills,
                "prerequisite_annotations": prerequisites_spans,
                "prerequisites_reviewed": True,
                "level": "beginner",
                "minimum_study_hours": 3,
                "cost_krw": cost,
                "cost_annotation": (
                    {"quote": fee, "start": start, "end": len(text)} if cost is not None else None
                ),
                "captured_at": datetime.now(timezone.utc) - timedelta(hours=1),
                "available": True,
                "dataset_kind": "real",
                "rights_basis": "research_authorization",
                "review_note": "Isolated synthetic fixture, not real learning-market evidence.",
                **overrides,
            }
        )

    return make


@pytest.fixture
def make_resource_version(make_resource, user):
    from src.career.contracts.curriculum import LearningResourceVersion

    def make(resource=None):
        resource = resource or make_resource()
        return LearningResourceVersion(
            resource_id=uuid4(),
            version_id=uuid4(),
            imported_at=datetime.now(timezone.utc),
            reviewed_by=user["id"],
            content_hash=digest_json(resource.model_dump(mode="json")),
            resource=resource,
        )

    return make


@pytest.fixture
def make_analysis(mapped_snapshot, make_posting, make_version):
    from src.career.career_analysis import reference
    from src.career.contracts.career import CareerAnalysis, CareerAnalysisRequest
    from src.career.gap_analyzer import compare_requirements, demand_statistics
    from src.career.skill_normalizer import map_skills

    def make(specs=None):
        version = make_version(make_posting(specs))
        evidence, unmapped = map_skills(mapped_snapshot)
        return CareerAnalysis(
            analysis_id=uuid4(),
            verification_snapshot_id=mapped_snapshot.verification_snapshot_id,
            subject_did=mapped_snapshot.subject_did,
            as_of=datetime.now(timezone.utc),
            valid_until=mapped_snapshot.valid_until,
            request=CareerAnalysisRequest(target_posting_id=version.posting_id),
            lookback_days=90,
            market_posting_count=1,
            duplicate_posting_count=0,
            comparison_scope="target_posting",
            sources=[reference(version, version.posting_id)],
            skill_evidence=evidence,
            comparisons=compare_requirements([version], evidence, False),
            market_demand=demand_statistics([version]),
            unmapped_record_ids=unmapped,
            excluded_record_ids=[],
            warnings=[],
        )

    return make


@pytest.fixture
def llm_settings(monkeypatch):
    monkeypatch.setenv("CURRICULUM_LLM_ENABLED", "true")
    monkeypatch.setenv("CURRICULUM_LLM_PROVIDER", "compatible")
    monkeypatch.setenv("CURRICULUM_LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("CURRICULUM_LLM_MODEL", "fixture-model")
    monkeypatch.setenv("CURRICULUM_LLM_API_KEY", secrets.token_urlsafe(32))
    monkeypatch.setenv("CURRICULUM_LLM_TOKEN_PARAMETER", "max_completion_tokens")
