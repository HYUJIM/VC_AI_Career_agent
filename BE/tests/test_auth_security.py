"""인증 보안 테스트 (실제 PostgreSQL + 가짜 발급·검증기, 가상 데이터).

- 인증 필수: 공개 API(/health, 회원가입, 로그인, 공유 링크 조회, 문서)를 뺀 모든 경로는 토큰·서비스 키가 없으면 401
- 토큰 무효화: 토큰의 ver 클레임과 users.token_version, POST /api/v1/users/logout
- 로그인 실패 제한(이메일별)과 bcrypt 전용 스레드 제한기
- 로그인 응답 시간: 미가입 이메일도 같은 비용의 bcrypt 비교를 한 번 한다
- 가입·로그인 입력: 길이 상한, 한 줄 텍스트, 저장할 수 없는 문자(NUL·짝 없는 서로게이트)
"""
import asyncio
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import anyio.to_thread
import bcrypt
import httpx
import jwt
import psycopg
import pytest
from starlette.concurrency import run_in_threadpool

from app.services import security
from app.services.login_throttle import MAX_FAILURES, WINDOW_SECONDS, LoginThrottle, login_throttle
from tests.conftest import make_vc_jwt

LOGIN = "/api/v1/users/login"
REGISTER = "/api/v1/users/register"
PASSWORD = "password1234"
SERVICE = {"X-Service-Key": "test-service-key"}
TOO_MANY = {"detail": "Too many attempts. Try again later."}
AUTH_REQUIRED = {"detail": "Authentication required"}
INVALID_TOKEN = {"detail": "Invalid token"}

# 토큰 없이 부를 수 있는 공개 API. 여기에 없는 경로는 모두 토큰(또는 읽기용 서비스 키)이 있어야 한다.
PUBLIC_ROUTES = {
    ("GET", "/health"),
    ("POST", REGISTER),
    ("POST", LOGIN),
    ("GET", "/api/v1/public/shares/{token}"),
}
DOCS_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}  # FastAPI 문서 화면


@pytest.fixture(autouse=True)
def fresh_throttle():
    login_throttle.clear()
    yield
    login_throttle.clear()


@pytest.fixture
def fast_bcrypt(monkeypatch):
    """로그인을 수십 번 하는 테스트용: 새 해시와 더미 해시를 비용 4로 만든다 (운영 비용 12는 그대로)."""
    real_gensalt = bcrypt.gensalt
    monkeypatch.setattr(security.bcrypt, "gensalt", lambda: real_gensalt(rounds=4))
    monkeypatch.setattr(security, "_DUMMY_PASSWORD_HASH",
                        bcrypt.hashpw(b"dummy-for-tests", real_gensalt(rounds=4)).decode())


@pytest.fixture
def checkpw_calls(monkeypatch):
    """bcrypt.checkpw가 어떤 해시와 비교했는지 (해시, 결과)로 기록한다. 비교 없이 바로 거절하면(ValueError) 결과는 'rejected'."""
    real = bcrypt.checkpw
    calls: list[tuple[bytes, object]] = []

    def spy(password: bytes, hashed: bytes) -> bool:
        try:
            result = real(password, hashed)
        except ValueError:
            calls.append((hashed, "rejected"))
            raise
        calls.append((hashed, result))
        return result

    monkeypatch.setattr(security.bcrypt, "checkpw", spy)
    return calls


DOTTED_I = "\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}"  # 'İ': 파이썬 lower()는 'i' + U+0307, PostgreSQL(en_US.UTF-8)은 'i'
SIGMA = "\N{GREEK CAPITAL LETTER SIGMA}"  # 'Σ': 파이썬 lower()는 어말에서 'ς', PostgreSQL(글자마다 접음)은 늘 'σ'


def fold_like_postgres(db, monkeypatch, variant: str, email: str) -> None:
    """이 DB의 lower()가 variant를 email과 같게 접지 않으면(C 정렬 등) 글자마다 접는 DB(en_US.UTF-8, RDS 기본)의 조회를 흉내 낸다."""
    from app.services.database import db_service

    if not db.execute("SELECT lower(%s) = lower(%s) AS same", (variant, email)).fetchone()["same"]:
        real = db_service.get_user_by_email
        monkeypatch.setattr(db_service, "get_user_by_email", lambda e: real("".join(ch.lower()[:1] for ch in e)))


@pytest.fixture
def collation_mismatch(monkeypatch):
    """실패 기록의 키를 예전처럼 파이썬 str.lower()로 접게 해, DB 정렬이 키와 다르게 접는 경우(예: ICU)를 흉내 낸다.

    이 테스트 DB(libc 정렬)는 키와 똑같이 접으므로, login_user의 대비책(DB가 다른 키의 표기로 계정을 찾으면 저장된 이메일로도
    세고, 그 기록이 잠겼으면 401·더미 비교 한 번)은 이렇게만 시험할 수 있다.
    """
    monkeypatch.setattr(LoginThrottle, "_key", staticmethod(
        lambda email: hashlib.sha256(email.strip().lower().encode("utf-8", "surrogatepass")).digest()))


# DB(글자마다 접음)만 가입 이메일과 같은 계정으로 찾는 표기: (가입 이메일 형식, 표기로 바꾸는 함수)
FOLDING_SPELLINGS = {
    "dotted-i": ("kim-{}@univ.test", lambda e: e.replace("i", DOTTED_I, 1)),  # 'kİm-…': 파이썬 lower()는 'i' + U+0307
    "final-sigma": ("ασ-{}@univ.test", lambda e: e.replace("σ", SIGMA, 1)),  # 'αΣ-…': 파이썬 lower()는 'ας-…'
}


def new_email(prefix: str = "t") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@test.com"


def login(client, email: str, password: str = PASSWORD):
    return client.post(LOGIN, json={"email": email, "password": password})


def fail(client, email: str, times: int) -> list[int]:
    return [login(client, email, "wrong-password").status_code for _ in range(times)]


def signed(payload: dict) -> str:
    """서버와 같은 비밀값으로 서명한 토큰 (클레임만 바꿔 서버 검사를 본다)."""
    from app.core.config import get_settings

    return jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def vc_be_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "vc-be"]


# ── 인증 필수 (경로 목록) ─────────────────────────────────────────────

def route_inventory() -> list[tuple[str, str]]:
    """앱의 모든 (메서드, 경로). include_router로 붙인 경로까지 펼친다 (FastAPI 0.16x는 포함한 라우터를 늦게 펼친다)."""
    from fastapi import routing

    from main import app

    iterate = getattr(routing, "iter_route_contexts", None)
    routes = []
    for route in (iterate(app.routes) if iterate else app.routes):
        methods = getattr(route, "methods", None)
        # Mount·WebSocket 같은 경로가 생기면 인증 여부를 이 테스트에서 따로 확인해야 한다.
        assert methods, f"HTTP 메서드가 없는 경로: {route!r}"
        routes += [(method, route.path) for method in sorted(methods)]
    # 펼친 결과가 OpenAPI 문서의 모든 작업을 포함하는지 확인한다 (펼치기가 경로를 빠뜨리면 이 테스트가 헛돈다).
    documented = {(method.upper(), path)
                  for path, operations in app.openapi()["paths"].items() for method in operations}
    assert documented <= set(routes)
    return routes


