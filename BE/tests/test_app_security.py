"""BE 앱 공통 보안 처리 (main.py).

- 요청 본문 상한(경로별): 넘으면 인증·JSON 파싱 전에 413. 오류 미들웨어 안쪽이라 'Unhandled error'로 기록하지 않는다.
  기본 64 KiB, 로그인 없이 부르는 가입·로그인·OAuth 콜백(POST, Google·Kakao·Naver) 16 KiB, 검증(POST /verify) 320 KiB,
  연구자 반입 2 MiB.
  본문을 받는 라우트 목록과 라우트별 가장 큰 정상 본문을 함께 고정한다 (새 라우트는 상한을 정해야 통과한다).
- 요청 검증 실패(422)는 위치·사유·종류만 싣는다 (입력값을 되돌려 주지 않는다. 오류 20개, loc 항목 64자까지).
- 끝에 /가 붙은 경로를 리다이렉트하지 않고(404), API 문서는 APP_ENV=production에서 만들지 않는다.
- 보안 응답 헤더(SecurityHeadersMiddleware): no-store·nosniff·no-referrer·프레임 금지, HSTS는 APP_ENV=production에서만.
- 접근 로그(uvicorn.access)에서 공유 링크 토큰·DID·OAuth 콜백(Google·Kakao·Naver) 쿼리 가리기.
- 발급·검증 인프라 오류(ProviderError)는 고정 문구와 code로만 답하고 협력사 문구는 로그에만 남긴다.
  httpx 요청 줄(비공개 DXWorks 호스트 URL)과 httpcore 디버그 줄(접속 호스트·응답 헤더)은 LOG_LEVEL=DEBUG일 때만 남긴다.
- 저장할 수 없는 문자(NUL·짝 없는 서로게이트): 422 응답은 항상 ASCII, 요청 검사를 지나친 경우의 422 안전망.

정상 흐름(가입·로그인·조회)의 상태 코드와 본문이 그대로인지도 함께 본다. 값은 모두 가상의 값이다.
"""
import asyncio
import base64
import json
import logging
import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import psycopg
import pytest

from app.integrations.ports import ProviderError
from tests.conftest import make_vc_jwt, make_vp_jwt

BE_DIR = Path(__file__).resolve().parents[1]
SECURITY_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'",
}
NUL, SURROGATE = "a\x00b", "a\ud800b"
UNSTORABLE_422 = {"detail": [{"type": "value_error", "loc": [],
                              "msg": "Value error, must not contain NUL or unpaired surrogate characters"}]}


def allowed_origin() -> str:
    """CORS_ORIGINS에 들어 있는 FE 출처 하나 (다른 출처에서 띄운 FE를 흉내 낸다)."""
    from fastapi.middleware.cors import CORSMiddleware

    from main import app

    [cors] = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    return cors.kwargs["allow_origins"][0]


def assert_security_headers(res) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert res.headers.get_list(name) == [value], (res.request.url, name, res.headers.get_list(name))
    assert "strict-transport-security" not in res.headers  # 테스트 기본값은 APP_ENV=development


def vc_be_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "vc-be"]


def post_json(client, url: str, body: dict, headers: dict | None = None):
    """본문을 json.dumps(ASCII 이스케이프)로 직접 만든다 (TestClient의 json=은 짝 없는 서로게이트를 인코딩하지 못한다)."""
    return client.post(url, content=json.dumps(body), headers={"Content-Type": "application/json", **(headers or {})})


def verify_badge(client, user: dict, headers: dict | None = None):
    return client.post("/api/v1/verify", json={"type": "badge", "token": make_vc_jwt(user["did"])},
                       headers={**user["headers"], **(headers or {})})


# ── 요청 본문 상한 ───────────────────────────────────────────────────

def padded_json(body: dict, size: int) -> bytes:
    """JSON 뒤에 공백을 붙여 정확히 size바이트로 만든 본문."""
    data = json.dumps(body).encode()
    assert len(data) <= size
    return data + b" " * (size - len(data))


def test_body_at_limit_is_processed_normally(client, register):
    from main import MAX_DEFAULT_BODY_BYTES, MAX_REQUEST_BODY_BYTES, MAX_VERIFY_BODY_BYTES

    assert MAX_REQUEST_BODY_BYTES == 2 * 1024 * 1024
    user = register()
    headers = {"Content-Type": "application/json", **user["headers"]}
    # 정확히 상한 크기까지는 처리하고(상한은 포함) 1바이트 넘으면 413이다. 상한은 경로마다 다르다 (16 KiB는 아래에서 따로 본다).
    # 검증(POST /verify): 320 KiB
    token = make_vc_jwt(user["did"])
    res = client.post("/api/v1/verify", content=padded_json({"type": "badge", "token": token}, MAX_VERIFY_BODY_BYTES),
                      headers=headers)
    assert res.status_code == 200 and res.json()["success"] is True, res.text
    over = client.post("/api/v1/verify", headers=headers,
                       content=padded_json({"type": "badge", "token": token}, MAX_VERIFY_BODY_BYTES + 1))
    assert over.status_code == 413 and over.text == "Content Too Large"
    # 기본 상한(64 KiB): 공유 링크 생성
    share = {"record_ids": [res.json()["data"]["record_id"]]}
    created = client.post("/api/v1/shares", content=padded_json(share, MAX_DEFAULT_BODY_BYTES), headers=headers)
    assert created.status_code == 201 and created.json()["data"]["token"], created.text
    over = client.post("/api/v1/shares", content=padded_json(share, MAX_DEFAULT_BODY_BYTES + 1), headers=headers)
    assert over.status_code == 413 and over.text == "Content Too Large"
    # 연구자 반입(2 MiB): 본문을 읽은 뒤에 나오는 403(연구자가 아님)이면 본문은 받아들인 것이다 (형식 검사는 그 뒤다).
    preview = "/api/v1/research/job-postings/preview"
    res = client.post(preview, content=padded_json({"raw_text": "SQL"}, MAX_REQUEST_BODY_BYTES), headers=headers)
    assert res.status_code == 403 and res.json() == {"detail": "Researcher permission required"}
    over = client.post(preview, content=padded_json({"raw_text": "SQL"}, MAX_REQUEST_BODY_BYTES + 1), headers=headers)
    assert over.status_code == 413 and over.text == "Content Too Large"


@pytest.mark.parametrize("path", ["/api/v1/users/login", "/api/v1/users/register", "/api/v1/verify",
                                  "/api/mock-issuer/issue", "/api/v1/portfolios", "/api/v1/research/job-postings"])
def test_oversized_body_is_413_before_auth_and_parsing(client, caplog, path):
    from main import MAX_REQUEST_BODY_BYTES

    origin = allowed_origin()
    # 토큰도 없고 JSON도 아닌 본문: 인증을 먼저 봤다면 401, 본문을 파싱했다면 422가 나왔을 것이다.
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = client.post(path, content=b"{" * (MAX_REQUEST_BODY_BYTES + 1),
                          headers={"Content-Type": "application/json", "Origin": origin})
    assert res.status_code == 413 and res.text == "Content Too Large"
    assert res.headers["access-control-allow-origin"] == origin  # 다른 출처 FE도 413을 읽을 수 있다
    assert_security_headers(res)
    assert not vc_be_records(caplog)  # 'Unhandled error'(경보 지표)로 기록하지 않는다


