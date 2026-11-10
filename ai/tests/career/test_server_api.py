"""HTTP boundary of the AI service: auth, contract, redaction and route wiring."""

import asyncio
import os

import httpx
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


TOO_LARGE = {"detail": "Request too large for the AI service", "code": "REQUEST_TOO_LARGE"}


def post_asgi(path, headers, content, root_path=""):
    """create_app()에 httpx.ASGITransport로 POST한다.

    TestClient와 달리 헤더 바이트를 그대로 넘기고, async 생성기 본문은 Content-Length 없이
    (Transfer-Encoding: chunked) 조각 하나를 ASGI 메시지 하나로 보낸다.
    """

    async def run():
        transport = httpx.ASGITransport(app=create_app(), root_path=root_path)
        async with httpx.AsyncClient(transport=transport, base_url="http://ai") as current:
            return await current.post(
                path, headers={**headers, "Content-Type": "application/json"}, content=content
            )

    return asyncio.run(run())


def post_chunked(path, headers, chunks, root_path=""):
    """청크 전송으로 보내고, 응답과 앱이 실제로 읽어 간 조각 수를 돌려준다."""
    pulled = 0

    async def body():
        nonlocal pulled
        for chunk in chunks:
            pulled += 1
            yield chunk

    return post_asgi(path, headers, body(), root_path), pulled


def test_chunked_body_is_capped_while_it_streams(headers, monkeypatch):
    monkeypatch.setenv("AI_MAX_BODY_BYTES", "4096")
    response, pulled = post_chunked("/v1/job-postings/preview", headers, [b"x" * 1024] * 2000)
    assert response.status_code == 413
    assert response.json() == TOO_LARGE
    assert response.headers[CONTRACT_HEADER] == CONTRACT_ID
    # 상한(4096)과 같은 4개까지는 받고, 넘는 첫 조각에서 끊는다 (2000개 전부가 아니라)
    assert pulled == 5


# "/ai": uvicorn --root-path /ai처럼 path 앞에 root_path가 붙어 와도(path="/ai/v1/...")
# 라우터와 같은 경로로 판단해 본문보다 토큰을 먼저 검사한다(scope["path"]로 보면 본문을 다 읽는다).
@pytest.mark.parametrize("root_path", ["", "/ai"])
@pytest.mark.parametrize("authorization", [None, "Bearer wrong"])
def test_service_token_is_checked_before_the_body_is_read(authorization, root_path):
    unauthenticated = {CONTRACT_HEADER: CONTRACT_ID}
    if authorization:
        unauthenticated["Authorization"] = authorization
    response, pulled = post_chunked(
        root_path + "/v1/curricula/generate", unauthenticated, [b"{"] * 1000, root_path
    )
    assert response.status_code == 401
    assert response.json()["code"] == "SERVICE_AUTH_REQUIRED"
    assert response.headers[CONTRACT_HEADER] == CONTRACT_ID
    assert pulled == 0


def test_unconfigured_token_is_answered_before_the_body_is_read(headers, monkeypatch):
    monkeypatch.setenv("AI_SERVICE_TOKEN", "short")
    response, pulled = post_chunked("/v1/job-postings/preview", headers, [b"{"] * 1000)
    assert response.status_code == 503
    assert response.json()["code"] == "AI_SERVICE_TOKEN_NOT_CONFIGURED"
    assert pulled == 0


def test_contract_mismatch_is_checked_before_the_token_and_the_body():
    response, pulled = post_chunked(
        "/v1/job-postings/preview", {CONTRACT_HEADER: "career-ai/0+old"}, [b"{"] * 1000
    )
    assert response.status_code == 409 and response.json()["code"] == "CONTRACT_MISMATCH"
    assert pulled == 0


def test_token_is_checked_before_the_declared_size(client, headers, monkeypatch):
    monkeypatch.setenv("AI_MAX_BODY_BYTES", "64")
    body = {"raw_text": "x" * 200}
    response = client.post(
        "/v1/job-postings/preview", headers={CONTRACT_HEADER: CONTRACT_ID}, json=body
    )
    assert response.status_code == 401  # 토큰이 없으면 크기와 관계없이 401
    response = client.post("/v1/job-postings/preview", headers=headers, json=body)
    assert response.status_code == 413 and response.json() == TOO_LARGE


@pytest.mark.parametrize(
    "length",
    [b"abc", b"-1", b"1e3", b"\xb2", b"1" * 5000, b"0" * 4400 + b"19", b"0" * 17 + b"19"],
)
def test_malformed_content_length_is_413_not_500(headers, length):
    # b"\xb2"는 latin-1로 "²": str.isdigit()는 참이지만 int()는 실패한다(전에는 처리되지 않은 500).
    # 4300자리를 넘는 숫자는 앞의 0을 포함해 int()가 ValueError를 낸다(전에는 처리되지 않은 500).
    # 그래서 18자를 넘으면 int() 전에 413: 본문 길이와 같은 19라도 19자로 쓰면 413이다.
    response = post_asgi(
        "/v1/job-postings/preview", {**headers, "Content-Length": length}, b'{"raw_text": "SQL"}'
    )
    assert response.status_code == 413 and response.json() == TOO_LARGE


def test_chunked_body_within_the_limit_is_served(headers):
    response, pulled = post_chunked(
        "/v1/job-postings/preview", headers, [b'{"raw_text": ', b'"SQL"}']
    )
    assert response.status_code == 200 and pulled == 2
    assert response.json()["candidates"][0]["skill_ids"] == ["sql"]
    assert response.headers[CONTRACT_HEADER] == CONTRACT_ID


@pytest.mark.parametrize("path", ["/health", "/openapi.json", "/docs", "/redoc"])
def test_paths_outside_v1_stay_public(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers[CONTRACT_HEADER] == CONTRACT_ID


def test_unknown_v1_paths_also_need_the_token(client, headers):
    assert client.get("/v1/unknown").status_code == 401
    response = client.get("/v1/unknown", headers=headers)
    assert response.status_code == 404
    assert response.headers[CONTRACT_HEADER] == CONTRACT_ID


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


def test_one_of_prerequisite_is_a_stable_422_code(client, headers, make_resource):
    data = make_resource().model_dump(mode="json")
    data["raw_text"] += "\n선수지식: "
    start = len(data["raw_text"])
    quote = "Python, SQL 중 한 가지"
    data["raw_text"] += quote
    data["prerequisite_annotations"] = [
        {"skill_id": skill, "quote": quote, "start": start, "end": start + len(quote)}
        for skill in ("python", "sql")
    ]
    response = client.post(
        "/v1/learning-resources/validate", headers=headers, json={"resource": data}
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": "AMBIGUOUS_RESOURCE_PREREQUISITES",
        "code": "AMBIGUOUS_RESOURCE_PREREQUISITES",
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


def test_normalization_bomb_is_a_stable_422_code(client, headers):
    # U+FDFA 1,000자는 NFKC로 18,000자가 된다.
    response = client.post(
        "/v1/job-postings/preview", headers=headers, json={"raw_text": "\ufdfa" * 1000}
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": "TEXT_EXPANDS_TOO_MUCH_WHEN_NORMALIZED",
        "code": "TEXT_EXPANDS_TOO_MUCH_WHEN_NORMALIZED",
    }
    assert response.headers[CONTRACT_HEADER] == CONTRACT_ID
