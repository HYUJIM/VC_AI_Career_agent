"""HTTP boundary of the AI service: auth, contract, redaction and route wiring."""

import os

import pytest
from fastapi.testclient import TestClient

from src.career.contracts import CONTRACT_HEADER, CONTRACT_ID
from src.server.app import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as current:
        yield current


@pytest.fixture
def headers():
    return {
        "Authorization": "Bearer " + os.environ["AI_SERVICE_TOKEN"],
        CONTRACT_HEADER: CONTRACT_ID,
    }


def test_health_is_public_and_announces_contract(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["contract"] == CONTRACT_ID
    assert response.headers[CONTRACT_HEADER] == CONTRACT_ID


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/v1/skills"),
        ("post", "/v1/job-postings/preview"),
        ("post", "/v1/career-analyses"),
        ("post", "/v1/curricula/generate"),
    ],
)
def test_every_v1_route_requires_the_service_token(client, method, path):
    response = getattr(client, method)(path, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert response.json()["code"] == "SERVICE_AUTH_REQUIRED"


def test_unconfigured_token_fails_closed(client, monkeypatch, headers):
    monkeypatch.setenv("AI_SERVICE_TOKEN", "short")
    response = client.get("/v1/skills", headers=headers)
    assert response.status_code == 503


def test_mismatched_client_contract_is_refused(client, headers):
    response = client.get("/v1/skills", headers={**headers, CONTRACT_HEADER: "career-ai/0+old"})
    assert response.status_code == 409 and response.json()["code"] == "CONTRACT_MISMATCH"


def test_validation_errors_never_echo_inputs(client, headers):
    secret = "personal-data-from-a-verified-credential"
    response = client.post(
        "/v1/job-postings/preview", headers=headers, json={"raw_text": secret, "extra": 1}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_SCHEMA_INVALID"
    assert secret not in response.text


def test_oversized_body_is_rejected(client, headers, monkeypatch):
    monkeypatch.setenv("AI_MAX_BODY_BYTES", "64")
    response = client.post("/v1/job-postings/preview", headers=headers, json={"raw_text": "x" * 200})
    assert response.status_code == 413


def test_annotation_rule_failure_is_a_stable_422_code(client, headers, make_posting):
    posting = make_posting([{"quote": "SQL 필수", "skill_ids": ["spark"]}])
    response = client.post(
        "/v1/job-postings/validate", headers=headers, json={"posting": posting.model_dump(mode="json")}
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": "ANNOTATED_SKILL_NOT_MENTIONED_IN_QUOTE",
        "code": "ANNOTATED_SKILL_NOT_MENTIONED_IN_QUOTE",
    }


def test_be_verified_snapshot_analysis_roundtrip(client, headers, mapped_snapshot, make_version):
    target = make_version()
    body = {
        "snapshot": mapped_snapshot.model_dump(mode="json"),
        "request": {"target_posting_id": str(target.posting_id)},
        "candidates": [target.model_dump(mode="json")],
        "target": target.model_dump(mode="json"),
    }
    analysis = client.post("/v1/career-analyses", headers=headers, json=body)
    assert analysis.status_code == 200
    assert analysis.json()["catalog"]["catalog_version"] == "local-data-engineer/1"
    statuses = {
        c["requirement"]["key"]: c["status"] for c in analysis.json()["analysis"]["comparisons"]
    }
    assert statuses["r0"] == "supported"


def test_rejections_keep_the_former_be_messages(client, headers, mapped_snapshot):
    body = {
        "snapshot": mapped_snapshot.model_dump(mode="json"),
        "request": {},
        "candidates": [],
        "target": None,
    }
    response = client.post("/v1/career-analyses", headers=headers, json=body)
    assert response.status_code == 422
    assert response.json()["detail"] == "No eligible reviewed job sources; import sources first"


def test_generator_metadata_requires_explicit_configuration(client, headers, llm_settings):
    response = client.get("/v1/curricula/generator", headers=headers)
    assert response.status_code == 200
    assert response.json()["generator"]["model"] == "fixture-model"
    assert "api_key" not in response.text