def test_oversized_chunked_body_is_413(client, caplog):
    from main import MAX_REQUEST_BODY_BYTES

    # Content-Length 없이(chunked) 보내도 받은 바이트를 세어 상한에서 끊는다.
    chunks = (b" " * 65536 for _ in range(MAX_REQUEST_BODY_BYTES // 65536 + 1))
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = client.post("/api/v1/users/login", content=chunks, headers={"Content-Type": "application/json"})
    assert res.status_code == 413 and res.json() == {"detail": "Content Too Large"}
    assert_security_headers(res)
    assert not vc_be_records(caplog)


def test_middleware_order_and_body_limit_configuration():
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.body_limit import RequestBodyLimitMiddleware

    from main import (MAX_REQUEST_BODY_BYTES, RouteBodyLimitMiddleware, SecurityHeadersMiddleware,
                      UnhandledErrorMiddleware, app)

    # 바깥 → 안쪽. CORS가 가장 바깥이라 500·413·422에도 CORS 헤더가 붙고, 보안 헤더는 오류 미들웨어가 만든 500에도
    # 붙고, 본문 상한은 오류 미들웨어 안쪽이라 413을 보낼 때 쓰는 내부 예외가 'Unhandled error'로 기록되지 않는다.
    # 경로별 상한은 전체 상한(가장 큰 값) 안쪽에서 그 요청의 상한만 바꾼다.
    assert [m.cls for m in app.user_middleware] == [CORSMiddleware, SecurityHeadersMiddleware, UnhandledErrorMiddleware,
                                                    RequestBodyLimitMiddleware, RouteBodyLimitMiddleware]
    assert app.user_middleware[-2].kwargs == {"max_body_size": MAX_REQUEST_BODY_BYTES}


# ── 경로별 본문 상한: 기본 64 KiB, 연구자 반입 2 MiB, 검증 320 KiB ─────────────────────

def body_routes() -> set[tuple[str, str]]:
    """앱에 등록된 라우트 가운데 요청 본문을 받는 것 (메서드, 경로). FastAPI가 본문 매개변수를 찾은 라우트다."""
    from fastapi import routing

    from main import app

    iterate = getattr(routing, "iter_route_contexts", None)
    return {(method, route.path) for route in (iterate(app.routes) if iterate else app.routes)
            for method in (getattr(route, "methods", None) or ()) if getattr(route, "body_field", None) is not None}


def largest_bodies() -> dict[tuple[str, str], dict]:
    """본문을 받는 라우트마다, 모델의 길이·개수 한도를 모두 채운 가장 큰 정상 본문.

    자유롭게 쓰는 글(이름·제목·원문·인용 등)은 한글로 채운다: json.dumps 기본값(ASCII 이스케이프)으로 만들면 글자마다
    \\uXXXX 6바이트라, 브라우저(UTF-8 3바이트)나 파이썬 클라이언트가 보내는 가장 큰 본문이 된다. 식별자(UUID·토큰·인가 코드·
    DID·템플릿 ID·비교 항목 참조)는 ASCII로만 만들어지는 값이라 ASCII로 채운다. 값의 내용은 가상이다 (크기만 본다).
    """
    def ids(count):
        return [str(uuid.uuid4()) for _ in range(count)]

    skills = ["data_modeling", "sql", "python", "scala", "spark", "kafka", "airflow", "etl", "git", "linux", "aws",
              "docker"]
    block = ("SQL " + "가" * 4000)[:4000]
    requirements = [{"key": f"{index:02d}" + "k" * 78, "kind": "skill", "importance": "preferred", "quote": block,
                     "start": 0, "end": 4000, "skill_ids": skills, "match": "unspecified",
                     "minimum_evidence": "experience"} for index in range(50)]
    annotations = [{"skill_id": skill, "quote": "가" * 2000, "start": 0, "end": 2000} for skill in skills]
    moment = "2026-11-02T00:00:00.000000+00:00"
    review = {"dataset_kind": "synthetic", "rights_basis": "research_authorization", "review_note": "가" * 2000}
    return {
        ("POST", "/api/v1/users/register"): {"name": "가" * 100, "email": "가" * 250 + "@a.kr", "password": "가" * 24},
        ("POST", "/api/v1/users/login"): {"email": "가" * 250 + "@a.kr", "password": "가" * 24},
        **{("POST", f"/api/v1/users/oauth/{provider}/callback"): {
            "code": "c" * 2048, "state": "s" * 2048, "redirect_uri": "r" * 2048, "code_verifier": "v" * 128}
           for provider in ("google", "kakao", "naver")},
        ("POST", "/api/v1/verify"): {"type": "identity", "token": "a" * (256 * 1024)},
        ("POST", "/api/mock-issuer/issue"): {"templateId": "t" * 255, "holderDid": "d" * 255},
        ("POST", "/api/v1/portfolios"): {"title": "가" * 255, "target_role": "가" * 100, "record_ids": ids(50),
                                         "source": "VC_ONLY"},
        ("PATCH", "/api/v1/portfolios/{portfolio_id}"): {"title": "가" * 255, "target_role": "가" * 100,
                                                         "status": "published"},
        ("POST", "/api/v1/shares"): {"record_ids": ids(50), "label": "가" * 100, "expires_in_days": 90},
        ("POST", "/api/v1/career-analyses"): {"target_role_id": "data_engineer", "target_posting_id": ids(1)[0],
                                              "region": "가" * 80, "seniority": "unspecified"},
        # 비교 항목 참조는 '<공고 버전 UUID>:<요구사항 key(영숫자 80자까지)>'이고 분석 하나의 비교 항목은 200개까지다.
        ("POST", "/api/v1/curricula"): {
            "analysis_id": ids(1)[0], "idempotency_key": ids(1)[0], "weeks": 24, "hours_per_week": 40,
            "budget_krw": 100000000, "mode": "template", "allow_external_processing": False,
            "learning_choices": [{"skill_id": skill, "action": "collect_evidence"} for skill in skills],
            "alternative_choices": [{"comparison_ref": f"{ref}:{'k' * 80}", "skill_id": "data_modeling"}
                                    for ref in ids(200)]},
        ("POST", "/api/v1/research/job-postings/preview"): {"raw_text": "가" * 100000},
        ("POST", "/api/v1/research/job-postings"): {
            "source": "s" * 50, "external_id": "가" * 255, "source_url": "https://jobs.example.test/" + "p" * 2000,
            "company": "가" * 200, "title": "가" * 255, "role_id": "data_engineer", "region": "가" * 80,
            "seniority": "unspecified", "posted_at": moment, "captured_at": moment, "closes_at": moment,
            "is_open": False, "raw_text": block * 25, "requirements": requirements, **review},
        ("POST", "/api/v1/research/learning-resources"): {
            "source": "s" * 50, "external_id": "가" * 255, "source_url": "https://learning.example.test/" + "p" * 2000,
            "title": "가" * 255, "provider": "가" * 200, "raw_text": "가" * 50000,
            "skill_annotations": annotations, "prerequisite_annotations": annotations,
            "prerequisites_reviewed": True, "level": "intermediate", "minimum_study_hours": 40, "cost_krw": 100000000,
            "cost_annotation": {"quote": "가" * 2000, "start": 0, "end": 2000}, "captured_at": moment,
            "available": False, **review},
    }


def test_every_route_that_reads_a_body_has_a_cap_that_fits_its_largest_normal_body():
    """본문을 받는 라우트 목록을 고정한다: 새 라우트를 만들면 가장 큰 정상 본문을 largest_bodies에 넣어야 통과하고,
    그 크기가 기본 상한(64 KiB)을 넘으면 main.py의 큰 상한 목록에도 넣어야 한다 (넣지 않으면 정상 요청이 413이 된다)."""
    import main

    assert (main.MAX_PUBLIC_AUTH_BODY_BYTES, main.MAX_DEFAULT_BODY_BYTES, main.MAX_VERIFY_BODY_BYTES,
            main.MAX_REQUEST_BODY_BYTES) == (16 * 1024, 64 * 1024, 320 * 1024, 2 * 1024 * 1024)
    sizes = {route: len(json.dumps(body).encode()) for route, body in largest_bodies().items()}
    assert set(sizes) == body_routes()
    limits = {route: main.body_limit(*route) for route in sizes}
    assert all(sizes[route] <= limits[route] for route in sizes), {r: (sizes[r], limits[r]) for r in sizes}
    # 큰 상한을 쓰는 경로는 정해 둔 목록뿐이고, 모두 실제로 본문을 받는 POST 라우트이며, 기본 상한으로는 모자란 것만이다.
    large = {("POST", path) for path in main.LARGE_BODY_PATHS}
    verify = {("POST", path) for path in main.VERIFY_BODY_PATHS}
    public = {("POST", path) for path in main.PUBLIC_AUTH_BODY_PATHS}
    assert main.LARGE_BODY_PATHS == {"/api/v1/research/job-postings/preview", "/api/v1/research/job-postings",
                                     "/api/v1/research/learning-resources"}
    assert main.VERIFY_BODY_PATHS == {"/api/v1/verify"}
    assert {route for route, limit in limits.items() if limit == main.MAX_REQUEST_BODY_BYTES} == large
    assert {route for route, limit in limits.items() if limit == main.MAX_VERIFY_BODY_BYTES} == verify
    assert {route for route, limit in limits.items() if limit == main.MAX_PUBLIC_AUTH_BODY_BYTES} == public
    assert all(sizes[route] > main.MAX_DEFAULT_BODY_BYTES for route in large | verify)
    assert all(sizes[route] > main.MAX_VERIFY_BODY_BYTES for route in large)  # 검증용 상한으로도 모자란 것만 2 MiB
    # 나머지는 모두 기본 상한이다: 가장 큰 것은 커리큘럼 요청(대안 선택 200개)이다.
    default = set(sizes) - large - verify - public
    assert default == {("POST", "/api/mock-issuer/issue"), ("POST", "/api/v1/portfolios"),
                       ("PATCH", "/api/v1/portfolios/{portfolio_id}"), ("POST", "/api/v1/shares"),
                       ("POST", "/api/v1/career-analyses"), ("POST", "/api/v1/curricula")}
    assert max(sizes[route] for route in default) == sizes[("POST", "/api/v1/curricula")] < 40_000


@pytest.mark.parametrize("method,path,expected", [
    ("POST", "/api/v1/shares", 64 * 1024), ("GET", "/api/v1/shares", 64 * 1024), ("POST", "/no-such-route", 64 * 1024),
    ("POST", "/api/v1/users/logout", 64 * 1024), ("PATCH", "/api/v1/portfolios/7d9c0c2e-0000-4000-8000-000000000000", 65536),
    ("POST", "/api/v1/users/login", 16 * 1024), ("PUT", "/api/v1/users/login", 64 * 1024),
    ("POST", "/api/v1/verify", 320 * 1024), ("PUT", "/api/v1/verify", 64 * 1024),
    ("POST", "/api/v1/research/job-postings", 2 * 1024 * 1024), ("GET", "/api/v1/research/job-postings", 64 * 1024),
    # 목록의 경로와 글자 그대로 같을 때만이다 (라우터도 이런 경로는 그 라우트로 보내지 않는다).
    ("POST", "/api/v1/research/job-postings/", 64 * 1024), ("POST", "/api/v1/research/job-postings/preview/x", 65536),
    ("POST", "/api/v1/research", 64 * 1024), ("POST", "/API/V1/VERIFY", 64 * 1024), ("POST", "//api/v1/verify", 65536),
])
def test_body_limit_is_chosen_by_method_and_exact_route_path(method, path, expected):
    from main import body_limit

    assert body_limit(method, path) == expected


UNKNOWN_KEYS = json.dumps({f"k{index:06d}": 0 for index in range(130_000)}).encode()  # 모르는 키 13만 개, 1.82 MB


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/shares"), ("POST", "/api/v1/portfolios"), ("POST", "/api/v1/career-analyses"),
    ("POST", "/api/v1/curricula"), ("PATCH", "/api/v1/portfolios/7d9c0c2e-0000-4000-8000-000000000000"),
    ("POST", "/api/mock-issuer/issue"),
])
def test_unknown_key_flood_from_a_signed_in_user_is_413_before_validation(client, register, caplog, monkeypatch,
                                                                          method, path):
    """회귀: 로그인한 계정이 모르는 키 13만 개(1.82 MB)를 모르는 키를 금지한 모델(공유 링크·포트폴리오·직무 비교·커리큘럼)에
    보내면 요청마다 이벤트 루프를 0.2초씩 잡고 422를 받았고, 100번이면 프로세스 메모리가 1.2 GiB까지 늘었다 (2 MiB 상한 안).
    이제 기본 상한(64 KiB)에서 본문을 읽기 전에 끊는다: 형식 검사(422 처리기)까지 가지 않는다."""
    import main

    assert len(UNKNOWN_KEYS) == 1_820_000
    validated = []
    real = main.app.exception_handlers[main.RequestValidationError]

    async def spy(request, exc):
        validated.append(request.url.path)
        return await real(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, main.RequestValidationError, spy)
    monkeypatch.setattr(main.app, "middleware_stack", None)  # 처리기 목록은 미들웨어를 조립할 때 복사되므로 다시 조립하게 한다
    user, origin = register(), allowed_origin()
    headers = {"Content-Type": "application/json", "Origin": origin, **user["headers"]}
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        declared = client.request(method, path, content=UNKNOWN_KEYS, headers=headers)
        chunks = (UNKNOWN_KEYS[start:start + 65536] for start in range(0, len(UNKNOWN_KEYS), 65536))  # chunked 전송
        chunked = client.request(method, path, content=chunks, headers=headers)
    assert declared.status_code == 413 and declared.text == "Content Too Large"
    assert chunked.status_code == 413 and chunked.json() == {"detail": "Content Too Large"}
    for res in (declared, chunked):
        assert res.headers["access-control-allow-origin"] == origin
        assert_security_headers(res)
    assert validated == [] and not vc_be_records(caplog)
    # 같은 본문을 기본 상한 안으로 줄이면(4,000개, 56 KB) 예전처럼 형식 검사까지 간다.
    small = json.dumps({f"k{index:06d}": 0 for index in range(4_000)}).encode()
    assert client.request(method, path, content=small, headers=headers).status_code == 422 and len(validated) == 1