@pytest.mark.parametrize("legacy_auth_required", [None, "false"],
                         ids=["auth-required-unset", "legacy-auth-required-false"])
@pytest.mark.parametrize("app_env", ["development", "production"])
def test_every_route_except_public_ones_requires_credentials(client, fake_dx, monkeypatch, request, app_env,
                                                             legacy_auth_required):
    """새 경로가 생겨도 공개 목록에 넣지 않는 한 토큰 없이 열리지 않는다. 예전 AUTH_REQUIRED=false·APP_ENV와 무관하다."""
    from app.core.config import get_settings

    if app_env == "production":
        request.getfixturevalue("production_env")  # 운영 검사를 통과하는 비밀값으로 APP_ENV=production
    if legacy_auth_required is None:
        monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    else:
        monkeypatch.setenv("AUTH_REQUIRED", legacy_auth_required)
    get_settings.cache_clear()
    assert get_settings().app_env == app_env
    fake_dx.error = AssertionError("partner API must not be called without credentials")

    routes = route_inventory()
    assert PUBLIC_ROUTES <= set(routes)  # 공개 경로 이름이 바뀌면 이 목록도 함께 고친다
    checked = set()
    for method, path in routes:
        if (method, path) in PUBLIC_ROUTES or path in DOCS_PATHS:
            continue
        url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)  # 경로 변수: UUID(문자열 DID 자리에도 그대로 쓴다)
        for headers in ({}, {"Authorization": "Basic dXNlcjpwYXNz"}):  # Bearer가 아닌 Authorization도 인증 정보가 아니다
            res = client.request(method, url, headers=headers)
            assert res.status_code == 401, (method, path, headers, res.status_code, res.text)
            assert res.headers["www-authenticate"] == "Bearer"
            if method != "HEAD":
                assert res.json() == AUTH_REQUIRED, (method, path)
        checked.add((method, path))
    # 사용자 데이터·쓰기 API의 대표 경로가 실제로 검사됐는지 (펼치기가 비어도 통과하지 않게)
    assert {("GET", "/api/v1/subjects/{did}/careers"), ("GET", "/api/v1/subjects/{did}/evidence"),
            ("POST", "/api/v1/verify"), ("POST", "/api/mock-issuer/issue"), ("POST", "/api/v1/users/logout"),
            ("GET", "/api/v1/users/me"), ("POST", "/api/v1/portfolios"), ("POST", "/api/v1/shares")} <= checked
    assert len(checked) >= 25
    assert fake_dx.issued == []


def test_subject_reads_fail_closed_for_every_principal_but_the_service_key():
    from fastapi import HTTPException

    from app.core.auth import Principal, ensure_can_read_subject

    me = "did:key:z6MkMe"
    ensure_can_read_subject(Principal(kind="service"), "did:key:z6MkAnyone")  # 서비스 키(읽기 전용)는 모든 DID
    ensure_can_read_subject(Principal(kind="user", user_id=str(uuid.uuid4()), did=me), me)
    # 남의 DID를 읽는 사용자, 그리고 DID가 없는 다른 종류의 주체가 생겨도 통과시키지 않는다.
    for principal in (Principal(kind="user", user_id=str(uuid.uuid4()), did=me),
                      Principal(kind="anonymous")):  # type: ignore[arg-type]
        with pytest.raises(HTTPException) as error:
            ensure_can_read_subject(principal, "did:key:z6MkSomeoneElse")
        assert error.value.status_code == 403


def test_public_routes_answer_without_credentials(client):
    assert client.get("/health").status_code == 200
    # 회원가입·로그인은 본문 검사(422)까지 간다 = 인증을 요구하지 않는다
    assert client.post(REGISTER, json={}).status_code == 422
    assert client.post(LOGIN, json={}).status_code == 422
    assert client.get(f"/api/v1/public/shares/{'x' * 43}").status_code == 404  # 없는 공유 링크
    assert client.get("/openapi.json").status_code == 200


def test_requests_without_credentials_change_nothing(client, register, fake_dx, db, monkeypatch):
    """예전 전환 기간에 토큰 없이 열려 있던 API. 남은 AUTH_REQUIRED=false로도 조회·저장·재검증·발급이 일어나지 않는다."""
    from app.core.config import get_settings

    victim = register()
    did = victim["did"]
    record_id = client.post("/api/v1/verify", json={"type": "badge", "token": make_vc_jwt(did)},
                            headers=victim["headers"]).json()["data"]["record_id"]
    before = db.execute("SELECT * FROM credentials WHERE user_id = %s", (victim["id"],)).fetchall()

    monkeypatch.setenv("AUTH_REQUIRED", "false")
    get_settings.cache_clear()
    fake_dx.error = AssertionError("partner API must not be called without credentials")
    for method, url, body in [
        ("GET", f"/api/v1/subjects/{did}/careers", None),
        ("GET", f"/api/v1/subjects/{did}/evidence?mode=labeled&record_id={record_id}", None),
        ("POST", "/api/v1/verify", {"type": "badge", "token": make_vc_jwt(did, name="몰래 넣은 배지")}),
        ("POST", "/api/mock-issuer/issue", {"holderDid": did}),
        ("POST", f"/api/v1/credentials/{record_id}/reverify", None),
    ]:
        res = client.request(method, url, json=body)
        assert res.status_code == 401 and res.json() == AUTH_REQUIRED, (method, url, res.text)

    assert fake_dx.issued == []  # 회사 API 키로 발급하지 않았다
    # 재검증·저장 없음: 피해자 지갑의 행(상태·본문·마지막 검증 시각)이 그대로다.
    assert db.execute("SELECT * FROM credentials WHERE user_id = %s", (victim["id"],)).fetchall() == before
    fake_dx.error = None
    own = client.get(f"/api/v1/subjects/{did}/careers", headers=victim["headers"])  # 본인 토큰으로는 그대로 동작
    assert own.status_code == 200 and [c["record_id"] for c in own.json()["verified_credentials"]] == [record_id]


# ── 토큰 버전 / 로그아웃 ─────────────────────────────────────────────

def test_issued_tokens_carry_the_current_token_version(client, register):
    user = register()
    claims = jwt.decode(user["token"], options={"verify_signature": False})
    assert claims["ver"] == 0 and claims["sub"] == user["id"] and claims["did"] == user["did"]
    claims = jwt.decode(login(client, user["email"]).json()["access_token"], options={"verify_signature": False})
    assert claims["ver"] == 0


def test_logout_revokes_every_token_of_that_user_only(client, register, db):
    user, other = register("로그아웃"), register("다른 사용자")
    second = bearer(login(client, user["email"]).json()["access_token"])  # 다른 탭·기기의 로그인
    assert client.get("/api/v1/users/me", headers=user["headers"]).status_code == 200
    assert client.get("/api/v1/users/me", headers=second).status_code == 200

    res = client.post("/api/v1/users/logout", headers=second)
    assert res.status_code == 204 and res.content == b""
    assert db.execute("SELECT token_version FROM users WHERE id = %s", (user["id"],)).fetchone()["token_version"] == 1

    # 가입 때 받은 토큰과 로그인 토큰 모두, 사용자 토큰이 필요한 API와 careers에서 거절된다.
    for headers in (user["headers"], second):
        me = client.get("/api/v1/users/me", headers=headers)
        assert me.status_code == 401 and me.json() == INVALID_TOKEN
        assert me.headers["www-authenticate"] == "Bearer"
        assert client.get(f"/api/v1/subjects/{user['did']}/careers", headers=headers).status_code == 401
        assert client.get("/api/v1/portfolios", headers=headers).status_code == 401
    assert client.post("/api/v1/users/logout", headers=second).status_code == 401  # 이미 무효
    assert client.get("/api/v1/users/me", headers=other["headers"]).status_code == 200  # 다른 사용자는 그대로

    fresh = login(client, user["email"]).json()["access_token"]  # 다시 로그인하면 새 버전 토큰
    assert jwt.decode(fresh, options={"verify_signature": False})["ver"] == 1
    assert client.get("/api/v1/users/me", headers=bearer(fresh)).status_code == 200


