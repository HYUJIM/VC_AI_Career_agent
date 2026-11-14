"""BE 앱 공통 보안 처리 (main.py).

- 요청 본문 상한(2 MiB): 넘으면 인증·JSON 파싱 전에 413. 오류 미들웨어 안쪽이라 'Unhandled error'로 기록하지 않는다.
- 보안 응답 헤더(SecurityHeadersMiddleware): no-store·nosniff·no-referrer·프레임 금지, HSTS는 APP_ENV=production에서만.
- 접근 로그(uvicorn.access)에서 공유 링크 토큰·DID 가리기.
- 발급·검증 인프라 오류(ProviderError)는 고정 문구와 code로만 답하고 협력사 문구는 로그에만 남긴다.
  httpx 요청 줄(비공개 DXWorks 호스트 URL)과 httpcore 디버그 줄(접속 호스트·응답 헤더)은 LOG_LEVEL=DEBUG일 때만 남긴다.
- 저장할 수 없는 문자(NUL·짝 없는 서로게이트): 422 입력값 되돌려 주기의 ASCII 대체, 요청 검사를 지나친 경우의 422 안전망.

정상 흐름(가입·로그인·조회)의 상태 코드와 본문이 그대로인지도 함께 본다. 값은 모두 가상의 값이다.
"""
import asyncio
import json
import logging
import os
import secrets
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import psycopg
import pytest

from app.integrations.ports import ProviderError
from tests.conftest import make_vc_jwt

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

def test_body_at_limit_is_processed_normally(client, register):
    from main import MAX_REQUEST_BODY_BYTES

    assert MAX_REQUEST_BODY_BYTES == 2 * 1024 * 1024
    user = register()
    body = json.dumps({"email": user["email"], "password": "password1234"}).encode()
    body += b" " * (MAX_REQUEST_BODY_BYTES - len(body))  # JSON 뒤 공백으로 정확히 상한 크기 (상한은 포함)
    res = client.post("/api/v1/users/login", content=body, headers={"Content-Type": "application/json"})
    assert res.status_code == 200 and res.json()["access_token"]


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

    from main import MAX_REQUEST_BODY_BYTES, SecurityHeadersMiddleware, UnhandledErrorMiddleware, app

    # 바깥 → 안쪽. CORS가 가장 바깥이라 500·413·422에도 CORS 헤더가 붙고, 보안 헤더는 오류 미들웨어가 만든 500에도
    # 붙고, 본문 상한은 오류 미들웨어 안쪽이라 413을 보낼 때 쓰는 내부 예외가 'Unhandled error'로 기록되지 않는다.
    assert [m.cls for m in app.user_middleware] == [CORSMiddleware, SecurityHeadersMiddleware,
                                                    UnhandledErrorMiddleware, RequestBodyLimitMiddleware]
    assert app.user_middleware[-1].kwargs == {"max_body_size": MAX_REQUEST_BODY_BYTES}


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
    paths = [f"/api/v1/public/shares/{token}", f"/api/v1/subjects/{quote(did, safe='')}/evidence?mode=labeled",
             # 라우트에 닿지 않는 겹친 슬래시: uvicorn은 디코딩한 경로를 다시 인코딩해 남긴다(%2F%2F → //).
             f"/api/v1/public//shares/{token}", f"/api/v1/public%2F%2Fshares/{token}"]
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
    assert len(lines) == 4, lines
    assert '10.0.0.7:51234 - "GET /api/v1/public/shares/[redacted] HTTP/1.1" 404' in lines[0]
    assert '"GET /api/v1/subjects/[did]/evidence?mode=labeled HTTP/1.1" 404' in lines[1]
    assert all('"GET /api/v1/public//shares/[redacted] HTTP/1.1" 404' in line for line in lines[2:])
    assert not any(token in line or "z6MkAccessLogTestOnly" in line for line in lines)


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


def test_provider_unavailable_is_503_with_fixed_body(client, register, fake_dx, caplog):
    user = register()
    fake_dx.error = ProviderError("DXWORKS_API_HOST가 설정되지 않았습니다", unavailable=True)
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = client.post("/api/v1/verify", json={"type": "identity", "token": user["did"]}, headers=user["headers"])
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


@pytest.mark.parametrize("log_level,httpx_level", [(None, logging.WARNING), ("debug", logging.NOTSET)])
def test_httpx_logger_level_follows_log_level(log_level, httpx_level):
    # main.py는 import 시점에 로깅을 설정하므로 새 프로세스에서 확인한다 (바이트코드 파일은 만들지 않는다).
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

def test_validation_error_echoing_lone_surrogate_is_ascii_422(client, caplog):
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = post_json(client, "/api/v1/users/register",
                        {"name": SURROGATE, "email": f"t-{secrets.token_hex(5)}@test.com", "password": "password1234"})
    assert res.status_code == 422, res.text
    assert res.content.isascii()  # FastAPI 기본 처리기는 이 입력을 UTF-8로 인코딩하지 못해 500이 났다
    [error] = [e for e in res.json()["detail"] if e["loc"] == ["body", "name"]]
    assert error["input"] == SURROGATE  # 되돌려 주는 입력값은 \\ud800 이스케이프로 그대로 읽힌다
    assert_security_headers(res)
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


def test_normal_validation_error_body_is_unchanged(client):
    body = {"name": "가" * 101, "email": f"t-{secrets.token_hex(5)}@test.com", "password": "password1234"}
    res = client.post("/api/v1/users/register", json=body)
    assert res.status_code == 422
    # 평소의 422는 FastAPI 기본 형식 그대로다 (한글 입력값은 \\u 이스케이프가 아닌 UTF-8 원문).
    assert "가가가" in res.text and "\\uac00" not in res.text
    [error] = [e for e in res.json()["detail"] if e["loc"] == ["body", "name"]]
    assert error["type"] == "string_too_long" and error["input"] == "가" * 101


async def test_validation_error_handler_falls_back_only_when_encoding_fails():
    from fastapi.exceptions import RequestValidationError
    from starlette.requests import Request

    from main import validation_error_handler

    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    error = {"type": "string_too_long", "loc": ("body", "name"), "msg": "String should have at most 1 character",
             "input": SURROGATE, "ctx": {"max_length": 1}}
    res = await validation_error_handler(request, RequestValidationError([error]))
    assert res.status_code == 422 and res.body.isascii()
    assert json.loads(res.body) == {"detail": [{**error, "loc": ["body", "name"]}]}

    normal = await validation_error_handler(request, RequestValidationError([{**error, "input": "가나"}]))
    assert normal.status_code == 422 and "가나".encode() in normal.body  # 평소에는 FastAPI 기본 처리기 그대로


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