@pytest.mark.parametrize("path", ["/api/v1/research/job-postings/preview", "/api/v1/research/job-postings",
                                  "/api/v1/research/learning-resources"])
def test_research_body_from_an_account_that_is_not_a_researcher_is_403_before_validation(client, register, monkeypatch,
                                                                                         path):
    """회귀: 연구자 반입 API 셋(본문 상한 2 MiB)은 본문 형식 검사를 마친 뒤에 연구자인지 확인했다. 연구자가 아닌 로그인 계정이
    모르는 키 13만 개(1.82 MB)를 보내면 요청마다 오류 13만 개를 만든 뒤 422였다 (실제 서버에서 100번이면 프로세스 메모리가
    1.2 GiB까지 늘었다). 이제 연구자 확인이 의존성이라 형식 검사보다 먼저 403으로 끝난다. 연구자는 지금과 같다."""
    import main
    from app.core.config import get_settings

    validated = []
    real = main.app.exception_handlers[main.RequestValidationError]

    async def spy(request, exc):
        validated.append(request.url.path)
        return await real(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, main.RequestValidationError, spy)
    monkeypatch.setattr(main.app, "middleware_stack", None)  # 처리기 목록은 미들웨어를 조립할 때 복사되므로 다시 조립하게 한다
    student = register()
    headers = {"Content-Type": "application/json", **student["headers"]}
    res = client.post(path, content=UNKNOWN_KEYS, headers=headers)
    assert res.status_code == 403 and res.json() == {"detail": "Researcher permission required"}
    assert validated == []
    # 작은 잘못된 본문도 같다: 연구자가 아니면 형식 오류(422)가 아니라 403이다. 토큰이 없으면 지금처럼 401이다.
    assert client.post(path, content=b"{}", headers=headers).status_code == 403 and validated == []
    assert client.post(path, content=b"{}", headers={"Content-Type": "application/json"}).status_code == 401
    # 연구자에게는 지금처럼 형식 검사 결과(422)로 답한다.
    monkeypatch.setenv("RESEARCHER_USER_IDS", student["id"])
    get_settings.cache_clear()
    small = json.dumps({f"k{index:06d}": 0 for index in range(100)}).encode()
    res = client.post(path, content=small, headers=headers)
    assert res.status_code == 422 and validated == [path]


TRAILING_NEWLINE_CASES = [
    ("POST", "/api/v1/users/login%0A", {"email": "a@test.com", "password": "password1234"}),
    ("POST", "/api/v1/users/register%0A", {"name": "가", "email": "a@test.com", "password": "password1234"}),
    ("POST", "/api/v1/users/oauth/google/callback%0A", {"code": "c", "state": "s"}),
    ("POST", "/api/v1/users/oauth/kakao/callback%0A", {"code": "c", "state": "s"}),
    ("POST", "/api/v1/users/oauth/naver/callback%0A", {"code": "c", "state": "s"}),
    ("POST", "/api/v1/verify%0A", {"type": "badge", "token": "a.b.c"}),
    ("GET", "/health%0A", None),
]


@pytest.mark.parametrize("method,path,body", TRAILING_NEWLINE_CASES)
def test_path_ending_in_an_encoded_newline_is_not_routed(client, caplog, monkeypatch, method, path, body):
    """회귀: 라우터의 경로 식은 `$`로 끝나 끝의 줄바꿈 하나를 무시한다. '/api/v1/users/login%0A'가 로그인 라우트에 닿으면서
    경로별 본문 상한 목록(글자 그대로 비교)과는 맞지 않아, 로그인 없이 부르는 인증 API 셋이 16 KiB가 아니라 기본 상한(64 KiB)
    까지 본문을 읽고 검사했다. 이제 줄바꿈으로 끝나는 경로는 라우터에 넘기지 않고 404로 답한다 (형식 검사까지 가지 않는다)."""
    import main

    validated = []
    real = main.app.exception_handlers[main.RequestValidationError]

    async def spy(request, exc):
        validated.append(request.url.path)
        return await real(request, exc)

    monkeypatch.setitem(main.app.exception_handlers, main.RequestValidationError, spy)
    monkeypatch.setattr(main.app, "middleware_stack", None)  # 처리기 목록은 미들웨어를 조립할 때 복사되므로 다시 조립하게 한다
    origin = allowed_origin()
    headers = {"Content-Type": "application/json", "Origin": origin}
    over_the_auth_cap = json.dumps({f"k{index:06d}": 0 for index in range(1_100)}).encode().ljust(
        main.MAX_PUBLIC_AUTH_BODY_BYTES + 1)  # 16 KiB + 1: 인증 API의 상한은 넘고 기본 상한 안인 모르는 키 본문
    assert len(over_the_auth_cap) == 16 * 1024 + 1
    if body is not None and "email" in body:
        body = {**body, "email": f"nl-{secrets.token_hex(5)}@test.com"}
    bodies = [None] if body is None else [json.dumps(body).encode(), over_the_auth_cap]
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        for content in bodies:
            res = client.request(method, path, content=content, headers=headers)
            assert res.status_code == 404 and res.json() == {"detail": "Not Found"}, (path, res.text)
            assert res.headers["access-control-allow-origin"] == origin
            assert_security_headers(res)
    assert validated == [] and not vc_be_records(caplog)  # 라우트에 닿지 않았다: 형식 검사도, 로그 줄도 없다
    # 정확한 경로는 지금과 같다: 라우트에 닿는다 (작은 본문은 그 라우트의 답, 16 KiB + 1은 인증 API에서 413).
    exact = path.removesuffix("%0A")
    res = client.request(method, exact, content=bodies[0], headers=headers)
    assert res.status_code != 404, (exact, res.text)
    if exact in main.PUBLIC_AUTH_BODY_PATHS:
        res = client.request(method, exact, content=over_the_auth_cap, headers=headers)
        assert res.status_code == 413 and res.text == "Content Too Large"
    # 줄바꿈 둘로 끝나는 경로도 같고, 줄바꿈이 끝이 아닌 곳에 있거나 다른 제어 문자로 끝나는 경로는 지금처럼 라우터가 404로 답한다.
    for other in (exact + "%0A%0A", exact + "%0D", exact + "%0Ax", exact + "%09"):
        res = client.request(method, other, content=bodies[0], headers=headers)
        assert res.status_code == 404 and res.json() == {"detail": "Not Found"}, other


def test_body_limits_apply_when_the_app_is_served_under_a_root_path():
    """회귀: 경로를 scope["path"] 그대로 목록과 비교해, uvicorn을 --root-path로 띄우면(경로 앞에 root_path가 붙는다) 작은
    상한이 조용히 빠졌다. 라우터와 같은 규칙으로 root_path를 떼고 본다."""
    from fastapi.testclient import TestClient

    from main import (MAX_DEFAULT_BODY_BYTES, MAX_PUBLIC_AUTH_BODY_BYTES, MAX_REQUEST_BODY_BYTES,
                      MAX_VERIFY_BODY_BYTES, app)

    def post(prefixed, path, size):
        # uvicorn --root-path /be가 만드는 scope: root_path="/be", path="/be" + 요청 경로
        client = TestClient(app, root_path="/be", raise_server_exceptions=False)
        return client.post(("/be" if prefixed else "") + path, content=b"{" * size,
                           headers={"Content-Type": "application/json"})

    cases = [("/api/v1/users/login", MAX_PUBLIC_AUTH_BODY_BYTES), ("/api/v1/users/register", MAX_PUBLIC_AUTH_BODY_BYTES),
             ("/api/v1/users/oauth/google/callback", MAX_PUBLIC_AUTH_BODY_BYTES),
             ("/api/v1/users/oauth/kakao/callback", MAX_PUBLIC_AUTH_BODY_BYTES),
             ("/api/v1/users/oauth/naver/callback", MAX_PUBLIC_AUTH_BODY_BYTES),
             ("/api/v1/shares", MAX_DEFAULT_BODY_BYTES), ("/api/v1/verify", MAX_VERIFY_BODY_BYTES),
             ("/api/v1/research/job-postings", MAX_REQUEST_BODY_BYTES)]
    for prefixed in (True, False):  # 경로에 root_path가 붙지 않은 scope(다른 서버·프록시 설정)도 같다
        for path, limit in cases:
            over, at = post(prefixed, path, limit + 1), post(prefixed, path, limit)
            assert over.status_code == 413 and over.text == "Content Too Large", (prefixed, path)
            assert at.status_code == 422, (prefixed, path, at.status_code)  # 상한 안이면 읽고 파싱한다 (JSON이 아니라 422)