def test_logout_requires_user_token(client):
    for headers in ({}, SERVICE, bearer("garbage")):
        res = client.post("/api/v1/users/logout", headers=headers)
        assert res.status_code == 401 and res.headers["www-authenticate"] == "Bearer"


def test_token_version_and_subject_are_checked(client, register, db):
    user = register()
    now = datetime.now(timezone.utc)
    legacy = {"sub": user["id"], "did": user["did"], "iat": now, "exp": now + timedelta(minutes=5), "type": "access"}

    def me(payload: dict):
        return client.get("/api/v1/users/me", headers=bearer(signed(payload)))

    assert me(legacy).status_code == 200  # ver가 없는 예전 토큰은 0으로 본다 (배포 직후에도 로그인 유지)
    assert me({**legacy, "ver": 0}).status_code == 200
    for bad in ({"ver": 1}, {"ver": "0"}, {"ver": True}, {"ver": False}, {"ver": 0.0}, {"ver": None},  # 버전이 다르거나 정수가 아님
                {"sub": str(uuid.uuid4())},  # 없는 사용자 (DB 초기화 등)
                {"sub": "not-a-uuid"},  # 500이 아니라 401
                {"type": "refresh"}):
        res = me({**legacy, **bad})
        assert res.status_code == 401 and res.json() == INVALID_TOKEN, bad
    expired = me({**legacy, "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1)})
    assert expired.status_code == 401 and expired.json() == {"detail": "Token expired"}

    assert client.post("/api/v1/users/logout", headers=user["headers"]).status_code == 204
    assert me(legacy).status_code == 401  # 로그아웃하면 ver 없는 예전 토큰도 무효
    assert me({**legacy, "ver": 1}).status_code == 200

    db.execute("DELETE FROM users WHERE id = %s", (user["id"],))  # 계정이 없어지면 서명이 유효한 토큰도 401
    assert me({**legacy, "ver": 1}).json() == INVALID_TOKEN


# ── 로그인 실패 제한 (이메일별) ──────────────────────────────────────

def test_repeated_failures_lock_that_account_even_for_the_right_password(client, register, fast_bcrypt):
    user = register()
    assert fail(client, user["email"], MAX_FAILURES) == [401] * MAX_FAILURES
    res = login(client, user["email"])
    assert res.status_code == 429 and res.json() == TOO_MANY
    assert 0 < int(res.headers["retry-after"]) <= WINDOW_SECONDS
    other = register()  # 다른 계정은 영향 없음
    assert login(client, other["email"]).status_code == 200


def test_success_clears_failures(client, register, fast_bcrypt):
    user = register()
    for _ in range(2):
        assert fail(client, user["email"], MAX_FAILURES - 1) == [401] * (MAX_FAILURES - 1)
        assert login(client, user["email"]).status_code == 200


def test_lock_ends_when_the_window_passes(client, register, fast_bcrypt, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(login_throttle, "_clock", lambda: now[0])
    user = register()
    fail(client, user["email"], MAX_FAILURES)
    assert login(client, user["email"]).status_code == 429
    now[0] += WINDOW_SECONDS + 1
    assert login(client, user["email"]).status_code == 200


def test_case_variants_share_one_counter_and_unknown_emails_look_the_same(client, register, fast_bcrypt):
    user = register()
    upper = user["email"].upper()
    assert fail(client, user["email"], 5) + fail(client, upper, 5) == [401] * 10
    assert login(client, f" {upper} ").status_code == 429
    # 가입하지 않은 이메일도 같은 횟수에서 같은 429 (가입 여부를 알려 주지 않음)
    assert fail(client, new_email("nobody"), MAX_FAILURES + 1) == [401] * MAX_FAILURES + [429]


def test_db_case_folding_variant_counts_against_the_same_account(client, fast_bcrypt, db, monkeypatch, checkpw_calls):
    """DB lower()가 같은 계정으로 찾는 다른 표기로 한도를 피하지 못한다.

    PostgreSQL(en_US.UTF-8)의 lower()는 글자마다 접어 'İ'(U+0130)를 'i'로 바꾸지만, 파이썬 lower()는 'i' + U+0307(두 글자)로
    바꾼다. 실패 기록의 키도 DB처럼 글자마다 접으므로 이 표기는 대소문자만 다른 표기처럼 계정과 같은 키를 쓴다.
    """
    email = f"kim-{uuid.uuid4().hex[:8]}@univ.test"
    assert client.post(REGISTER, json={"name": "김", "email": email, "password": PASSWORD}).status_code == 200
    variant = email.replace("i", DOTTED_I, 1)
    fold_like_postgres(db, monkeypatch, variant, email)
    assert login_throttle.same_key(variant, email)

    assert fail(client, email, MAX_FAILURES - 1) == [401] * (MAX_FAILURES - 1)
    assert fail(client, variant, 1) == [401]  # 다른 표기의 실패도 계정(저장된 이메일)과 같은 기록에 더해진다
    assert login(client, email).status_code == 429
    # 맞는 비밀번호라도 잠금을 우회하지 못한다 (예전에 새 키로만 셀 때는 200). 대소문자만 다른 표기처럼 입력한 표기의 키가
    # 잠겨 있어, 비밀번호도 bcrypt도 확인하지 않고 429다.
    checkpw_calls.clear()
    res = login(client, variant)
    assert res.status_code == 429 and res.json() == TOO_MANY and "retry-after" in res.headers
    assert checkpw_calls == []


def test_variant_found_through_a_differently_folding_collation_counts_against_the_stored_email(
        client, fast_bcrypt, db, monkeypatch, checkpw_calls, collation_mismatch):
    """대비책: DB 정렬이 키와 다르게 접어(collation_mismatch로 흉내) 다른 키의 표기로 같은 계정을 찾을 때.

    그 표기의 실패도 저장된 이메일의 기록에 더하고, 저장된 이메일이 잠겼으면 비밀번호는 확인하지 않고 429 대신 미가입
    이메일과 같은 401·같은 비용(더미 비교 한 번)으로 답한다 (가입 여부가 드러나지 않게).
    """
    email = f"kim-{uuid.uuid4().hex[:8]}@univ.test"
    assert client.post(REGISTER, json={"name": "김", "email": email, "password": PASSWORD}).status_code == 200
    variant = email.replace("i", DOTTED_I, 1)
    fold_like_postgres(db, monkeypatch, variant, email)
    assert not login_throttle.same_key(variant, email)

    assert fail(client, email, MAX_FAILURES - 1) == [401] * (MAX_FAILURES - 1)
    assert fail(client, variant, 1) == [401]  # 다른 키의 표기지만 계정(저장된 이메일) 기록에도 더해진다
    assert login(client, email).status_code == 429
    checkpw_calls.clear()
    res = login(client, variant)  # 맞는 비밀번호
    assert res.status_code == 401 and res.json() == {"detail": "Invalid email or password"}
    assert "retry-after" not in res.headers
    assert checkpw_calls == [(security._DUMMY_PASSWORD_HASH.encode(), False)]


@pytest.mark.parametrize("collation", ["libc", "differs"])
def test_db_folding_spelling_answers_the_same_for_registered_and_unknown_emails(client, fast_bcrypt, db, monkeypatch,
                                                                                checkpw_calls, request, collation):
    """잠긴 이메일을 DB만 접는 표기('İ' → 'i')로 보내도 가입 이메일과 미가입 이메일의 답과 비용이 같다.

    - libc(이 DB, RDS 기본): 키도 글자마다 접어 같은 키라, 대소문자만 다른 표기처럼 둘 다 입력한 표기의 키로 429(bcrypt 없음).
    - differs(DB 정렬이 키와 다르게 접음, collation_mismatch): 대비책으로 둘 다 401·Retry-After 없음·더미 비교 한 번.
    round 2 전에는 가입된 이메일만 저장된 이메일의 기록으로도 세어 429, 미가입 이메일은 401이라 가입 여부가 드러났다.
    """
    if collation == "differs":
        request.getfixturevalue("collation_mismatch")
    registered, unknown = (f"kim-{uuid.uuid4().hex[:8]}@univ.test" for _ in range(2))
    assert client.post(REGISTER, json={"name": "김", "email": registered, "password": PASSWORD}).status_code == 200
    fold_like_postgres(db, monkeypatch, registered.replace("i", DOTTED_I, 1), registered)
    dummy = security._DUMMY_PASSWORD_HASH.encode()
    for email in (registered, unknown):
        assert fail(client, email, MAX_FAILURES) == [401] * MAX_FAILURES
        assert login(client, email).status_code == 429  # 입력한 표기 그대로는 둘 다 429
        checkpw_calls.clear()
        res = login(client, email.replace("i", DOTTED_I, 1))
        if collation == "libc":
            assert res.status_code == 429 and res.json() == TOO_MANY and "retry-after" in res.headers, email
            assert checkpw_calls == [], email
        else:
            assert res.status_code == 401 and res.json() == {"detail": "Invalid email or password"}, email
            assert "retry-after" not in res.headers
            assert checkpw_calls == [(dummy, False)], email  # 비밀번호는 확인하지 않고 같은 비용의 더미 비교 한 번


# 틀린 비밀번호로 보낼 표기 순서 (exact: 가입한 표기, variant: DB만 같게 접는 표기). 마지막 한 번의 답을 비교한다.
FOLDING_SEQUENCES = {
    "forward": lambda exact, variant: [exact] * MAX_FAILURES + [variant],
    "reverse": lambda exact, variant: [variant] * MAX_FAILURES + [exact],
    "mixed-9-1": lambda exact, variant: [exact] * (MAX_FAILURES - 1) + [variant, exact],
    "mixed-5-5": lambda exact, variant: [exact] * 5 + [variant] * 5 + [exact],
}


@pytest.mark.parametrize("sequence", list(FOLDING_SEQUENCES))
@pytest.mark.parametrize("spelling", list(FOLDING_SPELLINGS))
def test_db_folding_spellings_in_any_order_answer_the_same_for_registered_and_unknown_emails(
        client, fast_bcrypt, db, monkeypatch, spelling, sequence):
    """DB만 접는 표기를 어떤 순서로 섞어 보내도 가입 이메일과 미가입 쌍둥이 이메일의 답이 같다 (둘 다 429 + Retry-After).

    고치기 전에는 키를 파이썬 lower()로 접어, 그 표기의 실패를 계정을 찾은 가입 이메일만 저장된 이메일로도 셌다. 그래서
    reverse·mixed 순서의 마지막 시도가 가입 이메일은 429, 미가입 이메일은 401이라 가입 여부가 드러났다.
    """
    template, to_variant = FOLDING_SPELLINGS[spelling]
    registered, unknown = (template.format(uuid.uuid4().hex[:8]) for _ in range(2))
    assert client.post(REGISTER, json={"name": "n", "email": registered, "password": PASSWORD}).status_code == 200
    fold_like_postgres(db, monkeypatch, to_variant(registered), registered)
    answers = []
    for email in (registered, unknown):
        responses = [login(client, typed, "wrong-password")
                     for typed in FOLDING_SEQUENCES[sequence](email, to_variant(email))]
        assert [r.status_code for r in responses[:-1]] == [401] * MAX_FAILURES, email
        answers.append((responses[-1].status_code, responses[-1].json(), "retry-after" in responses[-1].headers))
    assert answers == [(429, TOO_MANY, True)] * 2


def test_own_login_through_another_accounts_key_does_not_clear_it(client, fast_bcrypt, db, monkeypatch,
                                                                  collation_mismatch):
    """성공하면 찾은 계정(저장된 이메일)의 기록만 지운다. 입력한 표기의 키는 다른 계정의 것일 수 있다.

    DB 정렬이 키와 다르게 접는 경우다(collation_mismatch: 키는 파이썬 str.lower()). 'İx-…'로 가입한 피해자는 'i' + U+0307 +
    'x-…'로 저장되어 'İx-…' 표기의 키가 피해자의 키와 같다. 그런데 DB lower()는 'İ'를 'i'로 접어 공격자의 'ix-…' 계정을 찾는다.
    round 2 전에는 공격자가 이 표기로 자기 계정에 로그인할 때마다 피해자의 실패 기록이 지워져 한도 없이 추측할 수 있었다.
    (키를 DB처럼 글자마다 접으면 'İx-…'의 키는 공격자의 키다: test_throttle_key_folds_each_character_like_postgres_lower)
    """
    tag = uuid.uuid4().hex[:8]
    typed, attacker, attacker_password = f"{DOTTED_I}x-{tag}@univ.test", f"ix-{tag}@univ.test", "attacker-password"
    for email, password in ((typed, PASSWORD), (attacker, attacker_password)):
        assert client.post(REGISTER, json={"name": "n", "email": email, "password": password}).status_code == 200
    victim = typed.lower()  # 저장된 피해자 이메일
    assert login_throttle.same_key(typed, victim) and not login_throttle.same_key(typed, attacker)
    fold_like_postgres(db, monkeypatch, typed, attacker)

    assert fail(client, victim, MAX_FAILURES - 1) == [401] * (MAX_FAILURES - 1)
    res = login(client, typed, attacker_password)
    assert res.status_code == 200 and res.json()["data"]["email"] == attacker  # 공격자 자신의 계정
    assert login(client, victim).status_code == 429  # 피해자의 기록은 그대로라 잠긴다 (고치기 전에는 지워져 200)


def test_success_with_a_case_or_space_variant_clears_the_account_record(client, register, fast_bcrypt):
    user = register()
    assert fail(client, user["email"], MAX_FAILURES - 1) == [401] * (MAX_FAILURES - 1)
    assert login(client, f"  {user['email'].upper()} ").status_code == 200  # 저장된 이메일과 같은 키
    assert fail(client, user["email"], MAX_FAILURES - 1) == [401] * (MAX_FAILURES - 1)  # 처음부터 다시 센다


@pytest.mark.parametrize("collation", ["libc", "differs"])
def test_success_through_a_db_folding_variant_clears_the_stored_email_record(client, fast_bcrypt, db, monkeypatch,
                                                                             request, collation):
    """DB만 접는 표기로 로그인에 성공해도 계정(저장된 이메일)의 기록이 지워진다 (남은 실패로 나중에 일찍 잠기지 않는다).

    libc에서는 그 표기의 키가 곧 계정의 키다. DB 정렬이 키와 다르게 접으면(differs) 그 표기의 실패를 저장된 이메일로도 세므로
    성공하면 저장된 이메일의 기록을 지워야 한다.
    """
    if collation == "differs":
        request.getfixturevalue("collation_mismatch")
    email = f"kim-{uuid.uuid4().hex[:8]}@univ.test"
    assert client.post(REGISTER, json={"name": "김", "email": email, "password": PASSWORD}).status_code == 200
    variant = email.replace("i", DOTTED_I, 1)
    fold_like_postgres(db, monkeypatch, variant, email)
    assert fail(client, variant, 3) == [401] * 3  # 저장된 이메일 기록에도 더해진다
    assert login(client, variant).status_code == 200
    assert fail(client, email, MAX_FAILURES - 1) == [401] * (MAX_FAILURES - 1)  # 처음부터 다시 센다


@pytest.mark.parametrize("spelling", list(FOLDING_SPELLINGS))
def test_repeated_success_through_a_db_folding_spelling_is_never_throttled(client, fast_bcrypt, db, monkeypatch,
                                                                           spelling):
    """DB만 접는 표기로 로그인에 계속 성공해도 429가 되지 않는다: 그 표기의 키가 계정의 키라 성공할 때마다 지워진다.

    고치기 전에는 그 표기의 키가 따로 남아(성공은 저장된 이메일의 기록만 지움) 15분 안에 11번째 성공부터 429였다.
    """
    template, to_variant = FOLDING_SPELLINGS[spelling]
    email = template.format(uuid.uuid4().hex[:8])
    assert client.post(REGISTER, json={"name": "n", "email": email, "password": PASSWORD}).status_code == 200
    variant = to_variant(email)
    fold_like_postgres(db, monkeypatch, variant, email)
    assert [login(client, variant).status_code for _ in range(MAX_FAILURES + 2)] == [200] * (MAX_FAILURES + 2)


def test_locked_attempts_do_not_run_bcrypt(client, register, fast_bcrypt, monkeypatch):
    user = register()
    calls = []
    real = bcrypt.checkpw
    monkeypatch.setattr(security.bcrypt, "checkpw", lambda *a: calls.append(1) or real(*a))
    fail(client, user["email"], MAX_FAILURES + 5)
    assert len(calls) == MAX_FAILURES


def test_validation_errors_do_not_count_as_failures(client, register, fast_bcrypt):
    user = register()
    for _ in range(MAX_FAILURES + 2):
        res = client.post(LOGIN, json={"email": user["email"], "password": "p" * 1025})
        assert res.status_code == 422
    assert login(client, user["email"]).status_code == 200


def test_throttle_key_folds_each_character_like_postgres_lower():
    """키는 PostgreSQL lower()(libc 정렬)처럼 글자마다 접는다. ASCII·보통 이메일의 키는 예전(str.lower())과 같다."""
    key = LoginThrottle._key
    assert key(f"k{DOTTED_I}m@univ.test") == key("kim@univ.test")  # 'İ' → 'i' (파이썬 lower()는 'i' + U+0307)
    # 어말을 포함한 모든 'Σ' → 'σ' (파이썬 lower()는 어말에서 'ας-…', 'σς@…')
    assert key(f"α{SIGMA}-x@univ.test") == key("ΑΣ-X@UNIV.TEST") == key("ασ-x@univ.test")
    assert key(f"{SIGMA}{SIGMA}@test.com") == key("σσ@test.com")
    # DB lower()도 그대로 두는 글자는 키도 그대로다: 'ς', 'i' + U+0307 ('İ'로 가입해 저장된 이메일)
    assert key("ας-x@univ.test") != key("ασ-x@univ.test")
    assert key("i\N{COMBINING DOT ABOVE}x@univ.test") != key("ix@univ.test") == key(f"{DOTTED_I}x@univ.test")
    for email in ("user001@example.com", " Kim.Lee+Tag@Example.COM ", "홍길동@예시.한국", "ÄÖÜ@Straße.de",
                  "\N{LATIN CAPITAL LETTER D WITH SMALL LETTER Z WITH CARON}x@test.com", "Ω@ΩΜΕΓΑ.test"):
        assert key(email) == hashlib.sha256(email.strip().lower().encode("utf-8", "surrogatepass")).digest(), email
    assert key("a\ud800@test.com")  # 짝 없는 서로게이트도 예외 없이 키가 된다 (API는 422로 먼저 거절)


def test_throttle_window_retry_after_and_bounded_keys():
    now = [0.0]
    throttle = LoginThrottle(max_failures=2, window_seconds=60, max_keys=3, clock=lambda: now[0])
    assert throttle.reserve("a@test.com") is None
    now[0] = 10.0
    assert throttle.reserve(" A@TEST.com ") is None  # 앞뒤 공백·대소문자는 같은 키
    assert throttle.reserve("a@test.com") == 50  # 가장 오래된 실패(0초)가 60초 창 밖으로 나갈 때까지 남은 초
    now[0] = 59.5
    assert throttle.reserve("a@test.com") == 1  # 1초 미만은 1로 올린다
    now[0] = 60.0
    assert throttle.reserve("a@test.com") is None  # 0초의 실패가 빠져 다시 시도할 수 있다
    throttle.succeeded("a@test.com")
    assert throttle.reserve("a@test.com") is None and throttle.reserve("a@test.com") is None

    for i in range(10):
        assert throttle.reserve(f"u{i}@test.com") is None
    assert len(throttle._failures) == 3  # 키 수는 상한까지만 (가장 오래 쓰지 않은 키부터 버림)


def test_throttle_evicts_the_least_recently_used_key():
    throttle = LoginThrottle(max_failures=5, window_seconds=60, max_keys=3, clock=lambda: 0.0)
    for email in ("a@test.com", "b@test.com", "c@test.com", "a@test.com", "d@test.com"):
        assert throttle.reserve(email) is None
    # a는 다시 써서 맨 뒤(가장 최근)로 옮겨졌으므로, 가장 오래 쓰지 않은 b가 버려진다
    assert set(throttle._failures) == {LoginThrottle._key(e) for e in ("a@test.com", "c@test.com", "d@test.com")}


def test_flood_of_new_emails_does_not_evict_a_locked_key():
    """키 수 상한에 걸려도 잠긴 키는 남긴다. 고치기 전에는 새 이메일을 상한만큼 보내면 잠긴 키가 밀려나 잠금이 풀렸다."""
    now = [0.0]
    throttle = LoginThrottle(max_failures=3, window_seconds=60, max_keys=5, clock=lambda: now[0])
    for _ in range(3):
        assert throttle.reserve("victim@test.com") is None
    for i in range(5 + 20):  # 상한 + 20개의 새 이메일
        now[0] += 1
        assert throttle.reserve(f"flood{i}@test.com") is None
    assert len(throttle._failures) == 5
    assert throttle.reserve("victim@test.com") == 35  # 그대로 잠겨 있다 (0초의 실패가 창 밖으로 나갈 때까지)

    # 다른 키가 모두 잠겼으면 가장 오래된 키를 버려, 키 수는 여전히 상한을 넘지 않는다
    locked_all = LoginThrottle(max_failures=1, window_seconds=60, max_keys=2, clock=lambda: 0.0)
    for email in ("a@test.com", "b@test.com", "c@test.com"):
        assert locked_all.reserve(email) is None  # 한 번 실패로 잠긴다
    assert len(locked_all._failures) == 2
    assert locked_all.reserve("b@test.com") == 60 and locked_all.reserve("a@test.com") is None  # a가 버려졌다


def test_eviction_never_drops_the_key_just_reserved():
    """다른 키가 모두 잠긴 표에 새 이메일을 넣어도 방금 예약한 키는 버리지 않는다 (대신 가장 오래된 잠긴 키를 버림).

    방금 예약한 키를 버리면 그 이메일은 몇 번을 보내도 세지 않아(매번 첫 시도) 한도 없이 비밀번호를 추측할 수 있다.
    """
    throttle = LoginThrottle(max_failures=3, window_seconds=900, max_keys=3, clock=lambda: 0.0)
    for email in ("a@test.com", "b@test.com", "c@test.com"):  # 표가 잠긴 키로 가득 찬다
        assert [throttle.reserve(email) for _ in range(3)] == [None] * 3
    assert [throttle.reserve("new@test.com") for _ in range(4)] == [None, None, None, 900]  # 세 번 틀리면 잠긴다
    assert len(throttle._failures) == 3


def test_eviction_treats_a_lock_older_than_the_window_as_unlocked():
    """잠금이 창 밖으로 지난 키는 잠긴 키로 보지 않는다: 키 수 상한에 걸리면 그 키를 먼저 버리고 최근에 2번 틀린 키는 남긴다."""
    now = [0.0]
    throttle = LoginThrottle(max_failures=3, window_seconds=900, max_keys=3, clock=lambda: now[0])
    assert [throttle.reserve("old-lock@test.com") for _ in range(3)] == [None] * 3  # 0초에 잠김
    now[0] = 1000.0  # 900초 창이 지나 잠금이 풀렸다
    assert [throttle.reserve("recent@test.com") for _ in range(2)] == [None, None]
    assert throttle.reserve("other@test.com") is None
    assert throttle.reserve("new@test.com") is None  # 상한 초과: 하나를 버린다
    assert LoginThrottle._key("old-lock@test.com") not in throttle._failures
    assert [throttle.reserve("recent@test.com") for _ in range(2)] == [None, 900]  # 2번의 실패가 남아 있어 세 번째에 잠긴다


async def _asgi_client():
    from main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_concurrent_burst_cannot_exceed_the_limit(register, fast_bcrypt):
    user = register()
    async with await _asgi_client() as c:
        results = await asyncio.gather(*[c.post(LOGIN, json={"email": user["email"], "password": f"guess-{i}"})
                                         for i in range(MAX_FAILURES + 20)])
    assert sorted(r.status_code for r in results) == [401] * MAX_FAILURES + [429] * 20


# ── bcrypt 전용 스레드 제한기 ─────────────────────────────────────────

async def test_bcrypt_runs_in_its_own_small_limiter_not_the_shared_pool(monkeypatch):
    from app.services.database import db_service

    hashed = bcrypt.hashpw(b"the-right-password", bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(db_service, "get_user_by_email",
                        lambda email: {"id": uuid.uuid4(), "name": "x", "email": email, "subject_did": "did:key:z6Mk",
                                       "password_hash": hashed, "token_version": 0})
    lock, running, peak = threading.Lock(), [0], [0]

    def slow_checkpw(password, password_hash):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.2)  # bcrypt처럼 GIL을 놓고 오래 걸리는 작업
        with lock:
            running[0] -= 1
        return False

    monkeypatch.setattr(security.bcrypt, "checkpw", slow_checkpw)
    shared = anyio.to_thread.current_default_thread_limiter()
    async with await _asgi_client() as c:
        burst = [asyncio.create_task(c.post(LOGIN, json={"email": f"u{i}@test.com", "password": "x"}))
                 for i in range(41)]  # 공용 풀(40)보다 많이
        await asyncio.sleep(0.1)
        borrowed_during_burst = shared.borrowed_tokens
        started = time.perf_counter()
        await run_in_threadpool(lambda: None)  # 다른 동기 API·DB 호출 자리
        waited = time.perf_counter() - started
        results = await asyncio.gather(*burst)
    assert [r.status_code for r in results] == [401] * 41
    assert peak[0] == security.PASSWORD_HASH_CONCURRENCY
    assert borrowed_during_burst < 5
    assert waited < 0.05  # 고치기 전에는 첫 bcrypt 묶음이 끝날 때까지(약 0.2초) 기다렸다


async def test_register_hashing_uses_the_same_small_limiter(monkeypatch):
    lock, running, peak = threading.Lock(), [0], [0]
    real_hashpw = bcrypt.hashpw

    def slow_hashpw(password, salt):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.2)
        with lock:
            running[0] -= 1
        return real_hashpw(password, bcrypt.gensalt(rounds=4))

    monkeypatch.setattr(security.bcrypt, "hashpw", slow_hashpw)
    count = security.PASSWORD_HASH_CONCURRENCY * 2
    async with await _asgi_client() as c:
        results = await asyncio.gather(*[
            c.post(REGISTER, json={"name": "동시", "email": new_email("burst"), "password": PASSWORD})
            for _ in range(count)])
    assert [r.status_code for r in results] == [200] * count
    assert peak[0] == security.PASSWORD_HASH_CONCURRENCY


# ── 로그인 응답 시간 (가입 여부) ─────────────────────────────────────

def _bcrypt_cost(password_hash) -> int:
    if isinstance(password_hash, str):
        password_hash = password_hash.encode()
    return int(password_hash.split(b"$")[2])  # $2b$<cost>$<salt+hash>


def test_login_runs_same_bcrypt_check_whether_or_not_email_exists(client, register, db, monkeypatch):
    """미가입 이메일, 비밀번호 없는 사용자도 실제 해시와 같은 비용의 bcrypt 비교를 한 번 한다 (응답 시간으로 가입 여부가 드러나지 않음)."""
    user = register()
    stored = db.execute("SELECT password_hash FROM users WHERE email = %s", (user["email"],)).fetchone()
    legacy_email = new_email("legacy")  # password_hash가 NULL인 기존 사용자
    db.execute("INSERT INTO users (email, name, subject_did) VALUES (%s, %s, %s)",
               (legacy_email, "비밀번호 없음", f"did:key:z6MkLegacy{uuid.uuid4().hex[:8]}"))

    real_checkpw = bcrypt.checkpw
    costs: list[int] = []

    def counting_checkpw(password: bytes, hashed: bytes) -> bool:
        costs.append(_bcrypt_cost(hashed))
        return real_checkpw(password, hashed)

    monkeypatch.setattr(security.bcrypt, "checkpw", counting_checkpw)
    for email in (user["email"], new_email("nobody"), legacy_email):
        costs.clear()
        res = login(client, email, "wrong-password")
        assert res.status_code == 401 and res.json() == {"detail": "Invalid email or password"}
        assert costs == [_bcrypt_cost(stored["password_hash"])], email
    costs.clear()
    assert login(client, legacy_email, "").status_code == 401  # 빈 비밀번호로도 비밀번호 없는 사용자에 로그인할 수 없다
    assert len(costs) == 1


def test_overlong_login_password_still_runs_one_full_bcrypt_compare(client, register, fast_bcrypt, checkpw_calls):
    """bcrypt 5는 72바이트를 넘는 비밀번호를 비교 없이 바로 거절한다(ValueError). 그런 로그인도 더미 해시와 한 번 비교한다.

    고치기 전에는 비용이 없는 요청이라, 새 이메일로 몰아 보내 로그인 실패 기록의 키 상한을 채우고 잠긴 계정을 밀어낼 수 있었다.
    """
    user = register()
    dummy = security._DUMMY_PASSWORD_HASH.encode()
    for email in (user["email"], new_email("nobody")):
        for password in ("p" * 73, "가" * 25, "p" * 1024):  # 73바이트, 75바이트(25자), 1,024자
            checkpw_calls.clear()
            res = login(client, email, password)
            assert res.status_code == 401 and res.json() == {"detail": "Invalid email or password"}
            assert checkpw_calls == [(dummy, False)], (email, len(password))


# 조회가 던질 수 있는 예외: DB 연결 수 초과, 끊긴 연결(OperationalError가 아님), DB 오류가 아닌 예외
LOOKUP_ERRORS = [
    (psycopg.OperationalError, "simulated: sorry, too many clients already"),
    (psycopg.InterfaceError, "the connection is closed"),
    (RuntimeError, "simulated: unexpected lookup failure"),
]


@pytest.mark.parametrize(("error", "message"), LOOKUP_ERRORS, ids=[e.__name__ for e, _ in LOOKUP_ERRORS])
def test_login_whose_user_lookup_fails_still_costs_one_bcrypt(client, fast_bcrypt, monkeypatch, checkpw_calls, caplog,
                                                               error, message):
    """조회 단계의 오류(예: 연결 수 초과)도 다른 실패처럼 더미 bcrypt 비교를 한 번 하고, 응답·로그는 지금처럼 500이다.

    고치기 전에는 bcrypt 없이 실패 기록의 키만 만들어, 이런 요청을 몰아 보내 키 상한을 채우고 잠긴 키를 밀어낼 수 있었다.
    DB 연결 오류만이 아니라 조회가 던지는 모든 예외가 그렇다 (OperationalError만 잡으면 나머지 예외는 비용 없이 키를 만든다).
    """
    from app.services.database import db_service

    lookups = []

    def broken_lookup(email):
        lookups.append(email)
        raise error(message)

    monkeypatch.setattr(db_service, "get_user_by_email", broken_lookup)
    email, dummy = new_email("dberror"), security._DUMMY_PASSWORD_HASH.encode()
    for _ in range(MAX_FAILURES):
        checkpw_calls.clear()
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="vc-be"):
            res = login(client, email, "wrong-password")
        assert res.status_code == 500 and res.json() == {"detail": "Internal server error"}
        assert checkpw_calls == [(dummy, False)]
        assert [r.getMessage() for r in vc_be_records(caplog)] == ["Unhandled error"]
    # 시도는 조회 전에 기록됐다: 한도를 넘으면 DB 조회도 bcrypt도 없이 429
    checkpw_calls.clear()
    res = login(client, email)
    assert res.status_code == 429 and res.json() == TOO_MANY
    assert len(lookups) == MAX_FAILURES and checkpw_calls == []


def test_login_password_check_without_hash_is_always_false(monkeypatch):
    """해시가 없으면 더미 해시와 비밀번호가 우연히 맞아도 로그인할 수 없다."""
    known = bcrypt.hashpw(b"known-password", bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(security, "_DUMMY_PASSWORD_HASH", known)
    assert security.check_login_password("known-password", None) is False
    assert security.check_login_password("known-password", "") is False
    assert security.check_login_password("known-password", known) is True
    assert security.check_login_password("other-password", known) is False


def test_login_password_check_with_unencodable_password_is_false_after_one_dummy_compare(monkeypatch, checkpw_calls):
    """짝 없는 서로게이트(UTF-8로 바꿀 수 없음)가 든 비밀번호는 API에서는 422로 먼저 거절되지만, 함수를 바로 불러도
    예외 없이 같은 비용의 더미 비교를 한 번 한 뒤 False다 (고치기 전에는 UnicodeEncodeError)."""
    known = bcrypt.hashpw(b"known-password", bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(security, "_DUMMY_PASSWORD_HASH", bcrypt.hashpw(b"dummy", bcrypt.gensalt(rounds=4)).decode())
    dummy = security._DUMMY_PASSWORD_HASH.encode()
    for password_hash in (known, None):
        checkpw_calls.clear()
        assert security.check_login_password("known-password\ud800", password_hash) is False
        assert checkpw_calls == [(dummy, False)], password_hash is not None


@pytest.mark.parametrize(("password", "with_hash"), [
    (None, False), (b"x", False), (123, False),  # 문자열이 아닌 값: 해시가 없으면 비밀번호를 보지 않는다
    ("pw\ud800", False), ("pw\ud800", True),  # 짝 없는 서로게이트(UTF-8로 바꿀 수 없음)
], ids=["none-no-hash", "bytes-no-hash", "int-no-hash", "surrogate-no-hash", "surrogate-real-hash"])
def test_login_password_check_called_directly_never_raises_and_costs_one_dummy_compare(monkeypatch, checkpw_calls,
                                                                                      password, with_hash):
    """함수를 바로 불러도(API는 늘 문자열 비밀번호를 넘긴다) 해시가 없으면 비밀번호가 무엇이든 길이를 재지 않고 더미 비교를
    한 번 한 뒤 False다. 해시보다 비밀번호 길이를 먼저 재면 None·bytes·int에서 AttributeError가 났다."""
    known = bcrypt.hashpw(b"pw", bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(security, "_DUMMY_PASSWORD_HASH", bcrypt.hashpw(b"dummy", bcrypt.gensalt(rounds=4)).decode())
    dummy = security._DUMMY_PASSWORD_HASH.encode()
    for password_hash in ((known,) if with_hash else (None, "")):
        for check in (security.check_login_password,  # 아래는 API(로그인)가 쓰는 비동기 함수
                      lambda p, h: asyncio.run(security.verify_password_async(p, h))):
            checkpw_calls.clear()
            assert check(password, password_hash) is False
            assert checkpw_calls == [(dummy, False)], (password_hash, check)


def test_dummy_hash_has_the_default_bcrypt_cost():
    assert _bcrypt_cost(security._DUMMY_PASSWORD_HASH) == _bcrypt_cost(bcrypt.gensalt())


# ── 가입·로그인 입력 ─────────────────────────────────────────────────

def test_login_fields_are_bounded(client, register, fast_bcrypt):
    user = register()
    assert login(client, user["email"], "p" * 1024).status_code == 401  # 상한 안: 지금처럼 401
    res = login(client, user["email"], "p" * 1025)
    assert res.status_code == 422 and res.json()["detail"][0]["loc"] == ["body", "password"]
    res = login(client, "e" * 1025, "x")
    assert res.status_code == 422 and res.json()["detail"][0]["loc"] == ["body", "email"]
    assert login(client, "e" * 1024, "x").status_code == 401


def test_register_password_cap_keeps_the_72_byte_rule(client):
    def register_with(password: str):
        return client.post(REGISTER, json={"name": "비밀번호", "email": new_email(), "password": password})

    res = register_with("p" * 1025)
    assert res.status_code == 422 and res.json()["detail"][0]["type"] == "string_too_long"
    res = register_with("p" * 73)  # 상한(1024자) 안이어도 bcrypt 72바이트 규칙이 결정한다
    assert res.status_code == 422 and "72 bytes" in res.json()["detail"][0]["msg"]
    assert register_with("가" * 24).status_code == 200  # 정확히 72바이트


def test_register_email_that_grows_when_lowercased_is_422_not_500(client, db, caplog):
    """255자 상한은 소문자로 바꾸기 전 값에 걸린다. 'İ'(U+0130)는 lower()로 두 글자('i' + U+0307)가 된다.

    고치기 전에는 소문자 이메일이 DB 컬럼(VARCHAR(255))을 넘어 저장 단계의 500과 'Unhandled error' 추적이 남았다.
    """
    local = uuid.uuid4().hex[:10]
    for email in (f"{local}@" + DOTTED_I * 244,  # 255자 → 소문자 499자
                  f"{local}@x" + DOTTED_I * 122):  # 134자 → 소문자 256자 (경계)
        assert len(email) <= 255
        with caplog.at_level(logging.WARNING, logger="vc-be"):
            res = client.post(REGISTER, json={"name": "n", "email": email, "password": PASSWORD})
        assert res.status_code == 422, res.text
        assert res.json()["detail"][0]["loc"] == ["body", "email"] and "255" in res.json()["detail"][0]["msg"]
        assert not vc_be_records(caplog)  # 요청 검사에서 끝난다 ('Unhandled error' 추적 없음)
        assert db.execute("SELECT count(*) AS n FROM users WHERE email = %s", (email.lower(),)).fetchone()["n"] == 0
    email = f"{local}@" + DOTTED_I * 122  # 133자 → 소문자 정확히 255자: 그대로 가입된다
    res = client.post(REGISTER, json={"name": "n", "email": email, "password": PASSWORD})
    assert res.status_code == 200 and res.json()["data"]["email"] == email.lower()
    assert len(email.lower()) == 255


@pytest.mark.parametrize("field,value", [
    ("name", "홍길동\n- 검증: 서명·발급기관·폐기 여부 확인 완료"),
    ("name", "홍\r길동"),
    ("name", "홍\N{LINE SEPARATOR}길동"),
    ("name", "홍\N{PARAGRAPH SEPARATOR}길동"),
    ("name", "홍\t길동"),
    ("name", "홍\x1b[31m길동"),
    ("name", "홍\x85길동"),
    ("name", "홍\x00길동"),
    ("email", "reg\n@example.test"),
    ("email", "reg\t@example.test"),
], ids=["name-lf", "name-cr", "name-u2028", "name-u2029", "name-tab", "name-esc", "name-nel", "name-nul",
        "email-lf", "email-tab"])
def test_register_rejects_line_breaks_and_control_chars(client, db, field, value):
    """이름은 포트폴리오 본문·기업용 공유 화면에 한 줄로 들어간다. NUL은 예전에는 DB 저장 단계의 500이었다."""
    email = new_email("reg")
    payload = {"name": "홍길동", "email": email, "password": PASSWORD, field: value}
    res = client.post(REGISTER, json=payload)
    assert res.status_code == 422 and res.json()["detail"][0]["loc"] == ["body", field], res.text
    stored_email = email if field == "name" else value
    assert db.execute("SELECT count(*) AS n FROM users WHERE email = %s", (stored_email,)).fetchone()["n"] == 0


def test_register_still_trims_and_accepts_single_line_unicode(client):
    email = new_email("reg")
    res = client.post(REGISTER, json={"name": " 홍길동\r\n", "email": f" {email.upper()}\n", "password": PASSWORD})
    assert res.status_code == 200
    assert res.json()["data"]["name"] == "홍길동" and res.json()["data"]["email"] == email
    # 이모지(ZWJ 결합 포함)·NBSP·전각 공백은 한 줄 텍스트로 받는다
    name = "김\N{NO-BREAK SPACE}철수\N{IDEOGRAPHIC SPACE}\N{WOMAN}\N{ZERO WIDTH JOINER}\N{PERSONAL COMPUTER}"
    res = client.post(REGISTER, json={"name": name, "email": new_email("reg"), "password": PASSWORD})
    assert res.status_code == 200 and res.json()["data"]["name"] == name


NUL, SURROGATE = "a\x00b", "a\ud800b"


def post_raw(client, url: str, body: dict, headers: dict | None = None):
    """짝 없는 서로게이트는 JSON 이스케이프("\\ud800")로만 보낼 수 있어 본문을 json.dumps(ASCII)로 직접 만든다."""
    return client.post(url, content=json.dumps(body), headers={"Content-Type": "application/json", **(headers or {})})


AUTH_FIELDS = [
    ("register.name", REGISTER, lambda v: {"name": v, "email": new_email(), "password": PASSWORD}, "name"),
    ("register.email", REGISTER, lambda v: {"name": "n", "email": f"{v}@test.com", "password": PASSWORD}, "email"),
    ("login.email", LOGIN, lambda v: {"email": f"{v}@test.com", "password": PASSWORD}, "email"),
]


@pytest.mark.parametrize("bad", [NUL, SURROGATE], ids=["nul", "surrogate"])
@pytest.mark.parametrize("name,url,body,field", AUTH_FIELDS, ids=[f[0] for f in AUTH_FIELDS])
def test_unstorable_text_in_auth_fields_is_422(client, caplog, bad, name, url, body, field):
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = post_raw(client, url, body(bad))
    assert res.status_code == 422, res.text
    assert res.json()["detail"][0]["loc"] == ["body", field]
    # 요청 검사에서 끝난다: 'Unhandled error' 추적도, DB 단계 안전망의 경고도 남지 않는다.
    assert not vc_be_records(caplog)


@pytest.mark.parametrize("url", [REGISTER, LOGIN])
def test_lone_surrogate_in_password_is_422_not_500(client, caplog, url):
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = post_raw(client, url, {"name": "n", "email": new_email(), "password": "password" + SURROGATE})
    assert res.status_code == 422 and res.json()["detail"][0]["loc"] == ["body", "password"]
    assert not vc_be_records(caplog)


def test_password_with_nul_still_works(client):
    """bcrypt 5는 NUL을 그대로 해시한다 (앞부분만 맞는 비밀번호로는 로그인할 수 없음)."""
    email, password = new_email(), "pass\x00word1234"
    assert client.post(REGISTER, json={"name": "널", "email": email, "password": password}).status_code == 200
    assert login(client, email, password).status_code == 200
    assert login(client, email, "pass").status_code == 401


def test_careers_did_path_with_nul_is_422(client, register, caplog):
    user = register()
    path = "/api/v1/subjects/did:key:z%00x/careers"
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        for headers in (SERVICE, user["headers"]):
            res = client.get(path, headers=headers)
            assert res.status_code == 422 and res.json()["detail"][0]["loc"] == ["path", "did"]
    assert not vc_be_records(caplog)
    assert client.get(path).status_code == 401  # 인증 정보가 없으면 경로 검사보다 먼저 401