def test_largest_normal_requests_of_the_default_class_are_processed(client, register, monkeypatch):
    """기본 상한(64 KiB)·검증 상한(320 KiB)이 정상 요청을 막지 않는다: 한도를 모두 채운 가장 큰 요청을 한글 \\u 이스케이프
    JSON으로 보내 실제로 처리되는지 본다 (가입·로그인, 가장 긴 서명 원본의 검증, 공유 링크, 포트폴리오 생성·수정, 모의 발급).
    직무 비교·커리큘럼은 AI 서비스 없이 형식 검사를 지나 서비스 단계의 답까지 가는지만 본다 (흐름 전체는 test_career)."""
    from app.api.verify import MAX_TOKEN_CHARS
    from app.core.config import get_settings

    def send(method, path, body, headers=None):
        return client.request(method, path, content=json.dumps(body),  # ensure_ascii: 한글은 \uXXXX
                              headers={"Content-Type": "application/json", **(headers or {})})

    bodies = largest_bodies()
    # 가입·로그인: 이름 100자, 이메일 255자, 비밀번호 72바이트
    account = {**bodies[("POST", "/api/v1/users/register")], "email": f"{'가' * 239}-{secrets.token_hex(5)}@a.kr"}
    assert len(account["email"]) == 255 and len(account["password"].encode()) == 72
    signup = send("POST", "/api/v1/users/register", account)
    assert signup.status_code == 200, signup.text
    login = send("POST", "/api/v1/users/login", {"email": account["email"], "password": account["password"]})
    assert login.status_code == 200 and login.json()["data"]["name"] == "가" * 100
    did, auth = login.json()["data"]["did"], {"Authorization": f"Bearer {login.json()['access_token']}"}

    # 검증: 길이 한도(MAX_TOKEN_CHARS)를 꽉 채운 서명 원본 하나와 보통 배지 49개 → 자격증명 50개
    token = make_vc_jwt(did)
    token += "A" * (MAX_TOKEN_CHARS - len(token))  # 서명 부분을 늘린다 (가짜 검증기는 서명을 보지 않는다)
    record_ids = []
    for vc_jwt in [token, *(make_vc_jwt(did) for _ in range(49))]:
        verified = send("POST", "/api/v1/verify", {"type": "badge", "token": vc_jwt}, auth)
        assert verified.status_code == 200 and verified.json()["success"] is True, verified.text[:300]
        record_ids.append(verified.json()["data"]["record_id"])

    share = send("POST", "/api/v1/shares", {**bodies[("POST", "/api/v1/shares")], "record_ids": record_ids}, auth)
    assert share.status_code == 201 and len(share.json()["data"]["record_ids"]) == 50, share.text[:300]
    assert share.json()["data"]["label"] == "가" * 100
    portfolio = send("POST", "/api/v1/portfolios",
                     {**bodies[("POST", "/api/v1/portfolios")], "record_ids": record_ids}, auth)
    assert portfolio.status_code == 201 and portfolio.json()["data"]["credential_count"] == 50, portfolio.text[:300]
    patched = send("PATCH", f"/api/v1/portfolios/{portfolio.json()['data']['id']}",
                   {"title": "나" * 255, "target_role": "다" * 100, "status": "published"}, auth)
    assert patched.status_code == 200 and patched.json()["data"]["title"] == "나" * 255, patched.text[:300]

    # 모의 발급: 허용 목록에 든 255자 템플릿 ID
    monkeypatch.setenv("MOCK_ISSUER_TEMPLATE_IDS", "t" * 255)
    get_settings.cache_clear()
    issued = send("POST", "/api/mock-issuer/issue", {"templateId": "t" * 255, "holderDid": did}, auth)
    assert issued.status_code == 200 and issued.json()["vcJwt"], issued.text[:300]

    # 직무 비교·커리큘럼: 형식 검사를 지나 서비스 단계에서 답한다 (이 테스트에는 AI 서비스 설정·분석 결과가 없다)
    analysis = send("POST", "/api/v1/career-analyses", bodies[("POST", "/api/v1/career-analyses")], auth)
    assert (analysis.status_code, analysis.json()["code"]) == (503, "AI_SERVICE_NOT_CONFIGURED"), analysis.text
    curriculum = send("POST", "/api/v1/curricula", bodies[("POST", "/api/v1/curricula")], auth)
    assert curriculum.status_code == 404 and curriculum.json() == {"detail": "Analysis not found"}, curriculum.text


# ── 로그인 없이 부르는 가입·로그인·OAuth 콜백(Google·Kakao·Naver)의 본문 상한 (16 KiB) ──────────────

PUBLIC_AUTH_PATHS = ["/api/v1/users/login", "/api/v1/users/register", "/api/v1/users/oauth/google/callback",
                     "/api/v1/users/oauth/kakao/callback", "/api/v1/users/oauth/naver/callback"]


def test_public_auth_body_limit_covers_exactly_the_anonymous_post_routes():
    from fastapi import routing

    from main import MAX_PUBLIC_AUTH_BODY_BYTES, PUBLIC_AUTH_BODY_PATHS, app
    from tests.test_auth_security import PUBLIC_ROUTES

    assert MAX_PUBLIC_AUTH_BODY_BYTES == 16 * 1024
    assert PUBLIC_AUTH_BODY_PATHS == frozenset(PUBLIC_AUTH_PATHS)
    # 로그인 없이 부르는 POST 라우트는 모두 이 작은 상한을 쓴다: 공개 라우트 목록(test_auth_security의 라우트 목록 시험이 새
    # 공개 라우트를 여기에 넣게 한다)에 POST를 더하면 이 목록에도 넣어야 통과한다.
    assert {path for method, path in PUBLIC_ROUTES if method == "POST"} == PUBLIC_AUTH_BODY_PATHS
    # 경로 이름이 바뀌면(라우터 접두어 등) 상한이 조용히 빠지지 않게, 실제 POST 경로인지 확인한다.
    iterate = getattr(routing, "iter_route_contexts", None)
    posts = {route.path for route in (iterate(app.routes) if iterate else app.routes)
             if "POST" in (getattr(route, "methods", None) or ())}
    assert PUBLIC_AUTH_BODY_PATHS <= posts


@pytest.mark.parametrize("path", PUBLIC_AUTH_PATHS)
def test_public_auth_body_over_16_kib_is_413_before_parsing(client, caplog, path):
    """회귀: 토큰 없이 1.7 MiB짜리 JSON(모르는 키 13만 개)을 이 API들에 보내면 요청마다 이벤트 루프를 0.25~0.35초 잡고
    3~4.5 MiB 응답을 만들었다 (동시에 20개면 /health가 초 단위로 밀렸다). 이제 16 KiB를 넘으면 읽기 전에 끊는다."""
    from main import MAX_PUBLIC_AUTH_BODY_BYTES

    origin = allowed_origin()
    over = b"{" * (MAX_PUBLIC_AUTH_BODY_BYTES + 1)  # JSON도 아니다: 파싱했다면 422가 나왔을 것이다
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        declared = client.post(path, content=over, headers={"Content-Type": "application/json", "Origin": origin})
        chunks = (b" " * 4096 for _ in range(MAX_PUBLIC_AUTH_BODY_BYTES // 4096 + 1))  # Content-Length 없이(chunked)
        chunked = client.post(path, content=chunks, headers={"Content-Type": "application/json", "Origin": origin})
    # 전체 상한(2 MiB)과 같은 413이다: Content-Length가 크면 텍스트, chunked면 JSON.
    assert declared.status_code == 413 and declared.text == "Content Too Large"
    assert chunked.status_code == 413 and chunked.json() == {"detail": "Content Too Large"}
    for res in (declared, chunked):
        assert res.headers["access-control-allow-origin"] == origin
        assert_security_headers(res)
    assert not vc_be_records(caplog)  # 'Unhandled error'(경보 지표)로 기록하지 않는다


def test_public_auth_body_at_16_kib_is_processed_normally(client, register):
    from main import MAX_PUBLIC_AUTH_BODY_BYTES

    user = register()
    login = client.post("/api/v1/users/login", headers={"Content-Type": "application/json"}, content=padded_json(
        {"email": user["email"], "password": "password1234"}, MAX_PUBLIC_AUTH_BODY_BYTES))
    assert login.status_code == 200 and login.json()["access_token"]
    signup = client.post("/api/v1/users/register", headers={"Content-Type": "application/json"}, content=padded_json(
        {"name": "경계", "email": f"t-{secrets.token_hex(5)}@test.com", "password": "password1234"},
        MAX_PUBLIC_AUTH_BODY_BYTES))
    assert signup.status_code == 200 and signup.json()["access_token"]
    # 가장 긴 정상 콜백 본문(code·state·redirect_uri 각 2,048자 + code_verifier 128자)도 상한 안이다: 본문 검사까지 간다.
    for provider in ("google", "kakao", "naver"):
        callback = client.post(f"/api/v1/users/oauth/{provider}/callback", headers={"Content-Type": "application/json"},
                               content=padded_json({"code": "c" * 2048, "state": "s" * 2048, "redirect_uri": "r" * 2048,
                                                    "code_verifier": "v" * 128}, MAX_PUBLIC_AUTH_BODY_BYTES))
        assert callback.status_code == 400 and callback.json() == {"detail": "Invalid or expired OAuth state"}


def test_small_body_limit_does_not_apply_to_other_routes_or_methods(client, register):
    from main import MAX_PUBLIC_AUTH_BODY_BYTES

    user = register()
    body = padded_json({"type": "badge", "token": make_vc_jwt(user["did"])}, MAX_PUBLIC_AUTH_BODY_BYTES * 4)
    res = client.post("/api/v1/verify", content=body, headers={"Content-Type": "application/json", **user["headers"]})
    assert res.status_code == 200 and res.json()["success"] is True
    # 같은 경로의 다른 메서드(본문을 읽지 않는다)는 예전처럼 405다.
    res = client.put("/api/v1/users/login", content=b" " * (MAX_PUBLIC_AUTH_BODY_BYTES + 1))
    assert res.status_code == 405


# ── 끝에 /가 붙은 경로, API 문서 ─────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [("GET", "/health/"), ("POST", "/api/v1/users/login/"),
                                         ("GET", "/api/v1/portfolios/"), ("GET", "/api/v1/shares/")])
def test_trailing_slash_is_404_not_a_redirect_built_from_the_host_header(client, method, path):
    # 회귀: 307 Location: http://<요청의 Host 헤더>/...로 답해, CloudFront 뒤에서는 ALB 주소가 http://로 드러났다.
    from main import app

    assert app.router.redirect_slashes is False
    res = client.request(method, path, headers={"Host": "evil.example"}, follow_redirects=False)
    assert res.status_code == 404 and res.json() == {"detail": "Not Found"}
    assert "location" not in res.headers
    assert_security_headers(res)


# main.py를 불러온 새 프로세스에서(문서 경로는 import 시점의 APP_ENV로 정해진다) 경로 목록과 응답 코드를 출력한다.
# DB에는 접속하지 않는다 (/health와 없는 경로만 부른다).
_DOCS_PROBE = r"""
import json, logging
logging.disable(logging.CRITICAL)
from fastapi.testclient import TestClient
import main
client = TestClient(main.app)
paths = ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/health"]
print(json.dumps({"status": {path: client.get(path).status_code for path in paths},
                  "routes": sorted(getattr(route, "path", "") for route in main.app.routes),
                  "openapi_url": main.app.openapi_url,
                  "cors": main.get_settings().cors_origins}))
"""


@pytest.mark.parametrize("app_env", ["development", "production"])
def test_api_docs_are_served_in_development_only(app_env):
    env = {name: value for name, value in os.environ.items() if not name.startswith(
        ("GOOGLE_", "KAKAO_", "NAVER_", "OAUTH_", "CORS_"))}
    env.update({"APP_ENV": app_env, "PYTHONDONTWRITEBYTECODE": "1"})
    if app_env == "production":  # 운영 검사를 통과하는 값 (실행할 때마다 새로 만든다)
        env.update({"JWT_SECRET": secrets.token_urlsafe(48), "SERVICE_API_KEY": "",
                    "HOLDER_KEY_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode(),
                    "DB_PASSWORD": secrets.token_urlsafe(24)})
    result = subprocess.run([sys.executable, "-B", "-W", "ignore", "-c", _DOCS_PROBE], cwd=BE_DIR, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    docs = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
    if app_env == "production":
        # 운영 이미지에서는 문서 경로 자체가 없다 (스키마·Swagger UI를 로그인 없이 내주지 않는다). API는 그대로다.
        assert seen["status"] == {"/docs": 404, "/redoc": 404, "/openapi.json": 404, "/docs/oauth2-redirect": 404,
                                  "/health": 200}
        assert seen["openapi_url"] is None and not docs & set(seen["routes"])
        assert seen["cors"] == []  # CORS_ORIGINS를 비우면 운영은 다른 출처를 허용하지 않는다
    else:
        assert seen["status"] == {path: 200 for path in [*sorted(docs), "/health"]}
        assert docs <= set(seen["routes"])
        assert seen["cors"] == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert "/health" in seen["routes"]


# ── 보안 응답 헤더 ───────────────────────────────────────────────────

def test_security_headers_on_success_error_and_500_responses(client, register, fake_dx):
    user = register()
    responses = {
        "health": (200, client.get("/health")),
        "login": (200, client.post("/api/v1/users/login", json={"email": user["email"], "password": "password1234"})),
        "me": (200, client.get("/api/v1/users/me", headers=user["headers"])),
        "careers": (200, client.get(f"/api/v1/subjects/{user['did']}/careers", headers=user["headers"])),
        "wrong password": (401, client.post("/api/v1/users/login",
                                            json={"email": user["email"], "password": "wrong-password"})),
        "unknown share": (404, client.get("/api/v1/public/shares/no-such-token")),
        "unknown route": (404, client.get("/api/v1/no-such-route")),
        "validation": (422, client.post("/api/v1/users/login", json={})),
    }
    fake_dx.error = RuntimeError("boom")  # UnhandledErrorMiddleware가 만드는 500에도 붙는다
    responses["500"] = (500, verify_badge(client, user))
    for name, (status, res) in responses.items():
        assert res.status_code == status, (name, res.text)
        assert_security_headers(res)
    # 본문은 그대로다.
    assert responses["health"][1].json()["status"] == "ok"
    assert responses["login"][1].json()["access_token"]
    assert responses["unknown share"][1].json() == {"detail": "Share link not found or expired"}
    assert responses["500"][1].json() == {"detail": "Internal server error"}


def test_share_token_and_public_share_view_are_not_cached(client, register):
    """한 번만 보여 주는 공유 토큰과 기업용 조회 결과(이름·DID·서명 원본)는 브라우저·프록시·CDN 캐시에 남기지 않는다
    (철회하면 바로 404가 되어야 하므로 캐시된 조회 결과가 다시 보이면 안 된다)."""
    user = register()
    verified = verify_badge(client, user)
    assert verified.status_code == 200 and verified.json()["success"], verified.text
    created = client.post("/api/v1/shares", json={"record_ids": [verified.json()["data"]["record_id"]]},
                          headers=user["headers"])
    assert created.status_code == 201, created.text
    view = client.get(f"/api/v1/public/shares/{created.json()['data']['token']}")  # 로그인 없음
    assert view.status_code == 200 and view.json()["holder"]["did"] == user["did"]
    for res in (created, view):
        assert_security_headers(res)


def test_hsts_only_in_production(client, monkeypatch):
    import main

    assert "strict-transport-security" not in client.get("/health").headers  # development(테스트 기본값)
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(app_env="production"))
    res = client.get("/health")
    assert res.status_code == 200
    assert res.headers["strict-transport-security"] == "max-age=31536000"
    for name, value in SECURITY_HEADERS.items():
        assert res.headers[name] == value


def test_hsts_with_real_production_settings(client, production_env):
    # 운영 검사를 통과하는 설정(conftest production_env)으로 APP_ENV=production일 때
    res = client.get("/health")
    assert res.status_code == 200
    assert res.headers["strict-transport-security"] == "max-age=31536000"


def test_endpoint_header_values_are_kept():
    from fastapi.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from main import SecurityHeadersMiddleware

    async def page(_):
        return PlainTextResponse("ok", headers={"Cache-Control": "public, max-age=60", "X-Frame-Options": "SAMEORIGIN"})

    app = Starlette(routes=[Route("/", page)], middleware=[Middleware(SecurityHeadersMiddleware)])
    res = TestClient(app).get("/")
    assert res.text == "ok"
    assert res.headers.get_list("cache-control") == ["public, max-age=60"]  # 엔드포인트 값을 두고 중복으로 붙이지 않는다
    assert res.headers.get_list("x-frame-options") == ["SAMEORIGIN"]
    assert res.headers["x-content-type-options"] == "nosniff" and res.headers["referrer-policy"] == "no-referrer"


def test_docs_and_cors_preflight_still_work(client):
    docs = client.get("/docs")
    assert docs.status_code == 200 and "swagger-ui" in docs.text
    # Swagger UI는 CDN 스크립트·인라인 스크립트를 쓰므로 CSP는 프레임 금지만 둔다 (불러오기 제한 없음).
    assert docs.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert client.get("/openapi.json").status_code == 200

    origin = allowed_origin()
    preflight = client.options("/api/v1/users/login", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert preflight.status_code == 200 and preflight.headers["access-control-allow-origin"] == origin


def _preflight(client, origin: str, request_headers: str, path: str = "/api/v1/portfolios", method: str = "POST"):
    return client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": method,
                                         "Access-Control-Request-Headers": request_headers})


def _allowed_headers(res) -> set[str]:
    return {name.strip().lower() for name in res.headers["access-control-allow-headers"].split(",")}


def test_cors_preflight_allows_the_headers_the_fe_sends_and_never_credentials(client, register):
    """다른 출처에 띄운 FE(axios)는 로그인한 요청마다 사전 요청에 authorization,content-type을 싣는다. Authorization이 허용
    목록에서 빠지면 그 배포의 모든 로그인 요청이 브라우저에서 막히는데, BE 테스트는 content-type만 보내고 있었다."""
    origin = allowed_origin()
    for requested in ("authorization,content-type", "Authorization, Content-Type", "x-service-key"):
        preflight = _preflight(client, origin, requested)
        assert preflight.status_code == 200 and preflight.headers["access-control-allow-origin"] == origin, requested
        assert {"authorization", "content-type", "x-service-key"} <= _allowed_headers(preflight)
        # 인증은 Authorization 헤더로만 한다: 쿠키 자격증명을 허용한다고 답하지 않는다.
        assert "access-control-allow-credentials" not in preflight.headers
    for method, path in (("PATCH", "/api/v1/portfolios/7d9c0c2e-0000-4000-8000-000000000000"),
                         ("DELETE", "/api/v1/shares/7d9c0c2e-0000-4000-8000-000000000000")):
        assert _preflight(client, origin, "authorization,content-type", path, method).status_code == 200
    # 목록에 없는 헤더, 허용하지 않은 출처의 사전 요청은 거절한다 (허용 출처 헤더를 붙이지 않는다).
    assert _preflight(client, origin, "authorization,x-other").status_code == 400
    refused = _preflight(client, "https://evil.example", "authorization,content-type")
    assert refused.status_code == 400 and "access-control-allow-origin" not in refused.headers
    # 실제 응답: 허용한 출처에만 출처 헤더가 붙고, 자격증명 허용 헤더는 어디에도 없다.
    user = register()
    for sender, expected in ((origin, origin), ("https://evil.example", None)):
        res = client.get("/api/v1/users/me", headers={**user["headers"], "Origin": sender})
        assert res.status_code == 200 and res.headers.get("access-control-allow-origin") == expected
        assert "access-control-allow-credentials" not in res.headers


# main.py를 불러온 새 프로세스에서(CORS 허용 출처는 import 시점의 설정으로 정해진다) 사전 요청의 응답을 출력한다.
_CORS_PROBE = r"""
import json, logging
logging.disable(logging.CRITICAL)
from fastapi.testclient import TestClient
import main
client = TestClient(main.app)
seen = {}
for origin in ("https://app.example.com", "https://APP.example.com", "http://localhost:5173", "https://app.example.com/"):
    res = client.options("/api/v1/portfolios", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"})
    seen[origin] = {"status": res.status_code, **{name: res.headers.get(name) for name in (
        "access-control-allow-origin", "access-control-allow-headers", "access-control-allow-credentials")}}
print(json.dumps({"origins": main.get_settings().cors_origins, "seen": seen}))
"""


def test_cors_preflight_in_production_with_explicit_origins():
    env = {name: value for name, value in os.environ.items() if not name.startswith(
        ("GOOGLE_", "KAKAO_", "NAVER_", "OAUTH_", "CORS_"))}
    env.update({"APP_ENV": "production", "PYTHONDONTWRITEBYTECODE": "1", "CORS_ORIGINS": "https://app.example.com",
                "JWT_SECRET": secrets.token_urlsafe(48), "SERVICE_API_KEY": "",
                "HOLDER_KEY_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode(),
                "DB_PASSWORD": secrets.token_urlsafe(24)})  # 운영 검사를 통과하는 값 (실행할 때마다 새로 만든다)
    result = subprocess.run([sys.executable, "-B", "-W", "ignore", "-c", _CORS_PROBE], cwd=BE_DIR, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert seen["origins"] == ["https://app.example.com"]
    allowed = seen["seen"]["https://app.example.com"]
    assert allowed["status"] == 200 and allowed["access-control-allow-origin"] == "https://app.example.com"
    assert "authorization" in allowed["access-control-allow-headers"].lower()
    assert allowed["access-control-allow-credentials"] is None
    # 넣은 출처와 글자 그대로 같을 때만이다: 개발용 기본 출처, 대소문자가 다른 호스트, 끝에 /가 붙은 값은 허용하지 않는다.
    for origin in ("https://APP.example.com", "http://localhost:5173", "https://app.example.com/"):
        assert seen["seen"][origin]["status"] == 400 and seen["seen"][origin]["access-control-allow-origin"] is None


# ── 접근 로그의 공유 링크 토큰·DID 가리기 ─────────────────────────────

@pytest.mark.parametrize("path,expected", [
    ("/api/v1/public/shares/QnqJU3S9_x-Tok3n", "/api/v1/public/shares/[redacted]"),
    ("/api/v1/public/shares/QnqJU3S9_x-Tok3n?utm=1", "/api/v1/public/shares/[redacted]?utm=1"),
    ("/api/v1/public/shares//QnqJU3S9_x-Tok3n/extra", "/api/v1/public/shares/[redacted]"),
    ("/API/V1/PUBLIC/SHARES/QnqJU3S9_x-Tok3n", "/API/V1/PUBLIC/SHARES/[redacted]"),
    # 라우트에 닿지 않는 404 요청(주소를 이어 붙이다 생긴 //, 대소문자 차이)도 접근 로그에는 남으므로 가린다.
    ("/api/v1/public//shares/QnqJU3S9_x-Tok3n", "/api/v1/public//shares/[redacted]"),
    ("/api/v1/PUBLIC/shares/QnqJU3S9_x-Tok3n", "/api/v1/PUBLIC/shares/[redacted]"),
    ("/api/v1/public///shares//QnqJU3S9_x-Tok3n?utm=1", "/api/v1/public///shares/[redacted]?utm=1"),
    ("/api/v1/subjects/did%3Akey%3Az6MkTestOnly/careers", "/api/v1/subjects/[did]/careers"),
    ("/api/v1/subjects/did%3Akey%3Az6MkTestOnly/evidence?mode=labeled&record_id=1",
     "/api/v1/subjects/[did]/evidence?mode=labeled&record_id=1"),
    # Google 콜백의 쿼리(인가 코드·state·code_verifier)는 통째로 가린다. BE의 GET 콜백(개발용)과, BE로 잘못 들어온
    # FE 콜백 주소 모두. 라우트에 닿지 않는 표기(대소문자, 겹친 슬래시, 끝의 /)도 접근 로그에는 남으므로 가린다.
    ("/api/v1/users/oauth/google/callback?code=4%2F0A-CODE&state=eyJ.STATE.sig&code_verifier=PKCE-VERIFIER",
     "/api/v1/users/oauth/google/callback?[redacted]"),
    ("/api/v1/users/oauth/google/callback?error=access_denied", "/api/v1/users/oauth/google/callback?[redacted]"),
    ("/API/V1/USERS/OAUTH/GOOGLE/CALLBACK?code=4%2F0A-CODE", "/API/V1/USERS/OAUTH/GOOGLE/CALLBACK?[redacted]"),
    ("/api/v1/users/oauth//google//callback/?code=4%2F0A-CODE", "/api/v1/users/oauth//google//callback/?[redacted]"),
    ("/auth/google/callback?code=4%2F0A-CODE&state=eyJ.STATE.sig", "/auth/google/callback?[redacted]"),
    ("/api/v1/users/oauth/google/callback", "/api/v1/users/oauth/google/callback"),
    ("/api/v1/users/oauth/google/authorize?redirect=true", "/api/v1/users/oauth/google/authorize?redirect=true"),
    # 카카오·네이버 콜백도 같다 (BE의 GET 콜백, 잘못 들어온 FE 콜백 주소, 대소문자·겹친 슬래시).
    ("/api/v1/users/oauth/kakao/callback?code=KAKAO-CODE&state=eyJ.STATE.sig&code_verifier=PKCE-VERIFIER",
     "/api/v1/users/oauth/kakao/callback?[redacted]"),
    ("/api/v1/users/oauth/naver/callback?code=NAVER-CODE&state=eyJ.STATE.sig",
     "/api/v1/users/oauth/naver/callback?[redacted]"),
    ("/auth/kakao/callback?code=KAKAO-CODE&state=eyJ.STATE.sig", "/auth/kakao/callback?[redacted]"),
    ("/auth/naver/callback?error=access_denied&state=eyJ.STATE.sig", "/auth/naver/callback?[redacted]"),
    ("/API/V1/USERS/OAUTH/NAVER/CALLBACK?code=NAVER-CODE", "/API/V1/USERS/OAUTH/NAVER/CALLBACK?[redacted]"),
    ("/api/v1/users/oauth//kakao//callback/?code=KAKAO-CODE", "/api/v1/users/oauth//kakao//callback/?[redacted]"),
    ("/api/v1/users/oauth/kakao/authorize?redirect=true", "/api/v1/users/oauth/kakao/authorize?redirect=true"),
    ("/api/v1/users/oauth/naver/callback", "/api/v1/users/oauth/naver/callback"),
    # 비밀이 아닌 경로는 그대로 둔다 (공유 링크 관리용 ID, 포트폴리오 ID 등).
    ("/api/v1/shares/7d9c0c2e-0000-4000-8000-000000000000", "/api/v1/shares/7d9c0c2e-0000-4000-8000-000000000000"),
    ("/api/v1/portfolios/7d9c0c2e-0000-4000-8000-000000000000",
     "/api/v1/portfolios/7d9c0c2e-0000-4000-8000-000000000000"),
    ("/health", "/health"),
])
def test_redact_path(path, expected):
    from main import redact_path

    assert redact_path(path) == expected


def test_access_log_filter_rewrites_every_string_argument():
    import main  # noqa: F401  (import할 때 uvicorn.access 로거에 필터가 붙는다)

    token = secrets.token_urlsafe(32)
    access = logging.getLogger("uvicorn.access")
    # 인자 순서가 바뀌어도(다른 서버 구현·로그 설정) 문자열 인자라면 모두 가린다.
    record = access.makeRecord("uvicorn.access", logging.INFO, __file__, 0, '%s %s - "%s"',
                               (f"/api/v1/public/shares/{token}", 200, "10.0.0.7:51234"), None)
    assert access.filter(record)
    assert record.getMessage() == '/api/v1/public/shares/[redacted] 200 - "10.0.0.7:51234"'
    # 요청 줄 전체가 한 인자로 와도 Google 콜백의 쿼리만 가리고 뒤의 HTTP 버전은 남긴다.
    record = access.makeRecord("uvicorn.access", logging.INFO, __file__, 0, '%s - "%s" %d', (
        "10.0.0.7:51234", f"GET /api/v1/users/oauth/google/callback?code={token}&state=x.y.z HTTP/1.1", 200), None)
    assert access.filter(record)
    assert record.getMessage() == (
        '10.0.0.7:51234 - "GET /api/v1/users/oauth/google/callback?[redacted] HTTP/1.1" 200')


class _Transport:
    """uvicorn H11Protocol이 부르는 메서드만 흉내 낸 메모리 전송 계층 (소켓 없음)."""

    def __init__(self):
        self.written, self.closed = b"", False

    def get_extra_info(self, name, default=None):
        return {"sockname": ("10.0.1.25", 3000), "peername": ("10.0.0.7", 51234)}.get(name, default)

    def write(self, data):
        self.written += data

    def is_closing(self):
        return self.closed

    def close(self):
        self.closed = True

    def pause_reading(self):
        pass

    def resume_reading(self):
        pass


async def test_uvicorn_access_log_line_hides_share_token_and_did(caplog):
    """uvicorn의 실제 HTTP 프로토콜(H11Protocol)로 요청을 흘려 stdout(→ CloudWatch)으로 가는 접근 로그 줄을 본다."""
    from uvicorn.config import LOGGING_CONFIG, Config
    from uvicorn.logging import AccessFormatter
    from uvicorn.protocols.http.h11_impl import H11Protocol
    from uvicorn.server import ServerState

    import main  # noqa: F401  (import할 때 uvicorn.access 로거에 필터가 붙는다)

    async def app(scope, receive, send):  # 경로와 상관없이 404를 돌려주는 최소 ASGI 앱
        await send({"type": "http.response.start", "status": 404, "headers": [(b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})

    token, did = secrets.token_urlsafe(32), "did:key:z6MkAccessLogTestOnly"
    code, verifier = "4%2F0A" + secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    paths = [f"/api/v1/public/shares/{token}", f"/api/v1/subjects/{quote(did, safe='')}/evidence?mode=labeled",
             # 라우트에 닿지 않는 겹친 슬래시: uvicorn은 디코딩한 경로를 다시 인코딩해 남긴다(%2F%2F → //).
             f"/api/v1/public//shares/{token}", f"/api/v1/public%2F%2Fshares/{token}",
             # BE 단독 테스트용 GET 콜백: 인가 코드·state·code_verifier가 쿼리에 실린다.
             f"/api/v1/users/oauth/google/callback?code={code}&state={token}.{token}&code_verifier={verifier}"]
    config = Config(app=app, log_config=None)  # 테스트 프로세스의 로깅 설정은 바꾸지 않는다 (기록은 caplog로 받음)
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        for path in paths:
            state, transport = ServerState(), _Transport()
            protocol = H11Protocol(config=config, server_state=state, app_state={})
            protocol.connection_made(transport)
            protocol.data_received(f"GET {path} HTTP/1.1\r\nHost: api.example.com\r\n\r\n".encode())
            for _ in range(500):
                if protocol.cycle is not None and protocol.cycle.response_complete and not state.tasks:
                    break
                await asyncio.sleep(0.01)
            protocol.connection_lost(None)

    formatter = AccessFormatter(LOGGING_CONFIG["formatters"]["access"]["fmt"], use_colors=False)
    lines = [formatter.format(r) for r in caplog.records if r.name == "uvicorn.access"]
    assert len(lines) == 5, lines
    assert '10.0.0.7:51234 - "GET /api/v1/public/shares/[redacted] HTTP/1.1" 404' in lines[0]
    assert '"GET /api/v1/subjects/[did]/evidence?mode=labeled HTTP/1.1" 404' in lines[1]
    assert all('"GET /api/v1/public//shares/[redacted] HTTP/1.1" 404' in line for line in lines[2:4])
    # 회귀: 접근 로그 줄에 인가 코드·state·code_verifier가 그대로 남았다.
    assert '"GET /api/v1/users/oauth/google/callback?[redacted] HTTP/1.1" 404' in lines[4]
    assert not any(token in line or "z6MkAccessLogTestOnly" in line for line in lines)
    assert not any(code in line or verifier in line or "code=" in line for line in lines)


# ── 발급·검증 인프라 오류 응답, httpx 로그 ───────────────────────────

def test_provider_error_is_502_with_fixed_body_and_logged(client, register, fake_dx, caplog):
    user = register()
    origin = allowed_origin()
    fake_dx.error = ProviderError("DXWorks 오류 (TEMPLATE_NOT_FOUND): template tpl-x not found in org 4711",
                                  upstream_status=404)
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = verify_badge(client, user, {"Origin": origin})
    assert res.status_code == 502
    # 협력사 오류 코드·문구는 응답에 싣지 않는다 (템플릿 ID 추측·내부 정보 노출 방지).
    assert res.json() == {"detail": "Credential service error", "code": "PROVIDER_ERROR"}
    assert "TEMPLATE_NOT_FOUND" not in res.text and "4711" not in res.text
    assert res.headers["access-control-allow-origin"] == origin
    assert_security_headers(res)
    # 운영자는 협력사 오류를 로그 한 줄에서 본다 (지표 필터 "Provider error" 접두어 유지).
    assert [r.getMessage() for r in vc_be_records(caplog)] == [
        "Provider error: DXWorks 오류 (TEMPLATE_NOT_FOUND): template tpl-x not found in org 4711 (upstream=404)"]


def test_provider_unavailable_is_503_with_fixed_body(client, register, fake_dx, caplog, identity_enabled):
    user = register()
    fake_dx.error = ProviderError("DXWORKS_API_HOST가 설정되지 않았습니다", unavailable=True)
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = client.post("/api/v1/verify", json={"type": "identity", "token": make_vp_jwt(user["did"])},
                          headers=user["headers"])
    assert res.status_code == 503
    assert res.json() == {"detail": "Credential service is not configured", "code": "PROVIDER_NOT_CONFIGURED"}
    assert "DXWORKS" not in res.text  # 설정 이름은 응답에 싣지 않는다
    assert [r.getMessage() for r in vc_be_records(caplog)] == [
        "Provider error: DXWORKS_API_HOST가 설정되지 않았습니다 (upstream=None)"]


def test_httpx_request_lines_are_not_logged_by_default(caplog):
    import main  # noqa: F401  (import할 때 httpx 로거 수준을 정한다)

    if os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG":
        pytest.skip("LOG_LEVEL=DEBUG로 실행하면 httpx 요청 줄을 남기는 것이 맞다")
    assert logging.getLogger("httpx").level == logging.WARNING
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    with httpx.Client(transport=transport) as http:
        with caplog.at_level(logging.DEBUG):  # 루트·캡처 수준을 낮춰도 httpx 로거 자체가 WARNING이라 남지 않는다
            assert http.get("https://dx-private-host.invalid/api/v1/badges/verify").status_code == 200
        assert not [r for r in caplog.records if r.name.startswith("httpx")]
        # 대조: 수준을 INFO로 풀면 httpx는 요청 URL 전체를 남긴다 (이 테스트가 실제로 그 줄을 막고 있음을 확인).
        with caplog.at_level(logging.INFO, logger="httpx"):
            http.get("https://dx-private-host.invalid/api/v1/badges/verify")
        assert any("dx-private-host.invalid" in r.getMessage() for r in caplog.records if r.name == "httpx")


@pytest.mark.parametrize("log_level,httpx_level", [(None, logging.WARNING), ("debug", logging.NOTSET),
                                                   ("", logging.WARNING), ("  ", logging.WARNING)])
def test_httpx_logger_level_follows_log_level(log_level, httpx_level):
    # main.py는 import 시점에 로깅을 설정하므로 새 프로세스에서 확인한다 (바이트코드 파일은 만들지 않는다).
    # 비워 둔 LOG_LEVEL(`LOG_LEVEL=`)은 없는 것과 같다 (예전에는 "Unknown level: ''" traceback으로 서버가 뜨지 않았다).
    env = {name: value for name, value in os.environ.items() if name != "LOG_LEVEL"}
    if log_level is not None:
        env["LOG_LEVEL"] = log_level
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-c", "import logging, main; print(logging.getLogger('httpx').level)"],
        cwd=BE_DIR, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert int(result.stdout.strip().splitlines()[-1]) == httpx_level


# main.py를 불러온 새 프로세스에서 소켓 없는 가짜 전송으로 httpx 요청(INFO 요청 줄)과 httpcore 요청(DEBUG 줄: 접속 호스트·
# 응답 헤더)을 하나씩 보내고 두 로거의 수준을 출력한다. 로그는 basicConfig 처리기를 거쳐 stderr로 나간다.
_HTTP_LOG_PROBE = r"""
import json, logging
import httpcore, httpx
import main
with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as http:
    http.get("https://httpx-probe.invalid/api/v1/badges/verify")
backend = httpcore.MockBackend([b"HTTP/1.1 200 OK\r\n", b"Content-Length: 0\r\n", b"\r\n"])
with httpcore.ConnectionPool(network_backend=backend) as pool:
    pool.request("GET", "http://httpcore-probe.invalid/api/v1/badges/verify")
print(json.dumps([logging.getLogger(name).level for name in ("httpx", "httpcore")]))
"""


@pytest.mark.parametrize("log_level", ["NOTSET", "INFO", "WARNING", "DEBUG"])
def test_httpx_and_httpcore_lines_are_not_logged_unless_debug(log_level):
    # 회귀: httpx만 WARNING으로 묶어 LOG_LEVEL=NOTSET(루트가 모든 수준을 남긴다)이면 httpcore DEBUG 줄이 그대로 나갔다.
    # 이제 DEBUG가 아니면 httpx·httpcore 둘 다 WARNING이고, DEBUG면 둘 다 그대로 둔다(루트 수준을 따른다).
    env = {**os.environ, "LOG_LEVEL": log_level, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-B", "-c", _HTTP_LOG_PROBE], cwd=BE_DIR, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    levels = json.loads(result.stdout.strip().splitlines()[-1])
    if log_level == "DEBUG":
        assert levels == [logging.NOTSET, logging.NOTSET]
        assert "HTTP Request: GET https://httpx-probe.invalid/" in result.stderr
        assert "connect_tcp.started host='httpcore-probe.invalid'" in result.stderr
    else:
        assert levels == [logging.WARNING, logging.WARNING]
        assert "probe.invalid" not in result.stderr


# ── 저장할 수 없는 문자 (NUL·짝 없는 서로게이트) ───────────────────────

def test_validation_error_for_lone_surrogate_is_ascii_422_without_the_input(client, register, caplog):
    user = register()
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = post_json(client, "/api/v1/users/register",
                        {"name": SURROGATE, "email": f"t-{secrets.token_hex(5)}@test.com", "password": "password1234"})
        # 키 이름에 짝 없는 서로게이트가 있는 본문 (모르는 키를 금지한 모델)
        keyed = post_json(client, "/api/v1/shares", {"record_ids": [], SURROGATE: 1}, user["headers"])
    assert res.status_code == 422, res.text
    assert res.content.isascii()  # FastAPI 기본 처리기는 이 입력을 UTF-8로 인코딩하지 못해 500이 났다
    [error] = [e for e in res.json()["detail"] if e["loc"] == ["body", "name"]]
    assert set(error) == {"type", "loc", "msg"}  # 입력값(input)·ctx는 되돌려 주지 않는다
    assert "ud800" not in res.text
    assert_security_headers(res)
    assert keyed.status_code == 422 and keyed.content.isascii() and "ud800" not in keyed.text, keyed.text
    assert not vc_be_records(caplog)  # 'Unhandled error' 없음


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_validation_error_echoing_non_finite_number_is_422_not_500(client, caplog, literal):
    # 파이썬 json은 NaN·Infinity 리터럴을 받아들이지만 JSON 응답(allow_nan=False)으로는 되돌려 줄 수 없다.
    # 토큰 없이 보낼 수 있는 로그인·가입 본문도 500('Unhandled error')이 아니라 422여야 한다.
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        login = client.post("/api/v1/users/login", content=f'{{"email": {literal}, "password": "x"}}',
                            headers={"Content-Type": "application/json"})
        register = client.post("/api/v1/users/register",
                               content=f'{{"name": {literal}, "email": "t@test.com", "password": "password1234"}}',
                               headers={"Content-Type": "application/json"})
    for res, field in ((login, "email"), (register, "name")):
        assert res.status_code == 422, res.text
        [error] = [e for e in res.json()["detail"] if e["loc"] == ["body", field]]
        assert "input" not in error and error["type"] == "string_type"
        assert_security_headers(res)
    assert not vc_be_records(caplog)  # 'Unhandled error' 없음


def test_validation_error_body_has_only_loc_msg_and_type(client):
    """회귀: FastAPI 기본 422는 오류마다 입력값(input)을 통째로 되돌려 줬다. 본문 전체가 틀린 요청(필수 필드 없음)이면 걸린
    필드마다 본문이 한 번씩 실려, 토큰 없이 보낸 1.7 MiB가 3~4.5 MiB 응답이 됐다."""
    body = {"name": "가" * 101, "email": f"t-{secrets.token_hex(5)}@test.com", "password": "password1234"}
    res = client.post("/api/v1/users/register", json=body)
    assert res.status_code == 422
    assert res.json() == {"detail": [{"type": "string_too_long", "loc": ["body", "name"],
                                      "msg": "String should have at most 100 characters"}]}
    assert "가가가" not in res.text and "uac00" not in res.text  # 입력값은 원문으로도 이스케이프로도 없다
    assert res.headers["content-type"] == "application/json"

    # 필수 필드가 모두 빠진 본문(모르는 키만 있음): 필드마다 본문 전체를 되돌려 주던 경우다.
    junk = {f"k{index:04d}": "v" * 20 for index in range(400)}  # 약 13 KiB (공개 인증 API 상한 안)
    for path, fields in (("/api/v1/users/login", ["email", "password"]),
                         ("/api/v1/users/register", ["name", "email", "password"]),
                         ("/api/v1/users/oauth/google/callback", ["code", "state"]),
                         ("/api/v1/users/oauth/kakao/callback", ["code", "state"]),
                         ("/api/v1/users/oauth/naver/callback", ["code", "state"])):
        res = client.post(path, json=junk)
        assert res.status_code == 422, (path, res.text)
        assert res.json() == {"detail": [{"type": "missing", "loc": ["body", field], "msg": "Field required"}
                                         for field in fields]}
        assert len(res.content) < 400 and "k0001" not in res.text


def test_validation_error_body_is_bounded_for_models_that_forbid_unknown_keys(client, register):
    """회귀: 모르는 키를 금지한 모델은 키마다 오류를 하나씩 낸다. 로그인한 사용자가 모르는 키 13만 개(1.7 MiB)를 보내면
    오류 13만 개(14 MiB)를 1초 넘게 걸려 돌려줬다 (입력값을 빼도 11 MiB). 이제 앞의 20개만, 긴 키 이름은 자리표시로 싣는다."""
    from main import MAX_VALIDATION_ERRORS, MAX_VALIDATION_LOC_CHARS

    assert (MAX_VALIDATION_ERRORS, MAX_VALIDATION_LOC_CHARS) == (20, 64)
    user = register()
    # 기본 본문 상한(64 KiB) 안에서 보낼 수 있는 만큼: 모르는 키 5,500개 (더 큰 본문은 이제 읽기 전에 413이다)
    body = {"record_ids": [], **{f"k{index:05d}": 0 for index in range(5_500)}}
    res = client.post("/api/v1/shares", json=body, headers=user["headers"])
    assert res.status_code == 422 and 60_000 < len(res.request.content) <= 64 * 1024
    detail = res.json()["detail"]
    assert len(detail) == 20 and len(res.content) < 2500
    assert all(set(error) == {"type", "loc", "msg"} for error in detail)

    long_key, edge_key = "K" * 65, "E" * 64
    res = client.post("/api/v1/shares", json={"record_ids": [], long_key: 1, edge_key: 2, "가" * 65: 3},
                      headers=user["headers"])
    assert res.status_code == 422 and res.content.isascii()
    locs = [error["loc"] for error in res.json()["detail"] if error["type"] == "extra_forbidden"]
    assert sorted(locs) == sorted([["body", "<long key>"], ["body", edge_key], ["body", "<long key>"]])
    assert long_key not in res.text


async def test_validation_error_handler_drops_input_and_bounds_the_answer():
    from fastapi.exceptions import RequestValidationError
    from starlette.requests import Request

    from main import validation_error_handler

    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    error = {"type": "string_too_long", "loc": ("body", "name"), "msg": "String should have at most 1 character",
             "input": SURROGATE, "ctx": {"max_length": 1}}
    res = await validation_error_handler(request, RequestValidationError([error]))
    assert res.status_code == 422 and res.body.isascii()
    assert json.loads(res.body) == {"detail": [{"type": "string_too_long", "loc": ["body", "name"],
                                                "msg": "String should have at most 1 character"}]}

    # NaN이 든 입력값·ctx도 싣지 않으므로 JSON으로 만들지 못하는 일이 없다. 한글 사유와, loc에 들어간 키 이름의
    # 짝 없는 서로게이트는 ASCII 이스케이프로 나간다 (UTF-8로는 인코딩하지 못해 500이 된다).
    korean = {"type": "value_error", "loc": ("body", "items", 3, "가나", SURROGATE), "msg": "Value error, 한글 사유",
              "input": float("nan"), "ctx": {"error": ValueError("x"), "limit": float("inf")}}
    res = await validation_error_handler(request, RequestValidationError([korean]))
    assert res.body.isascii() and b"\\ud800" in res.body
    assert json.loads(res.body) == {"detail": [
        {"type": "value_error", "loc": ["body", "items", 3, "가나", SURROGATE], "msg": "Value error, 한글 사유"}]}

    # 앞의 20개만, 64자를 넘는 loc 항목(보낸 쪽이 정한 키 이름)은 자리표시로. 정수 위치는 그대로 둔다.
    many = [{"type": "extra_forbidden", "loc": ("body", index, "k" * (60 + index)), "msg": "Extra inputs are not permitted",
             "input": "x" * 1000} for index in range(25)]
    res = await validation_error_handler(request, RequestValidationError(many))
    detail = json.loads(res.body)["detail"]
    assert [error["loc"] for error in detail] == (
        [["body", index, "k" * (60 + index)] for index in range(5)]
        + [["body", index, "<long key>"] for index in range(5, 20)])
    assert len(res.body) < 2500 and b"xxxx" not in res.body


@pytest.mark.parametrize("error", [
    UnicodeEncodeError("utf-8", SURROGATE, 1, 2, "surrogates not allowed"),
    psycopg.DataError("PostgreSQL text fields cannot contain NUL (0x00) bytes"),
    psycopg.errors.UntranslatableCharacter("unsupported Unicode escape sequence"),
], ids=["surrogate-encode", "nul-text-param", "nul-jsonb"])
def test_unstorable_text_past_validation_is_422_without_traceback(client, register, fake_dx, caplog, error):
    user = register()
    origin = allowed_origin()
    fake_dx.error = error  # 요청 검사를 지나 처리 중(DB 저장·외부 호출)에 난 것처럼
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = verify_badge(client, user, {"Origin": origin})
    assert res.status_code == 422 and res.json() == UNSTORABLE_422
    assert res.headers["access-control-allow-origin"] == origin
    assert_security_headers(res)
    # 'Unhandled error'(경보 지표)·추적 없이 한 줄만 남기고, 사용자 입력이 들어 있을 수 있는 예외 메시지는 남기지 않는다.
    [warning] = vc_be_records(caplog)
    assert warning.levelno == logging.WARNING and warning.exc_info is None
    assert warning.getMessage() == (
        f"Rejected unstorable text: method=POST path=/api/v1/verify error={type(error).__name__}")


@pytest.mark.parametrize("error", [
    psycopg.errors.InvalidTextRepresentation("invalid input syntax for type uuid"),
    psycopg.DataError("date too large (after year 10K)"),
    UnicodeEncodeError("latin-1", "가", 0, 1, "ordinal not in range(256)"),
    RuntimeError("PostgreSQL text fields cannot contain NUL (0x00) bytes"),
], ids=["22P02", "other-client-data-error", "latin-1", "not-a-data-error"])
def test_other_errors_stay_500_with_traceback(client, register, fake_dx, caplog, error):
    user = register()
    fake_dx.error = error
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = verify_badge(client, user)
    assert res.status_code == 500 and res.json() == {"detail": "Internal server error"}
    [record] = vc_be_records(caplog)
    assert record.getMessage() == "Unhandled error" and record.exc_info is not None


def test_unstorable_text_warning_hides_share_token_and_after_start_is_not_rewritten(caplog):
    from fastapi.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import StreamingResponse
    from starlette.routing import Route

    from main import UnhandledErrorMiddleware

    async def share_view(_):
        raise UnicodeEncodeError("utf-8", SURROGATE, 1, 2, "surrogates not allowed")

    async def stream(_):
        async def body():
            yield b"partial"
            raise UnicodeEncodeError("utf-8", SURROGATE, 1, 2, "surrogates not allowed")

        return StreamingResponse(body())

    app = Starlette(routes=[Route("/api/v1/public/shares/{token}", share_view), Route("/stream", stream)],
                    middleware=[Middleware(UnhandledErrorMiddleware)])
    token = secrets.token_urlsafe(32)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        with caplog.at_level(logging.WARNING, logger="vc-be"):
            res = test_client.get(f"/api/v1/public/shares/{token}")
        assert res.status_code == 422 and res.json() == UNSTORABLE_422
        assert [r.getMessage() for r in vc_be_records(caplog)] == [
            "Rejected unstorable text: method=GET path=/api/v1/public/shares/[redacted] error=UnicodeEncodeError"]

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="vc-be"):
            streamed = test_client.get("/stream")
        # 응답을 이미 보내기 시작했으면 422로 바꿀 수 없으므로 예전처럼 'Unhandled error'로 남기고 연결을 끊게 둔다.
        assert streamed.status_code == 200
        assert [r.getMessage() for r in vc_be_records(caplog)] == ["Unhandled error"]


def test_unstorable_text_classifier_matches_real_psycopg_errors(db):
    """안전망이 고르는 예외가 실제 psycopg·PostgreSQL이 내는 예외와 같은지 본다 (메시지·SQLSTATE가 바뀌면 여기서 드러난다)."""
    from psycopg.types.json import Jsonb

    from main import _is_unstorable_text_error

    def raised(sql: str, value) -> Exception:
        with pytest.raises(Exception) as caught:
            db.execute(sql, (value,))
        return caught.value

    assert _is_unstorable_text_error(raised("SELECT %s::text", NUL))  # psycopg가 보내기 전에 거절 (SQLSTATE 없음)
    assert _is_unstorable_text_error(raised("SELECT %s::text", SURROGATE))  # UTF-8 인코딩 실패
    assert _is_unstorable_text_error(raised("SELECT %s::jsonb", Jsonb({"name": NUL})))  # 22P05
    assert not _is_unstorable_text_error(raised("SELECT %s::uuid", "not-a-uuid"))  # 22P02: 진짜 결함은 계속 500
